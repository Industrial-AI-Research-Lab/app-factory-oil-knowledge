"""Orchestration layer for the CSV MCP.

Thin orchestration over bounded download, strict parsing, canonical
writing with write-time formula protection, and S3 upload. Inline helpers
remain backward-compatible pure adapters. No direct fetch, parsing,
serialization, upload, URL validation, or security logic lives here; all
I/O happens inside the injected download/storage adapters at call time
without import-time I/O, FastMCP, or pandas.
"""

import csv
import io
import json
import logging
import math
import re
from collections.abc import Mapping

from .config import (
    ALLOWED_DELIMITERS,
    CANONICAL_COLUMNS,
    E_DELIMITER_INVALID,
    E_INTERNAL,
    E_UPLOAD_FAILED,
    E_LIMIT_CELL,
    E_LIMIT_CONTENT,
    E_LIMIT_NESTED,
    E_LIMIT_ROWS,
    E_VOLUME_NON_FINITE,
    FORMULA_TRIGGERS,
    NUMERIC_PATTERN_SOURCE,
    W_FORMULA_QUOTED,
    MAX_CELL_CHARS,
    MAX_CONTENT_CHARS,
    MAX_EDGES_PER_ROW,
    MAX_ROWS,
    MAX_STRUCTURE_ITEMS,
)
from .download import HttpClient, S3ReadClient, download_csv
from .errors import CsvDomainError, ServiceError
from .models import (
    CsvRow,
    CsvWarning,
    Dialect,
    DownloadRequest,
    NormalizeDelimiter,
    NormalizeResult,
    UploadResult,
    WriteResult,
)
from .parser import parse_csv_content
from .storage import S3WriteClient, upload_normalized_csv

logger = logging.getLogger(__name__)

__all__ = (
    "csv_normalize_inline",
    "csv_write_normalized",
    "csv_download_normalize_upload",
)

_NUMERIC_RE = re.compile(NUMERIC_PATTERN_SOURCE)


def _fail(code: str, hint: str, row: int | None = None) -> ServiceError:
    """Build a safe ValueError without echoing raw cell values.

    Args:
        code: Stable error code.
        hint: Safe human-readable hint.
        row: Optional 1-based data row number.
    """
    if row is None:
        return ServiceError(f"{code}: {hint}")
    return ServiceError(f"{code}: row {row}: {hint}")


def _guard_cell(cell: str, row: int, column: str, warnings: list[CsvWarning]) -> str:
    """Apply write-time formula protection to a single cell.

    Args:
        cell: Serialized cell text.
        row: 1-based data row number for the warning.
        column: Canonical column name for the warning.
        warnings: Mutable warning accumulator.

    Returns:
        Guarded cell text with one leading apostrophe when triggered.
    """
    # Strip all leading whitespace (spaces, tabs, newlines, BOM): spreadsheet
    # apps skip them before evaluating a cell, so "\n=cmd" must be guarded.
    stripped = cell.lstrip(" \t\r\n\v\f\u00a0\ufeff")
    if not stripped or stripped[0] not in FORMULA_TRIGGERS:
        return cell
    # Whole-cell numeric exemption: match against the original cell, so
    # leading whitespace means it is not a whole-cell numeric match.
    if _NUMERIC_RE.fullmatch(cell) is not None:
        return cell
    warnings.append(
        CsvWarning(
            code=W_FORMULA_QUOTED,
            row=row,
            column=column,
            message="formula guard applied",
        )
    )
    return "'" + cell


