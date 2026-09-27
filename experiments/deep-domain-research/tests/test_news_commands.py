import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


CLI = Path(__file__).resolve().parents[1] / "news_commands.py"


class NewsFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.results = self.root / "results"
        self.inputs.mkdir()
        self.publication = {
            "publication_id": "paper-a",
            "published_date": "2025-06-01",
            "title": "Технология",
            "text": "Интеллектуальное управление закачкой воды в пласт.",
            "url": "https://example.test/a",
            "source": "Архив",
        }
        self.write_package("news", "news", {"paper.json": self.publication})
        self.write_package(
            "terms",
            "terms",
            {
                "terms.json": {
                    "version": "1",
                    "terms": [{"id": "injection", "name": "Закачка"}],
                },
                "news-taxonomy.json": {
                    "version": "1",
                    "categories": [{"id": "water", "name": "Заводнение"}],
                },
            },
        )
        self.request = {
            "operation": "prepare_brief",
            "project_id": "p1",
            "run_id": "r1",
            "corpus_id": "news",
            "corpus_version": "1",
            "manifest": "news/manifest.json",
            "terminology": {
                "manifest": "terms/manifest.json",
                "corpus_id": "terms",
                "corpus_version": "1",
            },
            "brief": {
                "version": "1",
                "query": "Исследуй заводнение",
                "topic": "Заводнение",
                "period": {"start": "2025-01-01", "end": "2025-12-31"},
                "inclusions": ["Управление закачкой"],
                "exclusions": ["Переработка"],
                "output_formats": ["json"],
                "source_policy": "Сохранённый архив без перечитывания сайтов",
            },
        }

    def write_package(self, folder, corpus_id, values):
        root = self.inputs / folder
        root.mkdir(exist_ok=True)
        files = []
        for name, value in values.items():
            payload = json.dumps(value, ensure_ascii=False).encode()
            (root / name).write_bytes(payload)
            entry = {"path": name, "sha256": hashlib.sha256(payload).hexdigest()}
            if "publication_id" in value:
                entry["publication_id"] = value["publication_id"]
            files.append(entry)
        (root / "manifest.json").write_text(
            json.dumps({"corpus_id": corpus_id, "version": "1", "files": files}),
            encoding="utf-8",
        )

    def call(self, expected_error=None, **changes):
        path = self.root / "request.json"
        path.write_text(json.dumps(self.request | changes), encoding="utf-8")
        run = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--inputs",
                str(self.inputs),
                "--results",
                str(self.results),
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(
            run.returncode, 1 if expected_error else 0, run.stderr or run.stdout
        )
        self.assertTrue(run.stdout.strip(), run.stderr)
        result = json.loads(run.stdout)
        if expected_error:
            self.assertEqual(result["error"]["code"], expected_error)
            return result["error"]
        return result["data"]

    def search_request(self):
        reference = self.call()["reference"]
        return {
            "operation": "candidates",
            "brief_ref": reference,
            "approval": {
                "approval_id": "actual-approval",
                "gate_node_id": "review",
                "brief_sha256": reference["sha256"],
            },
            "terminology_result": {
                "query": self.request["brief"]["query"],
                "dictionary_versions": {"terms": "1", "taxonomy": "1"},
                "detected_terms": [],
                "expansions": [
                    {
                        "term_id": "injection",
                        "query_fragment": "управление закачкой воды",
                        "reason": "Водозакачка соответствует смыслу заводнения",
                    }
                ],
                "exclusions": [],
                "refined_query": "Исследуй управление закачкой воды",
                "constraints": [],
                "ambiguities": [],
            },
        }

    def analysis(self):
        quote = self.publication["text"]
        return {
            "publications": [
                {
                    "publication_id": "paper-a",
                    "decision": "include",
                    "reason": "Описано управление закачкой воды",
                    "categories": ["water"],
                    "evidence": [{"quote": quote}],
                    "limitations": [],
                }
            ],
            "events": [
                {
                    "event_id": "event-a",
                    "description": "Управление закачкой",
                    "technology": "Интеллектуальное управление",
                    "task": "Закачка воды",
                    "object": None,
                    "time": None,
                    "evidence_level": "statement",
                    "categories": ["water"],
                    "publication_ids": ["paper-a"],
                    "evidence": [{"publication_id": "paper-a", "quote": quote}],
                    "merge_reason": "Одна публикация",
                    "limitations": [],
                }
            ],
        }

    def finalize_request(self):
        search = self.call(**self.search_request())
        analysis = self.analysis()
        return {
            "operation": "finalize",
            "candidates_ref": search["reference"],
            "initial_analysis": analysis,
            "analysis": analysis,
            "critique": {"correction_cycle": 0, "notes": []},
        }


class NewsCommandsTest(NewsFixture):
    def test_brief_records_verified_archive_and_immutable_reference(self):
        result = self.call()
        reference = result["reference"]
        payload = (self.results / reference["path"]).read_bytes()
        self.assertEqual(reference["sha256"], hashlib.sha256(payload).hexdigest())
        brief = json.loads(payload)
        self.assertEqual(brief["query"], "Исследуй заводнение")
        self.assertEqual(
            brief["archive_period"], {"start": "2025-06-01", "end": "2025-06-01"}
        )
        self.assertEqual(brief["inputs"]["news"]["version"], "1")
        self.assertEqual(self.call()["reference"], reference)

    def test_expansion_finds_full_source_and_records_matching_reason(self):
        result = self.call(**self.search_request())
        self.assertEqual(len(result["candidates"]), 1)
        record = result["candidates"][0]
        self.assertEqual(record["text"], self.publication["text"])
        self.assertEqual(record["publication_id"], "paper-a")
        self.assertEqual(record["matches"][0]["term_id"], "injection")
        self.assertEqual(result["audit"][0]["status"], "candidate")

    def test_final_result_counts_evidence_backed_events_and_sources(self):
        output = self.call(**self.finalize_request())
        result = output["result"]
        self.assertEqual(result["statistics"]["publication_count"], 1)
        self.assertEqual(result["statistics"]["event_count"], 1)
        self.assertEqual(result["sources"][0]["url"], self.publication["url"])
        self.assertEqual(result["publications"][0]["published_date"], "2025-06-01")
        self.assertEqual(
            result["events"][0]["evidence"][0]["quote"], self.publication["text"]
        )
        stored = json.loads((self.results / output["reference"]["path"]).read_bytes())
        self.assertEqual(stored, result)

    def test_candidate_reference_cannot_be_used_as_a_brief(self):
        request = self.search_request()
        search = self.call(**request)
        request["brief_ref"] = search["reference"]
        request["approval"]["brief_sha256"] = search["reference"]["sha256"]
        error = self.call(expected_error="INVALID_INPUT", **request)
        self.assertEqual(error["field"], "kind")

    def test_missing_article_content_stays_insufficient_without_events(self):
        self.publication["text"] = None
        self.write_package("news", "news", {"paper.json": self.publication})
        search = self.call(**self.search_request())
        self.assertEqual(search["candidates"], [])
        self.assertEqual(search["audit"][0]["status"], "insufficient_evidence")
        analysis = {"publications": [], "events": []}
        result = self.call(
            operation="finalize",
            candidates_ref=search["reference"],
            initial_analysis=analysis,
            analysis=analysis,
            critique={"correction_cycle": 0, "notes": []},
        )["result"]
        self.assertEqual(result["statistics"]["event_count"], 0)
        self.assertEqual(result["publications"][0]["decision"], "insufficient_evidence")


if __name__ == "__main__":
    unittest.main()
