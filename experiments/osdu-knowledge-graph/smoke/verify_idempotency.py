"""Load the pinned OSDU graph twice and prove database idempotency."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph_loader.falkor_store import connect, load_graph, verify_database
from graph_loader.pipeline import build_graph


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data" / "raw")
    parser.add_argument("--graph", default="osdu-volve")
    parser.add_argument(
        "--url",
        default=os.environ.get("FALKORDB_URL", "falkor://127.0.0.1:6379"),
    )
    args = parser.parse_args()
    try:
        source = build_graph(args.input)
        database = connect(args.url)
        first = load_graph(database, args.graph, source)
        second = load_graph(database, args.graph, source)
        controls = verify_database(database, args.graph)
        if second.created_nodes != 0 or second.created_relationships != 0:
            raise RuntimeError("second load created duplicate graph elements")
        if not all(item["passed"] for item in controls):
            raise RuntimeError("Q1-Q10 failed after repeated load")
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(
        json.dumps(
            {
                "firstLoad": first.to_dict(),
                "secondLoad": second.to_dict(),
                "controls": controls,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print("[PASS] second load created 0 nodes and 0 relationships")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
