"""Immutable public models for the CSV MCP.

Defines exact type aliases and Pydantic v2 models for canonical rows
plus URL contracts for download-normalize-upload.
No parsing, error taxonomy, settings, I/O, tools, or storage live here.

Attributes:
    StructureItem: Tuple of (code, name, level, priority).
    Edge: Tuple of (predecessor_id, connection_type, lag).
    Dialect: Allowed output delimiters.
    NormalizeDelimiter: Allowed input delimiters including auto-detect.
"""

import math
from typing import Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = (
    "StructureItem",
    "Edge",
    "Dialect",
    "NormalizeDelimiter",
    "GranularUnit",
    "CsvRow",
    "CsvWarning",
    "NormalizeResult",
    "WriteResult",
    "DownloadRequest",
    "UploadResult",
)

# StructureItem: (code, name, level, priority)
StructureItem: TypeAlias = tuple[str, str, int, int]
# Edge: (predecessor, connection_type, lag)
Edge: TypeAlias = tuple[str, Literal["FS", "SS", "FF", "SF"], int]
Dialect: TypeAlias = Literal[";", ","]
NormalizeDelimiter: TypeAlias = Literal["auto", ";", ","]


class GranularUnit(BaseModel):
    """Single granular unit reference.

    Attributes:
        code: Unit code, non-empty.
        name: Unit name, non-empty.
        measurement: Unit of measure, non-empty.
        category: Category, defaults to empty string; None normalizes to "".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    name: str
    measurement: str
    category: str = Field(default="")

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: object) -> object:
        """Normalize missing/null category to empty string.

        Args:
            value: Raw input value.

        Returns:
            Empty string for None, otherwise the original value.
        """
        if value is None:
            return ""
        return value

    @field_validator("code", "name", "measurement")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """Reject empty or whitespace-only values.

        Args:
            value: Validated string value.

        Returns:
            The original value when non-blank.

        Raises:
            ValueError: If the value is empty or whitespace-only.
        """
        if not value.strip():
            raise ValueError("must be non-empty")
        return value


class CsvRow(BaseModel):
    """
    Single normalized CSV data row.

    Attributes:
        activity_id: Unique activity identifier, non-empty.
        activity_name: Activity name, non-empty.
        volume: Finite float volume.
        measurement: Unit of measure, non-empty.
        structure: Structural decomposition items.
        edges: Predecessor edges.
        granular_unit: Optional granular unit reference.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    activity_id: str
    activity_name: str
    volume: float
    measurement: str
    structure: list[StructureItem] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    granular_unit: GranularUnit | None = None

    @field_validator("activity_id", "activity_name", "measurement")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        """
        Reject empty or whitespace-only values.

        Args:
            value: Validated string value.

        Returns:
            The original value when non-blank.

        Raises:
            ValueError: If the value is empty or whitespace-only.
        """
        if not value.strip():
            raise ValueError("must be non-empty")
        return value

    @field_validator("volume", mode="before")
    @classmethod
    def _reject_bool_volume(cls, value: object) -> object:
        """Reject bools before lax float coercion (True would become 1.0).

        Raises:
            ValueError: If the value is a bool.
        """
        if isinstance(value, bool):
            raise ValueError("must not be a bool")
        return value

    @field_validator("structure", "edges", mode="before")
    @classmethod
    def _reject_bool_nested(cls, value: object) -> object:
        """Reject bools in int positions before lax tuple coercion.

        Structure items are (code, name, level, priority), edges are
        (predecessor, type, lag); bools in int slots would become 0/1.
        """
        if isinstance(value, (list, tuple)):
            for entry in value:
                if isinstance(entry, (list, tuple)):
                    for slot in entry[2:]:
                        if isinstance(slot, bool):
                            raise ValueError("must not be a bool")
        return value

    @field_validator("volume")
    @classmethod
    def _reject_non_finite(cls, value: float) -> float:
        """Reject NaN and infinite volumes.

        Args:
            value: Validated float value.

        Returns:
            The original value when finite.

        Raises:
            ValueError: If the value is NaN or infinite.
        """
        if not math.isfinite(value):
            raise ValueError("must be finite")
        return value


