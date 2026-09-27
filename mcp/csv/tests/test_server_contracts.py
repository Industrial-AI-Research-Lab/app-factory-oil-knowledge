"""Static server contracts for CSV MCP: thin FastMCP wrapper."""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import csv_mcp.server as srv  # noqa: E402

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = PACKAGE_ROOT / "src" / "csv_mcp" / "server.py"
MAIN_PATH = PACKAGE_ROOT / "src" / "csv_mcp" / "__main__.py"
REQUIREMENTS_PATH = PACKAGE_ROOT / "requirements.txt"
DOCKERFILE_PATH = PACKAGE_ROOT / "Dockerfile"
COMPOSE_PATH = PACKAGE_ROOT / "compose.yaml"

EXPECTED_TOOLS = (
    "csv_normalize_inline",
    "csv_write_normalized",
    "csv_download_normalize_upload",
)


def _tree() -> ast.Module:
    return ast.parse(SERVER_PATH.read_text(encoding="utf-8"))
def _unwrap(fn):
    return getattr(fn, "fn", fn)
def _tool_defs(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    found: dict[str, ast.FunctionDef] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(func, ast.Attribute) and func.attr == "tool":
                found[node.name] = node
    return found
def _decorator_tool_name(node: ast.FunctionDef) -> str | None:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if isinstance(func, ast.Attribute) and func.attr == "tool":
            for kw in dec.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    return kw.value.value
    return None


def test_exactly_three_tools_with_decorator_names():
    """All CSV operations are exposed as named MCP tools."""
    defs = _tool_defs(_tree())
    assert sorted(defs) == sorted(EXPECTED_TOOLS)
    for name, node in defs.items():
        assert _decorator_tool_name(node) == name
    assert callable(_unwrap(srv.csv_normalize_inline))
    assert callable(_unwrap(srv.csv_write_normalized))
    assert callable(_unwrap(srv.csv_download_normalize_upload))


def test_tools_keep_signatures_defaults_and_returns():
    """CSV tools keep exact params, defaults, and return annotations."""
    defs = _tool_defs(_tree())
    assert sorted(defs) == sorted(EXPECTED_TOOLS)

    normalize = defs["csv_normalize_inline"]
    assert [a.arg for a in normalize.args.args] == ["content", "delimiter"]
    assert ast.unparse(normalize.args.args[0].annotation) == "str"
    assert ast.unparse(normalize.args.args[1].annotation) == "NormalizeDelimiter"
    assert len(normalize.args.defaults) == 1
    default = normalize.args.defaults[0]
    assert isinstance(default, ast.Constant) and default.value == "auto"
    assert ast.unparse(normalize.returns) == "NormalizeResult"
    assert not normalize.args.vararg and not normalize.args.kwarg

    write = defs["csv_write_normalized"]
    assert [a.arg for a in write.args.args] == ["rows", "delimiter"]
    assert ast.unparse(write.args.args[0].annotation) == "list[CsvRow]"
    assert ast.unparse(write.args.args[1].annotation) == "Dialect"
    assert len(write.args.defaults) == 1
    w_default = write.args.defaults[0]
    assert isinstance(w_default, ast.Constant) and w_default.value == ";"
    assert ast.unparse(write.returns) == "WriteResult"
    assert not write.args.vararg and not write.args.kwarg

    upload = defs["csv_download_normalize_upload"]
    assert [a.arg for a in upload.args.args] == ["csv_url"]
    assert ast.unparse(upload.args.args[0].annotation) == "str"
    assert ast.unparse(upload.returns) == "UploadResult"
    assert not upload.args.vararg and not upload.args.kwarg


def test_each_tool_single_delegation_with_direct_return():
    """Each tool delegates once to its service function and returns it."""
    tree = _tree()
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    expectations = {
        "csv_normalize_inline": ("_csv_normalize_inline", ["content", "delimiter"]),
        "csv_write_normalized": ("_csv_write_normalized", ["rows", "delimiter"]),
        "csv_download_normalize_upload": ("_csv_download_normalize_upload", ["request"]),
    }
    for tool, (callee, args_src) in expectations.items():
        node = defs[tool]
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == callee]
        assert len(calls) == 1
        assert [ast.unparse(arg) for arg in calls[0].args] == args_src
        returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
        assert len(returns) == 1
        assert isinstance(returns[0].value, ast.Call)
        assert isinstance(returns[0].value.func, ast.Name)
        assert returns[0].value.func.id == callee
    total = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in {"_csv_normalize_inline", "_csv_write_normalized", "_csv_download_normalize_upload"}]
    assert len(total) == 3


