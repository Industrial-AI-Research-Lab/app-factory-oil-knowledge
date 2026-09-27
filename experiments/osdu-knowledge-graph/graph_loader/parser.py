"""Parse and validate Well, Wellbore and WellLog OSDU WKS records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterator

from .model import LoadIssue, Node, Relationship
from .normalize import first_text, normalize_osdu_id, parse_osdu_id, scalar_or_json


SUPPORTED = {
    "Well": "master-data",
    "Wellbore": "master-data",
    "WellLog": "work-product-component",
}
KIND_RE = re.compile(
    r"^[^:]+:wks:(?P<entity_group>[a-z0-9-]+)--"
    r"(?P<entity_type>[A-Za-z0-9-]+):(?P<version>\d+\.\d+\.\d+)$"
)


def iter_records(document: Any) -> Iterator[dict[str, Any]]:
    """Yield WKS records from direct arrays or OSDU manifest envelopes."""
    if isinstance(document, list):
        for item in document:
            yield from iter_records(item)
    elif isinstance(document, dict):
        kind = document.get("kind")
        if kind == "osdu:wks:Manifest:1.0.0":
            for value in document.values():
                if isinstance(value, (list, dict)):
                    yield from iter_records(value)
        elif detect_record_type(document) is not None or _looks_supported(document):
            yield document
        elif isinstance(kind, str):
            # A valid but out-of-scope WKS record (Dataset, WorkProduct, etc.).
            return
        else:
            for value in document.values():
                if isinstance(value, (list, dict)):
                    yield from iter_records(value)


def detect_record_type(record: dict[str, Any]) -> str | None:
    kind = record.get("kind")
    if not isinstance(kind, str):
        return None
    match = KIND_RE.fullmatch(kind.strip())
    if match is None:
        return None
    entity_type = match.group("entity_type")
    expected_group = SUPPORTED.get(entity_type)
    return (
        entity_type
        if expected_group is not None
        and match.group("entity_group") == expected_group
        else None
    )


def _looks_supported(record: dict[str, Any]) -> bool:
    candidates = (str(record.get("id") or ""), str(record.get("kind") or ""))
    return any(
        f"--{entity_type}" in value
        for entity_type in SUPPORTED
        for value in candidates
    )


def parse_file(
    path: Path,
) -> tuple[list[tuple[Node, str]], list[tuple[Relationship, str]], list[LoadIssue], int]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], [], [LoadIssue(str(path), f"invalid JSON: {exc}", fatal=True)], 0

    records = list(iter_records(document))
    if not records:
        return (
            [],
            [],
            [LoadIssue(str(path), "no supported OSDU records found", fatal=True)],
            0,
        )

    nodes: list[tuple[Node, str]] = []
    relationships: list[tuple[Relationship, str]] = []
    issues: list[LoadIssue] = []
    for index, record in enumerate(records):
        source = f"{path}#{index}"
        node, edges, issue = parse_record(record, source)
        if issue is not None:
            issues.append(issue)
        elif node is not None:
            nodes.append((node, source))
            relationships.extend((edge, source) for edge in edges)
    return nodes, relationships, issues, len(records)


def parse_record(
    record: dict[str, Any], source: str
) -> tuple[Node | None, list[Relationship], LoadIssue | None]:
    record_type = detect_record_type(record)
    if record_type is None:
        return None, [], LoadIssue(source, "unsupported or malformed OSDU kind")

    data = record.get("data")
    if not isinstance(data, dict):
        return None, [], LoadIssue(
            source,
            "missing required object: data",
            normalize_osdu_id(record.get("id")),
        )

    kind = str(record["kind"]).strip()
    osdu_id = normalize_osdu_id(record.get("id"))
    if record_type == "WellLog" and (
        osdu_id is None or osdu_id.startswith("surrogate-key:")
    ):
        osdu_id = _well_log_fallback_id(data, kind)
    elif not _id_matches_type(osdu_id, record_type):
        return None, [], LoadIssue(
            source, f"id does not identify a {record_type}", osdu_id
        )
    if osdu_id is None:
        return None, [], LoadIssue(source, "missing stable record identifier")

    name = first_text(data.get("FacilityName"), data.get("Name"))
    if name is None:
        return None, [], LoadIssue(source, "missing required entity name", osdu_id)

    properties = _properties(record_type, data, osdu_id, kind, name)
    node = Node(label=record_type, key=osdu_id, properties=properties)
    relationships: list[Relationship] = []

    if record_type == "Wellbore":
        parent_id = normalize_osdu_id(data.get("WellID"))
        if not _id_matches_type(parent_id, "Well"):
            return None, [], LoadIssue(
                source, "missing or invalid data.WellID", osdu_id
            )
        relationships.append(Relationship("BELONGS_TO_WELL", osdu_id, parent_id))
    elif record_type == "WellLog":
        parent_id = normalize_osdu_id(data.get("WellboreID"))
        if not _id_matches_type(parent_id, "Wellbore"):
            return None, [], LoadIssue(
                source, "missing or invalid data.WellboreID", osdu_id
            )
        relationships.append(
            Relationship("BELONGS_TO_WELLBORE", osdu_id, parent_id)
        )
    return node, relationships, None


def validate_relationship_targets(
    nodes: dict[str, Node], relationships: list[tuple[Relationship, str]]
) -> list[LoadIssue]:
    expected_labels = {
        "BELONGS_TO_WELL": ("Wellbore", "Well"),
        "BELONGS_TO_WELLBORE": ("WellLog", "Wellbore"),
    }
    issues: list[LoadIssue] = []
    for relationship, source in relationships:
        start = nodes.get(relationship.start_key)
        end = nodes.get(relationship.end_key)
        labels = expected_labels.get(relationship.type)
        if start is None or end is None:
            issues.append(
                LoadIssue(
                    source,
                    f"dangling relationship {relationship.type}: "
                    f"{relationship.start_key} -> {relationship.end_key}",
                    relationship.start_key,
                    fatal=True,
                )
            )
        elif labels is None or (start.label, end.label) != labels:
            issues.append(
                LoadIssue(
                    source,
                    f"invalid labels for {relationship.type}: "
                    f"{start.label} -> {end.label}",
                    relationship.start_key,
                    fatal=True,
                )
            )
    return issues


def _id_matches_type(value: str | None, expected_type: str) -> bool:
    parsed = parse_osdu_id(value)
    expected_group = SUPPORTED.get(expected_type)
    return (
        parsed is not None
        and parsed[0] == expected_group
        and parsed[1] == expected_type
    )


def _properties(
    record_type: str,
    data: dict[str, Any],
    osdu_id: str,
    kind: str,
    name: str,
) -> dict[str, Any]:
    fields = {
        "Well": ("FacilityID",),
        "Wellbore": ("FacilityID", "SequenceNumber"),
        "WellLog": ("TopMeasuredDepth", "BottomMeasuredDepth"),
    }[record_type]
    properties: dict[str, Any] = {
        "osduId": osdu_id,
        "name": name,
        "kind": kind,
    }
    for field in fields:
        value = scalar_or_json(data.get(field))
        if value is not None:
            properties[field[0].lower() + field[1:]] = value
    return properties


def _well_log_fallback_id(data: dict[str, Any], kind: str) -> str | None:
    wellbore_id = normalize_osdu_id(data.get("WellboreID"))
    name = first_text(data.get("Name"))
    if not _id_matches_type(wellbore_id, "Wellbore") or name is None:
        return None
    identity = json.dumps(
        [wellbore_id, name, kind], ensure_ascii=False, separators=(",", ":")
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"urn:osdu-demo:WellLog:{digest}"
