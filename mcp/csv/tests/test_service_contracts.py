"""Compact service contracts for CSV MCP Static only."""

import csv
import inspect
import io
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import csv_adapter.service as svc  # noqa: E402
from csv_adapter.config import (  # noqa: E402
    ALLOWED_DELIMITERS,
    CANONICAL_COLUMNS,
    E_DELIMITER_INVALID,
    E_INTERNAL,
    E_VOLUME_NON_FINITE,
    FORMULA_TRIGGERS,
    NUMERIC_PATTERN_SOURCE,
    W_FORMULA_QUOTED,
)
from csv_adapter.models import CsvRow, NormalizeResult, WriteResult  # noqa: E402

HDR = ["activity_id", "activity_name", "volume", "measurement",
       "structure", "edges", "granular_unit"]


def _row(**o):
    b = {"activity_id": "A1", "activity_name": "Excavation",
         "volume": 2.5, "measurement": "m3"}
    b.update(o)
    return CsvRow(**b)


def _cells(c, delimiter=";"):
    return list(csv.reader(io.StringIO(c), delimiter=delimiter))


def _vol_row(v):
    return CsvRow.model_construct(activity_id="A1", activity_name="n",
                                  volume=v, measurement="m3", structure=[],
                                  edges=[], granular_unit=None)


def _is_internal(exc, secret=""):
    assert str(exc.value) == E_INTERNAL
    assert exc.value.__cause__ is None
    if secret:
        assert secret not in str(exc.value)


def test_api_signatures_and_results():
    from csv_adapter.models import DownloadRequest, UploadResult

    assert svc.__all__ == ("csv_normalize_inline", "csv_write_normalized", "csv_download_normalize_upload")
    n, w = inspect.signature(svc.csv_normalize_inline), inspect.signature(svc.csv_write_normalized)
    np, wp = list(n.parameters.values()), list(w.parameters.values())
    assert [p.name for p in np] == ["content", "delimiter"] and np[1].default == "auto"
    assert np[0].default is inspect.Parameter.empty and n.return_annotation is NormalizeResult
    assert [p.name for p in wp] == ["rows", "delimiter"] and wp[1].default == ";"
    assert wp[0].default is inspect.Parameter.empty and w.return_annotation is WriteResult
    u = inspect.signature(svc.csv_download_normalize_upload)
    up = list(u.parameters.values())
    assert [p.name for p in up] == ["request", "environ", "http_client", "s3_client"]
    assert up[0].annotation is DownloadRequest and u.return_annotation is UploadResult
    assert all(p.default is None for p in up[1:])
    assert set(NormalizeResult.model_fields) == {"rows", "dialect", "warnings"}
    assert "content" not in NormalizeResult.model_fields
    assert set(WriteResult.model_fields) == {"content", "row_count", "dialect", "warnings"}


@pytest.mark.parametrize("delim", [",", "auto"])
def test_normalize_delegates_exact_args(monkeypatch, delim):
    seen = {}
    want = "payload"
    row = _row()

    def fake(c, d):
        seen.update(content=c, delimiter=d)
        return ([row], ",") if delim == "," else ([], ";")
    monkeypatch.setattr(svc, "parse_csv_content", fake)
    out = svc.csv_normalize_inline(want, delimiter=delim) if delim == "," else svc.csv_normalize_inline(want)
    assert seen == {"content": want, "delimiter": delim}
    assert isinstance(out, NormalizeResult) and out.warnings == []
    assert out.dialect == ("," if delim == "," else ";")


def test_normalize_no_serialization_no_warnings(monkeypatch):
    def boom_w(*a, **k):
        raise AssertionError("normalize must not use csv.writer")
    def boom_j(*a, **k):
        raise AssertionError("normalize must not use json.dumps")
    monkeypatch.setattr(svc.csv, "writer", boom_w)
    monkeypatch.setattr(svc.json, "dumps", boom_j)
    c = "activity_id;activity_name;volume;measurement\nA1;=SUM(1);2.5;m3\nA2;@m;3.0;m3\n"
    out = svc.csv_normalize_inline(c)
    assert [r.activity_name for r in out.rows] == ["=SUM(1)", "@m"]
    assert out.warnings == []


