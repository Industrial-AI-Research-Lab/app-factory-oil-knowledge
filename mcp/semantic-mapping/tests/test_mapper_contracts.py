"""Mapper contracts: donor parity + adapter wiring/guards. No DB/network."""
from __future__ import annotations
import inspect
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import numpy as np
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import semantic_mapping_adapter.hierarchies as hm  # noqa: E402
import semantic_mapping_adapter.works as wm  # noqa: E402
from semantic_mapping_adapter import RuntimeSettings, load_runtime_settings, search_hierarchies, search_works  # noqa: E402
from stairs_semantic_mapping.infrastructure.vector_query import PgVectorStore, PgVectorStoreConfig, VectorQueryResult  # noqa: E402
from stairs_semantic_mapping.mapping.hierarchical_mapper import HierarchicalMapper, HierarchicalMapperConfig  # noqa: E402
from stairs_semantic_mapping.mapping.simple_mapper import SimpleMapper, SimpleMapperConfig  # noqa: E402
def _settings(**ov: Any) -> RuntimeSettings:
    base: dict[str, Any] = {"DB_CONFIG": {"host": "h", "port": 5432, "dbname": "d", "user": "u", "password": "p"}, "EMBEDDING_HOST_CATEGORIES": "http://cat/embed", "EMBEDDING_HOST_TASKS": "http://tasks/embed", "EMBEDDING_HOST_HIERARCHIES": "http://hier/embed"}
    base.update(ov)
    return RuntimeSettings.model_validate(base)
class RecordingEmbedder:
    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[list[str]] = []
    def embed(self, texts: Any) -> np.ndarray:
        items = list(texts)
        self.calls.append(items)
        return np.zeros((len(items), self.dim), dtype="float32")
class FakeStore:
    def __init__(self, result: VectorQueryResult) -> None:
        self._result = result
        self.calls: list[dict[str, Any]] = []
    def query(self, query_embeddings: Any, top_k: Any, where: Any = None, include: Any = None) -> Any:
        self.calls.append({"top_k": top_k, "where": where, "include": list(include or [])})
        return self._result
def _empty() -> VectorQueryResult:
    return VectorQueryResult(ids=[], results=[], distances=None, metadatas=None)
class _NoProv:
    @contextmanager
    def get_cursor(self, cursor_factory: Any = None) -> Any:
        raise AssertionError("no IO")
        yield None  # pragma: no cover
def _pg(columns: list[str]) -> PgVectorStore:
    return PgVectorStore(conn_provider=_NoProv(), config=PgVectorStoreConfig(table_name="t", metadata_columns=columns))  # type: ignore[arg-type]
def _works_input(name: str = "wall") -> Any:
    from stairs_semantic_mapping.utils.mapping_result_model import InputWorkMappingUnit
    return InputWorkMappingUnit(work_name=name, work_measurement="m2")
def _hier_meta(cid: Any = 10, name: str = "Concrete", meas: str = "m3", code: str = "c1") -> Any:
    return [[{"category_id": cid, "category_name": name, "measurement": meas, "task_code": code}]]
def test_simple_empty_shapes() -> None:
    assert SimpleMapper(RecordingEmbedder(), FakeStore(_empty())).semantic_search([], top_k=1) == {"ids": [], "names": []}
    m = SimpleMapper(RecordingEmbedder(), FakeStore(_empty()), config=SimpleMapperConfig(metadata_fields=["category_id"]))
    assert m.semantic_search([], top_k=1, with_distances=True) == {"ids": [], "names": [], "metadatas": [], "scores": []}
def test_simple_prefix_and_top_k_or() -> None:
    emb = RecordingEmbedder()
    store = FakeStore(VectorQueryResult(ids=[["1"]], results=[["Task A"]]))
    mapper = SimpleMapper(emb, store, config=SimpleMapperConfig(top_k=1))
    mapper.semantic_search(["wall"], top_k=3)
    assert emb.calls[0] == ["query: wall"] and store.calls[0] == {"top_k": 3, "where": {}, "include": ["results"]}
    mapper.semantic_search(["wall"], top_k=0)
    assert store.calls[1]["top_k"] == 1
    mapper.semantic_search(["wall"], top_k=True)  # type: ignore[arg-type]
    assert store.calls[2]["top_k"] is True  # donor `or`: truthy True forwarded, never True == 1
    assert SimpleMapper(emb, FakeStore(_empty()), config=SimpleMapperConfig(query_prefix=""))._build_queries(["a"]) == ["a"]
