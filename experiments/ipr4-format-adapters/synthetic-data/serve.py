"""Serve synthetic SEG-Y, WITSML, and telemetry. This process holds the files; the MCP does not."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

try:
    from format_adapters_mcp.scada_mock import scada_get
except ImportError:
    from scada_mock import scada_get

PORT = int(os.environ.get("DATA_PORT", "8090"))


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data")).resolve()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        found = scada_get(path)
        if found is not None:
            self._json(*found)
            return
        self._file(path)

    def do_POST(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def do_PUT(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def do_PATCH(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def do_DELETE(self) -> None:  # noqa: N802
        self._json(405, {"error": "write_not_supported"})

    def _file(self, path: str) -> None:
        rel = path.lstrip("/")
        if not rel or ".." in rel.split("/"):
            self._json(404, {"error": "not_found"})
            return
        root = data_dir()
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            self._json(404, {"error": "not_found"})
            return
        if not target.is_file():
            self._json(404, {"error": "not_found"})
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
