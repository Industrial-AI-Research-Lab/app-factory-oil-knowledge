"""In-process synthetic SCADA HTTP API (GET only)."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

_MEASURED_AT = "2026-09-21T00:00:00Z"
_PAD_POINTS = (
    ("SYN-WELL-001", "SYN.WELL01.WHP", "Wellhead pressure", 12.5, "bar"),
    ("SYN-WELL-001", "SYN.WELL01.WHT", "Wellhead temperature", 64.0, "degC"),
    ("SYN-WELL-001", "SYN.WELL01.CHOKE", "Choke opening", 42.0, "%"),
    ("SYN-WELL-001", "SYN.WELL01.QOIL", "Oil rate", 86.4, "m3/d"),
    ("SYN-WELL-002", "SYN.WELL02.WHP", "Wellhead pressure", 9.8, "bar"),
    ("SYN-WELL-002", "SYN.WELL02.WHT", "Wellhead temperature", 58.2, "degC"),
    ("SYN-WELL-002", "SYN.WELL02.QOIL", "Oil rate", 120.0, "m3/d"),
    ("SYN-WELL-003", "SYN.WELL03.WHP", "Wellhead pressure", 18.4, "bar"),
    ("SYN-WELL-003", "SYN.WELL03.QGAS", "Gas rate", 42.5, "km3/d"),
    ("SYN-WELL-004", "SYN.WELL04.WHP", "Wellhead pressure", 0.0, "bar"),
    ("SYN-WELL-005", "SYN.WELL05.WHP", "Wellhead pressure", 11.1, "bar"),
    ("SYN-WELL-005", "SYN.WELL05.QINJ", "Injection rate", 240.0, "m3/d"),
)

OK_BODY = {
    "api_version": "1",
    "facility": "SYN-PAD-01",
    "well_uid": "SYN-WELL-001",
    "tag": "SYN.WELL01.WHP",
    "description": "Wellhead pressure",
    "value": 12.5,
    "unit": "bar",
    "quality": "good",
    "measured_at": _MEASURED_AT,
    "source": "synthetic-scada-v1",
    "points": [
        {
            "well_uid": well_uid,
            "tag": tag,
            "description": description,
            "value": value,
            "unit": unit,
            "quality": "good",
            "measured_at": _MEASURED_AT,
        }
        for well_uid, tag, description, value, unit in _PAD_POINTS
    ],
}

NULL_BODY = {
    "api_version": "1",
    "well_uid": "SYN-WELL-001",
    "facility": "SYN-PAD-01",
    "tag": "SYN.WELL01.WHP",
    "description": "Wellhead pressure",
    "value": None,
    "unit": "bar",
    "quality": "bad",
    "measured_at": _MEASURED_AT,
    "source": "synthetic-scada-v1",
}
ZERO_BODY = {
    "api_version": "1",
    "well_uid": "SYN-WELL-001",
    "facility": "SYN-PAD-01",
    "tag": "SYN.WELL01.CHOKE",
    "description": "Choke opening",
    "value": 0.0,
    "unit": "%",
    "quality": "good",
    "measured_at": _MEASURED_AT,
    "source": "synthetic-scada-v1",
}
INCOMPLETE_BODY = {
    "api_version": "1",
    "tag": "SYN.WELL01.WHT",
    "value": 64.0,
    "measured_at": "2026-09-21T00:00:00Z",
    "source": "synthetic-scada-v1",
}
V2_BODY = {**OK_BODY, "api_version": "2"}
NESTED_GAP_BODY = {
    "api_version": "1",
    "tag": "SYN.WELL01.WHP",
    "value": 12.5,
    "unit": "bar",
    "measured_at": _MEASURED_AT,
    "source": "synthetic-scada-v1",
    "points": [
        {
            "tag": "SYN.WELL01.WHP",
            "value": 12.5,
            "unit": "bar",
            "measured_at": _MEASURED_AT,
        },
        {
            "tag": "SYN.WELL03.WHP",
            "value": None,
            "measured_at": _MEASURED_AT,
        },
    ],
}


def scada_get(path: str) -> tuple[int, dict] | None:
    """JSON response for a telemetry path. None when the path is not telemetry."""
    if path in ("/v1/hang", "/v1/telemetry/hang"):
        time.sleep(30)
        return 200, OK_BODY
    if path in ("/v1/error", "/v1/telemetry/error"):
        return 503, {"error": "synthetic_backend_unavailable"}
    if path == "/v1/telemetry":
        return 200, OK_BODY
    if path == "/v1/telemetry/null":
        return 200, NULL_BODY
    if path == "/v1/telemetry/zero":
        return 200, ZERO_BODY
    if path == "/v1/telemetry/incomplete":
        return 200, INCOMPLETE_BODY
    if path == "/v1/telemetry/nested-gap":
        return 200, NESTED_GAP_BODY
    if path == "/v2/telemetry":
        return 200, V2_BODY
    return None


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        found = scada_get(urlparse(self.path).path)
        if found is None:
            self._json(404, {"error": "not_found"})
            return
        self._json(*found)

    def do_POST(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def do_PUT(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def do_PATCH(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ScadaMock:
    def __init__(self, host: str = "127.0.0.1", port: int = 8081) -> None:
        self._server = ThreadingHTTPServer((host, port), _Handler)
        self.host = host
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
