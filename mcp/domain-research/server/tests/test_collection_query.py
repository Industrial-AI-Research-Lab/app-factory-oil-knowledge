from __future__ import annotations

import json
import hashlib

import pytest
from app.collection import SourceRef
from app.collection.query import inspect_collection_impl, query_collection_impl
from collection_query_test_support import collection_payload


@pytest.mark.asyncio
async def test_query_collection_returns_join_rows_and_source_refs(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await query_collection_impl(
        "https://objects.test/collection",
        "SELECT source.node_id AS source_node_id, target.node_id AS target_node_id, relations.source_id, relations.fragment_id FROM relations JOIN nodes AS source ON source.node_id = relations.source_node_id JOIN nodes AS target ON target.node_id = relations.target_node_id",
        [],
        20,
    )

    assert result.rows == [
        {
            "source_node_id": "node:one",
            "target_node_id": "node:two",
            "source_id": "source-a",
            "fragment_id": "fragment-a",
        }
    ]
    assert result.source_refs == [SourceRef("source-a", "fragment-a")]


@pytest.mark.asyncio
async def test_query_collection_retains_columns_for_empty_result(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await query_collection_impl(
        "https://objects.test/collection",
        "SELECT node_id, source_id, fragment_id FROM nodes WHERE 1 = 0",
        [],
        20,
    )

    assert result.columns == ["node_id", "source_id", "fragment_id"]
    assert result.rows == []
    assert result.truncated is False
    assert result.source_refs == []


@pytest.mark.asyncio
async def test_query_collection_bounds_rows_and_uploads_exact_result(
    httpx_mock, tmp_path
):
    payload = collection_payload(tmp_path)
    httpx_mock.add_response(url="https://objects.test/collection", content=payload)
    httpx_mock.add_response(method="PUT", url="https://objects.test/result")

    result = await query_collection_impl(
        "https://objects.test/collection",
        "SELECT node_id, source_id, fragment_id FROM nodes ORDER BY node_id",
        [],
        1,
        "https://objects.test/result",
    )

    uploaded = httpx_mock.get_requests()[-1].content
    assert result.columns == ["node_id", "source_id", "fragment_id"]
    assert result.rows == [
        {"node_id": "node:one", "source_id": "source-a", "fragment_id": "fragment-a"}
    ]
    assert result.truncated is True
    assert json.loads(uploaded) == {
        "collection_sha256": hashlib.sha256(payload).hexdigest(),
        "sql": result.sql,
        "parameters": [],
        "columns": result.columns,
        "rows": result.rows,
        "truncated": True,
        "source_refs": [{"source_id": "source-a", "fragment_id": "fragment-a"}],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("max_rows", "row_count", "truncated"),
    [(3, 2, False), (2, 2, False), (1, 1, True)],
)
async def test_query_collection_applies_row_cap(
    httpx_mock, tmp_path, max_rows, row_count, truncated
):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await query_collection_impl(
        "https://objects.test/collection",
        "SELECT node_id FROM nodes ORDER BY node_id",
        [],
        max_rows,
    )

    assert len(result.rows) == row_count
    assert result.truncated is truncated


@pytest.mark.asyncio
async def test_query_collection_allows_cte_and_parameter_punctuation(
    httpx_mock, tmp_path
):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await query_collection_impl(
        "https://objects.test/collection",
        "WITH selected AS (SELECT ? AS value) SELECT value FROM selected",
        ["quote'; -- still data"],
        20,
    )

    assert result.rows == [{"value": "quote'; -- still data"}]


@pytest.mark.asyncio
async def test_inspect_collection_returns_curated_schema(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await inspect_collection_impl("https://objects.test/collection")

    assert result.ontology_id == "demo"
    assert [table.name for table in result.tables] == [
        "nodes",
        "relations",
        "observations",
        "fragments",
        "source_status",
    ]
