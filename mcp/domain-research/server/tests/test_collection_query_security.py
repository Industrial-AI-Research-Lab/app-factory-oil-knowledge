from __future__ import annotations

import pytest
from app.collection.query import inspect_collection_impl, query_collection_impl
from app.errors import ToolFailure
from collection_query_test_support import collection_payload, recursive_metadata_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM nodes",
        "ATTACH DATABASE 'x' AS other",
        "PRAGMA writable_schema=ON",
        "SELECT load_extension('extension')",
    ],
)
async def test_query_collection_denies_mutation(httpx_mock, tmp_path, sql):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    with pytest.raises(ToolFailure, match="SQLITE_QUERY_DENIED"):
        await query_collection_impl("https://objects.test/collection", sql, [], 20)


def test_service_startup_probe_confirms_json_each_under_the_guard():
    from app.server import json_each_runs_under_query_guard

    assert json_each_runs_under_query_guard() is True


@pytest.mark.asyncio
async def test_query_collection_allows_json_each_under_the_guard(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )
    sql = (
        "SELECT (SELECT value FROM json_each('{\"label\": \"x\"}') WHERE type = 'text' LIMIT 1)"
        " AS label FROM nodes"
    )

    result = await query_collection_impl("https://objects.test/collection", sql, [], 20)

    assert result.columns == ["label"]
    assert [row["label"] for row in result.rows] == ["x", "x"]


@pytest.mark.asyncio
async def test_query_collection_rejects_non_positive_row_cap(httpx_mock):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await query_collection_impl(
            "https://objects.test/collection", "SELECT 1", [], 0
        )


@pytest.mark.asyncio
async def test_query_collection_rejects_nonfinite_parameter_before_download(httpx_mock):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await query_collection_impl(
            "https://objects.test/collection", "SELECT ?", [float("nan")], 20
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-(2**63), 2**63 - 1])
async def test_query_collection_accepts_sqlite_int64_endpoints(
    httpx_mock, tmp_path, value
):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await query_collection_impl(
        "https://objects.test/collection", "SELECT ? AS value", [value], 20
    )

    assert result.rows == [{"value": value}]


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-(2**63) - 1, 2**63])
async def test_query_collection_rejects_int_outside_sqlite_int64_before_download(
    httpx_mock, value
):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await query_collection_impl(
            "https://objects.test/collection", "SELECT ? AS value", [value], 20
        )

    assert httpx_mock.get_requests() == []


@pytest.mark.asyncio
async def test_query_collection_allows_scalar_value_under_configured_limit(
    httpx_mock, monkeypatch, tmp_path
):
    monkeypatch.setenv("SQLITE_MAX_VALUE_BYTES", "1024")
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    result = await query_collection_impl(
        "https://objects.test/collection", "SELECT 'small' AS value", [], 20
    )

    assert result.rows == [{"value": "small"}]


@pytest.mark.asyncio
async def test_query_collection_rejects_scalar_value_over_configured_limit(
    httpx_mock, monkeypatch, tmp_path
):
    monkeypatch.setenv("SQLITE_MAX_VALUE_BYTES", "1024")
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    with pytest.raises(ToolFailure, match="SQLITE_QUERY_VALUE_TOO_LARGE"):
        await query_collection_impl(
            "https://objects.test/collection",
            "SELECT printf('%.*c', 1025, 'x') AS value",
            [],
            20,
        )


@pytest.mark.asyncio
async def test_query_collection_rejects_duplicate_result_labels(httpx_mock, tmp_path):
    httpx_mock.add_response(
        url="https://objects.test/collection", content=collection_payload(tmp_path)
    )

    with pytest.raises(ToolFailure, match="SQLITE_QUERY_INVALID"):
        await query_collection_impl(
            "https://objects.test/collection", "SELECT 1 AS value, 2 AS value", [], 20
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["query", "inspect"])
async def test_collection_open_bounds_recursive_metadata(
    httpx_mock, monkeypatch, operation, tmp_path
):
    monkeypatch.setenv("SQLITE_OPERATION_BUDGET", "20")
    httpx_mock.add_response(
        url="https://objects.test/collection",
        content=recursive_metadata_payload(tmp_path),
    )

    with pytest.raises(ToolFailure, match="SQLITE_QUERY_BUDGET_EXCEEDED"):
        if operation == "query":
            await query_collection_impl(
                "https://objects.test/collection", "SELECT 1", [], 20
            )
        else:
            await inspect_collection_impl("https://objects.test/collection")


@pytest.mark.asyncio
async def test_query_collection_stops_recursive_query_at_operation_budget(
    httpx_mock, monkeypatch, tmp_path
):
    monkeypatch.setenv("SQLITE_OPERATION_BUDGET", "200")
    payload = collection_payload(tmp_path)
    httpx_mock.add_response(url="https://objects.test/collection", content=payload)
    httpx_mock.add_response(url="https://objects.test/collection", content=payload)

    control = await query_collection_impl(
        "https://objects.test/collection",
        "SELECT node_id FROM nodes ORDER BY node_id",
        [],
        20,
    )

    assert control.rows == [{"node_id": "node:one"}, {"node_id": "node:two"}]

    with pytest.raises(ToolFailure, match="SQLITE_QUERY_BUDGET_EXCEEDED"):
        await query_collection_impl(
            "https://objects.test/collection",
            "WITH RECURSIVE counter(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM counter) SELECT count(*) AS total FROM counter",
            [],
            20,
        )
