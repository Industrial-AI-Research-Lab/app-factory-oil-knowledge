from __future__ import annotations

from uuid import uuid4

import httpx

from .config import Settings
from .models import WebSearchRequest, WebSearchResponse
from .search_evidence import issue_search_receipt
from .tavily import TavilyClient


async def search_web_impl(
    request: WebSearchRequest,
    client: httpx.AsyncClient,
    *,
    request_id: str | None = None,
) -> WebSearchResponse:
    settings = Settings.from_env()
    result = await TavilyClient(settings, client).search(
        request,
        request_id=request_id or uuid4().hex,
    )
    for row in result.results:
        evidence = {
            key: row.model_dump(mode="json")[key]
            for key in ("source_id", "url", "published_date")
        }
        evidence["request_id"] = result.request_id
        row.search_receipt = issue_search_receipt(evidence, settings.bundle_receipt_key)
    return result
