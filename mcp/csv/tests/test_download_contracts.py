"""Download contracts for CSV MCP

Covers DownloadRequest lexical policy, HTTPS/S3 allowlist fail-closed
behavior, bounds, exact encoding fallback order, empty payload, and safe
E_* errors. Uses injected fake clients and monkeypatch only; no real
DNS, network, or AWS calls.
"""

import inspect
from typing import get_type_hints
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import csv_adapter.download as dl  # noqa: E402
from csv_adapter.config import (  # noqa: E402
    CSV_ALLOWED_HOSTS,
    CSV_ALLOWED_S3_BUCKETS,
    CSV_ENCODINGS,
    E_DOWNLOAD_FAILED,
    E_LIMIT_DOWNLOAD,
    E_URL_FORBIDDEN,
    E_URL_INVALID,
    FETCH_TIMEOUT_SECONDS,
    MAX_DOWNLOAD_BYTES,
)
from csv_adapter.download import DownloadedCsv  # noqa: E402
from csv_adapter.models import DownloadRequest  # noqa: E402

GOOD_HOST = "allowed.invalid"
OTHER_HOST = "other.invalid"
# Documentation IP used only for allowlist stub validation; never contacted.
STUB_GLOBAL_IP = "8.8.8.8"


def _env(hosts: str | None = GOOD_HOST, buckets: str | None = "result-bucket") -> dict:
    """Build deterministic environ mapping for download tests."""
    env: dict = {}
    if hosts is not None:
        env[CSV_ALLOWED_HOSTS] = hosts
    if buckets is not None:
        env[CSV_ALLOWED_S3_BUCKETS] = buckets
    return env


class _Resp:
    """Fake HTTPS response with controllable status, headers, and body."""

    def __init__(self, body: bytes = b"a;b\n", status: int = 200, headers: dict | None = None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._chunks = chunks
        self.closed = False

    def iter_content(self, size: int = 65536):
        if self._chunks is not None:
            yield from self._chunks
            return
        yield self._body

    def close(self) -> None:
        self.closed = True


class _Http:
    """Fake HTTPS client recording GET calls without network."""

    def __init__(self, resp: _Resp | None = None, exc: Exception | None = None):
        self.resp = resp or _Resp()
        self.exc = exc
        self.calls: list = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.exc is not None:
            raise self.exc
        return self.resp


class _Body:
    """Fake S3 streaming body supporting iter_content/read/close."""

    def __init__(self, data: bytes):
        self._data = data

    def iter_content(self, size: int = 65536):
        yield self._data

    def read(self, size: int = 65536) -> bytes:
        data, self._data = self._data, b""
        return data

    def close(self) -> None:
        pass


class _S3Down:
    """Fake S3 client for download with bounded head/get reads."""

    def __init__(self, data: bytes = b"a;b\n", length=None, head_exc=None, get_exc=None):
        self._data = data
        self._length = length
        self._head_exc = head_exc
        self._get_exc = get_exc
        self.head_calls: list = []
        self.get_calls: list = []

    def head_object(self, Bucket: str, Key: str):
        self.head_calls.append((Bucket, Key))
        if self._head_exc is not None:
            raise self._head_exc
        if self._length is not None:
            return {"ContentLength": self._length}
        return {}

    def get_object(self, Bucket: str, Key: str):
        self.get_calls.append((Bucket, Key))
        if self._get_exc is not None:
            raise self._get_exc
        return {"Body": _Body(self._data)}


def _stub_dns(monkeypatch, address: str = STUB_GLOBAL_IP) -> None:
    """Stub DNS resolution so no real lookup happens."""
    monkeypatch.setattr(dl, "_resolve", lambda host: [address])


@pytest.mark.parametrize("url", [
    "https://allowed.invalid/data/file.csv",
    "https://allowed.invalid:443/a/b?x=1",
    "s3://result-bucket/path/to/file.csv",
])
def test_download_request_accepts_https_and_s3(url: str):
    assert DownloadRequest(csv_url=url).csv_url == url.strip()


@pytest.mark.parametrize("url", [
    "http://allowed.invalid/file.csv",
    "file:///etc/passwd",
    "ftp://allowed.invalid/file.csv",
    "https://user:pass@allowed.invalid/file.csv",
    "https://allowed.invalid/file.csv#frag",
    "s3://bucket/key#frag",
    "https://allowed.invalid",
    "https://allowed.invalid/",
    "s3://bucket",
    "s3://bucket/",
    "",
    "   ",
])
def test_download_request_rejects_unsafe_shapes(url: object):
    with pytest.raises(ValidationError):
        DownloadRequest(csv_url=url)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [None, 123, True, b"https://allowed.invalid/a"])
