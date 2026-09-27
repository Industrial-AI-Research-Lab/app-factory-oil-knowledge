"""Compact parser/model contract suite"""
from __future__ import annotations
import ast, json, math, sys
from pathlib import Path
from typing import get_args
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import csv_adapter.parser as pm  # noqa: E402
from csv_adapter import models as mm  # noqa: E402
from csv_adapter.config import *  # noqa: E402,F403
from csv_adapter.config import NUMERIC_PATTERN_SOURCE as NPS  # noqa: E402
from csv_adapter.models import CsvRow, GranularUnit  # noqa: E402
from csv_adapter.parser import parse_csv_content as P  # noqa: E402
H = "activity_id;activity_name;volume;measurement"
HC = "activity_id,activity_name,volume,measurement"
F = H + ";structure;edges;granular_unit"
PR = Path(__file__).resolve().parents[1] / "src" / "csv_adapter" / "parser.py"
MO = Path(__file__).resolve().parents[1] / "src" / "csv_adapter" / "models.py"
CF = Path(__file__).resolve().parents[1] / "src" / "csv_adapter" / "config.py"
def R(aid="A1", n="N1", v="1.5", m="pcs", s="", e="", g="", full=False):
    return f"{aid};{n};{v};{m}" if not full else f"{aid};{n};{v};{m};{s};{e};{g}"
def M(rows, h=H): return "\n".join([h, *rows])
def C(x): return str(x).split(":", 1)[0].strip()
def Q(p): return '"' + p.replace('"', '""') + '"'
def J(o): return Q(json.dumps(o))
def VE(c, d=";"):
    try: P(c, d)
    except ValueError as e: return (C(e), str(e), e)
    raise AssertionError(f"no ValueError: {c!r}")
def RE(c, d=";"):
    try: P(c, d)
    except RuntimeError as e: return (str(e), e)
    raise AssertionError(f"no RuntimeError: {c!r}")
def LOC(field):
    class L(BaseModel):
        model_config = ConfigDict(extra="forbid")
        granular_unit: str | None = None; edges: str | None = None; structure: str | None = None
        volume: str | None = None; activity_id: str | None = None; activity_name: str | None = None
        measurement: str | None = None; mystery: str | None = None
    try: L.model_validate({"unknown_field": "x"} if field == "mystery" else {field: 123, "mystery": "ok"})
    except ValidationError as e: return e
    raise AssertionError("no ValidationError")
def test_aliases():
    assert mm.StructureItem == tuple[str, str, int, int]
    a = get_args(mm.Edge); assert a[0] is str and sorted(get_args(a[1])) == ["FF", "FS", "SF", "SS"] and a[2] is int
    assert sorted(get_args(mm.Dialect)) == [",", ";"] and sorted(get_args(mm.NormalizeDelimiter)) == [",", ";", "auto"]
def test_frozen_extra():
    r = CsvRow(activity_id="A1", activity_name="N", volume=1.0, measurement="m")
    u = GranularUnit(code="c", name="n", measurement="m"); w = mm.CsvWarning(code="W", message="m")
    for o, f in ((r, "activity_id"), (u, "code"), (w, "code")):
        with pytest.raises(ValidationError): setattr(o, f, "X")
    with pytest.raises(ValidationError): CsvRow(activity_id="A", activity_name="N", volume=1.0, measurement="m", bogus=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError): GranularUnit(code="c", name="n", measurement="m", bogus=1)  # type: ignore[call-arg]
    with pytest.raises(ValidationError): mm.NormalizeResult(rows=[], dialect=";", oops=1)  # type: ignore[call-arg]
