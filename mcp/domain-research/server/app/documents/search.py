from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..config import Settings
from ..errors import ToolFailure
from ..http_io import download_bytes, temporary_call_directory
from . import (
    DocumentFragment,
    ReadDocumentFragmentsResult,
    SearchDocumentsResult,
    SearchHit,
)
from .excerpts import document_excerpt
from .formats import parse_json_object
from .index import SCHEMA_VERSION

logger = logging.getLogger(__name__)

MAX_FRAGMENT_CHARS = 8_000


async def search_documents_impl(
    index_url: str,
    query: str,
    source_ids: list[str] | None = None,
    limit: int = 20,
    expected_index_sha256: str | None = None,
) -> SearchDocumentsResult:
    fts_query, tokens = _search_query(query)
    selected_sources = _source_ids(source_ids)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ToolFailure("INPUT_INVALID", "limit must be between 1 and 20")
    index_bytes = await download_bytes(
        index_url,
        max_bytes=Settings.from_env().max_download_bytes,
        expected_sha256=expected_index_sha256,
    )
    with temporary_call_directory() as directory:
        path = directory / "documents.sqlite3"
        await asyncio.to_thread(path.write_bytes, index_bytes)
        results = await asyncio.to_thread(
            _search_index,
            path,
            fts_query,
            tokens,
            selected_sources,
            limit,
        )
    logger.info(
        "[DOCUMENT_SEARCH] source_filter_count=%s limit=%d result_count=%d — completed",
        "all" if selected_sources is None else len(selected_sources),
        limit,
        len(results),
    )
    return SearchDocumentsResult(results=results)


async def read_document_fragments_impl(
    index_url: str,
    fragment_ids: list[str],
    max_chars_per_fragment: int = MAX_FRAGMENT_CHARS,
    expected_index_sha256: str | None = None,
) -> ReadDocumentFragmentsResult:
    requested_ids = _fragment_ids(fragment_ids)
    if (
        isinstance(max_chars_per_fragment, bool)
        or not isinstance(max_chars_per_fragment, int)
        or not 1 <= max_chars_per_fragment <= MAX_FRAGMENT_CHARS
    ):
        raise ToolFailure(
            "INPUT_INVALID",
            f"max_chars_per_fragment must be between 1 and {MAX_FRAGMENT_CHARS}",
        )
    index_bytes = await download_bytes(
        index_url,
        max_bytes=Settings.from_env().max_download_bytes,
        expected_sha256=expected_index_sha256,
    )
    with temporary_call_directory() as directory:
        path = directory / "documents.sqlite3"
        await asyncio.to_thread(path.write_bytes, index_bytes)
        fragments = await asyncio.to_thread(
            _read_fragments,
            path,
            requested_ids,
            max_chars_per_fragment,
        )
    logger.info(
        "[DOCUMENT_SEARCH] operation=read requested=%d returned=%d — completed",
        len(requested_ids),
        len(fragments),
    )
    return ReadDocumentFragmentsResult(fragments=fragments)


def _search_query(query: object) -> tuple[str, list[str]]:
    if (
        not isinstance(query, str)
        or not query.strip()
        or "\x00" in query
        or len(query) > 2_000
    ):
        raise ToolFailure("INPUT_INVALID", "query must contain searchable text")
    tokens = re.findall(r"[^\W_]+", query, re.UNICODE)
    if not tokens:
        raise ToolFailure("INPUT_INVALID", "query must contain searchable text")
    # Agents describe what they look for in many words; requiring every word
    # returns nothing, so any word matches and bm25 ranks fuller matches first.
    return " OR ".join(f'"{token}"' for token in tokens), tokens


def _source_ids(source_ids: object) -> list[str] | None:
    if source_ids is None:
        return None
    if not isinstance(source_ids, list):
        raise ToolFailure("INPUT_INVALID", "source_ids must be a list or null")
    if len(source_ids) > 100:
        raise ToolFailure(
            "INPUT_INVALID",
            "source_ids must contain at most 100 entries",
        )
    if any(
        not isinstance(item, str)
        or not item.strip()
        or item != item.strip()
        or "\x00" in item
        for item in source_ids
    ):
        raise ToolFailure("INPUT_INVALID", "source_ids must contain non-empty strings")
    return list(dict.fromkeys(source_ids))