def test_download_request_rejects_non_string(bad: object):
    with pytest.raises(ValidationError):
        DownloadRequest(csv_url=bad)  # type: ignore[arg-type]


def test_https_happy_path_uses_fake_client_only(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Http(_Resp(body=b"a;b\n"))
    out = dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/data.csv"), environ=_env(), http_client=client)
    assert isinstance(out, DownloadedCsv) and out.content == "a;b\n" and out.encoding == "utf-8-sig"
    assert len(client.calls) == 1 and client.calls[0][0] == f"https://{GOOD_HOST}/data.csv"
    assert 0 < client.calls[0][1]["timeout"] <= FETCH_TIMEOUT_SECONDS
    assert client.calls[0][1]["allow_redirects"] is False


def test_https_fail_closed_without_allowlist(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Http()
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN) as exc:
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/a.csv"), environ={}, http_client=client)
    assert client.calls == [] and E_URL_FORBIDDEN in str(exc.value)


def test_https_rejects_non_allowlisted_host(monkeypatch):
    _stub_dns(monkeypatch)
    client = _Http()
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        dl.download_csv(DownloadRequest(csv_url=f"https://{OTHER_HOST}/a.csv"), environ=_env(), http_client=client)
    assert client.calls == []


def test_https_rejects_private_resolved_address(monkeypatch):
    _stub_dns(monkeypatch, "10.0.0.5")
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/a.csv"), environ=_env(), http_client=_Http())


def test_https_rejects_literal_loopback_without_dns(monkeypatch):
    _stub_dns(monkeypatch)
    env = _env(hosts="127.0.0.1")
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        dl.download_csv(DownloadRequest(csv_url="https://127.0.0.1/data.csv"), environ=env, http_client=_Http())


def test_https_redirect_to_forbidden_host_rejected(monkeypatch):
    _stub_dns(monkeypatch)
    first = _Resp(body=b"", status=302, headers={"Location": f"https://{OTHER_HOST}/evil.csv"})
    client = _Http(first)
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/start.csv"), environ=_env(), http_client=client)


def test_s3_happy_path_and_bucket_allowlist():
    s3 = _S3Down(data=b"a;b\n")
    out = dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(), s3_client=s3)
    assert out.content == "a;b\n" and s3.get_calls == [("result-bucket", "a.csv")]
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        dl.download_csv(DownloadRequest(csv_url="s3://other-bucket/a.csv"), environ=_env(), s3_client=_S3Down())
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(hosts=GOOD_HOST, buckets=None), s3_client=_S3Down())


def test_content_length_bound_enforced_before_body(monkeypatch):
    _stub_dns(monkeypatch)
    big = _Resp(body=b"x", headers={"Content-Length": str(MAX_DOWNLOAD_BYTES + 1)})
    with pytest.raises(ValueError, match=E_LIMIT_DOWNLOAD):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/big.csv"), environ=_env(), http_client=_Http(big))


def test_chunked_body_bound_enforced(monkeypatch):
    _stub_dns(monkeypatch)
    chunk = b"x" * 65536
    count = MAX_DOWNLOAD_BYTES // len(chunk) + 2
    resp = _Resp(chunks=[chunk] * count)
    with pytest.raises(ValueError, match=E_LIMIT_DOWNLOAD):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/big.csv"), environ=_env(), http_client=_Http(resp))