def test_model_semantics():
    assert GranularUnit(code="c", name="n", measurement="m").category == ""
    assert GranularUnit(code="c", name="n", measurement="m", category=None).category == ""  # type: ignore[arg-type]
    assert GranularUnit(code="c", name="n", measurement="m", category="x").category == "x"
    for k in ({"code": "", "name": "n", "measurement": "m"}, {"code": "  ", "name": "n", "measurement": "m"}, {"code": "c", "name": "", "measurement": "m"}, {"code": "c", "name": "n", "measurement": "  "}):
        with pytest.raises(ValidationError): GranularUnit(**k)  # type: ignore[arg-type]
    a = CsvRow(activity_id="A1", activity_name="N", volume=1.0, measurement="m")
    b = CsvRow(activity_id="A2", activity_name="N", volume=2.0, measurement="m")
    assert a.structure == [] and a.edges == [] and a.granular_unit is None and b.structure == []
    # frozen blocks attribute reassignment; this append checks mutable-default isolation, not deep immutability.
    a.structure.append(("c", "n", 1, 2)); assert b.structure == []  # type: ignore[arg-type]
    assert mm.NormalizeResult(rows=[], dialect=";").warnings == [] and mm.WriteResult(content="h\n", row_count=0, dialect=";").warnings == []
    for f in ("activity_id", "activity_name", "measurement"):
        k = {"activity_id": "A", "activity_name": "N", "volume": 1.0, "measurement": "m"}; k[f] = "   "
        with pytest.raises(ValidationError): CsvRow(**k)  # type: ignore[arg-type]
    for v in (math.inf, -math.inf, math.nan):
        with pytest.raises(ValidationError): CsvRow(activity_id="A", activity_name="N", volume=v, measurement="m")
    assert mm.CsvWarning(code="W", message="h").row is None and mm.CsvWarning(code="W", message="h").column is None
    assert set(mm.NormalizeResult.model_fields) == {"rows", "dialect", "warnings"} and set(mm.WriteResult.model_fields) == {"content", "row_count", "dialect", "warnings"}
def test_bom():
    rows, d = P("﻿" + M([R()]), "auto"); assert len(rows) == 1 and rows[0].activity_id == "A1" and d in (";", ",")
    c, _, e = VE("﻿﻿" + M([R()]), "auto"); assert c in (E_HEADER_UNKNOWN, E_REQUIRED_MISSING) and e.__cause__ is None
    assert VE("activity_id;activity_﻿name;volume;measurement\nA1;N1;1;m", ";")[0] == E_HEADER_UNKNOWN
@pytest.mark.parametrize("c", ["", "   ", " \t\n ", "\n", "﻿", "﻿  \n "])
def test_empty(c): assert VE(c, "auto")[0] == E_EMPTY_CONTENT and VE(c, "auto")[2].__cause__ is None
@pytest.mark.parametrize("c", [None, 123, b"a;b", ["x"]])
def test_nonstr(c): assert VE(c, "auto")[0] == E_EMPTY_CONTENT  # type: ignore[arg-type]
@pytest.mark.parametrize("d", ["|", "", "auto ", "AUTO", "; ", None, 123])
def test_baddelim(d):
    c, _, e = VE(M([R()]), d)  # type: ignore[arg-type]
    assert c == E_DELIMITER_INVALID and e.__cause__ is None
def test_headers():
    r, _ = P("measurement;volume;activity_name;activity_id\npcs;2.5;N1;A1", ";")
    assert (r[0].activity_id, r[0].volume, r[0].measurement) == ("A1", 2.5, "pcs")
    assert P(" activity_id ; activity_name ; volume ; measurement \nA1;N1;1;m", ";")[0][0].activity_id == "A1"
    assert VE("Activity_id;activity_name;volume;measurement\nA1;N1;1;m", ";")[0] == E_HEADER_UNKNOWN
    assert VE(H + ";surprise\nA1;N1;1;m;x", ";")[0] == E_HEADER_UNKNOWN
    assert VE("activity_id;activity_name;volume;measurement;volume\nA1;N1;1;m;2", ";")[0] == E_HEADER_DUPLICATE
    assert VE("activity_id; activity_id ;volume;measurement\nA1;A1;1;m", ";")[0] == E_HEADER_DUPLICATE
    for drop in ("activity_id", "activity_name", "volume", "measurement"):
        k = [x for x in ("activity_id", "activity_name", "volume", "measurement") if x != drop]
        assert VE(";".join(k) + "\n" + ";".join(["v"] * len(k)), ";")[0] == E_REQUIRED_MISSING
    r, _ = P(M([R()]), ";"); assert r[0].structure == [] and r[0].edges == [] and r[0].granular_unit is None
