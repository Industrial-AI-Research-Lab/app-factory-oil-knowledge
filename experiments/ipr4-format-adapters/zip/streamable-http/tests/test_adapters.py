"""Unit tests for synthetic SEG-Y / WITSML / SCADA adapters."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from format_adapters_mcp.formats import SEGY_TEXT_MARKER, WITSML_NS_1411  # noqa: E402
from format_adapters_mcp.generate_fixtures import (  # noqa: E402
    SEGY_JOB_ID,
    SEGY_N_SAMPLES,
    WELL_NAME,
    WELL_UID,
    generate,
)
from format_adapters_mcp.scada_mock import ScadaMock  # noqa: E402
from format_adapters_mcp.scada_reader import read_scada_snapshot  # noqa: E402
from format_adapters_mcp.segy_reader import read_segy_headers  # noqa: E402
from format_adapters_mcp.witsml_reader import read_witsml_well  # noqa: E402


class FixturesMixin(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = Path(tempfile.mkdtemp(prefix="ipr4-fixtures-"))
        generate(cls.fixtures)
        cls.segy = cls.fixtures / "segy"
        cls.witsml = cls.fixtures / "witsml"


class SegyTests(FixturesMixin):
    def test_ok_rev1_headers(self) -> None:
        result = read_segy_headers(self.segy / "ok_rev1.sgy")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["format"], "segy")
        self.assertTrue(result["format_version"].startswith("1."))
        self.assertTrue(result["data"]["textual_marker_present"])
        self.assertIn(SEGY_TEXT_MARKER, result["data"]["textual_header"])
        self.assertIn("SYN-LINE-01", result["data"]["textual_header"])
        self.assertEqual(result["data"]["binary_header"]["MeasurementSystem"], 1)
        self.assertEqual(result["data"]["binary_header"]["JobID"], SEGY_JOB_ID)
        self.assertEqual(result["data"]["binary_header"]["Samples"], SEGY_N_SAMPLES)
        self.assertGreater(Path(self.segy / "ok_rev1.sgy").stat().st_size, 100_000)

    def test_ok_rev0(self) -> None:
        result = read_segy_headers(self.segy / "ok_rev0.sgy")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["format_version"].startswith("0."))

    def test_unsupported_rev2(self) -> None:
        result = read_segy_headers(self.segy / "unsupported_rev2.sgy")
        self.assertEqual(result["status"], "unsupported_version")
        self.assertTrue(result["format_version"].startswith("2."))

    def test_corrupt(self) -> None:
        result = read_segy_headers(self.segy / "corrupt.sgy")
        self.assertEqual(result["status"], "corrupt_input")

    def test_empty(self) -> None:
        result = read_segy_headers(self.segy / "empty.sgy")
        self.assertEqual(result["status"], "empty")

    def test_missing_file(self) -> None:
        result = read_segy_headers(self.segy / "no-such.sgy")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "file_not_found")


class WitsmlTests(FixturesMixin):
    def test_ok_well(self) -> None:
        result = read_witsml_well(self.witsml / "ok_well_1411.xml")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["format_version"], "1.4.1.1")
        self.assertEqual(result["data"]["uid"], WELL_UID)
        self.assertEqual(result["data"]["name"], WELL_NAME)
        self.assertEqual(result["data"]["wellheadElevation"]["uom"], "m")
        self.assertEqual(result["data"]["wellheadElevation"]["value"], "12.5")
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(len(result["data"]["wells"]), 5)
        self.assertEqual(result["data"]["wells"][1]["uid"], "SYN-WELL-002")
        self.assertEqual(result["data"]["wells"][1]["wellheadElevation"]["value"], "15.2")
        self.assertEqual(result["data"]["wells"][2]["uid"], "SYN-WELL-003")
        self.assertEqual(result["data"]["wells"][2]["wellheadElevation"]["value"], "9.0")
        self.assertEqual(result["data"]["operator"], "Synthetic Operator")
        self.assertEqual(result["data"]["wellLocation"]["easting"]["value"], "500000")

    def test_incomplete_no_elevation(self) -> None:
        result = read_witsml_well(self.witsml / "incomplete_no_elevation.xml")
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("wellheadElevation", result["missing_fields"])
        self.assertIsNone(result["data"]["wellheadElevation"])

    def test_incomplete_elevation_without_uom_is_not_zero(self) -> None:
        result = read_witsml_well(self.witsml / "incomplete_elevation_no_uom.xml")
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("wellheadElevation.uom", result["missing_fields"])
        self.assertEqual(result["data"]["wellheadElevation"]["value"], "12.5")
        self.assertIsNone(result["data"]["wellheadElevation"]["uom"])

    def test_empty_no_well(self) -> None:
        result = read_witsml_well(self.witsml / "empty_no_well.xml")
        self.assertEqual(result["status"], "empty")

    def test_unsupported_1311(self) -> None:
        result = read_witsml_well(self.witsml / "unsupported_1311.xml")
        self.assertEqual(result["status"], "unsupported_version")
        self.assertEqual(result["format_version"], "1.3.1.1")

    def test_unsupported_20(self) -> None:
        result = read_witsml_well(self.witsml / "unsupported_20.xml")
        self.assertEqual(result["status"], "unsupported_version")

    def test_corrupt(self) -> None:
        result = read_witsml_well(self.witsml / "corrupt.xml")
        self.assertEqual(result["status"], "corrupt_input")

    def test_later_well_gap_is_indexed_and_keeps_status(self) -> None:
        path = self.witsml / "second_well_no_uom.xml"
        path.write_text(
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    f'<wells xmlns="{WITSML_NS_1411}" version="1.4.1.1">',
                    '  <well uid="SYN-WELL-001">',
                    "    <name>Synthetic Demo Well</name>",
                    "    <field>Synthetic Field</field>",
                    "    <timeZone>+03:00</timeZone>",
                    '    <wellheadElevation uom="m">12.5</wellheadElevation>',
                    "  </well>",
                    '  <well uid="SYN-WELL-003">',
                    "    <name>Synthetic South</name>",
                    "    <field>Synthetic Field</field>",
                    "    <timeZone>+03:00</timeZone>",
                    "    <wellheadElevation>9.0</wellheadElevation>",
                    "  </well>",
                    "</wells>",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = read_witsml_well(path)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["missing_fields"], ["wells[1].wellheadElevation.uom"])
        self.assertEqual(result["data"]["wellheadElevation"]["uom"], "m")
        self.assertEqual(result["data"]["wells"][1]["wellheadElevation"]["value"], "9.0")
        self.assertIsNone(result["data"]["wells"][1]["wellheadElevation"]["uom"])

    def test_first_well_gap_stays_incomplete_and_lists_later_gaps(self) -> None:
        path = self.witsml / "both_wells_incomplete.xml"
        path.write_text(
            "\n".join(
                [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    f'<wells xmlns="{WITSML_NS_1411}" version="1.4.1.1">',
                    '  <well uid="SYN-WELL-001">',
                    "    <name>Synthetic Demo Well</name>",
                    "    <field>Synthetic Field</field>",
                    "    <timeZone>+03:00</timeZone>",
                    "  </well>",
                    '  <well uid="SYN-WELL-003">',
                    "    <name>Synthetic South</name>",
                    "    <field>Synthetic Field</field>",
                    "    <timeZone>+03:00</timeZone>",
                    "    <wellheadElevation>9.0</wellheadElevation>",
                    "  </well>",
                    "</wells>",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        result = read_witsml_well(path)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(
            result["missing_fields"],
            ["wellheadElevation", "wells[1].wellheadElevation.uom"],
        )


class ScadaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mock = ScadaMock(port=0)
        cls.mock.start()
        cls.base = cls.mock.base_url
        cls._prev_scada_base = os.environ.get("SCADA_BASE_URL")
        os.environ["SCADA_BASE_URL"] = cls.base

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._prev_scada_base is None:
            os.environ.pop("SCADA_BASE_URL", None)
        else:
            os.environ["SCADA_BASE_URL"] = cls._prev_scada_base
        cls.mock.stop()

    def test_ok_snapshot(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/telemetry")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["tag"], "SYN.WELL01.WHP")
        self.assertEqual(result["data"]["value"], 12.5)
        self.assertEqual(result["data"]["unit"], "bar")
        self.assertEqual(result["data"]["quality"], "good")
        self.assertEqual(result["data"]["well_uid"], "SYN-WELL-001")
        self.assertEqual(len(result["data"]["points"]), 12)
        self.assertEqual(result["data"]["points"][0]["value"], 12.5)
        self.assertEqual(result["data"]["points"][4]["tag"], "SYN.WELL02.WHP")
        self.assertEqual(result["data"]["points"][4]["value"], 9.8)
        self.assertEqual(result["data"]["points"][7]["tag"], "SYN.WELL03.WHP")
        self.assertEqual(result["data"]["points"][7]["value"], 18.4)

    def test_null_is_not_zero(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/telemetry/null")
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(result["data"]["value"])
        self.assertIsNot(result["data"]["value"], 0)

    def test_zero_is_legal_measurement(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/telemetry/zero")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["value"], 0.0)

    def test_incomplete_missing_unit(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/telemetry/incomplete")
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("unit", result["missing_fields"])
        self.assertEqual(result["data"]["value"], 64.0)

    def test_later_point_gap_is_indexed_and_keeps_status(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/telemetry/nested-gap")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["missing_fields"], ["points[1].unit"])
        self.assertEqual(result["data"]["value"], 12.5)
        self.assertIsNone(result["data"]["points"][1]["value"])
        self.assertEqual(result["data"]["points"][1]["tag"], "SYN.WELL03.WHP")

    def test_unsupported_v2(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v2/telemetry")
        self.assertEqual(result["status"], "unsupported_version")
        self.assertEqual(result["reason"], "scada_api_version")
        self.assertEqual(result["format_version"], "2")

    def test_rejects_url_outside_mock(self) -> None:
        result = read_scada_snapshot("http://10.0.15.21:8081/v2/telemetry")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "url_not_allowed")

    def test_rejects_other_port(self) -> None:
        result = read_scada_snapshot("http://127.0.0.1:9/v1/telemetry")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "url_not_allowed")

    def test_http_error(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/error")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "http_503")

    def test_timeout(self) -> None:
        result = read_scada_snapshot(f"{self.base}/v1/hang", timeout_s=0.2)
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["reason"], "timeout")


class SplitSourceTests(FixturesMixin):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        data_root = Path(__file__).resolve().parents[3] / "synthetic-data"
        sys.path.insert(0, str(data_root))
        os.environ["DATA_DIR"] = str(cls.fixtures)
        from serve import Handler  # noqa: E402

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for name in ("SEGY_BASE_URL", "WITSML_BASE_URL", "SCADA_BASE_URL", "DATA_DIR"):
            os.environ.pop(name, None)

    def test_tools_read_the_separate_data_service(self) -> None:
        from format_adapters_mcp import server

        os.environ["SEGY_BASE_URL"] = self.base
        os.environ["WITSML_BASE_URL"] = self.base
        os.environ["SCADA_BASE_URL"] = self.base
        segy = server.read_segy_headers("segy/ok_rev1.sgy")
        self.assertEqual(segy["status"], "ok")
        self.assertEqual(segy["data"]["binary_header"]["JobID"], SEGY_JOB_ID)
        self.assertEqual(segy["source"], f"{self.base}/segy/ok_rev1.sgy")
        well = server.read_witsml_well("witsml/ok_well_1411.xml")
        self.assertEqual(well["status"], "ok")
        self.assertEqual(well["data"]["uid"], WELL_UID)
        snapshot = server.read_scada_snapshot("")
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["data"]["value"], 12.5)
        self.assertEqual(snapshot["source"], f"{self.base}/v1/telemetry")

    def test_unset_base_url_does_not_read_a_local_copy(self) -> None:
        from format_adapters_mcp import server

        os.environ.pop("SEGY_BASE_URL", None)
        result = server.read_segy_headers("segy/ok_rev1.sgy")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "source_not_configured")

    def test_relative_escape_is_rejected(self) -> None:
        from format_adapters_mcp import server

        os.environ["SEGY_BASE_URL"] = self.base
        result = server.read_segy_headers("../secret.sgy")
        self.assertEqual(result["reason"], "path_outside_source")


class WriteSurfaceTests(unittest.TestCase):
    def test_tool_functions_are_read_only(self) -> None:
        from format_adapters_mcp import server

        for name in ("read_segy_headers", "read_witsml_well", "read_scada_snapshot"):
            self.assertTrue(callable(getattr(server, name)))
        write_names = [n for n in dir(server) if "write" in n.lower()]
        self.assertEqual(write_names, [])

    def test_readers_do_not_import_the_fixture_writer(self) -> None:
        package = Path(__file__).resolve().parents[1] / "src" / "format_adapters_mcp"
        for name in ("segy_reader.py", "witsml_reader.py", "server.py", "scada_reader.py"):
            text = (package / name).read_text(encoding="utf-8")
            self.assertNotIn("generate_fixtures", text)
        for dockerfile in (
            package.parents[1] / "Dockerfile",
            package.parents[1] / "stdio" / "Dockerfile",
        ):
            text = dockerfile.read_text(encoding="utf-8")
            self.assertIn("rm -f ./src/format_adapters_mcp/generate_fixtures.py", text)


if __name__ == "__main__":
    unittest.main()
