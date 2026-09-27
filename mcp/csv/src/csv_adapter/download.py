"""
Bounded HTTPS/S3 CSV download with SSRF guards and encoding fallback.

SSRF policy: https/s3 only, allowlisted hosts/buckets, DNS resolved just
before use, non-global IPs rejected, every redirect revalidated, no proxies.
"""

import ipaddress
import http.client
import os
import socket
import ssl
import time
from collections.abc import Mapping
from typing import Literal, Protocol
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict

from .config import CSV_ALLOWED_HOSTS, CSV_ALLOWED_S3_BUCKETS, E_DOWNLOAD_FAILED, E_ENCODING_UNSUPPORTED, E_INTERNAL, \
    E_LIMIT_DOWNLOAD, E_URL_FORBIDDEN, E_URL_INVALID, FETCH_TIMEOUT_SECONDS, MAX_DOWNLOAD_BYTES, MAX_REDIRECTS
from stairs_csv.backend_file import decode_csv_bytes
from .models import DownloadRequest
from .errors import CsvDomainError, domain_error

__all__ = ("DownloadedCsv", "HttpClient", "S3ReadClient", "download_csv")
# HTTP-статусы, за которыми идем вручную (300 — выбор, 304 — кеш, 305 — мертв)
_REDIRECTS = frozenset({301, 302, 303, 307, 308})


class DownloadedCsv(BaseModel):
    """Decoded CSV payload with exact encoding label."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    content: str
    encoding: Literal["utf-8-sig", "cp1251"]


def _fail(code: str, hint: str) -> CsvDomainError:
    """Build safe error without URL, body, or exception text."""
    return domain_error(code, hint)


class HttpClient(Protocol):
    """Client contract; implementations must honor every security option."""

    def get(self, url: str, *, timeout: float, stream: bool,
            allow_redirects: bool, resolved_ip: str, tls_hostname: str,
            host_header: str) -> object:
        """GET once from the pre-resolved address without re-resolving."""
        ...


class S3ReadClient(Protocol):
    """Minimal S3 read surface: bounded head/get; nothing else is used."""

    def head_object(self, *, Bucket: str, Key: str) -> object: ...
    def get_object(self, *, Bucket: str, Key: str) -> object: ...


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a pre-validated IP with SNI of the hostname."""

    def __init__(self, host: str, ip: str, port: int, timeout: float):
        """Store the validated IP; TLS hostname stays the original host."""
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self) -> None:
        """Connect the TCP socket to the pinned IP, then wrap with TLS."""
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class _NativeResponse:
    """Minimal response surface (status/headers/chunks/close) over http.client."""

    def __init__(self, connection: _PinnedHTTPSConnection, response: http.client.HTTPResponse):
        """Bind a connection/response pair for joint cleanup."""
        self._connection = connection
        self._response = response
        self.status_code = response.status
        self.headers = dict(response.getheaders())

    def iter_content(self, size: int):
        """Yield body chunks of at most size bytes."""
        while chunk := self._response.read(size):
            yield chunk

    def close(self) -> None:
        """Close the response and its connection, best effort by caller."""
        self._response.close()
        self._connection.close()


class _PinnedHttpClient:
    """Direct HTTPS transport. It intentionally does not consult proxy env."""

    def get(self, url: str, *, timeout: float, stream: bool,
            allow_redirects: bool, resolved_ip: str, tls_hostname: str,
            host_header: str) -> object:
        """GET over the pinned connection; redirects handled by the caller."""
        parts = urlsplit(url)
        port = parts.port or 443
        target = parts.path + (("?" + parts.query) if parts.query else "")
        connection = _PinnedHTTPSConnection(tls_hostname, resolved_ip, port, timeout)
        connection.request("GET", target, headers={"Host": host_header, "Accept-Encoding": "identity"})
        return _NativeResponse(connection, connection.getresponse())


def _allowlist(raw: object, *, lower: bool = True) -> frozenset[str]:
    """Split comma allowlist deterministically."""
    if not isinstance(raw, str): return frozenset()
    return frozenset(p.strip().lower() if lower else p.strip() for p in raw.split(",") if p.strip())


