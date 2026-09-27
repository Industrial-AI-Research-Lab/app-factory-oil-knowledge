"""Static configuration constants for the CSV MCP: limits, codes, env names."""

from typing import Final

__all__ = (
    "MAX_CONTENT_CHARS",
    "MAX_ROWS",
    "MAX_COLS",
    "MAX_CELL_CHARS",
    "MAX_STRUCTURE_ITEMS",
    "MAX_EDGES_PER_ROW",
    "MAX_NESTED_DEPTH",
    "CANONICAL_COLUMNS",
    "REQUIRED_COLUMNS",
    "OPTIONAL_COLUMNS",
    "ALLOWED_DELIMITERS",
    "DEFAULT_DELIMITER",
    "NUMERIC_PATTERN_SOURCE",
    "FORMULA_TRIGGERS",
    "E_EMPTY_CONTENT",
    "E_DELIMITER_INVALID",
    "E_HEADER_UNKNOWN",
    "E_HEADER_DUPLICATE",
    "E_REQUIRED_MISSING",
    "E_ROW_WIDTH",
    "E_DUPLICATE_ID",
    "E_VOLUME_INVALID",
    "E_VOLUME_NON_FINITE",
    "E_NESTED_INVALID",
    "E_STRUCTURE_INVALID",
    "E_EDGE_INVALID",
    "E_GRANULAR_UNIT_INVALID",
    "E_LIMIT_CONTENT",
    "E_LIMIT_ROWS",
    "E_LIMIT_COLS",
    "E_LIMIT_CELL",
    "E_LIMIT_NESTED",
    "E_INTERNAL",
    "ALLOWED_URL_SCHEMES",
    "CSV_ENCODINGS",
    "MAX_DOWNLOAD_BYTES",
    "FETCH_TIMEOUT_SECONDS",
    "MAX_REDIRECTS",
    "PRESIGNED_URL_TTL_SECONDS",
    "CSV_ALLOWED_HOSTS",
    "CSV_ALLOWED_S3_BUCKETS",
    "S3_RESULT_BUCKET",
    "S3_RESULT_PREFIX",
    "E_URL_INVALID",
    "E_URL_FORBIDDEN",
    "E_DOWNLOAD_FAILED",
    "E_ENCODING_UNSUPPORTED",
    "E_UPLOAD_FAILED",
    "E_LIMIT_DOWNLOAD",
    "ERROR_CODES",
    "W_FORMULA_QUOTED",
)

# Bounds
MAX_CONTENT_CHARS: Final[int] = 200000
MAX_ROWS: Final[int] = 2000
MAX_COLS: Final[int] = 16
MAX_CELL_CHARS: Final[int] = 16000
MAX_STRUCTURE_ITEMS: Final[int] = 64
MAX_EDGES_PER_ROW: Final[int] = 64
MAX_NESTED_DEPTH: Final[int] = 3

CANONICAL_COLUMNS: Final[tuple[str, ...]] = (
    "activity_id",
    "activity_name",
    "volume",
    "measurement",
    "structure",
    "edges",
    "granular_unit",
)

REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {"activity_id", "activity_name", "volume", "measurement"}
)
OPTIONAL_COLUMNS: Final[frozenset[str]] = frozenset(
    {"structure", "edges", "granular_unit"}
)

ALLOWED_DELIMITERS: Final[tuple[str, ...]] = (";", ",")
DEFAULT_DELIMITER: Final[str] = ";"

NUMERIC_PATTERN_SOURCE: Final[str] = r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$"

# Formula guard triggers for write serialization
FORMULA_TRIGGERS: Final[frozenset[str]] = frozenset({"=", "+", "-", "@", "|", "%"})

