import asyncio
import json

import httpx
import pytest
from app.errors import ToolFailure
from app.server import mcp
from app.web_source_transport import download_bundle
from app.web_sources import ExtractRequest, extract_web_pages_impl
from fastmcp import Client

BUNDLE_RECEIPT_KEY = "test-bundle-receipt-key-000000000000"


class StubTransport:
    def __init__(self, responses: dict[tuple[str, str], httpx.Response]) -> None:
        self.responses = responses
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        return self.responses.get(
            (request.method, str(request.url)), httpx.Response(404)
        )


def _payload(*, published_date: str | None = None) -> dict:
    return {
        "results": [
            {
                "url": "https://source.test/a",
                "title": "Primary source",
                "published_date": published_date,
                "raw_content": "exact evidence",
            }
        ],
        "failed_results": [],
    }


@pytest.mark.asyncio
async def test_extract_returns_bounded_previews_and_media_type():
    tavily = StubTransport(
        {
            ("POST", "https://api.tavily.test/extract"): httpx.Response(
                200, json=_payload()
            )
        }
    )
    objects = StubTransport(
        {("PUT", "https://objects.test/put?signature=secret"): httpx.Response(200)}
    )
    async with tavily.client, objects.client:
        result = await extract_web_pages_impl(
            ExtractRequest(
                urls=["https://source.test/a"],
                upload_url="https://objects.test/put?signature=secret",
            ),
            tavily.client,
            objects.client,
            api_key="test-key",
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            base_url="https://api.tavily.test",
        )

    assert result.media_type == "application/json"
    assert result.previews == [
        {
            "source_id": "27a119cbb62b7b4d1c9badafb5020c9bc7fc38fd9b801f5427c08402ba0bf09b",
            "text": "exact evidence",
            "truncated": False,
        }
    ]


@pytest.mark.asyncio
async def test_extract_normalizes_provider_publication_date():
    tavily = StubTransport(
        {
            ("POST", "https://api.tavily.test/extract"): httpx.Response(
                200, json=_payload(published_date="Wed, 11 Mar 2026 00:00:00 GMT")
            )
        }
    )
    uploaded: list[bytes] = []

    def object_handler(request: httpx.Request) -> httpx.Response:
        uploaded.append(request.content)
        return httpx.Response(200)

    objects = httpx.AsyncClient(transport=httpx.MockTransport(object_handler))
    async with tavily.client, objects:
        await extract_web_pages_impl(
            ExtractRequest(
                urls=["https://source.test/a"],
                upload_url="https://objects.test/put?signature=secret",
            ),
            tavily.client,
            objects,
            api_key="test-key",
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            base_url="https://api.tavily.test",
        )

    assert json.loads(uploaded[0])["sources"][0]["published_date"] == "2026-03-11"


@pytest.mark.asyncio
async def test_extract_maps_malformed_failed_result_url_to_provider_error():
    payload = _payload()
    payload["failed_results"] = [{"url": "not a URL"}]
    tavily = StubTransport(
        {("POST", "https://api.tavily.test/extract"): httpx.Response(200, json=payload)}
    )
    objects = StubTransport({})
    async with tavily.client, objects.client:
        with pytest.raises(ToolFailure) as caught:
            await extract_web_pages_impl(
                ExtractRequest(
                    urls=["https://source.test/a"],
                    upload_url="https://objects.test/put?signature=secret",
                ),
                tavily.client,
                objects.client,
                api_key="test-key",
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
                base_url="https://api.tavily.test",
            )

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.asyncio
async def test_extract_rejects_final_bundle_larger_than_reader_limit():
    payload = _payload()
    payload["results"][0]["raw_content"] = "x" * 400
    tavily = StubTransport(
        {("POST", "https://api.tavily.test/extract"): httpx.Response(200, json=payload)}
    )
    objects = StubTransport({})
    async with tavily.client, objects.client:
        with pytest.raises(ToolFailure) as caught:
            await extract_web_pages_impl(
                ExtractRequest(
                    urls=["https://source.test/a"],
                    upload_url="https://objects.test/put?signature=secret",
                ),
                tavily.client,
                objects.client,
                api_key="test-key",
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
                base_url="https://api.tavily.test",
                max_bundle_bytes=400,
            )

    assert caught.value.code == "SOURCE_TOO_LARGE"


@pytest.mark.asyncio
async def test_signed_object_failure_returns_stable_error():
    objects = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret-value"): httpx.Response(
                503
            )
        }
    )
    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await download_bundle(
                objects.client,
                "https://objects.test/get?signature=secret-value",
                max_bytes=100,
                expected_sha256="0" * 64,
            )

    assert caught.value.code == "SOURCE_DOWNLOAD_FAILED"


@pytest.mark.asyncio
async def test_mcp_tools_translate_raw_wire_errors_to_structured_input_errors():
    async with Client(mcp) as client:
        extract = await client.call_tool(
            "extract_web_pages",
            {"urls": "not-a-list", "upload_url": "https://objects.test/put"},
        )
        read = await client.call_tool(
            "read_web_fragments",
            {
                "bundle_url": "https://objects.test/get",
                "source_ids": ["a"],
                "start": "bad",
                "unexpected": True,
            },
        )

    for result in (extract, read):
        assert result.is_error is False
        assert set(result.structured_content) == {"status", "request_id", "error"}
        assert result.structured_content["status"] == "error"
        assert len(result.structured_content["request_id"]) == 32
        assert result.structured_content["error"]["code"] == "INPUT_INVALID"


@pytest.mark.asyncio
async def test_signed_bundle_download_enforces_total_deadline():
    class DelayedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(0.02)
            yield b"x"

    delayed = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, stream=DelayedStream()
            )
        }
    )
    async with delayed.client:
        with pytest.raises(ToolFailure) as caught:
            await download_bundle(
                delayed.client,
                "https://objects.test/get?signature=secret",
                max_bytes=10,
                expected_sha256="0" * 64,
                timeout_seconds=0.001,
            )
    assert caught.value.code == "SOURCE_DOWNLOAD_FAILED"


@pytest.mark.asyncio
async def test_signed_bundle_download_rejects_encoded_response():
    encoded = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, headers={"Content-Encoding": "gzip"}
            )
        }
    )
    async with encoded.client:
        with pytest.raises(ToolFailure) as caught:
            await download_bundle(
                encoded.client,
                "https://objects.test/get?signature=secret",
                max_bytes=10,
                expected_sha256="0" * 64,
            )
    assert caught.value.code == "SOURCE_ENCODING_UNSUPPORTED"


def test_extract_rejects_query_larger_than_canonical_bundle_metadata_limit():
    with pytest.raises(ToolFailure) as caught:
        ExtractRequest(
            urls=["https://source.test/a"],
            upload_url="https://objects.test/put?signature=secret",
            query="x" * 401,
        )

    assert caught.value.code == "INPUT_INVALID"
