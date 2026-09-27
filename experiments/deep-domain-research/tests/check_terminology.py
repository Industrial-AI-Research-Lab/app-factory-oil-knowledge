"""Run in the project container; this checks data, not semantic relevance."""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from ontology_checks import require as check, validate_ontology

ROOT = Path(__file__).resolve().parents[1]
LOG = logging.getLogger(__name__)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    inputs = ROOT / "inputs"
    terminology = inputs / "terminology"
    LOG.info(
        "[B03] project_id=%s run_id=%s — checking delivered dictionaries",
        args.project_id,
        args.run_id,
    )
    before = {
        p.relative_to(inputs).as_posix(): sha(p)
        for p in inputs.rglob("*")
        if p.is_file()
    }
    terms_doc = json.loads((terminology / "terms.json").read_text())
    taxonomy = json.loads((terminology / "news-taxonomy.json").read_text())
    terms, categories = validate_ontology(terms_doc, taxonomy, inputs)
    manifest = json.loads((terminology / "manifest.json").read_text())
    request = {
        "project_id": args.project_id,
        "run_id": args.run_id,
        "operation": "read_term",
        "manifest": "terminology/manifest.json",
        "corpus_id": manifest["corpus_id"],
        "corpus_version": manifest["version"],
        "terms_path": "terms.json",
    }
    calls = []
    with tempfile.TemporaryDirectory() as folder:
        request_path = Path(folder) / "request.json"

        def call(payload):
            request_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "commands.py"),
                    "--inputs",
                    str(inputs),
                    "--results",
                    str(ROOT / "results"),
                    str(request_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
            check(result.returncode == 0, f"CLI failed: {result.stderr}")
            response = json.loads(result.stdout)
            check(response["status"] == "ok", "CLI did not return ok")
            return response

        for identifier, record in terms.items():
            response = call(request | {"term_id": identifier})
            check(response["data"]["record"] == record, f"Read differs: {identifier}")
            calls.append({"term_id": identifier, "exit_code": 0, "record_equal": True})
        for name in ("news", "knowledge"):
            corpus_manifest = json.loads((inputs / name / "manifest.json").read_text())
            call(
                request
                | {
                    "operation": "read_manifest",
                    "manifest": f"{name}/manifest.json",
                    "corpus_id": corpus_manifest["corpus_id"],
                    "corpus_version": corpus_manifest["version"],
                }
            )
    after = {
        p.relative_to(inputs).as_posix(): sha(p)
        for p in inputs.rglob("*")
        if p.is_file()
    }
    check(before == after, "Inputs changed")
    result = {
        "status": "ok",
        "project_id": args.project_id,
        "run_id": args.run_id,
        "terms_count": len(terms),
        "categories_count": len(categories),
        "dictionary_versions": {
            "terms": terms_doc["version"],
            "taxonomy": taxonomy["version"],
        },
        "input_sha256": after,
        "inputs_unchanged": True,
        "cli_reads": calls,
        "code_sha256": {
            name: sha(ROOT / name)
            for name in (
                "commands.py",
                "corpus.py",
                "command_io.py",
                "tests/check_terminology.py",
                "tests/ontology_checks.py",
            )
        },
        "semantic_quality": "not_measured_by_this_check",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    LOG.info(
        "[B03] terms=%d categories=%d — integrity and CLI reads passed",
        len(terms),
        len(categories),
    )


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        subprocess.SubprocessError,
    ) as exc:
        LOG.error("[B03] error=%s — validation failed", exc)
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
