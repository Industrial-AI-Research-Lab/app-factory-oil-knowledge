"""Thin server contracts: boundary, signatures, lazy delegation."""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import semantic_mapping_mcp.server as srv  # noqa: E402
from semantic_mapping_adapter import (  # noqa: E402
    InputWorkMappingUnit,
    OutputHierarchyMappingResult,
    OutputWorkMappingResult,
)
def _tree() -> ast.Module:
    """Parse current thin server.py source."""
    path = Path(__file__).resolve().parents[1] / "src" / "semantic_mapping_mcp" / "server.py"
    return ast.parse(path.read_text(encoding="utf-8"))
def _unwrap(fn):
    """Return callable behind @mcp.tool."""
    return getattr(fn, "fn", fn)
def _work(name: str = "wall") -> InputWorkMappingUnit:
    """Build minimal valid work input via public adapter model."""
    return InputWorkMappingUnit(work_name=name, work_measurement="m2")
def _tool_defs(tree: ast.Module) -> dict:
    """Map tool-decorated function names to AST nodes."""
    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(func, ast.Attribute) and func.attr == "tool":
                found[node.name] = node
    return found
def test_imports_respect_thin_layer_boundary():
    """Server imports only stdlib, FastMCP, and public adapter symbols."""
    tree = _tree()
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert {"argparse", "logging", "mcp.server.fastmcp", "semantic_mapping_adapter"} <= mods
    assert not any("stairs_semantic_mapping" in m for m in mods)
    assert not any(m.startswith("semantic_mapping_mcp.") for m in mods)
    forbidden = (".config", ".database", ".embeddings", ".mappers", ".models", ".vector_store")
    assert not any(any(p in m for p in forbidden) for m in mods)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "semantic_mapping_adapter":
            assert sorted(a.name for a in node.names) == sorted(
                ["InputWorkMappingUnit", "OutputHierarchyMappingResult", "OutputWorkMappingResult",
                 "load_runtime_settings", "search_hierarchies", "search_works"])
    assert srv.InputWorkMappingUnit is InputWorkMappingUnit
    assert srv.OutputWorkMappingResult is OutputWorkMappingResult
    assert srv.OutputHierarchyMappingResult is OutputHierarchyMappingResult
def test_no_deleted_helpers_or_legacy_search():
    """Thin server keeps no thick helpers and calls no legacy search method."""
    tree = _tree()
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "_format_work_query" not in names
    assert "_build_works_mapper" not in names
    assert "_build_hierarchies_mapper" not in names
    attrs = [n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)]
    assert "semantic_search" not in attrs
    ids = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    legacy = {"TEIEmbeddingModel", "NormalizingEmbeddingModel", "PostgresConnectionProvider",
              "PgVectorStore", "HierarchicalMapper", "SimpleMapper"}
    assert ids.isdisjoint(legacy) and set(attrs).isdisjoint(legacy)
def test_two_tools_keep_signatures_and_defaults():
    """Exactly two tools keep thin signatures, defaults, and returns."""
    defs = _tool_defs(_tree())
    assert sorted(defs) == ["semantic_search_hierarchies", "semantic_search_works"]
    works = defs["semantic_search_works"]
    assert ast.unparse(works.args.args[0].annotation) == "list[InputWorkMappingUnit]"
    assert ast.unparse(works.args.args[1].annotation) == "int"
    assert ast.unparse(works.args.defaults[0]) == "5"
    assert ast.unparse(works.returns) == "OutputWorkMappingResult"
    hier = defs["semantic_search_hierarchies"]
    assert ast.unparse(hier.args.args[0].annotation) == "list[str]"
    assert ast.unparse(hier.args.args[1].annotation) == "int"
    assert ast.unparse(hier.args.args[2].annotation) == "int"
    assert ast.unparse(hier.args.defaults[0]) == "5"
    assert ast.unparse(hier.returns) == "OutputHierarchyMappingResult"
    assert hier.args.args[1].arg == "level"
    assert callable(_unwrap(srv.semantic_search_works))
    assert callable(_unwrap(srv.semantic_search_hierarchies))
def test_single_list_materialization_per_tool():
    """Each tool materializes its batch exactly once with list()."""
    tree = _tree()
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    def _lists(fn, arg):
        calls = [n for n in ast.walk(defs[fn]) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == "list"]
        assert len(calls) == 1 and ast.unparse(calls[0].args[0]) == arg
    _lists("semantic_search_works", "works")
    _lists("semantic_search_hierarchies", "hierarchies")
    total = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "list"]
    assert len(total) == 2