def test_simple_where_include_and_validation() -> None:
    canned = VectorQueryResult(ids=[["1"]], results=[["A"]], distances=[[0.5]], metadatas=[[{"code": "L1", "extra": "drop"}]])
    store = FakeStore(canned)
    mapper = SimpleMapper(RecordingEmbedder(), store, config=SimpleMapperConfig(where_clause_fields={"code_hierarchical_work_type": [2]}, metadata_fields=["code"]))
    out = mapper.semantic_search(["h"], top_k=4, with_distances=True)
    assert store.calls[0]["where"] == {"code_hierarchical_work_type": [2]} and store.calls[0]["include"] == ["results", "distances", "metadatas"]
    assert out["metadatas"] == [[{"code": "L1"}]] and out["scores"] == [[0.5]]
    ok = SimpleMapper(RecordingEmbedder(), _pg(["code"]), config=SimpleMapperConfig(where_clause_fields={"code_hierarchical_work_type": [3]}, metadata_fields=["code"]))
    assert ok.config.where_clause_fields == {"code_hierarchical_work_type": [3]}
    assert SimpleMapper(RecordingEmbedder(), _pg(["code"]), config=SimpleMapperConfig(where_clause_fields={"anything": [1]})).config.where_clause_fields == {"anything": [1]}
    with pytest.raises(ValueError):
        SimpleMapper(RecordingEmbedder(), _pg(["category_id"]), config=SimpleMapperConfig(metadata_fields=["nope"]))
def test_hierarchical_empty_exact_donor_shape() -> None:
    mapper = HierarchicalMapper(RecordingEmbedder(), RecordingEmbedder(), FakeStore(_empty()), FakeStore(_empty()))
    assert mapper.semantic_search([]) == {"ids": [], "names": [], "categories": []}  # no `measurements` when empty
    assert mapper.semantic_search([], with_distances=True) == {"ids": [], "names": [], "categories": [], "scores": []}
def test_hierarchical_two_stage_mapping() -> None:
    cat_emb, name_emb = RecordingEmbedder(), RecordingEmbedder()
    cat_store = FakeStore(VectorQueryResult(ids=[["10", "20"]], results=[[]]))
    task_store = FakeStore(VectorQueryResult(ids=[["c1"]], results=[["Task A"]], distances=[[0.2]], metadatas=_hier_meta()))
    mapper = HierarchicalMapper(cat_emb, name_emb, cat_store, task_store, config=HierarchicalMapperConfig(category_top_k=5, name_top_k=2))
    out = mapper.semantic_search(["foundations"], top_k=2, with_distances=True)
    assert cat_emb.calls[0] == ["query: foundations"] and cat_store.calls[0]["top_k"] == 5
    assert task_store.calls[0]["where"] == {"category_id": [10, 20]} and task_store.calls[0]["top_k"] == 2
    assert out["ids"] == [["c1"]] and out["categories"] == [["Concrete"]] and out["measurements"] == [["m3"]] and out["scores"] == [[0.2]]
    mapper.semantic_search(["foundations"], top_k=0)
    assert task_store.calls[1]["top_k"] == 2
def test_hierarchical_filters_and_validation() -> None:
    cat0 = FakeStore(VectorQueryResult(ids=[[]], results=[[]]))
    task0 = FakeStore(VectorQueryResult(ids=[["c1"]], results=[["A"]], distances=[[0.1]], metadatas=_hier_meta(cid=1, name="C", meas="m")))
    HierarchicalMapper(RecordingEmbedder(), RecordingEmbedder(), cat0, task0).semantic_search(["x"])
    assert task0.calls[0]["where"] is None
    task2 = FakeStore(VectorQueryResult(ids=[["c1"]], results=[["A"]], distances=[[0.1]], metadatas=_hier_meta(cid=3, name="C", meas="m")))
    HierarchicalMapper(RecordingEmbedder(), RecordingEmbedder(), FakeStore(VectorQueryResult(ids=[[None, "3"]], results=[[]])), task2).semantic_search(["x"])
    assert task2.calls[0]["where"] == {"category_id": [3]}
    bad = HierarchicalMapper(RecordingEmbedder(), RecordingEmbedder(), FakeStore(VectorQueryResult(ids=[["not-an-int"]], results=[[]])), task2)
    with pytest.raises(ValueError):
        bad.semantic_search(["x"])
    with pytest.raises(ValueError):
        HierarchicalMapper(RecordingEmbedder(), RecordingEmbedder(), FakeStore(_empty()), _pg(["category_id", "category_name", "task_code"]))
    HierarchicalMapper(RecordingEmbedder(), RecordingEmbedder(), FakeStore(_empty()), _pg(["category_id", "category_name", "measurement"]))
