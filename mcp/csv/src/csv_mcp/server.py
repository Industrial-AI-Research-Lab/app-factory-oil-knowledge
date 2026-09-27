"""FastMCP transport wrapper for the CSV MCP.

CSV parsing, bounded download, normalization, and storage live behind the
public csv_adapter API.
"""

import argparse
import logging
import os
import threading

from mcp.server.fastmcp import FastMCP

from csv_adapter import (
    CsvRow,
    Dialect,
    DownloadRequest,
    E_INTERNAL,
    E_URL_INVALID,
    NormalizeDelimiter,
    NormalizeResult,
    UploadResult,
    WriteResult,
    csv_download_normalize_upload as _csv_download_normalize_upload,
    csv_normalize_inline as _csv_normalize_inline,
    csv_write_normalized as _csv_write_normalized,
)

logger = logging.getLogger(__name__)

__all__ = (
    "mcp",
    "main",
    "csv_normalize_inline",
    "csv_write_normalized",
    "csv_download_normalize_upload",
)

HOST = "0.0.0.0"
try:
    PORT = int(os.environ.get("PORT", "8080"))
except ValueError:
    PORT = 8080
if not 1 <= PORT <= 65535:
    PORT = 8080
MCP_PATH = "/mcp"

mcp = FastMCP("csv", host=HOST, port=PORT)
_DOWNLOAD_SLOTS = threading.BoundedSemaphore(8)


@mcp.tool(name="csv_normalize_inline")
def csv_normalize_inline(content: str, delimiter: NormalizeDelimiter = "auto") -> NormalizeResult:
    """Normalize CSV text into structured rows without serializing."""
    try:
        return _csv_normalize_inline(content, delimiter)
    except ValueError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None


@mcp.tool(name="csv_write_normalized")
def csv_write_normalized(rows: list[CsvRow], delimiter: Dialect = ";") -> WriteResult:
    """Serialize normalized rows to canonical CSV text."""
    try:
        return _csv_write_normalized(rows, delimiter)
    except ValueError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None


@mcp.tool(name="csv_download_normalize_upload")
def csv_download_normalize_upload(csv_url: str) -> UploadResult:
    """Download, normalize, serialize, and upload one CSV via URL."""
    try:
        request = DownloadRequest(csv_url=csv_url)
    except Exception:
        raise ValueError(f"{E_URL_INVALID}: invalid URL") from None
    try:
        with _DOWNLOAD_SLOTS:
            return _csv_download_normalize_upload(request)
    except ValueError:
        raise
    except RuntimeError:
        raise
    except Exception:
        raise RuntimeError(E_INTERNAL) from None


def main(argv: list[str] | None = None) -> None:
    """Run the server on http://0.0.0.0:8080/mcp via streamable-http."""
    parser = argparse.ArgumentParser(prog="csv_mcp")
    parser.add_argument(
        "--transport",
        default="streamable-http",
        choices=("streamable-http",),
        help="MCP transport (AppFactory mode: streamable-http)",
    )
    parser.parse_args(argv)
    logger.info(
        "[CSV_MCP][SERVE] host=%s port=%d path=%s -- starting",
        HOST,
        PORT,
        MCP_PATH,
    )
    mcp.run(transport="streamable-http")