def _resolve(hostname: str) -> list[str]:
    """Resolve hostname immediately before request."""
    import socket
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except Exception:
        raise _fail(E_DOWNLOAD_FAILED, "dns resolution failed") from None
    out = [str(i[4][0]) for i in infos if len(i) > 4 and len(i[4]) > 0]
    if not out: raise _fail(E_DOWNLOAD_FAILED, "dns resolution failed") from None
    return out


def _reject(addresses: list[str]) -> None:
    """Reject non-global/private/loopback/link-local/multicast/reserved/unspecified IPs."""
    for raw in addresses:
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise _fail(E_URL_FORBIDDEN, "forbidden address") from None
        if not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise _fail(E_URL_FORBIDDEN, "forbidden address") from None


def _header(headers: object, name: str) -> str | None:
    """Case-insensitive header lookup."""
    if not isinstance(headers, Mapping): return None
    try:
        want = name.lower()
        for k, v in headers.items():
            if isinstance(k, str) and k.lower() == want: return None if v is None else str(v)
    except Exception:
        return None
    return None


def _push(out: bytearray, chunk: object) -> None:
    """Append one chunk with hard bound."""
    if not chunk: return
    if isinstance(chunk, str): chunk = chunk.encode("utf-8")
    if not isinstance(chunk, (bytes, bytearray)): raise _fail(E_DOWNLOAD_FAILED, "download failed")
    out.extend(chunk)
    if len(out) > MAX_DOWNLOAD_BYTES: raise _fail(E_LIMIT_DOWNLOAD, "download too large")


def _drain(src: object, deadline: float) -> bytes:
    """
    Drain a response body into bounded bytes.

    Accepts requests-style (iter_content), urllib3-style (iter_chunks),
    or plain read() bodies; the first supported interface wins. Enforces
    MAX_DOWNLOAD_BYTES and the monotonic deadline on every chunk.

    Args:
        src: Response-like object with one of the supported read interfaces.
        deadline: time.monotonic() timestamp; expiry raises timed-out error.

    Returns:
        The full body as immutable bytes.

    Raises:
        CsvDomainError: Limit, timeout, or downstream domain failure, unchanged.
        CsvDomainError: E_DOWNLOAD_FAILED for unreadable bodies and transport errors.
    """
    out = bytearray()
    for attr in ("iter_content", "iter_chunks"):
        fn = getattr(src, attr, None)
        if callable(fn):
            try:
                it = fn(65536) # 64 KiB per chunk: throughput vs prompt limit/deadline checks
                for chunk in it:
                    if time.monotonic() >= deadline: raise _fail(E_DOWNLOAD_FAILED, "download timed out")
                    _push(out, chunk)
            except Exception as exc:
                if isinstance(exc, CsvDomainError): raise
                raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
            return bytes(out)
    rd = getattr(src, "read", None)
    if callable(rd):
        try:
            for chunk in iter(lambda: rd(65536), b""): # read chunk-wise until empty
                if time.monotonic() >= deadline: raise _fail(E_DOWNLOAD_FAILED, "download timed out")
                _push(out, chunk)
        except Exception as exc:
            if isinstance(exc, CsvDomainError): raise
            raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
        return bytes(out)
    raise _fail(E_DOWNLOAD_FAILED, "download failed")


def _get(url: str, client: HttpClient, ip: str, hostname: str, deadline: float) -> object:
    """GET once without redirects."""
    remaining = deadline - time.monotonic()
    if remaining <= 0: raise _fail(E_DOWNLOAD_FAILED, "download timed out")
    parts = urlsplit(url)
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    host_header = rendered_host if parts.port in (None, 443) else f"{rendered_host}:{parts.port}"
    try:
        return client.get(url, timeout=remaining, stream=True, allow_redirects=False,
                          resolved_ip=ip, tls_hostname=hostname, host_header=host_header)
    except Exception as exc:
        if isinstance(exc, CsvDomainError): raise
        raise _fail(E_DOWNLOAD_FAILED, "download failed") from None


