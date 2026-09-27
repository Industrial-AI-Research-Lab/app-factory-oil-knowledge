"""Core contracts for the donor-core/foundation matrix.

Donor core is byte-identical (9 files + 2 shims); adapter owns config and
secure infra; MCP is thin transport. No DB or network; fakes and AST only.
"""

import ast
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import numpy as np
import pytest
import requests
from pydantic import ValidationError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import stairs_semantic_mapping.infrastructure.embeddings as donor_emb_mod  # noqa: E402
import semantic_mapping_adapter.config as adapter_config_mod  # noqa: E402
from semantic_mapping_adapter.config import DBConfig, RuntimeSettings  # noqa: E402
from semantic_mapping_adapter.infrastructure import SecurePostgresConnectionProvider, ValidatingPgVectorStore  # noqa: E402
from stairs_semantic_mapping.infrastructure.embeddings import NormalizingEmbeddingModel, TEIEmbeddingModel, l2_normalize  # noqa: E402
from stairs_semantic_mapping.infrastructure.vector_query import PgVectorStore, PgVectorStoreConfig, VectorQueryResult  # noqa: E402
from stairs_semantic_mapping.utils.mapping_result_model import InputWorkMappingUnit as CoreInputWork  # noqa: E402
from stairs_semantic_mapping.utils.mapping_result_model import OutputHierarchyMappingUnit, OutputWorkMappingUnit, StandardHierarchyUnit, StandardWorkUnit  # noqa: E402
def _src() -> Path:
    """Return src root anchored at this test file."""
    return Path(__file__).resolve().parents[1] / "src"
def _core() -> Path:
    """Return donor-core root anchored at this test file."""
    return _src() / "stairs_semantic_mapping"
def _donor() -> Path:
    """Return the immutable source snapshot packaged with this MCP."""
    return Path(__file__).resolve().parents[1] / "sources" / "stairs-semantic-mapping" / "stairs_semantic_mapping"
EXPECTED = ["mapping/base.py", "mapping/simple_mapper.py", "mapping/hierarchical_mapper.py", "mapping/__init__.py", "infrastructure/embeddings.py", "infrastructure/db_connection_provider.py", "infrastructure/vector_query.py", "infrastructure/__init__.py", "utils/mapping_result_model.py"]
SHIMS = ["__init__.py", "utils/__init__.py"]
LEGACY = ["works_mapping_job.py", "hierarchies_mapping_job.py", "mapper.py", "data_loading.py", "jobs.py", "test.py", "utils/settings.py", "utils/data_loading.py", "loading/__init__.py", "loading/utils.py"]
BANNED = ["protollm_sdk", "chromadb", "transformers", "stairs_sdk", "pandas", "sqlalchemy"]
def _parse(p: Path) -> ast.Module:
    """Parse a Python file to AST."""
    return ast.parse(p.read_text(encoding="utf-8"))
