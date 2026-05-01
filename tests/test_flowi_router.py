from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import llm as llm_router  # noqa: E402
from routers.llm import (  # noqa: E402
    _feedback_summary_from_records,
    _feedback_to_golden_case,
    _handle_flowi_query,
    _matched_feature_entrypoints,
    _normalize_feedback_tags,
    _run_flowi_chat,
)


def test_flowi_feature_router_matches_korean_splittable_alias():
    matches = _matched_feature_entrypoints("A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘")

    assert matches
    assert matches[0]["key"] == "splittable"


def test_flowi_general_query_returns_deterministic_unit_action():
    out = _handle_flowi_query("A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘", "", 12)

    assert out["handled"] is True
    assert out["intent"] == "splittable_guidance"
    assert out["action"] == "open_splittable"
    assert out["table"]["kind"] == "flowi_action_plan"
    assert out["slots"]["lots"] == ["A10001"]


def test_flowi_feature_router_prefers_tablemap_relation_terms():
    out = _handle_flowi_query("테이블맵 relation에서 inline item과 knob 연결 보여줘", "", 12)

    assert out["intent"] == "tablemap_guidance"
    assert out["action"] == "open_tablemap"


def test_flowi_feature_router_filters_by_allowed_tabs():
    matches = _matched_feature_entrypoints(
        "테이블맵 relation에서 inline item과 knob 연결 보여줘",
        allowed_keys={"splittable"},
    )

    assert not matches or matches[0]["key"] != "tablemap"


def test_flowi_chart_request_returns_clarifying_unit_plan(monkeypatch):
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})

    out = _handle_flowi_query(
        "Inline 1.0 CD와 ET LKG Corr. scatter 그리고 1차식 fitting line 그려줘",
        "PRODA",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "dashboard_scatter_plan"
    assert out["action"] == "build_metric_scatter"
    assert out["chart"]["aggregations"]["INLINE"] == "avg"
    assert out["chart"]["aggregations"]["ET"] == "median"
    assert "linear_fit" in out["chart"]["operations"]
    assert out["clarification"]["choices"]
    assert out["table"]["kind"] == "flowi_chart_plan"


def test_flowi_chart_request_uses_admin_defaults(monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "_admin_settings",
        lambda: {
            "flowi_defaults": {
                "chart_defaults": {
                    "scatter": {"max_points": 123, "inline_agg": "median", "et_agg": "avg"}
                }
            }
        },
    )

    out = _handle_flowi_query(
        "Inline CD와 ET LKG Corr. scatter 그려줘",
        "PRODA",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["chart"]["render_preset"]["max_points"] == 123
    assert out["chart"]["aggregations"] == {"INLINE": "median", "ET": "avg"}


def test_flowi_wafer_match_expr_handles_prefixed_and_int_values():
    int_df = pl.DataFrame({"wafer_id": [1, 2, 3]})
    str_df = pl.DataFrame({"wafer_id": ["W01", "02", "WF03"]})

    int_rows = int_df.lazy().filter(llm_router._wafer_match_expr("wafer_id", ["W01"])).collect()
    str_rows = str_df.lazy().filter(llm_router._wafer_match_expr("wafer_id", ["3"])).collect()

    assert int_rows["wafer_id"].to_list() == [1]
    assert str_rows["wafer_id"].to_list() == ["WF03"]


def test_flowi_wafer_tokens_expand_ranges_and_ignore_out_of_range():
    assert llm_router._wafer_tokens("#1~3은 ABC, #1000은 제외") == ["1", "2", "3"]


def test_flowi_splittable_plan_confirms_and_saves_wafer_range(tmp_path, monkeypatch):
    from routers import splittable as splittable_router

    ml_fp = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "product": ["PRODA"] * 26,
        "root_lot_id": ["A1000"] * 26,
        "wafer_id": list(range(1, 26)) + [1000],
        "KNOB_A": ["OLD"] * 26,
        "KNOB_B": ["B"] * 26,
    }).write_parquet(ml_fp)
    plan_dir = tmp_path / "flow-data" / "splittable"
    plan_dir.mkdir(parents=True)
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product="": [ml_fp])
    monkeypatch.setattr(splittable_router, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)

    draft = llm_router._handle_splittable_plan_request(
        "PRODA A1000 A KNOB #1~10은 ABC로 plan해주고 나머지는 DGD로 plan 넣어줘",
        me={"username": "planner", "role": "user"},
        allowed_keys={"splittable"},
    )

    assert draft["intent"] == "splittable_plan_confirm"
    assert draft["slots"]["wafers"][0] == "1"
    assert draft["slots"]["wafers"][-1] == "25"
    confirm_prompt = draft["clarification"]["choices"][0]["prompt"]

    saved = llm_router._handle_splittable_plan_request(
        confirm_prompt,
        me={"username": "planner", "role": "user"},
        allowed_keys={"splittable"},
    )
    data = splittable_router.load_json(plan_dir / "ML_TABLE_PRODA.json", {})
    plans = data["plans"]

    assert saved["intent"] == "splittable_plan_saved"
    assert plans["A1000|1|KNOB_A"]["value"] == "ABC"
    assert plans["A1000|10|KNOB_A"]["value"] == "ABC"
    assert plans["A1000|11|KNOB_A"]["value"] == "DGD"
    assert plans["A1000|25|KNOB_A"]["value"] == "DGD"
    assert "A1000|1000|KNOB_A" not in plans


def test_flowi_chart_request_computes_inline_et_scatter(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 12.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "CD_MEAN", "value": 20.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 101.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 99.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "LKG_RAW", "value": 200.0},
    ]).write_parquet(et_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])

    out = _handle_flowi_query(
        "PRODX A10001 Inline CD와 ET LKG Corr scatter 그리고 1차식 fitting line 그려줘",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["chart"]["status"] == "computed"
    assert out["chart_result"]["kind"] == "dashboard_scatter"
    assert out["chart_result"]["total"] == 2
    assert out["chart_result"]["join_cols"] == ["lot_wf"]
    assert out["chart_result"]["aggregations"] == {"INLINE": "avg", "ET": "median"}
    assert out["chart_result"]["fit"]["r2"] == 1.0
    assert {p["x"] for p in out["chart_result"]["points"]} == {11.0, 20.0}
    assert {p["y"] for p in out["chart_result"]["points"]} == {100.0, 200.0}


def test_flowi_inline_shot_grain_uses_subitem_id_not_coordinates(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "1000", "item_id": "CD_MEAN", "subitem_id": "SHOT01", "shot_x": 99, "shot_y": 99, "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "1000", "item_id": "CD_MEAN", "subitem_id": "SHOT02", "shot_x": 99, "shot_y": 99, "value": 12.0},
    ]).write_parquet(inline_fp)
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])

    out = llm_router._flowi_metric_lf(
        "INLINE",
        "PRODX",
        ["A10001"],
        "CD",
        "inline_value",
        include_shot=True,
        agg_name="avg",
    )

    assert out["ok"] is True
    assert out["group_cols"] == ["root_lot_id", "wafer_id", "lot_wf", "shot_id"]
    rows = out["lf"].sort("shot_id").collect().to_dicts()
    assert [row["shot_id"] for row in rows] == ["SHOT01", "SHOT02"]
    assert all("shot_x" not in row and "shot_y" not in row for row in rows)
    assert {row["wafer_id"] for row in rows} == {"25"}


def test_flowi_chart_request_colors_and_filters_by_ml_table_knob(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 12.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "CD_MEAN", "value": 20.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 101.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 99.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "LKG_RAW", "value": 200.0},
    ]).write_parquet(et_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "KNOB_SPLIT": "A"},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "KNOB_SPLIT": "B"},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    out = _handle_flowi_query(
        "PRODX A10001 Inline CD와 ET LKG Corr scatter KNOB_SPLIT B 제외하고 컬러링",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["chart"]["status"] == "computed"
    assert out["chart_result"]["total"] == 1
    assert out["chart_result"]["color_by"] == "SPLIT"
    assert out["chart_result"]["filters"]["excluded_values"] == ["B"]
    assert out["chart_result"]["points"][0]["color_value"] == "A"
    assert out["chart_result"]["color_values"] == [{"value": "A", "count": 1}]


def test_flowi_chart_request_normalizes_wafer_keys_across_sources(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": 1, "item_id": "LKG_RAW", "value": 100.0},
    ]).write_parquet(et_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "W01", "KNOB_SPLIT": "A"},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    out = _handle_flowi_query(
        "PRODX A10001 Inline CD와 ET LKG Corr scatter KNOB_SPLIT 컬러링",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["chart"]["status"] == "computed"
    assert out["chart_result"]["total"] == 1
    assert out["chart_result"]["points"][0]["label"] == "A10001_1"
    assert out["chart_result"]["points"][0]["color_value"] == "A"


def test_flowi_chart_request_computes_product_level_cross_db_scatter(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "CD_MEAN", "value": 20.0},
        {"product": "PRODX", "root_lot_id": "A10002", "wafer_id": "01", "item_id": "CD_MEAN", "value": 30.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 100.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "LKG_RAW", "value": 200.0},
        {"product": "PRODX", "root_lot_id": "A10002", "wafer_id": "01", "item_id": "LKG_RAW", "value": 300.0},
    ]).write_parquet(et_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])

    out = _handle_flowi_query(
        "PRODX Inline CD와 ET LKG Corr scatter 그려줘",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["chart"]["status"] == "computed"
    assert out["chart_result"]["kind"] == "dashboard_scatter"
    assert out["chart_result"]["total"] == 3
    assert out["chart_result"]["join_cols"] == ["lot_wf"]
    assert out["slots"]["lots"] == []


def test_flowi_box_chart_returns_visible_chart_result(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_GATE", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "CD_GATE", "value": 12.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "03", "item_id": "CD_GATE", "value": 14.0},
        {"product": "PRODX", "root_lot_id": "A10002", "wafer_id": "01", "item_id": "CD_GATE", "value": 20.0},
        {"product": "PRODX", "root_lot_id": "A10002", "wafer_id": "02", "item_id": "CD_GATE", "value": 22.0},
        {"product": "PRODX", "root_lot_id": "A10002", "wafer_id": "03", "item_id": "CD_GATE", "value": 24.0},
    ]).write_parquet(inline_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])

    out = _handle_flowi_query(
        "PRODX CD_GATE box plot 그려줘",
        "",
        12,
        allowed_keys={"dashboard"},
    )

    assert out["handled"] is True
    assert out["intent"] == "dashboard_box_chart"
    assert out["chart_result"]["kind"] == "dashboard_box"
    assert len(out["chart_result"]["boxes"]) == 2
    assert out["table"]["kind"] == "dashboard_box"


