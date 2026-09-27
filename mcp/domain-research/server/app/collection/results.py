from __future__ import annotations

import math
import sqlite3
from typing import Any

from ..errors import ToolFailure
from . import SourceRef


def typed_row(columns: list[str], row: sqlite3.Row) -> dict[str, Any]:
    value = {column: row[index] for index, column in enumerate(columns)}
    if any(
        isinstance(item, bytes) or isinstance(item, float) and not math.isfinite(item)
        for item in value.values()
    ):
        raise ToolFailure(
            "SQLITE_QUERY_INVALID", "SQLite result contains unsupported values"
        )
    return value


def source_refs(columns: list[str], rows: list[dict[str, Any]]) -> list[SourceRef]:
    if "source_id" not in columns or "fragment_id" not in columns:
        return []
    return [
        SourceRef(*pair)
        for pair in sorted(
            {
                (row["source_id"], row["fragment_id"])
                for row in rows
                if isinstance(row["source_id"], str)
                and isinstance(row["fragment_id"], str)
            }
        )
    ]