def csv_normalize_inline(content: str, delimiter: NormalizeDelimiter = "auto") -> NormalizeResult:
    """Normalize CSV text into structured rows without serializing.

    Args:
        content: Decoded CSV text.
        delimiter: Input delimiter hint or auto-detect.

    Returns:
        NormalizeResult with rows, dialect, and an empty warnings list.

    Raises:
        ValueError: Safe E_* failure from the strict parser.
        RuntimeError: E_INTERNAL for unexpected failures.
    """
    logger.info("[CSV_MCP][ADAPTER] op=csv_normalize_inline -- start")
    try:
        rows, dialect = parse_csv_content(content, delimiter)
        logger.info("[CSV_MCP][ADAPTER] op=csv_normalize_inline -- finished rows=%d", len(rows))
        return NormalizeResult(rows=rows, dialect=dialect, warnings=[])
    except ServiceError:
        raise
    except ValueError as exc:
        # Trusted parser domain errors are the exact built-in ValueError;
        # subclasses (e.g. Pydantic ValidationError) map to E_INTERNAL.
        if type(exc) is ValueError:
            raise
        logger.error("[CSV_MCP][ADAPTER] op=csv_normalize_inline -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
    except Exception as exc:
        logger.error("[CSV_MCP][ADAPTER] op=csv_normalize_inline -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None


def csv_write_normalized(rows: list[CsvRow], delimiter: Dialect = ";") -> WriteResult:
    """Serialize normalized rows to canonical CSV with formula protection.

    Args:
        rows: Normalized data rows.
        delimiter: Output dialect delimiter.

    Returns:
        WriteResult with UTF-8 (no BOM) content, row count, dialect, warnings.

    Raises:
        ValueError: E_DELIMITER_INVALID or E_VOLUME_NON_FINITE with safe hints.
        RuntimeError: E_INTERNAL for unexpected failures.
    """
    logger.info("[CSV_MCP][ADAPTER] op=csv_write_normalized -- start")
    if delimiter not in ALLOWED_DELIMITERS:
        raise _fail(E_DELIMITER_INVALID, "unsupported delimiter")
    if not isinstance(rows, list):
        raise RuntimeError(E_INTERNAL)
    if len(rows) > MAX_ROWS:
        raise _fail(E_LIMIT_ROWS, "too many rows")
    try:
        warnings: list[CsvWarning] = []
        buffer = io.StringIO()
        writer = csv.writer(
            buffer,
            delimiter=delimiter,
            lineterminator="\n",
            quoting=csv.QUOTE_MINIMAL,
            doublequote=True,
        )
        writer.writerow(list(CANONICAL_COLUMNS))
        for pos, row in enumerate(rows, start=1):
            if not isinstance(row, CsvRow):
                raise RuntimeError(E_INTERNAL)
            if len(row.structure) > MAX_STRUCTURE_ITEMS or len(row.edges) > MAX_EDGES_PER_ROW:
                raise _fail(E_LIMIT_NESTED, "too many nested items", pos)
            volume = row.volume
            if isinstance(volume, bool) or not isinstance(volume, (int, float)):
                raise _fail(E_VOLUME_NON_FINITE, "non-finite volume", pos)
            if not math.isfinite(volume):
                raise _fail(E_VOLUME_NON_FINITE, "non-finite volume", pos)
            volume_text = repr(volume)
            structure_text = json.dumps(
                [list(item) for item in row.structure],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            edges_text = json.dumps(
                [list(edge) for edge in row.edges],
                separators=(",", ":"),
                ensure_ascii=False,
            )
            if row.granular_unit is None:
                granular_text = ""
            else:
                granular_text = json.dumps(
                    {
                        "code": row.granular_unit.code,
                        "name": row.granular_unit.name,
                        "measurement": row.granular_unit.measurement,
                        "category": row.granular_unit.category,
                    },
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            cells = {
                "activity_id": row.activity_id,
                "activity_name": row.activity_name,
                "volume": volume_text,
                "measurement": row.measurement,
                "structure": structure_text,
                "edges": edges_text,
                "granular_unit": granular_text,
            }
            if any(len(cell) > MAX_CELL_CHARS for cell in cells.values()):
                raise _fail(E_LIMIT_CELL, "cell too large", pos)
            guarded = [
                _guard_cell(cells[column], pos, column, warnings)
                for column in CANONICAL_COLUMNS
            ]
            writer.writerow(guarded)
        content = buffer.getvalue()
        if len(content) > MAX_CONTENT_CHARS or len(content.encode("utf-8")) > MAX_CONTENT_CHARS:
            raise _fail(E_LIMIT_CONTENT, "content too large")
        logger.info("[CSV_MCP][ADAPTER] op=csv_write_normalized -- finished rows=%d", len(rows))
        return WriteResult(
            content=content,
            row_count=len(rows),
            dialect=delimiter,
            warnings=warnings,
        )
    except ServiceError:
        raise
    except (ValueError, TypeError) as exc:
        logger.error("[CSV_MCP][ADAPTER] op=csv_write_normalized -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
    except Exception as exc:
        logger.error("[CSV_MCP][ADAPTER] op=csv_write_normalized -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None


def csv_download_normalize_upload(
    request: DownloadRequest,
    *,
    environ: Mapping[str, str] | None = None,
    http_client: HttpClient | None = None,
    s3_client: S3ReadClient | S3WriteClient | None = None,
) -> UploadResult:
    """Download, normalize, serialize, and upload one CSV.

    Composes exactly download_csv then csv_normalize_inline with auto
    delimiter then csv_write_normalized with the detected delimiter then
    upload_normalized_csv. All bounds, URL validation, and security checks
    stay inside the delegated adapters.

    Args:
        request: Download request with the source https/s3 URL.
        environ: Environment mapping override for testing.
        http_client: Injected HTTPS client for testing.
        s3_client: Injected S3 client for testing.

    Returns:
        UploadResult with the result URL, row count, canonical columns,
        source encoding, detected delimiter, and combined warnings.

    Raises:
        ValueError: Safe E_* failure from download, parsing, or upload.
        RuntimeError: Stable E_INTERNAL or E_UPLOAD_FAILED failure.
    """
    logger.info("[CSV_MCP][ADAPTER] op=csv_download_normalize_upload -- start")
    try:
        downloaded = download_csv(
            request,
            environ=environ,
            http_client=http_client,
            s3_client=s3_client,
        )
        normalized = csv_normalize_inline(downloaded.content, "auto")
        written = csv_write_normalized(normalized.rows, normalized.dialect)
        result_url = upload_normalized_csv(
            written.content, environ=environ, s3_client=s3_client
        )
        logger.info(
            "[CSV_MCP][ADAPTER] op=csv_download_normalize_upload -- finished rows=%d",
            written.row_count,
        )
        return UploadResult(
            csv_url=result_url,
            rows_count=written.row_count,
            columns=list(CANONICAL_COLUMNS),
            encoding=downloaded.encoding,
            delimiter=normalized.dialect,
            warnings=[*normalized.warnings, *written.warnings],
        )
    except ServiceError:
        raise
    except CsvDomainError:
        # Download-layer domain errors are a ValueError subclass by design;
        # the exact-type check below would misroute them to E_INTERNAL.
        raise
    except ValueError as exc:
        if type(exc) is ValueError:
            raise
        logger.error("[CSV_MCP][ADAPTER] op=csv_download_normalize_upload -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
    except RuntimeError as exc:
        if str(exc) in (E_INTERNAL, E_UPLOAD_FAILED):
            raise
        logger.error("[CSV_MCP][ADAPTER] op=csv_download_normalize_upload -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
    except Exception as exc:
        logger.error("[CSV_MCP][ADAPTER] op=csv_download_normalize_upload -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError(E_INTERNAL) from None
