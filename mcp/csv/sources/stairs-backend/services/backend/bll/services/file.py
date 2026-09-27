import csv
from enum import Enum
from io import BytesIO, TextIOWrapper
from itertools import groupby
import logging
from operator import attrgetter, itemgetter
from typing import Any, Iterable

import numpy as np
import pandas as pd
import ast
from fastapi import Depends
from sampo.schemas.graph import EdgeType

from backend.bll.services.base import BaseBLLService, BLLServiceType
from backend.bll.services.exceptions import UnsupportedFileType, FileProcessingError

from stairs_etl.importer import XerImporter

from models.messaging import (
    ParsedFileStructure,
    ParsedFileWork,
    Connection,
    ParsedFileRow,
    ParsedGranularUnit,
)
from DAL.models.messaging import MessageAttachment
from models.project import ProjectFromFile

DEFAULT_UPPER_HIERARCHY_NAME = "Работы проекта"

class FILE_TYPES(Enum):
    XER = ".xer"
    XLSX = ".xlsx"
    CSV = ".csv"


CSV_ENCODINGS = ("utf-8-sig", "cp1251")
CSV_DELIMITERS = (";", ",")



class FileParserBLLService(BaseBLLService):

    SERVICE_TYPE: BLLServiceType = BLLServiceType.FILE_PARSER

    @staticmethod
    def _require_granular_unit_value(granular_unit: dict[str, Any], key: str) -> str:
        value = granular_unit[key]
        if value is None or pd.isna(value) or str(value).strip() == "":
            raise FileProcessingError(f"granular_unit.{key} must not be empty")
        return str(value)

    @staticmethod
    def _normalize_granular_unit(granular_unit: Any) -> dict[str, str] | None:
        if granular_unit is None or (not isinstance(granular_unit, (str, dict, list, tuple)) and pd.isna(granular_unit)):
            return None

        if isinstance(granular_unit, str):
            try:
                granular_unit = ast.literal_eval(granular_unit)
            except (ValueError, SyntaxError) as exc:
                raise FileProcessingError(
                    f"Unsupported granular_unit format: {granular_unit!r}"
                ) from exc

        if isinstance(granular_unit, (tuple, list)):
            if len(granular_unit) != 4:
                raise FileProcessingError(
                    "Unsupported granular_unit format: "
                    f"{granular_unit!r}. Expected 4 values: code, name, measurement, category"
                )

            granular_unit = {
                "code": granular_unit[0],
                "name": granular_unit[1],
                "measurement": granular_unit[2],
                "category": granular_unit[3],
            }

        if not isinstance(granular_unit, dict):
            raise FileProcessingError(f"Unsupported granular_unit format: {granular_unit!r}")

        try:
            category = granular_unit["category"]
            return {
                "code": FileParserBLLService._require_granular_unit_value(granular_unit, "code"),
                "name": FileParserBLLService._require_granular_unit_value(granular_unit, "name"),
                "category": "" if category is None or pd.isna(category) else str(category),
                "measurement": FileParserBLLService._require_granular_unit_value(granular_unit, "measurement"),
            }
        except KeyError as exc:
            raise FileProcessingError(f"granular_unit is missing required key: {exc.args[0]}") from exc

    @classmethod
    async def parse_csv(cls, df: pd.DataFrame) -> list[ParsedFileRow]:
        if "edges" in df.columns:
            df["edges"] = df["edges"].fillna("[]")
        return [
            ParsedFileRow(
                id=str(row["activity_id"]),
                activity_name=row["activity_name"],
                volume=float(row["volume"]),
                measurement=row["measurement"],
                structure=(
                    ast.literal_eval(row["structure"])
                    if isinstance(row["structure"], str)
                    else row["structure"]
                ) if "structure" in row else [],
                edges=(
                    ast.literal_eval(row["edges"])
                    if isinstance(row["edges"], str)
                    else row["edges"]
                )  if "edges" in row else [],
                granular_unit=(
                    cls._normalize_granular_unit(row["granular_unit"])
                ) if "granular_unit" in row else None,
            )
            for row in df.to_dict(orient="records")
        ]

    @staticmethod
    def _detect_csv_delimiter(text: str) -> str:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return ";"

        sample_lines = lines[:5]
        header = sample_lines[0]

        # Prefer the delimiter that splits the header into the most columns.
        # This is more reliable than Sniffer for our CSVs because data cells
        # often contain tuples/lists with commas inside stringified values.
        header_scores = {
            delimiter: len(next(csv.reader([header], delimiter=delimiter)))
            for delimiter in CSV_DELIMITERS
        }
        best_header_score = max(header_scores.values())
        best_header_delimiters = [
            delimiter
            for delimiter, score in header_scores.items()
            if score == best_header_score
        ]
        if best_header_score > 1 and len(best_header_delimiters) == 1:
            return best_header_delimiters[0]

        consistency_scores = {}
        for delimiter in CSV_DELIMITERS:
            field_counts = [
                len(next(csv.reader([line], delimiter=delimiter)))
                for line in sample_lines
            ]
            expected_count = field_counts[0]
            consistency_scores[delimiter] = (
                sum(count == expected_count for count in field_counts),
                expected_count,
            )

        best_consistency = max(consistency_scores.values())
        best_consistency_delimiters = [
            delimiter
            for delimiter, score in consistency_scores.items()
            if score == best_consistency
        ]
        if best_consistency[1] > 1 and len(best_consistency_delimiters) == 1:
            return best_consistency_delimiters[0]

        sample = "\n".join(sample_lines)

        try:
            dialect = csv.Sniffer().sniff(sample, delimiters="".join(CSV_DELIMITERS))
            return dialect.delimiter
        except csv.Error:
            semicolons = sample.count(";")
            commas = sample.count(",")
            if semicolons == 0 and commas == 0:
                return ";"
            return ";" if semicolons >= commas else ","

    @staticmethod
    def _read_csv_with_fallback(content: bytes) -> pd.DataFrame:
        last_error: UnicodeError | None = None

        for encoding in CSV_ENCODINGS:
            try:
                text = content.decode(encoding)
                delimiter = FileParserBLLService._detect_csv_delimiter(text)
                return pd.read_csv(BytesIO(content), delimiter=delimiter, encoding=encoding)
            except UnicodeError as exc:
                last_error = exc

        supported_encodings = ", ".join(CSV_ENCODINGS)
        raise FileProcessingError(
            f"Unsupported CSV encoding. Supported encodings: {supported_encodings}"
        ) from last_error
    
    async def add_dangling_attachment(self, name: str, recognized: bool) -> int:
        """Adds a `MessageAttachment` to database without message reference. Used when need to save
        a file to database, that needs a `MessageAttachment` reference.

        Args:
            name (str): Name of attachment (file) to save.
            recognized (bool): Is file format recognized. 
        
        Returns:
            attachment_id: Id of new attachment from database.
        """
        data = dict(
            name=name,
            recognized=recognized,
            id_message=None
        )
        
        attachment_id = await self.entity_service.add_entity_with_return_id(data, MessageAttachment)

        return attachment_id

    async def save_parsed_file(self, attachment_id: int, file_lines: list[ParsedFileRow]):
        await self.message_service.add_parsed_work_volume_measurement(
            attachment_id=attachment_id, content=file_lines
        )

        # validate parsed file
        errors = []

        def check_presence(
                basic: Iterable[str], actual: set[str], name: str, error_on_missing=True
        ):
            missing = actual.difference(basic)
            if missing:
                msg = self._format_iter_err(
                    f"There are unknown entries of '{name}' found in the file:\n\t", missing
                )
                if error_on_missing:
                    self._logger.error(msg)
                    errors.append(msg)
                else:
                    self._logger.warning(msg)

        (
            _,
            name2basic_work,
            name2basic_measurement_unit,
            _,
        ) = await self.entity_service.get_basic_dicts()
        work_measurement_names = await self.work_measurement_service.get_all_mapping_names()

        work_names = {line.granular_unit["name"] for line in file_lines if line.granular_unit}
        measurements = {line.measurement for line in file_lines}
        work_measurement = {(line.activity_name, line.measurement) for line in file_lines}
        connection_types = {lag[1] for line in file_lines for lag in
                            line.edges}

        check_presence(name2basic_work, work_names, name="activity name")
        check_presence(name2basic_measurement_unit, measurements, name="measurement unit")
        # TODO: make obligatory
        check_presence(
            work_measurement_names,
            work_measurement,
            "activity measurement combination",
            error_on_missing=False,
        )
        check_presence(list(map(attrgetter("value"), EdgeType)), connection_types, "edge type")

        await self.message_service.update_attachment_status(
            attachment_id=attachment_id, status=not errors
        )

        if errors:
            raise ValueError("\n".join(errors))

    @classmethod
    async def build_hierarchy(cls, rows: list[ParsedFileRow]) -> list[ParsedFileStructure]:

        def validate_structure(structure: list[tuple[str, str, int, int]]) -> bool:
            """Verifies the correctness of the hierarchy of levels"""
            if not structure:
                return False

            if structure[0][2] != 1:
                logging.error(f"The first element of the structure must have level 1, obtained: {structure[0][2]}")
                return False

            levels = {item[2] for item in structure}
            missing_levels = {1, 2, 3} - levels
            if missing_levels:
                logging.warning(f"Missing levels: {missing_levels}")
                return True  # NOTE if you need a strict hierarchy structure OKS_OSSR-MARKA, change to false and log error (not warning)

            for i in range(1, len(structure)):
                current_lvl = structure[i][2]
                prev_lvl = structure[i - 1][2]

                if current_lvl <= prev_lvl:
                    logging.error(
                        f"Violation of the sequence of levels: Parent level-{prev_lvl} -> current level-{current_lvl}")
                    return False

            return True

        path_dict = {}
        for row in rows:
            if not validate_structure(row.structure):
                logging.warning(f"Skipping activity_id {row.id } due to an incorrect structure")
                continue

            path_key = tuple(item[0] for item in row.structure)
            path_dict.setdefault(path_key, []).append(row)

        def build_tree(level: int, items: dict[tuple[str, ...], list[ParsedFileRow]]):
            level_nodes = {}

            for path, rows in items.items():
                if level >= len(path):
                    parsed_rows = []
                    for row in rows:
                        granular_unit = row.granular_unit
                        if granular_unit is None:
                            raise FileProcessingError(f"Missing granular_unit for activity_id {row.id}")

                        parsed_rows.append(
                            ParsedFileWork(
                                id=row.id,
                                work_name=row.activity_name,
                                volume=row.volume,
                                measurement_unit=row.measurement,
                                connections=[
                                    Connection(
                                        predecessor_id=edge[0],
                                        connection_type=edge[1],
                                        lag=edge[2]
                                    )
                                    for edge in row.edges
                                ],
                                granular_unit=ParsedGranularUnit(
                                    code=granular_unit["code"],
                                    name=granular_unit["name"],
                                    measurement=granular_unit["measurement"],
                                    category=granular_unit["category"]
                                )
                            )
                        )

                    return parsed_rows

                level_id = path[level]
                level_nodes.setdefault(level_id, {}).setdefault(path, []).extend(rows)

            result = []
            for node_id, sub_items in level_nodes.items():
                first_row = next(iter(sub_items.values()))[0]
                struct_item = first_row.structure[level]

                # NOTE if you need a strict hierarchy structure OKS_OSSR-MARKA, use this code
                # struct_item = next(
                #     item for item in next(iter(sub_items.values()))[0].structure
                #     if item[0] == id and item[2] == level + 1
                # )

                node = ParsedFileStructure(
                    id=node_id,
                    name=struct_item[1],
                    lvl=struct_item[2],
                    priority=struct_item[3],
                    children=build_tree(level + 1, sub_items)
                )
                result.append(node)

            return result

        return build_tree(0, path_dict)

    async def get_project_structure(self, attachment_id: int) -> list[ParsedFileStructure]:
        """
        Read saved structure from DB and transform: flat to hierarchical
        """
        rows = await self.message_service.get_message_attachment_work_volume_measurement(
            attachment_id=attachment_id
        )

        self._logger.debug(f"get_project_structure {rows=}")

        return await self.build_hierarchy(rows=rows)

    @classmethod
    def _format_iter_err(cls, msg: str, iterable, sep="\n\t"):
        return msg + sep + sep.join(map(str, iterable))

    async def read_project_from_file(self, file):
        filename = file.filename.lower()
        file.file.seek(0)

        if filename.endswith(FILE_TYPES.CSV.value):
            content = await file.read()
            project_df = self._read_csv_with_fallback(content)
            project_df['structure'] = project_df['structure'].apply(ast.literal_eval)

        elif filename.endswith(FILE_TYPES.XER.value):
            try:
                file.file.seek(0)
                content = await file.read()
                binary_stream = BytesIO(content)
                text_stream = TextIOWrapper(
                    binary_stream,
                    encoding="cp1251",
                    newline=""
                )
                xer_importer = XerImporter(input_xer_file=text_stream)
                project_df = xer_importer.import_file()
                if project_df.empty:
                    raise ValueError("XER file is empty or has an invalid format.")
            except Exception as e:
                await file.close()
                if isinstance(e, (ValueError, NotImplementedError)):
                    raise
                raise FileProcessingError(f"XER file processing failed: {str(e)}") from e

        elif filename.endswith(FILE_TYPES.XLSX.value):
            contents = await file.read()
            buffer = BytesIO(contents)
            project_df = pd.read_excel(buffer, engine='openpyxl')
            project_df['structure'] = project_df['structure'].apply(ast.literal_eval)

        else:
            await file.close()
            raise UnsupportedFileType(
                f"Unsupported file type. Supported file types: {', '.join(v.value for v in FILE_TYPES)}"
            )
        await file.close()

        if "granular_unit" not in project_df.columns:
            project_df["granular_unit"] = np.nan

        return project_df
