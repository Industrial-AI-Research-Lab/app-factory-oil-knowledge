from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BuildCollectionResult:
    schema_version: int
    ontology_id: str
    ontology_version: str
    index_sha256: str
    ontology_sha256: str
    fact_batch_sha256s: list[str]
    input_fingerprint: str
    counts: dict[str, int]
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class CollectionTable:
    name: str
    columns: list[str]


@dataclass(frozen=True)
class InspectCollectionResult:
    schema_version: int
    ontology_id: str
    ontology_version: str
    input_checksums: dict[str, Any]
    counts: dict[str, int]
    tables: list[CollectionTable]
    collection_sha256: str = ""


@dataclass(frozen=True)
class SourceRef:
    source_id: str
    fragment_id: str


@dataclass(frozen=True)
class QueryCollectionResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    truncated: bool
    source_refs: list[SourceRef]

    collection_sha256: str = ""
    sql: str = ""
    parameters: list[Any] = field(default_factory=list)
    sha256: str | None = None
    size_bytes: int | None = None
