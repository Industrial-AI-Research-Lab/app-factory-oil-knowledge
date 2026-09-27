"""Thin FastMCP transport wrapper for semantic mapping.

Two read-only tools: ``semantic_search_works`` and
``semantic_search_hierarchies``. Transport-only validation, lazy settings,
safe logging, and error redaction live here; mapping logic lives in
``semantic_mapping_adapter``.
"""

import argparse
import logging
import os
import threading

from mcp.server.fastmcp import FastMCP

from semantic_mapping_adapter import (
    InputWorkMappingUnit,
    OutputHierarchyMappingResult,
    OutputWorkMappingResult,
    load_runtime_settings,
    search_hierarchies,
    search_works,
)

logger = logging.getLogger(__name__)


class _CapacityExceeded(RuntimeError):
    """Backpressure signal: must reach the client unwrapped."""

HOST = "0.0.0.0"
try:
    PORT = int(os.environ.get("PORT", "8080"))
except ValueError:
    PORT = 8080
if not 1 <= PORT <= 65535:
    PORT = 8080
MCP_PATH = "/mcp"
MAX_WORKS_ITEMS = 32
MAX_HIERARCHIES_ITEMS = 32
MAX_TOP_K = 10
MAX_TEXT_LENGTH = 4096
MAX_REQUEST_TEXT_LENGTH = 32768
MAX_CONCURRENT_REQUESTS = 8

mcp = FastMCP("semantic-mapping", host=HOST, port=PORT)
_request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)


def _validate_top_k(top_k: int) -> int:
    """Validate top-k in [1, 10].

    Args:
        top_k: Requested neighbours per input.

    Returns:
        Validated value.

    Raises:
        ValueError: If out of bounds or not an int.
    """
    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise ValueError("top_k must be an int in [1, 10]")
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError("top_k must be an int in [1, 10]")
    return top_k


@mcp.tool()
def semantic_search_works(works: list[InputWorkMappingUnit], top_k: int = 5) -> OutputWorkMappingResult:
    """Map works via the adapter search.

    Args:
        works: Bounded batch of 1..32 inputs.
        top_k: Neighbours per input in [1, 10].

    Returns:
        Mapped works in input order with distances.

    Raises:
        ValueError: For invalid batch/top-k (safe message).
        RuntimeError: For failures without sensitive data.
    """
    try:
        top_k = _validate_top_k(top_k)
        works = list(works)
        if not works or len(works) > MAX_WORKS_ITEMS:
            raise ValueError("works must contain 1..32 items")
        text_lengths = [
            len(value)
            for work in works
            for value in (
                work.work_name,
                work.work_measurement,
                work.bwd_name,
                work.ose_name,
                work.occ_name,
            )
            if value is not None
        ]
        if any(length > MAX_TEXT_LENGTH for length in text_lengths) or sum(text_lengths) > MAX_REQUEST_TEXT_LENGTH:
            raise ValueError("works text exceeds request limits")
        if not _request_slots.acquire(blocking=False):
            raise _CapacityExceeded("semantic mapping capacity exceeded")
        try:
            settings = load_runtime_settings()
            logger.info("[SEMANTIC_MCP][TOOL] tool=semantic_search_works n=%d top_k=%d -- start", len(works), top_k)
            result = search_works(works, top_k, settings=settings)
        finally:
            _request_slots.release()
        logger.info("[SEMANTIC_MCP][TOOL] tool=semantic_search_works n=%d -- finished", len(works))
        return result
    except ValueError:
        logger.error("[SEMANTIC_MCP][TOOL] tool=semantic_search_works -- validation failed")
        raise
    except _CapacityExceeded:
        raise
    except Exception as exc:
        logger.error("[SEMANTIC_MCP][TOOL] tool=semantic_search_works -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError("semantic_search_works failed") from None


@mcp.tool()
def semantic_search_hierarchies(hierarchies: list[str], level: int, top_k: int = 5) -> OutputHierarchyMappingResult:
    """Map hierarchies via the adapter search with level filter.

    Args:
        hierarchies: Bounded batch of 1..32 names.
        level: Hierarchy level filter.
        top_k: Neighbours per input in [1, 10].

    Returns:
        Mapped hierarchies in input order with distances.

    Raises:
        ValueError: For invalid batch/level/top-k (safe message).
        RuntimeError: For failures without sensitive data.
    """
    try:
        top_k = _validate_top_k(top_k)
        if isinstance(level, bool) or not isinstance(level, int) or level <= 0:
            raise ValueError("level must be a positive int")
        hierarchies = list(hierarchies)
        if not hierarchies or len(hierarchies) > MAX_HIERARCHIES_ITEMS:
            raise ValueError("hierarchies must contain 1..32 items")
        for item in hierarchies:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("hierarchies must be non-empty strings")
        if any(len(item) > MAX_TEXT_LENGTH for item in hierarchies) or sum(map(len, hierarchies)) > MAX_REQUEST_TEXT_LENGTH:
            raise ValueError("hierarchies text exceeds request limits")
        if not _request_slots.acquire(blocking=False):
            raise _CapacityExceeded("semantic mapping capacity exceeded")
        try:
            settings = load_runtime_settings()
            logger.info("[SEMANTIC_MCP][TOOL] tool=semantic_search_hierarchies n=%d top_k=%d -- start", len(hierarchies), top_k)
            result = search_hierarchies(hierarchies, level, top_k, settings=settings)
        finally:
            _request_slots.release()
        logger.info("[SEMANTIC_MCP][TOOL] tool=semantic_search_hierarchies n=%d -- finished", len(hierarchies))
        return result
    except ValueError:
        logger.error("[SEMANTIC_MCP][TOOL] tool=semantic_search_hierarchies -- validation failed")
        raise
    except _CapacityExceeded:
        raise
    except Exception as exc:
        logger.error("[SEMANTIC_MCP][TOOL] tool=semantic_search_hierarchies -- failed error_type=%s", type(exc).__name__)
        raise RuntimeError("semantic_search_hierarchies failed") from None


def main(argv: list[str] | None = None) -> None:
    """Run server on http://0.0.0.0:8080/mcp via streamable-http.

    Args:
        argv: Optional args, only ``--transport streamable-http``.
    """
    parser = argparse.ArgumentParser(prog="semantic_mapping_mcp")
    parser.add_argument("--transport", default="streamable-http", choices=("streamable-http",), help="MCP transport (AppFactory mode: streamable-http)")
    parser.parse_args(argv)
    logger.info("[SEMANTIC_MCP][SERVE] host=%s port=%d path=%s -- starting", HOST, PORT, MCP_PATH)
    mcp.run(transport="streamable-http")
