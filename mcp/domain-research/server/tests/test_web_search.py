import copy
import json
import math
from datetime import date

import pytest

from app.errors import ToolFailure
from app.models import WebSearchRequest
from app.web_search import search_web_impl


@pytest.mark.asyncio
async def test_search_web_passes_exact_dates_and_domains(tavily_transport):
    result = await search_web_impl(
        WebSearchRequest(
            query="polymer flooding digital optimization",
            topic="news",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
            include_domains=["spe.org", "slb.com/resources"],
            exclude_domains=["example.test"],
            search_depth="advanced",
            max_results=8,
        ),
        tavily_transport.client,
        request_id="call-123",
    )

    sent = tavily_transport.json_request()
    assert sent["start_date"] == "2026-01-01"
    assert sent["end_date"] == "2026-03-31"
    assert sent["filter_by_published_date"] is True
    assert sent["include_domains"] == ["spe.org", "slb.com/resources"]
    assert result.status == "ok"
    assert result.request_id == "call-123"
    assert result.provider_request_id == "tavily-request-123"
    assert result.results[0].source_id == (
        "7674149b9d4b166efd6b0b77da4ab83374a9283d0e266b672ec08a47677814e1"
    )
    assert result.results[0].published_date == date(2026, 3, 11)
    assert (
        result.results[0].url == "https://spe.org/articles/polymer-flooding/?ref=search"
    )
    assert result.results[0].snippet == (
        "A field study describes digital optimization for polymer flooding."
    )


@pytest.mark.asyncio
async def test_search_web_maps_provider_rate_limit(tavily_transport):
    tavily_transport.status_code = 429

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_RATE_LIMIT"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_search_web_maps_provider_auth_failure(
    tavily_transport,
    status_code,
):
    tavily_transport.status_code = status_code

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_AUTH"


@pytest.mark.asyncio
async def test_search_web_maps_provider_timeout(tavily_transport):
    tavily_transport.times_out = True

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_search_web_maps_invalid_provider_json(tavily_transport):
    tavily_transport.raw_content = b"not-json"

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", None),
        ("url", "not a URL"),
        ("url", "http://[::1"),
        ("url", "https://user:secret@example.test/path"),
        ("published_date", "yesterday"),
        ("published_date", "x" * 129),
        ("score", math.nan),
        ("title", "x" * 2_001),
        ("url", "https://example.test/" + "x" * 8_193),
    ],
)
async def test_search_web_maps_invalid_provider_result(
    tavily_transport,
    field,
    value,
):
    response = copy.deepcopy(tavily_transport.response)
    response["results"][0][field] = value
    if isinstance(value, float) and math.isnan(value):
        tavily_transport.raw_content = json.dumps(response, allow_nan=True).encode()
    else:
        tavily_transport.response = response

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 503])
async def test_search_web_maps_provider_server_error(tavily_transport, status_code):
    tavily_transport.status_code = status_code

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    assert f"HTTP {status_code}" in caught.value.public_message


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 404, 422, 432, 433])
async def test_search_web_maps_provider_client_error_to_rejection(
    tavily_transport, status_code
):
    tavily_transport.status_code = status_code

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_REJECTED"
    assert f"HTTP {status_code}" in caught.value.public_message


@pytest.mark.asyncio
async def test_search_web_maps_provider_connection_error(tavily_transport):
    tavily_transport.connection_fails = True

    with pytest.raises(ToolFailure) as caught:
        await search_web_impl(
            WebSearchRequest(query="polymer flooding"),
            tavily_transport.client,
        )

    assert caught.value.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("published_date", [None, "", "2025-12-31", "2026-04-01"])
async def test_search_web_enforces_bounded_date_window(
    tavily_transport,
    published_date,
):
    tavily_transport.response["results"][0]["published_date"] = published_date

    result = await search_web_impl(
        WebSearchRequest(
            query="polymer flooding",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
        ),
        tavily_transport.client,
    )

    assert result.results == []


@pytest.mark.asyncio
@pytest.mark.parametrize("published_date", ["2026-01-01", "2026-03-31"])
async def test_search_web_includes_date_window_boundaries(
    tavily_transport,
    published_date,
):
    tavily_transport.response["results"][0]["published_date"] = published_date

    result = await search_web_impl(
        WebSearchRequest(
            query="polymer flooding",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
        ),
        tavily_transport.client,
    )

    assert result.results[0].published_date == date.fromisoformat(published_date)


@pytest.mark.asyncio
async def test_search_web_bounds_result_snippet(tavily_transport):
    tavily_transport.response["results"][0]["content"] = "x" * 2_001

    result = await search_web_impl(
        WebSearchRequest(query="polymer flooding"),
        tavily_transport.client,
    )

    assert result.results[0].snippet == "x" * 2_000


@pytest.mark.asyncio
async def test_search_web_caps_provider_results_to_requested_limit(tavily_transport):
    extra_result = copy.deepcopy(tavily_transport.response["results"][0])
    extra_result["title"] = "Extra result"
    extra_result["url"] = "https://example.test/extra"
    tavily_transport.response["results"].append(extra_result)

    result = await search_web_impl(
        WebSearchRequest(query="polymer flooding", max_results=1),
        tavily_transport.client,
    )

    assert [row.title for row in result.results] == [
        "Digital optimization of polymer flooding"
    ]


@pytest.mark.asyncio
async def test_search_web_preserves_unicode_and_provider_controls(tavily_transport):
    await search_web_impl(
        WebSearchRequest(
            query="цифровая оптимизация добычи",
            exact_match=True,
        ),
        tavily_transport.client,
    )

    request = tavily_transport.requests[-1]
    sent = tavily_transport.json_request()
    assert request.headers["Authorization"] == "Bearer test-key"
    assert request.headers["Accept-Encoding"] == "identity"
    assert sent["query"] == '"цифровая оптимизация добычи"'
    assert sent["exact_match"] is True
    assert sent["include_raw_content"] is False
    assert sent["filter_by_published_date"] is False


@pytest.mark.asyncio
async def test_search_web_drops_malformed_rows_and_keeps_valid_ones(tavily_transport):
    good = copy.deepcopy(tavily_transport.response["results"][0])
    tavily_transport.response["results"] = [{**good, "url": "not a URL"}, good]

    result = await search_web_impl(
        WebSearchRequest(query="polymer flooding"), tavily_transport.client
    )

    assert [row.title for row in result.results] == [good["title"]]


@pytest.mark.asyncio
async def test_search_web_accepts_long_provider_content_as_a_snippet(tavily_transport):
    tavily_transport.response["results"][0]["content"] = "x" * 20_001

    result = await search_web_impl(
        WebSearchRequest(query="polymer flooding"), tavily_transport.client
    )

    assert result.results[0].snippet == "x" * 2_000