def test_settings_loaded_lazily_inside_tools_only():
    """Settings load happens inside tools, never at import time."""
    tree = _tree()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                assert name not in {"load_runtime_settings", "search_works", "search_hierarchies"}
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for tool in ("semantic_search_works", "semantic_search_hierarchies"):
        assert "load_runtime_settings" in ast.unparse(defs[tool])
    assert "load_runtime_settings" not in ast.unparse(defs["_validate_top_k"])
def test_validate_top_k_bounds():
    """Top-k accepts [1, 10] ints and rejects bool, zero, overflow."""
    assert srv._validate_top_k(1) == 1
    assert srv._validate_top_k(10) == 10
    for bad in (0, 11, True, False, "5", None):
        with pytest.raises(ValueError):
            srv._validate_top_k(bad)  # type: ignore[arg-type]
def test_works_validation_precedes_settings(monkeypatch):
    """Invalid works batch or top-k fails before settings or adapter."""
    monkeypatch.setattr(srv, "load_runtime_settings", lambda: (_ for _ in ()).throw(AssertionError("no IO")))
    monkeypatch.setattr(srv, "search_works", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no IO")))
    works = _unwrap(srv.semantic_search_works)
    with pytest.raises(ValueError, match="1..32"):
        works([], top_k=5)
    with pytest.raises(ValueError, match="1..32"):
        works([_work()] * 33, top_k=5)
    for bad in (0, 11, True):
        with pytest.raises(ValueError, match="top_k"):
            works([_work()], top_k=bad)
def test_hierarchies_validation_precedes_settings(monkeypatch):
    """Invalid hierarchies batch, level, or top-k fails before IO."""
    monkeypatch.setattr(srv, "load_runtime_settings", lambda: (_ for _ in ()).throw(AssertionError("no IO")))
    monkeypatch.setattr(srv, "search_hierarchies", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no IO")))
    fn = _unwrap(srv.semantic_search_hierarchies)
    with pytest.raises(ValueError, match="1..32"):
        fn([], level=1, top_k=5)
    with pytest.raises(ValueError, match="1..32"):
        fn(["x"] * 33, level=1, top_k=5)
    with pytest.raises(ValueError, match="non-empty"):
        fn(["  "], level=1, top_k=5)
    for bad_level in (True, "1", None, 0, -1, -5):
        with pytest.raises(ValueError, match="level"):
            fn(["x"], level=bad_level, top_k=5)
    with pytest.raises(ValueError, match="top_k"):
        fn(["x"], level=1, top_k=0)
def test_works_exact_delegation_and_direct_return(monkeypatch):
    """Works calls search_works(works, top_k, settings=settings) and returns as-is."""
    settings, sentinel = SimpleNamespace(marker="s"), SimpleNamespace(marker="r")
    seen: dict = {}
    def fake_search(works_arg, top_k_arg, settings=None):
        seen.update({"works": works_arg, "top_k": top_k_arg, "settings": settings})
        return sentinel
    calls = {"n": 0}
    def counting_loader():
        calls["n"] += 1
        return settings
    monkeypatch.setattr(srv, "load_runtime_settings", counting_loader)
    monkeypatch.setattr(srv, "search_works", fake_search)
    out = _unwrap(srv.semantic_search_works)([_work("wall")], top_k=5)
    assert out is sentinel and calls["n"] == 1
    assert seen["settings"] is settings and seen["top_k"] == 5
    assert isinstance(seen["works"], list) and [w.work_name for w in seen["works"]] == ["wall"]
def test_hierarchies_exact_delegation_and_direct_return(monkeypatch):
    """Hierarchies calls search_hierarchies(hierarchies, level, top_k, settings=settings)."""
    settings, sentinel = SimpleNamespace(marker="s"), SimpleNamespace(marker="r")
    seen: dict = {}
    def fake_search(hier_arg, level_arg, top_k_arg, settings=None):
        seen.update({"hier": hier_arg, "level": level_arg, "top_k": top_k_arg, "settings": settings})
        return sentinel
    monkeypatch.setattr(srv, "load_runtime_settings", lambda: settings)
    monkeypatch.setattr(srv, "search_hierarchies", fake_search)
    out = _unwrap(srv.semantic_search_hierarchies)(["wall"], level=2, top_k=5)
    assert out is sentinel
    assert seen == {"hier": ["wall"], "level": 2, "top_k": 5, "settings": settings}
def test_value_error_passthrough_not_redacted(monkeypatch):
    """Adapter ValueError propagates unchanged instead of generic RuntimeError."""
    monkeypatch.setattr(srv, "load_runtime_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(srv, "search_works", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad shape")))
    monkeypatch.setattr(srv, "search_hierarchies", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad level")))
    with pytest.raises(ValueError, match="bad shape"):
        _unwrap(srv.semantic_search_works)([_work()], top_k=5)
    with pytest.raises(ValueError, match="bad level"):
        _unwrap(srv.semantic_search_hierarchies)(["h"], level=1, top_k=5)
def test_unexpected_errors_redacted_without_cause_or_secret(monkeypatch):
    """Unexpected failures become generic RuntimeError without secret or cause."""
    secret = "SECRET-WALL-9"
    dsn, token = "postgres://u:pw@db:5432/d", "TOKEN-abc-123"
    def boom(*a, **k):
        raise RuntimeError(f"boom {secret} {dsn} {token}")
    monkeypatch.setattr(srv, "load_runtime_settings", lambda: SimpleNamespace())
    monkeypatch.setattr(srv, "search_works", boom)
    monkeypatch.setattr(srv, "search_hierarchies", boom)
    with pytest.raises(RuntimeError, match="semantic_search_works failed") as exc:
        _unwrap(srv.semantic_search_works)([_work(secret)], top_k=5)
    assert str(exc.value) == "semantic_search_works failed"
    assert secret not in str(exc.value) and dsn not in str(exc.value)
    assert token not in str(exc.value) and exc.value.__cause__ is None
    with pytest.raises(RuntimeError, match="semantic_search_hierarchies failed") as exc2:
        _unwrap(srv.semantic_search_hierarchies)([secret], level=1, top_k=5)
    assert str(exc2.value) == "semantic_search_hierarchies failed"
    assert secret not in str(exc2.value) and dsn not in str(exc2.value)
    assert token not in str(exc2.value) and exc2.value.__cause__ is None
def test_main_requests_streamable_http_only(monkeypatch):
    """Entrypoint runs single FastMCP on fixed address via streamable-http."""
    tree = _tree()
    assert (srv.HOST, srv.PORT, srv.MCP_PATH) == ("0.0.0.0", 8080, "/mcp")
    assert srv.mcp.name == "semantic-mapping"
    fast = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and ((isinstance(n.func, ast.Name) and n.func.id == "FastMCP")
                 or (isinstance(n.func, ast.Attribute) and n.func.attr == "FastMCP"))]
    assert len(fast) == 1
    kw = {k.arg: ast.unparse(k.value) for k in fast[0].keywords}
    assert kw.get("host") == "HOST" and kw.get("port") == "PORT"
    seen = {}
    monkeypatch.setattr(srv.mcp, "run", lambda transport=None, **kw: seen.update({"t": transport}))
    srv.main([])
    assert seen == {"t": "streamable-http"}
    srv.main(["--transport", "streamable-http"])
    assert seen == {"t": "streamable-http"}
    with pytest.raises(SystemExit):
        srv.main(["--transport", "sse"])


