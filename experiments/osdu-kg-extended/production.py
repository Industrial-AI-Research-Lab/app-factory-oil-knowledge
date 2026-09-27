"""Normalize Volve production rows without inventing rates or zeroes."""

from __future__ import annotations

import base64
import json
import math
import zlib
from datetime import date, datetime, timedelta

WORKBOOK_DATASET = "urn:extended-graph:dataset:volve-production-workbook"


def number(value):
    if value is None or isinstance(value, str) and value.strip().upper() in {"", "NULL"}:
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"invalid number: {value!r}")
    return int(result) if result.is_integer() else result


def _integer(value, field: str) -> int:
    parsed = number(value)
    if parsed is None or not float(parsed).is_integer():
        raise ValueError(f"invalid {field}: {value!r}")
    return int(parsed)


def _day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(value))
    return datetime.fromisoformat(str(value)).date()


def _copy_values(target: dict, raw: dict, mapping: dict[str, str]):
    for source, destination in mapping.items():
        value = number(raw.get(source))
        if value is not None:
            target[destination] = value


def encoded_json(raw: dict) -> str:
    normalized = {}
    for key, value in raw.items():
        if value is None:
            continue
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        normalized[key] = value
    content = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(zlib.compress(content, level=9)).decode()


def daily_record(raw: dict, row_number: int) -> dict:
    npd = _integer(raw.get("NPD_WELL_BORE_CODE"), "NPD wellbore code")
    start = _day(raw.get("DATEPRD"))
    result = {
        "uid": f"urn:extended-graph:production:daily:{npd}:{start.isoformat()}",
        "entityId": f"osdu:master-data--Wellbore:NPD-{npd}",
        "datasetUid": WORKBOOK_DATASET,
        "source": "Volve production workbook",
        "granularity": "day",
        "periodStart": start.isoformat(),
        "periodEnd": (start + timedelta(days=1)).isoformat(),
        "sourceRow": row_number,
        "volumeUnit": "Sm3",
    }
    _copy_values(result, raw, {
        "ON_STREAM_HRS": "onStreamHours", "BORE_OIL_VOL": "oilVolumeSm3",
        "BORE_GAS_VOL": "gasVolumeSm3", "BORE_WAT_VOL": "waterVolumeSm3",
        "BORE_WI_VOL": "waterInjectionVolumeSm3",
    })
    for source, destination in {
        "WELL_BORE_CODE": "wellboreCode", "NPD_WELL_BORE_NAME": "wellboreName",
        "NPD_FIELD_NAME": "fieldName", "NPD_FACILITY_NAME": "facilityName",
        "FLOW_KIND": "flowKind", "WELL_TYPE": "wellType",
    }.items():
        if raw.get(source) not in (None, ""):
            result[destination] = str(raw[source]).strip()
    return result


def monthly_record(raw: dict, row_number: int) -> dict:
    npd = _integer(raw.get("NPDCode"), "NPD wellbore code")
    year, month = _integer(raw.get("Year"), "year"), _integer(raw.get("Month"), "month")
    start = date(year, month, 1)
    end = date(year + month // 12, month % 12 + 1, 1)
    result = {
        "uid": f"urn:extended-graph:production:monthly-wellbore:{npd}:{year:04d}-{month:02d}",
        "entityId": f"osdu:master-data--Wellbore:NPD-{npd}",
        "datasetUid": WORKBOOK_DATASET,
        "source": "Volve production workbook",
        "granularity": "month",
        "periodStart": start.isoformat(), "periodEnd": end.isoformat(),
        "sourceRow": row_number, "volumeUnit": "Sm3",
    }
    _copy_values(result, raw, {
        "On Stream": "onStreamHours", "Oil": "oilVolumeSm3", "Gas": "gasVolumeSm3",
        "Water": "waterVolumeSm3", "GI": "gasInjectionVolumeSm3",
        "WI": "waterInjectionVolumeSm3",
    })
    if raw.get("Wellbore name"):
        result["wellboreName"] = str(raw["Wellbore name"]).strip()
    return result
