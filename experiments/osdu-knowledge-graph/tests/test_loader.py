import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from graph_loader.controls import verify_graph_model
from graph_loader.normalize import normalize_osdu_id, parse_osdu_id
from graph_loader.parser import detect_record_type
from graph_loader.pipeline import build_graph, validate_snapshot


FIXTURE = Path(__file__).parent / "fixtures" / "volve_sample.json"


class LoaderTests(unittest.TestCase):
    def test_normalize_osdu_id_decodes_one_trailing_separator(self):
        self.assertEqual(
            normalize_osdu_id("osdu:master-data--Well:15%2F9-19:"),
            "osdu:master-data--Well:15/9-19",
        )
        self.assertEqual(
            parse_osdu_id("osdu:master-data--Wellbore:NPD-2105"),
            ("master-data", "Wellbore", "NPD-2105"),
        )

    def test_kind_detection_is_exact(self):
        self.assertEqual(
            detect_record_type(
                {"kind": "osdu:wks:work-product-component--WellLog:1.1.0"}
            ),
            "WellLog",
        )
        self.assertIsNone(
            detect_record_type(
                {"kind": "osdu:wks:work-product-component--WellLogExtension:1.1.0"}
            )
        )

    def test_fixture_builds_required_graph_and_core_controls(self):
        graph = build_graph(FIXTURE)
        self.assertEqual(
            graph.report()["nodes"], {"Well": 1, "WellLog": 2, "Wellbore": 5}
        )
        self.assertEqual(
            graph.report()["relationships"],
            {"BELONGS_TO_WELL": 5, "BELONGS_TO_WELLBORE": 2},
        )
        self.assertFalse(graph.fatal_issues)
        results = {item["id"]: item for item in verify_graph_model(graph)}
        # Fixture is the 15/9-19 cluster only; Q6/Q7/Q9/Q10 need the full Volve slice.
        for control_id in ("Q1", "Q2", "Q3", "Q4", "Q5", "Q8"):
            self.assertTrue(
                results[control_id]["passed"],
                results[control_id],
            )
        for control_id in ("Q6", "Q7", "Q9", "Q10"):
            self.assertFalse(results[control_id]["passed"], results[control_id])
        self.assertEqual(len(results), 10)

    def test_duplicate_input_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = FIXTURE.read_text(encoding="utf-8")
            (root / "first.json").write_text(content, encoding="utf-8")
            (root / "second.json").write_text(content, encoding="utf-8")
            graph = build_graph(root)
        self.assertEqual(len(graph.nodes), 8)
        self.assertEqual(len(graph.relationships), 7)
        self.assertEqual(graph.duplicate_nodes, 8)
        self.assertEqual(graph.duplicate_relationships, 7)
        self.assertFalse(graph.fatal_issues)

    def test_missing_parent_is_fatal_and_relationship_is_excluded(self):
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
        records = [
            record
            for record in records
            if record.get("id") != "osdu:master-data--Wellbore:NPD-2105"
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(records), encoding="utf-8")
            graph = build_graph(path)
        self.assertTrue(graph.fatal_issues)
        self.assertTrue(
            any("dangling relationship" in issue.reason for issue in graph.fatal_issues)
        )

    def test_snapshot_checksum_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = FIXTURE.read_bytes()
            path = root / "fixture.json"
            path.write_bytes(content)
            snapshot = {
                "commit": "fixed",
                "counts": {"Well": 1},
                "files": [
                    {
                        "path": "fixture.json",
                        "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                ],
            }
            (root / "SNAPSHOT.json").write_text(
                json.dumps(snapshot), encoding="utf-8"
            )
            lock = {"commit": "fixed", "expected_files": {"Well": 1}}
            with patch("graph_loader.pipeline.data_version", return_value=lock):
                validate_snapshot(root)
                path.write_bytes(content + b" ")
                with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                    validate_snapshot(root)


if __name__ == "__main__":
    unittest.main()
