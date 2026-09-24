from types import SimpleNamespace

import polars as pl
import pytest

from core import auth, data_chat_et_chart, product_semantics, semantic_measure_catalog
from core.paths import PATHS
from routers import filebrowser as fb, reformatize


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    ml_path = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "ROOT_LOT_ID": ["LOT01", "LOT01", "LOT01"],
        "WAFER_ID": [1, 1, 2],
        "INLINE_L0": [0.0, 2.0, 2.0],
    }).write_parquet(ml_path)

    user = {"username": "et_chart_qa", "role": "admin"}
    monkeypatch.setattr(auth, "current_user", lambda request: user)
    monkeypatch.setattr(auth, "effective_permissions", lambda current: {"tabs": []})
    monkeypatch.setattr(fb, "current_user", lambda request: user)
    monkeypatch.setattr(fb, "_require_filebrowser_visible_root", lambda root: None)
    monkeypatch.setattr(fb, "_chart_builder_resolve_root_name", lambda root: "ET" if root == "ET" else root)
    monkeypatch.setattr(fb, "source_data_files",
                        lambda root, product: [ml_path] if root == "ML_TABLE" and product == "PRODA" else [])
    monkeypatch.setattr(fb, "_chart_builder_cache_get", lambda *args: None)
    monkeypatch.setattr(fb, "_chart_builder_cache_put", lambda *args: None)
    monkeypatch.setattr(PATHS, "data_root", tmp_path / "state")

    monkeypatch.setattr(reformatize, "products", lambda current: {"products": [{"product": "PRODA"}]})
    monkeypatch.setattr(reformatize, "list_items", lambda product, current: {
        "items": [{"alias": "ABCD"}, {"alias": "EFGH"}],
    })
    calls = []

    def run(req, user):
        calls.append(req)
        if req.agg:
            rows = [
                {"root_lot_id": "LOT01", "wafer_id": "1", "ABCD": 2.0, "EFGH": 10.0},
                {"root_lot_id": "LOT01", "wafer_id": "2", "ABCD": 4.0, "EFGH": 20.0},
                {"root_lot_id": "LOT02", "wafer_id": "1", "ABCD": 9.0, "EFGH": 45.0},
            ]
            columns = ["root_lot_id", "wafer_id", "ABCD", "EFGH"]
        else:
            rows = [
                {"root_lot_id": "LOT01", "wafer_id": "1", "tkout_time": "2026-09-20", "ABCD": 2.0},
                {"root_lot_id": "LOT01", "wafer_id": "2", "tkout_time": "2026-09-21", "ABCD": 4.0},
                {"root_lot_id": "LOT02", "wafer_id": "1", "tkout_time": "2026-09-22", "ABCD": 9.0},
            ]
            columns = ["root_lot_id", "wafer_id", "tkout_time", "ABCD"]
        return {"rows": rows, "columns": columns, "index_columns": ["ABCD"],
                "total_rows": len(rows), "rule_errors": [], "vehicle_csv": "PRODA.csv"}

    monkeypatch.setattr(reformatize, "run", run)
    measure = {"product": "PRODA", "source_type": "INLINE", "term": "L0",
               "step_id": "IN100", "item_id": "L0"}
    monkeypatch.setattr(semantic_measure_catalog, "match_terms", lambda *args, **kwargs: [measure])
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args, **kwargs: [])
    return user, calls


def test_et_trend_uses_reformatter_and_chart_history(scenario):
    _, calls = scenario
    result = data_chat_et_chart.dispatch("PRODA ET ABCD 10일치 보여줘", {}, None)

    assert result["ok"], result
    assert result["tool"]["action"] == "et.index_trend"
    assert [point["y"] for point in result["tool"]["chart_result"]["points"]] == [9.0, 4.0, 2.0]
    assert calls[0].product == "PRODA"
    assert calls[0].items == ["ABCD"]
    assert calls[0].days == 10
    assert calls[0].agg == ""
    assert result["tool"]["saved_chart"]["id"]
    assert "REFORMATTER = true" in result["tool"]["definition_code"]
    assert result["context"]["et_chart_query"]["root_lot_ids"] == ["LOT01", "LOT02"]


