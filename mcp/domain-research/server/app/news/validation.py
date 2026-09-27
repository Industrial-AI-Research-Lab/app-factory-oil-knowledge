from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from ..errors import ToolFailure
from ..models import DomainFilters, SearchDate, SearchQuery, SourceIdentifier

MAX_TEXT_CHARS = 4_000


def trimmed(value: str) -> str:
    if value != value.strip() or "\x00" in value:
        raise ValueError("value must be trimmed and contain no NUL")
    return value


def excerpt(value: str) -> str:
    if not value.strip() or value != value.strip() or "\x00" in value:
        raise ValueError("excerpt must be non-empty and trimmed")
    return value


def unique_strings(values: list[str]) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError("list values must be unique")
    return values


Text = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_TEXT_CHARS),
    AfterValidator(trimmed),
]
Excerpt = Annotated[
    str,
    StringConstraints(min_length=1, max_length=12_000),
    AfterValidator(excerpt),
]
Category = Annotated[
    str,
    StringConstraints(min_length=1, max_length=200),
    AfterValidator(trimmed),
]


class NewsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class NewsBrief(NewsModel):
    query: SearchQuery
    topic: Literal["general", "news"]
    start_date: SearchDate | None
    end_date: SearchDate | None
    include_domains: DomainFilters
    exclude_domains: DomainFilters
    formats: list[Literal["json", "html", "csv", "pdf"]] = Field(
        min_length=1,
        max_length=4,
    )

    @model_validator(mode="after")
    def boundaries_are_consistent(self) -> NewsBrief:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        domains = [
            domain.casefold()
            for domain in (*self.include_domains, *self.exclude_domains)
        ]
        if len(domains) != len(set(domains)) or len(self.formats) != len(
            set(self.formats)
        ):
            raise ValueError("brief filters and formats must be unique")
        return self


class PublicationEvidence(NewsModel):
    quote: Excerpt


class PublicationDecision(NewsModel):
    source_id: SourceIdentifier
    decision: Literal["include", "exclude", "uncertain"]
    reason: Text
    categories: list[Category] = Field(max_length=20)
    evidence: list[PublicationEvidence] = Field(max_length=20)
    limitations: list[Text] = Field(max_length=20)

    _unique_string_lists = field_validator("categories", "limitations")(unique_strings)


class EventEvidence(NewsModel):
    source_id: SourceIdentifier
    quote: Excerpt


CompanyName = Category
CompanyRole = Literal["developer", "adopter", "partner"]
AdoptionStage = Literal["announced", "pilot", "deployed"]


class CompanyMention(EventEvidence):
    name: CompanyName
    role: CompanyRole


class AdoptionEvidence(EventEvidence):
    stage: AdoptionStage


class NewsEvent(NewsModel):
    event_id: Text
    description: Text
    technology: Text
    task: Text
    object: Text | None
    time: Text | None
    evidence_level: Literal[
        "statement",
        "laboratory",
        "field",
        "operational",
        "not_established",
    ]
    categories: list[Category] = Field(max_length=20)
    limitations: list[Text] = Field(max_length=20)
    source_ids: list[SourceIdentifier] = Field(min_length=1, max_length=20)
    evidence: list[EventEvidence] = Field(min_length=1, max_length=20)
    merge_reason: Text
    companies: list[CompanyMention] = Field(default_factory=list, max_length=20)
    adoption: AdoptionEvidence | None = None

    _unique_string_lists = field_validator(
        "categories",
        "limitations",
        "source_ids",
    )(unique_strings)

    @field_validator("companies")
    @classmethod
    def company_roles_are_unique(
        cls, companies: list[CompanyMention]
    ) -> list[CompanyMention]:
        keys = {(item.name.casefold(), item.role) for item in companies}
        if len(keys) != len(companies):
            raise ValueError("a company may appear once per role")
        return companies


class NewsDecisions(NewsModel):
    publications: list[PublicationDecision] = Field(max_length=20)
    events: list[NewsEvent] = Field(max_length=20)

    @model_validator(mode="after")
    def uncategorized_content_has_limitation(self) -> NewsDecisions:
        unexplained_publication = any(
            item.decision == "include" and not item.categories and not item.limitations
            for item in self.publications
        )
        unexplained_event = any(
            not item.categories and not item.limitations for item in self.events
        )
        if unexplained_publication or unexplained_event:
            raise ValueError("uncategorized included content requires a limitation")
        return self


class CritiqueNote(NewsModel):
    id: Text
    source_ids: list[SourceIdentifier] = Field(max_length=20)
    event_ids: list[Text] = Field(max_length=20)
    issue: Text
    action: Text
    resolution: Literal["resolved", "unresolved"]
    resolution_reason: Text

    _unique_string_lists = field_validator("source_ids", "event_ids")(unique_strings)


class DateAssessment(NewsModel):
    source_id: SourceIdentifier
    assessment: Literal["accepted", "rejected"]
    reason: Text


class NewsCritique(NewsModel):
    reviewed_events: list[NewsEvent] = Field(default_factory=list, max_length=20)
    date_assessments: list[DateAssessment] = Field(default_factory=list, max_length=20)
    correction_cycle: Annotated[int, Field(strict=True, ge=0, le=1)]
    notes: list[CritiqueNote] = Field(max_length=100)


def validate_news_inputs(
    brief: object,
    decisions: object,
    critique: object,
) -> tuple[NewsBrief, NewsDecisions, NewsCritique]:
    return parse_news_inputs(
        brief, decisions, critique, NewsBrief, NewsDecisions, NewsCritique
    )


def parse_news_inputs(
    brief: object,
    decisions: object,
    critique: object,
    brief_model: type[NewsBrief],
    decisions_model: type[NewsDecisions],
    critique_model: type[NewsCritique],
) -> tuple[NewsBrief, NewsDecisions, NewsCritique]:
    try:
        parsed_brief = brief_model.model_validate(brief)
        parsed_decisions = decisions_model.model_validate(decisions)
        parsed_critique = critique_model.model_validate(critique)
    except ValidationError:
        raise ToolFailure(
            "NEWS_SCHEMA_INVALID",
            "News finalization input does not match schema version 1",
        ) from None
    require_unique_ids(parsed_decisions, parsed_critique)
    return parsed_brief, parsed_decisions, parsed_critique


def require_unique_ids(decisions: NewsDecisions, critique: NewsCritique) -> None:
    unique_ids = (
        [item.source_id for item in decisions.publications],
        [item.event_id for item in decisions.events],
        [item.id for item in critique.notes],
        [item.event_id for item in critique.reviewed_events],
        [item.source_id for item in critique.date_assessments],
    )
    if any(len(values) != len(set(values)) for values in unique_ids):
        raise ToolFailure(
            "NEWS_ID_DUPLICATE",
            "Publication, event, and critique note IDs must be unique",
        )
