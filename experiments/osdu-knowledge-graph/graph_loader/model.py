"""Database-neutral, deterministic graph representation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    label: str
    key: str
    properties: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Relationship:
    type: str
    start_key: str
    end_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LoadIssue:
    source: str
    reason: str
    osdu_id: str | None = None
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)
    relationships: dict[tuple[str, str, str], Relationship] = field(
        default_factory=dict
    )
    issues: list[LoadIssue] = field(default_factory=list)
    records_seen: int = 0
    duplicate_nodes: int = 0
    duplicate_relationships: int = 0

    def add_node(self, node: Node, source: str) -> None:
        existing = self.nodes.get(node.key)
        if existing is None:
            self.nodes[node.key] = node
            return
        self.duplicate_nodes += 1
        if existing != node:
            self.issues.append(
                LoadIssue(
                    source=source,
                    osdu_id=node.key,
                    reason="duplicate key has conflicting label or properties",
                    fatal=True,
                )
            )

    def add_relationship(self, relationship: Relationship, source: str) -> None:
        key = (relationship.type, relationship.start_key, relationship.end_key)
        existing = self.relationships.get(key)
        if existing is None:
            self.relationships[key] = relationship
            return
        self.duplicate_relationships += 1
        if existing != relationship:
            self.issues.append(
                LoadIssue(
                    source=source,
                    osdu_id=relationship.start_key,
                    reason="duplicate relationship key has conflicting data",
                    fatal=True,
                )
            )

    @property
    def fatal_issues(self) -> list[LoadIssue]:
        return [issue for issue in self.issues if issue.fatal]

    def report(self) -> dict[str, Any]:
        nodes: dict[str, int] = {}
        relationships: dict[str, int] = {}
        for node in self.nodes.values():
            nodes[node.label] = nodes.get(node.label, 0) + 1
        for relationship in self.relationships.values():
            relationships[relationship.type] = relationships.get(
                relationship.type, 0
            ) + 1
        return {
            "recordsSeen": self.records_seen,
            "nodes": dict(sorted(nodes.items())),
            "relationships": dict(sorted(relationships.items())),
            "duplicateNodes": self.duplicate_nodes,
            "duplicateRelationships": self.duplicate_relationships,
            "rejected": len(self.issues),
            "fatalIssues": len(self.fatal_issues),
        }

    def to_dict(self, provenance: dict[str, Any]) -> dict[str, Any]:
        return {
            "version": 1,
            "provenance": provenance,
            "nodes": [
                node.to_dict()
                for node in sorted(
                    self.nodes.values(), key=lambda item: (item.label, item.key)
                )
            ],
            "relationships": [
                relationship.to_dict()
                for relationship in sorted(
                    self.relationships.values(),
                    key=lambda item: (item.type, item.start_key, item.end_key),
                )
            ],
            "report": self.report(),
            "issues": [issue.to_dict() for issue in self.issues],
        }
