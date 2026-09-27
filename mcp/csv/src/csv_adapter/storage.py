"""S3 result storage for normalized CSV

Uploads canonical CSV text to the administrator-configured allowlisted
S3 bucket under a server-generated key and returns a validated HTTPS
presigned URL. No import-time I/O; all environment and provider access
happens at call time with dependency injection.
"""

import os
import re
import uuid
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.parse import urlsplit

from .config import (
    CSV_ALLOWED_S3_BUCKETS,
    E_LIMIT_DOWNLOAD,
    E_UPLOAD_FAILED,
    E_URL_FORBIDDEN,
    FETCH_TIMEOUT_SECONDS,
    MAX_DOWNLOAD_BYTES,
    PRESIGNED_URL_TTL_SECONDS,
    S3_RESULT_BUCKET,
    S3_RESULT_PREFIX,
)

__all__ = ("S3WriteClient", "upload_normalized_csv")


class S3WriteClient(Protocol):
    """Minimal S3 write surface: put, presigned GET URL, best-effort delete.

    Parameter names mirror boto3 exactly (CapWords per the AWS API);
    renaming them would break structural matching with the real client.
    """

    def put_object(self, *, Bucket: str, Key: str, Body: object, ContentType: str = "") -> object:
        """Store one object under Bucket/Key.

        Args:
            Bucket: Target bucket (already allowlisted by the caller).
            Key: Object key, `<prefix>/<uuid>.csv` shape.
            Body: Payload bytes.
            ContentType: MIME type, always text/csv here.

        Returns:
            Provider response, intentionally unused.
        """
        ...

    def generate_presigned_url(self, ClientMethod: str, Params: object = None, ExpiresIn: int = 3600) -> str:
        """Build a locally-signed GET URL without any network call.

        Args:
            ClientMethod: S3 method name, always `get_object` here.
            Params: Method parameters (`Bucket`, `Key`).
            ExpiresIn: URL lifetime in seconds.

        Returns:
            Presigned URL string (scheme validated by the caller).
        """
        ...

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        """Remove one object; failures are swallowed by the caller.

        Args:
            Bucket: Target bucket.
            Key: Object key to remove.

        Returns:
            Provider response, intentionally unused.
        """
        ...

_DEFAULT_PREFIX = "normalized"
_CONTENT_TYPE = "text/csv; charset=utf-8"
_PREFIX_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]+")


def _fail(code: str, hint: str) -> ValueError:
    """Build a safe ValueError without echoing secrets.

    Args:
        code: Stable error code.
        hint: Safe human-readable hint.

    Returns:
        ValueError with the canonical message shape.
    """
    return ValueError(f"{code}: {hint}")


def _sanitize_prefix(raw: object) -> str:
    """
    Validate an S3 prefix fail-closed, defaulting only when unset.

    Accepted prefix rules (anything else fails): no leading/trailing slash,
    no backslashes, no control characters, no empty/`.`/`..` segments, and
    only `[A-Za-z0-9._-]` characters per segment. This blocks absolute keys
    and path traversal outside the result bucket.

    Args:
        raw: Raw prefix value from the environment; anything but a string
            fails, except None/blank which select the default.

    Returns:
        Stripped valid prefix, or `normalized` when unset or blank.

    Raises:
        ValueError: E_UPLOAD_FAILED for any configured invalid prefix.
    """
    if raw is None:
        return _DEFAULT_PREFIX
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise _fail(E_UPLOAD_FAILED, "invalid prefix") from None
    text = raw.strip()
    if not text:
        return _DEFAULT_PREFIX
    if text.startswith("/") or text.endswith("/"):
        raise _fail(E_UPLOAD_FAILED, "invalid prefix") from None
    if "\\" in text:
        raise _fail(E_UPLOAD_FAILED, "invalid prefix") from None
    for char in text:
        if ord(char) < 32 or ord(char) == 127:
            raise _fail(E_UPLOAD_FAILED, "invalid prefix") from None
    for segment in text.split("/"):
        if not segment or segment in (".", ".."):
            raise _fail(E_UPLOAD_FAILED, "invalid prefix") from None
        if _PREFIX_SEGMENT_RE.fullmatch(segment) is None:
            raise _fail(E_UPLOAD_FAILED, "invalid prefix") from None
    return text