def test_flowi_wafer_map_chart_returns_visible_points(tmp_path, monkeypatch):
    et_fp = tmp_path / "et.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "shot_x": 0, "shot_y": 0, "item_id": "VTH", "value": 1.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "shot_x": 0, "shot_y": 1, "item_id": "VTH", "value": 2.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "shot_x": 1, "shot_y": 0, "item_id": "VTH", "value": 3.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "shot_x": 1, "shot_y": 1, "item_id": "VTH", "value": 4.0},
    ]).write_parquet(et_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])

    out = _handle_flowi_query(
        "PRODX ET VTH WF map 그려줘",
        "",
        12,
        allowed_keys={"dashboard", "waferlayout"},
    )

    assert out["handled"] is True
    assert out["intent"] == "dashboard_wafer_map_chart"
    assert out["chart_result"]["kind"] == "dashboard_wafer_map"
    assert out["chart_result"]["source"] == "ET"
    assert out["chart_result"]["total"] == 4


def test_flowi_inline_wafer_map_requires_explicit_coordinate_mapping(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "VTH", "subitem_id": "SHOT01", "value": 1.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "VTH", "subitem_id": "SHOT02", "value": 2.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "shot_x": 0, "shot_y": 0, "item_id": "VTH", "value": 10.0},
    ]).write_parquet(et_fp)
    monkeypatch.setattr(llm_router, "_admin_settings", lambda: {})
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])

    out = _handle_flowi_query(
        "PRODX INLINE VTH WF map 그려줘",
        "",
        12,
        allowed_keys={"dashboard", "waferlayout"},
    )

    assert out["handled"] is True
    assert out["intent"] == "dashboard_wafer_map_needs_inline_mapping"
    assert "subitem_id" in out["answer"]
    assert out["missing"] == ["inline_item_map", "inline_subitem_pos"]
    assert out["table"]["rows"] == [{"item_id": "VTH"}]


def test_flowi_value_lookup_returns_mltable_preview(tmp_path, monkeypatch):
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "R1000", "wafer_id": "01", "KNOB_ALPHA": "ON", "INLINE_CD": 12.3},
        {"product": "PRODX", "root_lot_id": "R1000", "wafer_id": "02", "KNOB_ALPHA": "OFF", "INLINE_CD": 12.7},
        {"product": "PRODX", "root_lot_id": "R2000", "wafer_id": "01", "KNOB_ALPHA": "ON", "INLINE_CD": 10.1},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    out = _handle_flowi_query(
        "PRODX R1000 KNOB_ALPHA 값 얼마야",
        "",
        12,
        allowed_keys={"filebrowser", "splittable"},
    )

    assert out["handled"] is True
    assert out["intent"] == "db_table_lookup"
    assert out["table"]["kind"] == "flowi_db_table"
    assert out["table"]["total"] == 2
    assert {row["KNOB_ALPHA"] for row in out["table"]["rows"]} == {"ON", "OFF"}


def test_flowi_knob_fastest_lot_joins_latest_fab_step(tmp_path, monkeypatch):
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "R1000", "wafer_id": "01", "KNOB_SPEED": "FAST"},
        {"product": "PRODX", "root_lot_id": "R2000", "wafer_id": "01", "KNOB_SPEED": "FAST"},
    ]).write_parquet(ml_fp)
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "R1000", "lot_id": "R1000A.1", "fab_lot_id": "R1000A.1", "wafer_id": "01", "step_id": "STEP_010", "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "R2000", "lot_id": "R2000A.1", "fab_lot_id": "R2000A.1", "wafer_id": "01", "step_id": "STEP_020", "tkout_time": "2026-04-29T09:00:00"},
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda product="", step_id="": {"func_step": {"STEP_010": "STI", "STEP_020": "GATE"}.get(step_id, "")})

    out = _handle_flowi_query(
        "PRODX KNOB_SPEED FAST 가진 것 중에 가장 빠른게 지금 어디있어",
        "",
        12,
        allowed_keys={"splittable", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "knob_fastest_lot"
    assert out["table"]["rows"][0]["root_lot_id"] == "R2000"
    assert out["table"]["rows"][0]["current_step_id"] == "STEP_020"
    assert out["table"]["rows"][0]["func_step"] == "GATE"


def test_flowi_fab_eqp_lookup_maps_function_step(tmp_path, monkeypatch):
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {
            "product": "PRODX",
            "root_lot_id": "A0001",
            "lot_id": "A0001.1",
            "fab_lot_id": "A0001.1",
            "wafer_id": "03",
            "step_id": "AA230400",
            "eqp_id": "EQP_STI_01",
            "chamber_id": "CH_A",
            "ppid": "PPID_STI",
            "tkout_time": "2026-04-29T08:00:00",
        },
        {
            "product": "PRODX",
            "root_lot_id": "A0001",
            "lot_id": "A0001.1",
            "fab_lot_id": "A0001.1",
            "wafer_id": "03",
            "step_id": "AA240000",
            "eqp_id": "EQP_GATE_01",
            "chamber_id": "CH_B",
            "ppid": "PPID_GATE",
            "tkout_time": "2026-04-29T10:00:00",
        },
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda product="", step_id="": {"func_step": {"AA230400": "STI", "AA240000": "GATE"}.get(step_id, "")})

    out = _handle_flowi_query(
        "A0001 #3 STI step eqp이 뭐야",
        "",
        12,
        allowed_keys={"filebrowser", "dashboard"},
    )

    assert out["handled"] is True
    assert out["intent"] == "fab_eqp_lookup"
    assert out["table"]["kind"] == "fab_eqp_lookup"
    assert out["table"]["rows"][0]["step_id"] == "AA230400"
    assert out["table"]["rows"][0]["function_step"] == "STI"
    assert out["table"]["rows"][0]["eqp_id"] == "EQP_STI_01"


def test_flowi_current_fab_lot_lookup_uses_fab_db_not_et_report(tmp_path, monkeypatch):
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODA" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {
            "product": "PRODA",
            "root_lot_id": "A1000",
            "lot_id": "A1000A.1",
            "fab_lot_id": "A1000A.1",
            "wafer_id": "06",
            "step_id": "AA230400",
            "process_id": "PROC_A",
            "tkout_time": "2026-04-29T08:00:00",
        },
        {
            "product": "PRODA",
            "root_lot_id": "A1000",
            "lot_id": "A1000A.2",
            "fab_lot_id": "A1000A.2",
            "wafer_id": "06",
            "step_id": "AA240000",
            "process_id": "PROC_A",
            "tkout_time": "2026-04-29T10:00:00",
        },
        {
            "product": "PRODA",
            "root_lot_id": "A1000",
            "lot_id": "A1000A.7",
            "fab_lot_id": "A1000A.7",
            "wafer_id": "07",
            "step_id": "AA250000",
            "process_id": "PROC_A",
            "tkout_time": "2026-04-29T11:00:00",
        },
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    out = _handle_flowi_query(
        "PRODA A1000 #6 현재 fab lot id가 뭐야?",
        "",
        12,
        allowed_keys={"filebrowser", "dashboard", "ettime"},
    )

    assert out["handled"] is True
    assert out["intent"] == "current_fab_lot_lookup"
    assert out["action"] == "query_current_fab_lot_from_fab_db"
    assert out["table"]["kind"] == "current_fab_lot_lookup"
    assert out["table"]["rows"][0]["fab_lot_id"] == "A1000A.2"
    assert out["table"]["rows"][0]["wafer_id"] == "6"
    assert "ET Report" not in out["answer"]