def test_rows():
    c, t, _ = VE(M(["A1;N1;1"]), ";"); assert c == E_ROW_WIDTH and "row 1" in t
    assert VE(M(["A1;N1;1;m;EXTRA"]), ";")[0] == E_ROW_WIDTH
    c, _, e = VE(H + '\nA1;"unclosed;1;m', ";"); assert c == E_ROW_WIDTH and e.__cause__ is None
    c, t, _ = VE(M([R(aid="A1"), R(aid="  A1  ")]), ";"); assert c == E_DUPLICATE_ID and "row 2" in t
    for f in ("activity_id", "activity_name", "measurement"):
        p = {"activity_id": "A1", "activity_name": "N1", "volume": "1", "measurement": "m"}; p[f] = "   "
        c, t, _ = VE(M([f"{p['activity_id']};{p['activity_name']};{p['volume']};{p['measurement']}"]), ";")
        assert c == E_REQUIRED_MISSING and "row 1" in t
@pytest.mark.parametrize("t,x", [("1", 1.0), ("-12", -12.0), ("+12", 12.0), ("0.5", 0.5), ("-3.25", -3.25), ("1e10", 1e10), ("1E-3", 0.001), ("+2.5E+4", 25000.0), ("007", 7.0)])
def test_vok(t, x): assert P(M([R(v=t)]), ";")[0][0].volume == x
@pytest.mark.parametrize("t", ["", "   ", "12,5", "1,000", "NaN", "nan", "Inf", "inf", "-Infinity", "0x10", "1..2", "1e", "--3", "3.", ".5", "1 2", "12m"])
def test_vbad(t):
    c = H + "\n" + (f'A1;N1;"{t}";m' if "," in t else R(v=t))
    code, msg, _ = VE(c, ";"); assert code == E_VOLUME_INVALID and "row 1" in msg and (t.strip() not in msg or t.strip() in ("", "1 2", "12m", "12,5"))
def test_vmeta():
    assert VE(M([R(v="1e999")]), ";")[0] == E_VOLUME_NON_FINITE
    assert NPS == r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$" and pm._NUMERIC_RE.pattern == NPS  # noqa: SLF001
def test_delim():
    r, d = P(M(["A1;N1;1;m"]), ";"); assert d == ";" and r[0].activity_id == "A1"
    r, d = P("activity_id,activity_name,volume,measurement\nA1,N1,2,pcs", ","); assert d == "," and r[0].volume == 2.0
    assert P(M(["A1;N1;1;m", "A2;N2;2;x"]), "auto")[1] == ";"
    assert P("activity_id,activity_name,volume,measurement\nA1,N1,1,m\nA2,N2,2,x", "auto")[1] == ","
    assert pm._select_delimiter("activity_id\nA1") == ";" and pm._select_delimiter("activity_id;activity_name,volume;measurement\nx") == ";"  # noqa: SLF001
    assert pm._select_delimiter(H) == ";" and pm._select_delimiter(HC) == ","  # noqa: SLF001
    r, d = P(H + '\nA1;"multi\nline;name";3;pcs', "auto"); assert d == ";" and r[0].activity_name == "multi\nline;name"
    r, d = P(HC + '\nA1,"multi,line",3,pcs', "auto"); assert d == "," and r[0].activity_name == "multi,line"
    good = [R(aid=f"A{i}", v=str(i)) for i in range(5)]; full = "\n".join([H, *good, *["BROKEN,TAIL,EXTRA,ROW,WITH,COMMAS"] * 10])
    trunc = "\n".join([H, *good])
    # Trailing malformed tails must not flip detection; detection is a pure
    # header/consistency vote now (backend_file), strictness lives in the reader.
    assert pm._select_delimiter(full) == ";" and pm._select_delimiter(trunc) == ";"  # noqa: SLF001
    assert VE(full, ";")[0] == E_ROW_WIDTH and VE('A1;"unclosed', ";")[0] == E_ROW_WIDTH
