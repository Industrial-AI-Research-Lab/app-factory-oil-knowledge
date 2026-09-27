"""Verify OSDU controls and strict write rejection through the official MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from graph_loader.controls import CONTROLS, CONTROL_PARAMS


MCP_URL = "http://127.0.0.1:8081/"
GRAPH_NAME = "osdu-volve"
EXPECTED_TOOLS = {
    "query_graph",
    "query_graph_readonly",
    "list_graphs",
    "delete_graph",
    "get_graph_schema",
    "get_node_schema",
    "get_relationship_schema",
}


async def verify_readonly_mcp(
    url: str = MCP_URL, graph_name: str = GRAPH_NAME
) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _session_id):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}
            if tool_names != EXPECTED_TOOLS:
                raise RuntimeError(
                    f"MCP tools mismatch: expected {sorted(EXPECTED_TOOLS)}, "
                    f"got {sorted(tool_names)}"
                )

            schema_result = await session.call_tool(
                "get_graph_schema", {"graphName": graph_name}
            )
            _require_success(schema_result, "get_graph_schema")

            controls = []
            for control in CONTROLS:
                result = await session.call_tool(
                    "query_graph_readonly",
                    {
                        "graphName": graph_name,
                        "query": control.query,
                        "params": CONTROL_PARAMS,
                    },
                )
                rows = _rows(result, control.id)
                controls.append(
                    {
                        "id": control.id,
                        "passed": rows == control.expected,
                        "expected": control.expected,
                        "actual": rows,
                    }
                )

            marker = "__osdu_readonly_probe__"
            write_result = await session.call_tool(
                "query_graph",
                {
                    "graphName": graph_name,
                    "query": f"CREATE (:WriteProbe {{name: '{marker}'}})",
                    "readOnly": False,
                },
            )
            if not _is_error(write_result):
                raise RuntimeError("strict read-only MCP accepted a CREATE query")
            postcondition = await session.call_tool(
                "query_graph_readonly",
                {
                    "graphName": graph_name,
                    "query": "MATCH (n:WriteProbe {name: $name}) RETURN count(n) AS count",
                    "params": {"name": marker},
                },
            )
            rows = _rows(postcondition, "write postcondition")
            if rows != [{"count": 0}]:
                raise RuntimeError("strict read-only probe changed the graph")

            delete_result = await session.call_tool(
                "delete_graph",
                {"graphName": graph_name, "confirmDelete": True},
            )
            if not _is_error(delete_result):
                raise RuntimeError("strict read-only MCP accepted delete_graph")
            list_result = await session.call_tool("list_graphs", {})
            _require_success(list_result, "list_graphs after delete_graph")
            if graph_name not in _text(list_result).splitlines():
                raise RuntimeError(
                    f"graph {graph_name!r} missing after rejected delete_graph"
                )

    if not all(item["passed"] for item in controls):
        raise RuntimeError("one or more OSDU Q1-Q10 MCP controls failed")
    return {
        "url": url,
        "graphName": graph_name,
        "schema": "available",
        "controls": controls,
        "writeRejected": True,
        "writePostcondition": 0,
        "deleteGraphRejected": True,
    }


def _rows(result: Any, label: str) -> list[dict[str, Any]]:
    _require_success(result, label)
    payload = getattr(result, "structuredContent", None)
    if payload is None:
        payload = getattr(result, "structured_content", None)
    text = _text(result)
    if payload is None and text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} did not return JSON") from exc
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise RuntimeError(f"{label} did not return query rows")
    return rows


def _require_success(result: Any, label: str) -> None:
    if _is_error(result):
        raise RuntimeError(f"{label} failed: {_text(result)[:300]}")


def _is_error(result: Any) -> bool:
    return bool(
        getattr(result, "isError", False) or getattr(result, "is_error", False)
    )


def _text(result: Any) -> str:
    return "\n".join(
        block.text
        for block in (getattr(result, "content", None) or [])
        if isinstance(getattr(block, "text", None), str)
    ).strip()


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=MCP_URL)
    parser.add_argument("--graph", default=GRAPH_NAME)
    args = parser.parse_args()
    try:
        report = await verify_readonly_mcp(args.url, args.graph)
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("[PASS] schema, Q1-Q10, write/delete rejection and graph retention")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
