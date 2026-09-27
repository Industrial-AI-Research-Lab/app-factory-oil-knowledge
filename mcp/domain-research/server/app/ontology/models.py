from __future__ import annotations

import unicodedata
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

ValueType = Literal["string", "integer", "number", "boolean", "string_list"]
SemanticVersion = Annotated[
    str,
    Field(
        pattern=r"^(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})\.(0|[1-9][0-9]{0,17})$"
    ),
]


def _validated_text(value: str) -> str:
    if not value or value != value.strip() or "\x00" in value:
        raise ValueError("strings must be non-empty and trimmed")
    return value


def _normalized_text(value: str) -> str:
    return unicodedata.normalize("NFC", _validated_text(value))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    verbatim_string_fields: ClassVar[frozenset[str]] = frozenset()
    normalized_string_fields: ClassVar[frozenset[str]] = frozenset()

    @field_validator("*")
    @classmethod
    def normalize_strings(cls, value: object, info: ValidationInfo) -> object:
        if info.field_name in cls.verbatim_string_fields:
            return value
        if isinstance(value, str):
            if info.field_name in cls.normalized_string_fields:
                return _normalized_text(value)
            return _validated_text(value)
        return value


class PropertyDefinition(StrictModel):
    normalized_string_fields = frozenset({"id"})

    id: str
    label: str
    value_type: ValueType
    required: bool


class EntityType(StrictModel):
    normalized_string_fields = frozenset({"id"})

    id: str
    label: str
    properties: list[PropertyDefinition]


class RelationType(StrictModel):
    normalized_string_fields = frozenset({"id", "source_type", "target_type"})

    id: str
    label: str
    source_type: str
    target_type: str


class ObservationType(StrictModel):
    normalized_string_fields = frozenset({"id", "subject_type"})

    id: str
    label: str
    subject_type: str
    value_type: ValueType
    unit_required: bool
    condition_properties: list[PropertyDefinition]


class Coverage(StrictModel):
    normalized_string_fields = frozenset({"question_id"})

    question_id: str
    schema_element_ids: list[str] = Field(min_length=1)

    @field_validator("schema_element_ids")
    @classmethod
    def normalize_schema_element_ids(cls, values: list[str]) -> list[str]:
        return [_normalized_text(value) for value in values]


class OntologyDocument(StrictModel):
    normalized_string_fields = frozenset({"ontology_id"})

    schema_version: Literal[1]
    ontology_id: str
    version: SemanticVersion
    base_version: SemanticVersion | None
    entity_types: list[EntityType] = Field(min_length=1)
    relation_types: list[RelationType]
    observation_types: list[ObservationType]
    coverage: list[Coverage] = Field(min_length=1)