def test_normalize_exact_delegation_and_direct_return(monkeypatch):
    """Normalize passes (content, delimiter) through and returns as-is."""
    sentinel = object()
    seen: dict = {}

    def fake(content, delimiter):
        seen.update({"content": content, "delimiter": delimiter})
        return sentinel

    monkeypatch.setattr(srv, "_csv_normalize_inline", fake)
    content = "activity_id;activity_name\nx;y"
    out = _unwrap(srv.csv_normalize_inline)(content, "auto")
    assert out is sentinel
    assert seen == {"content": content, "delimiter": "auto"}


def test_write_exact_delegation_and_direct_return(monkeypatch):
    """Write passes (rows, delimiter) through and returns as-is."""
    sentinel = object()
    seen: dict = {}

    def fake(rows, delimiter):
        seen.update({"rows": rows, "delimiter": delimiter})
        return sentinel

    monkeypatch.setattr(srv, "_csv_write_normalized", fake)
    rows = ["row-sentinel"]
    out = _unwrap(srv.csv_write_normalized)(rows, ";")  # type: ignore[arg-type]
    assert out is sentinel
    assert seen["delimiter"] == ";"
    assert seen["rows"] == rows


def test_fastmcp_csv_host_port():
    """Single FastMCP host/port binding keeps the csv contract."""
    tree = _tree()
    assert (srv.HOST, srv.PORT, srv.MCP_PATH) == ("0.0.0.0", 8080, "/mcp")
    assert srv.mcp.name == "csv"
    fast = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and ((isinstance(n.func, ast.Name) and n.func.id == "FastMCP") or (isinstance(n.func, ast.Attribute) and n.func.attr == "FastMCP"))]
    assert len(fast) == 1
    assert isinstance(fast[0].args[0], ast.Constant) and fast[0].args[0].value == "csv"
    kw = {k.arg: ast.unparse(k.value) for k in fast[0].keywords}
    assert kw.get("host") == "HOST"
    assert kw.get("port") == "PORT"


def test_imports_respect_thin_layer_boundary():
    """Server imports only stdlib, FastMCP, and the public adapter API."""
    tree = _tree()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level + (node.module or "")
            mods.add(prefix)
    assert {"argparse", "logging", "mcp.server.fastmcp"} <= mods
    assert "csv_adapter" in mods
    forbidden_substrings = (".parser", ".storage", "pandas", "numpy", "requests", "psycopg2", "sqlalchemy", "boto3", "storage")
    assert not any(any(part in m for part in forbidden_substrings) for m in mods)
    assert "csv" not in mods
    assert "json" not in mods
    assert "re" not in mods
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "csv_adapter":
            names = {a.asname or a.name for a in node.names}
            assert {"CsvRow", "Dialect", "NormalizeDelimiter", "NormalizeResult", "WriteResult"} <= names
            assert {"_csv_normalize_inline", "_csv_write_normalized", "_csv_download_normalize_upload"} <= names


def test_no_raw_logging_business_or_storage_calls():
    """No raw payload logging, prints, or business/storage calls."""
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            raise AssertionError("print() is forbidden in the thin server")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"call_tool", "fetch", "upload", "download"}:
            raise AssertionError(f"business/storage call forbidden: {node.attr}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "logger":
                rendered = ast.unparse(node)
                assert "content" not in rendered
                assert "rows" not in rendered
                assert "secret" not in rendered.lower()
                assert "token" not in rendered.lower()


def test_main_requests_streamable_http_only(monkeypatch):
    """Entrypoint runs single FastMCP via streamable-http only."""
    tree = _tree()
    mains = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"]
    assert len(mains) == 1
    rendered = ast.unparse(mains[0])
    assert "streamable-http" in rendered
    assert "sse" not in rendered
    assert "stdio" not in rendered
    runs = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "run"
    ]
    assert len(runs) == 1
    kw = {k.arg: ast.unparse(k.value) for k in runs[0].keywords}
    assert kw.get("transport") == "'streamable-http'"
    seen: dict = {}
    monkeypatch.setattr(srv.mcp, "run", lambda transport=None, **kw: seen.update({"t": transport}))
    srv.main([])
    assert seen == {"t": "streamable-http"}
    srv.main(["--transport", "streamable-http"])
    assert seen == {"t": "streamable-http"}
    with pytest.raises(SystemExit):
        srv.main(["--transport", "sse"])
    with pytest.raises(SystemExit):
        srv.main(["--transport", "stdio"])


