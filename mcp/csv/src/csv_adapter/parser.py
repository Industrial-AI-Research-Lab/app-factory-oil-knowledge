"""Bounded strict CSV parser"""

import ast
import csv
import io
import json
import math
import re

from pydantic import ValidationError

from .config import (
    CANONICAL_COLUMNS,
    E_DELIMITER_INVALID,
    E_DUPLICATE_ID,
    E_EDGE_INVALID,
    E_EMPTY_CONTENT,
    E_GRANULAR_UNIT_INVALID,
    E_HEADER_DUPLICATE,
    E_HEADER_UNKNOWN,
    E_INTERNAL,
    E_LIMIT_CELL,
    E_LIMIT_COLS,
    E_LIMIT_CONTENT,
    E_LIMIT_NESTED,
    E_LIMIT_ROWS,
    E_NESTED_INVALID,
    E_REQUIRED_MISSING,
    E_ROW_WIDTH,
    E_STRUCTURE_INVALID,
    E_VOLUME_INVALID,
    E_VOLUME_NON_FINITE,
    MAX_CELL_CHARS,
    MAX_COLS,
    MAX_CONTENT_CHARS,
    MAX_EDGES_PER_ROW,
    MAX_NESTED_DEPTH,
    MAX_ROWS,
    MAX_STRUCTURE_ITEMS,
    NUMERIC_PATTERN_SOURCE,
    REQUIRED_COLUMNS,
)
from stairs_csv.backend_file import detect_csv_delimiter
from .models import CsvRow, Dialect, GranularUnit, NormalizeDelimiter

__all__ = ("parse_csv_content",)

_NUMERIC_RE = re.compile(NUMERIC_PATTERN_SOURCE)
_EDGE_TYPES = frozenset({"FS", "SS", "FF", "SF"})


def _fail(code: str, hint: str, row: int | None = None) -> ValueError:
    """Build a safe error without echoing raw cell values."""
    if row is None:
        return ValueError(f"{code}: {hint}")
    return ValueError(f"{code}: row {row}: {hint}")


def _check_depth(value: object, depth: int, row: int) -> None:
    """Reject nested values deeper than MAX_NESTED_DEPTH or of foreign types."""
    if isinstance(value, (dict, list, tuple)):
        if depth > MAX_NESTED_DEPTH:
            raise _fail(E_LIMIT_NESTED, "nested value too deep", row)
        for item in value.values() if isinstance(value, dict) else value:
            _check_depth(item, depth + 1, row)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        return
    else:
        raise _fail(E_NESTED_INVALID, "unsupported nested value", row)


def _load_nested(text: str, row: int) -> object:
    """Parse a structure/edges/granular cell as JSON, else Python literal."""
    try:
        value = json.loads(text)
    except Exception:
        try:
            value = ast.literal_eval(text)
        except Exception:
            raise _fail(E_NESTED_INVALID, "invalid nested value", row) from None
    _check_depth(value, 1, row)
    return value


def _select_delimiter(content: str) -> Dialect:
    """Detect the backend CSV delimiter. stairs_csv is an immutable source slice, so the Dialect adaptation lives in this wrapper."""
    return detect_csv_delimiter(content)


def _parse_structure(text: str, row: int) -> list:
    """Parse a structure cell into (code, name, level, priority) tuples."""
    if text.strip() == "":
        return []
    value = _load_nested(text, row)
    if not isinstance(value, (list, tuple)):
        raise _fail(E_STRUCTURE_INVALID, "invalid structure value", row)
    if len(value) > MAX_STRUCTURE_ITEMS:
        raise _fail(E_LIMIT_NESTED, "too many structure items", row)
    items = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 4:
            raise _fail(E_STRUCTURE_INVALID, "invalid structure value", row)
        code, name, level, priority = entry
        if not isinstance(code, str) or not code.strip():
            raise _fail(E_STRUCTURE_INVALID, "invalid structure value", row)
        if not isinstance(name, str) or not name.strip():
            raise _fail(E_STRUCTURE_INVALID, "invalid structure value", row)
        if type(level) is not int or type(priority) is not int:
            raise _fail(E_STRUCTURE_INVALID, "invalid structure value", row)
        items.append((code, name, level, priority))
    return items


def _parse_edges(text: str, row: int) -> list:
    """Parse an edges cell into (predecessor, FS|SS|FF|SF, lag) tuples."""
    if text.strip() == "":
        return []
    value = _load_nested(text, row)
    if not isinstance(value, (list, tuple)):
        raise _fail(E_EDGE_INVALID, "invalid edge value", row)
    if len(value) > MAX_EDGES_PER_ROW:
        raise _fail(E_LIMIT_NESTED, "too many edges", row)
    edges = []
    for entry in value:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        predecessor, conn_type, lag = entry
        if not isinstance(predecessor, str) or not predecessor.strip():
            raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        if conn_type not in _EDGE_TYPES:
            raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        if type(lag) is not int:
            raise _fail(E_EDGE_INVALID, "invalid edge value", row)
        edges.append((predecessor, conn_type, lag))
    return edges


