"""FileBrowser sort: view sort with CAST, CSV save-sort options, LLM/keyword sort drafts."""
import polars as pl
import pytest
from fastapi import HTTPException

from routers import filebrowser as fb


# ── 조회(검색) 정렬: CAST 후 정렬 ──────────────────────────────────────────

def test_order_by_cast_parses_and_round_trips():
    cols = ["lot_id", "value", "tkout_time"]
    where, selected, sort = fb._parse_ai_sql_display_sql(
        "SELECT lot_id, value WHERE value IS NOT NULL ORDER BY CAST(value AS DOUBLE) DESC NULLS FIRST", cols)
    assert where == "value IS NOT NULL"
    assert selected == ["lot_id", "value"]
    assert sort == {"column": "value", "direction": "desc", "nulls": "first", "cast": "DOUBLE"}
    rebuilt = fb._build_ai_sql_display_sql(selected, where, sort)
    assert rebuilt.endswith("ORDER BY CAST(value AS DOUBLE) DESC NULLS FIRST")
    # 캐스트 없는 기존 계약은 그대로 (cast 키 없음)
    _, _, plain = fb._parse_ai_sql_display_sql("ORDER BY value ASC", cols)
    assert plain == {"column": "value", "direction": "asc", "nulls": "last"}
    _, _, try_cast = fb._parse_ai_sql_display_sql("ORDER BY TRY_CAST(tkout_time AS datetime) DESC", cols)
    assert try_cast["cast"] == "TIMESTAMP"


def test_order_by_rejects_unknown_cast_type():
    with pytest.raises(ValueError):
        fb._parse_ai_sql_display_sql("ORDER BY CAST(value AS BLOB) DESC", ["value"])


def test_run_view_sorts_string_numbers_by_value_after_cast():
    df = pl.DataFrame({"lot_id": ["a", "b", "c", "d"], "value": ["9", "10", "", "2.5"]})
    raw = fb._run_view(df, "ORDER BY value DESC", "", rows=10)
    assert [r["value"] for r in raw["data"]] == ["9", "2.5", "10", ""]  # 문자열 비교
    out = fb._run_view(df, "ORDER BY CAST(value AS DOUBLE) DESC", "", rows=10)
    assert [r["value"] for r in out["data"]] == ["10", "9", "2.5", ""]  # 변환 실패/빈 값은 뒤
    assert out["sort"] == {"column": "value", "direction": "desc", "nulls": "last", "cast": "DOUBLE"}
    assert "CAST(value AS DOUBLE)" in out["display_sql"]
    # 표시 값은 원본 문자열 그대로
    assert out["dtypes"]["value"] in {"String", "Utf8"}


def test_run_view_sort_cast_from_query_params_and_timestamp():
    df = pl.DataFrame({"t": ["2026-09-02 01:00:00", "2026-09-10 00:00:00", "bad", "2026-08-30 23:59:59"]})
    spec = fb._view_sort_query("t", "desc", "last", "timestamp")
    out = fb._run_view(df, "", "", rows=10, sort_spec=spec)
    assert [r["t"] for r in out["data"]] == ["2026-09-10 00:00:00", "2026-09-02 01:00:00", "2026-08-30 23:59:59", "bad"]


def test_duckdb_order_by_cast(tmp_path):
    from core import duckdb_engine
    path = tmp_path / "x.parquet"
    pl.DataFrame({"value": ["9", "10", "2.5", None]}).write_parquet(path)
    kwargs = fb._duckdb_sort_kwargs({"column": "value", "direction": "desc", "nulls": "last", "cast": "DOUBLE"})
    df, _, _ = duckdb_engine.query_files([path], limit=10, **kwargs)
    assert df["value"].to_list() == ["10", "9", "2.5", None]
    kwargs = fb._duckdb_sort_kwargs({"column": "value", "direction": "asc", "nulls": "first"})
    df, _, _ = duckdb_engine.query_files([path], limit=10, **kwargs)
    assert df["value"].to_list()[0] is None


