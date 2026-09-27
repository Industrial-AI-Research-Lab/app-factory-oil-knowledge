from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from document_test_support import build_single_document


def ontology_fixture() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "ontology_id": "surfactant-knowledge",
        "version": "1.0.0",
        "base_version": None,
        "entity_types": [
            {
                "id": "recipe",
                "label": "Recipe",
                "properties": [
                    {
                        "id": "recipe.name",
                        "label": "Name",
                        "value_type": "string",
                        "required": True,
                    }
                ],
            },
            {
                "id": "component",
                "label": "Component",
                "properties": [
                    {
                        "id": "component.name",
                        "label": "Name",
                        "value_type": "string",
                        "required": True,
                    }
                ],
            },
        ],
        "relation_types": [
            {
                "id": "contains_component",
                "label": "Contains component",
                "source_type": "recipe",
                "target_type": "component",
            }
        ],
        "observation_types": [
            {
                "id": "interfacial_tension",
                "label": "Interfacial tension",
                "subject_type": "recipe",
                "value_type": "number",
                "unit_required": True,
                "condition_properties": [
                    {
                        "id": "interfacial_tension.temperature_c",
                        "label": "Temperature",
                        "value_type": "number",
                        "required": False,
                    }
                ],
            }
        ],
        "coverage": [
            {
                "question_id": "compare-recipes",
                "schema_element_ids": [
                    "recipe",
                    "interfacial_tension",
                    "interfacial_tension.temperature_c",
                ],
            }
        ],
    }


async def indexed_fragment(
    httpx_mock,
    payload: bytes = b"Sample A contains Component A at 12.4 mN/m and 75 C.",
) -> tuple[bytes, str, str]:
    index_bytes = await build_single_document(
        httpx_mock,
        payload,
        content_type="text/plain",
        filename="source.txt",
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "index.sqlite3"
        path.write_bytes(index_bytes)
        connection = sqlite3.connect(path)
        try:
            fragment_id, text = connection.execute(
                "SELECT fragment_id, text FROM fragments"
            ).fetchone()
        finally:
            connection.close()
    return index_bytes, fragment_id, text


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fact_fixture(fragment_id: str, evidence_text: str) -> dict[str, Any]:
    return {
        "kind": "entity",
        "id": "recipe:sample-a",
        "type_id": "recipe",
        "properties": {"recipe.name": "Sample A"},
        "evidence": {
            "source_id": "source",
            "fragment_id": fragment_id,
            "text": evidence_text,
        },
    }


def valid_facts(fragment_id: str, evidence_text: str) -> list[dict[str, Any]]:
    evidence = {
        "source_id": "source",
        "fragment_id": fragment_id,
        "text": evidence_text,
    }
    return [
        fact_fixture(fragment_id, evidence_text),
        {
            "kind": "entity",
            "id": "component:sample-a",
            "type_id": "component",
            "properties": {"component.name": "Component A"},
            "evidence": evidence,
        },
        {
            "kind": "relation",
            "id": "relation:sample-a-component-a",
            "type_id": "contains_component",
            "source_entity_id": "recipe:sample-a",
            "source_type_id": "recipe",
            "target_entity_id": "component:sample-a",
            "target_type_id": "component",
            "evidence": evidence,
        },
        {
            "kind": "observation",
            "id": "observation:sample-a-ift-75c",
            "type_id": "interfacial_tension",
            "subject_entity_id": "recipe:sample-a",
            "subject_type_id": "recipe",
            "value": 12.4,
            "unit": "mN/m",
            "conditions": {"interfacial_tension.temperature_c": 75},
            "evidence": evidence,
        },
    ]
