from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError


def _without_ascii_controls(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("value must not contain ASCII control characters")
    return value


def _require_date_input(value: object) -> object:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        raise PydanticCustomError("date_type", "date must be an ISO string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("date must use YYYY-MM-DD") from None
    if value != parsed.isoformat():
        raise ValueError("date must use YYYY-MM-DD")
    return parsed


MAX_SEARCH_QUERY_CHARS = 400
_QUOTED_PHRASE = re.compile(r'"[^"]*[^"\s][^"]*"')
SearchQuery = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=MAX_SEARCH_QUERY_CHARS
    ),
    AfterValidator(_without_ascii_controls),
]
DomainFilter = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=253),
    AfterValidator(_without_ascii_controls),
]
DomainFilters = Annotated[list[DomainFilter], Field(max_length=20)]
ExactMatch = Annotated[
    bool,
    Field(
        strict=True,
        description=(
            "Only return results containing the query's quoted phrases verbatim. "
            "A query without a quoted phrase is searched as one exact phrase."
        ),
    ),
]
SearchResultCount = Annotated[int, Field(ge=1, le=20, strict=True)]
SearchDate = Annotated[date, BeforeValidator(_require_date_input)]
MAX_SEARCH_SNIPPET_CHARS = 2_000
MAX_SEARCH_TITLE_CHARS = 2_000
MAX_SEARCH_URL_CHARS = 8_192
RequestIdentifier = Annotated[str, StringConstraints(min_length=1, max_length=200)]
SourceIdentifier = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
SearchResultTitle = Annotated[
    str,
    StringConstraints(max_length=MAX_SEARCH_TITLE_CHARS),
]
SearchResultUrl = Annotated[
    str,
    StringConstraints(max_length=MAX_SEARCH_URL_CHARS),
]
SearchResultSnippet = Annotated[
    str,
    StringConstraints(max_length=MAX_SEARCH_SNIPPET_CHARS),
]


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    topic: Literal["general", "news"] = "general"
    start_date: SearchDate | None = None
    end_date: SearchDate | None = None
    include_domains: DomainFilters = Field(default_factory=list)
    exclude_domains: DomainFilters = Field(default_factory=list)
    exact_match: ExactMatch = False
    search_depth: Literal["basic", "advanced"] = "basic"
    max_results: SearchResultCount = 10

    @model_validator(mode="after")
    def dates_are_ordered(self) -> WebSearchRequest:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self

    @model_validator(mode="after")
    def domain_filters_are_unique(self) -> WebSearchRequest:
        domains = [
            domain.casefold()
            for domain in (*self.include_domains, *self.exclude_domains)
        ]
        if len(domains) != len(set(domains)):
            raise ValueError("domain filters must not contain duplicates")
        return self

    @model_validator(mode="after")
    def exact_match_query_is_sendable(self) -> WebSearchRequest:
        query = self.provider_query()
        if self.exact_match and (
            len(query) > MAX_SEARCH_QUERY_CHARS or not _QUOTED_PHRASE.search(query)
        ):
            raise ValueError("exact_match query must fit the provider as a phrase")
        return self

    def provider_query(self) -> str:
        # Tavily's exact_match only matches the phrases quoted inside the query,
        # so a query without a quoted phrase is sent as one phrase.
        if not self.exact_match or _QUOTED_PHRASE.search(self.query):
            return self.query
        return '"' + self.query.replace('"', "").strip() + '"'


class WebSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: Literal["general", "news"]
    start_date: date | None
    end_date: date | None
    include_domains: DomainFilters
    exclude_domains: DomainFilters
    exact_match: bool
    search_depth: Literal["basic", "advanced"]
    max_results: SearchResultCount


class WebSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: SourceIdentifier
    title: SearchResultTitle
    url: SearchResultUrl
    published_date: date | None
    score: float = Field(allow_inf_nan=False)
    snippet: SearchResultSnippet
    search_receipt: str | None = None


class WebSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    request_id: RequestIdentifier
    provider_request_id: RequestIdentifier | None
    filters: WebSearchFilters
    results: list[WebSearchResult] = Field(max_length=20)


class WebSearchErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class WebSearchToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "error"]
    request_id: RequestIdentifier
    provider_request_id: RequestIdentifier | None = None
    filters: WebSearchFilters | None = None
    results: Annotated[list[WebSearchResult], Field(max_length=20)] | None = None
    error: WebSearchErrorDetail | None = None

    @model_validator(mode="after")
    def payload_matches_status(self) -> WebSearchToolResponse:
        if self.status == "ok":
            if self.filters is None or self.results is None or self.error is not None:
                raise ValueError("successful response must contain only success fields")
        elif (
            any(
                value is not None
                for value in (self.provider_request_id, self.filters, self.results)
            )
            or self.error is None
        ):
            raise ValueError("error response must contain only error fields")
        return self
