"""Behavior checks: provenance, fail-closed reads, immutable inputs and replay."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parents[1] / "commands.py"


class CommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        self.records = [
            {
                "es_id": "chunk-1",
                "document_id": "doc-1",
                "chapter": "Conditions",
                "chunk_index": 7,
                "text": "Температура 23 °C; значение неизвестно.",
            }
        ]
        chunks = json.dumps(self.records[0], ensure_ascii=False) + "\n"
        (self.inputs / "chunks.jsonl").write_bytes(chunks.encode())
        terms = {"terms": [{"id": "aos", "name": "AOS", "source": "doc-1"}]}
        (self.inputs / "terms.json").write_text(json.dumps(terms), encoding="utf-8")
        self.manifest = {
            "corpus_id": "fixture",
            "version": "1",
            "files": [
                self.entry("chunks.jsonl", "fact_source"),
                self.entry("terms.json", "terminology"),
            ],
        }
        self.write_manifest()
        self.request = {
            "operation": "read_fragment",
            "project_id": "p1",
            "run_id": "r1",
            "corpus_id": "fixture",
            "corpus_version": "1",
            "manifest": "manifest.json",
            "source_id": "chunk-1",
        }

    def entry(self, name, role):
        return {
            "path": name,
            "role": role,
            "sha256": hashlib.sha256((self.inputs / name).read_bytes()).hexdigest(),
        }

    def write_manifest(self):
        (self.inputs / "manifest.json").write_text(
            json.dumps(self.manifest), encoding="utf-8"
        )

    def call(self, **changes):
        request = self.request | changes
        path = self.root / "request.json"
        path.write_text(json.dumps(request), encoding="utf-8")
        run = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--inputs",
                str(self.inputs),
                "--results",
                str(self.root / "results"),
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertTrue(run.stdout.strip(), f"CLI must return JSON: {run.stderr}")
        result = json.loads(run.stdout)
        self.assertEqual(run.returncode, 0 if result["status"] == "ok" else 1)
        self.assertIn("[B04]", run.stderr)
        return result

    def test_fragment_keeps_source_and_context(self):
        result = self.call()
        self.assertEqual(result["data"]["record"], self.records[0])
        self.assertEqual(result["context"]["project_id"], "p1")
        self.assertEqual(result["context"]["corpus_version"], "1")
        self.assertEqual(result["data"]["source_path"], "chunks.jsonl")

    def test_manifest_and_term_reads(self):
        self.assertEqual(self.call(operation="read_manifest")["data"]["file_count"], 2)
        result = self.call(
            operation="read_term", term_id="aos", terms_path="terms.json"
        )
        self.assertEqual(result["data"]["record"]["source"], "doc-1")

    def test_unknown_id_is_error(self):
        result = self.call(source_id="absent")
        self.assertEqual(result["error"]["code"], "UNKNOWN_ID")
        self.assertEqual(result["context"]["run_id"], "r1")

    def test_missing_file_is_error(self):
        (self.inputs / "chunks.jsonl").unlink()
        self.assertEqual(self.call()["error"]["code"], "MISSING_FILE")

    def test_invalid_json_is_error(self):
        (self.inputs / "chunks.jsonl").write_text("{broken", encoding="utf-8")
        self.manifest["files"][0] = self.entry("chunks.jsonl", "fact_source")
        self.write_manifest()
        self.assertEqual(self.call()["error"]["code"], "INVALID_JSON")

    def test_changed_checksum_and_version_fail(self):
        self.assertEqual(
            self.call(corpus_version="2")["error"]["code"], "VERSION_MISMATCH"
        )
        (self.inputs / "chunks.jsonl").write_text("changed", encoding="utf-8")
        self.assertEqual(self.call()["error"]["code"], "CHECKSUM_MISMATCH")

    def test_paths_cannot_escape_inputs_or_output_scope(self):
        self.assertEqual(
            self.call(manifest="../request.json")["error"]["code"], "INVALID_PATH"
        )
        self.assertEqual(
            self.call(operation="save_text", name="../manifest", text="x")["error"][
                "code"
            ],
            "INVALID_PATH",
        )
        self.assertEqual(self.call(project_id="../p2")["error"]["code"], "INVALID_PATH")

    def test_nul_paths_return_structured_errors(self):
        self.assertEqual(
            self.call(manifest="bad\x00.json")["error"]["code"], "INVALID_PATH"
        )
        self.manifest["files"][0]["path"] = "bad\x00.jsonl"
        self.write_manifest()
        self.assertEqual(self.call()["error"]["code"], "INVALID_PATH")

    def test_empty_or_duplicate_record_is_not_success(self):
        for records in [[self.records[0] | {"text": ""}], self.records * 2]:
            (self.inputs / "chunks.jsonl").write_text(
                "\n".join(json.dumps(r) for r in records), encoding="utf-8"
            )
            self.manifest["files"][0] = self.entry("chunks.jsonl", "fact_source")
            self.write_manifest()
            self.assertEqual(self.call()["status"], "error")

    def test_save_replay_conflict_and_two_projects(self):
        before = (self.inputs / "chunks.jsonl").read_bytes()
        args = {"operation": "save_text", "name": "answer", "text": "Привет\r\n23 °C\r"}
        first = self.call(**args)
        self.assertEqual(first["status"], "ok")
        saved = self.root / "results" / first["data"]["path"]
        expected = "Привет\n23 °C\n".encode("utf-8")
        self.assertEqual(saved.read_bytes(), expected)
        self.assertEqual(first["data"]["sha256"], hashlib.sha256(expected).hexdigest())
        self.assertEqual(self.call(**args)["data"], first["data"])
        self.assertEqual(
            self.call(**(args | {"text": "new"}))["error"]["code"], "RESULT_CONFLICT"
        )
        other = self.call(**(args | {"project_id": "p2", "text": "second"}))
        self.assertNotEqual(other["data"]["path"], first["data"]["path"])
        self.assertEqual(saved.read_bytes(), expected)
        self.assertEqual((self.inputs / "chunks.jsonl").read_bytes(), before)
        metadata = json.loads(saved.with_suffix(".json").read_text())
        self.assertEqual(metadata["request"]["run_id"], "r1")
        self.assertEqual(metadata["encoding"], "UTF-8")


if __name__ == "__main__":
    unittest.main()