def test_flowi_parses_yaml_product_fab_lot_and_wafer_aliases(monkeypatch):
    monkeypatch.setattr(llm_router.product_config, "load_all", lambda _root: {"GAA2N": {"product": "GAA2N"}})
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda _kind: [])

    prompt = "gaa2n A1000 #6 현재 fab lot id가 뭐야? 이전 fab은 AZGASB.1 ASDAGFH.NJ"

    assert llm_router._product_hint(prompt) == "GAA2N"
    assert llm_router._lot_tokens(prompt) == ["A1000", "AZGASB.1", "ASDAGFH.NJ"]
    assert llm_router._wafer_tokens("A1000 slot 6 / 6번 slot / 6번장 / 6장") == ["6"]


def test_flowi_function_call_preview_structures_fab_lot_lookup(monkeypatch):
    monkeypatch.setattr(llm_router.product_config, "load_all", lambda _root: {"PRODA": {"product": "PRODA"}})
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda _kind: [])

    out = llm_router._structure_flowi_function_call("PRODA A1000 #6 현재 fab lot id가 뭐야?")

    assert out["persona"]["role"] == "semiconductor_process_data_analyst"
    assert out["selected_function"]["name"] == "query_current_fab_lot_from_fab_db"
    assert out["function_call"]["function"]["arguments"]["product"] == "PRODA"
    assert out["function_call"]["function"]["arguments"]["root_lot_ids"] == ["A1000"]
    assert out["function_call"]["function"]["arguments"]["wafer_ids"] == [6]
    assert out["function_call"]["function"]["arguments"]["source_types"][0] == "FAB"
    assert out["validation"]["valid"] is True


def test_flowi_function_call_preview_keeps_wafer_slots_1_to_25(monkeypatch):
    monkeypatch.setattr(llm_router.product_config, "load_all", lambda _root: {"PRODA": {"product": "PRODA"}})
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda _kind: [])

    out = llm_router._structure_flowi_function_call(
        "PRODA A1000 A KNOB #1~10은 ABC로 plan해주고 #1000은 DGD로 plan 넣어줘"
    )
    args = out["function_call"]["function"]["arguments"]

    assert out["selected_function"]["name"] == "preview_splittable_plan_update"
    assert args["root_lot_ids"] == ["A1000"]
    assert args["plan_assignments"][0]["wafers"] == [str(i) for i in range(1, 11)]
    assert "1000" not in {wf for item in args["plan_assignments"] for wf in item["wafers"]}
    assert any("1~25" in w for w in out["validation"]["warnings"])


def test_flowi_function_catalog_routes_required_acceptance_patterns(monkeypatch):
    monkeypatch.setattr(llm_router.product_config, "load_all", lambda _root: {"PRODA": {"product": "PRODA"}})
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda _kind: [])

    q1 = llm_router._structure_flowi_function_call("PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?")
    a1 = q1["function_call"]["function"]["arguments"]
    assert q1["selected_function"]["name"] == "query_lot_knobs_from_ml_table"
    assert a1["product"] == "PRODA"
    assert a1["root_lot_ids"] == ["A1002"]
    assert a1["step"] == "24.0 SORT"
    assert a1["group"] == "KNOB"

    q2 = llm_router._structure_flowi_function_call("PRODA A1002 #1 24.0 SORT Split이 뭐야? 뭘로 진행했어?")
    a2 = q2["function_call"]["function"]["arguments"]
    assert q2["selected_function"]["name"] == "query_wafer_split_at_step"
    assert a2["root_lot_ids"] == ["A1002"]
    assert a2["wafer_ids"] == [1]
    assert a2["step"] == "24.0 SORT"

    q3 = llm_router._structure_flowi_function_call("24.0 SORT PPID_24_3인 자재 가장 빠른게 어디에 있어?")
    a3 = q3["function_call"]["function"]["arguments"]
    assert q3["selected_function"]["name"] == "find_lots_by_knob_value"
    assert a3["step"] == "24.0 SORT"
    assert a3["knob_value"] == "PPID_24_3"
    assert a3["sort"] == "earliest_progress"

    q4 = llm_router._structure_flowi_function_call("A1002A.1 어디에 있어?")
    assert q4["selected_function"]["name"] == "query_fab_progress"
    assert q4["function_call"]["function"]["arguments"]["fab_lot_ids"] == ["A1002A.1"]
    assert "product" not in q4["validation"]["missing"]

    q5 = llm_router._structure_flowi_function_call("A1000 #20 16.0 VIA2 Avg 몇이야?")
    a5 = q5["function_call"]["function"]["arguments"]
    assert q5["selected_function"]["name"] == "query_metric_at_step"
    assert a5["root_lot_ids"] == ["A1000"]
    assert a5["wafer_ids"] == [20]
    assert a5["step"] == "16.0 VIA2"
    assert a5["metric"] == "VIA2 Avg"
    assert a5["agg"] == "avg"

    q6 = llm_router._structure_flowi_function_call("PRODA A1000A.3 GATE 모듈 인폼해줘 test1 스플릿으로 선택해줘 내용은 GATE 모듈인폼입니다.")
    a6 = q6["function_call"]["function"]["arguments"]
    assert q6["selected_function"]["name"] == "register_inform_log"
    assert a6["fab_lot_ids"] == ["A1000A.3"]
    assert a6["module"] == "GATE"
    assert a6["split_set"] == "test1"
    assert a6["note"] == "GATE 모듈인폼입니다."
    assert q6["selected_function"]["side_effect"] == "confirm_before_write"


def test_flowi_inform_batch_and_edge_choices(monkeypatch):
    monkeypatch.setattr(llm_router.product_config, "load_all", lambda _root: {"PRODA": {"product": "PRODA"}, "PRODB": {"product": "PRODB"}})
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda _kind: [])
    monkeypatch.setattr(llm_router, "_flowi_inform_modules", lambda: ["GATE", "STI", "PC"])

    batch = llm_router._structure_flowi_function_call("A1003 GATE는 test1 STI는 test2 이런식으로 A1003에 대해서 인폼로그 다 만들어줘")
    args = batch["function_call"]["function"]["arguments"]
    assert batch["selected_function"]["name"] == "register_inform_log"
    assert args["root_lot_ids"] == ["A1003"]
    assert args["mode"] == "batch"
    assert args["entries"] == [{"module": "GATE", "split_set": "test1"}, {"module": "STI", "split_set": "test2"}]

    mail = llm_router._structure_flowi_function_call("메일 보내")
    assert mail["selected_function"]["name"] == "compose_inform_module_mail"
    assert mail["validation"]["missing"] == ["module"]
    fields = mail["arguments_choices"]["fields"]
    assert fields and fields[0]["field"] == "module"
    assert [c["value"] for c in fields[0]["choices"][:3]] == ["GATE", "STI", "PC"]

    split = llm_router._structure_flowi_function_call("A1002 #1 24.0 SORT Split")
    assert split["selected_function"]["name"] == "query_wafer_split_at_step"
    assert split["validation"]["missing"][0] == "product"
    assert split["arguments_choices"]["fields"][0]["field"] == "product"

    invalid = llm_router._structure_flowi_function_call("A1002 #26 24.0 SORT Split")
    assert invalid["selected_function"]["invalid_wafers"] == ["26"]
    assert any("26번 wafer" in w for w in invalid["validation"]["warnings"])


