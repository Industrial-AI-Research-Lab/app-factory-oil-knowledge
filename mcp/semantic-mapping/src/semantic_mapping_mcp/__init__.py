"""Semantic mapping MCP package.

Re-exports the FastMCP server object, entrypoint, and exactly two tools.
Importing reads only the PORT env var; TEI/PostgreSQL settings load lazily
inside tool calls and no connections open at import.
"""

from .server import main, mcp, semantic_search_hierarchies, semantic_search_works

__all__ = ["main", "mcp", "semantic_search_hierarchies", "semantic_search_works"]