def test_s3_length_bound_and_empty_payload():
    with pytest.raises(ValueError, match=E_LIMIT_DOWNLOAD):
        dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(), s3_client=_S3Down(length=MAX_DOWNLOAD_BYTES + 1))
    with pytest.raises(ValueError, match=E_DOWNLOAD_FAILED):
        dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(), s3_client=_S3Down(data=b""))


def test_encoding_fallback_order_is_exact():
    assert CSV_ENCODINGS == ("utf-8-sig", "cp1251")
    s3 = _S3Down(data="a;b\n".encode("utf-8"))
    assert dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(), s3_client=s3).encoding == "utf-8-sig"
    # Lone 0xE9 is invalid UTF-8 but valid cp1251, so the second encoding wins.
    s3 = _S3Down(data=b"a;\xe9\n")
    out = dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(), s3_client=s3)
    assert out.encoding == "cp1251" and out.content == "a;\u0439\n"
    # cp1251 is total over bytes in CPython, so every non-UTF-8 byte falls
    # through to it (E_ENCODING_UNSUPPORTED via raw bytes is unreachable by
    # construction; the branch stays as defense in depth).
    s3 = _S3Down(data=b"\x81")
    out = dl.download_csv(DownloadRequest(csv_url="s3://result-bucket/a.csv"), environ=_env(), s3_client=s3)
    assert out.encoding == "cp1251" and out.content == "Ѓ"


def test_safe_errors_hide_url_canary_and_body(monkeypatch):
    _stub_dns(monkeypatch)
    canary = "CANARY-9f3a-body"
    client = _Http(exc=RuntimeError(f"boom {canary}"))
    url = f"https://{GOOD_HOST}/path-{canary}.csv"
    with pytest.raises(ValueError) as exc:
        dl.download_csv(DownloadRequest(csv_url=url), environ=_env(), http_client=client)
    assert str(exc.value).split(":")[0] in (E_DOWNLOAD_FAILED, E_URL_FORBIDDEN, E_URL_INVALID)
    assert canary not in str(exc.value) and url not in str(exc.value)
    assert exc.value.__cause__ is None


def test_download_csv_signature_and_result_model():
    sig = inspect.signature(dl.download_csv)
    assert list(sig.parameters) == ["request", "environ", "http_client", "s3_client"]
    assert sig.parameters["environ"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(dl.download_csv)["return"] is DownloadedCsv
    assert set(DownloadedCsv.model_fields) == {"content", "encoding"}
    assert DownloadedCsv.model_config.get("frozen") is True


def test_https_too_many_redirects_failed(monkeypatch):
    _stub_dns(monkeypatch)
    loop = _Resp(body=b"", status=302, headers={"Location": f"https://{GOOD_HOST}/loop.csv"})
    with pytest.raises(ValueError, match=E_DOWNLOAD_FAILED) as exc:
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/start.csv"), environ=_env(), http_client=_Http(loop))
    assert "too many redirects" in str(exc.value)


@pytest.mark.parametrize("location,code", [
    ("http://allowed.invalid/b.csv", E_URL_INVALID),
    ("  ", E_URL_INVALID),
    ("https://allowed.invalid/", E_URL_INVALID),
])
def test_https_bad_redirect_targets(monkeypatch, location, code):
    _stub_dns(monkeypatch)
    resp = _Resp(body=b"", status=302, headers={"Location": location})
    with pytest.raises(ValueError, match=code):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/start.csv"), environ=_env(), http_client=_Http(resp))


def test_https_304_is_not_redirect(monkeypatch):
    _stub_dns(monkeypatch)
    with pytest.raises(ValueError, match=E_DOWNLOAD_FAILED):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/a.csv"), environ=_env(), http_client=_Http(_Resp(status=304)))


def test_https_content_length_lie_caught_by_drain(monkeypatch):
    _stub_dns(monkeypatch)
    lying = _Resp(body=b"z" * (MAX_DOWNLOAD_BYTES + 1), headers={"Content-Length": "10"})
    with pytest.raises(ValueError, match=E_LIMIT_DOWNLOAD):
        dl.download_csv(DownloadRequest(csv_url=f"https://{GOOD_HOST}/a.csv"), environ=_env(), http_client=_Http(lying))
