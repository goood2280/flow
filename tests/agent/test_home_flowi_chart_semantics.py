from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for p in (BACKEND, ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from core import dashboard_join  # noqa: E402
from core import semantic_measure_catalog as catalog  # noqa: E402
from routers import llm  # noqa: E402


def _patch_semantic_catalog(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "TERMS_FILE", tmp_path / "flow-data" / "semantic" / "measurement_terms.json")
    monkeypatch.setattr(catalog, "CHANGES_FILE", tmp_path / "flow-data" / "semantic" / "measurement_terms.changes.jsonl")
    monkeypatch.setattr(
        catalog,
        "CHANGE_MANAGEMENT_HISTORY",
        tmp_path / "flow-data" / "agent_unit_ai_sessions" / "change_management" / "history.jsonl",
    )
    monkeypatch.setattr(catalog, "PATHS", SimpleNamespace(db_root=tmp_path / "DB"))


def test_home_flowi_et_trend_session_supports_followup_knob_coloring(monkeypatch, tmp_path):
    db_root = tmp_path / "DB"
    et_dir = db_root / "1.RAWDATA_DB_ET" / "PRODA"
    et_dir.mkdir(parents=True)
    pl.DataFrame(
        [
            {"product": "PRODA", "root_lot_id": "A1001", "lot_id": "L1", "wafer_id": 1, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 10.0, "tkout_time": "2026-01-01T00:00:00"},
            {"product": "PRODA", "root_lot_id": "A1001", "lot_id": "L1", "wafer_id": 1, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 12.0, "tkout_time": "2026-01-01T00:00:00"},
            {"product": "PRODA", "root_lot_id": "A1001", "lot_id": "L1", "wafer_id": 2, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 20.0, "tkout_time": "2026-01-02T00:00:00"},
            {"product": "PRODA", "root_lot_id": "A1002", "lot_id": "L2", "wafer_id": 1, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 30.0, "tkout_time": "2026-01-03T00:00:00"},
            {"product": "PRODA", "root_lot_id": "A1002", "lot_id": "L2", "wafer_id": 2, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 40.0, "tkout_time": "2026-01-04T00:00:00"},
        ]
    ).write_parquet(et_dir / "part.parquet")
    pl.DataFrame(
        [
            {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 1, "KNOB_SPEED": "FAST"},
            {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 2, "KNOB_SPEED": "SLOW"},
            {"product": "PRODA", "root_lot_id": "A1002", "wafer_id": 1, "KNOB_SPEED": "FAST"},
            {"product": "PRODA", "root_lot_id": "A1002", "wafer_id": 2, "KNOB_SPEED": "SLOW"},
        ]
    ).write_parquet(db_root / "ML_TABLE_PRODA.parquet")
    monkeypatch.setattr(llm, "PATHS", SimpleNamespace(db_root=db_root, base_root=db_root, data_root=tmp_path / "flow-data"))
    monkeypatch.setattr(dashboard_join, "CHART_SESSION_DIR", tmp_path / "chart-sessions")

    first = llm._handle_flowi_query_core(
        "PRODA ET IOFF trend chart",
        product="PRODA",
        max_rows=20,
        allowed_keys={"dashboard"},
        username="tester",
        agent_context={},
    )

    first_chart = first["chart_result"]
    assert first["intent"] == "dashboard_et_trend_chart"
    assert first["chart_session_id"]
    assert first_chart["chart_type"] == "scatter"
    assert first_chart["x_label"] == "tkout_time"
    assert first_chart["aggregation"] == "median"
    assert first_chart["color_by"] == ""
    assert len(first_chart["points"]) == 4
    assert first_chart["points"][0]["median"] == 11.0

    second = llm._handle_flowi_query_core(
        "color by SPEED KNOB",
        product="PRODA",
        max_rows=20,
        allowed_keys={"dashboard"},
        username="tester",
        agent_context={"chart_session_id": first["chart_session_id"]},
    )

    chart = second["chart_result"]
    assert second["intent"] == "dashboard_chart_knob_coloring"
    assert chart["chart_session_id"] == first["chart_session_id"]
    assert chart["color_by"] == "SPEED"
    assert chart["config"]["color_by"] == "SPEED"
    assert second["validation"]["base_rows_reused"] == 4
    assert second["validation"]["matched_rows"] == 4
    assert {point["color_value"] for point in chart["points"]} == {"FAST", "SLOW"}
    assert {row["value"]: row["count"] for row in chart["color_values"]} == {"FAST": 2, "SLOW": 2}


def test_semantic_measure_keyword_resolves_step_and_item_id(monkeypatch, tmp_path):
    _patch_semantic_catalog(monkeypatch, tmp_path)
    src_dir = tmp_path / "DB" / "1.RAWDATA_DB_ET" / "PRODA"
    src_dir.mkdir(parents=True)
    pl.DataFrame(
        [
            {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 1, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 10.0},
            {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 1, "step_id": "OTHER_STEP", "item_id": "IOFF", "value": 999.0},
            {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 1, "step_id": "ET_STEP_7", "item_id": "OTHER_ITEM", "value": 888.0},
            {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": 2, "step_id": "ET_STEP_7", "item_id": "IOFF", "value": 20.0},
        ]
    ).write_parquet(src_dir / "part.parquet")
    korean_alias = "\ud56b\ub9ac\ud06c"
    catalog.save_term(
        {
            "id": "measure_et_proda_hot_leak",
            "term": "Hot Leak",
            "aliases": [korean_alias, "HL_KEYWORD"],
            "source_type": "ET",
            "product": "PRODA",
            "step_id": "ET_STEP_7",
            "item_id": "IOFF",
            "default_agg": "avg",
        },
        actor="tester",
    )

    out = catalog.query_measurement(f"PRODA A1001 {korean_alias} value", product="PRODA", max_rows=25)
    tool = llm._handle_semantic_measurement("PRODA A1001 HL_KEYWORD value", product="PRODA", max_rows=25)

    assert out and out["handled"] is True
    assert "item_id=IOFF" in out["term_resolution"][0]["meaning"]
    assert [row["value_avg"] for row in out["table"]["rows"]] == [10.0, 20.0]
    assert {row["step_id"] for row in out["table"]["rows"]} == {"ET_STEP_7"}
    assert {row["item_id"] for row in out["table"]["rows"]} == {"IOFF"}
    assert tool and tool["intent"] == "semantic_measurement_lookup"
    assert [row["value_avg"] for row in tool["table"]["rows"]] == [10.0, 20.0]
