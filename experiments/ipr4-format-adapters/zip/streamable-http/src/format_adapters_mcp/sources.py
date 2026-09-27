"""Fetch a SEG-Y or WITSML file from the separate synthetic-data service."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from format_adapters_mcp.contract import envelope

FILE_FETCH_TIMEOUT_S = 30.0
DEFAULT_SEGY_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_WITSML_MAX_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class RemoteFile:
    path: Path
    url: str


def relative_key(user_path: str) -> str | None:
    raw = user_path.strip().replace("\\", "/")
    if not raw or raw.startswith("/") or "://" in raw:
        return None
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _max_bytes(env_name: str, default: int) -> int:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def load_remote(
    base_env: str,
    user_path: str,
    *,
    format_name: str,
    suffix: str,
    max_bytes_env: str,
    default_max_bytes: int,
) -> RemoteFile | dict | None:
    """Temp file, an error envelope, or None when the base URL is unset."""
    base = os.environ.get(base_env, "").strip()
    if not base:
        return None
    key = relative_key(user_path)
    if key is None:
        return envelope(
            status="error",
            reason="path_outside_source",
            format_name=format_name,
            source=user_path,
        )
    url = base.rstrip("/") + "/" + key
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return envelope(
            status="error",
            reason="source_url_invalid",
            format_name=format_name,
            source=url,
        )
    limit = _max_bytes(max_bytes_env, default_max_bytes)
    try:
        with httpx.stream(
            "GET",
            url,
            timeout=FILE_FETCH_TIMEOUT_S,
            follow_redirects=False,
        ) as response:
            if response.status_code >= 400:
                return envelope(
                    status="error",
                    reason=f"http_{response.status_code}",
                    format_name=format_name,
                    source=url,
                )
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > limit:
                    return envelope(
                        status="error",
                        reason="source_too_large",
                        format_name=format_name,
                        source=url,
                    )
                chunks.append(chunk)
    except httpx.TimeoutException:
        return envelope(
            status="timeout",
            reason="timeout",
            format_name=format_name,
            source=url,
        )
    except httpx.HTTPError as exc:
        return envelope(
            status="error",
            reason=f"{type(exc).__name__}: {exc}",
            format_name=format_name,
            source=url,
        )
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        handle.write(b"".join(chunks))
    finally:
        handle.close()
    return RemoteFile(path=Path(handle.name), url=url)