# Exact error codes
E_EMPTY_CONTENT: Final[str] = "E_EMPTY_CONTENT"
E_DELIMITER_INVALID: Final[str] = "E_DELIMITER_INVALID"
E_HEADER_UNKNOWN: Final[str] = "E_HEADER_UNKNOWN"
E_HEADER_DUPLICATE: Final[str] = "E_HEADER_DUPLICATE"
E_REQUIRED_MISSING: Final[str] = "E_REQUIRED_MISSING"
E_ROW_WIDTH: Final[str] = "E_ROW_WIDTH"
E_DUPLICATE_ID: Final[str] = "E_DUPLICATE_ID"
E_VOLUME_INVALID: Final[str] = "E_VOLUME_INVALID"
E_VOLUME_NON_FINITE: Final[str] = "E_VOLUME_NON_FINITE"
E_NESTED_INVALID: Final[str] = "E_NESTED_INVALID"
E_STRUCTURE_INVALID: Final[str] = "E_STRUCTURE_INVALID"
E_EDGE_INVALID: Final[str] = "E_EDGE_INVALID"
E_GRANULAR_UNIT_INVALID: Final[str] = "E_GRANULAR_UNIT_INVALID"
E_LIMIT_CONTENT: Final[str] = "E_LIMIT_CONTENT"
E_LIMIT_ROWS: Final[str] = "E_LIMIT_ROWS"
E_LIMIT_COLS: Final[str] = "E_LIMIT_COLS"
E_LIMIT_CELL: Final[str] = "E_LIMIT_CELL"
E_LIMIT_NESTED: Final[str] = "E_LIMIT_NESTED"
E_INTERNAL: Final[str] = "E_INTERNAL"

# URL/download/upload error codes
E_URL_INVALID: Final[str] = "E_URL_INVALID"
E_URL_FORBIDDEN: Final[str] = "E_URL_FORBIDDEN"
E_DOWNLOAD_FAILED: Final[str] = "E_DOWNLOAD_FAILED"
E_ENCODING_UNSUPPORTED: Final[str] = "E_ENCODING_UNSUPPORTED"
E_UPLOAD_FAILED: Final[str] = "E_UPLOAD_FAILED"
E_LIMIT_DOWNLOAD: Final[str] = "E_LIMIT_DOWNLOAD"

ERROR_CODES: Final[tuple[str, ...]] = (
    E_EMPTY_CONTENT,
    E_DELIMITER_INVALID,
    E_HEADER_UNKNOWN,
    E_HEADER_DUPLICATE,
    E_REQUIRED_MISSING,
    E_ROW_WIDTH,
    E_DUPLICATE_ID,
    E_VOLUME_INVALID,
    E_VOLUME_NON_FINITE,
    E_NESTED_INVALID,
    E_STRUCTURE_INVALID,
    E_EDGE_INVALID,
    E_GRANULAR_UNIT_INVALID,
    E_LIMIT_CONTENT,
    E_LIMIT_ROWS,
    E_LIMIT_COLS,
    E_LIMIT_CELL,
    E_LIMIT_NESTED,
    E_INTERNAL,
    E_URL_INVALID,
    E_URL_FORBIDDEN,
    E_DOWNLOAD_FAILED,
    E_ENCODING_UNSUPPORTED,
    E_UPLOAD_FAILED,
    E_LIMIT_DOWNLOAD,
)

# Formula warning emitted only by write serialization
W_FORMULA_QUOTED: Final[str] = "W_FORMULA_QUOTED"

# URL/S3 policy constants
ALLOWED_URL_SCHEMES: Final[tuple[str, ...]] = ("https", "s3")
CSV_ENCODINGS: Final[tuple[str, ...]] = ("utf-8-sig", "cp1251")
MAX_DOWNLOAD_BYTES: Final[int] = 10_000_000
FETCH_TIMEOUT_SECONDS: Final[int] = 15
MAX_REDIRECTS: Final[int] = 3
PRESIGNED_URL_TTL_SECONDS: Final[int] = 3600

# Environment variable names only (no reads, no values, no I/O).
CSV_ALLOWED_HOSTS: Final[str] = "CSV_ALLOWED_HOSTS"
CSV_ALLOWED_S3_BUCKETS: Final[str] = "CSV_ALLOWED_S3_BUCKETS"
S3_RESULT_BUCKET: Final[str] = "S3_RESULT_BUCKET"
S3_RESULT_PREFIX: Final[str] = "S3_RESULT_PREFIX"