def test_ai_sql_sort_normalizer_keeps_cast():
    spec = fb._normalize_ai_sql_sort({"column": "VALUE", "direction": "desc", "cast": "numeric"}, ["value"], [])
    assert spec == {"column": "value", "direction": "desc", "nulls": "last", "cast": "DOUBLE"}
    warnings: list[str] = []
    spec = fb._normalize_ai_sql_sort({"column": "value", "cast": "blob"}, ["value"], warnings)
    assert "cast" not in spec and warnings


# ── 파일설정 저장 정렬: 세부 옵션 ─────────────────────────────────────────

def _sorted(rows, sort):
    header = ["k", "v"]
    rule = fb._normalize_csv_rule({"sort": sort})
    return [r[0] for r in fb._apply_csv_sort_rule(header, [[k, ""] for k in rows], rule)]


def test_save_sort_natural_custom_case_and_pattern():
    assert _sorted(["ST10", "ST9", "st1"], "k asc natural") == ["st1", "ST9", "ST10"]
    assert _sorted(["DONE", "RUN", "X", "hold"], "k asc custom last values=RUN|HOLD|DONE") == ["RUN", "hold", "DONE", "X"]
    assert _sorted(["b", "B", "a"], "k asc string last case=i") == ["a", "b", "B"]
    assert _sorted(["L_0012", "L_0100", "L_0003"], r"k desc numeric last pattern=_(\d+)$") == ["L_0100", "L_0012", "L_0003"]
    # values 만 주면 custom 으로 승격
    rule = fb._normalize_csv_rule({"sort": [{"column": "k", "values": ["B", "A"]}]})
    assert rule["sort"][0]["type"] == "custom"


def test_save_sort_desc_keeps_nulls_last():
    assert _sorted(["1", "", "3"], "k desc numeric last") == ["3", "1", ""]
    assert _sorted(["1", "", "3"], "k desc numeric first") == ["", "3", "1"]


def test_order_spec_validation_errors():
    with pytest.raises(HTTPException):
        fb._normalize_csv_rule({"sort": [{"column": "k", "type": "custom"}]})
    with pytest.raises(HTTPException):
        fb._normalize_csv_rule({"sort": [{"column": "k", "pattern": "("}]})


# ── LLM 초안: 우선순위·범위·수정 의미 ─────────────────────────────────────

class _Req:
    def __init__(self, prompt, current_rule=None, scope="all", columns=None, sample_rows=None):
        self.file = "demo.csv"
        self.prompt = prompt
        self.columns = columns or ["product", "feature_name", "rule_order", "value", "step_id"]
        self.sample_rows = sample_rows or [
            {"product": "P1", "feature_name": "3.0 VTN", "rule_order": "R1", "value": "10", "step_id": "ST9"},
            {"product": "P2", "feature_name": "1.0 VTP", "rule_order": "R2", "value": "9", "step_id": "ST10"},
        ]
        self.current_rule = current_rule or {}
        self.scope = scope


@pytest.fixture
def no_manager_check(monkeypatch):
    monkeypatch.setattr(fb, "_require_filebrowser_manager", lambda request: {"username": "t"})


def _patch_llm(monkeypatch, obj):
    from core import llm_adapter
    monkeypatch.setattr(llm_adapter, "is_available", lambda: obj is not None)
    monkeypatch.setattr(llm_adapter, "complete_json", lambda *a, **k: {"ok": True, "obj": obj})


def test_llm_sort_draft_wins_over_keyword_heuristic(monkeypatch, no_manager_check):
    llm_sort = [
        {"column": "product", "direction": "asc", "type": "string", "nulls": "last"},
        {"column": "rule_order", "direction": "desc", "type": "rule_order", "nulls": "last"},
    ]
    _patch_llm(monkeypatch, {"csv_rules": {"demo.csv": {"sort": llm_sort}}})
    out = fb.filebrowser_settings_llm_draft(_Req("product 오름차순 정렬, rule_order 는 내림차순"), None)
    assert out["llm"]["source"] == "llm"
    assert out["draft"]["sort"] == llm_sort


