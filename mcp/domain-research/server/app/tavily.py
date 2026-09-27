from __future__ import annotations

import asyncio
import logging
import time
from typing import NoReturn

import httpx

from .config import Settings
from .errors import ToolFailure
from .models import WebSearchFilters, WebSearchRequest, WebSearchResponse
from .tavily_response import ProviderResponse, provider_rows

logger = logging.getLogger(__name__)


def _duration_ms(started_at: float) -> int:
    return int((time.monotonic() - started_at) * 1_000)


def _raise_invalid_response(request_id: str, started_at: float) -> NoReturn:
    logger.warning(
        "[WEB_SEARCH] request_id=%s provider=tavily duration_ms=%d "
        "code=PROVIDER_RESPONSE_INVALID — provider response is invalid",
        request_id,
        _duration_ms(started_at),
    )
    raise ToolFailure(
        "PROVIDER_RESPONSE_INVALID",
        "Search provider returned an invalid response",
    )


class TavilyClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def search(
        self,
        request: WebSearchRequest,
        *,
        request_id: str,
        filter_dates: bool = True,
        tolerant_dates: bool = False,
    ) -> WebSearchResponse:
        started_at = time.monotonic()
        payload = request.model_dump(mode="json")
        payload["query"] = request.provider_query()
        payload["include_raw_content"] = False
        payload["filter_by_published_date"] = filter_dates and bool(
            request.start_date or request.end_date
        )
        logger.info(
            "[WEB_SEARCH] request_id=%s provider=tavily topic=%s max_results=%d "
            "— provider request started",
            request_id,
            request.topic,
            request.max_results,
        )
        try:
            response_payload = bytearray()
            async with asyncio.timeout(self._settings.request_timeout_seconds):
                async with self._client.stream(
                    "POST",
                    f"{self._settings.tavily_base_url}/search",
                    headers={
                        "Accept-Encoding": "identity",
                        "Authorization": f"Bearer {self._settings.tavily_api_key}",
                    },
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    content_encoding = response.headers.get("Content-Encoding", "")
                    if content_encoding.strip().casefold() not in {"", "identity"}:
                        _raise_invalid_response(request_id, started_at)
                    async for chunk in response.aiter_bytes(chunk_size=65_536):
                        if (
                            len(response_payload) + len(chunk)
                            > self._settings.max_search_response_bytes
                        ):
                            logger.warning(
                                "[WEB_SEARCH] request_id=%s provider=tavily "
                                "duration_ms=%d code=PROVIDER_RESPONSE_TOO_LARGE "
                                "— provider response exceeds the size limit",
                                request_id,
                                _duration_ms(started_at),
                            )
                            raise ToolFailure(
                                "PROVIDER_RESPONSE_TOO_LARGE",
                                "Search provider response exceeds the size limit",
                            )
                        response_payload.extend(chunk)
        except (TimeoutError, httpx.TimeoutException):
            logger.warning(
                "[WEB_SEARCH] request_id=%s provider=tavily duration_ms=%d "
                "code=PROVIDER_TIMEOUT — provider request timed out",
                request_id,
                _duration_ms(started_at),
            )
            raise ToolFailure(
                "PROVIDER_TIMEOUT",
                "Search provider request timed out",
            ) from None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                logger.warning(
                    "[WEB_SEARCH] request_id=%s provider=tavily status=%d "
                    "duration_ms=%d code=PROVIDER_AUTH "
                    "— provider rejected authentication",
                    request_id,
                    exc.response.status_code,
                    _duration_ms(started_at),
                )
                raise ToolFailure(
                    "PROVIDER_AUTH",
                    "Search provider authentication failed",
                ) from None
            if exc.response.status_code == 429:
                logger.warning(
                    "[WEB_SEARCH] request_id=%s provider=tavily status=429 "
                    "duration_ms=%d code=PROVIDER_RATE_LIMIT "
                    "— provider rate limit was exceeded",
                    request_id,
                    _duration_ms(started_at),
                )
                raise ToolFailure(
                    "PROVIDER_RATE_LIMIT",
                    "Search provider rate limit exceeded",
                ) from None
            status = exc.response.status_code
            code, outcome = (
                ("PROVIDER_REJECTED", "rejected the request")
                if 400 <= status < 500
                else ("PROVIDER_UNAVAILABLE", "is unavailable")
            )
            logger.warning(
                "[WEB_SEARCH] request_id=%s provider=tavily status=%d "
                "duration_ms=%d code=%s "
                "— provider returned an unsuccessful response",
                request_id,
                status,
                _duration_ms(started_at),
                code,
            )
            raise ToolFailure(
                code,
                f"Search provider {outcome} (HTTP {status})",
            ) from None
        except httpx.RequestError:
            logger.warning(
                "[WEB_SEARCH] request_id=%s provider=tavily duration_ms=%d "
                "code=PROVIDER_UNAVAILABLE — provider connection failed",
                request_id,
                _duration_ms(started_at),
            )
            raise ToolFailure(
                "PROVIDER_UNAVAILABLE",
                "Search provider is unavailable",
            ) from None
        try:
            provider = ProviderResponse.model_validate_json(response_payload)
        except (TypeError, ValueError):
            _raise_invalid_response(request_id, started_at)

        results, skipped = provider_rows(
            provider,
            request,
            filter_dates=filter_dates,
            tolerant_dates=tolerant_dates,
        )
        if provider.results and skipped == len(provider.results):
            _raise_invalid_response(request_id, started_at)
        if skipped:
            logger.warning(
                "[WEB_SEARCH] request_id=%s provider=tavily skipped=%d "
                "— malformed provider rows dropped",
                request_id,
                skipped,
            )

        provider_request_id = provider.request_id
        logger.info(
            "[WEB_SEARCH] request_id=%s provider=tavily provider_request_id=%s "
            "provider_status=200 duration_ms=%d result_count=%d "
            "— provider request completed",
            request_id,
            provider_request_id or "-",
            _duration_ms(started_at),
            len(results),
        )
        return WebSearchResponse(
            request_id=request_id,
            provider_request_id=provider_request_id,
            filters=WebSearchFilters(
                topic=request.topic,
                start_date=request.start_date,
                end_date=request.end_date,
                include_domains=request.include_domains,
                exclude_domains=request.exclude_domains,
                exact_match=request.exact_match,
                search_depth=request.search_depth,
                max_results=request.max_results,
            ),
            results=results,
        )
