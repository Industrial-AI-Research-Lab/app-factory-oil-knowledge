"""Typed envelope for the three read-only adapters.

`status` describes the top-level record only: the first WITSML well, or the
SCADA snapshot fields (`tag`, `value`, `unit`, `measured_at`, `source`).
`missing_fields` also lists gaps in later wells and in telemetry points, as
`wells[i].field` and `points[i].field`. A nested gap does not by itself change
`status`, so one incomplete point does not mark the whole snapshot incomplete.
"""

from __future__ import annotations

from typing import Any, Sequence

STATUSES = (
    "ok",
    "empty",
    "incomplete",
    "unsupported_version",
    "corrupt_input",
    "timeout",
    "error",
)


def envelope(
    *,
    status: str,
    format_name: str,
    source: str,
    reason: str | None = None,
    format_version: str | None = None,
    data: Any = None,
    missing_fields: Sequence[str] | None = None,
) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    return {
        "status": status,
        "reason": reason,
        "format": format_name,
        "format_version": format_version,
        "source": source,
        "data": data,
        "missing_fields": list(missing_fields or ()),
    }
