from __future__ import annotations

import base64
import hashlib
import hmac
import re

from .errors import ToolFailure

_RECEIPT_PATTERN = re.compile(r"v1\.[A-Za-z0-9_-]{43}")
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_RECEIPT_DOMAIN = b"AppFactory.web-source-bundle.v1\x00"


def valid_bundle_receipt(value: object) -> bool:
    return isinstance(value, str) and _RECEIPT_PATTERN.fullmatch(value) is not None


def valid_bundle_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def issue_bundle_receipt(bundle_sha256: str, signing_key: str | bytes) -> str:
    digest = bytes.fromhex(bundle_sha256)
    key = signing_key.encode("utf-8") if isinstance(signing_key, str) else signing_key
    signature = hmac.new(key, _RECEIPT_DOMAIN + digest, hashlib.sha256).digest()
    encoded = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"v1.{encoded}"


def verify_bundle_receipt(
    bundle_sha256: str,
    receipt: str,
    signing_key: str | bytes,
) -> None:
    expected = issue_bundle_receipt(bundle_sha256, signing_key)
    if not hmac.compare_digest(receipt, expected):
        raise ToolFailure(
            "BUNDLE_RECEIPT_INVALID",
            "Saved source bundle receipt is invalid",
        )
