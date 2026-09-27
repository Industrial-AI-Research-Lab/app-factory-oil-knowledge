"""Storage boundary contracts for CSV MCP

Covers result bucket allowlist, prefix validation, server-generated keys,
UTF-8 payload with ContentType, presigned HTTPS validation, provider
failure redaction, and delegation shape. Uses injected fake S3 only.
"""

from __future__ import annotations

import inspect
from typing import get_type_hints
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import csv_adapter.service as svc  # noqa: E402
import csv_adapter.storage as sto  # noqa: E402
from csv_adapter.config import (  # noqa: E402
    CSV_ALLOWED_S3_BUCKETS,
    E_LIMIT_DOWNLOAD,
    E_UPLOAD_FAILED,
    E_URL_FORBIDDEN,
    MAX_DOWNLOAD_BYTES,
    PRESIGNED_URL_TTL_SECONDS,
    S3_RESULT_BUCKET,
    S3_RESULT_PREFIX,
)
from csv_adapter.models import DownloadRequest  # noqa: E402

BUCKET = "result-bucket"
GOOD_URL = "https://files.invalid/results/out.csv"
CONTENT_TYPE = "text/csv; charset=utf-8"


class _FakeS3:
    """Fake S3 client capturing put and presign calls without AWS."""

    def __init__(self, url: str = GOOD_URL, put_exc=None, sign_exc=None):
        self.url = url
        self.put_exc = put_exc
        self.sign_exc = sign_exc
        self.put_calls: list = []
        self.sign_calls: list = []

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType})
        if self.put_exc is not None:
            raise self.put_exc
        return {}

    def generate_presigned_url(self, operation: str, Params: dict, ExpiresIn: int):
        self.sign_calls.append({"operation": operation, "Params": dict(Params), "ExpiresIn": ExpiresIn})
        if self.sign_exc is not None:
            raise self.sign_exc
        return self.url


def _env(bucket: str | None = BUCKET, prefix: str | None = "out", allow: str | None = BUCKET) -> dict:
    """Build deterministic environ mapping for storage tests."""
    env: dict = {}
    if bucket is not None:
        env[S3_RESULT_BUCKET] = bucket
    if prefix is not None:
        env[S3_RESULT_PREFIX] = prefix
    if allow is not None:
        env[CSV_ALLOWED_S3_BUCKETS] = allow
    return env


class _Hex:
    """Fixed hex holder for deterministic uuid4 stubbing."""

    def __init__(self, value: str):
        self.hex = value


