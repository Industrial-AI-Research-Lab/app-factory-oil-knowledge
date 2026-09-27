"""Read-only MCP adapters for synthetic SEG-Y, WITSML, and SCADA."""

__all__ = ["main", "mcp"]


def __getattr__(name: str):
    if name in {"main", "mcp"}:
        from format_adapters_mcp.server import main, mcp

        return {"main": main, "mcp": mcp}[name]
    raise AttributeError(name)
