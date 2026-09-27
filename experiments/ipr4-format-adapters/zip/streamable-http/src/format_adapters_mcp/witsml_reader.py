"""Read one WITSML 1.4.1.1 well object from local XML."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from format_adapters_mcp.contract import envelope
from format_adapters_mcp.formats import WITSML_NS_1311, WITSML_NS_1411, WITSML_NS_20


def _local(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _ns_uri(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None


def _child(elem: ET.Element, name: str) -> ET.Element | None:
    for child in list(elem):
        if _local(child.tag) == name:
            return child
    return None


def _text(elem: ET.Element | None) -> str | None:
    if elem is None or elem.text is None:
        return None
    value = elem.text.strip()
    return value or None


def read_witsml_well(path: str | Path) -> dict[str, Any]:
    source = str(path)
    p = Path(path)
    if not p.is_file():
        return envelope(
            status="error",
            reason="file_not_found",
            format_name="witsml",
            source=source,
        )
    if p.stat().st_size == 0:
        return envelope(
            status="empty",
            reason="empty_file",
            format_name="witsml",
            source=source,
        )
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError as exc:
        return envelope(
            status="corrupt_input",
            reason=str(exc),
            format_name="witsml",
            source=source,
        )

    ns = _ns_uri(root.tag)
    version = root.attrib.get("version") or root.attrib.get("schemaVersion")
    if ns == WITSML_NS_20 or (version or "").startswith("2"):
        return envelope(
            status="unsupported_version",
            reason="witsml_2x",
            format_name="witsml",
            format_version=version or "2.0",
            source=source,
        )
    if ns == WITSML_NS_1311 or version == "1.3.1.1":
        return envelope(
            status="unsupported_version",
            reason="witsml_1.3.1.1",
            format_name="witsml",
            format_version=version or "1.3.1.1",
            source=source,
        )
    if ns not in (WITSML_NS_1411, None) or (version and version != "1.4.1.1"):
        return envelope(
            status="unsupported_version",
            reason="witsml_unexpected_version",
            format_name="witsml",
            format_version=version,
            source=source,
        )

    wells: list[ET.Element] = []
    if _local(root.tag) == "well":
        wells = [root]
    else:
        wells = [child for child in list(root) if _local(child.tag) == "well"]
    if not wells:
        return envelope(
            status="empty",
            reason="no_well_object",
            format_name="witsml",
            format_version="1.4.1.1",
            source=source,
        )

    records = [_describe_well(item) for item in wells]
    data, top_missing = records[0]
    # Unprefixed names belong to the first well, which `status` describes.
    # Later wells are addressed so a gap there stays visible without flipping
    # `status` when the first well itself is complete.
    missing = list(top_missing)
    if len(records) > 1:
        data = {**data, "wells": [item[0] for item in records]}
        for index, (_nested, nested_missing) in enumerate(records[1:], start=1):
            missing.extend(f"wells[{index}].{field}" for field in nested_missing)
    if top_missing:
        return envelope(
            status="incomplete",
            reason="missing_fields",
            format_name="witsml",
            format_version="1.4.1.1",
            source=source,
            data=data,
            missing_fields=missing,
        )
    return envelope(
        status="ok",
        format_name="witsml",
        format_version="1.4.1.1",
        source=source,
        data=data,
        missing_fields=missing,
    )


def _describe_well(well: ET.Element) -> tuple[dict[str, Any], list[str]]:
    name = _text(_child(well, "name"))
    field = _text(_child(well, "field"))
    time_zone = _text(_child(well, "timeZone"))
    elev_el = _child(well, "wellheadElevation")
    elevation_value = _text(elev_el)
    elevation_uom = elev_el.get("uom") if elev_el is not None else None
    uid = well.attrib.get("uid")

    missing: list[str] = []
    data: dict[str, Any] = {
        "uid": uid,
        "name": name,
        "field": field,
        "timeZone": time_zone,
        "wellheadElevation": None,
    }
    if elevation_value is not None and elevation_uom:
        data["wellheadElevation"] = {
            "value": elevation_value,
            "uom": elevation_uom,
        }
    elif elevation_value is not None and not elevation_uom:
        missing.append("wellheadElevation.uom")
        data["wellheadElevation"] = {"value": elevation_value, "uom": None}
    else:
        missing.append("wellheadElevation")

    for optional in (
        "nameLegal",
        "numLicense",
        "numGovt",
        "country",
        "region",
        "block",
        "district",
        "operator",
        "operatorDiv",
        "statusWell",
        "purposeWell",
        "fluidWell",
        "directionWell",
        "wellDatum",
        "dTimSpud",
    ):
        text = _text(_child(well, optional))
        if text is not None:
            data[optional] = text
    for measured in ("groundElevation", "waterDepth", "pcInterest"):
        element = _child(well, measured)
        text = _text(element)
        if text is None:
            continue
        data[measured] = {
            "value": text,
            "uom": element.get("uom") if element is not None else None,
        }
    location = _child(well, "wellLocation")
    if location is not None:
        place: dict[str, Any] = {}
        for coord in ("latitude", "longitude", "easting", "northing"):
            element = _child(location, coord)
            text = _text(element)
            if text is None:
                continue
            place[coord] = {
                "value": text,
                "uom": element.get("uom") if element is not None else None,
            }
        if place:
            data["wellLocation"] = place

    for key, val in (
        ("uid", uid),
        ("name", name),
        ("field", field),
        ("timeZone", time_zone),
    ):
        if not val:
            missing.append(key)
    return data, missing