def _imports(t: ast.Module) -> list[str]:
    """Collect imported module names from an AST, preserving relative level."""
    out = []
    for n in ast.walk(t):
        if isinstance(n, ast.Import):
            out.extend(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                out.append("." * n.level + n.module)
            else:
                out.extend("." * n.level + a.name for a in n.names)
    return out
def _db(**kw: Any) -> DBConfig:
    """Return minimal valid DB config."""
    data: dict[str, Any] = {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"}
    data.update(kw)
    return DBConfig(**data)
def _scfg(**kw: Any) -> PgVectorStoreConfig:
    """Return minimal valid donor store config."""
    return PgVectorStoreConfig(table_name="items", **kw)
class _Resp:
    """Minimal fake for requests.Response used by TEI tests."""
    def __init__(self, payload: Any, error: Exception | None = None) -> None:
        self._p = payload
        self._e = error
    def raise_for_status(self) -> None:
        if self._e is not None:
            raise self._e
    def json(self) -> Any:
        return self._p
class _Cur:
    """Fake cursor recording execute calls without a database."""
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: list[dict] = []
    def execute(self, q: Any, p: Any) -> None:
        self.executed.append({"query": q, "params": p})
    def fetchall(self) -> list[dict]:
        return list(self._rows)
class _Prov:
    """Fake provider yielding a single fake cursor."""
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.cursors: list[_Cur] = []
    @contextmanager
    def get_cursor(self, cursor_factory: Any = None):  # type: ignore[no-untyped-def]
        cur = _Cur(self._rows)
        self.cursors.append(cur)
        yield cur
class _NoIo:
    """Provider failing if any SQL IO is attempted."""
    @contextmanager
    def get_cursor(self, cursor_factory: Any = None):  # type: ignore[no-untyped-def]
        raise AssertionError("no IO expected")
        yield None  # pragma: no cover
class _Stub:
    """Stub donor store returning a canned result without SQL."""
    def __init__(self, r: VectorQueryResult) -> None:
        self._r = r
    def query(self, query_embeddings: Any, top_k: Any, where: Any = None, include: Any = None) -> VectorQueryResult:
        return self._r
def _with_stub(r: VectorQueryResult, c: PgVectorStoreConfig, **kw: Any) -> ValidatingPgVectorStore:
    """Build validating store with stubbed donor; single sanctioned _store touch."""
    s = ValidatingPgVectorStore(_NoIo(), c, **kw)  # type: ignore[arg-type]
    object.__setattr__(s, "_store", _Stub(r))
    return s
def test_filesystem_manifest_is_nine_plus_two_shims():
    """Actual filesystem holds exactly 9 donor files plus 2 shims."""
    assert sorted(p.relative_to(_core()).as_posix() for p in _core().rglob("*.py")) == sorted(EXPECTED + SHIMS)
@pytest.mark.parametrize("rel", EXPECTED)
def test_donor_file_is_byte_identical(rel: str):
    """Each of the 9 files is byte/SHA-identical to donor."""
    cb, db = (_core() / rel).read_bytes(), (_donor() / rel).read_bytes()
    assert cb == db, f"byte mismatch for {rel}"
    assert hashlib.sha256(cb).hexdigest() == hashlib.sha256(db).hexdigest()
def test_legacy_donor_modules_are_excluded():
    """Excluded legacy donor modules are absent from runtime core."""
    for rel in LEGACY:
        assert not (_core() / rel).exists(), rel
    assert not (_core() / "loading").exists()
@pytest.mark.parametrize("rel", EXPECTED + SHIMS)
def test_core_import_closure(rel: str):
    """Core never imports adapter/MCP/mcp/FastMCP, banned deps, or env."""
    mods = _imports(_parse(_core() / rel))
    for m in mods:
        assert "semantic_mapping_adapter" not in m and "semantic_mapping_mcp" not in m
        assert m != "mcp" and not m.startswith("mcp.") and "fastmcp" not in m.lower()
        for b in BANNED:
            assert b not in m
    t = (_core() / rel).read_text(encoding="utf-8")
    assert "os.environ" not in t and "os.getenv" not in t
    assert "load_runtime_settings" not in t and "RuntimeSettings" not in t
def test_adapter_has_no_mcp_imports():
    """Adapter never imports MCP package, mcp.server, or FastMCP."""
    for name in ["__init__.py", "config.py", "infrastructure.py", "works.py", "hierarchies.py"]:
        for m in _imports(_parse(_src() / "semantic_mapping_adapter" / name)):
            assert "semantic_mapping_mcp" not in m, name
            assert m != "mcp" and not m.startswith("mcp."), name
            assert "fastmcp" not in m.lower(), name
def test_root_shim_is_empty_and_safe():
    """Root shim has no imports, Jobs, settings, or protollm."""
    t = (_core() / "__init__.py").read_text(encoding="utf-8")
    assert _imports(ast.parse(t)) == []
    for bad in ("WorksMappingJob", "HierarchiesMappingJob", "settings", "protollm_sdk"):
        assert bad not in t
def test_utils_shim_reexports_only_result_model():
    """Utils shim relatively re-exports only mapping_result_model."""
    assert _imports(_parse(_core() / "utils" / "__init__.py")) == [".mapping_result_model"]
    t = (_core() / "utils" / "__init__.py").read_text(encoding="utf-8")
    for bad in ("settings", "RuntimeSettings", "Jobs", "WorksMappingJob", "HierarchiesMappingJob", "data_loading", "protollm"):
        assert bad not in t, bad
def test_init_files_preserve_donor_identity_and_order():
    """Mapping/infra inits stay identical with donor import order."""
    for rel in ["mapping/__init__.py", "infrastructure/__init__.py"]:
        assert (_core() / rel).read_bytes() == (_donor() / rel).read_bytes()
    m = (_core() / "mapping" / "__init__.py").read_text(encoding="utf-8")
    assert m.index("from .base import") < m.index("from .hierarchical_mapper import") < m.index("from .simple_mapper import")
    i = (_core() / "infrastructure" / "__init__.py").read_text(encoding="utf-8")
    assert "ConnectionProvider" in i and i.index("db_connection_provider") < i.index("vector_query") < i.index("embeddings")
def test_output_units_validate_top_k_lengths():
    """Result models reject unequal and accept equal top_k lengths."""
    w, h = StandardWorkUnit(code="a", name="n", measurement="m", category="c"), StandardHierarchyUnit(code="h", name="n")
    with pytest.raises(ValidationError):
        OutputWorkMappingUnit(work_name="w", work_measurement="m", top_k_units=(w, w), top_k_distances=(0.1,))
    ok = OutputWorkMappingUnit(work_name="w", work_measurement="m", top_k_units=(w,), top_k_distances=(0.1,))
    assert len(ok.top_k_units) == len(ok.top_k_distances) == 1
    with pytest.raises(ValidationError):
        OutputHierarchyMappingUnit(hierarchical_work_name="h", top_k_units=(h,), top_k_distances=(0.1, 0.2))
    assert CoreInputWork(work_name="w", work_measurement="m", bwd_name="b").bwd_name == "b"
    assert CoreInputWork(work_name="w", work_measurement="m").ose_name is None
def test_l2_normalize_and_delegating_model():
    """l2 handles unit/zero rows plus 2D guard; wrapper delegates."""
    out = l2_normalize(np.array([[3.0, 4.0], [0.0, 0.0]], dtype="float32"))
    assert out.shape == (2, 2) and np.allclose(np.linalg.norm(out[0]), 1.0, atol=1e-6)
    assert np.allclose(out[1], np.zeros(2)) and not np.isnan(out).any()
    with pytest.raises(ValueError):
        l2_normalize(np.array([1.0, 2.0]))
    class _B:
        def __init__(self) -> None:
            self.seen: list = []
        def embed(self, texts: Any) -> np.ndarray:
            self.seen.append(list(texts))
            return np.array([[3.0, 4.0]] * len(list(texts)), dtype="float32")
    b = _B()
    wrapped = NormalizingEmbeddingModel(b).embed(["a", "b"])  # type: ignore[arg-type]
    assert wrapped.shape == (2, 2) and b.seen == [["a", "b"]] and np.allclose(np.linalg.norm(wrapped, axis=1), 1.0, atol=1e-6)
def test_tei_empty_input_skips_io(monkeypatch: pytest.MonkeyPatch):
    """Empty TEI input returns (0, 0) float32 without HTTP."""
    calls: list = []
    monkeypatch.setattr(donor_emb_mod.requests, "post", lambda *a, **k: calls.append((a, k)))
    out = TEIEmbeddingModel(host="http://tei/embed").embed([])
    assert out.shape == (0, 0) and out.dtype == np.float32 and calls == []
def test_tei_batches_and_reshapes(monkeypatch: pytest.MonkeyPatch):
    """TEI batches inputs and reshapes flat single-vector payloads."""
    seen: list = []
    def _fake(url: str, json: dict, headers: Any = None, timeout: Any = None) -> _Resp:
        seen.append(json["inputs"])
        assert json["truncate"] is False
        return _Resp([[float(i), 0.0] for i in range(len(json["inputs"]))])
    monkeypatch.setattr(donor_emb_mod.requests, "post", _fake)
    out = TEIEmbeddingModel(host="http://tei/embed", batch_size=2, timeout=5.0).embed(["t1", "t2", "t3", "t4", "t5"])
    assert out.shape == (5, 2) and seen == [["t1", "t2"], ["t3", "t4"], ["t5"]]
    monkeypatch.setattr(donor_emb_mod.requests, "post", lambda *a, **k: _Resp([0.1, 0.2]))
    assert TEIEmbeddingModel(host="http://tei/embed").embed(["one"]).shape == (1, 2)
def test_tei_propagates_transport_errors(monkeypatch: pytest.MonkeyPatch):
    """TEI surfaces HTTP transport errors to the caller."""
    monkeypatch.setattr(donor_emb_mod.requests, "post", lambda *a, **k: _Resp([], error=requests.HTTPError("413")))
    with pytest.raises(requests.HTTPError):
        TEIEmbeddingModel(host="http://tei/embed").embed(["boom"])
def test_where_clause_is_parameterized():
    """WHERE uses ANY placeholders without interpolating values."""
    c, p = PgVectorStore(_Prov([]), _scfg())._build_where_clause({"category_id": [1, 2]})
    assert "category_id" in str(c) and "ANY" in str(c) and "1, 2" not in str(c) and p == [[1, 2]]
    assert PgVectorStore(_Prov([]), _scfg())._build_where_clause({})[1] == []
def test_query_uses_distance_and_ranking_operators():
    """Donor SQL uses <=> for distance and <-> for ORDER BY ranking."""
    prov = _Prov([{"id": 1, "distance": 0.42}])
    res = PgVectorStore(prov, _scfg()).query(query_embeddings=np.array([[1.0, 0.0]], dtype="float32"), top_k=1, include=["distances"])
    assert res.ids == [["1"]] and res.distances is not None
    sql = str(prov.cursors[0].executed[0]["query"])
    assert "<=>" in sql and "<->" in sql and sql.index("<=>") < sql.index("ORDER BY") <= sql.index("<->")
    params = prov.cursors[0].executed[0]["params"]
    assert params[-1] == 1 and any(isinstance(p, list) and all(isinstance(x, float) for x in p) for p in params)
def test_donor_query_rejects_non_2d():
    """Donor vector coercion fails fast for non-2D inputs."""
    with pytest.raises((ValueError, TypeError)):
        PgVectorStore(_Prov([]), _scfg()).query(query_embeddings=np.array([1.0, 2.0]), top_k=1)
def test_dbconfig_redacts_and_parses():
    """DBConfig redacts SecretStr; DB_CONFIG parses dict/JSON strictly."""
    assert "s3cret" not in repr(_db(password="s3cret")) + str(_db(password="s3cret").model_dump())
    assert _db(password="s3cret").to_psycopg_kwargs()["password"] == "s3cret"
    payload = {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"}
    assert RuntimeSettings.model_validate({"DB_CONFIG": payload, "EMBEDDING_HOST_CATEGORIES": "http://a/embed"}).db_config.host == "h"
    assert RuntimeSettings.model_validate({"DB_CONFIG": json.dumps(payload), "EMBEDDING_HOST_CATEGORIES": "http://a/embed"}).db_config.dbname == "d"
    for bad in (json.dumps([1, 2]), "not-json{{{"):
        with pytest.raises(ValidationError, match="DB_CONFIG"):
            RuntimeSettings.model_validate({"DB_CONFIG": bad, "EMBEDDING_HOST_CATEGORIES": "http://a/embed"})
@pytest.mark.parametrize("bad_host", ["ftp://tei/embed", "tei.local/embed", "", "file:///embed"])
def test_tei_endpoint_rejects_non_http(bad_host: str):
    """TEI hosts must be http(s) URLs."""
    good = {"DB_CONFIG": _db().model_dump(), "EMBEDDING_HOST_CATEGORIES": "https://tei.local/embed"}
    assert RuntimeSettings.model_validate(good).embedding_host_categories.startswith("https://")
    with pytest.raises(ValidationError, match="TEI endpoint"):
        RuntimeSettings.model_validate({**good, "EMBEDDING_HOST_CATEGORIES": bad_host})
def test_config_lazy_no_env_at_import():
    """Adapter config reads env only inside the loader function."""
    tree = _parse(_src() / "semantic_mapping_adapter" / "config.py")
    top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    top_src = "\n".join(ast.dump(n) for n in top)
    assert "environ" not in top_src and "getenv" not in top_src
    loader = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "load_runtime_settings")
    assert "RuntimeSettings" in ast.dump(loader) and callable(adapter_config_mod.load_runtime_settings)
def test_secure_provider_redacts_and_validates():
    """Secure provider redacts SecretStr and rejects non-DBConfig."""
    p = SecurePostgresConnectionProvider(_db(password="s3cret"))
    assert "s3cret" not in repr(p) + str(p) and "SecretStr" in repr(p)
    with pytest.raises(TypeError):
        SecurePostgresConnectionProvider({"host": "h"})  # type: ignore[arg-type]
def test_validating_store_rejects_bad_inputs():
    """Validating wrapper fails fast on identifiers, top_k, vectors, include, where."""
    with pytest.raises(ValueError):
        ValidatingPgVectorStore(_NoIo(), PgVectorStoreConfig(table_name="bad-name!"))  # type: ignore[arg-type]
    s, v = ValidatingPgVectorStore(_NoIo(), _scfg()), np.zeros((1, 2))
    for bad_k in (True, False, 0, -1):
        with pytest.raises(ValueError, match="top_k"):
            s.query(query_embeddings=v, top_k=bad_k)  # type: ignore[arg-type]
    for kw, pat in [({"query_embeddings": np.array([1.0, 2.0])}, "2D"), ({"query_embeddings": np.zeros((1, 0))}, "non-zero dimension"), ({"query_embeddings": v, "include": ["nope"]}, "invalid include"), ({"query_embeddings": v, "where": "x"}, "where"), ({"query_embeddings": v, "where": {"c": "str"}}, "non-string sequence"), ({"query_embeddings": v, "where": {"bad-col!": [1]}}, "")]:
        with pytest.raises(ValueError, match=pat) if pat else pytest.raises(ValueError):
            s.query(top_k=1, **kw)  # type: ignore[arg-type]
def test_validating_cardinality_and_metadata_guards():
    """Include-driven cardinality and required-metadata checks via one stub helper."""
    with pytest.raises(ValueError, match="distances"):
        _with_stub(VectorQueryResult(ids=[["1"]], results=[["a"]]), _scfg()).query(query_embeddings=np.zeros((1, 2)), top_k=1, include=["distances"])
    for bad_m in (None, [[{"other": 1}]], [[None]], [["str"]]):
        g = _with_stub(VectorQueryResult(ids=[["1"]], results=[["a"]], metadatas=bad_m), _scfg(metadata_columns=["code"]), required_metadata_fields=["code"])  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="metadata|metadatas"):
            g.query(query_embeddings=np.zeros((1, 2)), top_k=1, include=["metadatas"])
    r = _with_stub(VectorQueryResult(ids=[["1"]], results=[["a"]]), _scfg(metadata_columns=["code"]), required_metadata_fields=["code"])
    assert r.query(query_embeddings=np.zeros((1, 2)), top_k=1, include=["results"]).ids == [["1"]]
def test_validating_preserves_sql_operators():
    """Delegated donor SQL still uses <=> for distance and <-> for ordering."""
    prov = _Prov([{"id": 7, "distance": 0.11, "code": "L1"}])
    res = ValidatingPgVectorStore(prov, PgVectorStoreConfig(table_name="items", metadata_columns=["code"])).query(query_embeddings=np.array([[0.0, 1.0]], dtype=float), top_k=1, include=["distances", "metadatas"])
    assert res.ids == [["7"]]
    sql = str(prov.cursors[0].executed[0]["query"])
    assert "<=>" in sql and "<->" in sql and sql.index("<=>") < sql.index("ORDER BY")


class _FakeConn:
    connects: list = []

    def __init__(self):
        _FakeConn.connects.append(self)
        self.closed = False
        self.session = None
        self.cursor_obj = SimpleNamespace()

    def set_session(self, **kw):
        self.session = kw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, cursor_factory=None):
        from contextlib import contextmanager

        @contextmanager
        def _cur():
            yield self.cursor_obj

        return _cur()

    def close(self):
        self.closed = True