def test_adapter_public_api_and_signatures() -> None:
    assert set(wm.__all__) == {"search_works", "InputWorkMappingUnit", "OutputWorkMappingResult", "OutputWorkMappingUnit", "StandardWorkUnit"}
    assert "search_hierarchies" in hm.__all__ and callable(search_works) and callable(search_hierarchies) and callable(load_runtime_settings)
    assert wm.search_works is search_works and hm.search_hierarchies is search_hierarchies
    for fn in (search_works, search_hierarchies):
        assert inspect.signature(fn).parameters["settings"].kind is inspect.Parameter.KEYWORD_ONLY
@pytest.mark.parametrize(("kwargs", "expected"), [({"work_name": "w", "work_measurement": "m", "bwd_name": "b", "ose_name": "o", "occ_name": "c"}, "w, m || b || o || c"), ({"work_name": "w", "work_measurement": "m", "ose_name": "o"}, "w, m || o"), ({"work_name": "wall", "work_measurement": "m2"}, "wall, m2 || ")])
def test_adapter_format_work_query(kwargs: dict[str, Any], expected: str) -> None:
    from stairs_semantic_mapping.utils.mapping_result_model import InputWorkMappingUnit
    assert wm._format_work_query(InputWorkMappingUnit(**kwargs)) == expected
def test_adapter_build_works_mapper_wiring(monkeypatch: Any) -> None:
    seen_tei: list[dict[str, Any]] = []
    seen_stores: list[Any] = []
    seen_mappers: list[dict[str, Any]] = []
    class FakeTEI:
        def __init__(self, host: Any = None, batch_size: Any = None, access_token: Any = None, **kw: Any) -> None:
            seen_tei.append({"host": host, "batch_size": batch_size, "token": access_token})
    class FakeStoreCap:
        def __init__(self, conn_provider: Any = None, config: Any = None, **kw: Any) -> None:
            seen_stores.append(config)
    class FakeMapper:
        def __init__(self, **kw: Any) -> None:
            seen_mappers.append(kw)
    monkeypatch.setattr(wm, "TEIEmbeddingModel", FakeTEI)
    monkeypatch.setattr(wm, "NormalizingEmbeddingModel", lambda base, **kw: base)
    monkeypatch.setattr(wm, "SecurePostgresConnectionProvider", lambda cfg: SimpleNamespace(cfg=cfg))
    monkeypatch.setattr(wm, "ValidatingPgVectorStore", FakeStoreCap)
    monkeypatch.setattr(wm, "HierarchicalMapper", FakeMapper)
    wm._build_works_mapper(_settings(BATCH_SIZE_CATEGORIES=7, BATCH_SIZE_TASKS=9, TOP_K_CATEGORIES=5, TOP_K_NAMES=1))
    assert sorted(s["host"] for s in seen_tei) == ["http://cat/embed", "http://tasks/embed"] and sorted(s["batch_size"] for s in seen_tei) == [7, 9]
    assert all(s["token"] is None for s in seen_tei) and sorted(c.table_name for c in seen_stores) == ["granular_category", "granular_name"]
    assert (seen_mappers[0]["config"].category_top_k, seen_mappers[0]["config"].name_top_k) == (5, 1)
def test_adapter_works_category_field_alignment(monkeypatch: Any) -> None:
    assert _settings().category_id_field == "category_id" and _settings().category_name_field == "category_name"
    seen: list[Any] = []
    seen_cfg: list[Any] = []
    class FakeStoreCap:
        def __init__(self, conn_provider: Any = None, config: Any = None, required_metadata_fields: Any = None, **kw: Any) -> None:
            seen.append((config, tuple(required_metadata_fields or ())))
    class FakeMapper:
        def __init__(self, **kw: Any) -> None:
            seen_cfg.append(kw)
    monkeypatch.setattr(wm, "TEIEmbeddingModel", lambda host=None, batch_size=None, access_token=None, **kw: SimpleNamespace(host=host))
    monkeypatch.setattr(wm, "NormalizingEmbeddingModel", lambda base, **kw: base)
    monkeypatch.setattr(wm, "SecurePostgresConnectionProvider", lambda cfg: SimpleNamespace(cfg=cfg))
    monkeypatch.setattr(wm, "ValidatingPgVectorStore", FakeStoreCap)
    monkeypatch.setattr(wm, "HierarchicalMapper", FakeMapper)
    wm._build_works_mapper(_settings(CATEGORY_ID_FIELD="cat_id_custom", CATEGORY_NAME_FIELD="cat_name_custom"))
    cfg, req = {c.table_name: (c, r) for c, r in seen}["granular_name"]
    assert "cat_id_custom" in cfg.metadata_columns and "cat_name_custom" in cfg.metadata_columns and "cat_id_custom" in req and "cat_name_custom" in req
    assert seen_cfg[0]["config"].category_id_field == "cat_id_custom" and seen_cfg[0]["config"].category_name_field == "cat_name_custom"
