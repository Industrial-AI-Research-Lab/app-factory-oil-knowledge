import hashlib
import json

import httpx
import pytest
from app.http_io import (
    ToolFailure,
    download_bytes,
    temporary_call_directory,
)


class ReadForbiddenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("response body must not be read")
        yield b""


@pytest.mark.asyncio
async def test_download_rejects_body_above_limit(httpx_mock):
    httpx_mock.add_response(content=b"12345")

    with pytest.raises(ToolFailure, match="SOURCE_TOO_LARGE"):
        await download_bytes(
            "https://objects.test/in?X-Amz-Signature=secret",
            max_bytes=4,
        )


@pytest.mark.asyncio
async def test_download_rejects_encoded_body_before_reading_it(httpx_mock):
    httpx_mock.add_response(
        headers={"Content-Encoding": "gzip"},
        stream=ReadForbiddenStream(),
    )

    with pytest.raises(ToolFailure, match="SOURCE_ENCODING_UNSUPPORTED"):
        await download_bytes(
            "https://objects.test/in?X-Amz-Signature=secret",
            max_bytes=10,
        )


@pytest.mark.asyncio
async def test_download_rejects_checksum_mismatch(httpx_mock):
    httpx_mock.add_response(content=b"payload")

    with pytest.raises(ToolFailure, match="SOURCE_CHECKSUM_MISMATCH"):
        await download_bytes(
            "https://objects.test/in?X-Amz-Signature=secret",
            max_bytes=7,
            expected_sha256="0" * 64,
        )


@pytest.mark.asyncio
async def test_download_rejects_plain_http_for_remote_hosts(httpx_mock):
    with pytest.raises(ToolFailure, match="URL_SCHEME_INVALID"):
        await download_bytes(
            "http://objects.test/in?X-Amz-Signature=secret",
            max_bytes=10,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1/private",
        "https://127.1/private",
        "https://2130706433/private",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/private",
        "https://[::ffff:127.0.0.1]/private",
        "https://unrelated.test/object",
        "https://objects.test.evil/object",
        "https://objects.test.:443/object",
        "https://objects.test:8443/object",
    ],
)
async def test_download_rejects_destination_outside_storage_allowlist(
    httpx_mock,
    monkeypatch,
    url,
):
    monkeypatch.setenv("OBJECT_STORAGE_ALLOWED_ORIGINS", "https://objects.test")
    httpx_mock.add_response(url=url, content=b"unexpected", is_optional=True)

    with pytest.raises(ToolFailure) as caught:
        await download_bytes(url, max_bytes=10)

    assert caught.value.code == "URL_HOST_INVALID"


