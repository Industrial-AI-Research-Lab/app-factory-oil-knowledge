import hashlib
import json

import httpx
from app.news.result import finalize_news_result_impl
from app.web_source_receipts import issue_bundle_receipt

BUNDLE_RECEIPT_KEY = "test-bundle-receipt-key-000000000000"
SOURCE_A = hashlib.sha256(b"https://source.test/a").hexdigest()
SOURCE_B = hashlib.sha256(b"https://source.test/b").hexdigest()
MISSING = object()


def bundle_bytes() -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "created_at": "2026-09-17T10:00:00+00:00",
            "query": "polymer flooding",
            "sources": [
                {
                    "source_id": SOURCE_A,
                    "url": "https://source.test/a",
                    "title": "Primary report",
                    "published_date": "2026-09-10",
                    "text": "Field trial improved oil recovery.",
                },
                {
                    "source_id": SOURCE_B,
                    "url": "https://source.test/b",
                    "title": "Reprinted report",
                    "published_date": "2026-09-11",
                    "text": "Field trial improved oil recovery.",
                },
            ],
            "failures": [],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def approved_brief() -> dict:
    return {
        "query": "polymer flooding",
        "topic": "news",
        "start_date": "2026-09-01",
        "end_date": "2026-09-30",
        "include_domains": ["source.test"],
        "exclude_domains": [],
        "formats": ["json", "html"],
    }


def reviewed_decisions() -> dict:
    publications = []
    for source_id in (SOURCE_A, SOURCE_B):
        publications.append(
            {
                "source_id": source_id,
                "decision": "include",
                "reason": "Describes a field trial",
                "categories": ["polymer-flooding"],
                "evidence": [{"quote": "improved oil recovery"}],
                "limitations": [],
            }
        )
    return {
        "publications": publications,
        "events": [
            {
                "event_id": "event-1",
                "description": "A field trial reported improved recovery",
                "technology": "Polymer flooding",
                "task": "Improve oil recovery",
                "object": None,
                "time": "2026-09",
                "evidence_level": "field",
                "categories": ["polymer-flooding"],
                "limitations": [],
                "source_ids": [SOURCE_A, SOURCE_B],
                "evidence": [
                    {
                        "source_id": SOURCE_A,
                        "quote": "improved oil recovery",
                    },
                    {
                        "source_id": SOURCE_B,
                        "quote": "improved oil recovery",
                    },
                ],
                "merge_reason": "Exact reposts",
            }
        ],
    }


class NewsObjectTransport:
    def __init__(self, bundle: bytes | None = None) -> None:
        self.bundle = bundle if bundle is not None else bundle_bytes()
        self.requests: list[httpx.Request] = []
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, content=self.bundle)
        return httpx.Response(200)

    def uploaded_bytes(self) -> bytes:
        return next(
            request.content for request in self.requests if request.method == "PUT"
        )

    async def finalize(
        self,
        *,
        brief: object = MISSING,
        decisions: object = MISSING,
        critique: object = MISSING,
        expected_bundle_sha256: object = MISSING,
        bundle_receipt: object = MISSING,
    ):
        checksum = (
            hashlib.sha256(self.bundle).hexdigest()
            if expected_bundle_sha256 is MISSING
            else expected_bundle_sha256
        )
        receipt = (
            issue_bundle_receipt(checksum, BUNDLE_RECEIPT_KEY)
            if bundle_receipt is MISSING
            else bundle_receipt
        )
        return await finalize_news_result_impl(
            brief=approved_brief() if brief is MISSING else brief,
            decisions=reviewed_decisions() if decisions is MISSING else decisions,
            critique=(
                {"correction_cycle": 1, "notes": []}
                if critique is MISSING
                else critique
            ),
            source_bundle_url="https://objects.test/source-bundle",
            expected_bundle_sha256=checksum,
            bundle_receipt=receipt,
            upload_url="https://objects.test/news-result",
            client=self.client,
            bundle_receipt_key=BUNDLE_RECEIPT_KEY,
        )