def test_adapter_build_hierarchies_mapper_wiring(monkeypatch: Any) -> None:
    seen_tei: list[dict[str, Any]] = []
    seen_stores: list[Any] = []
    seen_mappers: list[dict[str, Any]] = []
    class FakeStoreCap:
        def __init__(self, conn_provider: Any = None, config: Any = None, required_metadata_fields: Any = None, **kw: Any) -> None:
            seen_stores.append((config, tuple(required_metadata_fields or ())))
    class FakeMapper:
        def __init__(self, **kw: Any) -> None:
            seen_mappers.append(kw)
    monkeypatch.setattr(hm, "TEIEmbeddingModel", lambda host=None, batch_size=None, access_token=None, **kw: seen_tei.append({"host": host, "batch_size": batch_size, "token": access_token}) or SimpleNamespace(host=host))
    monkeypatch.setattr(hm, "NormalizingEmbeddingModel", lambda base, **kw: base)
    monkeypatch.setattr(hm, "SecurePostgresConnectionProvider", lambda cfg: SimpleNamespace(cfg=cfg))
    monkeypatch.setattr(hm, "ValidatingPgVectorStore", FakeStoreCap)
    monkeypatch.setattr(hm, "SimpleMapper", FakeMapper)
    hm._build_hierarchies_mapper(_settings(), level=3, top_k=4)
    assert seen_tei[0]["host"] == "http://hier/embed" and seen_tei[0]["token"] is None and isinstance(seen_tei[0]["batch_size"], int)
    cfg, required = seen_stores[0]
    assert cfg.table_name == "semantic_hierarchical_works" and cfg.metadata_columns == ["code"] and required == ("code",)
    assert seen_mappers[0]["config"].top_k == 4 and seen_mappers[0]["config"].where_clause_fields == {"code_hierarchical_work_type": [3]}
def test_adapter_search_works_delegates_ordered(monkeypatch: Any) -> None:
    calls: dict[str, Any] = {}
    class FakeMapper:
        def semantic_search(self, inputs: Any = None, top_k: Any = None, with_distances: Any = None) -> Any:
            calls.update({"inputs": inputs, "top_k": top_k, "dist": with_distances})
            return {"ids": [["c1"]], "names": [["Task A"]], "measurements": [["m3"]], "categories": [["Concrete"]], "scores": [[0.25]]}
    monkeypatch.setattr(wm, "_build_works_mapper", lambda s: FakeMapper())
    out = search_works([_works_input("wall")], 5, settings=_settings())
    assert calls == {"inputs": ["wall, m2 || "], "top_k": 5, "dist": True}
    assert out.result[0].work_name == "wall" and out.result[0].top_k_units[0].code == "c1" and out.result[0].top_k_distances == (0.25,)
def test_adapter_search_works_empty_skips_mapper(monkeypatch: Any) -> None:
    def _fail(s: Any) -> Any:
        raise AssertionError("mapper must not be built for empty inputs")
    monkeypatch.setattr(wm, "_build_works_mapper", _fail)
    out = search_works([], 5, settings=_settings())
    assert list(out.result) == []
