"""Strict normalization helpers for the selected OSDU manifests."""

from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import unquote


OSDU_ID_RE = re.compile(
    r"^osdu:(?P<entity_group>[a-z0-9-]+)--(?P<entity_type>[A-Za-z0-9-]+):"
    r"(?P<record_id>[^:\s].*?)$"
)


def normalize_osdu_id(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = unquote(value.strip())
    if normalized.endswith(":"):
        normalized = normalized[:-1]
    return normalized or None


def parse_osdu_id(value: Any) -> tuple[str, str, str] | None:
    normalized = normalize_osdu_id(value)
    if normalized is None:
        return None
    match = OSDU_ID_RE.fullmatch(normalized)
    if match is None:
        return None
    return (
        match.group("entity_group"),
        match.group("entity_type"),
        match.group("record_id"),
    )


def first_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(float(value)) else None


def scalar_or_json(value: Any) -> str | int | float | bool | None:
    if isinstance(value, (str, bool)):
        return value
    return finite_number(value)
