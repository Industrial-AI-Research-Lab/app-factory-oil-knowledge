import json
from pathlib import Path

import pytest
from app.server import mcp
from fastmcp import Client


@pytest.mark.asyncio
async def test_search_web_tool_publishes_bounded_schemas():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    tool = next(tool for tool in tools if tool.name == "search_web")
    properties = tool.inputSchema["properties"]
    domain_schema = next(
        schema
        for schema in properties["include_domains"]["anyOf"]
        if schema.get("type") == "array"
    )
    output_results_schema = next(
        schema
        for schema in tool.outputSchema["properties"]["results"]["anyOf"]
        if schema.get("type") == "array"
    )
    output_result_properties = output_results_schema["items"]["properties"]
    assert properties["query"]["minLength"] == 1
    assert properties["query"]["maxLength"] == 400
    assert domain_schema["maxItems"] == 20
    assert domain_schema["items"]["maxLength"] == 253
    assert properties["exact_match"]["type"] == "boolean"
    assert properties["max_results"]["minimum"] == 1
    assert properties["max_results"]["maximum"] == 20
    assert tool.outputSchema["type"] == "object"
    assert tool.outputSchema["additionalProperties"] is False
    assert output_results_schema["maxItems"] == 20
    assert output_result_properties["source_id"]["pattern"] == "^[0-9a-f]{64}$"
    assert output_result_properties["title"]["maxLength"] == 2_000
    assert output_result_properties["url"]["maxLength"] == 8_192
    assert output_result_properties["snippet"]["maxLength"] == 2_000
    assert set(tool.outputSchema["properties"]) == {
        "status",
        "request_id",
        "provider_request_id",
        "filters",
        "results",
        "error",
    }


@pytest.mark.asyncio
async def test_search_web_tool_returns_normalized_json(
    httpx_mock,
    monkeypatch,
):
    fixture_path = Path(__file__).parent / "fixtures" / "tavily_search.json"
    httpx_mock.add_response(
        method="POST",
        url="https://api.tavily.test/search",
        json=json.loads(fixture_path.read_text(encoding="utf-8")),
    )
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_BASE_URL", "https://api.tavily.test")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_web",
            {
                "query": "polymer flooding digital optimization",
                "topic": "news",
                "start_date": "2026-01-01",
                "end_date": "2026-03-31",
                "include_domains": None,
                "exclude_domains": None,
            },
        )

    assert result.is_error is False
    assert result.structured_content["status"] == "ok"
    assert len(result.structured_content["request_id"]) == 32
    assert result.structured_content["provider_request_id"] == "tavily-request-123"
    assert result.structured_content["results"][0]["published_date"] == "2026-03-11"


@pytest.mark.asyncio
async def test_search_web_tool_returns_structured_provider_error(
    httpx_mock,
    monkeypatch,
):
    httpx_mock.add_response(
        method="POST",
        url="https://api.tavily.test/search",
        status_code=429,
    )
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_BASE_URL", "https://api.tavily.test")

    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_web",
            {"query": "polymer flooding"},
        )

    assert result.is_error is False
    assert result.structured_content["status"] == "error"
    assert len(result.structured_content["request_id"]) == 32
    assert result.structured_content["error"]["code"] == "PROVIDER_RATE_LIMIT"


@pytest.mark.asyncio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "   "},
        {"query": "polymer flooding", "start_date": "2026-02-30"},
        {
            "query": "polymer flooding",
            "start_date": "2026-04-01",
            "end_date": "2026-03-31",
        },
    ],
)
async def test_search_web_tool_returns_structured_input_error(arguments):
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_web",
            arguments,
        )

    assert result.is_error is False
    assert result.structured_content["status"] == "error"
    assert len(result.structured_content["request_id"]) == 32
    assert result.structured_content["error"]["code"] == "INPUT_INVALID"


@pytest.mark.asyncio
@pytest.mark.httpx_mock(assert_all_responses_were_requested=False)
@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "polymer flooding", "max_results": "5"},
        {"query": "polymer flooding", "exact_match": "true"},
        {"query": "polymer flooding", "topic": "invalid"},
        {"query": "polymer flooding", "start_date": 0},
        {"query": "polymer flooding", "end_date": 86_400},
        {"query": "polymer flooding", "start_date": "2026-01-01T00:00:00"},
        {"query": "polymer flooding", "end_date": "2026-01-01 00:00:00"},
        {"query": "polymer flooding", "start_date": "20260101"},
        {"query": "polymer flooding", "unexpected": "field"},
    ],
)
async def test_search_web_tool_returns_structured_error_for_wire_type_violation(
    arguments,
    httpx_mock,
    monkeypatch,
):
    httpx_mock.add_response(
        method="POST",
        url="https://api.tavily.test/search",
        json={"results": []},
    )
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_BASE_URL", "https://api.tavily.test")

    async with Client(mcp) as client:
        result = await client.call_tool("search_web", arguments)

    assert result.is_error is False
    assert result.structured_content["status"] == "error"
    assert len(result.structured_content["request_id"]) == 32
    assert result.structured_content["error"]["code"] == "INPUT_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "extract_web_pages",
            {
                "urls": ["https://source.test/a"],
                "upload_url": "https://evil.test/bundle",
            },
        ),
        (
            "read_web_fragments",
            {
                "bundle_url": "https://evil.test/bundle",
                "expected_bundle_sha256": "0" * 64,
                "bundle_receipt": "v1." + "A" * 43,
                "source_ids": ["a" * 64],
            },
        ),
    ],
)
async def test_saved_source_tools_preserve_origin_validation_error(
    tool_name,
    arguments,
):
    async with Client(mcp) as client:
        result = await client.call_tool(tool_name, arguments)

    assert result.is_error is False
    assert set(result.structured_content) == {"status", "request_id", "error"}
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error"] == {
        "code": "URL_HOST_INVALID",
        "message": "Signed object URL origin is not allowed",
    }


@pytest.mark.asyncio
async def test_news_finalizer_returns_structured_schema_error_for_null_decisions():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "finalize_news_result",
            {
                "brief": {},
                "decisions": None,
                "critique": {},
                "source_bundle_url": "https://objects.test/source-bundle",
                "expected_bundle_sha256": "0" * 64,
                "bundle_receipt": "v1." + "A" * 43,
                "upload_url": "https://objects.test/news-result",
            },
            raise_on_error=False,
        )

    assert result.is_error is False
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error"]["code"] == "NEWS_SCHEMA_INVALID"
