"""Read-only MCP: SEG-Y headers, WITSML well, SCADA HTTP snapshot."""

from __future__ import annotations

import argparse
import os

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from format_adapters_mcp.contract import envelope
from format_adapters_mcp.scada_reader import default_snapshot_url
from format_adapters_mcp.scada_reader import read_scada_snapshot as parse_scada_snapshot
from format_adapters_mcp.segy_reader import read_segy_headers as parse_segy_headers
from format_adapters_mcp.sources import (
    DEFAULT_SEGY_MAX_BYTES,
    DEFAULT_WITSML_MAX_BYTES,
    RemoteFile,
    load_remote,
)
from format_adapters_mcp.witsml_reader import read_witsml_well as parse_witsml_well

PORT = int(os.environ.get("FASTMCP_PORT") or os.environ.get("PORT", "8080"))

mcp = FastMCP(
    "ipr4-format-adapters",
    host="0.0.0.0",
    port=PORT,
)


@mcp.custom_route("/ping", methods=["GET"])
async def ping(_request: Request) -> PlainTextResponse:
    """AppFactory ZIP smoke first probes GET /ping, then /mcp."""
    return PlainTextResponse("ok")

def _missing_source(format_name: str, source: str) -> dict:
    return envelope(
        status="error",
        reason="source_not_configured",
        format_name=format_name,
        source=source,
    )


def _read_remote(path: str, *, format_name: str, parse, suffix: str, base_env: str, max_env: str, default_max: int) -> dict:
    loaded = load_remote(
        base_env,
        path,
        format_name=format_name,
        suffix=suffix,
        max_bytes_env=max_env,
        default_max_bytes=default_max,
    )
    if loaded is None:
        return _missing_source(format_name, path)
    if isinstance(loaded, dict):
        return loaded
    assert isinstance(loaded, RemoteFile)
    try:
        result = parse(loaded.path)
    finally:
        loaded.path.unlink(missing_ok=True)
    result["source"] = loaded.url
    return result


@mcp.tool()
def read_segy_headers(path: str = "segy/ok_rev1.sgy") -> dict:
    """Read textual (3200) and binary (400) headers from a SEG-Y file. Read-only."""
    return _read_remote(
        path,
        format_name="segy",
        parse=parse_segy_headers,
        suffix=".sgy",
        base_env="SEGY_BASE_URL",
        max_env="SEGY_MAX_BYTES",
        default_max=DEFAULT_SEGY_MAX_BYTES,
    )


@mcp.tool()
def read_witsml_well(path: str = "witsml/ok_well_1411.xml") -> dict:
    """Read one WITSML 1.4.1.1 well object from XML. Read-only."""
    return _read_remote(
        path,
        format_name="witsml",
        parse=parse_witsml_well,
        suffix=".xml",
        base_env="WITSML_BASE_URL",
        max_env="WITSML_MAX_BYTES",
        default_max=DEFAULT_WITSML_MAX_BYTES,
    )


@mcp.tool()
def read_scada_snapshot(url: str = "") -> dict:
    """GET one telemetry snapshot from SCADA_BASE_URL. Read-only."""
    target = url.strip() or (default_snapshot_url() or "")
    if not target:
        return _missing_source("scada", "")
    return parse_scada_snapshot(target)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="format_adapters_mcp")
    parser.add_argument(
        "--transport",
        default="streamable-http",
        choices=("streamable-http", "stdio"),
        help="MCP transport (AppFactory ZIP: streamable-http or stdio)",
    )
    args = parser.parse_args(argv)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