def _parse_allowlist(raw: object) -> frozenset[str]:
    """
    Parse a comma-separated bucket allowlist.

    Args:
        raw: Raw allowlist value from the environment; anything but a
            string (including a missing variable) means an empty set.

    Returns:
        Immutable set of non-empty bucket names. Names keep their case:
        S3 bucket names are case-sensitive. Empty result denies everything.
    """
    if not isinstance(raw, str):
        return frozenset()
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


def _require_https_url(raw: object) -> str:
    """Validate a provider URL lexically without network access.

    Accepted shape: `https://` scheme (plain `http` would leak the signed
    query, which is a bearer credential), no credentials, no fragment,
    non-empty host and a real object path. Anything else fails closed:
    a non-conforming provider URL never leaves the service.

    Args:
        raw: Candidate URL returned by the provider (must be a string).

    Returns:
        Stripped URL when it is https without credentials or fragment.

    Raises:
        RuntimeError: E_UPLOAD_FAILED for any invalid shape.
    """
    try:
        if isinstance(raw, bool) or not isinstance(raw, str):
            raise RuntimeError(E_UPLOAD_FAILED)
        text = raw.strip()
        if not text:
            raise RuntimeError(E_UPLOAD_FAILED)
        parts = urlsplit(text)
        if parts.scheme != "https":
            raise RuntimeError(E_UPLOAD_FAILED)
        if not text.startswith("https://"):
            raise RuntimeError(E_UPLOAD_FAILED)
        if parts.username or parts.password or parts.fragment:
            raise RuntimeError(E_UPLOAD_FAILED)
        if not parts.hostname or not parts.path or parts.path == "/":
            raise RuntimeError(E_UPLOAD_FAILED)
        return text
    except RuntimeError:
        raise RuntimeError(E_UPLOAD_FAILED) from None
    except Exception:
        raise RuntimeError(E_UPLOAD_FAILED) from None


def upload_normalized_csv(
    content: str,
    *,
    environ: Mapping[str, str] | None = None,
    s3_client: S3WriteClient | None = None,
) -> str:
    """Upload normalized CSV text to S3 and return a presigned URL.

    Args:
        content: Canonical CSV text to store.
        environ: Environment mapping override for testing.
        s3_client: Injected S3 client for testing.

    Returns:
        Validated HTTPS presigned URL for the stored object.

    Raises:
        ValueError: Safe E_* failure for invalid content or config.
        RuntimeError: E_UPLOAD_FAILED for provider or URL failures.
    """
    if isinstance(content, bool) or not isinstance(content, str):
        raise _fail(E_UPLOAD_FAILED, "invalid content") from None
    try:
        payload = content.encode("utf-8")
    except Exception:
        raise _fail(E_UPLOAD_FAILED, "invalid content") from None
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise _fail(E_LIMIT_DOWNLOAD, "payload too large") from None
    env: Mapping[str, str] = environ if environ is not None else os.environ
    try:
        bucket_raw = env.get(S3_RESULT_BUCKET)
        prefix_raw = env.get(S3_RESULT_PREFIX)
        allow_raw = env.get(CSV_ALLOWED_S3_BUCKETS)
    except Exception:
        raise RuntimeError(E_UPLOAD_FAILED) from None
    if not isinstance(bucket_raw, str) or not bucket_raw.strip():
        raise _fail(E_UPLOAD_FAILED, "missing result bucket") from None
    bucket = bucket_raw.strip()
    prefix = _sanitize_prefix(prefix_raw)
    allowlist = _parse_allowlist(allow_raw)
    if bucket not in allowlist:
        raise _fail(E_URL_FORBIDDEN, "forbidden bucket") from None
    try:
        suffix = uuid.uuid4().hex
    except Exception:
        raise RuntimeError(E_UPLOAD_FAILED) from None
    key = f"{prefix}/{suffix}.csv"
    if s3_client is not None:
        client: Any = s3_client
    else:
        try:
            import boto3  # lazy import, no import-time dependency
            from botocore.config import Config

            client = boto3.client(
                "s3",
                config=Config(
                    connect_timeout=FETCH_TIMEOUT_SECONDS,
                    read_timeout=FETCH_TIMEOUT_SECONDS,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        except Exception:
            raise RuntimeError(E_UPLOAD_FAILED) from None
    try:
        client.put_object(
            Bucket=bucket, Key=key, Body=payload, ContentType=_CONTENT_TYPE
        )
    except Exception:
        raise RuntimeError(E_UPLOAD_FAILED) from None
    try:
        url = client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
        )
        return _require_https_url(url)
    except Exception:
        try:
            client.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        raise RuntimeError(E_UPLOAD_FAILED) from None