def test_corr_requires_explicit_et_aggregation_then_keeps_unmatched_rows(scenario):
    _, calls = scenario
    trend = data_chat_et_chart.dispatch("PRODA ET ABCD 10일치 가져와줘", {}, None)
    asked = data_chat_et_chart.dispatch("ABCD L0과 Corr chart", trend["context"], None)

    assert asked["ok"] is False
    assert asked["tool"]["missing"] == ["et_aggregation"]
    assert len(calls) == 1  # no correlation query runs before HITL choice

    corr = data_chat_et_chart.dispatch("평균 (mean)", asked["context"], None)
    assert corr["ok"], corr
    assert calls[-1].agg == "avg"
    assert corr["tool"]["joins"][0]["how"] == "left"
    assert corr["tool"]["chart_result"]["fit"]["r2"] == 1.0
    assert any("평균해 결합" in warning for warning in corr["tool"]["warnings"])
    assert calls[-1].agg_scope == "wafer"
    assert corr["tool"]["evidence"] == {
        "complete_pair_count": 2,
        "unmatched_et_count": 1,
        "total_et_rows": 3,
        "r2": 1.0,
    }
    assert len(corr["tool"]["chart_result"]["points"]) == 2
    unmatched = [row for row in corr["tool"]["table"]["rows"] if row["pair_status"] == "unmatched"]
    assert [(row["root_lot_id"], row["wafer_id"]) for row in unmatched] == [("LOT02", "1")]
    assert "FIT = linear" in corr["tool"]["definition_code"]
    from core.chart_builder_definition import parse_chart_builder_definition
    assert parse_chart_builder_definition(corr["tool"]["definition_code"])["sources"][0]["reformatter_agg_scope"] == "wafer"
    assert corr["tool"]["saved_chart"]["id"]


def test_corr_inline_query_is_scoped_to_previous_et_roots(scenario, monkeypatch):
    trend = data_chat_et_chart.dispatch("PRODA ET ABCD 10일치 추출해줘", {}, None)
    asked = data_chat_et_chart.dispatch("Inline L0 Corr chart", trend["context"], None)
    captured = []
    original = fb.chart_builder_run

    def inspect(req, request):
        captured.append(req)
        return original(req, request)

    monkeypatch.setattr(fb, "chart_builder_run", inspect)
    result = data_chat_et_chart.dispatch("median", asked["context"], None)

    assert result["ok"], result
    assert captured[0].sources[1].runtime_root_lot_ids == ["LOT01", "LOT02"]
    assert captured[0].sources[1].root == "ML_TABLE"
    assert captured[0].sources[1].sql == "SELECT `ROOT_LOT_ID`, `WAFER_ID`, `INLINE_L0`"
    assert captured[0].joins[0].right_on == "ROOT_LOT_ID,WAFER_ID"


def test_two_et_items_correlate_at_wafer_level(scenario):
    _, calls = scenario
    asked = data_chat_et_chart.dispatch("PRODA ET ABCD와 EFGH 최근 10일 Corr 차트 그려줘", {}, None)
    assert asked["tool"]["missing"] == ["et_aggregation"]
    result = data_chat_et_chart.dispatch("평균 (mean)", asked["context"], None)
    assert result["ok"], result
    assert result["tool"]["action"] == "et.item_correlation"
    assert calls[-1].items == ["ABCD", "EFGH"]
    assert calls[-1].agg_scope == "wafer"
    assert result["tool"]["fit"]["r2"] == 1.0
    assert result["tool"]["saved_chart"]["id"]


def test_both_reformatize_and_chart_permissions_are_required(scenario, monkeypatch):
    user, calls = scenario
    user["role"] = "user"
    monkeypatch.setattr(auth, "effective_permissions", lambda current: {"tabs": ["reformatize"]})

    result = data_chat_et_chart.dispatch("PRODA ET ABCD 10일치 보여줘", {}, None)

    assert result["ok"] is False
    assert result["tool"]["blocked"] is True
    assert result["tool"]["missing_permissions"] == ["chartbuilder"]
    assert calls == []


def test_wafer_scope_aggregates_raw_across_packages():
    frame = pl.DataFrame({"root_lot_id": ["a", "A", "A"], "wafer_id": ["01", "1", "WF01"],
                          "step_id": ["S1", "S2", "S2"], "pgm": ["P1", "P2", "P2"], "V": [1., 4., 7.]})
    result = reformatize._aggregate(frame, ["V"], "avg", "wafer")
    assert result.height == 1
    assert result["V"][0] == 4.
