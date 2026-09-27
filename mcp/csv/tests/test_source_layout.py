"""Contract for the packaged backend source and isolated CSV boundary."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_source_snapshot_is_packaged() -> None:
    """The original backend file is present in the MCP package."""
    source_file = (
        ROOT
        / "sources"
        / "stairs-backend"
        / "services"
        / "backend"
        / "bll"
        / "services"
        / "file.py"
    )
    assert source_file.is_file()
    text = source_file.read_text(encoding="utf-8")
    assert "class FileParserBLLService" in text
    assert "CSV_ENCODINGS = (\"utf-8-sig\", \"cp1251\")" in text


def test_csv_runtime_does_not_import_backend_application_code() -> None:
    """Only the isolated CSV boundary is used by the runtime parser."""
    parser = (ROOT / "src" / "csv_adapter" / "parser.py").read_text(
        encoding="utf-8"
    )
    download = (ROOT / "src" / "csv_adapter" / "download.py").read_text(
        encoding="utf-8"
    )
    assert "from backend" not in parser + download
    assert "from stairs_csv.backend_file import" in parser + download
