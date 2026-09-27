from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

import httpx

from .errors import ToolFailure
from .search_evidence import (
    saved_search_evidence,
    validated_search_evidence,
    verify_search_evidence,
)
from .tavily_response import canonical_url
from .web_source_models import ExtractResponse, ReadResponse, WebSourceFragment
from .web_source_normalization import sources_from_provider, valid_saved_source
from .web_source_receipts import (
    issue_bundle_receipt,
    verify_bundle_receipt,
)
from .web_source_requests import MAX_SOURCES, ExtractRequest, ReadWebFragmentsRequest
from .web_source_transport import (
    MAX_BUNDLE_BYTES,
    download_bundle,
    extract_from_tavily,
    upload_bundle,
)

logger = logging.getLogger(__name__)
MAX_PREVIEW_CHARS = 600


async def extract_web_pages_impl(
    request: ExtractRequest,
    tavily_client: httpx.AsyncClient,
    object_client: httpx.AsyncClient,
    *,
    api_key: str,
    bundle_receipt_key: str | bytes,
    base_url: str,
    clock: Callable[[], datetime] = datetime.now,
    request_id: str | None = None,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
    timeout_seconds: float = 120,
) -> ExtractResponse:
    identifier = request_id or uuid4().hex
    try:
        evidence = validated_search_evidence(
            request.search_evidence, request.urls, bundle_receipt_key
        )
    except ToolFailure:
        logger.warning(
            "[WEB_EXTRACT] request_id=%s code=INPUT_INVALID — search evidence rejected",
            identifier,
        )
        raise
    if request.urls:
        provider = await extract_from_tavily(
            request.urls,
            request.extract_depth,
            tavily_client,
            api_key=api_key,
            base_url=base_url,
            request_id=identifier,
            timeout_seconds=timeout_seconds,
        )
        try:
            sources, failures = sources_from_provider(
                provider, {canonical_url(url) for url in request.urls}
            )
        except ToolFailure as failure:
            logger.warning(
                "[WEB_EXTRACT] request_id=%s operation=normalize code=%s — provider data could not be normalized",
                identifier,
                failure.code,
            )
            raise
    else:
        sources, failures = [], []
        logger.warning(
            "[WEB_EXTRACT] request_id=%s — no URLs; saving an empty snapshot",
            identifier,
        )
    if request.search_evidence is not None:
        for source in sources:
            item = evidence[source["source_id"]]
            source.update(
                search_published_date=item["published_date"],
                search_request_id=item["request_id"],
                search_receipt=item["search_receipt"],
            )
    bundle = {
        "schema_version": 1,
        "created_at": clock().isoformat(),
        "query": request.query,
        "sources": sources,
        "failures": failures,
    }
    payload = json.dumps(
        bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > max_bundle_bytes:
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=bundle code=SOURCE_TOO_LARGE — saved bundle exceeded the size limit",
            identifier,
        )
        raise ToolFailure(
            "SOURCE_TOO_LARGE", "Saved source bundle exceeds the size limit"
        )
    await upload_bundle(
        object_client,
        request.upload_url,
        payload,
        timeout_seconds=timeout_seconds,
        request_id=identifier,
    )
    bundle_sha256 = hashlib.sha256(payload).hexdigest()
    logger.info(
        "[WEB_EXTRACT] request_id=%s source_count=%d failure_count=%d bytes=%d sha256=%s — completed",
        identifier,
        len(sources),
        len(failures),
        len(payload),
        bundle_sha256,
    )
    return ExtractResponse(
        status="partial" if failures else "ok",
        request_id=identifier,
        bundle_sha256=bundle_sha256,
        bundle_receipt=issue_bundle_receipt(bundle_sha256, bundle_receipt_key),
        size_bytes=len(payload),
        source_ids=[source["source_id"] for source in sources],
        previews=[
            {
                "source_id": source["source_id"],
                "text": source["text"][:MAX_PREVIEW_CHARS],
                "truncated": len(source["text"]) > MAX_PREVIEW_CHARS,
            }
            for source in sources
        ],
        failures=failures,
    )