def test_write_header_empty_newline():
    e = svc.csv_write_normalized([])
    assert e.content == ";".join(CANONICAL_COLUMNS) + "\n" and e.row_count == 0
    assert e.dialect == ";" and e.warnings == []
    o = svc.csv_write_normalized([_row(), _row(activity_id="A2")])
    h, r, _ = _cells(o.content)
    assert h == list(CANONICAL_COLUMNS) == HDR
    assert (r[0], r[1], r[3]) == ("A1", "Excavation", "m3")
    assert "\r" not in o.content and "\ufeff" not in o.content
    assert o.content.endswith("\n") and o.content.count("\n") == 3 and o.row_count == 2


def test_write_quoting_minimal_doublequote_multiline():
    assert svc.csv_write_normalized([_row()]).content.split("\n")[1] == "A1;Excavation;2.5;m3;[];[];"
    q = svc.csv_write_normalized([_row(activity_name="cut;fill")])
    assert '"cut;fill"' in q.content and _cells(q.content)[1][1] == "cut;fill"
    dq = svc.csv_write_normalized([_row(activity_name='say "hi"')])
    assert 'say ""hi""' in dq.content and _cells(dq.content)[1][1] == 'say "hi"'
    ml = svc.csv_write_normalized([_row(activity_name="line1\nline2")])
    assert '"line1\nline2"' in ml.content and _cells(ml.content)[1][1] == "line1\nline2"


@pytest.mark.parametrize("d", [";", ","])
def test_write_quoting_follows_active_delimiter(d):
    o = svc.csv_write_normalized([_row(activity_name="cut;fill,mix")], delimiter=d)
    assert _cells(o.content, delimiter=d)[1][1] == "cut;fill,mix"
    assert _cells(o.content, delimiter=d)[0] == list(CANONICAL_COLUMNS)
    assert '"cut;fill,mix"' in o.content


def test_write_compact_json_unicode_repr():
    o = svc.csv_write_normalized([_row()])
    _, r = _cells(o.content)
    assert (r[4], r[5], r[6]) == ("[]", "[]", "")
    assert '": "' not in o.content and '", "' not in o.content
    j = svc.csv_write_normalized([_row(structure=[("S1", "Base", 2, 0)], edges=[("A0", "FS", 1)])])
    _, r = _cells(j.content)
    assert (r[4], r[5]) == ('[["S1","Base",2,0]]', '[["A0","FS",1]]')
    g = svc.csv_write_normalized([_row(granular_unit={"code": "g1", "name": "tonne", "measurement": "t", "category": "mass"})])
    assert _cells(g.content)[1][6] == '{"code":"g1","name":"tonne","measurement":"t","category":"mass"}'
    u = svc.csv_write_normalized([_row(structure=[("S1", "Müller-линия", 1, 0)])])
    assert "Müller-линия" in u.content and "\\u" not in u.content.split("\n")[1]
    for v in (1.0, 2.5, -0.125, 1e20, 0.1):
        _, r = _cells(svc.csv_write_normalized([_row(volume=v)]).content)
        assert r[2] == repr(v) and float(r[2]) == v
    assert _cells(svc.csv_write_normalized([_row(volume=1.0)]).content)[1][2] == "1.0"


@pytest.mark.parametrize("d", [";", ","])
def test_write_delimiters_echo(d):
    o = svc.csv_write_normalized([_row(), _row(activity_id="A2")], delimiter=d)
    assert o.content.split("\n")[0] == d.join(CANONICAL_COLUMNS)
    assert o.row_count == 2 and o.dialect == d
    assert _cells(o.content, delimiter=d)[1][0] == "A1"
    assert set(ALLOWED_DELIMITERS) == {";", ","}
    assert svc.csv_write_normalized([], delimiter=d).dialect == d


@pytest.mark.parametrize("bad", ["auto", "|", "\t", "", ";;"])
def test_write_bad_delimiter_domain(bad):
    with pytest.raises(ValueError, match=E_DELIMITER_INVALID) as e:
        svc.csv_write_normalized([_row()], delimiter=bad)
    assert not isinstance(e.value, RuntimeError) and str(e.value).startswith(E_DELIMITER_INVALID)


@pytest.mark.parametrize("t", sorted(FORMULA_TRIGGERS))
def test_write_all_triggers_single_guard(t):
    m = f"{t}SECRET-{t}-9"
    o = svc.csv_write_normalized([_row(activity_name=m)])
    _, r = _cells(o.content)
    assert r[1] == "'" + m and not r[1].startswith("''")
    assert len(o.warnings) == 1
    w = o.warnings[0]
    assert (w.code, w.row, w.column, w.message) == (W_FORMULA_QUOTED, 1, "activity_name", "formula guard applied")


