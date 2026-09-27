from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import ToolFailure
from ..http_io import download_bytes, temporary_call_directory, upload_bytes
from . import DocumentSpec, FragmentRecord, IndexDocumentsResult
from .formats import parse_document, supported_content_type

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_DOCUMENTS = 100
INDEX_CONTENT_TYPE = "application/vnd.sqlite3"
_SCHEMA_STATEMENTS = (
    "CREATE TABLE index_metadata (schema_version INTEGER NOT NULL)",
    (
        "CREATE TABLE sources (source_id TEXT PRIMARY KEY, filename TEXT NOT NULL, "
        "content_type TEXT NOT NULL, metadata_json TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE fragments (fragment_id TEXT PRIMARY KEY, "
        "source_id TEXT NOT NULL REFERENCES sources(source_id), "
        "locator TEXT NOT NULL, text TEXT NOT NULL, text_sha256 TEXT NOT NULL, "
        "metadata_json TEXT NOT NULL, UNIQUE(source_id, locator))"
    ),
    (
        "CREATE VIRTUAL TABLE fragment_search USING fts5("
        "fragment_id UNINDEXED, text, tokenize='unicode61')"
    ),
)


async def index_documents_impl(
    documents: list[dict[str, Any]],
    upload_url: str,
) -> IndexDocumentsResult:
    specs = _validate_documents(documents)
    settings = Settings.from_env()
    parsed: list[tuple[DocumentSpec, list[FragmentRecord]]] = []
    total_source_bytes = 0
    for spec in specs:
        payload = await download_bytes(
            spec.url,
            max_bytes=settings.max_download_bytes,
        )
        total_source_bytes += len(payload)
        if total_source_bytes > settings.max_download_bytes:
            logger.warning(
                "[DOCUMENT_INDEX] documents=%d source_bytes=%d code=DOCUMENTS_TOO_LARGE",
                len(specs),
                total_source_bytes,
            )
            raise ToolFailure(
                "DOCUMENTS_TOO_LARGE",
                "Documents exceed the configured combined download limit",
            )
        fragments = await asyncio.to_thread(parse_document, spec, payload)
        parsed.append((spec, fragments))

    with temporary_call_directory() as directory:
        index_path = directory / "documents.sqlite3"
        fragment_count = await asyncio.to_thread(_build_index, index_path, parsed)
        payload = await asyncio.to_thread(index_path.read_bytes)
        if len(payload) > settings.max_download_bytes:
            raise ToolFailure(
                "DOCUMENT_INDEX_TOO_LARGE",
                "Document index exceeds the configured download limit",
            )
        sha256 = hashlib.sha256(payload).hexdigest()
        await upload_bytes(upload_url, payload, content_type=INDEX_CONTENT_TYPE)

    logger.info(
        "[DOCUMENT_INDEX] documents=%d fragments=%d bytes=%d sha256=%s — uploaded",
        len(specs),
        fragment_count,
        len(payload),
        sha256,
    )
    return IndexDocumentsResult(
        schema_version=SCHEMA_VERSION,
        document_count=len(specs),
        fragment_count=fragment_count,
        sha256=sha256,
        size_bytes=len(payload),
    )


def _validate_documents(documents: object) -> list[DocumentSpec]:
    if not isinstance(documents, list) or not documents:
        raise ToolFailure("INPUT_INVALID", "documents must be a non-empty list")
    if len(documents) > MAX_DOCUMENTS:
        raise ToolFailure(
            "INPUT_INVALID",
            f"documents must contain at most {MAX_DOCUMENTS} entries",
        )
    specs = [_document_spec(item) for item in documents]
    source_ids = [spec.source_id for spec in specs]
    if len(set(source_ids)) != len(source_ids):
        raise ToolFailure(
            "DOCUMENT_SOURCE_DUPLICATE",
            "Document source_id values must be unique",
        )
    return sorted(specs, key=lambda spec: spec.source_id)


def _document_spec(value: object) -> DocumentSpec:
    if not isinstance(value, dict):
        raise ToolFailure("INPUT_INVALID", "Each document must be an object")
    required = ("source_id", "filename", "content_type", "url")
    fields: dict[str, str] = {}
    for name in required:
        item = value.get(name)
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise ToolFailure(
                "INPUT_INVALID",
                f"Document {name} must be a non-empty string without surrounding whitespace",
            )
        if "\x00" in item:
            raise ToolFailure(
                "INPUT_INVALID", f"Document {name} contains a NUL character"
            )
        fields[name] = item
    fields["content_type"] = supported_content_type(fields["content_type"])
    metadata = {key: item for key, item in value.items() if key not in required}
    try:
        json.dumps(metadata, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ToolFailure(
            "INPUT_INVALID",
            "Document metadata must be JSON serializable",
        ) from None
    return DocumentSpec(metadata=metadata, **fields)


def _build_index(
    path: Path,
    documents: list[tuple[DocumentSpec, list[FragmentRecord]]],
) -> int:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO index_metadata(schema_version) VALUES (?)",
            (SCHEMA_VERSION,),
        )
        fragment_count = 0
        for spec, fragments in documents:
            connection.execute(
                "INSERT INTO sources VALUES (?, ?, ?, ?)",
                (
                    spec.source_id,
                    spec.filename,
                    spec.content_type,
                    _canonical_json(spec.metadata),
                ),
            )
            for fragment in fragments:
                locator = unicodedata.normalize("NFC", fragment.locator.strip())
                text_bytes = fragment.text.encode("utf-8")
                text_sha256 = hashlib.sha256(text_bytes).hexdigest()
                fragment_id = hashlib.sha256(
                    b"\x00".join(
                        (
                            spec.source_id.encode("utf-8"),
                            locator.encode("utf-8"),
                            text_bytes,
                        )
                    )
                ).hexdigest()
                connection.execute(
                    "INSERT INTO fragments VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        fragment_id,
                        spec.source_id,
                        locator,
                        fragment.text,
                        text_sha256,
                        _canonical_json(fragment.metadata),
                    ),
                )
                connection.execute(
                    "INSERT INTO fragment_search(fragment_id, text) VALUES (?, ?)",
                    (fragment_id, fragment.text),
                )
                fragment_count += 1
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
        return fragment_count
    except sqlite3.IntegrityError:
        connection.rollback()
        raise ToolFailure(
            "DOCUMENT_FRAGMENT_DUPLICATE",
            "Document fragments must have unique locators within each source",
        ) from None
    finally:
        connection.close()


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