def test_signature_is_injectable():
    sig = inspect.signature(sto.upload_normalized_csv)
    assert list(sig.parameters) == ["content", "environ", "s3_client"]
    assert sig.parameters["environ"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["s3_client"].kind is inspect.Parameter.KEYWORD_ONLY
    assert get_type_hints(sto.upload_normalized_csv)["return"] is str


def test_missing_bucket_and_allowlist_fail_closed():
    with pytest.raises(ValueError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv("a;b\n", environ=_env(bucket=None), s3_client=_FakeS3())
    with pytest.raises(ValueError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv("a;b\n", environ=_env(bucket="   "), s3_client=_FakeS3())
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        sto.upload_normalized_csv("a;b\n", environ=_env(allow=None), s3_client=_FakeS3())
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        sto.upload_normalized_csv("a;b\n", environ=_env(bucket="other-bucket"), s3_client=_FakeS3())


def test_bucket_must_match_allowlist_exactly():
    s3 = _FakeS3()
    url = sto.upload_normalized_csv("a;b\n", environ=_env(bucket=BUCKET, allow=f"{BUCKET},extra-bucket"), s3_client=s3)
    assert url == GOOD_URL and s3.put_calls[0]["Bucket"] == BUCKET
    with pytest.raises(ValueError, match=E_URL_FORBIDDEN):
        sto.upload_normalized_csv("a;b\n", environ=_env(bucket="Result-Bucket", allow=BUCKET), s3_client=_FakeS3())


@pytest.mark.parametrize("prefix", ["out", "a/b/c", "a.b_c-d", "  out  "])
def test_valid_prefixes_accepted(prefix: str, monkeypatch):
    monkeypatch.setattr(sto.uuid, "uuid4", lambda: _Hex("a" * 32))
    s3 = _FakeS3()
    sto.upload_normalized_csv("a;b\n", environ=_env(prefix=prefix), s3_client=s3)
    assert s3.put_calls[0]["Key"] == f"{prefix.strip()}/{'a' * 32}.csv"


@pytest.mark.parametrize("prefix", ["/lead", "trail/", "a//b", "a/./b", "a/../b", "a\\b", "a b", "a:b", ".", ".."])
def test_invalid_prefixes_rejected(prefix: str):
    with pytest.raises(ValueError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv("a;b\n", environ=_env(prefix=prefix), s3_client=_FakeS3())


@pytest.mark.parametrize("prefix", [123, True, "a\x00b"])
def test_non_string_prefix_rejected(prefix: object):
    with pytest.raises(ValueError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv("a;b\n", environ=_env(prefix=prefix), s3_client=_FakeS3())  # type: ignore[arg-type]


def test_default_prefix_when_unset_or_blank(monkeypatch):
    monkeypatch.setattr(sto.uuid, "uuid4", lambda: _Hex("b" * 32))
    # Unset omits the key; blank falls back to the default prefix.
    for env in (_env(prefix=None), _env(prefix="   ")):
        s3 = _FakeS3()
        sto.upload_normalized_csv("a;b\n", environ=env, s3_client=s3)
        assert s3.put_calls[0]["Key"] == f"normalized/{'b' * 32}.csv"


def test_server_generated_key_shape_and_uniqueness(monkeypatch):
    hexes = iter(["c" * 32, "d" * 32])
    monkeypatch.setattr(sto.uuid, "uuid4", lambda: _Hex(next(hexes)))
    first = _FakeS3()
    sto.upload_normalized_csv("a;b\n", environ=_env(prefix="out"), s3_client=first)
    second = _FakeS3()
    sto.upload_normalized_csv("a;b\n", environ=_env(prefix="out"), s3_client=second)
    key1, key2 = first.put_calls[0]["Key"], second.put_calls[0]["Key"]
    assert key1 == f"out/{'c' * 32}.csv" and key2 == f"out/{'d' * 32}.csv"
    assert key1 != key2 and "a;b" not in key1


def test_utf8_payload_and_content_type(monkeypatch):
    monkeypatch.setattr(sto.uuid, "uuid4", lambda: _Hex("e" * 32))
    s3 = _FakeS3()
    text = "activity_id;name\nA1;M\u00fcller-\u043b\u0438\u043d\u0438\u044f\n"
    sto.upload_normalized_csv(text, environ=_env(), s3_client=s3)
    call = s3.put_calls[0]
    assert call["Body"] == text.encode("utf-8") and call["Body"].decode("utf-8") == text
    assert call["ContentType"] == CONTENT_TYPE
    assert call["Bucket"] == BUCKET and call["Key"].endswith(".csv")


def test_presign_params_and_https_validation(monkeypatch):
    monkeypatch.setattr(sto.uuid, "uuid4", lambda: _Hex("f" * 32))
    s3 = _FakeS3()
    sto.upload_normalized_csv("a;b\n", environ=_env(), s3_client=s3)
    sign = s3.sign_calls[0]
    assert sign["operation"] == "get_object"
    assert sign["Params"] == {"Bucket": BUCKET, "Key": f"out/{'f' * 32}.csv"}
    assert sign["ExpiresIn"] == PRESIGNED_URL_TTL_SECONDS


@pytest.mark.parametrize("url", [
    "http://files.invalid/out.csv",
    "s3://result-bucket/out.csv",
    "https://files.invalid",
    "https://files.invalid/",
    "https://user:pass@files.invalid/out.csv",
    "https://files.invalid/out.csv#frag",
    "",
    "   ",
])
def test_invalid_presigned_url_rejected(url: str):
    with pytest.raises(RuntimeError, match=E_UPLOAD_FAILED) as exc:
        sto.upload_normalized_csv("a;b\n", environ=_env(), s3_client=_FakeS3(url=url))
    assert str(exc.value) == E_UPLOAD_FAILED and exc.value.__cause__ is None


@pytest.mark.parametrize("url", [None, 123, True, b"https://files.invalid/a.csv"])
def test_non_string_presigned_url_rejected(url: object):
    with pytest.raises(RuntimeError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv("a;b\n", environ=_env(), s3_client=_FakeS3(url=url))  # type: ignore[arg-type]


def test_provider_failures_redacted():
    canary = "CANARY-7c1e-provider"
    with pytest.raises(RuntimeError, match=E_UPLOAD_FAILED) as exc:
        sto.upload_normalized_csv("a;b\n", environ=_env(), s3_client=_FakeS3(put_exc=RuntimeError(f"boom {canary}")))
    assert str(exc.value) == E_UPLOAD_FAILED and canary not in str(exc.value)
    with pytest.raises(RuntimeError, match=E_UPLOAD_FAILED) as exc2:
        sto.upload_normalized_csv("a;b\n", environ=_env(), s3_client=_FakeS3(sign_exc=RuntimeError(f"boom {canary}")))
    assert str(exc2.value) == E_UPLOAD_FAILED and canary not in str(exc2.value)


def test_content_and_size_bounds():
    with pytest.raises(ValueError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv(None, environ=_env(), s3_client=_FakeS3())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=E_UPLOAD_FAILED):
        sto.upload_normalized_csv(True, environ=_env(), s3_client=_FakeS3())  # type: ignore[arg-type]
    big = "x" * (MAX_DOWNLOAD_BYTES + 1)
    with pytest.raises(ValueError, match=E_LIMIT_DOWNLOAD):
        sto.upload_normalized_csv(big, environ=_env(), s3_client=_FakeS3())


def test_download_normalize_upload_delegates_public_adapters(monkeypatch):
    """High-level chain delegates via public adapters without private coupling."""
    seen: dict = {}
    expected_url = "https://files.invalid/results/final.csv"

    def fake_download(request, *, environ=None, http_client=None, s3_client=None):
        assert isinstance(request, DownloadRequest)
        seen["download"] = (request.csv_url, environ, http_client, s3_client)
        from csv_adapter.download import DownloadedCsv as _Downloaded

        return _Downloaded(content="activity_id;activity_name;volume;measurement\nA1;N1;1;m\n", encoding="utf-8-sig")

    def fake_upload(content, *, environ=None, s3_client=None):
        seen["upload"] = (content, environ, s3_client)
        assert "activity_id" in content
        return expected_url

    monkeypatch.setattr(svc, "download_csv", fake_download)
    monkeypatch.setattr(svc, "upload_normalized_csv", fake_upload)
    env = _env()
    out = svc.csv_download_normalize_upload(DownloadRequest(csv_url="s3://result-bucket/in.csv"), environ=env, http_client="hc", s3_client="sc")  # type: ignore[arg-type]
    assert out.csv_url == expected_url and out.encoding == "utf-8-sig" and out.delimiter == ";"
    assert out.rows_count == 1 and list(out.columns) == ["activity_id", "activity_name", "volume", "measurement", "structure", "edges", "granular_unit"]
    assert seen["download"][1] is env and seen["download"][2] == "hc" and seen["download"][3] == "sc"
    assert seen["upload"][1] is env and seen["upload"][2] == "sc"


class _FakeS3Del(_FakeS3):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.deletes: list = []

    def delete_object(self, Bucket: str, Key: str):
        self.deletes.append((Bucket, Key))
        return {}


def test_orphan_cleaned_on_bad_presigned_url():
    from csv_adapter import storage as sto

    s3 = _FakeS3Del(url="http://bad/x.csv")
    with pytest.raises(RuntimeError, match="E_UPLOAD_FAILED"):
        sto.upload_normalized_csv("a;b\n1;2\n", environ=_env(), s3_client=s3)
    assert len(s3.put_calls) == 1
    assert s3.deletes == [(BUCKET, s3.put_calls[0]["Key"])]