def test_bounds():
    assert VE("x" * (MAX_CONTENT_CHARS + 1), "auto")[0] == E_LIMIT_CONTENT
    assert VE(M([R(aid=f"A{i}", v="1") for i in range(MAX_ROWS + 1)]), ";")[0] == E_LIMIT_ROWS
    assert len(P(M([R(aid=f"A{i}", v="1") for i in range(MAX_ROWS)]), ";")[0]) == MAX_ROWS
    h = ";".join(["activity_id", "activity_name", "volume", "measurement"] + [f"c{i}" for i in range(MAX_COLS)])
    assert VE(h + "\n" + ";".join(["v"] * (MAX_COLS + 4)), ";")[0] in (E_LIMIT_COLS, E_HEADER_UNKNOWN)
    assert (MAX_COLS, MAX_CONTENT_CHARS, MAX_ROWS, MAX_CELL_CHARS, MAX_STRUCTURE_ITEMS, MAX_EDGES_PER_ROW, MAX_NESTED_DEPTH) == (16, 200000, 2000, 16000, 64, 64, 3)
    c, t, _ = VE(f"activity_id;activity_name;volume;{'x' * (MAX_CELL_CHARS + 1)}\nA1;N1;1;m", ";")
    assert c == E_LIMIT_CELL and "row" not in t
    big = "y" * (MAX_CELL_CHARS + 1); c, t, _ = VE(M([f"A1;{big};1;m"]), ";")
    assert c == E_LIMIT_CELL and "row 1" in t and big[:32] not in t
    cell = J([[f"C{i}", f"N{i}", 1, i] for i in range(MAX_STRUCTURE_ITEMS + 1)])
    assert VE(F + f"\nA1;N1;1;m;{cell};;", ";")[0] == E_LIMIT_NESTED
    cell = J([[f"P{i}", "FS", 0] for i in range(MAX_EDGES_PER_ROW + 1)])
    assert VE(F + f"\nA1;N1;1;m;;{cell};", ";")[0] == E_LIMIT_NESTED
def test_nested():
    assert P(F + "\nA1;N1;1;m;\"[('C1', 'N1', 1, 2)]\";;", ";")[0][0].structure == [("C1", "N1", 1, 2)]
    assert VE(F + "\nA1;N1;1;m;not-json-nor-python;;", ";")[0] == E_NESTED_INVALID
    assert VE(F + "\nA1;N1;1;m;" + Q("[[[[1]]]]") + ";;", ";")[0] == E_LIMIT_NESTED
    pm._check_depth([["a"], ["b"]], 1, 1)  # noqa: SLF001
    try: pm._check_depth([[[["too-deep"]]]], 1, 1)  # noqa: SLF001
    except ValueError as e: assert C(e) == E_LIMIT_NESTED
    else: raise AssertionError("depth not limited")
    assert VE(F + "\nA1;N1;1;m;" + Q("{1, 2}") + ";;", ";")[0] == E_NESTED_INVALID
def test_struct_edges():
    assert P(F + "\nA1;N1;1;m;;;", ";")[0][0].structure == []
    assert P(F + f"\nA1;N1;1;m;{J([['C1', 'N1', 2, 3]])};;", ";")[0][0].structure == [("C1", "N1", 2, 3)]
    for p in ('{"not": "a-list"}', '[["only", "three", 1]]', '[["", "N", 1, 2]]', '[["C", "", 1, 2]]', '[["C", "N", true, 2]]', '[["C", "N", 1.5, 2]]'):
        assert VE(F + f"\nA1;N1;1;m;{Q(p)};;", ";")[0] == E_STRUCTURE_INVALID
    assert P(F + "\nA1;N1;1;m;;;", ";")[0][0].edges == []
    r, _ = P(F + f"\nA1;N1;1;m;;{J([['P1', 'FS', 0], ['P2', 'SS', 1], ['P3', 'FF', -1], ['P4', 'SF', 2]])};", ";")
    assert ("P4", "SF", 2) in r[0].edges
    for p in ('{"not": "a-list"}', '[["P1", "FS"]]', '[["", "FS", 0]]', '[["P1", "XX", 0]]', '[["P1", "FS", "0"]]', '[["P1", "FS", true]]', '[["P1", "fs", 0]]'):
        assert VE(F + f"\nA1;N1;1;m;;{Q(p)};", ";")[0] == E_EDGE_INVALID
