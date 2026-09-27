"""Small JSON-in/JSON-out CLI; execute through AppFactory Container-Use only."""

import argparse
import json
import logging
import sys
from pathlib import Path

from command_io import CommandError, component, read_json, required, save_text
from corpus import load_manifest, read_fragment, read_term

LOG = logging.getLogger(__name__)
CONTEXT = (
    "operation",
    "project_id",
    "run_id",
    "corpus_id",
    "corpus_version",
    "schema_version",
    "collection_version",
)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CommandError("INVALID_INPUT", message)


def execute(request, inputs, results):
    if not isinstance(request, dict):
        raise CommandError("INVALID_INPUT", "Request must be an object")
    for key in CONTEXT[:5]:
        required(request, key)
    component(request["project_id"])
    component(request["run_id"])
    if "schema_version" in request or "collection_version" in request:
        required(request, "schema_version")
        required(request, "collection_version")
    operation = request["operation"]
    if operation not in {"read_manifest", "read_fragment", "read_term", "save_text"}:
        raise CommandError("INVALID_INPUT", "Unknown operation", operation=operation)
    manifest, root, checksum = load_manifest(inputs, request)
    if operation == "read_manifest":
        return {
            "manifest": manifest,
            "manifest_sha256": checksum,
            "file_count": len(manifest["files"]),
        }
    if operation == "read_fragment":
        return read_fragment(manifest, root, request)
    if operation == "read_term":
        return read_term(manifest, root, request)
    return save_text(request, inputs, results)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    context = {}
    try:
        parser = Parser(description=__doc__)
        parser.add_argument("--inputs", type=Path, required=True)
        parser.add_argument("--results", type=Path, required=True)
        parser.add_argument("request", type=Path)
        args = parser.parse_args()
        request = read_json(args.request)
        if isinstance(request, dict):
            context = {key: request[key] for key in CONTEXT if key in request}
        LOG.info("[B04] context=%s — started", json.dumps(context, ensure_ascii=False))
        data = execute(request, args.inputs, args.results)
        result = {"status": "ok", "context": context, "data": data}
        LOG.info(
            "[B04] context=%s — completed", json.dumps(context, ensure_ascii=False)
        )
    except CommandError as exc:
        result = {"status": "error", "context": context, "error": exc.error}
        LOG.warning("[B04] context=%s error=%s — command failed", context, exc.error)
    except (OSError, UnicodeError) as exc:
        result = {
            "status": "error",
            "context": context,
            "error": {"code": "IO_ERROR", "message": str(exc)},
        }
        LOG.error("[B04] context=%s error=%s — I/O failed", context, type(exc).__name__)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