async def read_web_fragments_impl(
    request: ReadWebFragmentsRequest,
    object_client: httpx.AsyncClient,
    *,
    bundle_receipt_key: str | bytes,
    max_bundle_bytes: int = MAX_BUNDLE_BYTES,
    request_id: str | None = None,
    timeout_seconds: float = 120,
    max_sources: int = MAX_SOURCES,
) -> ReadResponse:
    indexed = await load_saved_sources(
        object_client,
        request.bundle_url,
        request.expected_bundle_sha256,
        request.bundle_receipt,
        bundle_receipt_key=bundle_receipt_key,
        max_bundle_bytes=max_bundle_bytes,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
        max_sources=max_sources,
    )
    fragments = []
    for source_id in request.source_ids:
        source = indexed.get(source_id)
        if source is None:
            logger.warning(
                "[WEB_EXTRACT] request_id=%s operation=read code=INPUT_INVALID — requested source is absent from saved bundle",
                request_id or "-",
            )
            raise ToolFailure(
                "INPUT_INVALID", "source_id does not exist in saved bundle"
            )
        text = source["text"]
        fragment = text[request.start : request.start + request.max_chars_per_source]
        fragments.append(
            WebSourceFragment(
                source_id=source_id,
                url=source.get("url", ""),
                title=source.get("title", ""),
                published_date=source.get("published_date"),
                search_published_date=source.get("search_published_date"),
                search_request_id=source.get("search_request_id"),
                search_receipt=source.get("search_receipt"),
                text=fragment,
                total_chars=len(text),
                truncated=request.start + len(fragment) < len(text),
            )
        )
    identifier = request_id or uuid4().hex
    logger.info(
        "[WEB_EXTRACT] request_id=%s operation=read fragment_count=%d — completed",
        identifier,
        len(fragments),
    )
    return ReadResponse(request_id=identifier, fragments=fragments)


async def load_saved_sources(
    object_client: httpx.AsyncClient,
    bundle_url: str,
    expected_bundle_sha256: str,
    bundle_receipt: str,
    *,
    bundle_receipt_key: str | bytes,
    max_bundle_bytes: int,
    request_id: str | None,
    timeout_seconds: float,
    max_sources: int,
    tag: str = "WEB_EXTRACT",
    operation: str = "read",
) -> dict[str, dict]:
    verify_bundle_receipt(expected_bundle_sha256, bundle_receipt, bundle_receipt_key)
    payload = await download_bundle(
        object_client,
        bundle_url,
        max_bytes=max_bundle_bytes,
        expected_sha256=expected_bundle_sha256,
        timeout_seconds=timeout_seconds,
        request_id=request_id or "-",
    )
    try:
        bundle = json.loads(payload)
        sources = bundle["sources"]
        if bundle["schema_version"] != 1 or not isinstance(sources, list):
            raise ValueError
        indexed = {source["source_id"]: source for source in sources}
        if (
            len(sources) > max_sources
            or len(indexed) != len(sources)
            or any(not valid_saved_source(source) for source in sources)
        ):
            raise ValueError
    except (
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ):
        logger.warning(
            "[%s] request_id=%s operation=%s code=SOURCE_BUNDLE_INVALID — saved bundle failed validation",
            tag,
            request_id or "-",
            operation,
        )
        raise ToolFailure(
            "SOURCE_BUNDLE_INVALID", "Saved source bundle is invalid"
        ) from None
    for source in sources:
        evidence = saved_search_evidence(source)
        if evidence is not None:
            try:
                verify_search_evidence(evidence, bundle_receipt_key)
            except ToolFailure:
                logger.warning(
                    "[%s] request_id=%s operation=%s — search evidence rejected",
                    tag,
                    request_id or "-",
                    operation,
                )
                raise
    return indexed
