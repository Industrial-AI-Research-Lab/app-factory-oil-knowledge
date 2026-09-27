"""Discovery smoke: initialize and list tools over Streamable HTTP.

Requires exactly ``semantic_search_works`` and
``semantic_search_hierarchies``. Performs no business tool calls and
sends no input payloads or secrets; only ``initialize`` plus
``list_tools``.
"""

import argparse
import asyncio
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_URL = "http://127.0.0.1:18080/mcp"
EXPECTED_TOOLS = frozenset({"semantic_search_works", "semantic_search_hierarchies"})
_CONNECT_ATTEMPTS = 20
_CONNECT_SLEEP_S = 0.5


def parse_args(argv: list[str] | None = None) -> str:
    """Parse CLI args and return the MCP URL.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Selected Streamable HTTP endpoint URL.
    """
    parser = argparse.ArgumentParser(prog="smoke")
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="Streamable HTTP MCP endpoint",
    )
    return parser.parse_args(argv).url


async def _run_once(url: str) -> None:
    """Run a single discovery attempt against the endpoint.

    Args:
        url: Streamable HTTP MCP endpoint.

    Raises:
        SystemExit: If the discovered tool set mismatches the contract.
    """
    async with AsyncExitStack() as stack:
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(url, timeout=30.0)
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()

        listed = await session.list_tools()
        names = {t.name for t in listed.tools}
        if names != EXPECTED_TOOLS:
            raise SystemExit(
                f"tool mismatch; expected={sorted(EXPECTED_TOOLS)} "
                f"actual={sorted(names)}"
            )

    print(
        "smoke_ok streamable-http discovery "
        "tools=semantic_search_hierarchies,semantic_search_works"
    )


async def _run(url: str) -> None:
    """Retry discovery until success or attempts are exhausted.

    Args:
        url: Streamable HTTP MCP endpoint.

    Raises:
        BaseException: Last connection error after retries.
    """
    last: BaseException | None = None
    for _ in range(_CONNECT_ATTEMPTS):
        try:
            await _run_once(url)
            return
        except SystemExit:
            raise
        except Exception as exc:
            last = exc
            await asyncio.sleep(_CONNECT_SLEEP_S)
    assert last is not None
    raise last


def main(argv: list[str] | None = None) -> None:
    """Entry point with async lifecycle and concise failure output.

    Args:
        argv: Optional CLI args, supports ``--url``.
    """
    try:
        asyncio.run(_run(parse_args(argv)))
    except Exception as exc:
        print(f"smoke_failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
