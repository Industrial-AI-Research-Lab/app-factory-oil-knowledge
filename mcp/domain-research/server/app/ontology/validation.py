from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from ..errors import ToolFailure
from ..http_io import upload_bytes
from . import ValidateOntologyResult
from .models import OntologyDocument, SemanticVersion

_EXPECTED_BASE_VERSION = TypeAdapter(SemanticVersion | None)


async def validate_ontology_impl(
    ontology: object,
    upload_url: str,
    expected_base_version: str | None = None,
) -> ValidateOntologyResult:
    try:
        document = OntologyDocument.model_validate(ontology)
    except ValidationError:
        raise ToolFailure(
            "ONTOLOGY_SCHEMA_INVALID",
            "Ontology does not match schema version 1",
        ) from None
    _validate_unique_ids(document)
    _validate_references(document)
    try:
        _EXPECTED_BASE_VERSION.validate_python(expected_base_version, strict=True)
    except ValidationError:
        raise ToolFailure(
            "INPUT_INVALID",
            "expected_base_version must be null or a MAJOR.MINOR.PATCH version",
        ) from None
    if (
        document.base_version != expected_base_version
        or document.base_version == document.version
    ):
        raise ToolFailure(
            "ONTOLOGY_VERSION_CONFLICT",
            "Ontology base_version does not match the approved version",
        )
    canonical = canonical_ontology(document)
    upload = await upload_bytes(
        upload_url,
        canonical,
        content_type="application/json",
    )
    return ValidateOntologyResult(
        schema_version=1,
        ontology_id=document.ontology_id,
        version=document.version,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
    )


def _validate_unique_ids(document: OntologyDocument) -> None:
    schema_ids: list[str] = []
    for entity in document.entity_types:
        schema_ids.append(entity.id)
        schema_ids.extend(item.id for item in entity.properties)
    for relation in document.relation_types:
        schema_ids.append(relation.id)
    for observation in document.observation_types:
        schema_ids.append(observation.id)
        schema_ids.extend(item.id for item in observation.condition_properties)
    question_ids = [coverage.question_id for coverage in document.coverage]
    if len(schema_ids) != len(set(schema_ids)) or len(question_ids) != len(
        set(question_ids)
    ):
        raise ToolFailure(
            "ONTOLOGY_ID_DUPLICATE",
            "Ontology IDs must be globally unique",
        )


def _validate_references(document: OntologyDocument) -> None:
    entity_ids = {entity.id for entity in document.entity_types}
    for relation in document.relation_types:
        if (
            relation.source_type not in entity_ids
            or relation.target_type not in entity_ids
        ):
            raise ToolFailure(
                "ONTOLOGY_REFERENCE_INVALID",
                "Relation endpoints must reference declared entity types",
            )
    for observation in document.observation_types:
        if observation.subject_type not in entity_ids:
            raise ToolFailure(
                "ONTOLOGY_REFERENCE_INVALID",
                "Observation subjects must reference declared entity types",
            )
    schema_ids = set(entity_ids)
    for entity in document.entity_types:
        schema_ids.update(item.id for item in entity.properties)
    schema_ids.update(item.id for item in document.relation_types)
    for observation in document.observation_types:
        schema_ids.add(observation.id)
        schema_ids.update(item.id for item in observation.condition_properties)
    if any(
        not coverage.schema_element_ids
        or any(item not in schema_ids for item in coverage.schema_element_ids)
        for coverage in document.coverage
    ):
        raise ToolFailure(
            "ONTOLOGY_REFERENCE_INVALID",
            "Coverage must reference declared schema elements",
        )


def canonical_ontology(document: OntologyDocument) -> bytes:
    value = document.model_dump(mode="json")
    value["entity_types"] = sorted(value["entity_types"], key=lambda item: item["id"])
    for entity in value["entity_types"]:
        entity["properties"] = sorted(entity["properties"], key=lambda item: item["id"])
    value["relation_types"] = sorted(
        value["relation_types"], key=lambda item: item["id"]
    )
    value["observation_types"] = sorted(
        value["observation_types"], key=lambda item: item["id"]
    )
    for observation in value["observation_types"]:
        observation["condition_properties"] = sorted(
            observation["condition_properties"], key=lambda item: item["id"]
        )
    value["coverage"] = sorted(value["coverage"], key=lambda item: item["question_id"])
    for coverage in value["coverage"]:
        coverage["schema_element_ids"] = sorted(coverage["schema_element_ids"])
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
