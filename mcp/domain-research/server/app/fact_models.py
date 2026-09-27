from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError, field_validator

from .errors import ToolFailure
from .ontology.models import StrictModel

_ENDPOINT_FIELDS = frozenset(
    {
        "source_entity_id",
        "source_type_id",
        "target_entity_id",
        "target_type_id",
        "subject_entity_id",
        "subject_type_id",
    }
)


class Evidence(StrictModel):
    verbatim_string_fields = frozenset({"text"})
    normalized_string_fields = frozenset({"source_id", "fragment_id"})

    source_id: str
    fragment_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("evidence text must be non-empty")
        return value


class EntityFact(StrictModel):
    normalized_string_fields = frozenset({"id", "type_id"})

    kind: Literal["entity"]
    id: str
    type_id: str
    properties: dict[str, Any]
    evidence: Evidence


class RelationFact(StrictModel):
    normalized_string_fields = frozenset(
        {
            "id",
            "type_id",
            "source_entity_id",
            "source_type_id",
            "target_entity_id",
            "target_type_id",
        }
    )

    kind: Literal["relation"]
    id: str
    type_id: str
    source_entity_id: str
    source_type_id: str
    target_entity_id: str
    target_type_id: str
    evidence: Evidence


class ObservationFact(StrictModel):
    verbatim_string_fields = frozenset({"unit"})
    normalized_string_fields = frozenset(
        {"id", "type_id", "subject_entity_id", "subject_type_id"}
    )

    kind: Literal["observation"]
    id: str
    type_id: str
    subject_entity_id: str
    subject_type_id: str
    value: Any
    unit: str | None
    conditions: dict[str, Any]
    evidence: Evidence

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "\x00" in value):
            raise ValueError("unit must be non-empty")
        return value


Fact = EntityFact | RelationFact | ObservationFact
FactInput = list[Annotated[Fact, Field(discriminator="kind")]]
FACTS_ADAPTER = TypeAdapter(FactInput)


def parse_facts(value: object) -> list[Fact]:
    if not isinstance(value, list) or not value:
        raise ToolFailure("FACT_SCHEMA_INVALID", "facts must be a non-empty list")
    try:
        facts = FACTS_ADAPTER.validate_python(value)
    except ValidationError as exc:
        code, message = _validation_failure(exc)
        raise ToolFailure(code, message) from None
    ids = [fact.id for fact in facts]
    if len(ids) != len(set(ids)):
        raise ToolFailure("FACT_ID_DUPLICATE", "Fact IDs must be unique")
    return facts


def _validation_failure(exc: ValidationError) -> tuple[str, str]:
    errors = exc.errors(include_url=False)
    if errors and all(
        error["type"] == "value_error"
        and tuple(error["loc"][-2:]) == ("evidence", "text")
        for error in errors
    ):
        return "FACT_EVIDENCE_INVALID", "Evidence text must be non-empty"
    if errors and all(
        error["type"] in {"missing", "value_error"}
        and error["loc"][-1] in _ENDPOINT_FIELDS
        for error in errors
    ):
        return "FACT_ENDPOINT_INVALID", "Fact endpoint is invalid"
    if errors and all(
        error["type"] == "value_error" and error["loc"][-1] == "unit"
        for error in errors
    ):
        return "FACT_MEASUREMENT_INVALID", "Observation unit is invalid"
    return "FACT_SCHEMA_INVALID", "Facts do not match schema version 1"
