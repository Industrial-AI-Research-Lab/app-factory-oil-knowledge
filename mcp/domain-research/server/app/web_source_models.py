from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WebSourceFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    url: str
    title: str
    published_date: str | None
    search_published_date: str | None = None
    search_request_id: str | None = None
    search_receipt: str | None = None
    text: str
    total_chars: int = Field(ge=0)
    truncated: bool


class ExtractResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "partial"] = "ok"
    request_id: str
    bundle_sha256: str
    bundle_receipt: str
    size_bytes: int
    media_type: str = "application/json"
    source_ids: list[str]
    previews: list[dict[str, str | bool]]
    failures: list[dict[str, str]]


class ReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    request_id: str
    fragments: list[WebSourceFragment]