def _check_https(url: str, allowed: frozenset[str]) -> tuple[str, str]:
    """
    Validate target, allowlist, and resolved IPs.

    Returns:
        (hostname, ip): lowercased host for TLS SNI and the first validated global IP to connect to.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        raise _fail(E_URL_INVALID, "invalid URL") from None
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
        if parts.scheme != "https":
            raise _fail(E_URL_INVALID, "unsupported URL scheme")
        if parts.username or parts.password or parts.fragment:
            raise _fail(E_URL_INVALID, "URL must not include credentials or fragment")
    host = parts.hostname
    if not host or not parts.path or parts.path == "/": raise _fail(E_URL_INVALID, "URL must include host and path")
    low = host.lower()
    if low not in allowed: raise _fail(E_URL_FORBIDDEN, "host not allowlisted")
    try:
        literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = ipaddress.ip_address(low.strip("[]"))
    except ValueError:
        literal = None
    # Pinned per request: each hop is re-resolved, validated, then pinned;
    # no cross-request DNS cache is kept.
    addresses = [str(literal)] if literal is not None else _resolve(low)
    _reject(addresses)
    return low, addresses[0]


def _fetch_https(url: str, allowed: frozenset[str], client: HttpClient | None) -> bytes:
    """Fetch HTTPS bytes with manual redirect revalidation."""
    try:
        origin = (urlsplit(url).hostname or "").lower()
    except ValueError:
        raise _fail(E_URL_INVALID, "invalid URL") from None
    current = url
    deadline = time.monotonic() + FETCH_TIMEOUT_SECONDS
    transport = client or _PinnedHttpClient()
    for attempt in range(MAX_REDIRECTS + 1):
        hostname, ip = _check_https(current, allowed)
        resp = _get(current, transport, ip, hostname, deadline)
        try:
            if getattr(resp, "status_code", None) in _REDIRECTS:
                if attempt >= MAX_REDIRECTS: raise _fail(E_DOWNLOAD_FAILED, "too many redirects")
                loc = _header(getattr(resp, "headers", {}), "location")
                if not loc or not loc.strip(): raise _fail(E_URL_INVALID, "invalid redirect")
                nxt = urljoin(current, loc.strip())
                try:
                    rparts = urlsplit(nxt)
                except ValueError:
                    raise _fail(E_URL_INVALID, "invalid redirect") from None
                if rparts.scheme != "https" or not rparts.hostname or not rparts.path or rparts.path == "/": raise _fail(
                    E_URL_INVALID, "invalid redirect")
                new_host = rparts.hostname.lower()
                if new_host != origin and new_host not in allowed: raise _fail(E_URL_FORBIDDEN, "host not allowlisted")
                current = nxt
                continue
            if getattr(resp, "status_code", None) != 200: raise _fail(E_DOWNLOAD_FAILED, "download failed")
            declared = _header(getattr(resp, "headers", {}), "content-length")
            if declared is not None:
                try:
                    size: int = int(declared.strip())
                except (ValueError, AttributeError):
                    raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
                if size < 0: raise _fail(E_DOWNLOAD_FAILED, "download failed")
                if size > MAX_DOWNLOAD_BYTES: raise _fail(E_LIMIT_DOWNLOAD, "download too large")
            return _drain(resp, deadline)
        finally:
            try:
                fn = getattr(resp, "close", None); fn() if callable(fn) else None
            except Exception:
                pass
    raise _fail(E_DOWNLOAD_FAILED, "too many redirects")


def _split_s3(url: str) -> tuple[str, str]:
    """Split s3 URL into bucket and key."""
    try:
        parts = urlsplit(url)
    except ValueError:
        raise _fail(E_URL_INVALID, "invalid URL") from None
    if parts.scheme != "s3" or parts.username or parts.password or parts.fragment:
        raise _fail(E_URL_INVALID, "URL must include bucket and key") if parts.scheme == "s3" else _fail(E_URL_INVALID,
                                                                                                         "unsupported URL scheme")
    bucket, key = parts.netloc, parts.path.lstrip("/")
    if not bucket or not key or not key.strip("/"): raise _fail(E_URL_INVALID, "URL must include bucket and key")
    return bucket, key


def _fetch_s3(url: str, allowed: frozenset[str], s3: S3ReadClient | None) -> bytes:
    """Fetch S3 bytes with allowlist and bounded head/get reads."""
    bucket, key = _split_s3(url)
    if bucket not in allowed: raise _fail(E_URL_FORBIDDEN, "bucket not allowlisted")
    client: object = s3
    if client is None:
        try:
            import boto3
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
            raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
    head_fn = getattr(client, "head_object", None)
    if callable(head_fn):
        try:
            head = head_fn(Bucket=bucket, Key=key)
        except Exception as exc:
            if isinstance(exc, CsvDomainError): raise
            raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
        if isinstance(head, Mapping):
            raw_len: object = head.get("ContentLength", head.get("Content-Length"))
            if raw_len is not None:
                try:
                    size: int = int(str(raw_len).strip())
                except (ValueError, AttributeError):
                    raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
                if size < 0: raise _fail(E_DOWNLOAD_FAILED, "download failed")
                if size > MAX_DOWNLOAD_BYTES: raise _fail(E_LIMIT_DOWNLOAD, "download too large")
    get_fn = getattr(client, "get_object", None)
    if not callable(get_fn): raise _fail(E_DOWNLOAD_FAILED, "download failed")
    try:
        obj = get_fn(Bucket=bucket, Key=key)
    except Exception as exc:
        if isinstance(exc, CsvDomainError): raise
        raise _fail(E_DOWNLOAD_FAILED, "download failed") from None
    body = obj.get("Body", obj.get("body")) if isinstance(obj, Mapping) else (
                getattr(obj, "Body", None) or getattr(obj, "body", None))
    if body is None: raise _fail(E_DOWNLOAD_FAILED, "download failed")
    try:
        return _drain(body, time.monotonic() + FETCH_TIMEOUT_SECONDS)
    finally:
        try:
            fn = getattr(body, "close", None); fn() if callable(fn) else None
        except Exception:
            pass


def _decode(raw: bytes) -> tuple[str, str]:
    """
    Decode bytes using the vendored backend fallback order.

    Returns:
        (text, encoding): decoded text and the winning encoding label.
    """
    try:
        return decode_csv_bytes(raw)
    except UnicodeError:
        raise _fail(E_ENCODING_UNSUPPORTED, "unsupported encoding") from None


def download_csv(request: DownloadRequest, *, environ: Mapping[str, str] | None = None,
                 http_client: HttpClient | None = None, s3_client: S3ReadClient | None = None) -> DownloadedCsv:
    """Download one CSV over HTTPS or S3 with bounds and safe errors."""
    try:
        if not isinstance(request, DownloadRequest): raise _fail(E_URL_INVALID, "invalid URL")
        env: Mapping[str, str] = environ if environ is not None else os.environ
        try:
            scheme = urlsplit(request.csv_url).scheme
        except ValueError:
            raise _fail(E_URL_INVALID, "invalid URL") from None
        if scheme == "https":
            if not (allowed_hosts := _allowlist(env.get(CSV_ALLOWED_HOSTS) if isinstance(env, Mapping) else None,
                                                lower=True)): raise _fail(E_URL_FORBIDDEN, "host not allowlisted")
            raw = _fetch_https(request.csv_url, allowed_hosts, http_client)
        elif scheme == "s3":
            if not (allowed_buckets := _allowlist(env.get(CSV_ALLOWED_S3_BUCKETS) if isinstance(env, Mapping) else None,
                                                  lower=False)): raise _fail(E_URL_FORBIDDEN, "bucket not allowlisted")
            raw = _fetch_s3(request.csv_url, allowed_buckets, s3_client)
        else:
            raise _fail(E_URL_INVALID, "unsupported URL scheme")
        if not raw: raise _fail(E_DOWNLOAD_FAILED, "download failed")
        text, encoding = _decode(raw)
        try:
            return DownloadedCsv(content=text, encoding=encoding)  # type: ignore[arg-type]
        except Exception:
            raise RuntimeError(E_INTERNAL) from None
    except ValueError as exc:
        if isinstance(exc, CsvDomainError): raise
        raise RuntimeError(E_INTERNAL) from None
    except RuntimeError as exc:
        if str(exc) == E_INTERNAL: raise
        raise RuntimeError(E_INTERNAL) from None
    except Exception:
        raise RuntimeError(E_INTERNAL) from None
