from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings, _origin_key
from .errors import ToolFailure

logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True)
class UploadResult:
    sha256: str
    size_bytes: int
    content_type: str


@contextmanager
def temporary_call_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="domain-research-") as directory:
        yield Path(directory)


def _safe_location(url: httpx.URL) -> str:
    return f"{url.host or '<invalid-host>'}{url.path or '/'}"


def _validate_url(url: str) -> httpx.URL:
    if not isinstance(url, str) or not url.strip():
        raise ToolFailure("URL_INVALID", "Signed object URL is invalid")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ToolFailure("URL_INVALID", "Signed object URL is invalid")
    try:
        split_url = urlsplit(url)
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, UnicodeError, ValueError):
        raise ToolFailure("URL_INVALID", "Signed object URL is invalid") from None
    if not split_url.hostname or split_url.username or split_url.password:
        raise ToolFailure("URL_INVALID", "Signed object URL is invalid")
    settings = Settings.from_env()
    scheme_is_allowed = parsed.scheme == "https" or (
        parsed.scheme == "http"
        and (
            settings.allow_insecure_object_storage
            or (_is_loopback(parsed.host) and settings.allow_insecure_loopback)
        )
    )
    if not scheme_is_allowed:
        raise ToolFailure(
            "URL_SCHEME_INVALID",
            "Signed object URL must use HTTPS outside local tests",
        )
    origin = _origin_key(parsed.scheme, parsed.host, parsed.port)
    if origin not in settings.object_storage_allowed_origins:
        raise ToolFailure(
            "URL_HOST_INVALID",
            "Signed object URL origin is not allowed",
        )
    return parsed


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=Settings.from_env().request_timeout_seconds,
        follow_redirects=False,
    )


def _failure_detail(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return type(exc).__name__


def _valid_content_type(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return value == value.strip() and all(32 <= byte < 127 for byte in encoded)


async def download_bytes(
    url: str,
    *,
    max_bytes: int,
    expected_sha256: str | None = None,
) -> bytes:
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ToolFailure("INPUT_INVALID", "max_bytes must be a positive integer")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or _SHA256_PATTERN.fullmatch(expected_sha256) is None
    ):
        raise ToolFailure("INPUT_INVALID", "expected_sha256 must be 64 hex characters")
    validated_url = _validate_url(url)
    location = _safe_location(validated_url)
    payload = bytearray()

    try:
        async with (
            _http_client() as client,
            client.stream("GET", validated_url) as response,
        ):
            response.raise_for_status()
            content_encoding = response.headers.get("Content-Encoding", "")
            if content_encoding.strip().casefold() not in {"", "identity"}:
                logger.warning(
                    "[SIGNED_IO] operation=download location=%s "
                    "code=SOURCE_ENCODING_UNSUPPORTED",
                    location,
                )
                raise ToolFailure(
                    "SOURCE_ENCODING_UNSUPPORTED",
                    f"Source encoding is not supported at {location}",
                )
            async for chunk in response.aiter_bytes():
                if len(payload) + len(chunk) > max_bytes:
                    logger.warning(
                        "[SIGNED_IO] operation=download location=%s code=SOURCE_TOO_LARGE",
                        location,
                    )
                    raise ToolFailure(
                        "SOURCE_TOO_LARGE",
                        f"Source at {location} exceeds the download limit",
                    )
                payload.extend(chunk)
    except ToolFailure:
        raise
    except httpx.HTTPError as exc:
        detail = _failure_detail(exc)
        logger.warning(
            "[SIGNED_IO] operation=download location=%s code=SOURCE_DOWNLOAD_FAILED cause=%s",
            location,
            detail,
        )
        raise ToolFailure(
            "SOURCE_DOWNLOAD_FAILED",
            f"Could not download source at {location} ({detail})",
        ) from None

    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None and not hmac.compare_digest(
        actual_sha256,
        expected_sha256.lower(),
    ):
        logger.warning(
            "[SIGNED_IO] operation=download location=%s code=SOURCE_CHECKSUM_MISMATCH",
            location,
        )
        raise ToolFailure(
            "SOURCE_CHECKSUM_MISMATCH",
            f"Source checksum does not match at {location}",
        )

    logger.info(
        "[SIGNED_IO] operation=download location=%s bytes=%d sha256=%s",
        location,
        len(payload),
        actual_sha256,
    )
    return bytes(payload)


async def upload_bytes(
    url: str,
    payload: bytes,
    *,
    content_type: str,
) -> UploadResult:
    if not isinstance(payload, bytes):
        raise ToolFailure("INPUT_INVALID", "payload must be bytes")
    if not _valid_content_type(content_type):
        raise ToolFailure(
            "INPUT_INVALID",
            "content_type must be printable ASCII without surrounding whitespace",
        )
    validated_url = _validate_url(url)
    location = _safe_location(validated_url)

    try:
        async with (
            _http_client() as client,
            client.stream(
                "PUT",
                validated_url,
                content=payload,
                headers={"Content-Type": content_type},
            ) as response,
        ):
            response.raise_for_status()
    except httpx.HTTPError as exc:
        detail = _failure_detail(exc)
        logger.warning(
            "[SIGNED_IO] operation=upload location=%s code=UPLOAD_FAILED cause=%s",
            location,
            detail,
        )
        raise ToolFailure(
            "UPLOAD_FAILED",
            f"Could not upload result to {location} ({detail})",
        ) from None

    sha256 = hashlib.sha256(payload).hexdigest()
    logger.info(
        "[SIGNED_IO] operation=upload location=%s bytes=%d sha256=%s",
        location,
        len(payload),
        sha256,
    )
    return UploadResult(
        sha256=sha256,
        size_bytes=len(payload),
        content_type=content_type,
    )