def test_flowi_product_process_id_lookup_uses_latest_fab_row(tmp_path, monkeypatch):
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "process_id": "PROC_OLD", "tkout_time": "2026-04-28T08:00:00"},
        {"product": "PRODX", "root_lot_id": "A0002", "process_id": "PROC_NEW", "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "A0003", "process_id": "PROC_NEW", "tkout_time": "2026-04-29T09:00:00"},
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    out = _handle_flowi_query(
        "현재 PRODX 제품 process_id 확인해줘",
        "",
        12,
        allowed_keys={"filebrowser"},
    )

    assert out["handled"] is True
    assert out["intent"] == "product_process_id_lookup"
    assert out["table"]["rows"][0]["process_id"] == "PROC_NEW"
    assert out["table"]["rows"][0]["latest_root_lot_id"] == "A0003"


def test_flowi_meeting_decision_recall_by_date(monkeypatch):
    monkeypatch.setattr(llm_router, "_load_flowi_meetings", lambda: [
        {
            "id": "m1",
            "title": "모듈내부회의",
            "owner": "hol",
            "sessions": [
                {
                    "id": "s1",
                    "idx": 1,
                    "scheduled_at": "2026-04-01T11:00:00",
                    "minutes": {"decisions": [{"id": "d1", "text": "WF Agg를 기본으로 한다", "calendar_pushed": True}], "action_items": []},
                },
                {
                    "id": "s2",
                    "idx": 2,
                    "scheduled_at": "2026-04-08T11:00:00",
                    "minutes": {"decisions": [{"id": "d2", "text": "Admin만 chart default 변경"}], "action_items": []},
                },
            ],
        }
    ])
    monkeypatch.setattr(llm_router, "_load_flowi_calendar_events", lambda: [])

    out = llm_router._handle_meeting_recall(
        "모듈내부회의에서 결정사항들 날짜별로 정리해줘",
        max_rows=12,
        me={"username": "hol", "role": "user"},
    )

    assert out["handled"] is True
    assert out["intent"] == "meeting_recall_summary"
    assert out["table"]["rows"][0]["date"] == "2026-04-08"
    assert out["table"]["rows"][0]["type"] == "decision"
    assert "Admin만" in out["table"]["rows"][0]["text"]


def test_flowi_meeting_recall_filters_requested_session_and_agenda(monkeypatch):
    monkeypatch.setattr(llm_router, "_load_flowi_meetings", lambda: [
        {
            "id": "m1",
            "title": "모듈내부회의",
            "owner": "hol",
            "sessions": [
                {
                    "id": "s1",
                    "idx": 1,
                    "scheduled_at": "2026-04-01T11:00:00",
                    "agendas": [{"title": "1차 아젠다", "owner": "hol"}],
                    "minutes": {"body": "1차 회의록", "decisions": [], "action_items": []},
                },
                {
                    "id": "s2",
                    "idx": 2,
                    "scheduled_at": "2026-04-08T14:30:00",
                    "agendas": [{"title": "2차 아젠다", "description": "차트 default 확정", "owner": "hol"}],
                    "minutes": {"body": "2차 회의록", "decisions": [], "action_items": []},
                },
            ],
        }
    ])
    monkeypatch.setattr(llm_router, "_load_flowi_calendar_events", lambda: [])

    out = llm_router._handle_meeting_recall(
        "모듈내부회의 2차 날짜랑 시간이 어떻게돼? 아젠다는?",
        max_rows=12,
        me={"username": "hol", "role": "user"},
    )

    rows = out["table"]["rows"]
    assert out["handled"] is True
    assert out["table"]["title"] == "Meeting session details"
    assert {row["session_idx"] for row in rows} == {2}
    assert rows[0]["type"] == "session"
    assert rows[0]["date"] == "2026-04-08"
    assert rows[0]["time"] == "14:30"
    assert any(row["type"] == "agenda" and "2차 아젠다" in row["text"] for row in rows)
    assert out["slots"]["meeting_id"] == "m1"
    assert out["slots"]["session_idx"] == 2


def test_flowi_meeting_recall_uses_context_for_followup_session_minutes(monkeypatch):
    monkeypatch.setattr(llm_router, "_load_flowi_meetings", lambda: [
        {
            "id": "m1",
            "title": "모듈내부회의",
            "owner": "hol",
            "sessions": [
                {
                    "id": "s1",
                    "idx": 1,
                    "scheduled_at": "2026-04-01T11:00:00",
                    "minutes": {
                        "body": "1차 회의록 본문",
                        "decisions": [{"id": "d1", "text": "1차 결정"}],
                        "action_items": [{"id": "a1", "text": "1차 액션", "owner": "hol", "due": "2026-04-03"}],
                    },
                },
                {
                    "id": "s2",
                    "idx": 2,
                    "scheduled_at": "2026-04-08T14:30:00",
                    "minutes": {
                        "body": "2차 회의록 본문",
                        "decisions": [{"id": "d2", "text": "2차 결정"}],
                        "action_items": [{"id": "a2", "text": "2차 액션", "owner": "hol", "due": "2026-04-09"}],
                    },
                },
            ],
        }
    ])
    monkeypatch.setattr(llm_router, "_load_flowi_calendar_events", lambda: [])
    agent_context = {
        "type": "home_flowi_chat",
        "messages": [
            {
                "role": "assistant",
                "intent": "meeting_recall_summary",
                "feature": "meeting",
                "slots": {"meeting_id": "m1", "meeting_title": "모듈내부회의", "session_idx": 2},
                "workflow_state": {"slots": {"meeting_id": "m1", "meeting_title": "모듈내부회의", "session_idx": 2}},
            }
        ],
    }

    out = llm_router._handle_meeting_recall(
        "그 회의록 정리해줘",
        max_rows=12,
        me={"username": "hol", "role": "user"},
        agent_context=agent_context,
    )

    rows = out["table"]["rows"]
    assert out["handled"] is True
    assert out["table"]["title"] == "Meeting minutes"
    assert {row["session_idx"] for row in rows} == {2}
    assert any(row["type"] == "minutes" and "2차 회의록 본문" in row["text"] for row in rows)
    assert any(row["type"] == "decision" and "2차 결정" in row["text"] for row in rows)
    assert any(row["type"] == "action" and "2차 액션" in row["text"] for row in rows)
    assert all("1차" not in row["text"] for row in rows)


def test_flowi_app_write_request_returns_draft_not_execution():
    out = llm_router._handle_app_write_draft(
        "A0003 #3에 ABC 이상있는데 꼬리표 남겨줘",
        me={"username": "hol", "role": "user"},
        allowed_keys={"tracker", "inform"},
    )

    assert out["handled"] is True
    assert out["requires_confirmation"] is True
    assert out["intent"] == "lot_wafer_annotation_draft"
    assert out["slots"]["lots"] == ["A0003"]
    assert out["slots"]["wafers"] == ["3"]


def test_flowi_agent_chat_summarizes_inform_modules(monkeypatch):
    from routers import informs as informs_router

    items = [
        {"id": "a", "root_lot_id": "A1000", "lot_id": "A1000", "wafer_id": "1", "module": "ET", "flow_status": "completed", "created_at": "2026-04-27T10:00:00", "status_history": [{"status": "completed", "at": "2026-04-27T11:00:00"}]},
        {"id": "b", "root_lot_id": "A1000", "lot_id": "A1000", "wafer_id": "2", "module": "FAB", "flow_status": "received", "created_at": "2026-04-27T12:00:00"},
    ]
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "lotmgr", "role": "admin"})
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs_router, "_load_upgraded", lambda: items)
    monkeypatch.setattr(informs_router, "_load_config", lambda: {"modules": ["ET", "FAB", "PC"]})

    out = llm_router.flowi_agent_chat(
        llm_router.FlowiAgentChatReq(
            prompt="A1000 인폼 모듈 전체 현황 보여줘",
            source_ai="codex",
            client_run_id="run-1",
        ),
        object(),
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "inform_lot_module_summary"
    assert out["tool"]["summary"]["missing_modules"] == ["PC"]
    assert out["tool"]["summary"]["pending_modules"] == ["FAB"]
    assert out["agent_api"]["source_ai"] == "codex"
    assert out["agent_api"]["client_run_id"] == "run-1"
    assert out["agent_api"]["workflow_state"]["action"] == "summarize_inform_modules"


def test_flowi_agent_chat_creates_inform_record(tmp_path, monkeypatch):
    from routers import informs as informs_router

    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs_router, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs_router, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs_router, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs_router, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "FAB1000.1")
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "lotmgr", "role": "admin"})
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)

    out = llm_router.flowi_agent_chat(
        llm_router.FlowiAgentChatReq(
            prompt="PRODA A1000 #1 인폼 등록, module: ET, reason: PEMS, 내용: Gate CD 이상 확인 필요",
            source_ai="codex",
            client_run_id="run-2",
        ),
        object(),
    )
    saved = informs_router._load_upgraded()

    assert out["ok"] is True
    assert out["tool"]["intent"] == "inform_log_draft"
    assert out["tool"]["requires_confirmation"] is True
    assert out["agent_api"]["workflow_state"]["action"] == "register_inform_log"
    assert len(saved) == 0

    confirm_prompt = out["tool"]["clarification"]["choices"][0]["prompt"]
    confirmed = llm_router.flowi_agent_chat(
        llm_router.FlowiAgentChatReq(
            prompt=confirm_prompt,
            source_ai="codex",
            client_run_id="run-2-confirm",
        ),
        object(),
    )
    saved = informs_router._load_upgraded()

    assert confirmed["tool"]["intent"] == "inform_log_registered"
    assert len(saved) == 1
    assert saved[0]["root_lot_id"] == "A1000"
    assert saved[0]["wafer_id"] == "1"
    assert saved[0]["module"] == "ET"
    assert saved[0]["reason"] == "PEMS"
    assert saved[0]["fab_lot_id_at_save"] == "FAB1000.1"


def test_flowi_fab_step_eta_estimates_from_historical_lots(tmp_path, monkeypatch):
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "lot_id": "A0001.1", "step_id": "AA220000", "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "B0001", "lot_id": "B0001.1", "step_id": "AA220000", "tkout_time": "2026-04-28T00:00:00"},
        {"product": "PRODX", "root_lot_id": "B0001", "lot_id": "B0001.1", "step_id": "AA230400", "tkout_time": "2026-04-28T10:00:00"},
        {"product": "PRODX", "root_lot_id": "B0002", "lot_id": "B0002.1", "step_id": "AA220000", "tkout_time": "2026-04-28T00:00:00"},
        {"product": "PRODX", "root_lot_id": "B0002", "lot_id": "B0002.1", "step_id": "AA230400", "tkout_time": "2026-04-28T20:00:00"},
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda product="", step_id="": {"func_step": {"AA220000": "STI", "AA230400": "MOL"}.get(step_id, "")})

    out = _handle_flowi_query(
        "A0001 AA230400에 언제쯤 도착해?",
        "",
        12,
        allowed_keys={"filebrowser", "dashboard"},
    )

    assert out["handled"] is True
    assert out["intent"] == "fab_step_eta"
    row = out["table"]["rows"][0]
    assert row["product"] == "PRODX"
    assert row["current_step_id"] == "AA220000"
    assert row["target_step_id"] == "AA230400"
    assert row["eta_median_hours"] == 15.0
    assert row["sample_lots"] == 2
    assert row["eta_at_median"] == "2026-04-29T23:00:00"


