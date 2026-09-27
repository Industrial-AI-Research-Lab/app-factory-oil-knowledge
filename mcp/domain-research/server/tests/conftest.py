import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio


class TavilyTransport:
    def __init__(self, response: dict) -> None:
        self.requests: list[httpx.Request] = []
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(self._handle),
        )
        self.response = response
        self.raw_content: bytes | None = None
        self.response_headers: dict[str, str] = {}
        self.status_code = 200
        self.connection_fails = False
        self.times_out = False
        self.response_stream: httpx.AsyncByteStream | None = None

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.times_out:
            raise httpx.ReadTimeout("provider timed out", request=request)
        if self.connection_fails:
            raise httpx.ConnectError("provider unavailable", request=request)
        if self.response_stream is not None:
            return httpx.Response(self.status_code, stream=self.response_stream)
        if self.raw_content is not None:
            return httpx.Response(
                self.status_code,
                content=self.raw_content,
                headers=self.response_headers,
            )
        return httpx.Response(
            self.status_code,
            json=self.response,
            headers=self.response_headers,
        )

    def json_request(self) -> dict:
        return json.loads(self.requests[-1].content)


@pytest.fixture(autouse=True)
def allowed_object_storage_origin(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_ALLOWED_ORIGINS", "https://objects.test")
    monkeypatch.setenv("BUNDLE_RECEIPT_KEY", "test-bundle-receipt-key-000000000000")


@pytest_asyncio.fixture
async def tavily_transport(monkeypatch):
    fixture_path = Path(__file__).parent / "fixtures" / "tavily_search.json"
    transport = TavilyTransport(json.loads(fixture_path.read_text(encoding="utf-8")))
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setenv("TAVILY_BASE_URL", "https://api.tavily.test")
    async with transport.client:
        yield transport
