from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FinalizeNewsResult:
    schema_version: int
    source_bundle_sha256: str
    publication_count: int
    event_count: int
    sha256: str
    size_bytes: int
    publication_dates: list[dict]
