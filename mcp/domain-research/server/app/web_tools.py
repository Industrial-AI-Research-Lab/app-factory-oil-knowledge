from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastmcp import FastMCP
from pydantic import Field, ValidationError

from .config import Settings
from .errors import ToolFailure
from .models import (
    DomainFilters,
    ExactMatch,
    SearchDate,
    SearchQuery,
    SearchResultCount,
    WebSearchRequest,
    WebSearchToolResponse,
)
from .news.result import finalize_news_result_impl
from .news.validation import (
    NewsBrief,
    NewsCritique,
    NewsDecisions,
)
from .search_evidence import SearchEvidence
from .web_input_validation import InputValidationMiddleware, _error_response
from .web_search import search_web_impl
from .web_sources import (
    ExtractRequest,
    ReadWebFragmentsRequest,
    extract_web_pages_impl,
    read_web_fragments_impl,
)

logger = logging.getLogger(__name__)


async def search_web(
    query: SearchQuery,
    topic: Literal["general", "news"] = "general",
    start_date: SearchDate | None = None,
    end_date: SearchDate | None = None,
    include_domains: DomainFilters | None = None,
    exclude_domains: DomainFilters | None = None,
    exact_match: ExactMatch = False,
    search_depth: Literal["basic", "advanced"] = "basic",
    max_results: SearchResultCount = 10,
) -> WebSearchToolResponse:
    request_id = uuid4().hex
    try:
        request = WebSearchRequest(
            query=query,
            topic=topic,
            start_date=start_date,
            end_date=end_date,
            include_domains=include_domains or [],
            exclude_domains=exclude_domains or [],
            exact_match=exact_match,
            search_depth=search_depth,
            max_results=max_results,
        )
        settings = Settings.from_env()
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            result = await search_web_impl(
                request,
                client,
                request_id=request_id,
            )
        return WebSearchToolResponse.model_validate(result.model_dump())
    except ValidationError:
        logger.warning(
            "[WEB_SEARCH] request_id=%s code=INPUT_INVALID — web search input is invalid",
            request_id,
        )
        failure = ToolFailure("INPUT_INVALID", "Invalid web search request")
        return _error_response(failure, request_id)
    except ToolFailure as failure:
        return _error_response(failure, request_id)


async def extract_web_pages(
    urls: list[str],
    upload_url: str,
    query: Annotated[
        str | None,
        Field(
            description=(
                "Copy the approved brief.query exactly, including for empty urls. "
                "News finalization requires the saved snapshot query to match."
            )
        ),
    ] = None,
    extract_depth: Literal["basic", "advanced"] = "advanced",
    search_evidence: Annotated[
        list[SearchEvidence] | None,
        Field(
            description=(
                "One complete evidence object for each requested URL. Copy request_id "
                "from the search_web response and source_id, url, published_date, "
                "search_receipt from its matching result without changes. "
                "Keep published_date=null when Search returned no date."
            )
        ),
    ] = None,
) -> dict:
    request_id = uuid4().hex
    try:
        request = ExtractRequest(
            urls=urls,
            search_evidence=search_evidence,
            upload_url=upload_url,
            query=query,
            extract_depth=extract_depth,
        )
        settings = Settings.from_env()
        async with (
            httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                follow_redirects=False,
            ) as tavily_client,
            httpx.AsyncClient(
                timeout=settings.request_timeout_seconds,
                follow_redirects=False,
            ) as object_client,
        ):
            result = await extract_web_pages_impl(
                request,
                tavily_client,
                object_client,
                api_key=settings.tavily_api_key,
                bundle_receipt_key=settings.bundle_receipt_key,
                base_url=settings.tavily_base_url,
                request_id=request_id,
                timeout_seconds=settings.request_timeout_seconds,
            )
        return result.model_dump(mode="json")
    except ToolFailure as failure:
        return {**failure.as_result(), "request_id": request_id}


async def read_web_fragments(
    bundle_url: str,
    expected_bundle_sha256: str,
    bundle_receipt: str,
    source_ids: list[str],
    start: int = 0,
    max_chars_per_source: int = 6000,
) -> dict:
    request_id = uuid4().hex
    try:
        request = ReadWebFragmentsRequest(
            bundle_url=bundle_url,
            expected_bundle_sha256=expected_bundle_sha256,
            bundle_receipt=bundle_receipt,
            source_ids=source_ids,
            start=start,
            max_chars_per_source=max_chars_per_source,
        )
        settings = Settings.from_env()
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        ) as object_client:
            result = await read_web_fragments_impl(
                request,
                object_client,
                bundle_receipt_key=settings.bundle_receipt_key,
                request_id=request_id,
                timeout_seconds=settings.request_timeout_seconds,
            )
        return result.model_dump(mode="json")
    except ToolFailure as failure:
        return {**failure.as_result(), "request_id": request_id}


async def finalize_news_result(
    brief: NewsBrief,
    decisions: NewsDecisions,
    critique: NewsCritique,
    source_bundle_url: str,
    expected_bundle_sha256: str,
    bundle_receipt: str,
    upload_url: str,
) -> dict:
    request_id = uuid4().hex
    try:
        settings = Settings.from_env()
        async with httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        ) as object_client:
            result = await finalize_news_result_impl(
                brief=brief,
                decisions=decisions,
                critique=critique,
                source_bundle_url=source_bundle_url,
                expected_bundle_sha256=expected_bundle_sha256,
                bundle_receipt=bundle_receipt,
                upload_url=upload_url,
                client=object_client,
                bundle_receipt_key=settings.bundle_receipt_key,
                request_id=request_id,
                timeout_seconds=settings.request_timeout_seconds,
            )
        logger.info(
            "[NEWS_FINALIZE] request_id=%s publications=%d events=%d sha256=%s — uploaded",
            request_id,
            result.publication_count,
            result.event_count,
            result.sha256,
        )
        return {"status": "ok", "request_id": request_id, **asdict(result)}
    except ToolFailure as failure:
        logger.warning(
            "[NEWS_FINALIZE] request_id=%s code=%s — rejected",
            request_id,
            failure.code,
        )
        return {**failure.as_result(), "request_id": request_id}


def register_web_tools(mcp: FastMCP) -> None:
    mcp.add_middleware(InputValidationMiddleware())
    mcp.tool(name="search_web")(search_web)
    mcp.tool(name="extract_web_pages")(extract_web_pages)
    mcp.tool(name="read_web_fragments")(read_web_fragments)
    mcp.tool(name="finalize_news_result")(finalize_news_result)
