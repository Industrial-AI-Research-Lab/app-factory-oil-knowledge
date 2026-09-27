"""CSV domain adapter over the canonical CSV pipeline.

This package is the public integration boundary for the CSV MCP. It owns
immutable data models, parsing, bounded download, normalization, and S3
storage. It never imports FastMCP or the transport package.
"""

from .config import E_INTERNAL, E_URL_INVALID
from .models import (
    CsvRow,
    CsvWarning,
    Dialect,
    DownloadRequest,
    Edge,
    GranularUnit,
    NormalizeDelimiter,
    NormalizeResult,
    StructureItem,
    UploadResult,
    WriteResult,
)
from .service import (
    csv_download_normalize_upload,
    csv_normalize_inline,
    csv_write_normalized,
)

__all__ = (
    "CsvRow",
    "CsvWarning",
    "Dialect",
    "DownloadRequest",
    "E_INTERNAL",
    "E_URL_INVALID",
    "Edge",
    "GranularUnit",
    "NormalizeDelimiter",
    "NormalizeResult",
    "StructureItem",
    "UploadResult",
    "WriteResult",
    "csv_download_normalize_upload",
    "csv_normalize_inline",
    "csv_write_normalized",
)
