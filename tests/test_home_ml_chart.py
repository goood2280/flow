from types import SimpleNamespace

import polars as pl
import pytest

from core import audit, data_chat_ml_chart as ml_chart
from routers import filebrowser


@pytest.fixture
def ml_database(tmp_path, monkeypatch):
    path = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({
        "root_lot_id": ["LOT1", "LOT2", "LOT3"],
        "wafer_id": [1, 2, 3],
        "00.0_AAXV": [1.0, 2.0, 3.0],
        "00.0_AAXW": [4.0, 5.0, 6.0],
        "00.0_AAXV_tkout_time": ["2026-09-01 01:00:00", "2026-09-02 01:00:00", "2026-09-03 01:00:00"],
        "10.0_OTHER_tkout_time": ["2026-08-01 01:00:00", "2026-08-02 01:00:00", "2026-08-03 01:00:00"],
        "BDCD_ecu_all": ["EQ1_CH1_U1", "EQ1_CH2", "EQ2"],
        "ECU_BDCE_ALL": ["EQ9_CH9_U9", "EQ9", None],
        "KNOB_00.0 AAXV": ["S0", "S1", "S0"],
    }).write_parquet(path)

    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [path] if root == "ML_TABLE" and product == "PRODA" else [])
    user = {"username": "engineer", "role": "user"}
    monkeypatch.setattr(ml_chart.auth, "current_user", lambda request: user)
    monkeypatch.setattr(ml_chart.auth, "effective_permissions", lambda _user: {"tabs": ["chartbuilder"]})
    monkeypatch.setattr(filebrowser, "current_user", lambda request: user)
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(filebrowser, "_record_chart_builder_history", lambda **kwargs: {
        "history_id": "chart_ml_1", "name": kwargs["req"].chart_name, "reuse_count": 0,
    })
    filebrowser._CHART_BUILDER_RESULT_CACHE.clear()
    return path


def _context(**extra):
    return {"confirmed_product": "PRODA", "product": "PRODA", **extra}


def test_non_exact_numeric_measurement_requires_human_choice_and_ranks_time(ml_database):
    first = ml_chart.dispatch("PRODA AAX Trend ML_TABLE_제품으로 그려줘", _context())
    assert first["tool"]["clarification"]["kind"] == "ml_measurement"
    assert [option["value"] for option in first["tool"]["clarification"]["options"]][:2] == [
        "00.0_AAXV", "00.0_AAXW",
    ]

    second = ml_chart.dispatch("00.0_AAXV", first["context"])
    assert second["tool"]["clarification"]["kind"] == "ml_time"
    options = second["tool"]["clarification"]["options"]
    assert options[0]["value"] == "00.0_AAXV_tkout_time"
    assert options[0]["same_numeric_prefix"] is True
    assert options[0]["process_similarity"] > options[1]["process_similarity"]


def test_exact_measurement_still_confirms_unspecified_time_then_runs_shared_builder(ml_database):
    first = ml_chart.dispatch("PRODA AAXV Trend 스플릿테이블에 있는거 가져와서 그려줘", _context())
    assert first["tool"]["clarification"]["kind"] == "ml_time"
    assert first["context"]["pending_ml_chart"]["query"]["ml_measurement"] == "00.0_AAXV"

    result = ml_chart.dispatch("00.0_AAXV_tkout_time", first["context"])
    assert result["ok"], result
    assert result["tool"]["saved_chart"]["id"] == "chart_ml_1"
    assert result["tool"]["chart_result"]["y"] == "00.0_AAXV"
    assert result["tool"]["chart_result"]["x"] == "00.0_AAXV_tkout_time"
    assert result["tool"]["chart_result"]["points"][0]["y"] == 3.0
    assert "TABLE = ML_TABLE" in result["tool"]["definition_code"]
    assert "PRODUCT = PRODA" in result["tool"]["definition_code"]
    assert result["context"]["ml_chart_query"]["history_id"] == "chart_ml_1"
    assert "pending_ml_chart" not in result["context"]


