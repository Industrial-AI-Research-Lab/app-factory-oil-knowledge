"""OOXML cell-position handling for the standard-library XLSX reader."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from xlsx_rows import worksheet_rows

NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def write_xlsx(path: Path, cells: str) -> None:
    shared = (
        f'<sst xmlns="{NAMESPACE}"><si><t>first</t></si>' "<si><t>third</t></si></sst>"
    )
    sheet = (
        f'<worksheet xmlns="{NAMESPACE}"><sheetData><row r="1">'
        f"{cells}</row></sheetData></worksheet>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


class XlsxRowsTests(unittest.TestCase):
    def test_missing_cell_reference_uses_next_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xlsx"
            write_xlsx(path, '<c t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c>')
            try:
                rows = list(worksheet_rows(path, 1))
            except TypeError as exc:
                self.fail(f"missing cell reference was not inferred: {exc}")

        self.assertEqual(rows, [["first", None, "third"]])

    def test_malformed_explicit_reference_has_a_domain_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.xlsx"
            write_xlsx(path, '<c r="1A" t="s"><v>0</v></c>')
            with self.assertRaisesRegex(ValueError, "invalid XLSX cell reference"):
                list(worksheet_rows(path, 1))


if __name__ == "__main__":
    unittest.main()
