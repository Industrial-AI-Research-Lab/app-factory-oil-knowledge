#!/usr/bin/env python3
"""Smoke: list_tools + call three read-only adapters over Streamable HTTP."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ENDPOINT = "http://127.0.0.1:18080/mcp"
_CONNECT_ATTEMPTS = 30
_CONNECT_SLEEP_S = 0.5
EXPECTED_TOOLS = {
    "read_segy_headers",
    "read_witsml_well",
    "read_scada_snapshot",
}


async def _run_once() -> None:
    async with AsyncExitStack() as stack:
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(ENDPOINT, timeout=30.0)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        missing = EXPECTED_TOOLS - names
        if missing:
            raise SystemExit(f"missing tools {sorted(missing)}; have={sorted(names)}")
        writes = [n for n in names if "write" in n.lower()]
        if writes:
            raise SystemExit(f"write tools present: {writes}")

        segy = _as_obj(await session.call_tool("read_segy_headers", {}))
        if segy.get("status") != "ok" or segy.get("format") != "segy":
            raise SystemExit(f"segy unexpected: {segy!r}")

        witsml = _as_obj(await session.call_tool("read_witsml_well", {}))
        if witsml.get("status") != "ok" or witsml.get("data", {}).get("uid") != "SYN-WELL-001":
            raise SystemExit(f"witsml unexpected: {witsml!r}")

        scada = _as_obj(await session.call_tool("read_scada_snapshot", {}))
        if scada.get("status") != "ok" or scada.get("data", {}).get("value") != 12.5:
            raise SystemExit(f"scada unexpected: {scada!r}")

    print("smoke_ok streamable-http read_segy_headers read_witsml_well read_scada_snapshot")


def _as_obj(result: object) -> dict:
    text = _tool_text(result)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"tool result is not JSON: {text!r}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"tool result is not an object: {payload!r}")
    return payload


async def _run() -> None:
    last: BaseException | None = None
    for _ in range(_CONNECT_ATTEMPTS):
        try:
            await _run_once()
            return
        except SystemExit:
            raise
        except Exception as exc:
            last = exc
            await asyncio.sleep(_CONNECT_SLEEP_S)
    assert last is not None
    raise last


def _tool_text(result: object) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        parts.append(text if isinstance(text, str) else str(block))
    return "\n".join(parts).strip()


def main() -> None:
    try:
        asyncio.run(_run())
    except Exception as exc:
        print(f"smoke_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
