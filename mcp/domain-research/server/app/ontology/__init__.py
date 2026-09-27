from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ValidateOntologyResult:
    schema_version: int
    ontology_id: str
    version: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class ReadOntologyResult:
    sha256: str
    ontology: dict[str, Any]


@dataclass(frozen=True)
class ValidateFactBatchResult:
    schema_version: int
    index_sha256: str
    ontology_sha256: str
    ontology_id: str
    ontology_version: str
    fact_count: int
    sha256: str
    size_bytes: int
