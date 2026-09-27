"""Read SEG-Y textual + binary headers only (no traces)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import segyio

from format_adapters_mcp.contract import envelope
from format_adapters_mcp.formats import SEGY_TEXT_MARKER

_BIN_FIELDS = (
    "JobID",
    "LineNumber",
    "ReelNumber",
    "Traces",
    "AuxTraces",
    "Interval",
    "IntervalOriginal",
    "Samples",
    "SamplesOriginal",
    "Format",
    "EnsembleFold",
    "SortingCode",
    "MeasurementSystem",
    "SEGYRevision",
    "SEGYRevisionMinor",
    "TraceFlag",
    "ExtendedHeaders",
)


def _major_revision(raw: int) -> int:
    """SEG-Y stores rev 1.0 as 0x0100; some readers already return 1."""
    if raw >= 256:
        return raw >> 8
    return raw


def read_segy_headers(path: str | Path) -> dict[str, Any]:
    source = str(path)
    p = Path(path)
    if not p.is_file():
        return envelope(
            status="error",
            reason="file_not_found",
            format_name="segy",
            source=source,
        )
    size = p.stat().st_size
    if size == 0:
        return envelope(
            status="empty",
            reason="empty_file",
            format_name="segy",
            source=source,
        )
    if size < 3600:
        return envelope(
            status="corrupt_input",
            reason="truncated_header",
            format_name="segy",
            source=source,
        )
    try:
        raw_text = p.read_bytes()[:3200]
        textual = raw_text.decode("ascii", errors="replace")
        if SEGY_TEXT_MARKER not in textual:
            textual = raw_text.decode("cp500", errors="replace")
        with segyio.open(source, "r", ignore_geometry=True) as f:
            binary: dict[str, int] = {}
            for name in _BIN_FIELDS:
                field = getattr(segyio.BinField, name)
                binary[name] = int(f.bin[field])
    except Exception as exc:
        return envelope(
            status="corrupt_input",
            reason=f"{type(exc).__name__}: {exc}",
            format_name="segy",
            source=source,
        )

    major = _major_revision(binary["SEGYRevision"])
    format_version = f"{major}.{int(binary.get('SEGYRevisionMinor') or 0)}"
    if major >= 2:
        return envelope(
            status="unsupported_version",
            reason="segy_revision_ge_2",
            format_name="segy",
            format_version=format_version,
            source=source,
            data={"textual_header": textual, "binary_header": binary},
        )
    return envelope(
        status="ok",
        format_name="segy",
        format_version=format_version,
        source=source,
        data={
            "textual_header": textual,
            "textual_marker_present": SEGY_TEXT_MARKER in textual,
            "binary_header": binary,
        },
    )
