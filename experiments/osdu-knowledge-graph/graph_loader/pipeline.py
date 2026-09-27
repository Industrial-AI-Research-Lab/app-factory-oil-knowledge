"""Build and persist the validated database-neutral graph artifact."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .model import Graph, LoadIssue
from .parser import parse_file, validate_relationship_targets
from .version import data_version


REQUIRED_LABELS = {"Well", "Wellbore", "WellLog"}
REQUIRED_RELATIONSHIPS = {"BELONGS_TO_WELL", "BELONGS_TO_WELLBORE"}


def build_graph(input_path: Path) -> Graph:
    if input_path.is_dir() and (input_path / "SNAPSHOT.json").exists():
        validate_snapshot(input_path)
    paths = [input_path] if input_path.is_file() else sorted(input_path.rglob("*.json"))
    paths = [path for path in paths if path.name != "SNAPSHOT.json"]
    if not paths:
        raise ValueError(f"no OSDU JSON files found under {input_path}")

    graph = Graph()
    pending_relationships = []
    for path in paths:
        nodes, relationships, issues, records_seen = parse_file(path)
        graph.records_seen += records_seen
        graph.issues.extend(issues)
        for node, source in nodes:
            graph.add_node(node, source)
        pending_relationships.extend(relationships)

    graph.issues.extend(
        validate_relationship_targets(graph.nodes, pending_relationships)
    )
    invalid_relationships = {
        (issue.osdu_id, issue.source)
        for issue in graph.issues
        if issue.reason.startswith(("dangling relationship", "invalid labels"))
    }
    for relationship, source in pending_relationships:
        if (relationship.start_key, source) not in invalid_relationships:
            graph.add_relationship(relationship, source)

    report = graph.report()
    missing_labels = REQUIRED_LABELS - report["nodes"].keys()
    missing_relationships = REQUIRED_RELATIONSHIPS - report["relationships"].keys()
    if missing_labels:
        graph.issues.append(
            LoadIssue(
                "<aggregate>",
                f"missing required entity types: {', '.join(sorted(missing_labels))}",
                fatal=True,
            )
        )
    if missing_relationships:
        graph.issues.append(
            LoadIssue(
                "<aggregate>",
                "missing required relationship types: "
                + ", ".join(sorted(missing_relationships)),
                fatal=True,
            )
        )
    return graph


def validate_snapshot(input_path: Path) -> dict[str, Any]:
    """Verify every fetched byte against the immutable snapshot inventory."""
    snapshot_path = input_path / "SNAPSHOT.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    lock = data_version()
    if snapshot.get("commit") != lock["commit"]:
        raise ValueError("snapshot commit does not match DATA_VERSION.json")
    expected_entries = snapshot.get("files")
    if not isinstance(expected_entries, list):
        raise ValueError("snapshot inventory is missing")
    expected = {
        str(entry.get("path")): entry
        for entry in expected_entries
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    actual_paths = {
        path.relative_to(input_path).as_posix(): path
        for path in input_path.rglob("*.json")
        if path.name != "SNAPSHOT.json"
    }
    if set(actual_paths) != set(expected):
        missing = sorted(set(expected) - set(actual_paths))
        extra = sorted(set(actual_paths) - set(expected))
        raise ValueError(f"snapshot file set mismatch: missing={missing}, extra={extra}")
    for relative, path in actual_paths.items():
        content = path.read_bytes()
        entry = expected[relative]
        if entry.get("bytes") != len(content) or entry.get(
            "sha256"
        ) != hashlib.sha256(content).hexdigest():
            raise ValueError(f"snapshot checksum mismatch: {relative}")
    counts = snapshot.get("counts")
    if counts != lock["expected_files"]:
        raise ValueError("snapshot entity counts do not match DATA_VERSION.json")
    return snapshot


def provenance(input_path: Path) -> dict[str, Any]:
    lock = data_version()
    snapshot_path = input_path / "SNAPSHOT.json" if input_path.is_dir() else None
    snapshot = None
    if snapshot_path is not None and snapshot_path.exists():
        snapshot = validate_snapshot(input_path)
    return {
        "dataset": lock["dataset"],
        "project": lock["project"],
        "commit": lock["commit"],
        "sliceRoot": lock["slice_root"],
        "snapshot": snapshot,
    }


def write_graph(graph: Graph, input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        graph.to_dict(provenance(input_path)),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.", dir=output_path.parent, text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output_path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