@pytest.mark.asyncio
async def test_download_rejects_object_io_when_allowlist_is_empty(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.delenv("OBJECT_STORAGE_ALLOWED_ORIGINS", raising=False)
    httpx_mock.add_response(
        url="https://objects.test/object",
        content=b"unexpected",
        is_optional=True,
    )

    with pytest.raises(ToolFailure) as caught:
        await download_bytes("https://objects.test/object", max_bytes=10)

    assert caught.value.code == "URL_HOST_INVALID"


@pytest.mark.asyncio
async def test_download_does_not_follow_redirect_outside_storage_allowlist(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.setenv("OBJECT_STORAGE_ALLOWED_ORIGINS", "https://objects.test")
    httpx_mock.add_response(
        url="https://objects.test/redirect",
        status_code=302,
        headers={"Location": "https://169.254.169.254/latest/meta-data"},
    )
    httpx_mock.add_response(
        url="https://169.254.169.254/latest/meta-data",
        content=b"unexpected",
        is_optional=True,
    )

    with pytest.raises(ToolFailure) as caught:
        await download_bytes("https://objects.test/redirect", max_bytes=10)

    assert caught.value.code == "SOURCE_DOWNLOAD_FAILED"
    assert [str(request.url) for request in httpx_mock.get_requests()] == [
        "https://objects.test/redirect"
    ]


@pytest.mark.asyncio
async def test_download_rejects_plain_http_for_loopback_by_default(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.delenv("ALLOW_INSECURE_LOOPBACK", raising=False)

    with pytest.raises(ToolFailure, match="URL_SCHEME_INVALID"):
        await download_bytes(
            "http://127.0.0.1:8080/in?X-Amz-Signature=secret",
            max_bytes=7,
        )


@pytest.mark.asyncio
async def test_download_allows_plain_http_for_opted_in_loopback_tests(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.setenv("ALLOW_INSECURE_LOOPBACK", "true")
    monkeypatch.setenv(
        "OBJECT_STORAGE_ALLOWED_ORIGINS",
        "http://127.0.0.1:8080",
    )
    httpx_mock.add_response(content=b"fixture")

    result = await download_bytes(
        "http://127.0.0.1:8080/in?X-Amz-Signature=secret",
        max_bytes=7,
    )

    assert result == b"fixture"


@pytest.mark.asyncio
async def test_download_allows_plain_http_for_opted_in_object_storage(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.setenv("ALLOW_INSECURE_OBJECT_STORAGE", "true")
    monkeypatch.setenv(
        "OBJECT_STORAGE_ALLOWED_ORIGINS",
        "http://minio:9000",
    )
    httpx_mock.add_response(content=b"fixture")

    result = await download_bytes(
        "http://minio:9000/in?X-Amz-Signature=secret",
        max_bytes=7,
    )

    assert result == b"fixture"


@pytest.mark.asyncio
async def test_download_failure_redacts_signed_url_from_logs_and_result(
    httpx_mock,
):
    httpx_mock.add_response(status_code=503)

    with pytest.raises(ToolFailure) as caught:
        await download_bytes(
            "https://objects.test/in?X-Amz-Signature=secret",
            max_bytes=10,
        )

    published = json.dumps(caught.value.as_result())
    exposed = f"{caught.value}\n{published}"
    assert caught.value.code == "SOURCE_DOWNLOAD_FAILED"
    assert "objects.test/in" in caught.value.public_message
    assert "X-Amz-Signature" not in exposed
    assert "secret" not in exposed


def test_each_call_gets_a_disposable_directory():
    with temporary_call_directory() as outer_directory:
        marker = outer_directory / "marker"
        marker.write_bytes(b"call one")

        with (
            pytest.raises(RuntimeError, match="tool failed"),
            temporary_call_directory() as nested_directory,
        ):
            assert nested_directory != outer_directory
            assert not (nested_directory / "marker").exists()
            raise RuntimeError("tool failed")

        assert not nested_directory.exists()

    assert not outer_directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_bytes", [None, 0, -1, "4", True])
async def test_download_rejects_invalid_size_limit(httpx_mock, max_bytes):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await download_bytes(
            "https://objects.test/in?X-Amz-Signature=secret",
            max_bytes=max_bytes,
        )


@pytest.mark.asyncio
async def test_download_accepts_body_at_exact_limit(httpx_mock):
    httpx_mock.add_response(content=b"1234")

    result = await download_bytes(
        "https://objects.test/in?X-Amz-Signature=secret",
        max_bytes=4,
    )

    assert result == b"1234"


@pytest.mark.asyncio
@pytest.mark.parametrize("expected_sha256", ["", "0" * 63, "z" * 64, 123])
async def test_download_rejects_malformed_checksum(
    httpx_mock,
    expected_sha256,
):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await download_bytes(
            "https://objects.test/in?X-Amz-Signature=secret",
            max_bytes=10,
            expected_sha256=expected_sha256,
        )


@pytest.mark.asyncio
async def test_download_accepts_matching_checksum(httpx_mock):
    payload = b"payload"
    httpx_mock.add_response(content=payload)

    result = await download_bytes(
        "https://objects.test/in?X-Amz-Signature=secret",
        max_bytes=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest().upper(),
    )

    assert result == payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "   ",
        123,
        "https://[bad",
        "https://objects.test:bad/in?X-Amz-Signature=secret",
        "https:// objects.test/in?X-Amz-Signature=secret",
        "https://objects.test/\ud800?X-Amz-Signature=secret",
        "https://objects.test/\x7f?X-Amz-Signature=secret",
    ],
)
async def test_download_rejects_invalid_url_value(httpx_mock, url):
    with pytest.raises(ToolFailure, match="URL_INVALID"):
        await download_bytes(url, max_bytes=10)


@pytest.mark.asyncio
async def test_download_uses_configured_request_timeout(httpx_mock, monkeypatch):
    monkeypatch.setenv("REQUEST_TIMEOUT_SECONDS", "17.5")
    httpx_mock.add_response(content=b"payload")

    await download_bytes(
        "https://objects.test/in?X-Amz-Signature=secret",
        max_bytes=7,
    )

    timeout = httpx_mock.get_request().extensions["timeout"]
    assert set(timeout.values()) == {17.5}