def test_thin_main_entrypoint():
    """__main__ stays thin: imports main and calls it under guard."""
    text = MAIN_PATH.read_text(encoding="utf-8")
    tree = ast.parse(text)
    assert len(text.splitlines()) <= 10
    assert "from .server import main" in text
    assert 'if __name__ == "__main__":' in text
    assert "main()" in text
    assert "FastMCP" not in text
    assert "argparse" not in text
    assert "csv_normalize_inline" not in text
    assert "csv_write_normalized" not in text
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 1


def test_requirements_static_contract():
    """Requirements pin the approved runtime without business deps."""
    text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert "mcp[cli]==1.16.0" in lines
    assert "pydantic==2.11.9" in lines
    lowered = text.lower()
    assert "pandas" not in lowered
    assert "numpy" not in lowered
    assert "requests" not in lowered
    # boto3 is required: s3:// download/upload is core functionality, not an extra.


def test_dockerfile_static_contract():
    """Dockerfile keeps the standalone streamable-http package contract."""
    text = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in text
    assert "PYTHONPATH=/app/src" in text
    assert "PORT=8080" in text
    assert "EXPOSE 8080" in text
    assert 'CMD ["python", "-m", "csv_mcp", "--transport", "streamable-http"]' in text
    assert "USER appuser" in text
    assert "sse" not in text.lower()
    assert "stdio" not in text.lower()


def test_compose_static_contract():
    """Compose keeps single csv service on 18081:8080 with TCP check."""
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    assert "csv:" in text
    assert '"18081:8080"' in text or "'18081:8080'" in text or "18081:8080" in text
    assert 'PORT: "8080"' in text or "PORT" in text
    assert "socket.create_connection" in text
    assert "127.0.0.1" in text
    assert "18081" in text


def test_download_admission_queues_when_slots_exhausted(monkeypatch):
    import threading

    monkeypatch.setattr(srv, "_csv_download_normalize_upload", lambda req: "DONE")
    for _ in range(8):
        assert srv._DOWNLOAD_SLOTS.acquire(blocking=False)
    try:
        done: list = []
        thread = threading.Thread(
            target=lambda: done.append(_unwrap(srv.csv_download_normalize_upload)("https://allowed.invalid/a.csv"))
        )
        thread.start()
        thread.join(timeout=2)
        assert not done, "call must wait for a free slot"
    finally:
        for _ in range(8):
            srv._DOWNLOAD_SLOTS.release()
    thread.join(timeout=5)
    assert done == ["DONE"]


def test_download_error_mapping(monkeypatch):
    with pytest.raises(ValueError, match="E_URL_INVALID"):
        _unwrap(srv.csv_download_normalize_upload)("http://x/a.csv")

    class _Boom(Exception):
        pass

    monkeypatch.setattr(srv, "_csv_download_normalize_upload", lambda req: (_ for _ in ()).throw(ValueError("E_LIMIT_ROWS: x")))
    with pytest.raises(ValueError, match="E_LIMIT_ROWS"):
        _unwrap(srv.csv_download_normalize_upload)("https://allowed.invalid/a.csv")
    monkeypatch.setattr(srv, "_csv_download_normalize_upload", lambda req: (_ for _ in ()).throw(RuntimeError("E_UPLOAD_FAILED")))
    with pytest.raises(RuntimeError, match="E_UPLOAD_FAILED"):
        _unwrap(srv.csv_download_normalize_upload)("https://allowed.invalid/a.csv")
    monkeypatch.setattr(srv, "_csv_download_normalize_upload", lambda req: (_ for _ in ()).throw(_Boom("secret")))
    with pytest.raises(RuntimeError, match="E_INTERNAL") as exc:
        _unwrap(srv.csv_download_normalize_upload)("https://allowed.invalid/a.csv")
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("port,expected", [("abc", 8080), ("", 8080), ("0", 8080), ("-1", 8080), ("99999", 8080), ("18081", 18081), ("1", 1), ("65535", 65535)])
def test_port_env_parsing(monkeypatch, port, expected):
    import importlib

    monkeypatch.setenv("PORT", port)
    reloaded = importlib.reload(srv)
    try:
        assert reloaded.PORT == expected
    finally:
        monkeypatch.undo()
        importlib.reload(srv)
