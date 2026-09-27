import hashlib
import json

import httpx
import pytest
from app.http_io import ToolFailure, upload_bytes


class ReadForbiddenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError("response body must not be read")
        yield b""


@pytest.mark.asyncio
async def test_upload_rejects_destination_outside_storage_allowlist(
    httpx_mock,
    monkeypatch,
):
    monkeypatch.setenv("OBJECT_STORAGE_ALLOWED_ORIGINS", "https://objects.test")
    httpx_mock.add_response(
        method="PUT",
        url="https://127.0.0.1/private",
        is_optional=True,
    )

    with pytest.raises(ToolFailure) as caught:
        await upload_bytes(
            "https://127.0.0.1/private",
            b"payload",
            content_type="application/json",
        )

    assert caught.value.code == "URL_HOST_INVALID"


@pytest.mark.asyncio
async def test_upload_sends_exact_bytes_and_content_type(httpx_mock):
    httpx_mock.add_response(method="PUT", status_code=200)

    result = await upload_bytes(
        "https://objects.test/out?X-Amz-Signature=secret",
        b"payload",
        content_type="application/json",
    )

    request = httpx_mock.get_request()
    assert request.content == b"payload"
    assert request.headers["Content-Type"] == "application/json"
    assert result.sha256 == hashlib.sha256(b"payload").hexdigest()
    assert result.size_bytes == 7


@pytest.mark.asyncio
async def test_upload_does_not_read_response_body(httpx_mock):
    httpx_mock.add_response(
        method="PUT",
        status_code=200,
        stream=ReadForbiddenStream(),
    )

    result = await upload_bytes(
        "https://objects.test/out?X-Amz-Signature=secret",
        b"payload",
        content_type="application/json",
    )

    assert result.size_bytes == 7


@pytest.mark.asyncio
async def test_upload_failure_redacts_signed_url_from_logs_and_result(
    httpx_mock,
):
    httpx_mock.add_response(method="PUT", status_code=500)

    with pytest.raises(ToolFailure) as caught:
        await upload_bytes(
            "https://objects.test/out?X-Amz-Signature=secret",
            b"payload",
            content_type="application/json",
        )

    published = json.dumps(caught.value.as_result())
    exposed = f"{caught.value}\n{published}"
    assert caught.value.code == "UPLOAD_FAILED"
    assert "objects.test/out" in caught.value.public_message
    assert "X-Amz-Signature" not in exposed
    assert "secret" not in exposed


@pytest.mark.asyncio
async def test_upload_failure_names_the_http_status(httpx_mock):
    httpx_mock.add_response(method="PUT", status_code=503)

    with pytest.raises(ToolFailure) as caught:
        await upload_bytes(
            "https://objects.test/out?X-Amz-Signature=secret",
            b"payload",
            content_type="application/json",
        )

    assert caught.value.code == "UPLOAD_FAILED"
    assert "(HTTP 503)" in caught.value.public_message


@pytest.mark.asyncio
async def test_upload_failure_names_the_transport_error(httpx_mock):
    httpx_mock.add_exception(httpx.WriteTimeout("stalled"), method="PUT")

    with pytest.raises(ToolFailure) as caught:
        await upload_bytes(
            "https://objects.test/out?X-Amz-Signature=secret",
            b"payload",
            content_type="application/json",
        )

    assert caught.value.code == "UPLOAD_FAILED"
    assert "(WriteTimeout)" in caught.value.public_message


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [None, "payload", bytearray(b"payload")])
async def test_upload_rejects_non_bytes_payload(httpx_mock, payload):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await upload_bytes(
            "https://objects.test/out?X-Amz-Signature=secret",
            payload,
            content_type="application/json",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type",
    [
        None,
        "",
        "   ",
        "application/json\r\nX-Leak: secret",
        "application/json\x00x",
        "application/json\tx",
        "text/💣",
    ],
)
async def test_upload_rejects_invalid_content_type(httpx_mock, content_type):
    with pytest.raises(ToolFailure, match="INPUT_INVALID"):
        await upload_bytes(
            "https://objects.test/out?X-Amz-Signature=secret",
            b"payload",
            content_type=content_type,
        )