def test_granular():
    assert P(F + "\nA1;N1;1;m;;;   ", ";")[0][0].granular_unit is None
    for pl, exp in ((json.dumps({"code": "c", "name": "n", "measurement": "m"}), ""), (json.dumps({"code": "c", "name": "n", "measurement": "m", "category": None}), ""), (json.dumps({"code": "c", "name": "n", "measurement": "m", "category": "k"}), "k"), (json.dumps(["c", "n", "m", "k"]), "k"), (json.dumps(["c", "n", "m", None]), "")):
        assert P(F + f"\nA1;N1;1;m;;;{Q(pl)}", ";")[0][0].granular_unit.category == exp
    for pl in ('{"code": "c", "name": "n", "measurement": "m", "extra": 1}', '{"code": "", "name": "n", "measurement": "m"}', '{"code": "c", "name": "n"}', '["c", "n", "m"]', '"just-a-string"', "123"):
        assert VE(F + f"\nA1;N1;1;m;;;{Q(pl)}", ";")[0] == E_GRANULAR_UNIT_INVALID
def test_sf_multiline():
    s, e, g = json.dumps([["C1", "S1", 1, 1]]), json.dumps([["A0", "SF", 0]]), json.dumps({"code": "g", "name": "n", "measurement": "m"})
    r, d = P(F + f"\nA1;N1;2.5;pcs;{J(json.loads(s))};{J(json.loads(e))};{J(json.loads(g))}", ";")
    assert d == ";" and r[0].volume == 2.5 and r[0].structure == [("C1", "S1", 1, 1)] and r[0].edges == [("A0", "SF", 0)] and r[0].granular_unit.code == "g"
    r, _ = P(H + '\nA1;"line1\nline2";1;m', ";"); assert len(r) == 1 and r[0].activity_name == "line1\nline2"
def test_safe():
    many = J([[f"C{i}", "N", 1, 1] for i in range(65)])
    cases = ["", H + ";nope\nA1;N1;1;m;x", H + ";volume\nA1;N1;1;m;2", "activity_id;activity_name;volume\nA1;N1;1", M(["A1;N1;1;m;EXTRA"]), M([R(aid="SECRET-ID"), R(aid="SECRET-ID")]), M([R(v="SECRET-VOL")]), M([R(v="1e999")]), F + "\nA1;N1;1;m;broken;;", F + f"\nA1;N1;1;m;{many};;", F + '\nA1;N1;1;m;;;"SECRET-GRAN"']
    for c in cases:
        code, t, ex = VE(c, ";"); assert code.startswith("E_") and ex.__cause__ is None
        for s in ("SECRET-ID", "SECRET-VOL", "SECRET-GRAN"): assert s not in t
    assert "row" not in VE(H + ";nope\nA1;N1;1;m;x", ";")[1] and "row 1" in VE(M([R(v="bad")]), ";")[1]
    try: raise RuntimeError(E_INTERNAL) from None
    except RuntimeError as e: assert str(e) == E_INTERNAL and e.__cause__ is None
