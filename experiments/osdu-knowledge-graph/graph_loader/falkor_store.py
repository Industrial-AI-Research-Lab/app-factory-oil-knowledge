"""Idempotent FalkorDB writer and deterministic database verifier."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .controls import CONTROLS, CONTROL_PARAMS
from .model import Graph


GRAPH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")
NODE_LABELS = ("Well", "Wellbore", "WellLog")
RELATIONSHIP_TYPES = ("BELONGS_TO_WELL", "BELONGS_TO_WELLBORE")
NODE_QUERY = {
    label: f"""MERGE (n:{label} {{osduId: $osduId}})
SET n += $properties
RETURN n.osduId AS osduId"""
    for label in NODE_LABELS
}
RELATIONSHIP_QUERY = {
    "BELONGS_TO_WELL": """MATCH (a:Wellbore {osduId: $startKey})
MATCH (b:Well {osduId: $endKey})
MERGE (a)-[r:BELONGS_TO_WELL]->(b)
RETURN count(r) AS relationships""",
    "BELONGS_TO_WELLBORE": """MATCH (a:WellLog {osduId: $startKey})
MATCH (b:Wellbore {osduId: $endKey})
MERGE (a)-[r:BELONGS_TO_WELLBORE]->(b)
RETURN count(r) AS relationships""",
}


@dataclass(frozen=True)
class DatabaseReport:
    graph_name: str
    before_nodes: dict[str, int]
    after_nodes: dict[str, int]
    before_relationships: dict[str, int]
    after_relationships: dict[str, int]
    created_nodes: int
    matched_nodes: int
    created_relationships: int
    matched_relationships: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "graphName": self.graph_name,
            "before": {
                "nodes": self.before_nodes,
                "relationships": self.before_relationships,
            },
            "after": {
                "nodes": self.after_nodes,
                "relationships": self.after_relationships,
            },
            "createdNodes": self.created_nodes,
            "matchedNodes": self.matched_nodes,
            "createdRelationships": self.created_relationships,
            "matchedRelationships": self.matched_relationships,
        }


def connect(url: str):
    try:
        from falkordb import FalkorDB
    except ImportError as exc:
        raise RuntimeError(
            "FalkorDB client is not installed; install requirements.txt"
        ) from exc
    return FalkorDB.from_url(url)


def load_graph(database: Any, graph_name: str, source: Graph) -> DatabaseReport:
    _validate_graph_name(graph_name)
    if source.fatal_issues:
        raise ValueError("refusing to load a graph with fatal validation issues")
    graph = database.select_graph(graph_name)
    graph_exists = graph_name in set(database.list_graphs())
    before_nodes, before_relationships = (
        database_counts(graph)
        if graph_exists
        else (
            {label: 0 for label in NODE_LABELS},
            {kind: 0 for kind in RELATIONSHIP_TYPES},
        )
    )

    for node in sorted(source.nodes.values(), key=lambda item: (item.label, item.key)):
        query = NODE_QUERY.get(node.label)
        if query is None:
            raise ValueError(f"unsupported node label: {node.label}")
        graph.query(
            query,
            {
                "osduId": node.key,
                "properties": dict(node.properties),
            },
        )

    for relationship in sorted(
        source.relationships.values(),
        key=lambda item: (item.type, item.start_key, item.end_key),
    ):
        query = RELATIONSHIP_QUERY.get(relationship.type)
        if query is None:
            raise ValueError(f"unsupported relationship: {relationship.type}")
        result = graph.query(
            query,
            {
                "startKey": relationship.start_key,
                "endKey": relationship.end_key,
            },
        )
        if _first_scalar(result) != 1:
            raise RuntimeError(
                f"relationship endpoints missing for {relationship.start_key}"
            )

    after_nodes, after_relationships = database_counts(graph)
    expected_nodes = source.report()["nodes"]
    expected_relationships = source.report()["relationships"]
    if after_nodes != expected_nodes or after_relationships != expected_relationships:
        raise RuntimeError(
            "database counts differ from validated artifact: "
            f"expected nodes={expected_nodes}, relationships={expected_relationships}; "
            f"got nodes={after_nodes}, relationships={after_relationships}"
        )

    created_nodes = sum(after_nodes.values()) - sum(before_nodes.values())
    created_relationships = sum(after_relationships.values()) - sum(
        before_relationships.values()
    )
    return DatabaseReport(
        graph_name=graph_name,
        before_nodes=before_nodes,
        after_nodes=after_nodes,
        before_relationships=before_relationships,
        after_relationships=after_relationships,
        created_nodes=created_nodes,
        matched_nodes=len(source.nodes) - created_nodes,
        created_relationships=created_relationships,
        matched_relationships=len(source.relationships) - created_relationships,
    )


def database_counts(graph: Any) -> tuple[dict[str, int], dict[str, int]]:
    nodes = {
        label: int(
            _first_scalar(graph.ro_query(f"MATCH (n:{label}) RETURN count(n)"))
            or 0
        )
        for label in NODE_LABELS
    }
    relationships = {
        relationship_type: int(
            _first_scalar(
                graph.ro_query(
                    f"MATCH ()-[r:{relationship_type}]->() RETURN count(r)"
                )
            )
            or 0
        )
        for relationship_type in RELATIONSHIP_TYPES
    }
    return nodes, relationships


def verify_database(database: Any, graph_name: str) -> list[dict[str, Any]]:
    _validate_graph_name(graph_name)
    graph = database.select_graph(graph_name)
    results = []
    for control in CONTROLS:
        actual = _rows(graph.ro_query(control.query, CONTROL_PARAMS))
        results.append(
            {
                "id": control.id,
                "question": control.question,
                "passed": actual == control.expected,
                "expected": control.expected,
                "actual": actual,
            }
        )
    return results


def _rows(result: Any) -> list[dict[str, Any]]:
    header = getattr(result, "header", None) or []
    columns = []
    for entry in header:
        if isinstance(entry, (list, tuple)) and entry:
            columns.append(str(entry[-1]))
        else:
            columns.append(str(entry))
    return [
        dict(zip(columns, row))
        for row in (getattr(result, "result_set", None) or [])
    ]


def _first_scalar(result: Any) -> Any:
    rows = getattr(result, "result_set", None) or []
    return rows[0][0] if rows and rows[0] else None


def _validate_graph_name(graph_name: str) -> None:
    if not GRAPH_NAME_RE.fullmatch(graph_name):
        raise ValueError("graph name must contain only letters, digits, '_' or '-'")
