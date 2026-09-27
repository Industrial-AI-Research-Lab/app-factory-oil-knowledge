from __future__ import annotations

import asyncio
import hashlib
import json
import math
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .documents.search import _open_index
from .errors import ToolFailure
from .fact_models import (
    EntityFact,
    Fact,
    ObservationFact,
    RelationFact,
    parse_facts,
)
from .http_io import download_bytes, temporary_call_directory, upload_bytes
from .ontology import ValidateFactBatchResult
from .ontology.models import OntologyDocument
from .ontology.validation import _validate_references, _validate_unique_ids


async def validate_fact_batch_impl(
    index_url: str,
    expected_index_sha256: str,
    ontology_url: str,
    expected_ontology_sha256: str,
    facts: object,
    upload_url: str,
) -> ValidateFactBatchResult:
    parsed_facts = parse_facts(facts)
    settings = Settings.from_env()
    index_bytes = await download_bytes(
        index_url,
        max_bytes=settings.max_download_bytes,
        expected_sha256=expected_index_sha256,
    )
    ontology_bytes = await download_bytes(
        ontology_url,
        max_bytes=settings.max_download_bytes,
        expected_sha256=expected_ontology_sha256,
    )
    ontology = _parse_ontology(ontology_bytes)
    _validate_facts(parsed_facts, ontology)
    with temporary_call_directory() as directory:
        index_path = directory / "documents.sqlite3"
        await asyncio.to_thread(index_path.write_bytes, index_bytes)
        fragments = await asyncio.to_thread(
            _load_evidence_fragments,
            index_path,
            parsed_facts,
        )
    _validate_evidence(parsed_facts, fragments)
    payload = _canonical_batch(
        parsed_facts,
        ontology,
        hashlib.sha256(index_bytes).hexdigest(),
        hashlib.sha256(ontology_bytes).hexdigest(),
    )
    upload = await upload_bytes(upload_url, payload, content_type="application/json")
    return ValidateFactBatchResult(
        schema_version=1,
        index_sha256=hashlib.sha256(index_bytes).hexdigest(),
        ontology_sha256=hashlib.sha256(ontology_bytes).hexdigest(),
        ontology_id=ontology.ontology_id,
        ontology_version=ontology.version,
        fact_count=len(parsed_facts),
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
    )


def _parse_ontology(payload: bytes) -> OntologyDocument:
    try:
        value = json.loads(
            payload.decode("utf-8"),
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            parse_float=_finite_float,
        )
        ontology = OntologyDocument.model_validate(value)
        _validate_unique_ids(ontology)
        _validate_references(ontology)
        return ontology
    except ToolFailure:
        raise
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError, ValidationError):
        raise ToolFailure(
            "ONTOLOGY_SCHEMA_INVALID",
            "Approved ontology does not match schema version 1",
        ) from None


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(value)
    return number


def _validate_facts(
    facts: list[Fact],
    ontology: OntologyDocument,
) -> None:
    entities = {item.id: item for item in ontology.entity_types}
    relations = {item.id: item for item in ontology.relation_types}
    observations = {item.id: item for item in ontology.observation_types}
    batch_entities = {
        fact.id: fact.type_id for fact in facts if isinstance(fact, EntityFact)
    }
    for fact in facts:
        if isinstance(fact, EntityFact):
            definition = entities.get(fact.type_id)
            if definition is None:
                raise ToolFailure("FACT_TYPE_UNKNOWN", "Entity type is not declared")
            _validate_entity_properties(fact, definition.properties)
        elif isinstance(fact, RelationFact):
            definition = relations.get(fact.type_id)
            if definition is None:
                raise ToolFailure("FACT_TYPE_UNKNOWN", "Relation type is not declared")
            if (
                fact.source_type_id != definition.source_type
                or fact.target_type_id != definition.target_type
                or batch_entities.get(fact.source_entity_id, fact.source_type_id)
                != fact.source_type_id
                or batch_entities.get(fact.target_entity_id, fact.target_type_id)
                != fact.target_type_id
            ):
                raise ToolFailure(
                    "FACT_ENDPOINT_INVALID", "Relation endpoints are invalid"
                )
        else:
            definition = observations.get(fact.type_id)
            if definition is None:
                raise ToolFailure(
                    "FACT_TYPE_UNKNOWN", "Observation type is not declared"
                )
            if (
                fact.subject_type_id != definition.subject_type
                or batch_entities.get(fact.subject_entity_id, fact.subject_type_id)
                != fact.subject_type_id
            ):
                raise ToolFailure(
                    "FACT_ENDPOINT_INVALID", "Observation subject is invalid"
                )
            _validate_observation(fact, definition)