def _parse_granular_unit(text: str, row: int) -> GranularUnit | None:
    """
    Parse a granular_unit cell into a GranularUnit or None when blank.

    Accepts a dict (human-written) ({code: , name: , measurement: ,category: })
    or a 4-list [code, name, measurement,category] (backend positional export);
    both yield the same model.
    """
    if text.strip() == "":
        return None
    value = _load_nested(text, row)
    try:
        if isinstance(value, dict):
            if set(value) - {"code", "name", "measurement", "category"}:
                raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row)
            code, name = value.get("code"), value.get("name")
            measurement, category = value.get("measurement"), value.get("category", "")
            if category is None:
                category = ""
        elif isinstance(value, (list, tuple)) and len(value) == 4:
            code, name, measurement, category = value
            if category is None:
                category = ""
        else:
            raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row)
        if not isinstance(code, str) or not code.strip():
            raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row)
        if not isinstance(name, str) or not name.strip():
            raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row)
        if not isinstance(measurement, str) or not measurement.strip():
            raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row)
        if not isinstance(category, str):
            raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row)
        return GranularUnit(code=code, name=name, measurement=measurement, category=category)
    except ValueError:
        raise
    except Exception:
        raise _fail(E_GRANULAR_UNIT_INVALID, "invalid granular unit", row) from None


def parse_csv_content(content: str, delimiter: NormalizeDelimiter = "auto") -> tuple[list[CsvRow], Dialect]:
    """Parse CSV text strictly; return (rows, dialect) with safe E_* errors."""
    if delimiter not in ("auto", ";", ","):
        raise _fail(E_DELIMITER_INVALID, "unsupported delimiter")
    if not isinstance(content, str):
        raise _fail(E_EMPTY_CONTENT, "empty content")
    if len(content) > MAX_CONTENT_CHARS:
        raise _fail(E_LIMIT_CONTENT, "content too large")
    # BOM excel-экспорта; без снятия сломает имя первой колонки
    if content.startswith("\ufeff"):
        content = content[1:]
    if content.strip() == "":
        raise _fail(E_EMPTY_CONTENT, "empty content")
    dialect: Dialect = _select_delimiter(content) if delimiter == "auto" else delimiter
    try:
        records = list(csv.reader(io.StringIO(content), delimiter=dialect, strict=True))
    except csv.Error:
        raise _fail(E_ROW_WIDTH, "malformed csv record") from None
    except Exception:
        raise RuntimeError(E_INTERNAL) from None
    if not records:
        raise _fail(E_EMPTY_CONTENT, "empty content")
    for raw_cell in records[0]:
        if len(raw_cell) > MAX_CELL_CHARS:
            raise _fail(E_LIMIT_CELL, "cell too large")
    header = [cell.strip() for cell in records[0]]
    if len(header) > MAX_COLS:
        raise _fail(E_LIMIT_COLS, "too many columns")
    if len(set(header)) != len(header):
        raise _fail(E_HEADER_DUPLICATE, "duplicate header")
    for name in header:
        if name not in CANONICAL_COLUMNS:
            raise _fail(E_HEADER_UNKNOWN, "unknown header")
    if not REQUIRED_COLUMNS.issubset(set(header)):
        raise _fail(E_REQUIRED_MISSING, "missing required header")
    index = {name: pos for pos, name in enumerate(header)}
    data = records[1:]
    if len(data) > MAX_ROWS:
        raise _fail(E_LIMIT_ROWS, "too many rows")
    rows: list[CsvRow] = []
    seen: set[str] = set()
    has_structure = "structure" in index
    has_edges = "edges" in index
    has_granular = "granular_unit" in index
    for pos, record in enumerate(data, start=1):
        if len(record) != len(header):
            raise _fail(E_ROW_WIDTH, "row width mismatch", pos)
        for cell in record:
            if len(cell) > MAX_CELL_CHARS:
                raise _fail(E_LIMIT_CELL, "cell too large", pos)
        activity_id = record[index["activity_id"]]
        activity_name = record[index["activity_name"]]
        measurement = record[index["measurement"]]
        if not activity_id.strip() or not activity_name.strip() or not measurement.strip():
            raise _fail(E_REQUIRED_MISSING, "missing required value", pos)
        activity_key = activity_id.strip()
        if activity_key in seen:
            raise _fail(E_DUPLICATE_ID, "duplicate activity id", pos)
        seen.add(activity_key)
        volume_text = record[index["volume"]].strip()
        if not volume_text or _NUMERIC_RE.fullmatch(volume_text) is None:
            raise _fail(E_VOLUME_INVALID, "invalid volume", pos)
        try:
            volume = float(volume_text)
        except Exception:
            raise _fail(E_VOLUME_INVALID, "invalid volume", pos) from None
        if not math.isfinite(volume):
            raise _fail(E_VOLUME_NON_FINITE, "non-finite volume", pos)
        structure = _parse_structure(record[index["structure"]], pos) if has_structure else []
        edges = _parse_edges(record[index["edges"]], pos) if has_edges else []
        granular = _parse_granular_unit(record[index["granular_unit"]], pos) if has_granular else None
        try:
            rows.append(
                CsvRow(
                    activity_id=activity_id,
                    activity_name=activity_name,
                    volume=volume,
                    measurement=measurement,
                    structure=structure,
                    edges=edges,
                    granular_unit=granular,
                )
            )
        except ValidationError as exc:
            loc: object = None
            try:
                errs = exc.errors()
                if errs:
                    raw_loc = errs[0].get("loc", ())
                    loc = raw_loc[0] if raw_loc else None
            except Exception:
                raise RuntimeError(E_INTERNAL) from None
            if loc == "granular_unit":
                code = E_GRANULAR_UNIT_INVALID
            elif loc == "edges":
                code = E_EDGE_INVALID
            elif loc == "structure":
                code = E_STRUCTURE_INVALID
            elif loc == "volume":
                code = E_VOLUME_INVALID
            elif loc in ("activity_id", "activity_name", "measurement"):
                code = E_REQUIRED_MISSING
            else:
                raise RuntimeError(E_INTERNAL) from None
            raise _fail(code, "invalid row value", pos) from None
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
    return rows, dialect