@pytest.mark.parametrize("payload", [{"ids": [], "names": [], "measurements": [], "categories": [], "scores": []}, {"ids": [["c1", "c2"]], "names": [["A"]], "measurements": [["m"]], "categories": [["C"]], "scores": [[0.1]]}, {"ids": [[None]], "names": [["A"]], "measurements": [["m"]], "categories": [["C"]], "scores": [[0.1]]}])
def test_adapter_search_works_cardinality_guards(monkeypatch: Any, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(wm, "_build_works_mapper", lambda s: SimpleNamespace(semantic_search=lambda **kw: payload))
    with pytest.raises(ValueError, match="cardinalities"):
        search_works([_works_input()], 5, settings=_settings())
@pytest.mark.parametrize("err", [ValueError("boom SECRET-W-1"), TypeError("boom SECRET-W-1")])
def test_adapter_search_works_score_and_mapper_redaction(monkeypatch: Any, err: Exception) -> None:
    bad = SimpleNamespace(semantic_search=lambda **kw: {"ids": [["c1"]], "names": [["A"]], "measurements": [["m"]], "categories": [["C"]], "scores": [["not-a-float"]]})
    monkeypatch.setattr(wm, "_build_works_mapper", lambda s: bad)
    with pytest.raises(ValueError, match="cardinalities") as exc:
        search_works([_works_input()], 5, settings=_settings())
    assert "not-a-float" not in str(exc.value)
    class Boom:
        def semantic_search(self, **kw: Any) -> Any:
            raise err
    monkeypatch.setattr(wm, "_build_works_mapper", lambda s: Boom())
    with pytest.raises(ValueError, match="works mapping failed") as exc2:
        search_works([_works_input("SECRET-W-1")], 5, settings=_settings())
    assert "SECRET-W-1" not in str(exc2.value) and "boom" not in str(exc2.value)
def test_adapter_search_hierarchies_delegates(monkeypatch: Any) -> None:
    calls: dict[str, Any] = {}
    class FakeMapper:
        def semantic_search(self, inputs: Any = None, top_k: Any = None, with_distances: Any = None) -> Any:
            calls.update({"inputs": inputs, "top_k": top_k, "dist": with_distances})
            return {"names": [["H A"]], "metadatas": [[{"code": "L1"}]], "scores": [[0.1]]}
    monkeypatch.setattr(hm, "_build_hierarchies_mapper", lambda s, level, top_k: FakeMapper())
    out = search_hierarchies(["wall"], level=2, top_k=5, settings=_settings())
    assert calls == {"inputs": ["wall"], "top_k": 5, "dist": True} and out.result[0].top_k_units[0].code == "L1" and out.result[0].top_k_distances == (0.1,)
@pytest.mark.parametrize("payload", [{"names": [], "metadatas": [], "scores": []}, {"names": [["A", "B"]], "metadatas": [[{"code": "L1"}]], "scores": [[0.1]]}, {"names": [["H"]], "metadatas": [[{"code": None}]], "scores": [[0.1]]}, {"names": [["H"]], "metadatas": [["not-a-dict"]], "scores": [[0.1]]}, {"names": [["H"]], "metadatas": [[None]], "scores": [[0.1]]}, {"names": [["H"]], "metadatas": [[{"code": "L1"}]], "scores": [["bad-score"]]}])
def test_adapter_search_hierarchies_guards(monkeypatch: Any, payload: dict[str, Any]) -> None:
    monkeypatch.setattr(hm, "_build_hierarchies_mapper", lambda *a, **k: SimpleNamespace(semantic_search=lambda **kw: payload))
    with pytest.raises(ValueError, match="cardinalities") as exc:
        search_hierarchies(["h"], level=1, top_k=5, settings=_settings())
    assert "bad-score" not in str(exc.value) and "not-a-dict" not in str(exc.value)
@pytest.mark.parametrize("err", [ValueError("boom SECRET-H-1"), TypeError("boom SECRET-H-1")])
def test_adapter_search_hierarchies_failure_redaction(monkeypatch: Any, err: Exception) -> None:
    def _boom(**kw: Any) -> Any:
        raise err
    monkeypatch.setattr(hm, "_build_hierarchies_mapper", lambda *a, **k: SimpleNamespace(semantic_search=_boom))
    with pytest.raises(ValueError, match="hierarchy mapping failed") as exc:
        search_hierarchies(["SECRET-H-1"], level=1, top_k=5, settings=_settings())
    assert "SECRET-H-1" not in str(exc.value) and "boom" not in str(exc.value)


@pytest.mark.parametrize("bad", [0, 11, -1, True, "5", 2.5, None])
def test_adapter_top_k_defense_in_depth(bad: Any) -> None:
    with pytest.raises(ValueError, match=r"top_k must be an int"):
        search_works([{"work_name": "w", "work_measurement": "m"}], bad, settings=_settings())
    with pytest.raises(ValueError, match=r"top_k must be an int"):
        search_hierarchies(["h"], level=1, top_k=bad, settings=_settings())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_adapter_distances_reject_non_finite(bad: float) -> None:
    from semantic_mapping_adapter._common import to_distances

    with pytest.raises(ValueError, match="cardinalities"):
        to_distances([0.1, bad])
