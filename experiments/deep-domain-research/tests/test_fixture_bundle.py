import hashlib
import json
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOTS = ("data", "evaluation", "negative")


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FixtureBundleTest(TestCase):
    def setUp(self):
        self.lock_path = ROOT / "package-lock.json"
        self.lock = load_json(self.lock_path)

    def test_lock_covers_every_fixture_and_no_runtime_file(self):
        self.assertEqual(self.lock["scope"]["included_roots"], list(FIXTURE_ROOTS))
        actual = sorted(
            path.relative_to(ROOT).as_posix()
            for root in FIXTURE_ROOTS
            for path in (ROOT / root).rglob("*")
            if path.is_file()
        )
        locked = sorted(entry["path"] for entry in self.lock["files"])
        self.assertEqual(locked, actual)
        for entry in self.lock["files"]:
            path = ROOT / entry["path"]
            self.assertEqual(entry["size_bytes"], path.stat().st_size, entry["path"])
            self.assertEqual(entry["sha256"], sha256(path), entry["path"])

    def test_components_keep_web_evaluation_out_of_knowledge_inputs(self):
        components = self.lock["components"]
        self.assertEqual(
            components["web_evaluation"]["role"], "offline_provider_replay"
        )
        self.assertFalse(components["web_evaluation"]["replaces_live_search"])
        self.assertEqual(
            components["knowledge_corpus"]["role"], "demo_3_attachment_input"
        )
        web_paths = set(components["web_evaluation"]["paths"])
        knowledge_paths = set(components["knowledge_corpus"]["paths"])
        self.assertTrue(web_paths)
        self.assertTrue(knowledge_paths)
        self.assertTrue(web_paths.isdisjoint(knowledge_paths))

    def test_knowledge_evaluation_is_not_an_attachment_input(self):
        components = self.lock["components"]
        self.assertEqual(
            components["knowledge_corpus"]["paths"],
            ["data/knowledge"],
        )
        self.assertEqual(
            components["knowledge_evaluation"],
            {
                "role": "holdout_evaluation",
                "paths": [
                    "evaluation/knowledge-checks.json",
                    "evaluation/knowledge-questions.json",
                ],
            },
        )

    def test_canonical_terminology_is_the_only_dictionary_set(self):
        terminology = self.lock["components"]["canonical_terminology"]
        self.assertEqual(terminology["version"], "2.0.0")
        self.assertEqual(
            terminology["canonical_json"],
            [
                "data/terminology/news-taxonomy.json",
                "data/terminology/terms.json",
            ],
        )
        terms = load_json(ROOT / "data/terminology/terms.json")
        taxonomy = load_json(ROOT / "data/terminology/news-taxonomy.json")
        self.assertEqual(terms["version"], "2.0.0")
        self.assertEqual(taxonomy["version"], "2.0.0")
        self.assertEqual(len(terms["terms"]), 77)
        self.assertEqual(len(taxonomy["categories"]), 16)
        self.assertEqual(self.lock["review"]["human_domain_review"], "not_performed")
        self.assertFalse(self.lock["review"]["independent_quality_benchmark"])

    def test_directory_integrity_describes_the_fixture_lock_only(self):
        integrity = load_json(ROOT / "directory-integrity.json")
        self.assertEqual(integrity["status"], "pass")
        self.assertEqual(integrity["scope"], list(FIXTURE_ROOTS))
        self.assertEqual(integrity["locked_files"], len(self.lock["files"]))
        self.assertEqual(
            integrity["files_by_root"],
            {
                root: sum(1 for path in (ROOT / root).rglob("*") if path.is_file())
                for root in FIXTURE_ROOTS
            },
        )
        self.assertEqual(integrity["missing_files"], 0)
        self.assertEqual(integrity["unexpected_fixture_files"], 0)
        self.assertEqual(integrity["size_mismatches"], 0)
        self.assertEqual(integrity["checksum_mismatches"], 0)
        self.assertEqual(integrity["package_lock_sha256"], sha256(self.lock_path))
        self.assertFalse(integrity["legacy_runtime_covered"])
