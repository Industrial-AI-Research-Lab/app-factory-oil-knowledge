"""CSV MCP transport package.

The public domain API lives in csv_adapter. Importing this package only
registers the FastMCP server exports; it does not read environment values or
perform network or storage I/O.
"""

from .server import (
    csv_download_normalize_upload,
    csv_normalize_inline,
    csv_write_normalized,
    main,
    mcp,
)

__all__ = [
    "csv_download_normalize_upload",
    "csv_normalize_inline",
    "csv_write_normalized",
    "main",
    "mcp",
]