def test_text_limits_enforced():
    with pytest.raises(ValueError, match="exceeds request limits"):
        _unwrap(srv.semantic_search_works)([_work("a" * 4097)])
    with pytest.raises(ValueError, match="exceeds request limits"):
        _unwrap(srv.semantic_search_works)([_work("a" * 16384), _work("b" * 16385)])
    with pytest.raises(ValueError, match="exceeds request limits"):
        _unwrap(srv.semantic_search_hierarchies)(["a" * 4097], level=1)


def test_text_limits_boundary_ok(monkeypatch):
    monkeypatch.setattr(srv, "search_hierarchies", lambda h, level, top_k, settings: "OK")
    monkeypatch.setattr(srv, "load_runtime_settings", lambda: object())
    assert _unwrap(srv.semantic_search_hierarchies)(["a" * 4096], level=1) == "OK"


def test_capacity_exceeded(monkeypatch):
    class _Full:
        def acquire(self, blocking=True):
            return False

        def release(self):
            pass

    monkeypatch.setattr(srv, "_request_slots", _Full())
    with pytest.raises(RuntimeError, match="capacity exceeded"):
        _unwrap(srv.semantic_search_works)([_work()])
    with pytest.raises(RuntimeError, match="capacity exceeded"):
        _unwrap(srv.semantic_search_hierarchies)(["h"], level=1)


@pytest.mark.parametrize("port,expected", [("bad", 8080), ("0", 8080), ("70000", 8080), ("9000", 9000)])
def test_port_env_parsing(monkeypatch, port, expected):
    import importlib

    monkeypatch.setenv("PORT", port)
    reloaded = importlib.reload(srv)
    try:
        assert reloaded.PORT == expected
    finally:
        monkeypatch.undo()
        importlib.reload(srv)


@pytest.mark.parametrize("level", [2.5, "2", [], None])
def test_level_rejects_non_int(level):
    with pytest.raises(ValueError, match="positive int"):
        _unwrap(srv.semantic_search_hierarchies)(["h"], level=level)