def test_sort_scope_drops_validation_keys_and_supports_clear(monkeypatch, no_manager_check):
    _patch_llm(monkeypatch, {"csv_rules": {"demo.csv": {
        "required_columns": ["product"],
        "sort": [{"column": "step_id", "direction": "asc", "type": "natural", "nulls": "last"}],
    }}})
    out = fb.filebrowser_settings_llm_draft(_Req("step_id 자연 정렬", scope="sort"), None)
    assert set(out["draft"]) == {"sort"}
    _patch_llm(monkeypatch, {"csv_rules": {"demo.csv": {"sort": []}}})
    current = {"sort": [{"column": "product", "direction": "asc", "type": "string", "nulls": "last"}]}
    out = fb.filebrowser_settings_llm_draft(_Req("정렬 없애줘", current_rule=current, scope="sort"), None)
    assert out["sort_cleared"] is True and not out["draft"].get("sort")


def test_keyword_fallback_per_column_direction_and_prompt_order(monkeypatch, no_manager_check):
    _patch_llm(monkeypatch, None)
    out = fb.filebrowser_settings_llm_draft(_Req("rule_order 내림차순, 그다음 product 오름차순으로 저장 정렬"), None)
    sort = out["draft"]["sort"]
    assert [(s["column"], s["direction"], s["type"]) for s in sort] == [
        ("rule_order", "desc", "rule_order"), ("product", "asc", "string")]
    assert set(out["draft"]) == {"sort"}  # 정렬 요청이 검증로직을 만들지 않는다
    out = fb.filebrowser_settings_llm_draft(_Req("value 숫자 기준 내림차순 정렬"), None)
    assert out["draft"] == {"sort": [{"column": "value", "direction": "desc", "type": "numeric", "nulls": "last"}]}


def test_keyword_fallback_edits_existing_sort(monkeypatch, no_manager_check):
    _patch_llm(monkeypatch, None)
    current = {"sort": [
        {"column": "feature_name", "direction": "asc", "type": "leading_number", "nulls": "last"},
        {"column": "rule_order", "direction": "asc", "type": "rule_order", "nulls": "last"},
    ]}
    out = fb.filebrowser_settings_llm_draft(_Req("rule_order 만 내림차순으로 바꿔줘 정렬", current_rule=current, scope="sort"), None)
    assert [(s["column"], s["direction"], s["type"]) for s in out["draft"]["sort"]] == [
        ("feature_name", "asc", "leading_number"), ("rule_order", "desc", "rule_order")]
    out = fb.filebrowser_settings_llm_draft(_Req("step_id 자연 정렬 기준 추가", current_rule=current, scope="sort"), None)
    assert [s["column"] for s in out["draft"]["sort"]] == ["feature_name", "rule_order", "step_id"]
    assert out["draft"]["sort"][-1]["type"] == "natural"
    out = fb.filebrowser_settings_llm_draft(_Req("정렬에서 rule_order 빼줘", current_rule=current, scope="sort"), None)
    assert [s["column"] for s in out["draft"]["sort"]] == ["feature_name"]


# ── 저장 속도: 벡터화 diff 가 기존 행 루프와 같은 결과 ─────────────────────

@pytest.mark.parametrize("keyed,n", [(True, 400), (False, 6200)])
def test_vector_diff_matches_row_loop(tmp_path, keyed, n):
    import random
    rnd = random.Random(7)
    head = ["id", "a", "b"] if keyed else ["a", "b", "c"]
    prev = [[f"k{i}" if keyed else f"x{i % 50}", str(rnd.randint(0, 9)), ""] for i in range(n)]
    cur = [list(r) for r in prev]
    for i in rnd.sample(range(n), 40):
        cur[i][1] = "changed"
    del cur[5:9]
    cur.insert(3, ["knew" if keyed else "new", "1", "2"])
    cur.append(["kz" if keyed else "zz", "", "3"])
    p_prev, p_cur = tmp_path / "prev.csv", tmp_path / "cur.csv"
    for path, rows in ((p_prev, prev), (p_cur, cur)):
        path.write_text("\n".join(",".join(r) for r in [head, *rows]) + "\n", encoding="utf-8")
    fast = fb._diff_table_between_fast(p_cur, p_prev, max_changes=30)
    slow = fb._diff_table_between_rows(p_cur, p_prev, max_changes=30)
    assert fast is not None
    assert fast["match_strategy"] == slow["match_strategy"] == ("unique_key" if keyed else "row_index")
    assert fast["counts"] == slow["counts"]
    assert fast["rows"] == slow["rows"]
    assert fast["truncated"] == slow["truncated"]
