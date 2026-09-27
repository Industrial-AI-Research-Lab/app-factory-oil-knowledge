"""Write tiny synthetic SEG-Y / WITSML fixtures (not production data)."""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

try:
    from format_adapters_mcp.formats import (
        SEGY_TEXT_MARKER,
        WITSML_NS_1311,
        WITSML_NS_1411,
        WITSML_NS_20,
    )
except ImportError:  # the data image runs this file beside formats.py, outside the package
    from formats import (  # type: ignore[import-not-found]
        SEGY_TEXT_MARKER,
        WITSML_NS_1311,
        WITSML_NS_1411,
        WITSML_NS_20,
    )

SEGY_JOB_ID = 42
SEGY_INTERVAL_US = 2000
SEGY_SAMPLE_FORMAT = 5  # IEEE float32
SEGY_N_TRACES = 240
SEGY_N_SAMPLES = 1000
WELL_UID = "SYN-WELL-001"
WELL_NAME = "Synthetic Demo Well"
WELL_FIELD = "Synthetic Field"
WELL_TZ = "+03:00"
WELL_ELEVATION = "12.5"
WELL_ELEVATION_UOM = "m"

_PAD_WELLS = (
    {
        "uid": "SYN-WELL-002",
        "name": "Synthetic North",
        "status": "active",
        "purpose": "production",
        "fluid": "oil",
        "elevation": "15.2",
        "easting": "500350",
        "northing": "6600420",
    },
    {
        "uid": "SYN-WELL-003",
        "name": "Synthetic South",
        "status": "active",
        "purpose": "production",
        "fluid": "gas",
        "elevation": "9.0",
        "easting": "499640",
        "northing": "6599600",
    },
    {
        "uid": "SYN-WELL-004",
        "name": "Synthetic East",
        "status": "suspended",
        "purpose": "exploration",
        "fluid": "dry",
        "elevation": "21.4",
        "easting": "500800",
        "northing": "6600100",
    },
    {
        "uid": "SYN-WELL-005",
        "name": "Synthetic West",
        "status": "active",
        "purpose": "injection",
        "fluid": "water",
        "elevation": "6.8",
        "easting": "499200",
        "northing": "6600080",
    },
)


def default_fixtures_dir() -> Path:
    # zip/streamable-http/fixtures  (this file lives in src/format_adapters_mcp/)
    return Path(__file__).resolve().parents[2] / "fixtures"


def _textual_header(title: str) -> bytes:
    cards: list[str] = [
        f"C 1 {title}",
        f"C 2 {SEGY_TEXT_MARKER}  SYNTHETIC 2D LINE  NOT FIELD DATA",
        "C 3 CLIENT SYNTHETIC OPERATOR",
        "C 4 LINE NAME SYN-LINE-01   AREA SYNTHETIC FIELD",
        "C 5 COUNTRY RU   YEAR 2026   CREW SYN-01",
        f"C 6 CDP RANGE 1-{SEGY_N_TRACES}   FOLD 1   SORT CDP",
        f"C 7 SAMPLES {SEGY_N_SAMPLES}   INTERVAL {SEGY_INTERVAL_US} MICROSEC",
        "C 8 SAMPLE FORMAT 5 IEEE FLOAT32   MEASUREMENT METERS",
        "C 9 REEL 1   PROCESSING SYNTHETIC DEMO",
        "C10 DATUM 0 M   REPLACEMENT VELOCITY 2000 M/S",
        "C11 RECORD LENGTH 2.0 S   FILTER NONE",
        "C12 COORDINATE UNITS METERS   CRS LOCAL SYNTHETIC GRID",
        "C13 FIRST EASTING 500000   FIRST NORTHING 6600000",
        "C14 INLINE 100   CROSSLINE 1 TO 240",
        "C15 SOURCE SYNTHETIC WAVELET   NO NAVIGATION P1/90",
        "C16 TRACE HEADER BYTES 181-184 SOURCE X",
        "C17 TRACE HEADER BYTES 185-188 SOURCE Y",
        "C18 TRACE HEADER BYTES 189-192 INLINE",
        "C19 TRACE HEADER BYTES 193-196 CROSSLINE",
        "C20 THIS REEL CONTAINS HEADERS AND A SHORT LINE ONLY",
        "C21 NO RAW FIELD RECORDS ARE STORED IN THIS FILE",
        "C22 VALUES ARE GENERATED FOR ADAPTER TESTS",
        "C23 DO NOT USE FOR INTERPRETATION OR DRILLING",
    ]
    while len(cards) < 39:
        n = len(cards) + 1
        cards.append(f"C{n:2d} ")
    cards.append("C40 END TEXTUAL HEADER")
    blob = b"".join(card[:80].ljust(80).encode("ascii") for card in cards[:40])
    if len(blob) != 3200:
        raise RuntimeError(f"textual header must be 3200 bytes, got {len(blob)}")
    return blob


