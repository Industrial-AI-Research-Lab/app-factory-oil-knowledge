import pytest
from app.errors import ToolFailure
from app.server import mcp
from app.web_source_normalization import sources_from_provider
from app.web_sources import ExtractRequest
from fastmcp import Client


@pytest.mark.parametrize(
    "row",
    [
        {"url": "https://source.test/a", "published_date": 1, "raw_content": "x"},
        {"published_date": None, "raw_content": "x"},
    ],
)
def test_provider_success_rows_with_wrong_typed_fields_fail_closed(row):
    with pytest.raises(ToolFailure) as caught:
        sources_from_provider(
            {"results": [row], "failed_results": []},
            {"https://source.test/a"},
        )

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.parametrize(
    "requested_urls, payload",
    [
        (
            {"https://source.test/a", "https://source.test/b"},
            {"results": [{"url": "https://source.test/a", "raw_content": "x"}]},
        ),
        (
            {"https://source.test/a"},
            {"results": [{"url": "https://source.test/b", "raw_content": "x"}]},
        ),
    ],
)
def test_provider_rows_must_reconcile_with_requested_urls(requested_urls, payload):
    with pytest.raises(ToolFailure) as caught:
        sources_from_provider(payload, requested_urls)

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


def test_extract_input_rejects_unpaired_surrogate_query():
    with pytest.raises(ToolFailure) as caught:
        ExtractRequest(
            urls=["https://source.test/a"],
            upload_url="https://objects.test/put",
            query="\ud800",
        )

    assert caught.value.code == "INPUT_INVALID"


@pytest.mark.asyncio
async def test_raw_mcp_validation_uses_tool_specific_error_messages():
    async with Client(mcp) as client:
        extract = await client.call_tool(
            "extract_web_pages",
            {"urls": "invalid", "upload_url": "https://objects.test/put"},
        )
        read = await client.call_tool(
            "read_web_fragments",
            {"bundle_url": "https://objects.test/get", "source_ids": "invalid"},
        )

    assert (
        extract.structured_content["error"]["message"]
        == "Invalid web extraction request"
    )
    assert (
        read.structured_content["error"]["message"]
        == "Invalid web fragment read request"
    )


@pytest.mark.asyncio
async def test_raw_fragment_read_requires_extraction_checksum():
    async with Client(mcp) as client:
        result = await client.call_tool(
            "read_web_fragments",
            {
                "bundle_url": "https://objects.test/get",
                "source_ids": ["a"],
            },
        )

    assert result.is_error is False
    assert result.structured_content["status"] == "error"
    assert result.structured_content["error"]["code"] == "INPUT_INVALID"
