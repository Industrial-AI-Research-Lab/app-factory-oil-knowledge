"""Download the small, pinned Volve inputs and snapshot the source graph."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from graph_api import GraphClient

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
SOURCES = {
    "volve_production_archive.xlsx": {
        "url": (
            "https://raw.githubusercontent.com/f0nzie/volve_eclipse_reservoir/"
            "0d34eaf1be7003a6c251dea938de3e3738501d71/inst/rawdata/"
            "Volve%20production%20data.xlsx"
        ),
        "sha256": "7e9f7056af9bb89e907ce1b05ec85090e611021e072b6ecd395b5641a0689da9",
    },
    "volve_wellbores.geojson": {
        "url": (
            "https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/"
            "MapServer/201/query?where=wlbField%3D%27VOLVE%27&outFields=*"
            "&outSR=4326&f=geojson"
        ),
        "sha256": "2d547f93b2055d05130ecb4a6bb6ff6a5b5c21de39757df171b3a8bd83ee5906",
    },
    "volve_field.geojson": {
        "url": (
            "https://factmaps.sodir.no/api/rest/services/Factmaps/FactMapsWGS84/"
            "MapServer/502/query?where=fldName%3D%27VOLVE%27&outFields=*"
            "&outSR=4326&f=geojson"
        ),
        "sha256": "055049a0acdfac2e7ec47f6b02c86e19e137c076967042a21bc1fa509bd3f64a",
    },
    "volve_field_monthly.json": {
        "url": (
            "https://factmaps.sodir.no/api/rest/services/DataService/Data/FeatureServer/"
            "7300/query?where=prfInformationCarrier%3D%27VOLVE%27%20AND%20prfMonth%3E0"
            "&outFields=*&returnGeometry=false&orderByFields=prfYear,prfMonth&f=json"
        ),
        "sha256": "350b63bf00cef15691fbc6b1b8c9c5360d69005e20a524b2421b09d81d1ec621",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, path: Path, expected_sha256: str) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "extended-graph-data/1.0"}
    )
    temporary = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open(
            "wb"
        ) as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        checksum = sha256(temporary)
        if checksum != expected_sha256:
            raise ValueError(
                f"pinned source checksum mismatch for {path.name}: {checksum}"
            )
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def download(force: bool = False) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    records = []
    for filename, spec in SOURCES.items():
        path = RAW / filename
        if force or not path.exists():
            _download(spec["url"], path, spec["sha256"])
        checksum = sha256(path)
        if checksum != spec["sha256"]:
            raise ValueError(
                f"pinned source checksum mismatch for {filename}: {checksum}"
            )
        records.append(
            {
                "file": filename,
                "url": spec["url"],
                "bytes": path.stat().st_size,
                "sha256": checksum,
            }
        )
    manifest = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sources": records,
        "notes": (
            "All remote inputs were validated against pinned SHA-256 values before "
            "publication; map and field series originate from official SODIR APIs."
        ),
    }
    (ROOT / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def snapshot_source(client: GraphClient) -> dict:
    nodes = client.query(
        "osdu-volve",
        "MATCH (n) RETURN id(n) AS nodeId, labels(n) AS labels, properties(n) AS properties",
    )
    edges = client.query(
        "osdu-volve",
        "MATCH (a)-[r]->(b) RETURN a.osduId AS source, type(r) AS type, "
        "b.osduId AS target, properties(r) AS properties",
    )
    snapshot = {"nodes": nodes, "edges": edges}
    path = RAW / "source_graph.json"
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    return {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
        "graph": "osdu-volve",
        "nodes": len(nodes),
        "edges": len(edges),
    }


def download_all(client: GraphClient, force: bool = False) -> dict:
    manifest = download(force)
    manifest["sourceGraph"] = snapshot_source(client)
    (ROOT / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def client_from_env(environ: Mapping[str, str] = os.environ) -> GraphClient:
    url = (environ.get("FALKORDB_URL") or "").strip()
    if not url:
        raise ValueError("FALKORDB_URL is required")
    if "FALKORDB_PASSWORD" not in environ:
        raise ValueError("FALKORDB_PASSWORD is required")
    return GraphClient(
        url,
        environ.get("FALKORDB_USERNAME", "default"),
        environ["FALKORDB_PASSWORD"],
    )
