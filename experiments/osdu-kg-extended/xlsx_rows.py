"""Read plain worksheet values from XLSX using only the standard library."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CELL_REFERENCE = re.compile(r"([A-Z]+)[1-9][0-9]*")


def _column(reference: str) -> int:
    match = CELL_REFERENCE.fullmatch(reference)
    if match is None:
        raise ValueError(f"invalid XLSX cell reference: {reference!r}")
    letters = match.group(1)
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value - 1


def worksheet_rows(path, sheet_number: int):
    with zipfile.ZipFile(path) as archive:
        shared = []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        for item in root.findall(f"{NS}si"):
            shared.append("".join(node.text or "" for node in item.iter(f"{NS}t")))
        with archive.open(f"xl/worksheets/sheet{sheet_number}.xml") as stream:
            for _, row in ET.iterparse(stream, events=("end",)):
                if row.tag != f"{NS}row":
                    continue
                values = {}
                next_column = 0
                for cell in row.findall(f"{NS}c"):
                    reference = cell.get("r")
                    column = (
                        _column(reference) if reference is not None else next_column
                    )
                    next_column = column + 1
                    value = cell.find(f"{NS}v")
                    if value is None:
                        continue
                    raw = value.text
                    if cell.get("t") == "s":
                        raw = shared[int(raw)]
                    else:
                        number = float(raw)
                        raw = int(number) if number.is_integer() else number
                    values[column] = raw
                width = max(values, default=-1) + 1
                yield [values.get(index) for index in range(width)]
                row.clear()
