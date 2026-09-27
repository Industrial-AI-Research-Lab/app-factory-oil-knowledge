"""Read one telemetry snapshot from the test SCADA HTTP API."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from format_adapters_mcp.contract import envelope

DEFAULT_TIMEOUT_S = 2.0


def configured_scada_base() -> str:
    return os.environ.get("SCADA_BASE_URL", "").strip().rstrip("/")


def default_snapshot_url() -> str | None:
    base = configured_scada_base()
    if not base:
        return None
    if urlparse(base).path not in ("", "/"):
        return base
    return f"{base}/v1/telemetry"


def _port_for(parsed) -> int | None:
    if parsed.port is not None:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _allowed_origin() -> tuple[str, str, int] | None:
    """Scheme, host, and port of SCADA_BASE_URL. Only the path may vary."""
    parsed = urlparse(configured_scada_base())
    host = (parsed.hostname or "").lower()
    port = _port_for(parsed)
    if parsed.scheme not in ("http", "https") or not host or port is None:
        return None
    if parsed.username or parsed.password:
        return None
    return parsed.scheme, host, port


def _url_allowed(url: str) -> bool:
    allowed = _allowed_origin()
    if allowed is None:
        return False
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = _port_for(parsed)
    if parsed.username or parsed.password or port is None:
        return False
    return (parsed.scheme, host, port) == allowed


def read_scada_snapshot(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    if not _url_allowed(url):
        return envelope(
            status="error",
            reason="url_not_allowed",
            format_name="scada",
            source=url,
        )
    try:
        response = httpx.get(url, timeout=timeout_s, follow_redirects=False)
    except httpx.TimeoutException:
        return envelope(
            status="timeout",
            reason="timeout",
            format_name="scada",
            format_version="1",
            source=url,
        )
    except httpx.HTTPError as exc:
        return envelope(
            status="error",
            reason=f"{type(exc).__name__}: {exc}",
            format_name="scada",
            format_version="1",
            source=url,
        )

    if response.status_code >= 400:
        return envelope(
            status="error",
            reason=f"http_{response.status_code}",
            format_name="scada",
            format_version="1",
            source=url,
        )

    try:
        body = response.json()
    except ValueError:
        return envelope(
            status="corrupt_input",
            reason="response_not_json",
            format_name="scada",
            format_version="1",
            source=url,
        )
    if not isinstance(body, dict):
        return envelope(
            status="corrupt_input",
            reason="response_not_object",
            format_name="scada",
            format_version="1",
            source=url,
        )

    api_version = str(body.get("api_version") or "1")
    if api_version != "1":
        return envelope(
            status="unsupported_version",
            reason="scada_api_version",
            format_name="scada",
            format_version=api_version,
            source=url,
            data=body,
        )

    tag = body.get("tag")
    unit = body.get("unit")
    measured_at = body.get("measured_at")
    source_field = body.get("source")
    value_present = "value" in body
    value = body.get("value") if value_present else None

    missing: list[str] = []
    if not tag:
        missing.append("tag")
    if "value" not in body:
        missing.append("value")
    if unit is None or unit == "":
        missing.append("unit")
    if not measured_at:
        missing.append("measured_at")
    if not source_field:
        missing.append("source")

    data = {
        "tag": tag,
        "value": value,
        "unit": unit,
        "measured_at": measured_at,
        "source": source_field,
    }
    for optional in ("well_uid", "facility", "description", "quality"):
        if body.get(optional) not in (None, ""):
            data[optional] = body.get(optional)
    points = body.get("points")
    point_missing: list[str] = []
    if isinstance(points, list) and points:
        data["points"] = points
        for index, point in enumerate(points):
            point_missing.extend(_missing_point_fields(point, index))
    if missing:
        return envelope(
            status="incomplete",
            reason="missing_fields",
            format_name="scada",
            format_version="1",
            source=url,
            data=data,
            missing_fields=[*missing, *point_missing],
        )
    return envelope(
        status="ok",
        format_name="scada",
        format_version="1",
        source=url,
        data=data,
        missing_fields=point_missing,
    )


def _missing_point_fields(point: Any, index: int) -> list[str]:
    """Gaps in one telemetry point. A JSON null value is a real absence, not a gap."""
    prefix = f"points[{index}]"
    if not isinstance(point, dict):
        return [prefix]
    missing: list[str] = []
    if not point.get("tag"):
        missing.append(f"{prefix}.tag")
    if "value" not in point:
        missing.append(f"{prefix}.value")
    unit = point.get("unit")
    if unit is None or unit == "":
        missing.append(f"{prefix}.unit")
    if not point.get("measured_at"):
        missing.append(f"{prefix}.measured_at")
    return missing
