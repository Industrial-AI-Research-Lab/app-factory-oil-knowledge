"""Compare live graph counts, links and numeric totals with the graph package."""

from __future__ import annotations

import json
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from graph_api import GraphClient

ROOT = Path(__file__).parent
TARGET = "osdu-volve-extended"
VOLUME_FIELDS = [
    "oilVolumeSm3", "gasVolumeSm3", "waterVolumeSm3", "waterInjectionVolumeSm3",
    "gasInjectionVolumeSm3", "oilNetVolumeSm3", "oilGrossVolumeSm3",
    "gasNetVolumeSm3", "gasGrossVolumeSm3", "nglNetVolumeSm3",
    "condensateNetVolumeSm3", "condensateGrossVolumeSm3",
    "oilEquivalentNetVolumeSm3", "oilEquivalentGrossVolumeSm3",
]


def _scalar(client: GraphClient, graph: str, query: str, key: str):
    rows = client.query(graph, query, timeout=120000)
    return rows[0][key] if rows else None


def verify(client: GraphClient, package_path=None) -> dict:
    path = Path(package_path or ROOT / "data" / "processed" / "graph_package.json")
    package = json.loads(path.read_text(encoding="utf-8"))
    package_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    expected_labels = Counter(node["label"] for node in package["nodes"])
    expected_types = Counter(edge["type"] for edge in package["edges"])

    live_nodes = int(_scalar(client, TARGET, "MATCH (n) RETURN count(n) AS count", "count"))
    live_edges = int(_scalar(client, TARGET, "MATCH ()-[r]->() RETURN count(r) AS count", "count"))
    labels = {row["label"]: int(row["count"]) for row in client.query(
        TARGET, "MATCH (n) UNWIND labels(n) AS label WITH label,n WHERE label <> 'Record' "
                "RETURN label, count(n) AS count ORDER BY label"
    )}
    types = {row["type"]: int(row["count"]) for row in client.query(
        TARGET, "MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS count ORDER BY type(r)"
    )}
    orphans = int(_scalar(
        client, TARGET,
        "MATCH (p:ProductionRecord) WHERE NOT (p)-[:FOR_ENTITY]->() RETURN count(p) AS count", "count"
    ))
    duplicates = int(_scalar(
        client, TARGET,
        "MATCH (n:Record) WITH n.uid AS uid,count(n) AS copies WHERE copies > 1 RETURN count(uid) AS count",
        "count",
    ) or 0)
    marker_rows = client.query(
        TARGET,
        "MATCH (m:ImportMetadata {uid:'urn:extended-graph:import-metadata'}) "
        "RETURN m.status AS status,m.packageSha256 AS packageSha256,"
        "m.expectedNodes AS expectedNodes,m.expectedEdges AS expectedEdges",
    )
    marker = marker_rows[0] if marker_rows else {}

    expected_sums = defaultdict(float)
    for node in package["nodes"]:
        if node["label"] == "ProductionRecord":
            for field in VOLUME_FIELDS:
                expected_sums[field] += node["properties"].get(field, 0)
    live_sums = {}
    for field in VOLUME_FIELDS:
        live_sums[field] = float(_scalar(
            client, TARGET,
            f"MATCH (p:ProductionRecord) RETURN sum(coalesce(p.{field},0)) AS total", "total"
        ) or 0)

    source_nodes = int(_scalar(client, "osdu-volve", "MATCH (n) RETURN count(n) AS count", "count"))
    source_edges = int(_scalar(client, "osdu-volve", "MATCH ()-[r]->() RETURN count(r) AS count", "count"))
    errors = []
    if live_nodes != len(package["nodes"]): errors.append("node count mismatch")
    if live_edges != len(package["edges"]): errors.append("relationship count mismatch")
    if labels != dict(sorted(expected_labels.items())): errors.append("label counts mismatch")
    if types != dict(sorted(expected_types.items())): errors.append("relationship type counts mismatch")
    if orphans: errors.append(f"orphan production records: {orphans}")
    if duplicates: errors.append(f"duplicate uids: {duplicates}")
    if marker.get("status") != "complete" or marker.get("packageSha256") != package_hash:
        errors.append("import metadata does not match package")
    if (source_nodes, source_edges) != (66, 55): errors.append("source graph changed")
    for field, expected in expected_sums.items():
        if abs(live_sums[field] - expected) > max(1e-6, abs(expected) * 1e-12):
            errors.append(f"volume sum mismatch: {field}")

    report = {
        "verifiedAt": datetime.now(timezone.utc).isoformat(), "ok": not errors,
        "graph": TARGET, "nodes": live_nodes, "edges": live_edges,
        "labels": labels, "relationshipTypes": types, "orphanProductionRecords": orphans,
        "duplicateUids": duplicates, "packageSha256": package_hash, "importMetadata": marker,
        "sourceGraph": {"nodes": source_nodes, "edges": source_edges},
        "volumeSumsSm3": live_sums, "errors": errors,
    }
    output = ROOT / "reports" / "verification.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if errors:
        raise ValueError("; ".join(errors))
    return report
