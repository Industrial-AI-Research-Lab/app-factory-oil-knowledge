"""CLI for the reproducible Volve graph loading pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Any

from .controls import verify_graph_model
from .falkor_store import connect, load_graph, verify_database
from .fetch import fetch_volve
from .pipeline import build_graph, write_graph


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data" / "raw"
DEFAULT_ARTIFACT = ROOT / "build" / "graph.json"
DEFAULT_REPORT = ROOT / "build" / "load-report.json"
DEFAULT_GRAPH = "osdu-volve"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser("fetch", help="download the pinned Volve snapshot")
    fetch.add_argument("--output", type=Path, default=DEFAULT_DATA)

    build = commands.add_parser("build", help="build the validated graph artifact")
    _input_output_arguments(build)

    load = commands.add_parser("load", help="validate and load FalkorDB")
    load.add_argument("--input", type=Path, default=DEFAULT_DATA)
    _database_arguments(load)
    load.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    verify = commands.add_parser("verify", help="verify Q1-Q10 in FalkorDB")
    verify.add_argument("--input", type=Path, default=DEFAULT_DATA)
    _database_arguments(verify)

    all_command = commands.add_parser(
        "all", help="fetch, build, load and verify the complete demo"
    )
    _input_output_arguments(all_command, input_flag="--data")
    _database_arguments(all_command)
    all_command.add_argument("--report", type=Path, default=DEFAULT_REPORT)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s | %(message)s",
    )
    try:
        if args.command == "fetch":
            snapshot = fetch_volve(args.output)
            _print_json({"snapshot": snapshot})
            return 0

        input_path = args.input
        if args.command == "all":
            fetch_volve(input_path)
        source = build_graph(input_path)
        model_controls = verify_graph_model(source)

        if args.command == "build":
            write_graph(source, input_path, args.output)
            _print_json({"report": source.report(), "controls": model_controls})
            return _status(source, model_controls)

        database = connect(args.url)
        if args.command in {"load", "all"}:
            artifact_path = args.output if args.command == "all" else DEFAULT_ARTIFACT
            write_graph(source, input_path, artifact_path)
            report = load_graph(database, args.graph, source)
            database_controls = verify_database(database, args.graph)
            payload = {
                "source": source.report(),
                "load": report.to_dict(),
                "controls": database_controls,
            }
            _write_json(args.report, payload)
            _print_json(payload)
            return _status(source, model_controls, database_controls)

        database_controls = verify_database(database, args.graph)
        _print_json({"controls": database_controls})
        return 0 if all(item["passed"] for item in database_controls) else 1
    except Exception as exc:
        logging.getLogger(__name__).error("[OSDU_LOADER] %s", exc)
        return 1


def _input_output_arguments(
    parser: argparse.ArgumentParser, input_flag: str = "--input"
) -> None:
    parser.add_argument(input_flag, dest="input", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT)


def _database_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--url",
        default=os.environ.get("FALKORDB_URL", "falkor://127.0.0.1:6379"),
        help="FalkorDB URL; prefer FALKORDB_URL when it contains credentials",
    )
    parser.add_argument("--graph", default=DEFAULT_GRAPH)


def _status(source, *control_groups: list[dict[str, Any]]) -> int:
    controls_pass = all(
        item["passed"] for group in control_groups for item in group
    )
    return 0 if not source.fatal_issues and controls_pass else 1


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    raise SystemExit(main())