def test_flowi_et_report_lookup_by_lot(tmp_path, monkeypatch):
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODX" / "date=20260429"
    et_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "step_id": "ET1000", "item_id": "AB_DC", "value": 10.0, "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "step_id": "ET1000", "item_id": "AB_DC", "value": 14.0, "tkout_time": "2026-04-29T08:05:00"},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "02", "step_id": "ET1000", "item_id": "AB_DC", "value": 20.0, "tkout_time": "2026-04-29T08:10:00"},
    ]).write_parquet(et_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_ET"] if kind.upper() == "ET" else [])

    out = _handle_flowi_query(
        "A0002 ET Report 보여줘",
        "",
        12,
        allowed_keys={"ettime", "filebrowser"},
    )

    assert out["handled"] is True
    assert out["intent"] == "et_report_lookup"
    assert out["table"]["kind"] == "et_report_lookup"
    assert out["table"]["total"] == 2
    assert out["table"]["rows"][0]["root_lot_id"] == "A0002"
    assert {row["median"] for row in out["table"]["rows"]} == {12.0, 20.0}


def test_flowi_et_report_freshness_uses_file_and_data_time(tmp_path, monkeypatch):
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODX" / "date=20260429"
    et_dir.mkdir(parents=True)
    fp = et_dir / "ABC_ET_REPORT.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0002", "step_id": "ET1000", "item_id": "AB_DC", "value": 10.0, "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "A0003", "step_id": "ET1000", "item_id": "AB_DC", "value": 20.0, "tkout_time": "2026-04-29T09:30:00"},
    ]).write_parquet(fp)
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_ET"] if kind.upper() == "ET" else [])

    out = _handle_flowi_query(
        "PRODX ABC ET Report 안올라왔는데 언제 최근업데이트 되었어?",
        "",
        12,
        allowed_keys={"ettime", "filebrowser"},
    )

    assert out["handled"] is True
    assert out["intent"] == "et_report_freshness_lookup"
    assert out["table"]["kind"] == "et_report_freshness"
    assert out["table"]["rows"][0]["latest_data_time"] == "2026-04-29T09:30:00"
    assert out["table"]["rows"][0]["row_count"] == 2


def test_flowi_measurement_duration_lookup(tmp_path, monkeypatch):
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODX" / "date=20260429"
    et_dir.mkdir(parents=True)
    pl.DataFrame([
        {
            "product": "PRODX",
            "root_lot_id": "A0001",
            "wafer_id": "01",
            "step_id": "ET1000",
            "item_id": "AB_DC",
            "value": 10.0,
            "tkin_time": "2026-04-29T08:00:00",
            "tkout_time": "2026-04-29T09:00:00",
        }
    ]).write_parquet(et_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_ET"] if kind.upper() == "ET" else [])

    out = _handle_flowi_query(
        "A0001 AB DC 측정시간 얼마나 걸렸어?",
        "",
        12,
        allowed_keys={"ettime", "filebrowser"},
    )

    assert out["handled"] is True
    assert out["intent"] == "measurement_duration_lookup"
    row = out["table"]["rows"][0]
    assert row["item_id"] == "AB_DC"
    assert row["duration_min"] == 60.0
    assert row["duration_basis"] == "start_end_columns"


def test_flowi_inline_item_lookup_by_step_id(tmp_path, monkeypatch):
    inline_dir = tmp_path / "1.RAWDATA_DB_INLINE" / "PRODX" / "date=20260429"
    inline_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "AA230400", "item_id": "CD_TOP", "value": 11.0, "time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "02", "step_id": "AA230400", "item_id": "CD_TOP", "value": 13.0, "time": "2026-04-29T08:10:00"},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "step_id": "AA230400", "item_id": "THK", "value": 100.0, "time": "2026-04-29T09:00:00"},
    ]).write_parquet(inline_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_INLINE"] if kind.upper() == "INLINE" else [])

    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda product="", step_id="": {"func_step": "STI"} if step_id == "AA230400" else {})

    out = _handle_flowi_query(
        "PRODX step_id AA230400이면 이거 어떤 인라인 아이템이야?",
        "",
        12,
        allowed_keys={"filebrowser", "dashboard"},
    )

    assert out["handled"] is True
    assert out["intent"] == "inline_item_by_step_lookup"
    assert out["table"]["kind"] == "inline_item_by_step"
    assert {row["item_id"] for row in out["table"]["rows"]} == {"CD_TOP", "THK"}
    assert out["table"]["rows"][0]["function_step"] == "STI"


