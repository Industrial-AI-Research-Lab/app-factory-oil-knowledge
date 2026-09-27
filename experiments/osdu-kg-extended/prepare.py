"""Build a deterministic graph package from the downloaded Volve sources."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from graph_api import parse_sse
from production import WORKBOOK_DATASET, daily_record, encoded_json, monthly_record, number
from xlsx_rows import worksheet_rows

ROOT = Path(__file__).parent
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
GEOGRAPHY_DATASET = "urn:extended-graph:dataset:sodir-geography"
FIELD_PRODUCTION_DATASET = "urn:extended-graph:dataset:sodir-field-production"
SOURCE_GRAPH_DATASET = "urn:extended-graph:dataset:osdu-volve"
FIELD_UID = "osdu:master-data--Field:NPD-3420717"


def add_node(nodes: dict, label: str, properties: dict):
    uid = properties.get("uid")
    if not uid:
        raise ValueError("node has no uid")
    node = {"label": label, "properties": properties}
    if uid in nodes and nodes[uid] != node:
        raise ValueError(f"conflicting node uid: {uid}")
    nodes[uid] = node


def validate_package(package: dict):
    identities = set()
    for node in package["nodes"]:
        properties = node["properties"]
        uid = properties.get("uid")
        if not uid or uid in identities:
            raise ValueError(f"missing or duplicate uid: {uid}")
        identities.add(uid)
        if "latitude" in properties and not -90 <= properties["latitude"] <= 90:
            raise ValueError(f"invalid latitude for {uid}")
        if "longitude" in properties and not -180 <= properties["longitude"] <= 180:
            raise ValueError(f"invalid longitude for {uid}")
        if node["label"] == "ProductionRecord":
            if properties["entityId"] not in identities and not any(
                other["properties"].get("uid") == properties["entityId"] for other in package["nodes"]
            ):
                raise ValueError(f"missing production entity: {properties['entityId']}")
            if properties["periodStart"] >= properties["periodEnd"]:
                raise ValueError(f"invalid reporting period: {uid}")
    edge_keys = set()
    for edge in package["edges"]:
        key = (edge["source"], edge["type"], edge["target"])
        if key in edge_keys:
            raise ValueError(f"duplicate edge: {key}")
        edge_keys.add(key)
        if edge["source"] not in identities or edge["target"] not in identities:
            raise ValueError(f"edge has missing endpoint: {key}")
    return package


def _source_graph() -> dict:
    json_path = RAW / "source_graph.json"
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))
    return {
        "nodes": parse_sse((RAW / "base_nodes.sse").read_text(encoding="utf-8")),
        "edges": parse_sse((RAW / "base_edges.sse").read_text(encoding="utf-8")),
    }


def _datasets(nodes: dict):
    for uid, name, url in [
        (SOURCE_GRAPH_DATASET, "OSDU Volve base graph", "graph:osdu-volve"),
        (WORKBOOK_DATASET, "Volve production workbook", "source_manifest.json#volve_production_archive.xlsx"),
        (GEOGRAPHY_DATASET, "SODIR Volve geography", "source_manifest.json#volve_wellbores.geojson"),
        (FIELD_PRODUCTION_DATASET, "SODIR Volve field production", "source_manifest.json#volve_field_monthly.json"),
    ]:
        add_node(nodes, "Dataset", {"uid": uid, "name": name, "source": url})


def _base(nodes: dict, edges: list):
    source = _source_graph()
    for raw in source["nodes"]:
        properties = dict(raw["properties"])
        properties.update(uid=properties["osduId"], datasetUid=SOURCE_GRAPH_DATASET)
        add_node(nodes, raw["labels"][0], properties)
        edges.append({"source": properties["uid"], "type": "FROM_DATASET", "target": SOURCE_GRAPH_DATASET})
    for raw in source["edges"]:
        edges.append({"source": raw["source"], "type": raw["type"], "target": raw["target"]})


def _geography(nodes: dict, edges: list):
    wellbores = json.loads((RAW / "volve_wellbores.geojson").read_text(encoding="utf-8"))["features"]
    if len(wellbores) != 27:
        raise ValueError(f"expected 27 Volve wellbores, got {len(wellbores)}")
    for feature in wellbores:
        raw, (longitude, latitude) = feature["properties"], feature["geometry"]["coordinates"]
        uid = f"osdu:master-data--Wellbore:NPD-{int(raw['wlbNpdidWellbore'])}"
        if uid not in nodes or nodes[uid]["label"] != "Wellbore":
            raise ValueError(f"SODIR wellbore is absent from source graph: {uid}")
        properties = nodes[uid]["properties"]
        properties.update({
            "npdWellboreId": int(raw["wlbNpdidWellbore"]), "latitude": latitude,
            "longitude": longitude, "coordinateReferenceSystem": "EPSG:4326",
            "surfaceLocationWkt": f"POINT ({longitude} {latitude})",
            "sodirPropertiesJsonZlibBase64": encoded_json(raw),
        })
    field = json.loads((RAW / "volve_field.geojson").read_text(encoding="utf-8"))["features"]
    if len(field) != 1:
        raise ValueError(f"expected one Volve field polygon, got {len(field)}")
    feature, raw = field[0], field[0]["properties"]
    ring = feature["geometry"]["coordinates"][0]
    longitudes, latitudes = zip(*ring)
    add_node(nodes, "Field", {
        "uid": FIELD_UID, "osduId": FIELD_UID, "name": raw["fldName"],
        "npdFieldId": int(raw["fldNpdidField"]), "status": raw["fldCurrentActivitySatus"],
        "hydrocarbonType": raw["fldHcType"], "coordinateReferenceSystem": "EPSG:4326",
        "geometryGeoJson": json.dumps(feature["geometry"], separators=(",", ":")),
        "minLongitude": min(longitudes), "maxLongitude": max(longitudes),
        "minLatitude": min(latitudes), "maxLatitude": max(latitudes),
        "datasetUid": GEOGRAPHY_DATASET,
        "sodirPropertiesJsonZlibBase64": encoded_json(raw),
    })
    edges.append({"source": FIELD_UID, "type": "FROM_DATASET", "target": GEOGRAPHY_DATASET})
    for uid, node in nodes.items():
        if node["label"] == "Well":
            edges.append({"source": uid, "type": "IN_FIELD", "target": FIELD_UID})


def _workbook_production(nodes: dict, edges: list):
    path = RAW / "volve_production_archive.xlsx"
    daily = worksheet_rows(path, 1)
    header = next(daily)
    expected = ["DATEPRD", "WELL_BORE_CODE", "NPD_WELL_BORE_CODE", "NPD_WELL_BORE_NAME",
                "NPD_FIELD_CODE", "NPD_FIELD_NAME", "NPD_FACILITY_CODE", "NPD_FACILITY_NAME",
                "ON_STREAM_HRS", "AVG_DOWNHOLE_PRESSURE", "AVG_DOWNHOLE_TEMPERATURE",
                "AVG_DP_TUBING", "AVG_ANNULUS_PRESS", "AVG_CHOKE_SIZE_P", "AVG_CHOKE_UOM",
                "AVG_WHP_P", "AVG_WHT_P", "DP_CHOKE_SIZE", "BORE_OIL_VOL", "BORE_GAS_VOL",
                "BORE_WAT_VOL", "BORE_WI_VOL", "FLOW_KIND", "WELL_TYPE"]
    if header != expected:
        raise ValueError("unexpected Daily Production Data headers")
    count = 0
    for row_number, values in enumerate(daily, 2):
        raw = dict(zip(header, values))
        record = daily_record(raw, row_number)
        _production_node(nodes, edges, record)
        count += 1
    if count != 15634:
        raise ValueError(f"expected 15634 daily rows, got {count}")

    monthly = worksheet_rows(path, 2)
    header = next(monthly)
    expected = ["Wellbore name", "NPDCode", "Year", "Month", "On Stream", "Oil", "Gas", "Water", "GI", "WI"]
    if header != expected:
        raise ValueError("unexpected Monthly Production Data headers")
    count = 0
    for row_number, values in enumerate(monthly, 2):
        raw = dict(zip(header, values))
        if raw.get("NPDCode") is None and raw.get("Year") is None and raw.get("Month") is None:
            continue
        record = monthly_record(raw, row_number)
        _production_node(nodes, edges, record)
        count += 1
    if count != 526:
        raise ValueError(f"expected 526 monthly wellbore rows, got {count}")


def _production_node(nodes: dict, edges: list, record: dict):
    if record["entityId"] not in nodes:
        raise ValueError(f"unknown production entity: {record['entityId']}")
    add_node(nodes, "ProductionRecord", record)
    edges.extend([
        {"source": record["uid"], "type": "FOR_ENTITY", "target": record["entityId"]},
        {"source": record["uid"], "type": "FROM_DATASET", "target": record["datasetUid"]},
    ])


def _field_production(nodes: dict, edges: list):
    features = json.loads((RAW / "volve_field_monthly.json").read_text(encoding="utf-8"))["features"]
    if len(features) != 114:
        raise ValueError(f"expected 114 field months, got {len(features)}")
    mapping = {
        "prfPrdOilNetMillSm3": ("oilNetVolumeSm3", 1_000_000),
        "prfPrdOilGrossMillSm3": ("oilGrossVolumeSm3", 1_000_000),
        "prfPrdGasNetBillSm3": ("gasNetVolumeSm3", 1_000_000_000),
        "prfPrdGasGrossBillSm3": ("gasGrossVolumeSm3", 1_000_000_000),
        "prfPrdNGLNetMillSm3": ("nglNetVolumeSm3", 1_000_000),
        "prfPrdCondensateNetMillSm3": ("condensateNetVolumeSm3", 1_000_000),
        "prfPrdCondensateGrossMillSm3": ("condensateGrossVolumeSm3", 1_000_000),
        "prfPrdOeNetMillSm3": ("oilEquivalentNetVolumeSm3", 1_000_000),
        "prfPrdOeGrossMillSm3": ("oilEquivalentGrossVolumeSm3", 1_000_000),
        "prfPrdProducedWaterInFieldMillS": ("waterVolumeSm3", 1_000_000),
    }
    for row_number, feature in enumerate(features, 1):
        raw = feature["attributes"]
        year, month = int(raw["prfYear"]), int(raw["prfMonth"])
        start = date(year, month, 1)
        end = date(year + month // 12, month % 12 + 1, 1)
        record = {
            "uid": f"urn:extended-graph:production:monthly-field:3420717:{year:04d}-{month:02d}",
            "entityId": FIELD_UID, "datasetUid": FIELD_PRODUCTION_DATASET,
            "source": "SODIR field production API", "granularity": "month",
            "periodStart": start.isoformat(), "periodEnd": end.isoformat(),
            "sourceRow": row_number, "volumeUnit": "Sm3",
        }
        for source, (destination, multiplier) in mapping.items():
            value = number(raw.get(source))
            if value is not None:
                record[destination] = value * multiplier
        investment = number(raw.get("prfInvestmentsMillNOK"))
        if investment is not None:
            record["investmentMillionNok"] = investment
        _production_node(nodes, edges, record)


def build_package() -> dict:
    nodes, edges = {}, []
    add_node(nodes, "ImportMetadata", {
        "uid": "urn:extended-graph:import-metadata", "managedBy": "extended-graph-data",
        "schemaVersion": 1, "status": "prepared",
    })
    _datasets(nodes)
    _base(nodes, edges)
    _geography(nodes, edges)
    _workbook_production(nodes, edges)
    _field_production(nodes, edges)
    package = {"schemaVersion": 1, "targetGraph": "osdu-volve-extended",
               "nodes": list(nodes.values()), "edges": edges}
    validate_package(package)
    package["summary"] = {
        "nodes": len(package["nodes"]), "edges": len(edges),
        "labels": dict(Counter(node["label"] for node in package["nodes"])),
        "relationshipTypes": dict(Counter(edge["type"] for edge in edges)),
    }
    return package


def prepare() -> dict:
    package = build_package()
    PROCESSED.mkdir(parents=True, exist_ok=True)
    path = PROCESSED / "graph_package.json"
    path.write_text(json.dumps(package, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return package["summary"]
