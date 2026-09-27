import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    request = {
        "project_id": args.project_id,
        "run_id": args.run_id,
        "corpus_id": "ipr4-pav-small",
        "corpus_version": "1.0.0",
        "manifest": "knowledge/manifest.json",
        "operation": "read_manifest",
    }
    before = {
        p.relative_to(ROOT / "inputs").as_posix(): sha(p)
        for p in (ROOT / "inputs").rglob("*")
        if p.is_file()
    }
    evidence = {"project_id": args.project_id, "run_id": args.run_id, "calls": []}
    with tempfile.TemporaryDirectory() as temporary:
        temp = Path(temporary)

        def call(changes, expected="ok", inputs=ROOT / "inputs"):
            body = request | changes
            request_path = temp / "request.json"
            request_path.write_text(
                json.dumps(body, ensure_ascii=False), encoding="utf-8"
            )
            command = [
                sys.executable,
                str(ROOT / "commands.py"),
                "--inputs",
                str(inputs),
                "--results",
                str(ROOT / "results"),
                str(request_path),
            ]
            run = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8"
            )
            response = json.loads(run.stdout)
            assert response["status"] == expected, response
            assert run.returncode == (0 if expected == "ok" else 1)
            evidence["calls"].append(
                {
                    "request": body,
                    "exit_code": run.returncode,
                    "stdout": response,
                    "stderr": run.stderr,
                }
            )
            return response

        manifest = call({})
        assert manifest["data"]["manifest"]["record_count"] == 116
        news_manifest = call(
            {"manifest": "news/manifest.json", "corpus_id": "ipr4-news-tn416"}
        )
        assert news_manifest["data"]["file_count"] == 26
        fragment = call(
            {
                "operation": "read_fragment",
                "source_id": "94b2e826-08d1-42b2-a95c-c09d7d3b4ecf",
            }
        )
        original = json.loads(
            (ROOT / "inputs/knowledge/chunks.jsonl").read_text().splitlines()[0]
        )
        assert fragment["data"]["record"] == original
        assert original["document_id"] == "0f8b983d-51d1-41c8-b926-8b78018dfb74"
        news = call(
            {
                "operation": "read_fragment",
                "manifest": "news/manifest.json",
                "corpus_id": "ipr4-news-tn416",
                "source_id": "tn416-publication-0001",
            }
        )
        assert news["data"]["record"] == json.loads(
            (ROOT / "inputs/news/publications/tn416-publication-0001.json").read_text()
        )
        assert (
            call({"operation": "read_fragment", "source_id": "b04-unknown"}, "error")[
                "error"
            ]["code"]
            == "UNKNOWN_ID"
        )
        assert (
            call({"manifest": "missing.json"}, "error")["error"]["code"]
            == "MISSING_FILE"
        )
        (temp / "invalid.json").write_text("{invalid", encoding="utf-8")
        assert (
            call({"manifest": "invalid.json"}, "error", temp)["error"]["code"]
            == "INVALID_JSON"
        )
        text = (
            json.dumps(
                {
                    "source_id": original["es_id"],
                    "document_id": original["document_id"],
                    "chunk_index": original["chunk_index"],
                    "text": original["text"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        save_request = {
            "operation": "save_text",
            "name": "source-excerpt",
            "text": text,
        }
        saved = call(save_request)
        repeated = call(save_request)
        assert saved == repeated
        path = ROOT / "results" / saved["data"]["path"]
        assert path.read_bytes() == text.encode("utf-8")
        assert sha(path) == saved["data"]["sha256"]
        after = {
            p.relative_to(ROOT / "inputs").as_posix(): sha(p)
            for p in (ROOT / "inputs").rglob("*")
            if p.is_file()
        }
        assert before == after
        evidence.update(
            {
                "inputs_unchanged": True,
                "input_sha256": after,
                "artifact": saved["data"],
                "source_record_equal": True,
            }
        )
    destination = (
        ROOT / "results" / args.project_id / args.run_id / "live-evidence.json"
    )
    destination.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "project_id": args.project_id,
                "run_id": args.run_id,
                "call_count": len(evidence["calls"]),
                "inputs_unchanged": True,
                "artifact": evidence["artifact"],
                "evidence_path": str(destination),
            }
        )
    )


if __name__ == "__main__":
    main()
