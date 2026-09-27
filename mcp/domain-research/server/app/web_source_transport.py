from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging

import httpx

from .errors import ToolFailure
from .http_io import _safe_location, _validate_url

logger = logging.getLogger(__name__)
MAX_BUNDLE_BYTES = 10_485_760
MAX_EXTRACT_RESPONSE_BYTES = 10_485_760


async def _read_response(
    response: httpx.Response, *, max_bytes: int, request_id: str
) -> bytes:
    if response.headers.get("Content-Encoding", "").strip().casefold() not in {
        "",
        "identity",
    }:
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_RESPONSE_INVALID — provider response used an unsupported encoding",
            request_id,
        )
        raise ToolFailure("PROVIDER_RESPONSE_INVALID", "Provider response is invalid")
    payload = bytearray()
    async for chunk in response.aiter_bytes():
        if len(payload) + len(chunk) > max_bytes:
            logger.warning(
                "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_RESPONSE_TOO_LARGE — provider response exceeded the byte limit",
                request_id,
            )
            raise ToolFailure(
                "PROVIDER_RESPONSE_TOO_LARGE",
                "Provider response exceeds the size limit",
            )
        payload.extend(chunk)
    return bytes(payload)


async def extract_from_tavily(
    urls: list[str],
    extract_depth: str,
    client: httpx.AsyncClient,
    *,
    api_key: str,
    base_url: str,
    request_id: str,
    timeout_seconds: float = 120,
) -> dict:
    logger.info(
        "[WEB_EXTRACT] request_id=%s provider=tavily url_count=%d — started",
        request_id,
        len(urls),
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            async with client.stream(
                "POST",
                f"{base_url.rstrip('/')}/extract",
                headers={
                    "Accept-Encoding": "identity",
                    "Authorization": f"Bearer {api_key}",
                },
                json={"urls": urls, "extract_depth": extract_depth},
            ) as response:
                response.raise_for_status()
                payload = await _read_response(
                    response,
                    max_bytes=MAX_EXTRACT_RESPONSE_BYTES,
                    request_id=request_id,
                )
    except (TimeoutError, httpx.TimeoutException):
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_TIMEOUT — provider request timed out",
            request_id,
        )
        raise ToolFailure(
            "PROVIDER_TIMEOUT", "Extract provider request timed out"
        ) from None
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            logger.warning(
                "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_AUTH — provider rejected authentication",
                request_id,
            )
            raise ToolFailure(
                "PROVIDER_AUTH", "Extract provider authentication failed"
            ) from None
        if exc.response.status_code == 429:
            logger.warning(
                "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_RATE_LIMIT — provider rate limit was exceeded",
                request_id,
            )
            raise ToolFailure(
                "PROVIDER_RATE_LIMIT", "Extract provider rate limit exceeded"
            ) from None
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_UNAVAILABLE — provider response was unavailable",
            request_id,
        )
        raise ToolFailure(
            "PROVIDER_UNAVAILABLE", "Extract provider is unavailable"
        ) from None
    except httpx.HTTPError:
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_UNAVAILABLE — provider connection failed",
            request_id,
        )
        raise ToolFailure(
            "PROVIDER_UNAVAILABLE", "Extract provider is unavailable"
        ) from None
    try:
        decoded = json.loads(payload)
    except (
        RecursionError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
    ):
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_RESPONSE_INVALID — provider response was invalid JSON",
            request_id,
        )
        raise ToolFailure(
            "PROVIDER_RESPONSE_INVALID", "Provider response is invalid"
        ) from None
    if not isinstance(decoded, dict):
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=provider code=PROVIDER_RESPONSE_INVALID — provider response was not an object",
            request_id,
        )
        raise ToolFailure("PROVIDER_RESPONSE_INVALID", "Provider response is invalid")
    return decoded


async def upload_bundle(
    client: httpx.AsyncClient,
    url: str,
    payload: bytes,
    *,
    timeout_seconds: float = 120,
    request_id: str = "-",
) -> None:
    validated_url = _validate_url(url)
    location = _safe_location(validated_url)
    try:
        async with asyncio.timeout(timeout_seconds):
            async with client.stream(
                "PUT",
                validated_url,
                content=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                response.raise_for_status()
    except (TimeoutError, httpx.TimeoutException, httpx.HTTPError):
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=upload location=%s code=UPLOAD_FAILED — signed object upload failed",
            request_id,
            location,
        )
        raise ToolFailure(
            "UPLOAD_FAILED", f"Could not upload result to {location}"
        ) from None


async def download_bundle(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    expected_sha256: str,
    timeout_seconds: float = 120,
    request_id: str = "-",
) -> bytes:
    validated_url = _validate_url(url)
    location = _safe_location(validated_url)
    payload = bytearray()
    try:
        async with asyncio.timeout(timeout_seconds):
            async with client.stream("GET", validated_url) as response:
                response.raise_for_status()
                if response.headers.get(
                    "Content-Encoding", ""
                ).strip().casefold() not in {
                    "",
                    "identity",
                }:
                    logger.warning(
                        "[WEB_EXTRACT] request_id=%s operation=download location=%s code=SOURCE_ENCODING_UNSUPPORTED — saved bundle used an unsupported encoding",
                        request_id,
                        location,
                    )
                    raise ToolFailure(
                        "SOURCE_ENCODING_UNSUPPORTED",
                        f"Source encoding is not supported at {location}",
                    )
                async for chunk in response.aiter_bytes():
                    if len(payload) + len(chunk) > max_bytes:
                        logger.warning(
                            "[WEB_EXTRACT] request_id=%s operation=download location=%s code=SOURCE_TOO_LARGE — saved bundle exceeded the byte limit",
                            request_id,
                            location,
                        )
                        raise ToolFailure(
                            "SOURCE_TOO_LARGE",
                            f"Source at {location} exceeds the download limit",
                        )
                    payload.extend(chunk)
    except ToolFailure:
        raise
    except (TimeoutError, httpx.TimeoutException, httpx.HTTPError):
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=download location=%s code=SOURCE_DOWNLOAD_FAILED — signed object download failed",
            request_id,
            location,
        )
        raise ToolFailure(
            "SOURCE_DOWNLOAD_FAILED", f"Could not download source at {location}"
        ) from None
    result = bytes(payload)
    if not hmac.compare_digest(
        hashlib.sha256(result).hexdigest(),
        expected_sha256.lower(),
    ):
        logger.warning(
            "[WEB_EXTRACT] request_id=%s operation=download location=%s code=SOURCE_CHECKSUM_MISMATCH — saved bundle checksum did not match",
            request_id,
            location,
        )
        raise ToolFailure(
            "SOURCE_CHECKSUM_MISMATCH",
            f"Source checksum does not match at {location}",
        )
    return result