def _put_u16(buf: bytearray, spec_offset: int, value: int) -> None:
    i = spec_offset - 3201
    buf[i : i + 2] = struct.pack(">H", value & 0xFFFF)


def _put_u32(buf: bytearray, spec_offset: int, value: int) -> None:
    i = spec_offset - 3201
    buf[i : i + 4] = struct.pack(">I", value & 0xFFFFFFFF)


def write_segy_headers(
    path: Path,
    *,
    title: str,
    revision_code: int,
) -> None:
    """Headers plus a short synthetic line. Readers still use headers only."""
    binary = bytearray(400)
    _put_u32(binary, 3201, SEGY_JOB_ID)
    _put_u32(binary, 3205, 1)
    _put_u32(binary, 3209, 1)
    _put_u16(binary, 3213, 1)
    _put_u16(binary, 3215, 0)
    _put_u16(binary, 3217, SEGY_INTERVAL_US)
    _put_u16(binary, 3219, SEGY_INTERVAL_US)
    _put_u16(binary, 3221, SEGY_N_SAMPLES)
    _put_u16(binary, 3223, SEGY_N_SAMPLES)
    _put_u16(binary, 3225, SEGY_SAMPLE_FORMAT)
    _put_u16(binary, 3227, 1)
    _put_u16(binary, 3229, 4)
    _put_u16(binary, 3255, 1)
    _put_u16(binary, 3501, revision_code)
    _put_u16(binary, 3503, 1)
    _put_u16(binary, 3505, 0)
    traces = bytearray()
    for trace_no in range(1, SEGY_N_TRACES + 1):
        header = bytearray(240)
        header[0:4] = struct.pack(">i", trace_no)
        header[4:8] = struct.pack(">i", trace_no)
        header[20:24] = struct.pack(">i", trace_no)
        header[28:30] = struct.pack(">h", 1)
        header[114:116] = struct.pack(">h", SEGY_N_SAMPLES)
        header[116:118] = struct.pack(">h", SEGY_INTERVAL_US)
        header[180:184] = struct.pack(">i", 500000 + trace_no * 25)
        header[184:188] = struct.pack(">i", 6600000)
        header[188:192] = struct.pack(">i", 100)
        header[192:196] = struct.pack(">i", trace_no)
        traces += header
        for sample_no in range(SEGY_N_SAMPLES):
            traces += struct.pack(">f", float((trace_no + sample_no) % 17) * 0.1)
    path.write_bytes(_textual_header(title) + bytes(binary) + traces)


def _simple_well_xml(well: dict[str, str]) -> str:
    return "\n".join(
        [
            f'  <well uid="{well["uid"]}">',
            f'    <name>{well["name"]}</name>',
            f"    <field>{WELL_FIELD}</field>",
            f"    <timeZone>{WELL_TZ}</timeZone>",
            f'    <wellheadElevation uom="m">{well["elevation"]}</wellheadElevation>',
            "    <country>RU</country>",
            "    <operator>Synthetic Operator</operator>",
            f'    <statusWell>{well["status"]}</statusWell>',
            f'    <purposeWell>{well["purpose"]}</purposeWell>',
            f'    <fluidWell>{well["fluid"]}</fluidWell>',
            "    <directionWell>vertical</directionWell>",
            "    <wellDatum>KB</wellDatum>",
            "    <wellLocation>",
            f'      <easting uom="m">{well["easting"]}</easting>',
            f'      <northing uom="m">{well["northing"]}</northing>',
            "    </wellLocation>",
            "  </well>",
        ]
    )


