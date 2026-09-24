import json
from types import SimpleNamespace

import polars as pl
import pytest

from test_home_inline_shot_chart import scenario
from core import data_chat_wafer_map as wafer, lot_progress_cache
from core.latest_lot_cache_format import FORMAT_COLUMN, FORMAT_VERSION
from routers import filebrowser as fb, lot_progress


@pytest.fixture
def mapped(scenario, tmp_path, monkeypatch):
    send, _, user, rawdir, _ = scenario
    paths = SimpleNamespace(base_root=tmp_path, data_root=tmp_path / "state", db_root=tmp_path)
    monkeypatch.setattr(wafer, "PATHS", paths)
    monkeypatch.setattr(fb, "PATHS", paths)
    monkeypatch.setattr(lot_progress, "current_user", lambda request: user)
    wip = tmp_path / "wip.parquet"
    pl.DataFrame({FORMAT_COLUMN: [FORMAT_VERSION] * 3, "product": ["PRODA"] * 3,
        "root_lot_id": ["AZAAA"] * 3, "lot_id": ["AZBBB.1", "AZBBB.1", "AZBBB.2"], "wafer_id": [1, 2, 3]}).write_parquet(wip)
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: wip)
    conf = tmp_path / "confidential"
    conf.mkdir()
    (conf / "inline_shot_matching.csv").write_text("product,step_id,item_id,map_name\nPRODA,PC100,CD1,PC_MAP\n", encoding="utf-8")
    (conf / "inline_map_settings.json").write_text(json.dumps({"tables": [{"table_name": "PC_MAP", "vehicle": "VH_PRODA",
        "shots": [{"subitem_id": "SHOT01", "shot_x": -1, "shot_y": 0}, {"subitem_id": "SHOT02", "shot_x": 0, "shot_y": 0},
                  {"subitem_id": "SHOT03", "shot_x": 1, "shot_y": 0}, {"subitem_id": "SHOT04", "shot_x": 99, "shot_y": 99}]}]}), encoding="utf-8")
    monkeypatch.setattr(wafer.teg_map, "map_payload", lambda vehicle: {"vehicle": vehicle,
        "geometry": {"fit": "radius", "wafer_radius_mm": 150, "wafer_edge_mm": 147, "kx": 20, "ky": 20, "cx": 0, "cy": 0, "shot_w_mm": 20, "shot_h_mm": 20},
        "shots": [{"x": x, "y": 0, "mm_x": x * 20, "mm_y": 0, "radius": abs(x)*20} for x in [-1, 0, 1]], "tegs": []})
    return send, rawdir, conf


def test_current_lot_membership_mapping_trellis_global_scale_and_history(mapped):
    send, _, _ = mapped
    result = send("PRODA AZBBB.1 PC CD wafer map 그려줘")
    assert result["ok"], result
    chart = result["tool"]["chart_result"]
    assert chart["chart_type"] == "wafer_map"
    assert [p["points"][0]["value"] for p in chart["panels"]] == [10., 20.]
    assert [p["points"][0]["x"] for p in chart["panels"]] == [-1., 0.]  # overrides raw 1,2
    assert chart["wafer_low"] == 11 and chart["wafer_center"] == 15 and chart["wafer_high"] == 19
    assert len(fb._chart_builder_history_entries()) == 1
    code = result["tool"]["definition_code"]
    assert "wafer_map" in code and "PC100" in code and "SITE" in code
    assert "AZAAA" in code and "WAFER_HIGH = 19" in code
    from core.chart_builder_definition import parse_chart_builder_definition
    parsed = parse_chart_builder_definition(code)
    assert parsed["sources"][0]["runtime_lot_wafer_pairs"] == [
        {"root_lot_id": "AZAAA", "wafer_id": "1"}, {"root_lot_id": "AZAAA", "wafer_id": "2"}]
    replay = fb.chart_builder_run(fb.ChartBuilderRunReq(**{k: parsed[k] for k in ("sources", "joins", "chart", "max_rows")}, save_history=False), None)
    assert len(replay["joined"]["rows"]) == 2


def test_no_current_membership_never_expands_to_sibling_lot(mapped):
    send, *_ = mapped
    result = send("PRODA AZNONE.1 PC CD wafer map 그려줘")
    assert not result["ok"]
    assert "형제 Lot" in result["reply"]
    assert not fb._chart_builder_history_entries()


def test_outside_and_unmapped_shots_excluded_not_placed_at_raw_coordinates(mapped):
    send, rawdir, _ = mapped
    frame = pl.read_parquet(rawdir / "part.parquet")
    extra = frame.head(1).with_columns(pl.lit("SHOT04").alias("subitem_id"), pl.lit(100.).alias("value"))
    unknown = frame.head(1).with_columns(pl.lit("UNMAPPED").alias("subitem_id"), pl.lit(200.).alias("value"))
    pl.concat([frame, extra, unknown]).write_parquet(rawdir / "part.parquet")
    result = send("PRODA AZBBB.1 PC CD wafer map 그려줘")
    assert result["ok"], result
    assert result["tool"]["table"]["total"] == 2
    assert "2개를 제외" in result["reply"]


def test_repeated_measurements_ask_aggregation_before_history(mapped):
    send, rawdir, _ = mapped
    frame = pl.read_parquet(rawdir / "part.parquet")
    pl.concat([frame, frame.head(1).with_columns(pl.lit(40.).alias("value"))]).write_parquet(rawdir / "part.parquet")
    result = send("PRODA AZBBB.1 PC CD wafer map 그려줘")
    assert result["tool"]["missing"] == ["aggregation"]
    assert not fb._chart_builder_history_entries()
    result = send("평균")
    assert result["ok"], result
    assert result["tool"]["chart_result"]["panels"][0]["points"][0]["value"] == 25
    assert len(fb._chart_builder_history_entries()) == 1
