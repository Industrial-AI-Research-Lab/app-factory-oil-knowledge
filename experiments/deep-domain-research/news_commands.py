import argparse
import json
import logging
import sys
from pathlib import Path

from command_io import CommandError, read_json
from news_brief import prepare_brief
from news_inputs import context, load_inputs
from news_result import finalize
from news_search import candidates
from news_storage import roots

LOG = logging.getLogger(__name__)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CommandError("INVALID_INPUT", message)


def execute(request, inputs, results):
    scope = context(request)
    roots(inputs, results)
    records, terms, taxonomy, provenance = load_inputs(request, inputs)
    if scope["operation"] == "prepare_brief":
        return prepare_brief(request, records, provenance, results)
    if scope["operation"] == "candidates":
        return candidates(request, records, terms, taxonomy, provenance, results)
    if scope["operation"] == "finalize":
        return finalize(request, records, taxonomy, provenance, results)
    raise CommandError("INVALID_INPUT", "Unknown news operation")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    scope = {}
    try:
        parser = Parser()
        parser.add_argument("--inputs", type=Path, required=True)
        parser.add_argument("--results", type=Path, required=True)
        parser.add_argument("request", type=Path)
        args = parser.parse_args()
        request = read_json(args.request)
        scope = context(request)
        LOG.info("[N02] context=%s — started", scope)
        data = execute(request, args.inputs, args.results)
        result = {"status": "ok", "context": scope, "data": data}
        LOG.info("[N02] context=%s — completed", scope)
    except CommandError as exc:
        result = {"status": "error", "context": scope, "error": exc.error}
        LOG.warning("[N02] context=%s error=%s — command rejected", scope, exc.error)
    except (OSError, UnicodeError) as exc:
        result = {
            "status": "error",
            "context": scope,
            "error": {"code": "IO_ERROR", "message": str(exc)},
        }
        LOG.error("[N02] context=%s error=%s — I/O failed", scope, type(exc).__name__)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