def test_provider_request_scope_nesting_single_connect(monkeypatch):
    import semantic_mapping_adapter.infrastructure as infra

    _FakeConn.connects.clear()
    monkeypatch.setattr(infra.psycopg2, "connect", lambda **kw: _FakeConn())
    provider = SecurePostgresConnectionProvider(_db())
    with provider.request_scope() as outer:
        with provider.request_scope() as inner:
            assert inner is outer
    assert len(_FakeConn.connects) == 1
    conn = _FakeConn.connects[0]
    assert conn.session == {"readonly": True, "autocommit": False}
    assert conn.closed is True


def test_provider_str_matches_redacted_repr():
    provider = SecurePostgresConnectionProvider(_db(password="s3cret"))
    assert str(provider) == repr(provider) and "s3cret" not in str(provider)


@pytest.mark.parametrize("bad", [2.5, "5", None])
def test_store_top_k_types_rejected(bad):
    # Note: the store only requires a positive int; the [1, 10] cap lives
    # in transport (_validate_top_k) and adapter (require_top_k).
    import numpy as np

    s = _with_stub(VectorQueryResult(ids=[[1]], results=[["a"]]), _scfg())
    with pytest.raises(ValueError):
        s.query(np.zeros((1, 2)), top_k=bad)


def test_store_nan_embeddings_rejected():
    import numpy as np

    s = _with_stub(VectorQueryResult(ids=[[1]], results=[["a"]]), _scfg())
    with pytest.raises(ValueError, match="finite"):
        s.query(np.array([[float("nan"), 1.0]]), top_k=1)


