from __future__ import annotations

import csv
import io
import json
import math
from typing import Any

from ..errors import ToolFailure
from . import DocumentSpec, FragmentRecord

_TEXT_TYPES = {"text/plain", "text/markdown"}
_JSON_TYPES = {"application/json", "text/json"}
_JSONL_TYPES = {
    "application/jsonl",
    "application/x-jsonlines",
    "application/x-ndjson",
}
_CSV_TYPES = {"text/csv", "application/csv"}


def supported_content_type(content_type: str) -> str:
    media_type = content_type.partition(";")[0].strip().casefold()
    if media_type in _TEXT_TYPES | _JSON_TYPES | _JSONL_TYPES | _CSV_TYPES:
        return media_type
    raise ToolFailure(
        "DOCUMENT_TYPE_UNSUPPORTED",
        f"Document content type is not supported: {media_type or '<empty>'}",
    )


def parse_document(spec: DocumentSpec, payload: bytes) -> list[FragmentRecord]:
    if not payload:
        raise ToolFailure("DOCUMENT_EMPTY", "Document is empty")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ToolFailure(
            "DOCUMENT_ENCODING_INVALID",
            "Document is not valid UTF-8",
        ) from None
    if not text.strip():
        raise ToolFailure("DOCUMENT_EMPTY", "Document is empty")
    if "\x00" in text:
        raise ToolFailure(
            "DOCUMENT_FORMAT_INVALID",
            "Document contains a NUL character",
        )

    media_type = supported_content_type(spec.content_type)
    if media_type in _TEXT_TYPES:
        fragments = [FragmentRecord(locator="document", text=text, metadata={})]
    elif media_type in _JSON_TYPES:
        fragments = _parse_json(text)
    elif media_type in _JSONL_TYPES:
        fragments = _parse_jsonl(text)
    else:
        fragments = _parse_csv(text)

    if not fragments:
        raise ToolFailure("DOCUMENT_EMPTY", "Document has no indexable content")
    return fragments


def _parse_json(text: str) -> list[FragmentRecord]:
    try:
        value = json.loads(
            text,
            parse_constant=_reject_json_constant,
            parse_float=_parse_finite_float,
        )
    except json.JSONDecodeError as exc:
        raise ToolFailure(
            "DOCUMENT_FORMAT_INVALID",
            f"JSON is invalid at line {exc.lineno}, column {exc.colno}",
        ) from None
    except (ValueError, RecursionError):
        raise ToolFailure("DOCUMENT_FORMAT_INVALID", "JSON is invalid") from None
    values = value if isinstance(value, list) else [value]
    return [
        _json_fragment(item, f"item:{index}" if isinstance(value, list) else "document")
        for index, item in enumerate(values, start=1)
    ]


def _parse_jsonl(text: str) -> list[FragmentRecord]:
    fragments: list[FragmentRecord] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        locator = f"line:{line_number}"
        try:
            value = json.loads(
                line,
                parse_constant=_reject_json_constant,
                parse_float=_parse_finite_float,
            )
        except (json.JSONDecodeError, ValueError, RecursionError):
            raise ToolFailure(
                "DOCUMENT_FORMAT_INVALID",
                f"JSONL is invalid at {locator}",
            ) from None
        fragments.append(_json_fragment(value, locator))
    return fragments


def _json_fragment(value: Any, locator: str) -> FragmentRecord:
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        text = value["text"]
        metadata = {key: item for key, item in value.items() if key != "text"}
    elif isinstance(value, str):
        text = value
        metadata = {}
    else:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        metadata = value if isinstance(value, dict) else {}
    if not text.strip():
        raise ToolFailure(
            "DOCUMENT_EMPTY",
            f"Document fragment is empty at {locator}",
        )
    return FragmentRecord(locator=locator, text=text, metadata=metadata)


def _reject_json_constant(value: str) -> None:
    raise ValueError(value)


def _parse_finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(value)
    return number


def parse_json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        raise TypeError("JSON object must be text")
    parsed = json.loads(
        value,
        parse_constant=_reject_json_constant,
        parse_float=_parse_finite_float,
    )
    if not isinstance(parsed, dict):
        raise TypeError("JSON value must be an object")
    return parsed


def _parse_csv(text: str) -> list[FragmentRecord]:
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fieldnames = reader.fieldnames
        if not fieldnames or any(not name for name in fieldnames):
            raise ToolFailure(
                "DOCUMENT_FORMAT_INVALID",
                "CSV header is missing or contains an empty column",
            )
        if len(set(fieldnames)) != len(fieldnames):
            raise ToolFailure(
                "DOCUMENT_FORMAT_INVALID",
                "CSV header contains duplicate columns",
            )
        fragments: list[FragmentRecord] = []
        for row_number, row in enumerate(reader, start=2):
            locator = f"row:{row_number}"
            if None in row or any(value is None for value in row.values()):
                raise ToolFailure(
                    "DOCUMENT_FORMAT_INVALID",
                    f"CSV row has the wrong number of columns at {locator}",
                )
            row_text = "\n".join(f"{name}: {row[name]}" for name in fieldnames)
            if row_text.strip():
                fragments.append(
                    FragmentRecord(locator=locator, text=row_text, metadata=dict(row))
                )
        return fragments
    except csv.Error as exc:
        raise ToolFailure(
            "DOCUMENT_FORMAT_INVALID",
            f"CSV is invalid: {exc}",
        ) from None