def test_internal(monkeypatch):
    _orig = pm.csv.reader
    monkeypatch.setattr(pm.csv, "reader", lambda *a, **k: (_ for _ in ()).throw(OSError("disk gone")))
    assert RE(M([R()]), ";")[0] == E_INTERNAL and RE(M([R()]), ";")[1].__cause__ is None
    monkeypatch.setattr(pm.csv, "reader", _orig)
    monkeypatch.setattr(pm, "CsvRow", lambda **k: (_ for _ in ()).throw(OSError("unexpected")))
    assert RE(M([R()]), ";")[0] == E_INTERNAL
    for f, exp in (("granular_unit", E_GRANULAR_UNIT_INVALID), ("edges", E_EDGE_INVALID), ("structure", E_STRUCTURE_INVALID), ("volume", E_VOLUME_INVALID), ("activity_id", E_REQUIRED_MISSING), ("activity_name", E_REQUIRED_MISSING), ("measurement", E_REQUIRED_MISSING)):
        tpl = LOC(f); monkeypatch.setattr(pm, "CsvRow", lambda **k: (_ for _ in ()).throw(tpl))
        c, t, ex = VE(M([R()]), ";"); assert c == exp and "row 1" in t and ex.__cause__ is None
    try: (_ for _ in ()).throw(ValidationError.from_exception_data("X", [{"loc": ("mystery",), "type": "missing", "input": "x"}]))
    except ValidationError as tpl:
        monkeypatch.setattr(pm, "CsvRow", lambda **k: (_ for _ in ()).throw(tpl))
        assert RE(M([R()]), ";")[0] == E_INTERNAL and RE(M([R()]), ";")[1].__cause__ is None
    try: CsvRow(activity_id=" ", activity_name="N", volume=1.0, measurement="m")
    except ValidationError as real:
        with monkeypatch.context() as mp:
            mp.setattr(ValidationError, "errors", lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("broken")))
            mp.setattr(pm, "CsvRow", lambda **k: (_ for _ in ()).throw(real))
            assert RE(M([R()]), ";")[0] == E_INTERNAL
    class W(ValueError): pass
    monkeypatch.setattr(pm, "CsvRow", lambda **k: (_ for _ in ()).throw(W("weird")))
    assert RE(M([R()]), ";")[0] == E_INTERNAL
def test_static():
    t = PR.read_text(encoding="utf-8"); assert "Sniffer" not in t and "pandas" not in t.lower() and "numpy" not in t.lower()
    tree = ast.parse(t); mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import): mods.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom): mods.add("." * n.level + (n.module or ""))
    assert {".config", ".models"} <= mods and "csv" in mods
    rd = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "reader"]
    # Single strict entry point: delimiter voting counts without strict in backend_file.
    assert len(rd) == 1 and all({k.arg: ast.unparse(k.value) for k in c.keywords}.get("strict") == "True" for c in rd)
    assert ('content.startswith("\\ufeff")' in t or "content.startswith('\ufeff')" in t) and t.count("ufeff") == 1
    imp = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "config" for a in n.names}
    assert {"E_EMPTY_CONTENT", "E_DELIMITER_INVALID", "E_HEADER_UNKNOWN", "E_HEADER_DUPLICATE", "E_REQUIRED_MISSING", "E_ROW_WIDTH", "E_DUPLICATE_ID", "E_VOLUME_INVALID", "E_VOLUME_NON_FINITE", "E_NESTED_INVALID", "E_STRUCTURE_INVALID", "E_EDGE_INVALID", "E_GRANULAR_UNIT_INVALID", "E_LIMIT_CONTENT", "E_LIMIT_ROWS", "E_LIMIT_COLS", "E_LIMIT_CELL", "E_LIMIT_NESTED", "E_INTERNAL"} <= imp
    for p in (MO, PR, CF):
        # Docstrings may mention layer names; only real imports count.
        tree_p = ast.parse(p.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for n in ast.walk(tree_p):
            if isinstance(n, ast.Import):
                imported.update(a.name.split(".")[0].lower() for a in n.names)
            elif isinstance(n, ast.ImportFrom):
                if n.module:
                    imported.add(n.module.split(".")[-1].lower())
                imported.update(a.name.split(".")[0].lower() for a in n.names)
        for f in ("boto3", "requests", "psycopg2", "sqlalchemy", "pandas", "storage"):
            assert f not in imported