class CsvWarning(BaseModel):
    """Non-fatal normalization or serialization warning.

    Attributes:
        code: Stable warning code.
        row: Optional 1-based data row number.
        column: Optional column name.
        message: Human-readable hint without raw cell payloads.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    row: int | None = None
    column: str | None = None
    message: str


class NormalizeResult(BaseModel):
    """Structured result of CSV normalization.

    Attributes:
        rows: Normalized data rows.
        dialect: Detected or requested dialect.
        warnings: Non-fatal warnings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: list[CsvRow]
    dialect: Dialect
    warnings: list[CsvWarning] = Field(default_factory=list)


class WriteResult(BaseModel):
    """Serialized result of CSV writing.

    Attributes:
        content: Canonical CSV text (UTF-8 without BOM).
        row_count: Number of serialized data rows.
        dialect: Used delimiter dialect.
        warnings: Non-fatal warnings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    row_count: int
    dialect: Dialect
    warnings: list[CsvWarning] = Field(default_factory=list)


def _check_url(value: object) -> object:
    """Validate https/s3 URL syntax via urlsplit (no DNS/network)."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("must be non-empty")
    stripped = value.strip()
    if not stripped:
        raise ValueError("must be non-empty")
    try:
        parts = urlsplit(stripped)
    except ValueError:
        raise ValueError("unsupported URL scheme")
    if parts.scheme not in ("https", "s3"):
        raise ValueError("unsupported URL scheme")
    if not stripped.startswith(parts.scheme + "://"):
        raise ValueError("unsupported URL scheme")
    if parts.username or parts.password or parts.fragment:
        raise ValueError("URL must not include credentials or fragment")
    if parts.scheme == "https":
        if not parts.hostname or not parts.path or parts.path == "/":
            raise ValueError("URL must include host and path")
    else:
        if not parts.netloc or not parts.path.strip("/"):
            raise ValueError("URL must include bucket and key")
    return stripped


class DownloadRequest(BaseModel):
    """URL download request (lexical only; DNS/SSRF in download.py).

    Attributes:
        csv_url: Source URL with https/s3 scheme, non-blank.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    csv_url: str

    @field_validator("csv_url", mode="before")
    @classmethod
    def _validate_csv_url(cls, value: object) -> object:
        """Validate csv_url lexically."""
        return _check_url(value)


class UploadResult(BaseModel):
    """Upload result with safe metadata (URL security not complete).

    Attributes:
        csv_url: Result URL with https/s3 scheme, non-blank.
        rows_count: Normalized row count, non-negative.
        columns: Column names, each non-blank and unique.
        encoding: Source encoding label.
        delimiter: Detected dialect.
        warnings: Non-fatal warnings.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    csv_url: str
    rows_count: int = Field(ge=0)
    columns: list[str]
    encoding: Literal["utf-8-sig", "cp1251"]
    delimiter: Dialect
    warnings: list[CsvWarning] = Field(default_factory=list)

    @field_validator("csv_url", mode="before")
    @classmethod
    def _validate_csv_url(cls, value: object) -> object:
        """Validate csv_url lexically."""
        return _check_url(value)

    @field_validator("rows_count", mode="before")
    @classmethod
    def _reject_bool_rows(cls, value: object) -> object:
        """Reject bool before int coercion."""
        if isinstance(value, bool):
            raise ValueError("must be integer")
        return value

    @field_validator("columns", mode="before")
    @classmethod
    def _validate_columns(cls, value: object) -> object:
        """Reject non-sequence, blank, or duplicate column names."""
        if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
            raise ValueError("invalid columns")
        seen: set[str] = set()
        for item in value:
            if isinstance(item, bool) or not isinstance(item, str):
                raise ValueError("must be non-empty")
            if not item.strip():
                raise ValueError("must be non-empty")
            if item in seen:
                raise ValueError("duplicate column")
            seen.add(item)
        return list(value)