def _fragment_ids(fragment_ids: object) -> list[str]:
    if not isinstance(fragment_ids, list) or not fragment_ids:
        raise ToolFailure("INPUT_INVALID", "fragment_ids must be a non-empty list")
    if len(fragment_ids) > 100:
        raise ToolFailure(
            "INPUT_INVALID",
            "fragment_ids must contain at most 100 entries",
        )
    if any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
        for item in fragment_ids
    ):
        raise ToolFailure(
            "INPUT_INVALID", "fragment_ids must contain non-empty strings"
        )
    return fragment_ids


def _open_index(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        metadata = connection.execute(
            "SELECT schema_version FROM index_metadata"
        ).fetchone()
        if version == SCHEMA_VERSION and metadata and metadata[0] == SCHEMA_VERSION:
            return connection
    except (sqlite3.Error, TypeError):
        connection.close()
        raise
    connection.close()
    raise ToolFailure(
        "DOCUMENT_INDEX_VERSION_UNSUPPORTED",
        "Document index schema version is not supported",
    )


def _search_index(
    path: Path,
    fts_query: str,
    query_tokens: list[str],
    source_ids: list[str] | None,
    limit: int,
) -> list[SearchHit]:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_index(path)
        filters = ""
        parameters: list[Any] = [fts_query]
        if source_ids is not None:
            if not source_ids:
                return []
            placeholders = ", ".join("?" for _ in source_ids)
            filters = f" AND f.source_id IN ({placeholders})"
            parameters.extend(source_ids)
        parameters.append(limit)
        rows = connection.execute(
            "SELECT f.fragment_id, f.source_id, s.filename, s.content_type, "
            "f.locator, f.text, f.metadata_json, s.metadata_json AS source_metadata_json, "
            "bm25(fragment_search) AS score "
            "FROM fragment_search "
            "JOIN fragments f ON f.fragment_id = fragment_search.fragment_id "
            "JOIN sources s ON s.source_id = f.source_id "
            "WHERE fragment_search MATCH ?"
            f"{filters} ORDER BY score, f.fragment_id LIMIT ?",
            parameters,
        ).fetchall()
        return [
            SearchHit(
                fragment_id=row["fragment_id"],
                source_id=row["source_id"],
                filename=row["filename"],
                content_type=row["content_type"],
                locator=row["locator"],
                excerpt=document_excerpt(row["text"], query_tokens),
                score=float(row["score"]),
                metadata=parse_json_object(row["metadata_json"]),
                source_metadata=parse_json_object(row["source_metadata_json"]),
            )
            for row in rows
        ]
    except ToolFailure:
        raise
    except (sqlite3.Error, TypeError, ValueError, AttributeError):
        raise ToolFailure(
            "DOCUMENT_INDEX_INVALID",
            "Document index is invalid or truncated",
        ) from None
    finally:
        if connection is not None:
            connection.close()


def _read_fragments(
    path: Path,
    fragment_ids: list[str],
    max_chars: int,
) -> list[DocumentFragment]:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_index(path)
        unique_ids = list(dict.fromkeys(fragment_ids))
        placeholders = ", ".join("?" for _ in unique_ids)
        rows = connection.execute(
            "SELECT f.fragment_id, f.source_id, s.filename, s.content_type, "
            "f.locator, f.text, f.text_sha256, f.metadata_json, "
            "s.metadata_json AS source_metadata_json "
            "FROM fragments f JOIN sources s ON s.source_id = f.source_id "
            f"WHERE f.fragment_id IN ({placeholders})",
            unique_ids,
        ).fetchall()
        by_id = {row["fragment_id"]: row for row in rows}
        if any(fragment_id not in by_id for fragment_id in unique_ids):
            raise ToolFailure(
                "DOCUMENT_FRAGMENT_NOT_FOUND",
                "One or more document fragments were not found",
            )
        return [
            _document_fragment(by_id[fragment_id], max_chars)
            for fragment_id in fragment_ids
        ]
    except ToolFailure:
        raise
    except (sqlite3.Error, TypeError, ValueError, AttributeError):
        raise ToolFailure(
            "DOCUMENT_INDEX_INVALID",
            "Document index is invalid or truncated",
        ) from None
    finally:
        if connection is not None:
            connection.close()


def _document_fragment(row: sqlite3.Row, max_chars: int) -> DocumentFragment:
    text = row["text"]
    return DocumentFragment(
        fragment_id=row["fragment_id"],
        source_id=row["source_id"],
        filename=row["filename"],
        content_type=row["content_type"],
        locator=row["locator"],
        text=text[:max_chars],
        text_sha256=row["text_sha256"],
        truncated=len(text) > max_chars,
        metadata=parse_json_object(row["metadata_json"]),
        source_metadata=parse_json_object(row["source_metadata_json"]),
    )
