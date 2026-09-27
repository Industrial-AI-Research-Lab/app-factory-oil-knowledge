import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from app.errors import ToolFailure
from app.server import mcp
from app.web_source_receipts import issue_bundle_receipt
from app.web_sources import (
    ExtractRequest,
    ReadWebFragmentsRequest,
    extract_web_pages_impl,
    read_web_fragments_impl,
)
from fastmcp import Client

BUNDLE_RECEIPT_KEY = "test-bundle-receipt-key-000000000000"


class StubTransport:
    def __init__(self, responses: dict[tuple[str, str], httpx.Response]) -> None:
        self.responses = responses
        self.requests: list[httpx.Request] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.get((request.method, str(request.url)))
        if response is None:
            return httpx.Response(404)
        return response


@pytest.mark.asyncio
async def test_service_exposes_saved_web_source_tools():
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} >= {
        "extract_web_pages",
        "read_web_fragments",
    }
    read_tool = next(tool for tool in tools if tool.name == "read_web_fragments")
    assert "expected_bundle_sha256" in read_tool.inputSchema["required"]
    assert "bundle_receipt" in read_tool.inputSchema["required"]


@pytest.fixture
def extract_payload() -> dict:
    fixture = Path(__file__).parent / "fixtures" / "tavily_extract.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_extract_upload_and_read_use_identical_saved_text(extract_payload):
    upload_url = "https://objects.test/put?signature=secret"
    bundle_url = "https://objects.test/get?signature=secret"
    uploaded: list[bytes] = []

    def object_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            uploaded.append(request.content)
            return httpx.Response(200)
        if request.method == "GET":
            return httpx.Response(200, content=uploaded[0])
        return httpx.Response(404)

    tavily = StubTransport(
        {
            ("POST", "https://api.tavily.test/extract"): httpx.Response(
                200, json=extract_payload
            )
        }
    )
    objects = httpx.AsyncClient(transport=httpx.MockTransport(object_handler))
    async with tavily.client, objects:
        created = await extract_web_pages_impl(
            ExtractRequest(urls=["https://source.test/a"], upload_url=upload_url),
            tavily.client,
            objects,
            api_key="test-key",
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            base_url="https://api.tavily.test",
            clock=lambda: datetime(2026, 9, 17, tzinfo=timezone.utc),
        )
        saved = json.loads(uploaded[0])
        read = await read_web_fragments_impl(
            ReadWebFragmentsRequest(
                bundle_url=bundle_url,
                expected_bundle_sha256=created.bundle_sha256,
                bundle_receipt=created.bundle_receipt,
                source_ids=[saved["sources"][0]["source_id"]],
            ),
            objects,
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
        )

    assert created.bundle_sha256 == hashlib.sha256(uploaded[0]).hexdigest()
    assert created.bundle_receipt == issue_bundle_receipt(
        created.bundle_sha256,
        BUNDLE_RECEIPT_KEY,
    )
    assert uploaded[0] == (
        b'{"created_at":"2026-09-17T00:00:00+00:00","failures":[],"query":null,'
        b'"schema_version":1,"sources":[{"published_date":null,'
        b'"source_id":"27a119cbb62b7b4d1c9badafb5020c9bc7fc38fd9b801f5427c08402ba0bf09b",'
        b'"text":"Saved source text with exact evidence.","title":"'
        + "Первичный источник".encode()
        + b'","url":"https://source.test/a"}]}'
    )
    assert read.fragments[0].text == "Saved source text with exact evidence."
    assert saved == {
        "created_at": "2026-09-17T00:00:00+00:00",
        "failures": [],
        "query": None,
        "schema_version": 1,
        "sources": [
            {
                "published_date": None,
                "source_id": hashlib.sha256(b"https://source.test/a").hexdigest(),
                "text": "Saved source text with exact evidence.",
                "title": "Первичный источник",
                "url": "https://source.test/a",
            }
        ],
    }


@pytest.mark.asyncio
async def test_read_rejects_bundle_digest_not_attested_by_extraction():
    payload = b'{"schema_version":1,"sources":[]}'
    requests: list[httpx.Request] = []
    objects = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: requests.append(request)
            or httpx.Response(200, content=payload)
        )
    )
    async with objects:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get?signature=secret",
                    expected_bundle_sha256=hashlib.sha256(payload).hexdigest(),
                    bundle_receipt="v1." + "A" * 43,
                    source_ids=["a" * 64],
                ),
                objects,
                bundle_receipt_key=b"server-only-key",
            )

    assert caught.value.code == "BUNDLE_RECEIPT_INVALID"
    assert requests == []


@pytest.mark.asyncio
async def test_extract_reports_partial_provider_failure(extract_payload):
    payload = copy.deepcopy(extract_payload)
    payload["failed_results"] = [{"url": "https://source.test/b", "error": "blocked"}]
    tavily = StubTransport(
        {("POST", "https://api.tavily.test/extract"): httpx.Response(200, json=payload)}
    )
    objects = StubTransport(
        {("PUT", "https://objects.test/put?signature=secret"): httpx.Response(200)}
    )
    async with tavily.client, objects.client:
        result = await extract_web_pages_impl(
            ExtractRequest(
                urls=["https://source.test/a", "https://source.test/b"],
                upload_url="https://objects.test/put?signature=secret",
            ),
            tavily.client,
            objects.client,
            api_key="test-key",
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            base_url="https://api.tavily.test",
        )

    assert result.status == "partial"
    assert result.failures == [
        {"url": "https://source.test/b", "code": "PROVIDER_FAILED"}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "results": [{"url": "https://source.test/a", "raw_content": ""}],
            "failed_results": [],
        },
        {
            "results": [
                {"url": "https://source.test/a", "raw_content": "first"},
                {"url": "https://source.test/a#x", "raw_content": "x"},
            ],
            "failed_results": [],
        },
    ],
)
async def test_extract_rejects_unusable_provider_results(payload):
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
async def test_extract_reports_all_pages_failed_with_stable_error():
    tavily = StubTransport(
        {
            ("POST", "https://api.tavily.test/extract"): httpx.Response(
                200,
                json={
                    "results": [],
                    "failed_results": [{"url": "https://source.test/a", "error": "no"}],
                },
            )
        }
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

    assert caught.value.code == "EXTRACTION_ALL_FAILED"


@pytest.mark.asyncio
async def test_extract_redacts_signed_upload_url_when_upload_fails():
    upload_url = "https://objects.test/put?signature=secret-value"
    tavily = StubTransport(
        {
            ("POST", "https://api.tavily.test/extract"): httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "url": "https://source.test/a",
                            "raw_content": "evidence",
                        }
                    ],
                    "failed_results": [],
                },
            )
        }
    )
    request = httpx.Request("PUT", upload_url)

    def upload_failure(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("signed upload failed", request=request)

    objects = httpx.AsyncClient(transport=httpx.MockTransport(upload_failure))
    async with tavily.client, objects:
        with pytest.raises(ToolFailure) as caught:
            await extract_web_pages_impl(
                ExtractRequest(urls=["https://source.test/a"], upload_url=upload_url),
                tavily.client,
                objects,
                api_key="test-key",
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
                base_url="https://api.tavily.test",
            )

    assert caught.value.code == "UPLOAD_FAILED"
    assert "secret-value" not in str(caught.value)
