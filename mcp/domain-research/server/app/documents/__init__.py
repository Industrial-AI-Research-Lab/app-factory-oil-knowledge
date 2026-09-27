from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DocumentSpec:
    source_id: str
    filename: str
    content_type: str
    url: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class FragmentRecord:
    locator: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class IndexDocumentsResult:
    schema_version: int
    document_count: int
    fragment_count: int
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SearchHit:
    fragment_id: str
    source_id: str
    filename: str
    content_type: str
    locator: str
    excerpt: str
    score: float
    metadata: dict[str, Any]
    source_metadata: dict[str, Any]


@dataclass(frozen=True)
class SearchDocumentsResult:
    results: list[SearchHit]


@dataclass(frozen=True)
class DocumentFragment:
    fragment_id: str
    source_id: str
    filename: str
    content_type: str
    locator: str
    text: str
    text_sha256: str
    truncated: bool
    metadata: dict[str, Any]
    source_metadata: dict[str, Any]


@dataclass(frozen=True)
class ReadDocumentFragmentsResult:
    fragments: list[DocumentFragment]
