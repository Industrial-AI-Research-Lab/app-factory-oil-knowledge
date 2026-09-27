from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..documents.search import _open_index
from ..errors import ToolFailure
from ..fact_models import EntityFact, Fact, ObservationFact, RelationFact
from . import SCHEMA_VERSION

_SCHEMA = (
    "CREATE TABLE metadata (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)",
    "CREATE TABLE source_status (source_id TEXT PRIMARY KEY, filename TEXT NOT NULL, content_type TEXT NOT NULL, status TEXT NOT NULL)",
    "CREATE TABLE fragments (fragment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source_status(source_id), locator TEXT NOT NULL, text TEXT NOT NULL)",
    "CREATE TABLE nodes (node_id TEXT PRIMARY KEY, type_id TEXT NOT NULL, properties_json TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES source_status(source_id), fragment_id TEXT NOT NULL REFERENCES fragments(fragment_id))",
    "CREATE TABLE relations (relation_id TEXT PRIMARY KEY, type_id TEXT NOT NULL, source_node_id TEXT NOT NULL REFERENCES nodes(node_id), source_type_id TEXT NOT NULL, target_node_id TEXT NOT NULL REFERENCES nodes(node_id), target_type_id TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES source_status(source_id), fragment_id TEXT NOT NULL REFERENCES fragments(fragment_id))",
    "CREATE TABLE observations (observation_id TEXT PRIMARY KEY, type_id TEXT NOT NULL, subject_node_id TEXT NOT NULL REFERENCES nodes(node_id), subject_type_id TEXT NOT NULL, value_json TEXT NOT NULL, unit TEXT, conditions_json TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES source_status(source_id), fragment_id TEXT NOT NULL REFERENCES fragments(fragment_id))",
)


def build_database(
    index_path: Path,
    collection_path: Path,
    facts: list[Fact],
    metadata: dict[str, Any],
) -> dict[str, int]:
    sources, fragments = _source_data(index_path)
    _validate_evidence(facts, fragments)
    connection = sqlite3.connect(collection_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _create_schema(connection)
        connection.executemany(
            "INSERT INTO metadata(key, value_json) VALUES (?, ?)",
            [(key, _json(value)) for key, value in metadata.items()],
        )
        connection.executemany(
            "INSERT INTO source_status(source_id, filename, content_type, status) "
            "VALUES (?, ?, ?, 'indexed')",
            sources,
        )
        connection.executemany(
            "INSERT INTO fragments(fragment_id, source_id, locator, text) "
            "VALUES (?, ?, ?, ?)",
            fragments.values(),
        )
        _insert_facts(connection, facts)
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ToolFailure("COLLECTION_INVALID", "Collection integrity check failed")
        connection.commit()
    except ToolFailure:
        connection.rollback()
        raise
    except (sqlite3.Error, TypeError, ValueError) as exc:
        connection.rollback()
        raise ToolFailure(
            "COLLECTION_INVALID", "Collection could not be built"
        ) from exc
    finally:
        connection.close()
    return {
        "nodes": sum(isinstance(fact, EntityFact) for fact in facts),
        "relations": sum(isinstance(fact, RelationFact) for fact in facts),
        "observations": sum(isinstance(fact, ObservationFact) for fact in facts),
        "fragments": len(fragments),
        "source_status": len(sources),
    }


def _source_data(
    index_path: Path,
) -> tuple[list[tuple[str, str, str]], dict[str, tuple[str, str, str, str]]]:
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_index(index_path)
        sources = [
            tuple(row)
            for row in connection.execute(
                "SELECT source_id, filename, content_type FROM sources ORDER BY source_id"
            )
        ]
        fragments = {
            row[0]: tuple(row)
            for row in connection.execute(
                "SELECT fragment_id, source_id, locator, text FROM fragments "
                "ORDER BY fragment_id"
            )
        }
        return sources, fragments
    except (sqlite3.Error, TypeError, ValueError):
        raise ToolFailure(
            "DOCUMENT_INDEX_INVALID", "Document index is invalid"
        ) from None
    finally:
        if connection is not None:
            connection.close()


def _validate_evidence(
    facts: list[Fact], fragments: dict[str, tuple[str, str, str, str]]
) -> None:
    for fact in facts:
        fragment = fragments.get(fact.evidence.fragment_id)
        if fragment is None or fragment[1] != fact.evidence.source_id:
            raise ToolFailure("FACT_SOURCE_INVALID", "Evidence source is invalid")
        if fact.evidence.text not in fragment[3]:
            raise ToolFailure(
                "FACT_EVIDENCE_INVALID", "Evidence text is not in fragment"
            )


def _create_schema(connection: sqlite3.Connection) -> None:
    for statement in _SCHEMA:
        connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _insert_facts(connection: sqlite3.Connection, facts: list[Fact]) -> None:
    for fact in sorted(facts, key=lambda item: (item.kind, item.id)):
        evidence = fact.evidence
        if isinstance(fact, EntityFact):
            connection.execute(
                "INSERT INTO nodes VALUES (?, ?, ?, ?, ?)",
                (
                    fact.id,
                    fact.type_id,
                    _json(fact.properties),
                    evidence.source_id,
                    evidence.fragment_id,
                ),
            )
        elif isinstance(fact, RelationFact):
            connection.execute(
                "INSERT INTO relations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fact.id,
                    fact.type_id,
                    fact.source_entity_id,
                    fact.source_type_id,
                    fact.target_entity_id,
                    fact.target_type_id,
                    evidence.source_id,
                    evidence.fragment_id,
                ),
            )
        else:
            connection.execute(
                "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fact.id,
                    fact.type_id,
                    fact.subject_entity_id,
                    fact.subject_type_id,
                    _json(fact.value),
                    fact.unit,
                    _json(fact.conditions),
                    evidence.source_id,
                    evidence.fragment_id,
                ),
            )


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
