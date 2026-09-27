"""Idempotently load a validated package into the owned target graph."""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

from graph_api import GraphClient, GraphHttpError, cypher_literal, validate_target
from prepare import validate_package

ROOT = Path(__file__).parent
MARKER_UID = "urn:extended-graph:import-metadata"
SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_QUERY_ATTEMPTS = 8
MAX_NON_HTTP_FAILURES = 3


def _query(client: GraphClient, graph: str, cypher: str) -> list[dict]:
    non_http_failures = 0
    last_error: Exception | None = None
    for attempt in range(MAX_QUERY_ATTEMPTS):
        try:
            result = client.query(graph, cypher, timeout=120000)
            time.sleep(0.5)
            return result
        except GraphHttpError as exc:
            last_error = exc
            if exc.status == 429 and attempt < MAX_QUERY_ATTEMPTS - 1:
                time.sleep(exc.retry_after or min(60, 10 * (attempt + 1)))
                continue
            raise
        except Exception as exc:
            last_error = exc
            non_http_failures += 1
            if (
                non_http_failures >= MAX_NON_HTTP_FAILURES
                or attempt == MAX_QUERY_ATTEMPTS - 1
            ):
                raise
            time.sleep(1 + attempt)
    if last_error is None:
        raise RuntimeError("query retry loop exited without a result or error")
    raise last_error


def _batches(prefix: str, rows: list[dict], suffix: str, max_url_size: int = 13000):
    batch = []
    for row in rows:
        candidate = batch + [row]
        query = prefix + cypher_literal(candidate) + suffix
        encoded_size = len(urllib.parse.quote(query))
        if not batch and encoded_size > max_url_size:
            raise ValueError(f"one graph row exceeds safe query size: {encoded_size}")
        if batch and encoded_size > max_url_size:
            yield prefix + cypher_literal(batch) + suffix
            batch = [row]
        else:
            batch = candidate
    if batch:
        yield prefix + cypher_literal(batch) + suffix


def _node_queries(label: str, rows: list[dict]):
    shapes = defaultdict(list)
    for row in rows:
        keys = tuple(row)
        if not all(SAFE_NAME.fullmatch(key) for key in keys):
            raise ValueError(f"invalid property in {label}")
        shapes[keys].append([row[key] for key in keys])
    for keys, values in shapes.items():
        uid_index = keys.index("uid")
        assignments = ",".join(
            f"n.{key}=row[{index}]" for index, key in enumerate(keys)
        )
        suffix = (
            f" AS row MERGE (n:Record:{label} {{uid:row[{uid_index}]}}) "
            f"SET {assignments}"
        )
        yield from _batches("UNWIND ", values, suffix)


def _preflight_target(client: GraphClient, target: str) -> dict:
    rows = _query(client, target, "MATCH (n) RETURN count(n) AS count")
    count = int(rows[0]["count"])
    if count == 0:
        return {}
    marker = _query(
        client,
        target,
        f"MATCH (m:ImportMetadata {{uid:{cypher_literal(MARKER_UID)}}}) "
        "RETURN m.managedBy AS managedBy, m.schemaVersion AS schemaVersion, "
        "m.status AS status, m.packageSha256 AS packageSha256, "
        "m.expectedNodes AS expectedNodes, m.expectedEdges AS expectedEdges",
    )
    if (
        not marker
        or marker[0].get("managedBy") != "extended-graph-data"
        or marker[0].get("schemaVersion") != 1
    ):
        raise ValueError(
            f"target graph {target!r} exists but is not owned by this loader"
        )
    return marker[0]


def _ensure_record_uid_index(client: GraphClient, target: str) -> None:
    indexes = _query(
        client,
        target,
        "CALL db.indexes() YIELD label, properties RETURN label, properties",
    )
    exists = any(
        row.get("label") == "Record" and list(row.get("properties") or []) == ["uid"]
        for row in indexes
    )
    if not exists:
        _query(client, target, "CREATE INDEX FOR (n:Record) ON (n.uid)")