def write_witsml_well(
    path: Path,
    *,
    namespace: str,
    version: str,
    uid: str | None = WELL_UID,
    name: str | None = WELL_NAME,
    field: str | None = WELL_FIELD,
    time_zone: str | None = WELL_TZ,
    elevation: str | None = WELL_ELEVATION,
    elevation_uom: str | None = WELL_ELEVATION_UOM,
    include_well: bool = True,
) -> None:
    if namespace == WITSML_NS_20:
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Well xmlns="{WITSML_NS_20}" schemaVersion="2.0" '
            'uuid="00000000-0000-0000-0000-000000000000">\n'
            f"  <Citation><Title>{WELL_NAME}</Title></Citation>\n"
            "</Well>\n"
        )
        path.write_text(body, encoding="utf-8")
        return

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<wells xmlns="{namespace}" version="{version}">',
    ]
    if include_well:
        uid_attr = f' uid="{uid}"' if uid else ""
        parts.append(f"  <well{uid_attr}>")
        if name is not None:
            parts.append(f"    <name>{name}</name>")
        if field is not None:
            parts.append(f"    <field>{field}</field>")
        if time_zone is not None:
            parts.append(f"    <timeZone>{time_zone}</timeZone>")
        if elevation is not None:
            if elevation_uom:
                parts.append(
                    f'    <wellheadElevation uom="{elevation_uom}">'
                    f"{elevation}</wellheadElevation>"
                )
            else:
                parts.append(
                    f"    <wellheadElevation>{elevation}</wellheadElevation>"
                )
        if uid == WELL_UID and version == "1.4.1.1" and namespace == WITSML_NS_1411 and elevation_uom:
            parts.extend(
                [
                    "    <nameLegal>Synthetic Demo Well 1</nameLegal>",
                    "    <numLicense>SYN-LIC-2026-01</numLicense>",
                    "    <numGovt>SYN-GOV-001</numGovt>",
                    "    <country>RU</country>",
                    "    <region>Synthetic Region</region>",
                    "    <block>SYN-BLOCK-A</block>",
                    "    <district>Synthetic District</district>",
                    "    <operator>Synthetic Operator</operator>",
                    "    <operatorDiv>Exploration</operatorDiv>",
                    "    <pcInterest uom=\"%\">100</pcInterest>",
                    "    <statusWell>active</statusWell>",
                    "    <purposeWell>exploration</purposeWell>",
                    "    <fluidWell>dry</fluidWell>",
                    "    <directionWell>vertical</directionWell>",
                    '    <groundElevation uom="m">18.0</groundElevation>',
                    '    <waterDepth uom="m">0</waterDepth>',
                    "    <wellDatum>KB</wellDatum>",
                    "    <dTimSpud>2026-09-01T06:00:00.000Z</dTimSpud>",
                    "    <wellLocation>",
                    "      <latitude uom=\"dega\">60.0</latitude>",
                    "      <longitude uom=\"dega\">50.0</longitude>",
                    "      <easting uom=\"m\">500000</easting>",
                    "      <northing uom=\"m\">6600000</northing>",
                    "    </wellLocation>",
                ]
            )
        parts.append("  </well>")
        if uid == WELL_UID and version == "1.4.1.1" and namespace == WITSML_NS_1411 and elevation_uom:
            for extra in _PAD_WELLS:
                parts.append(_simple_well_xml(extra))
    parts.append("</wells>")
    parts.append("")
    path.write_text("\n".join(parts), encoding="utf-8")


def generate(fixtures_dir: Path | None = None) -> Path:
    root = fixtures_dir or default_fixtures_dir()
    segy = root / "segy"
    witsml = root / "witsml"
    segy.mkdir(parents=True, exist_ok=True)
    witsml.mkdir(parents=True, exist_ok=True)

    write_segy_headers(
        segy / "ok_rev1.sgy",
        title="SYNTHETIC SEG-Y REV 1",
        revision_code=0x0100,
    )
    write_segy_headers(
        segy / "ok_rev0.sgy",
        title="SYNTHETIC SEG-Y REV 0",
        revision_code=0x0000,
    )
    write_segy_headers(
        segy / "unsupported_rev2.sgy",
        title="SYNTHETIC SEG-Y REV 2 UNSUPPORTED",
        revision_code=0x0200,
    )
    (segy / "corrupt.sgy").write_bytes(b"NOT-A-SEGY-FILE" + b"\x00" * 80)
    (segy / "empty.sgy").write_bytes(b"")

    write_witsml_well(witsml / "ok_well_1411.xml", namespace=WITSML_NS_1411, version="1.4.1.1")
    write_witsml_well(
        witsml / "incomplete_no_elevation.xml",
        namespace=WITSML_NS_1411,
        version="1.4.1.1",
        elevation=None,
        elevation_uom=None,
    )
    write_witsml_well(
        witsml / "incomplete_elevation_no_uom.xml",
        namespace=WITSML_NS_1411,
        version="1.4.1.1",
        elevation_uom=None,
    )
    write_witsml_well(
        witsml / "empty_no_well.xml",
        namespace=WITSML_NS_1411,
        version="1.4.1.1",
        include_well=False,
    )
    write_witsml_well(
        witsml / "unsupported_1311.xml",
        namespace=WITSML_NS_1311,
        version="1.3.1.1",
    )
    write_witsml_well(
        witsml / "unsupported_20.xml",
        namespace=WITSML_NS_20,
        version="2.0",
    )
    (witsml / "corrupt.xml").write_text("<wells><well>not-closed", encoding="utf-8")
    return root


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic fixtures")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    root = generate(args.out)
    print(f"fixtures_ok {root}")


if __name__ == "__main__":
    main()
