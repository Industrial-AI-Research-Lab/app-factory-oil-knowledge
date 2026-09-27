import hashlib
import json

import httpx
import pytest
from app.errors import ToolFailure
from app.web_source_receipts import issue_bundle_receipt
from app.web_sources import ReadWebFragmentsRequest, read_web_fragments_impl

SOURCE_ID = hashlib.sha256(b"https://source.test/a").hexdigest()
BUNDLE_RECEIPT_KEY = "test-bundle-receipt-key-000000000000"


def _receipt(bundle_sha256: str) -> str:
    return issue_bundle_receipt(bundle_sha256, BUNDLE_RECEIPT_KEY)


class StubTransport:
    def __init__(self, responses: dict[tuple[str, str], httpx.Response]) -> None:
        self.responses = responses
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        return self.responses.get(
            (request.method, str(request.url)),
            httpx.Response(404),
        )


@pytest.mark.asyncio
async def test_read_rejects_bundle_not_matching_extraction_checksum():
    bundle = json.dumps({"schema_version": 1, "sources": []}).encode()
    objects = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, content=bundle
            )
        }
    )

    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get?signature=secret",
                    source_ids=["a"],
                    expected_bundle_sha256="0" * 64,
                    bundle_receipt=_receipt("0" * 64),
                ),
                objects.client,
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            )

    assert caught.value.code == "SOURCE_CHECKSUM_MISMATCH"


@pytest.mark.parametrize(
    "expected_bundle_sha256",
    [None, "", "0" * 63, "z" * 64, 123, True],
)
def test_read_rejects_malformed_expected_bundle_checksum(expected_bundle_sha256):
    with pytest.raises(ToolFailure) as caught:
        ReadWebFragmentsRequest(
            bundle_url="https://objects.test/get?signature=secret",
            expected_bundle_sha256=expected_bundle_sha256,
            bundle_receipt="v1." + "A" * 43,
            source_ids=["a"],
        )

    assert caught.value.code == "INPUT_INVALID"


@pytest.mark.parametrize(
    "bundle_receipt",
    [None, "", "v2." + "A" * 43, "v1." + "A" * 42, "v1." + "!" * 43, 1],
)
def test_read_rejects_malformed_bundle_receipt(bundle_receipt):
    with pytest.raises(ToolFailure) as caught:
        ReadWebFragmentsRequest(
            bundle_url="https://objects.test/get?signature=secret",
            expected_bundle_sha256="0" * 64,
            bundle_receipt=bundle_receipt,
            source_ids=["a"],
        )

    assert caught.value.code == "INPUT_INVALID"


def test_read_normalizes_uppercase_checksum():
    request = ReadWebFragmentsRequest(
        bundle_url="https://objects.test/get?signature=secret",
        expected_bundle_sha256="A" * 64,
        bundle_receipt=_receipt("a" * 64),
        source_ids=["a"],
    )

    assert request.expected_bundle_sha256 == "a" * 64


@pytest.mark.asyncio
async def test_read_rejects_missing_or_duplicate_source_ids():
    bundle = json.dumps(
        {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": SOURCE_ID,
                    "url": "https://source.test/a",
                    "title": "Primary source",
                    "published_date": None,
                    "text": "evidence",
                }
            ],
        }
    ).encode()
    objects = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, content=bundle
            )
        }
    )
    async with objects.client:
        for source_ids in (["b" * 64], [SOURCE_ID, SOURCE_ID]):
            with pytest.raises(ToolFailure) as caught:
                await read_web_fragments_impl(
                    ReadWebFragmentsRequest(
                        bundle_url="https://objects.test/get?signature=secret",
                        expected_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                        bundle_receipt=_receipt(hashlib.sha256(bundle).hexdigest()),
                        source_ids=source_ids,
                    ),
                    objects.client,
                    bundle_receipt_key=BUNDLE_RECEIPT_KEY,
                )
            assert caught.value.code == "INPUT_INVALID"


@pytest.mark.asyncio
async def test_read_rejects_invalid_saved_source_row():
    bundle = json.dumps(
        {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": SOURCE_ID,
                    "url": 1,
                    "title": "Primary source",
                    "published_date": None,
                    "text": "evidence",
                }
            ],
        }
    ).encode()
    objects = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, content=bundle
            )
        }
    )
    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get?signature=secret",
                    expected_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                    bundle_receipt=_receipt(hashlib.sha256(bundle).hexdigest()),
                    source_ids=["a"],
                ),
                objects.client,
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            )

    assert caught.value.code == "SOURCE_BUNDLE_INVALID"


@pytest.mark.asyncio
async def test_read_returns_saved_source_provenance_with_fragment():
    bundle = json.dumps(
        {
            "schema_version": 1,
            "sources": [
                {
                    "source_id": SOURCE_ID,
                    "url": "https://source.test/a",
                    "title": "Primary source",
                    "published_date": "2026-09-17",
                    "text": "evidence",
                }
            ],
        }
    ).encode()
    objects = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, content=bundle
            )
        }
    )
    async with objects.client:
        result = await read_web_fragments_impl(
            ReadWebFragmentsRequest(
                bundle_url="https://objects.test/get?signature=secret",
                expected_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                bundle_receipt=_receipt(hashlib.sha256(bundle).hexdigest()),
                source_ids=[SOURCE_ID],
            ),
            objects.client,
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
        )

    assert result.fragments[0].model_dump() == {
        "source_id": SOURCE_ID,
        "url": "https://source.test/a",
        "title": "Primary source",
        "published_date": "2026-09-17",
        "search_published_date": None,
        "search_request_id": None,
        "search_receipt": None,
        "text": "evidence",
        "total_chars": 8,
        "truncated": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("start", "max_chars"),
    [(-1, 1), (0, 0), (0, 12001)],
)
async def test_read_rejects_invalid_fragment_bounds(start, max_chars):
    with pytest.raises(ToolFailure) as caught:
        ReadWebFragmentsRequest(
            bundle_url="https://objects.test/get?signature=secret",
            expected_bundle_sha256="0" * 64,
            bundle_receipt=_receipt("0" * 64),
            source_ids=["a"],
            start=start,
            max_chars_per_source=max_chars,
        )

    assert caught.value.code == "INPUT_INVALID"


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not-json", b"x" * 101])
async def test_read_maps_malformed_or_oversized_bundle_to_stable_errors(content):
    objects = StubTransport(
        {
            ("GET", "https://objects.test/get?signature=secret"): httpx.Response(
                200, content=content
            )
        }
    )
    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get?signature=secret",
                    expected_bundle_sha256=hashlib.sha256(content).hexdigest(),
                    bundle_receipt=_receipt(hashlib.sha256(content).hexdigest()),
                    source_ids=["a"],
                ),
                objects.client,
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
                max_bundle_bytes=100,
            )

    assert caught.value.code in {"SOURCE_BUNDLE_INVALID", "SOURCE_TOO_LARGE"}