def load_package(client: GraphClient, package_path=None) -> dict:
    path = Path(package_path or ROOT / "data" / "processed" / "graph_package.json")
    package = json.loads(path.read_text(encoding="utf-8"))
    validate_package(package)
    target = validate_target(package["targetGraph"])
    package_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    existing = _preflight_target(client, target)
    if (
        existing.get("status") == "complete"
        and existing.get("packageSha256") == package_hash
    ):
        live_nodes = int(
            _query(client, target, "MATCH (n) RETURN count(n) AS count")[0]["count"]
        )
        live_edges = int(
            _query(client, target, "MATCH ()-[r]->() RETURN count(r) AS count")[0][
                "count"
            ]
        )
        if (live_nodes, live_edges) == (len(package["nodes"]), len(package["edges"])):
            return {
                "graph": target,
                "nodes": live_nodes,
                "edges": live_edges,
                "unchanged": True,
            }

    marker = {
        "uid": MARKER_UID,
        "managedBy": "extended-graph-data",
        "schemaVersion": 1,
        "status": "loading",
    }
    _query(
        client,
        target,
        "MERGE (m:Record:ImportMetadata {uid:"
        + cypher_literal(MARKER_UID)
        + "}) SET m += "
        + cypher_literal(marker),
    )
    _query(client, target, "MATCH (p:ProductionRecord) REMOVE p.rawJsonZlibBase64")
    _ensure_record_uid_index(client, target)

    groups = defaultdict(list)
    for node in package["nodes"]:
        label = node["label"]
        if not SAFE_NAME.fullmatch(label):
            raise ValueError(f"invalid label: {label}")
        groups[label].append(node["properties"])
    loaded_nodes = 0
    for label, rows in groups.items():
        for query in _node_queries(label, rows):
            _query(client, target, query)
        loaded_nodes += len(rows)
        print(f"nodes {label}: {len(rows)}")

    edge_groups = defaultdict(list)
    for edge in package["edges"]:
        relationship = edge["type"]
        if not SAFE_NAME.fullmatch(relationship):
            raise ValueError(f"invalid relationship type: {relationship}")
        edge_groups[relationship].append(
            {"source": edge["source"], "target": edge["target"]}
        )
    loaded_edges = 0
    for relationship, rows in edge_groups.items():
        if relationship == "FROM_DATASET":
            _query(
                client,
                target,
                "MATCH (s:Record),(t:Dataset) WHERE s.datasetUid=t.uid "
                "MERGE (s)-[:FROM_DATASET]->(t)",
            )
            loaded_edges += len(rows)
            print(f"relationships {relationship}: {len(rows)}")
            continue
        if relationship == "FOR_ENTITY":
            _query(
                client,
                target,
                "MATCH (s:ProductionRecord),(t:Record) WHERE s.entityId=t.uid "
                "MERGE (s)-[:FOR_ENTITY]->(t)",
            )
            loaded_edges += len(rows)
            print(f"relationships {relationship}: {len(rows)}")
            continue
        prefix = "UNWIND "
        suffix = (
            " AS row MATCH (s:Record {uid:row.source}), (t:Record {uid:row.target}) "
            f"MERGE (s)-[:{relationship}]->(t)"
        )
        for query in _batches(prefix, rows, suffix):
            _query(client, target, query)
        loaded_edges += len(rows)
        print(f"relationships {relationship}: {len(rows)}")

    final = {
        "status": "complete",
        "expectedNodes": loaded_nodes,
        "expectedEdges": loaded_edges,
        "packageSha256": package_hash,
    }
    _query(
        client,
        target,
        f"MATCH (m:ImportMetadata {{uid:{cypher_literal(MARKER_UID)}}}) SET m += "
        + cypher_literal(final),
    )
    return {"graph": target, "nodes": loaded_nodes, "edges": loaded_edges}