def _validate_entity_properties(fact: EntityFact, definitions: list[Any]) -> None:
    declared = {item.id: item for item in definitions}
    if any(not _canonical_id(key) or key not in declared for key in fact.properties):
        raise ToolFailure("FACT_TYPE_UNKNOWN", "Entity property is not declared")
    if any(item.required and item.id not in fact.properties for item in definitions):
        raise ToolFailure("FACT_SCHEMA_INVALID", "Required entity property is missing")
    if any(
        not _matches_value(fact.properties[key], declared[key].value_type)
        for key in fact.properties
    ):
        raise ToolFailure("FACT_SCHEMA_INVALID", "Entity property value is invalid")


def _validate_observation(fact: ObservationFact, definition: Any) -> None:
    conditions = {item.id: item for item in definition.condition_properties}
    if not _matches_value(fact.value, definition.value_type):
        raise ToolFailure("FACT_MEASUREMENT_INVALID", "Observation value is invalid")
    if definition.unit_required and fact.unit is None:
        raise ToolFailure("FACT_MEASUREMENT_INVALID", "Observation unit is required")
    if any(not _canonical_id(key) or key not in conditions for key in fact.conditions):
        raise ToolFailure("FACT_TYPE_UNKNOWN", "Observation condition is not declared")
    if any(
        not _matches_value(fact.conditions[key], conditions[key].value_type)
        for key in fact.conditions
    ):
        raise ToolFailure(
            "FACT_MEASUREMENT_INVALID", "Observation condition is invalid"
        )
    if any(
        item.required and item.id not in fact.conditions for item in conditions.values()
    ):
        raise ToolFailure("FACT_MEASUREMENT_INVALID", "Required condition is missing")


def _matches_value(value: object, value_type: str) -> bool:
    if value_type == "string":
        return isinstance(value, str)
    if value_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if value_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        return isinstance(value, int) or math.isfinite(value)
    if value_type == "boolean":
        return isinstance(value, bool)
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _canonical_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and "\x00" not in value
        and value == unicodedata.normalize("NFC", value)
    )


def _load_evidence_fragments(
    path: Path,
    facts: list[Fact],
) -> dict[str, tuple[str, str]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_index(path)
        ids = sorted({fact.evidence.fragment_id for fact in facts})
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            "SELECT f.fragment_id, f.source_id, f.text "
            "FROM fragments f JOIN sources s ON s.source_id = f.source_id "
            f"WHERE f.fragment_id IN ({placeholders})",
            ids,
        ).fetchall()
        if any(
            not isinstance(row[field], str)
            for row in rows
            for field in ("fragment_id", "source_id", "text")
        ):
            raise TypeError("Evidence fragment fields must be text")
        return {row["fragment_id"]: (row["source_id"], row["text"]) for row in rows}
    except ToolFailure:
        raise
    except (sqlite3.Error, TypeError, ValueError):
        raise ToolFailure(
            "DOCUMENT_INDEX_INVALID", "Document index is invalid"
        ) from None
    finally:
        if connection is not None:
            connection.close()


def _validate_evidence(
    facts: list[Fact],
    fragments: dict[str, tuple[str, str]],
) -> None:
    for fact in facts:
        fragment = fragments.get(fact.evidence.fragment_id)
        if fragment is None or fragment[0] != fact.evidence.source_id:
            raise ToolFailure("FACT_SOURCE_INVALID", "Evidence source is invalid")
        if fact.evidence.text not in fragment[1]:
            raise ToolFailure(
                "FACT_EVIDENCE_INVALID",
                f"Evidence text of fact {fact.id!r} is not in fragment: "
                f"{fact.evidence.text[:80]!r}. Quote one contiguous span of the fragment",
            )


def _canonical_batch(
    facts: list[Fact],
    ontology: OntologyDocument,
    index_sha256: str,
    ontology_sha256: str,
) -> bytes:
    value = {
        "schema_version": 1,
        "index_sha256": index_sha256,
        "ontology_sha256": ontology_sha256,
        "ontology_id": ontology.ontology_id,
        "ontology_version": ontology.version,
        "facts": sorted(
            (fact.model_dump(mode="json") for fact in facts),
            key=lambda item: (item["kind"], item["id"]),
        ),
    }
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