def test_even_one_time_candidate_requires_confirmation(tmp_path, monkeypatch, ml_database):
    frame = pl.read_parquet(ml_database).drop("10.0_OTHER_tkout_time")
    frame.write_parquet(ml_database)
    first = ml_chart.dispatch("PRODA AAXV Trend ML_TABLE로 그려줘", _context())
    assert first["tool"]["clarification"]["kind"] == "ml_time"
    assert [row["value"] for row in first["tool"]["clarification"]["options"]] == ["00.0_AAXV_tkout_time"]


def test_fuzzy_ecu_color_requires_choice_and_derives_equipment_chamber(ml_database):
    first = ml_chart.dispatch("PRODA AAXV Trend ML_TABLE로 00.0_AAXV_tkout_time 그려줘", _context())
    assert first["ok"], first

    pending = ml_chart.dispatch("BDCD ecu로 eqp id와 chamber id 컬러링", first["context"])
    assert pending["tool"]["clarification"]["kind"] == "ml_color"
    selected = next(option for option in pending["tool"]["clarification"]["options"] if option["value"] == "BDCD_ecu_all")
    result = ml_chart.dispatch(selected["value"], pending["context"])
    assert result["ok"], result
    chart = result["tool"]["chart_result"]
    assert chart["color_by"] == "ml_equipment_chamber_color"
    assert chart["color_source"] == "BDCD_ecu_all"
    assert chart["color_mode"] == "equipment_chamber"
    assert {point["color_value"] for point in chart["points"]} == {"EQ1_CH1", "EQ1_CH2", "EQ2"}
    assert "DERIVE = ml_equipment_chamber_color" in result["tool"]["definition_code"]


def test_exact_ecu_color_and_knob_resolution_use_actual_columns(ml_database, monkeypatch):
    first = ml_chart.dispatch("PRODA AAXV Trend ML_TABLE로 00.0_AAXV_tkout_time 그려줘", _context())
    ecu = ml_chart.dispatch("BDCD ecu_all로 컬러링", first["context"])
    assert ecu["ok"], ecu
    assert ecu["tool"]["chart_result"]["color_by"] == "BDCD_ecu_all"

    called = {}
    from core import knob_resolution
    monkeypatch.setattr(knob_resolution, "resolve", lambda product, text, columns, username: (
        called.update(product=product, columns=columns, username=username) or
        {"alias": "aaxv", "exact": "KNOB_00.0 AAXV", "options": []}
    ))
    knob = ml_chart.dispatch("AAXV KNOB로 컬러링", ecu["context"])
    assert knob["ok"], (knob["reply"], knob["tool"])
    assert called == {"product": "PRODA", "columns": ["KNOB_00.0 AAXV"], "username": "engineer"}
    assert knob["tool"]["chart_result"]["color_by"] == "KNOB_00.0 AAXV"


def test_equipment_followup_keeps_selected_source_and_definition_replays(ml_database):
    first = ml_chart.dispatch("PRODA AAXV Trend ML_TABLE로 00.0_AAXV_tkout_time 그려줘", _context())
    ecu = ml_chart.dispatch("BDCD ecu_all로 컬러링", first["context"])
    result = ml_chart.dispatch("eqp id와 chamber id로 컬러링 해줘", ecu["context"])
    assert result["ok"], result
    from core.chart_builder_definition import parse_chart_builder_definition
    parsed = parse_chart_builder_definition(result["tool"]["definition_code"])
    assert parsed["sources"][0]["derived_columns"][0]["operation"] == "split_prefix"
    replay = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(**{k: parsed[k] for k in ("sources", "joins", "chart", "max_rows")}, save_history=False), None)
    assert {r["ml_equipment_chamber_color"] for r in replay["joined"]["rows"]} == {"EQ1_CH1", "EQ1_CH2", "EQ2"}