def test_flowi_ppid_knob_lookup_links_fab_to_mltable(tmp_path, monkeypatch):
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "AA230400", "ppid": "PPID_STI", "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "step_id": "AA230400", "ppid": "PPID_STI", "tkout_time": "2026-04-29T09:00:00"},
    ]).write_parquet(fab_dir / "part.parquet")
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "KNOB_STI": "A", "KNOB_OTHER": "OFF"},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "KNOB_STI": "B", "KNOB_OTHER": "OFF"},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    out = _handle_flowi_query(
        "PRODX step_id AA230400 ppid PPID_STI 이거 무슨 knob이야?",
        "",
        12,
        allowed_keys={"filebrowser", "splittable", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "ppid_knob_lookup"
    assert out["table"]["kind"] == "ppid_knob_lookup"
    assert {row["knob"] for row in out["table"]["rows"]} >= {"KNOB_STI", "KNOB_OTHER"}
    assert {row["knob_value"] for row in out["table"]["rows"] if row["knob"] == "KNOB_STI"} == {"A", "B"}
    assert out["filters"]["fab_root_count"] == 2


def test_flowi_index_addp_form_lookup_profiles_columns(tmp_path, monkeypatch):
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "ADDP_INDEX": "IDX_A", "VALUE": 1.0},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "ADDP_INDEX": "IDX_B", "VALUE": 2.0},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [])
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [])

    out = _handle_flowi_query(
        "PRODX 이 index는 어떻게 만들어진거야? addp form이 어떻게돼?",
        "",
        12,
        allowed_keys={"filebrowser", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "index_form_lookup"
    assert out["table"]["kind"] == "index_form_lookup"
    assert any(row["column"] == "ADDP_INDEX" for row in out["table"]["rows"])
    assert any(row["source"] == "reformatter_template" and row["column"] == "python_expr" for row in out["table"]["rows"])


def test_flowi_teg_radius_lookup_uses_wafer_layout(monkeypatch):
    import routers.waferlayout as waferlayout_router

    layout = {
        "waferRadius": 35,
        "wfCenterX": 0,
        "wfCenterY": 0,
        "refShotX": 0,
        "refShotY": 0,
        "refShotCenterX": 0,
        "refShotCenterY": 0,
        "shotPitchX": 20,
        "shotPitchY": 20,
        "shotSizeX": 12,
        "shotSizeY": 12,
        "tegSizeX": 1.2,
        "tegSizeY": 0.6,
        "edgeExclusionMm": 0,
        "teg_definitions": [{"id": "AAA_TEG", "label": "AAA_TEG", "dx_mm": 2, "dy_mm": 0}],
    }
    monkeypatch.setattr(waferlayout_router, "_load_product_wafer_layout", lambda product: layout)

    out = _handle_flowi_query(
        "PRODX AAA TEG radius 가장 먼게 어느 샷까지 있어? 풀맵기준으로",
        "",
        12,
        allowed_keys={"waferlayout"},
    )

    assert out["handled"] is True
    assert out["intent"] == "teg_radius_lookup"
    assert out["feature"] == "waferlayout"
    rows = out["table"]["rows"]
    assert rows
    assert rows[0]["teg_label"] == "AAA_TEG"
    assert rows[0]["radius_mm"] >= rows[-1]["radius_mm"]
    assert out["workflow_state"]["intent"] == "teg_radius_lookup" if "workflow_state" in out else True


def test_flowi_teg_position_lookup_returns_shot_local_coordinates(monkeypatch):
    import routers.waferlayout as waferlayout_router

    layout = {
        "shotSizeX": 30,
        "shotSizeY": 30,
        "tegSizeX": 1.5,
        "tegSizeY": 0.7,
        "teg_definitions": [{"id": "AAA_TEG", "label": "AAA_TEG", "dx_mm": 13.6, "dy_mm": 29.6}],
    }
    monkeypatch.setattr(waferlayout_router, "_load_product_wafer_layout", lambda product: layout)

    out = _handle_flowi_query(
        "PRODX AAA TEG Shot내 위치 보여줘",
        "",
        12,
        allowed_keys={"waferlayout"},
    )

    assert out["handled"] is True
    assert out["intent"] == "teg_shot_position_lookup"
    row = out["table"]["rows"][0]
    assert row["teg_label"] == "AAA_TEG"
    assert row["shot_local_x_mm"] == 13.6
    assert row["shot_local_y_mm"] == 29.6
    assert row["origin"] == "shot_center + dx/dy"


def test_flowi_wafer_map_similarity_prefers_beol_candidates(tmp_path, monkeypatch):
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODX" / "date=20260429"
    et_dir.mkdir(parents=True)
    rows = []
    for sx, sy, v in [(0, 0, 1.0), (0, 1, 2.0), (1, 0, 3.0), (1, 1, 4.0)]:
        rows.append({"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "FEOL", "item_id": "VTH", "shot_x": sx, "shot_y": sy, "value": v})
        rows.append({"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "BEOL_M1", "item_id": "BEOL_CD", "shot_x": sx, "shot_y": sy, "value": v * 2})
        rows.append({"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "FEOL", "item_id": "FEOL_INV", "shot_x": sx, "shot_y": sy, "value": 5 - v})
    pl.DataFrame(rows).write_parquet(et_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_ET"] if kind.upper() == "ET" else [])
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [])

    out = _handle_flowi_query(
        "PRODX VTH wf map이랑 가장 비슷한 map을 보이는거는 뭐야? beol쪽 위주로 찾아줘",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "wafer_map_similarity"
    assert out["table"]["kind"] == "wafer_map_similarity"
    row = out["table"]["rows"][0]
    assert row["candidate_item"] == "BEOL_CD"
    assert row["similarity"] == 1.0
    assert row["common_shots"] == 4
    assert row["beol_hint"] is True


def test_flowi_wafer_map_similarity_asks_for_target_item(tmp_path, monkeypatch):
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODX" / "date=20260429"
    et_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "BEOL_M1", "item_id": "BEOL_CD", "shot_x": 0, "shot_y": 0, "value": 1.0},
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "step_id": "BEOL_M1", "item_id": "BEOL_CD", "shot_x": 0, "shot_y": 1, "value": 2.0},
    ]).write_parquet(et_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_ET"] if kind.upper() == "ET" else [])
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [])

    out = _handle_flowi_query(
        "PRODX 이 항목 wf map이랑 가장 비슷한 map을 보이는거는 뭐야? beol쪽 위주로 찾아줘",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "wafer_map_similarity"
    assert out["action"] == "clarify_target_map_item"
    assert out["clarification"]["choices"]


def test_flowi_splittable_fab_lot_basis_uses_match_cache(monkeypatch, tmp_path):
    import routers.splittable as splittable_router

    cache_path = tmp_path / "ML_TABLE_PRODX.parquet"
    monkeypatch.setattr(splittable_router, "_match_cache_current", lambda product: {
        "product": "ML_TABLE_PRODX",
        "fab_source": "1.RAWDATA_DB_FAB/PRODX",
        "path": cache_path,
        "meta": {
            "built_at": "2026-04-29T10:00:00",
            "row_count": 123,
            "join_keys": ["root_lot_id", "wafer_id"],
            "fab_col": "fab_lot_id",
            "ts_col": "tkout_time",
        },
    })
    monkeypatch.setattr(splittable_router, "_match_cache_refresh_minutes", lambda: 30)

    out = _handle_flowi_query(
        "PRODX 스플릿 테이블 fab_lot_id 언제 업데이트 기준이야?",
        "",
        12,
        allowed_keys={"splittable"},
    )

    assert out["handled"] is True
    assert out["intent"] == "splittable_fab_lot_basis"
    row = out["table"]["rows"][0]
    assert row["built_at"] == "2026-04-29T10:00:00"
    assert row["interval_minutes"] == 30
    assert row["ts_col"] == "tkout_time"


def test_flowi_fab_corun_lots_by_function_step(tmp_path, monkeypatch):
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0004", "lot_id": "A0004.1", "fab_lot_id": "A0004.1", "wafer_id": "01", "step_id": "MOL100", "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "B0004", "lot_id": "B0004.1", "fab_lot_id": "B0004.1", "wafer_id": "01", "step_id": "MOL100", "tkout_time": "2026-04-29T09:00:00"},
        {"product": "PRODX", "root_lot_id": "C0004", "lot_id": "C0004.1", "fab_lot_id": "C0004.1", "wafer_id": "01", "step_id": "MOL100", "tkout_time": "2026-05-05T09:00:00"},
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda product="", step_id="": {"func_step": "MOL"} if step_id == "MOL100" else {})

    out = _handle_flowi_query(
        "A0004와 MOL 기준 같이 진행한 랏이 뭐야?",
        "",
        12,
        allowed_keys={"filebrowser", "dashboard"},
    )

    assert out["handled"] is True
    assert out["intent"] == "fab_corun_lots"
    assert out["table"]["rows"][0]["peer_root_lot_id"] == "B0004"
    assert out["table"]["rows"][0]["delta_hours"] == 1.0
    assert out["table"]["total"] == 1


def test_flowi_knob_clean_split_and_interference(tmp_path, monkeypatch):
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A0001", "wafer_id": "01", "KNOB_ABC": "ON", "KNOB_DEF": ""},
        {"product": "PRODX", "root_lot_id": "A0002", "wafer_id": "01", "KNOB_ABC": "ON", "KNOB_DEF": "HI"},
        {"product": "PRODX", "root_lot_id": "A0003", "wafer_id": "01", "KNOB_ABC": "", "KNOB_DEF": "HI"},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    clean = _handle_flowi_query(
        "PRODX ABC Knob 클린 스플릿 어떤 랏이야?",
        "",
        12,
        allowed_keys={"splittable", "ml"},
    )
    assert clean["handled"] is True
    assert clean["intent"] == "knob_clean_split"
    assert clean["table"]["rows"][0]["root_lot_id"] == "A0001"
    assert clean["table"]["rows"][0]["clean_split"] is True

    inter = _handle_flowi_query(
        "PRODX ABC Knob 분석할때 신경쓸만한 다른 Knob 적용된거있어?",
        "",
        12,
        allowed_keys={"splittable", "ml"},
    )
    assert inter["handled"] is True
    assert inter["intent"] == "knob_interference_lookup"
    assert inter["table"]["rows"][0]["root_lot_id"] == "A0002"
    assert "KNOB_DEF=HI" in inter["table"]["rows"][0]["other_knobs"]


def test_flowi_lot_anomaly_summary_compares_against_baseline(tmp_path, monkeypatch):
    et_dir = tmp_path / "1.RAWDATA_DB_ET" / "PRODX" / "date=20260429"
    et_dir.mkdir(parents=True)
    rows = []
    for lot, val in [("A0001", 10.0), ("A0002", 11.0), ("A0003", 9.0), ("A0004", 10.5)]:
        rows.append({"product": "PRODX", "root_lot_id": lot, "wafer_id": "01", "step_id": "ET1000", "item_id": "VTH", "value": val, "tkout_time": "2026-04-29T08:00:00"})
    rows.append({"product": "PRODX", "root_lot_id": "A0005", "wafer_id": "01", "step_id": "ET1000", "item_id": "VTH", "value": 20.0, "tkout_time": "2026-04-29T09:00:00"})
    rows.append({"product": "PRODX", "root_lot_id": "A0005", "wafer_id": "02", "step_id": "ET1000", "item_id": "VTH", "value": 21.0, "tkout_time": "2026-04-29T09:10:00"})
    pl.DataFrame(rows).write_parquet(et_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_ET"] if kind.upper() == "ET" else [])
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [])

    out = _handle_flowi_query(
        "A0005 랏에 특이사항있었어? Trend대비 상하향이나 아님 outlier 생겼던것들",
        "",
        12,
        allowed_keys={"dashboard", "ettime"},
    )

    assert out["handled"] is True
    assert out["intent"] == "lot_anomaly_summary"
    row = out["table"]["rows"][0]
    assert row["item_id"] == "VTH"
    assert row["direction"] == "up"
    assert row["severity"] == "outlier"
    assert row["target_n"] == 2
    assert row["baseline_n"] == 4


def test_flowi_user_file_write_request_is_blocked(monkeypatch):
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)

    out = _run_flowi_chat(
        prompt="files sample.csv 삭제해줘",
        product="",
        max_rows=12,
        me={"username": "normal_user", "role": "user"},
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "blocked_write_request"
    assert out["tool"]["blocked"] is True
    assert out["llm"]["blocked"] is True
    assert out["trace"]["kind"] == "public_execution_trace"
    assert any(step["key"] == "guardrail" and step["status"] == "blocked" for step in out["trace"]["steps"])
    assert "사고과정 원문" in out["trace"]["note"]


def test_flowi_rag_update_marker_alias_is_detected(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)

    def fake_update(prompt, username="", role="user", require_marker=False):
        seen["prompt"] = prompt
        seen["require_marker"] = require_marker
        return {
            "ok": True,
            "saved": {"id": "CK1", "kind": "research_note", "visibility": "private"},
            "structured": {"schema_type": "research_note", "known_canonical_candidates": [], "raw_item_tokens": [], "discriminators": []},
            "storage": {"custom_knowledge": "custom_knowledge.jsonl"},
        }

    monkeypatch.setattr(llm_router.semi_knowledge, "structure_rag_update_from_prompt", fake_update)

    out = _run_flowi_chat(
        prompt="[flow-i update] GAA DIBL SS 판단 지식 추가",
        product="",
        max_rows=12,
        me={"username": "normal_user", "role": "user"},
        allow_rag_update=True,
    )

    assert out["tool"]["intent"] == "semiconductor_rag_update"
    assert seen["require_marker"] is True
    assert seen["prompt"].startswith("[flow-i update]")


def test_flowi_chat_returns_public_execution_trace(monkeypatch):
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_profile_context", lambda _username: "")
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)

    out = _run_flowi_chat(
        prompt="A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘",
        product="",
        max_rows=12,
        me={"username": "trace_user", "role": "admin"},
        agent_context={"messages": [{"role": "user", "text": "이전 질문"}]},
    )

    steps = out["trace"]["steps"]
    assert out["ok"] is True
    assert [step["key"] for step in steps[:4]] == ["receive", "auth", "route", "guardrail"]
    assert any("context 1 messages" in step["detail"] for step in steps)
    assert any(step["key"] == "tool" and "table" in step["detail"] for step in steps)


def test_flowi_admin_file_delete_requires_structured_confirmation(tmp_path, monkeypatch):
    (tmp_path / "sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_flowi_file_roots", lambda: [("DB", tmp_path)])

    out = _run_flowi_chat(
        prompt="files sample.csv 삭제해줘",
        product="",
        max_rows=12,
        me={"username": "admin_user", "role": "admin"},
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "admin_file_operation"
    assert out["tool"]["requires_confirmation"] is True
    assert out["tool"]["clarification"]["choices"][0]["prompt"].startswith("FLOWI_FILE_OP")
    assert (tmp_path / "sample.csv").exists()


def test_flowi_admin_file_delete_archives_file(tmp_path, monkeypatch):
    target = tmp_path / "sample.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_flowi_file_roots", lambda: [("DB", tmp_path)])

    out = _run_flowi_chat(
        prompt='FLOWI_FILE_OP {"op":"delete","path":"sample.csv","confirm":"DELETE sample.csv"}',
        product="",
        max_rows=12,
        me={"username": "admin_user", "role": "admin"},
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "admin_file_operation"
    assert out["tool"]["action"] == "delete"
    assert out["tool"]["file_operation"]["executed"] is True
    assert not target.exists()
    assert list((tmp_path / ".trash").glob("*_sample.csv"))


def test_flowi_admin_replace_text_requires_exact_match_and_backup(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"mode":"old"}\n', encoding="utf-8")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_flowi_file_roots", lambda: [("Files", tmp_path)])

    out = _run_flowi_chat(
        prompt='FLOWI_FILE_OP {"op":"replace_text","path":"settings.json","old":"old","new":"new","confirm":"REPLACE settings.json"}',
        product="",
        max_rows=12,
        me={"username": "admin_user", "role": "admin"},
    )

    assert out["tool"]["action"] == "replace_text"
    assert target.read_text(encoding="utf-8") == '{"mode":"new"}\n'
    assert list((tmp_path / ".trash").glob("*_settings.json.bak"))


def test_flowi_chat_route_passes_home_conversation_context(monkeypatch):
    seen = {}

    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})

    def fake_run_flowi_chat(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "answer": "ok"}

    monkeypatch.setattr(llm_router, "_run_flowi_chat", fake_run_flowi_chat)

    out = llm_router.flowi_chat(
        llm_router.FlowiChatReq(
            prompt="그 조건으로 다음도 봐줘",
            context={"type": "home_flowi_chat", "messages": [{"role": "user", "text": "A10001 먼저 봐줘"}]},
        ),
        request=object(),
    )

    assert out["ok"] is True
    assert seen["prompt"] == "그 조건으로 다음도 봐줘"
    assert seen["agent_context"]["type"] == "home_flowi_chat"
    assert seen["agent_context"]["messages"][0]["text"] == "A10001 먼저 봐줘"


def test_flowi_chat_route_redacts_workflow_for_non_admin(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(
        llm_router,
        "_run_flowi_chat",
        lambda **_kwargs: {
            "ok": True,
            "active": True,
            "answer": "선택해주세요.",
            "trace": {"steps": [{"key": "route"}]},
            "workflow_state": {"status": "awaiting_choice"},
            "next_actions": [{"type": "open_tab", "tab": "splittable"}],
            "llm": {"used": True},
            "agent_api": {"workflow_state": {"status": "awaiting_choice"}},
            "tool": {
                "intent": "splittable_plan_confirm",
                "action": "confirm_splittable_plan",
                "workflow_state": {"status": "awaiting_choice"},
                "next_actions": [{"type": "open_tab", "tab": "splittable"}],
                "feature_entrypoints": [{"key": "splittable"}],
                "clarification": {"question": "저장할까요?", "choices": [{"id": "ok", "label": "1", "title": "저장", "prompt": "yes"}]},
                "table": {"columns": [], "rows": []},
            },
        },
    )

    out = llm_router.flowi_chat(llm_router.FlowiChatReq(prompt="plan"), request=object())

    assert out["ok"] is True
    assert "trace" not in out
    assert "workflow_state" not in out
    assert "next_actions" not in out
    assert "llm" not in out
    assert "intent" not in out["tool"]
    assert "workflow_state" not in out["tool"]
    assert out["tool"]["clarification"]["choices"][0]["title"] == "저장"


def test_flowi_verify_tests_llm_connection_for_home_start(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": True, "text": "확인완료"})

    out = llm_router.flowi_verify(llm_router.FlowiVerifyReq(), object())

    assert out == {"ok": True, "message": "확인완료"}


def test_flowi_verify_reports_disconnected_when_llm_test_fails(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": False, "error": "timeout"})

    out = llm_router.flowi_verify(llm_router.FlowiVerifyReq(), object())

    assert out["ok"] is False
    assert out["error"] == "timeout"


def test_flowi_verify_requires_confirmation_text(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": True, "text": "pong"})

    out = llm_router.flowi_verify(llm_router.FlowiVerifyReq(), object())

    assert out["ok"] is False
    assert out["error"] == "pong"


def test_flowi_agent_chat_accepts_codex_source_and_returns_web_actions(monkeypatch):
    seen = {}

    def fake_complete(prompt, **_kwargs):
        seen["prompt"] = prompt
        return {"ok": True, "text": "Codex 입력 기준으로 스플릿 테이블을 열고 plan/actual을 확인하세요."}

    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_profile_context", lambda _username: "")
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", fake_complete)

    out = _run_flowi_chat(
        prompt="A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘",
        product="",
        max_rows=12,
        me={"username": "codex_tester", "role": "admin"},
        source_ai="codex",
        client_run_id="codex-smoke-1",
        agent_context={"origin": "codex-test", "surface": "api"},
    )

    assert out["ok"] is True
    assert out["llm"]["used"] is True
    assert out["answer"].startswith("Codex 입력 기준")
    assert out["agent_api"]["received"] is True
    assert out["agent_api"]["source_ai"] == "codex"
    assert out["agent_api"]["auth_user"] == "codex_tester"
    assert out["workflow_state"]["status"] == "ready"
    assert out["agent_api"]["workflow_state"]["intent"] == out["tool"]["intent"]
    assert any(a["type"] == "open_tab" for a in out["next_actions"])
    assert any(a["type"] == "open_tab" and a["tab"] == "splittable" for a in out["agent_api"]["actions"])
    assert any(a["type"] == "flowi_unit_action" and a["action"] == "open_splittable" for a in out["agent_api"]["actions"])
    assert "외부 AI source: codex" in seen["prompt"]
    assert "codex-smoke-1" in seen["prompt"]
    assert "codex-test" in seen["prompt"]


def test_flowi_chart_tool_skips_llm_polish_for_fast_visible_payload(monkeypatch):
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_profile_context", lambda _username: "")
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_router.llm_adapter,
        "complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("chart requests should not wait for LLM polish")),
    )
    monkeypatch.setattr(
        llm_router,
        "_handle_flowi_query",
        lambda *_args, **_kwargs: {
            "handled": True,
            "intent": "dashboard_box_chart",
            "action": "query_inline_box_chart",
            "answer": "chart ready",
            "feature": "dashboard",
            "chart_result": {"kind": "dashboard_box", "boxes": []},
        },
    )

    out = _run_flowi_chat(
        prompt="PRODA CD_GATE box plot 그려줘",
        product="PRODA",
        max_rows=12,
        me={"username": "fast_chart_user", "role": "admin"},
    )

    assert out["ok"] is True
    assert out["answer"] == "chart ready"
    assert out["llm"]["used"] is False
    assert out["llm"]["skipped"] == "deterministic_tool_result"


def test_flowi_agent_api_returns_confirmation_workflow_for_app_writes(monkeypatch):
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: False)

    out = _run_flowi_chat(
        prompt="A0003 #3에 ABC 이상있는데 꼬리표 남겨줘",
        product="",
        max_rows=12,
        me={"username": "discord_user", "role": "admin"},
        source_ai="discord",
        client_run_id="discord-1",
        agent_context={"surface": "discord", "messages": [{"role": "user", "text": "이어서 해줘"}]},
    )

    assert out["ok"] is True
    assert out["tool"]["requires_confirmation"] is True
    assert out["workflow_state"]["status"] == "awaiting_confirmation"
    assert out["workflow_state"]["waiting_for"] == "user_confirmation"
    assert out["workflow_state"]["context_message_count"] == 1
    assert out["agent_api"]["requires_confirmation"] is True
    assert out["agent_api"]["workflow_state"]["status"] == "awaiting_confirmation"
    assert any(a["type"] == "respond_with_prompt" and a["recommended"] for a in out["agent_api"]["next_actions"])
    assert out["tool"]["slots"]["lots"] == ["A0003"]
    assert out["tool"]["slots"]["wafers"] == ["3"]


def test_flowi_semiconductor_diagnosis_includes_file_source_profile(monkeypatch):
    monkeypatch.setattr(llm_router, "_flowi_file_tokens", lambda _prompt: ["ET_PRODX.parquet"])
    monkeypatch.setattr(llm_router.semi_knowledge, "dataset_profile", lambda source, limit=250: {
        "ok": True,
        "source": source,
        "suggested_source_type": "ET",
        "metric_shape": "long",
        "grain": "lot_wf",
        "join_keys": ["lot_wf", "root_lot_id", "wafer_id"],
        "unique_items": ["DIBL", "SS"],
        "metric_columns": [],
        "default_aggregation": "median by lot_wf unless exact shot/point match exists",
        "warnings": [],
    })
    monkeypatch.setattr(llm_router.semi_knowledge, "run_diagnosis", lambda prompt, product="", filters=None, save=True, **_kwargs: {
        "ranked_hypotheses": [
            {
                "rank": 1,
                "hypothesis": "GAA electrostatic control degradation",
                "confidence": 0.72,
                "electrical_mechanism": "DIBL/SS increase",
                "knowledge_card_id": "KC_DIBL_SS_GAA_ELECTROSTATICS",
            }
        ],
        "interpreted_items": {
            "resolved": [
                {"raw_item": "DIBL", "status": "resolved", "canonical_item_id": "DIBL", "item": {"meaning": "Drain-induced barrier lowering"}},
                {"raw_item": "SS", "status": "resolved", "canonical_item_id": "SS", "item": {"meaning": "Subthreshold swing"}},
            ]
        },
        "feature_extractor": {"items": ["DIBL", "SS"], "modules": ["GAA_CHANNEL_RELEASE"]},
    })

    out = llm_router._handle_semiconductor_diagnosis_query(
        "ET_PRODX.parquet 기준으로 DIBL SS 증가 원인 후보 보여줘",
        "PRODX",
        12,
    )

    assert out["handled"] is True
    assert out["data_source"]["file"] == "ET_PRODX.parquet"
    assert out["source_profile"]["suggested_source_type"] == "ET"
    assert out["source_profile"]["metric_shape"] == "long"
    assert "ET_PRODX.parquet" in out["answer"]
    assert "ET / long / lot_wf" in out["answer"]
    trace = llm_router._flowi_public_trace(
        prompt="ET_PRODX.parquet 기준으로 DIBL SS 증가 원인 후보 보여줘",
        allowed_keys={"diagnosis"},
        result={"tool": out, "llm": {"available": False, "used": False}},
    )
    tool_step = next(step for step in trace["steps"] if step["key"] == "tool")
    assert "source=ET_PRODX.parquet" in tool_step["detail"]
    assert "profile=ET / long / lot_wf" in tool_step["detail"]


def test_flowi_semiconductor_diagnosis_asks_when_file_profile_is_ambiguous(monkeypatch):
    monkeypatch.setattr(llm_router, "_flowi_file_tokens", lambda _prompt: ["MIXED_PRODX.parquet"])
    monkeypatch.setattr(llm_router.semi_knowledge, "dataset_profile", lambda source, limit=250: {
        "ok": True,
        "source": source,
        "suggested_source_type": "AUTO",
        "metric_shape": "wide",
        "grain": "row",
        "join_keys": [],
        "unique_items": [],
        "metric_columns": ["COL_A", "COL_B"],
        "default_aggregation": "Review grain and item meaning before aggregation.",
        "warnings": ["Source type could not be inferred; choose ET/INLINE/EDS/VM/QTIME in the prompt or source profile."],
    })

    def fail_run(*_args, **_kwargs):
        raise AssertionError("run_diagnosis should wait for source clarification")

    monkeypatch.setattr(llm_router.semi_knowledge, "run_diagnosis", fail_run)

    out = llm_router._handle_semiconductor_diagnosis_query(
        "MIXED_PRODX.parquet 기준으로 DIBL SS 증가 원인 후보 보여줘",
        "PRODX",
        12,
    )

    assert out["handled"] is True
    assert out["intent"] == "semiconductor_source_clarification"
    assert out["action"] == "confirm_semiconductor_source_profile"
    assert out["data_source"]["file"] == "MIXED_PRODX.parquet"
    assert out["source_profile"]["suggested_source_type"] == "AUTO"
    choices = out["clarification"]["choices"]
    assert choices[0]["label"] == "1"
    assert choices[0]["recommended"] is True
    assert "source_type=ET" in choices[0]["prompt"]
    assert out["table"]["kind"] == "semiconductor_source_profile_review"


def test_flowi_semiconductor_diagnosis_runs_after_source_type_choice(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm_router, "_flowi_file_tokens", lambda _prompt: ["MIXED_PRODX.parquet"])
    monkeypatch.setattr(llm_router.semi_knowledge, "dataset_profile", lambda source, limit=250: {
        "ok": True,
        "source": source,
        "suggested_source_type": "AUTO",
        "metric_shape": "wide",
        "grain": "row",
        "join_keys": [],
        "warnings": ["Source type could not be inferred; choose ET/INLINE/EDS/VM/QTIME in the prompt or source profile."],
    })

    def fake_run(prompt, product="", filters=None, save=True, **_kwargs):
        seen["filters"] = filters or {}
        return {
            "ranked_hypotheses": [],
            "interpreted_items": {"resolved": []},
            "feature_extractor": {"items": ["DIBL"], "modules": []},
        }

    monkeypatch.setattr(llm_router.semi_knowledge, "run_diagnosis", fake_run)

    out = llm_router._handle_semiconductor_diagnosis_query(
        "MIXED_PRODX.parquet 기준으로 DIBL 증가 원인 후보 보여줘 / source_type=ET grain=lot_wf aggregation=median 으로 진행",
        "PRODX",
        12,
    )

    assert out["intent"] == "semiconductor_diagnosis"
    assert out["data_source"]["flowi_source_confirmed"] is True
    assert out["data_source"]["source_type_filter"] == "ET"
    assert seen["filters"]["source_type_filter"] == "ET"


def test_flowi_feedback_tags_normalize_and_default_by_rating():
    assert _normalize_feedback_tags([], "up") == ["correct"]
    assert _normalize_feedback_tags([], "down") == ["output_issue"]
    assert _normalize_feedback_tags(["wrong_data_source", "bad_key", "wrong_data_source"], "down") == ["wrong_data_source"]


def test_flowi_feedback_summary_builds_review_queue():
    records = [
        {
            "id": "ff1",
            "timestamp": "2026-04-29T00:00:00+00:00",
            "username": "u1",
            "rating": "up",
            "intent": "inform_create",
            "tags": ["correct"],
        },
        {
            "id": "ff2",
            "timestamp": "2026-04-29T00:01:00+00:00",
            "username": "u2",
            "rating": "down",
            "intent": "dashboard_scatter_plan",
            "tags": ["wrong_data_source", "missed_clarification"],
            "needs_review": True,
        },
    ]

    summary = _feedback_summary_from_records(records)

    assert summary["total"] == 2
    assert summary["by_rating"]["down"] == 1
    assert summary["by_tag"]["wrong_data_source"] == 1
    assert [r["id"] for r in summary["review_queue"]] == ["ff2"]


def test_flowi_feedback_promotes_to_golden_case_with_forbidden_rules():
    rec = {
        "id": "ff2",
        "prompt_excerpt": "INLINE CD와 ET LKG 그려줘",
        "rating": "down",
        "intent": "dashboard_scatter_plan",
        "tags": ["hallucination", "aggregation_error"],
        "correct_route": "INLINE은 avg, ET는 median으로 lot_wf join",
        "tool_summary": {"action": "build_metric_scatter"},
    }

    case = _feedback_to_golden_case(rec, created_by="admin")

    assert case["source_feedback_id"] == "ff2"
    assert case["expected_intent"] == "dashboard_scatter_plan"
    assert case["expected_tool"] == "build_metric_scatter"
    assert "INLINE은 avg" in case["expected_answer"]
    assert any("없는 값" in rule or "생성하지" in rule for rule in case["forbidden"])
