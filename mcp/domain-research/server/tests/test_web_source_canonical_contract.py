import hashlib
import json

import httpx
import pytest

from app.errors import ToolFailure
from app.models import MAX_SEARCH_URL_CHARS
from app.tavily_response import canonical_url
from app.web_source_normalization import sources_from_provider
from app.web_source_receipts import issue_bundle_receipt
from app.web_sources import (
    ExtractRequest,
    ReadWebFragmentsRequest,
    extract_web_pages_impl,
    read_web_fragments_impl,
)

BUNDLE_RECEIPT_KEY = "test-bundle-receipt-key-000000000000"


def _receipt(payload: bytes) -> str:
    return issue_bundle_receipt(hashlib.sha256(payload).hexdigest(), BUNDLE_RECEIPT_KEY)


class StubTransport:
    def __init__(self, content: bytes) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=content)
            )
        )


def _expanded_url() -> str:
    return "https://source.test/" + "é" * (MAX_SEARCH_URL_CHARS // 2)


def _saved_source(**changes: object) -> dict[str, object]:
    url = "https://source.test/a"
    source = {
        "source_id": hashlib.sha256(url.encode()).hexdigest(),
        "url": url,
        "title": "Primary source",
        "published_date": None,
        "text": "evidence",
    }
    source.update(changes)
    return source


def _bundle(source: dict[str, object]) -> bytes:
    return json.dumps({"schema_version": 1, "sources": [source]}).encode()


def test_canonical_url_uses_one_identity_for_homepage():
    assert canonical_url("https://source.test") == "https://source.test/"
    assert canonical_url("https://source.test/") == "https://source.test/"


def test_extract_request_rejects_homepage_spellings_as_duplicates():
    with pytest.raises(ToolFailure) as caught:
        ExtractRequest(
            urls=["https://source.test", "https://source.test/"],
            upload_url="https://objects.test/put",
        )

    assert caught.value.code == "INPUT_INVALID"


def test_provider_homepage_spelling_reconciles_with_requested_url():
    sources, failures = sources_from_provider(
        {
            "results": [
                {
                    "url": "https://source.test/",
                    "raw_content": "evidence",
                }
            ],
            "failed_results": [],
        },
        {canonical_url("https://source.test")},
    )

    assert failures == []
    assert sources[0]["url"] == "https://source.test/"
    assert (
        sources[0]["source_id"] == hashlib.sha256(b"https://source.test/").hexdigest()
    )


REDIRECTED = {
    "results": [
        {"url": "https://source.test/a", "raw_content": "evidence"},
        {"url": "https://elsewhere.test/b", "raw_content": "moved"},
    ]
}


def test_default_normalizer_still_rejects_a_whole_response_for_one_redirect():
    with pytest.raises(ToolFailure) as caught:
        sources_from_provider(
            REDIRECTED, {"https://source.test/a", "https://source.test/b"}
        )
    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


def test_per_url_normalizer_fails_only_the_redirected_url():
    sources, failures = sources_from_provider(
        REDIRECTED,
        {"https://source.test/a", "https://source.test/b"},
        per_url_failures=True,
    )
    assert [source["url"] for source in sources] == ["https://source.test/a"]
    assert [(item["url"], item["code"]) for item in failures] == [
        ("https://source.test/b", "PROVIDER_RESPONSE_INVALID")
    ]


def test_per_url_normalizer_with_every_page_failed_raises_nothing():
    sources, failures = sources_from_provider(
        {"results": [], "failed_results": [{"url": "https://source.test/a"}]},
        {"https://source.test/a"},
        per_url_failures=True,
    )
    assert sources == []
    assert failures == [{"url": "https://source.test/a", "code": "PROVIDER_FAILED"}]


def test_extract_request_rejects_unicode_url_expanded_past_canonical_limit():
    url = _expanded_url()

    assert len(url) < MAX_SEARCH_URL_CHARS
    with pytest.raises(ToolFailure) as caught:
        ExtractRequest(urls=[url], upload_url="https://objects.test/put")

    assert caught.value.code == "INPUT_INVALID"


@pytest.mark.asyncio
async def test_extract_does_not_put_oversized_canonical_provider_url():
    url = _expanded_url()
    provider = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "results": [{"url": url, "raw_content": "evidence"}],
                    "failed_results": [],
                },
            )
        )
    )
    put_requests: list[httpx.Request] = []
    objects = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: put_requests.append(request) or httpx.Response(200)
        )
    )

    async with provider, objects:
        with pytest.raises(ToolFailure) as caught:
            await extract_web_pages_impl(
                ExtractRequest(
                    urls=["https://source.test/a"],
                    upload_url="https://objects.test/put",
                ),
                provider,
                objects,
                api_key="test-key",
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
                base_url="https://api.tavily.test",
            )

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"
    assert put_requests == []


def test_normalizer_rejects_oversized_canonical_failed_provider_url():
    url = _expanded_url()

    with pytest.raises(ToolFailure) as caught:
        sources_from_provider(
            {
                "results": [
                    {"url": "https://source.test/a", "raw_content": "evidence"}
                ],
                "failed_results": [{"url": url}],
            },
            {
                "https://source.test/a",
                httpx.URL(url).copy_with(fragment=None).__str__(),
            },
        )

    assert caught.value.code == "PROVIDER_RESPONSE_INVALID"


@pytest.mark.asyncio
async def test_read_rejects_malformed_saved_url_as_source_bundle_invalid():
    bundle = _bundle(_saved_source(url="http://["))
    objects = StubTransport(bundle)

    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get",
                    expected_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                    bundle_receipt=_receipt(bundle),
                    source_ids=[hashlib.sha256(b"https://source.test/a").hexdigest()],
                ),
                objects.client,
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            )

    assert caught.value.code == "SOURCE_BUNDLE_INVALID"


@pytest.mark.asyncio
async def test_read_rejects_non_iso_saved_publication_date():
    source = _saved_source(published_date="2026/09/17")
    bundle = _bundle(source)
    objects = StubTransport(bundle)

    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get",
                    expected_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                    bundle_receipt=_receipt(bundle),
                    source_ids=[source["source_id"]],
                ),
                objects.client,
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            )

    assert caught.value.code == "SOURCE_BUNDLE_INVALID"


@pytest.mark.asyncio
async def test_read_rejects_saved_source_id_not_matching_canonical_url():
    source = _saved_source(source_id="b" * 64)
    bundle = _bundle(source)
    objects = StubTransport(bundle)

    async with objects.client:
        with pytest.raises(ToolFailure) as caught:
            await read_web_fragments_impl(
                ReadWebFragmentsRequest(
                    bundle_url="https://objects.test/get",
                    expected_bundle_sha256=hashlib.sha256(bundle).hexdigest(),
                    bundle_receipt=_receipt(bundle),
                    source_ids=[source["source_id"]],
                ),
                objects.client,
                bundle_receipt_key=BUNDLE_RECEIPT_KEY,
            )

    assert caught.value.code == "SOURCE_BUNDLE_INVALID"