def test_store_where_variants():
    import numpy as np

    s = _with_stub(VectorQueryResult(ids=[[1]], results=[["a"]]), _scfg())
    with pytest.raises(ValueError, match="non-string sequence"):
        s.query(np.zeros((1, 2)), top_k=1, where={"c": 123})
    with pytest.raises(ValueError, match="non-string sequence"):
        s.query(np.zeros((1, 2)), top_k=1, where={"c": None})
    # Empty list is allowed (ignored downstream).
    s.query(np.zeros((1, 2)), top_k=1, where={"c": []})


def test_store_outer_mismatch_rejected():
    s = _with_stub(VectorQueryResult(ids=[["1"]], results=[]), _scfg())
    import numpy as np

    with pytest.raises(ValueError, match="cardinality"):
        s.query(np.zeros((1, 2)), top_k=1)


def test_store_bad_provider_or_config():
    with pytest.raises(TypeError):
        ValidatingPgVectorStore(object(), _scfg())  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ValidatingPgVectorStore(_NoIo(), object())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ValidatingPgVectorStore(_NoIo(), PgVectorStoreConfig(table_name="t", id_column="bad col!"))  # type: ignore[arg-type]


def test_config_bad_endpoints_and_forms():
    import json

    from semantic_mapping_adapter import RuntimeSettings

    base = {"DB_CONFIG": _db().model_dump(mode="json")}
    for bad in ["ftp://h/embed", "http://user:pass@h/embed", "https://h/embed#frag", "not-a-url"]:
        with pytest.raises(Exception):
            RuntimeSettings.model_validate({**base, "EMBEDDING_HOST_TASKS": bad})
    raw = dict(base)
    raw["DB_CONFIG"] = json.dumps(base["DB_CONFIG"])
    assert RuntimeSettings.model_validate(raw).db_config.host == "h"
    raw["DB_CONFIG"] = json.dumps(base["DB_CONFIG"]).encode()
    assert RuntimeSettings.model_validate(raw).db_config.host == "h"
    with pytest.raises(Exception):
        RuntimeSettings.model_validate({"DB_CONFIG": "[1,2]"})
    assert "statement_timeout=15000" in _db().to_psycopg_kwargs()["options"]
    with pytest.raises(Exception):
        _db(port=0)
    with pytest.raises(Exception):
        _db(connect_timeout=31)