@pytest.mark.parametrize("c", ["  =SUM(A1)", "\t+cmd", " \t@mention", "   |pipe", "  %pct", "  -evil"])
def test_write_triggers_after_whitespace(c):
    o = svc.csv_write_normalized([_row(activity_name=c)])
    assert _cells(o.content)[1][1] == "'" + c
    assert len(o.warnings) == 1 and o.warnings[0].column == "activity_name"


@pytest.mark.parametrize("c", ["\n=SUM(A1)", "\r@x", "\r\n+cmd", "\v-evil", "\f|pipe", "\u00a0=cmd", "\ufeff%pct"])
def test_write_triggers_after_hidden_whitespace(c):
    ws: list = []
    assert svc._guard_cell(c, 1, "activity_name", ws) == "'" + c
    assert len(ws) == 1 and ws[0].code == W_FORMULA_QUOTED


@pytest.mark.parametrize("c", ["+5", "-3.2", "+1e10", "-0.5", "42", "@"])
def test_write_numeric_exemption(c):
    num = re.compile(NUMERIC_PATTERN_SOURCE).fullmatch(c) is not None
    assert svc._guard_cell(c, 1, "activity_name", []) == (c if num else ("'" + c if c in FORMULA_TRIGGERS else c))


@pytest.mark.parametrize("c", ["+5a", "=1+1", "+ 5", "++", "--x", "  +5"])
def test_write_near_numeric_guarded(c):
    ws: list = []
    assert svc._guard_cell(c, 2, "measurement", ws) == "'" + c
    assert len(ws) == 1 and (ws[0].row, ws[0].column) == (2, "measurement")


def test_write_warnings_safe_positioned():
    s = "=SECRET-PAYLOAD-42"
    o = svc.csv_write_normalized([_row(activity_name=s)])
    w = o.warnings[0]
    assert (w.code, w.row, w.column, w.message) == (W_FORMULA_QUOTED, 1, "activity_name", "formula guard applied")
    assert s not in str(w) and s not in w.message and s not in w.code
    m = svc.csv_write_normalized([_row(activity_id="A1", activity_name="=one"), _row(activity_id="A2", activity_name="plain"), _row(activity_id="A3", activity_name="@three")])
    assert [(x.row, x.column) for x in m.warnings] == [(1, "activity_name"), (3, "activity_name")]
    a = svc.csv_write_normalized([_row(activity_id="=A9", measurement="@m")])
    _, r = _cells(a.content)
    assert (r[0], r[3]) == ("'=A9", "'@m")
    assert [(x.column, x.row) for x in a.warnings] == [("activity_id", 1), ("measurement", 1)]


def test_write_read_repeat_stability():
    c = "activity_id;activity_name;volume;measurement\nA1;'=old;2.5;m3\n"
    f = svc.csv_normalize_inline(c)
    assert f.rows[0].activity_name == "'=old" and f.warnings == []
    o = svc.csv_write_normalized(f.rows)
    assert _cells(o.content)[1][1] == "'=old" and o.warnings == []
    assert svc.csv_write_normalized(f.rows).content == o.content
    g1 = svc.csv_write_normalized([_row(activity_name="=SUM(1)")])
    g2 = svc.csv_write_normalized(svc.csv_normalize_inline(g1.content).rows)
    assert g2.content == g1.content and g2.warnings == []
    assert _cells(g2.content)[1][1] == "'=SUM(1)" and not _cells(g2.content)[1][1].startswith("''")


@pytest.mark.parametrize("bad", ["not-a-row", None, 42, {"volume": 1.0}])
def test_write_bad_rows_internal(bad):
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_write_normalized([bad])
    _is_internal(e)


@pytest.mark.parametrize("v", ["12", None, [1], {"v": 1}, True, float("inf"), float("-inf"), float("nan")])
def test_write_bad_volumes_domain(v):
    with pytest.raises(ValueError, match=E_VOLUME_NON_FINITE) as e:
        svc.csv_write_normalized([_vol_row(v)])
    assert not isinstance(e.value, RuntimeError) and "row 1" in str(e.value)


def test_write_second_row_reports_row2():
    with pytest.raises(ValueError, match=E_VOLUME_NON_FINITE) as e:
        svc.csv_write_normalized([_row(), _vol_row(float("inf"))])
    assert "row 2" in str(e.value)


def test_normalize_domain_passthrough_identity(monkeypatch):
    err = ValueError(f"{E_VOLUME_NON_FINITE}: row 1: non-finite volume")
    monkeypatch.setattr(svc, "parse_csv_content", lambda c, delimiter="auto": (_ for _ in ()).throw(err))
    with pytest.raises(ValueError) as e:
        svc.csv_normalize_inline("x")
    assert e.value is err and not isinstance(e.value, RuntimeError)


