import hashlib
import json
import sqlite3

import pytest
from app.documents.search import (
    read_document_fragments_impl,
    search_documents_impl,
)
from app.errors import ToolFailure
from document_test_support import build_single_document


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [None, "", "   ", "---"])
async def test_search_rejects_query_without_searchable_text(query):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await search_documents_impl("https://objects.test/index", query)


@pytest.mark.asyncio
async def test_search_rejects_oversized_query():
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await search_documents_impl("https://objects.test/index", "x" * 2_001)


@pytest.mark.asyncio
async def test_search_rejects_too_many_source_filters():
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await search_documents_impl(
            "https://objects.test/index",
            "query",
            source_ids=[f"source-{index}" for index in range(101)],
        )


@pytest.mark.asyncio
async def test_unicode_query_matches_json_text(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        json.dumps(
            [{"text": "Измерено межфазное натяжение."}],
            ensure_ascii=False,
        ).encode(),
        content_type="application/json",
        filename="source.json",
    )
    httpx_mock.add_response(content=index_bytes)

    result = await search_documents_impl(
        "https://objects.test/index",
        "межфазное натяжение",
    )

    assert len(result.results) == 1
    assert "межфазное натяжение" in result.results[0].excerpt.casefold()


@pytest.mark.asyncio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_search_rejects_index_checksum_mismatch(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"searchable text",
        content_type="text/plain",
        filename="source.txt",
    )
    httpx_mock.add_response(content=index_bytes)

    with pytest.raises(ToolFailure, match="SOURCE_CHECKSUM_MISMATCH"):
        await search_documents_impl(
            "https://objects.test/index",
            "searchable",
            expected_index_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_search_rejects_index_with_invalid_fragment_fields(httpx_mock, tmp_path):
    index_path = tmp_path / "invalid.sqlite3"
    connection = sqlite3.connect(index_path)
    connection.executescript(
        "CREATE TABLE index_metadata(schema_version INTEGER);"
        "INSERT INTO index_metadata VALUES(1);"
        "CREATE TABLE sources(source_id, filename, content_type, metadata_json);"
        "CREATE TABLE fragments("
        "fragment_id, source_id, locator, text, text_sha256, metadata_json);"
        "CREATE VIRTUAL TABLE fragment_search USING fts5("
        "fragment_id UNINDEXED, text, tokenize='unicode61');"
        "PRAGMA user_version=1;"
    )
    connection.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?)",
        ("source", "source.txt", "text/plain", "{}"),
    )
    connection.execute(
        "INSERT INTO fragments VALUES (?, ?, ?, ?, ?, ?)",
        ("a" * 64, "source", "document", None, "0" * 64, "{}"),
    )
    connection.execute(
        "INSERT INTO fragment_search VALUES (?, ?)",
        ("a" * 64, "searchable"),
    )
    connection.commit()
    connection.close()
    index_bytes = index_path.read_bytes()
    httpx_mock.add_response(content=index_bytes)

    with pytest.raises(ToolFailure, match="DOCUMENT_INDEX_INVALID"):
        await search_documents_impl(
            "https://objects.test/index",
            "searchable",
            expected_index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        )


@pytest.mark.asyncio
async def test_read_rejects_unknown_fragment_id(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"known text",
        content_type="text/plain",
        filename="source.txt",
    )
    httpx_mock.add_response(content=index_bytes)

    with pytest.raises(ToolFailure, match="DOCUMENT_FRAGMENT_NOT_FOUND"):
        await read_document_fragments_impl(
            "https://objects.test/index",
            ["0" * 64],
        )


@pytest.mark.asyncio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
async def test_read_rejects_index_checksum_mismatch(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"known text",
        content_type="text/plain",
        filename="source.txt",
    )
    httpx_mock.add_response(content=index_bytes)

    with pytest.raises(ToolFailure, match="SOURCE_CHECKSUM_MISMATCH"):
        await read_document_fragments_impl(
            "https://objects.test/index",
            ["0" * 64],
            expected_index_sha256="0" * 64,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fragment_text", "metadata_json"),
    [(None, "{}"), ("valid text", "null")],
)
async def test_read_rejects_index_with_invalid_fragment_fields(
    httpx_mock,
    tmp_path,
    fragment_text,
    metadata_json,
):
    index_path = tmp_path / "invalid.sqlite3"
    connection = sqlite3.connect(index_path)
    connection.executescript(
        "CREATE TABLE index_metadata(schema_version INTEGER);"
        "INSERT INTO index_metadata VALUES(1);"
        "CREATE TABLE sources(source_id, filename, content_type, metadata_json);"
        "CREATE TABLE fragments("
        "fragment_id, source_id, locator, text, text_sha256, metadata_json);"
        "PRAGMA user_version=1;"
    )
    connection.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?)",
        ("source", "source.txt", "text/plain", "{}"),
    )
    connection.execute(
        "INSERT INTO fragments VALUES (?, ?, ?, ?, ?, ?)",
        (
            "a" * 64,
            "source",
            "document",
            fragment_text,
            "0" * 64,
            metadata_json,
        ),
    )
    connection.commit()
    connection.close()
    index_bytes = index_path.read_bytes()
    httpx_mock.add_response(content=index_bytes)

    with pytest.raises(ToolFailure, match="DOCUMENT_INDEX_INVALID"):
        await read_document_fragments_impl(
            "https://objects.test/index",
            ["a" * 64],
            expected_index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fragment_ids", [None, [], [""], ["not-a-sha256"]])
async def test_read_rejects_malformed_fragment_ids(fragment_ids):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await read_document_fragments_impl(
            "https://objects.test/index",
            fragment_ids,
        )


@pytest.mark.asyncio
async def test_search_rejects_truncated_index(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"searchable text",
        content_type="text/plain",
        filename="source.txt",
    )
    httpx_mock.add_response(content=index_bytes[:100])

    with pytest.raises(ToolFailure, match="DOCUMENT_INDEX_INVALID"):
        await search_documents_impl("https://objects.test/index", "searchable")


@pytest.mark.asyncio
async def test_search_accepts_one_and_twenty_result_limits(httpx_mock):
    records = "\n".join(
        json.dumps({"text": f"common term {index}"}) for index in range(21)
    ).encode()
    index_bytes = await build_single_document(
        httpx_mock,
        records,
        content_type="application/x-ndjson",
        filename="source.jsonl",
    )
    httpx_mock.add_response(content=index_bytes)
    one = await search_documents_impl("https://objects.test/index", "common", limit=1)
    httpx_mock.add_response(content=index_bytes)
    twenty = await search_documents_impl(
        "https://objects.test/index",
        "common",
        limit=20,
    )

    assert len(one.results) == 1
    assert len(twenty.results) == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [0, 21, True])
async def test_search_rejects_out_of_range_limit(limit):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await search_documents_impl(
            "https://objects.test/index",
            "query",
            limit=limit,
        )


@pytest.mark.asyncio
async def test_descriptive_query_finds_fragment_without_every_word(httpx_mock):
    index_bytes = await build_single_document(
        httpx_mock,
        b"Recipe Alpha contains Surfactant A. Interfacial tension was 0.12 mN/m.",
        content_type="text/plain",
        filename="sample-a.txt",
    )
    httpx_mock.add_response(content=index_bytes)

    result = await search_documents_impl(
        "https://objects.test/index",
        "Recipe Alpha component concentration interfacial tension measurement method",
    )

    assert len(result.results) == 1
    assert "Recipe Alpha" in result.results[0].excerpt