def test_normalize_unexpected_to_internal(monkeypatch):
    class Sub(ValueError):
        pass
    monkeypatch.setattr(svc, "parse_csv_content", lambda c, delimiter="auto": (_ for _ in ()).throw(Sub("leak SECRET-1")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_normalize_inline("x")
    _is_internal(e, "SECRET-1")
    monkeypatch.setattr(svc, "parse_csv_content", lambda c, delimiter="auto": (_ for _ in ()).throw(RuntimeError("boom SECRET-2")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e2:
        svc.csv_normalize_inline("x")
    _is_internal(e2, "SECRET-2")


def test_write_unexpected_to_internal(monkeypatch):
    monkeypatch.setattr(svc.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(ValueError("json leak SECRET-3")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_write_normalized([_row(structure=[("S1", "n", 1, 0)])])
    _is_internal(e, "SECRET-3")
    monkeypatch.setattr(svc.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(TypeError("type leak SECRET-4")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_write_normalized([_row()])
    _is_internal(e, "SECRET-4")
    monkeypatch.setattr(svc.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rt leak SECRET-5")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_write_normalized([_row()])
    _is_internal(e, "SECRET-5")

    class PL(ValueError):
        pass
    monkeypatch.undo()
    monkeypatch.setattr(svc, "CsvWarning", lambda *a, **k: (_ for _ in ()).throw(PL("1 validation error SECRET-6")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_write_normalized([_row(activity_name="=evil")])
    _is_internal(e, "SECRET-6")


def test_write_result_build_to_internal(monkeypatch):
    monkeypatch.setattr(svc, "WriteResult", lambda **k: (_ for _ in ()).throw(ValueError("result leak SECRET-7")))
    with pytest.raises(RuntimeError, match=E_INTERNAL) as e:
        svc.csv_write_normalized([_row()])
    _is_internal(e, "SECRET-7")


def test_write_domain_preserved():
    with pytest.raises(ValueError, match=E_DELIMITER_INVALID):
        svc.csv_write_normalized([], delimiter="|")
    try:
        svc.csv_write_normalized([], delimiter="|")
    except RuntimeError:
        pytest.fail("domain E_DELIMITER_INVALID must stay ValueError")
    except ValueError as e:
        assert str(e).startswith(E_DELIMITER_INVALID)


def test_download_domain_errors_pass_through_orchestrator(monkeypatch):
    """Download CsvDomainError (a ValueError subclass) must keep its code."""
    from csv_adapter.config import E_URL_FORBIDDEN
    from csv_adapter.errors import domain_error
    from csv_adapter.models import DownloadRequest

    def boom(request, *, environ=None, http_client=None, s3_client=None):
        raise domain_error(E_URL_FORBIDDEN, "host not allowlisted")

    monkeypatch.setattr(svc, "download_csv", boom)
    with pytest.raises(ValueError) as e:
        svc.csv_download_normalize_upload(DownloadRequest(csv_url="https://x.invalid/a.csv"))
    assert str(e.value).split(":")[0] == E_URL_FORBIDDEN
    assert not isinstance(e.value, RuntimeError)


@pytest.mark.parametrize("bad", [True, False])
def test_row_volume_rejects_bool(bad):
    with pytest.raises(Exception, match="bool"):
        _row(volume=bad)


@pytest.mark.parametrize("bad", [[["c", "n", True, 0]], [["p", "FS", True]]])
def test_row_nested_rejects_bool(bad):
    field = "structure" if len(bad[0]) == 4 else "edges"
    with pytest.raises(Exception, match="bool"):
        _row(**{field: bad})


@pytest.mark.parametrize("c", ["\n=SUM(A1)", "\u00a0=cmd", "\ufeff%pct", "\n+5"])
def test_write_hidden_whitespace_end_to_end(c):
    o = svc.csv_write_normalized([_row(activity_name=c)])
    assert _cells(o.content)[1][1] == "'" + c
    assert len(o.warnings) == 1 and o.warnings[0].code == W_FORMULA_QUOTED


@pytest.mark.parametrize("field,value", [
    ("structure", [(True, "N", 1, 2)]),
    ("structure", [("C", True, 1, 2)]),
    ("structure", True),
    ("edges", [("P1", True, 0)]),
    ("edges", True),
])
def test_row_nested_bool_positions_rejected(field, value):
    with pytest.raises(Exception, match="bool"):
        _row(**{field: value})
