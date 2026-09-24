from types import SimpleNamespace
from uuid import uuid4

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import auth, data_chat, flowi_routing, llm_adapter, product_semantics, semantic_measure_catalog
from core.paths import PATHS
from routers import data_chat as api, filebrowser as fb, template_report


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    from core import utils
    monkeypatch.setattr(utils, "PATHS", SimpleNamespace(db_root=tmp_path, base_root=tmp_path))
    monkeypatch.setattr(fb, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(PATHS, "data_root", tmp_path / "state")
    monkeypatch.setattr(flowi_routing, "PATHS", SimpleNamespace(db_root=tmp_path))
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODA", "PRODB"])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a, **k: [])
    measure = {"product": "PRODA", "source_type": "INLINE", "term": "PC CD", "step_id": "PC100", "item_id": "CD1", "value_column": "value"}
    monkeypatch.setattr(semantic_measure_catalog, "match_terms", lambda *a, **k: [measure])
    monkeypatch.setattr(fb, "_chart_builder_cache_get", lambda *a: None)
    monkeypatch.setattr(fb, "_chart_builder_cache_put", lambda *a: None)
    rawdir = tmp_path / "1.RAWDATA_DB_INLINE" / "PRODA"
    rawdir.mkdir(parents=True)
    raw = pl.DataFrame({"root_lot_id": ["AZAAA"] * 6, "wafer_id": ["01", "2", "3", "1", "1", "1"],
        "step_id": ["PC100"] * 5 + ["OTHER"], "item_id": ["CD1"] * 6,
        "data_type": ["SITE", "SITE", "SITE", "SUM", "RANGE", "SITE"],
        "subitem_id": ["SHOT01", "SHOT02", "SHOT03", "SUM", "RANGE", "SHOT01"],
        "shot_x": [1, 2, 3, 0, 0, 1], "shot_y": [0] * 6,
        "tkout_time": ["2026-09-01T10:00:00", "2026-09-02T10:00:00", "2026-09-03T10:00:00"] * 2,
        "value": [10., 20., 30., 999., 888., 777.]})
    raw.write_parquet(rawdir / "part.parquet")
    mlpath = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame({"ROOT_LOT_ID": ["AZAAA", "AZAAA", "AZAAA"], "WAFER_ID": [1, 1, 2],
                  "KNOB_AAA_PHOTO": ["A", "A", "B"], "KNOB_AAA_ETCH": ["X", "X", "Y"]}).write_parquet(mlpath)
    from core import ml_table_lookup
    monkeypatch.setattr(ml_table_lookup, "resolve_ml_table_file", lambda product: mlpath if product == "PRODA" else None)
    user = {"username": "shot_qa", "role": "admin"}
    monkeypatch.setattr(auth, "current_user", lambda req: user)
    monkeypatch.setattr(fb, "current_user", lambda req: user)
    monkeypatch.setattr(auth, "effective_permissions", lambda u: {"tabs": []})
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[api.require_flowi_user] = lambda: user
    client = TestClient(app)
    cid = str(uuid4())
    def send(prompt):
        r = client.post("/api/home-agent/orchestrate", json={"prompt": prompt, "conversation_id": cid})
        assert r.status_code == 200, r.text
        return r.json()
    return send, mlpath, user, rawdir, measure


def test_raw_site_trend_split_hitl_history_and_template_replay(scenario):
    send, _, user, _, _ = scenario
    first = send("prodA PC CD 샷별 Trend 보여줘")
    assert first["ok"], first
    assert sorted(p["y"] for p in first["tool"]["chart_result"]["points"]) == [10., 20., 30.]
    assert "SITE/SHOT" in first["interpretation"]["summary"]
    first_id = first["tool"]["saved_chart"]["id"]
    second = send("AAA Split으로 컬러링해줘")
    assert second["tool"]["missing"] == ["split_column"]
    assert "LEFT" not in second["tool"].get("executed_sql", "")
    assert len(fb._chart_builder_history_entries()) == 1
    third = send("KNOB_AAA_PHOTO")
    assert third["ok"], third
    points = third["tool"]["chart_result"]["points"]
    assert len(points) == 3  # duplicate dimension rows never multiply measurements
    colors = {p["y"]: p["color_value"] for p in points}
    assert colors == {10.: "A", 20.: "B", 30.: None}  # unmatched LEFT points survive
    saved_id = third["tool"]["saved_chart"]["id"]
    assert saved_id != first_id
    assert third["tool"]["joins"][0]["how"] == "left"
    assert saved_id in template_report._chart_history()
    saved = template_report._chart_history()[saved_id]
    replay = template_report._chart_request(saved["definition_code"], "", {})
    assert replay["joins"][0]["how"] == "left"
    assert replay["save_history"] is False
    result = fb.chart_builder_run(fb.ChartBuilderRunReq(**replay), None)
    assert result["joined"]["row_count"] == 3
    assert len(fb._chart_builder_history_entries()) == 2


def test_explicit_raw_step_item_works_without_semantic_mapping(scenario, monkeypatch):
    send, _, _, _, _ = scenario
    monkeypatch.setattr(semantic_measure_catalog, "match_terms", lambda *a, **k: [])
    result = send("PRODA PC100 CD1 원본 INLINE 샷별 Trend 보여줘")
    assert result["ok"], result
    assert sorted(point["y"] for point in result["tool"]["chart_result"]["points"]) == [10., 20., 30.]
    assert result["tool"]["query_scope"]["measurement"]["measure"]["selection_source"] == "verified_raw_db"


@pytest.mark.parametrize("title", ["ET 수정 검증", "스플릿 위치 template 10일"])
def test_chart_title_edit_precedes_domain_dispatch_and_saves_revision(scenario, title):
    from core.chart_builder_definition import parse_chart_builder_definition
    send, *_ = scenario
    original = send("PRODA PC CD 샷별 Trend 보여줘")
    edited = send(f'차트 제목을 "{title}"으로 바꿔줘')
    assert edited["ok"], edited
    assert edited["tool"].get("chart_result"), edited
    assert edited["tool"]["chart_result"]["title"] == title
    assert edited["tool"]["chart_result"]["points"] == original["tool"]["chart_result"]["points"]
    entries = fb._chart_builder_history_entries()
    assert len(entries) == 2
    saved_id = edited["tool"]["saved_chart"]["id"]
    assert saved_id != original["tool"]["saved_chart"]["id"]
    saved = next(row for row in entries if row["history_id"] == saved_id)
    assert parse_chart_builder_definition(saved["definition_code"])["chart"]["title"] == title
    assert saved["name"] == title


def test_conflicting_split_values_block_join(scenario):
    send, mlpath, *_ = scenario
    send("PRODA PC CD 샷별 Trend 보여줘")
    pl.DataFrame({"root_lot_id": ["AZAAA", "AZAAA"], "wafer_id": [1, 1], "KNOB_AAA": ["A", "B"]}).write_parquet(mlpath)
    result = send("AAA Split으로 컬러링해줘")
    assert result["ok"] is False
    assert "서로 다른 Split" in result["reply"]
    assert len(fb._chart_builder_history_entries()) == 1


def test_large_ml_dimension_scopes_to_visible_wafers_and_learns(scenario):
    send, mlpath, *_ = scenario
    original = pl.read_parquet(mlpath)
    unrelated = pl.DataFrame({"ROOT_LOT_ID": ["OTHER"] * 6000, "WAFER_ID": list(range(6000)),
        "KNOB_AAA_PHOTO": ["unrelated"] * 6000, "KNOB_AAA_ETCH": ["unrelated"] * 6000})
    pl.concat([unrelated, original]).write_parquet(mlpath)
    send("PRODA PC CD 샷별 Trend 보여줘")
    send("AAA 조건으로 분류해줘")
    chosen = send("KNOB_AAA_PHOTO")
    assert chosen["ok"], chosen
    assert len(chosen["tool"]["chart_result"]["points"]) == 3
    again = send("aaa knob으로 컬러링해줘")
    assert again["tool"]["clarification"]["options"][0]["value"] == "KNOB_AAA_PHOTO"
    assert again["tool"]["clarification"]["options"][0]["selected_count"] == 1
    assert len(fb._chart_builder_history_entries()) == 2


def test_pending_knob_does_not_capture_new_vm_request(scenario, monkeypatch):
    from core import data_chat_inline
    send, mlpath, *_ = scenario
    pl.read_parquet(mlpath).with_columns(pl.lit(42.).alias("VM_CD1")).write_parquet(mlpath)
    monkeypatch.setattr(data_chat_inline, "resolve_ml_table_file", lambda product: mlpath)
    send("PRODA PC CD 샷별 Trend 보여줘")
    send("AAA Split으로 컬러링해줘")
    result = send("PRODA VM CD1 보여줘")
    assert result["tool"]["missing"] == ["inline_measure"]
    assert "pending_inline_chart" not in result["context"]
    selected = send("1")
    assert selected["ok"]
    assert selected["tool"]["action"] == "vm.values"
    assert "inline_chart_query" not in selected["context"]


def test_no_chart_permission_no_execution(scenario):
    send, _, user, *_ = scenario
    user["role"] = "user"
    result = send("PRODA PC CD 샷별 Trend 보여줘")
    assert result["tool"]["blocked"]
    assert not fb._chart_builder_history_entries()


def test_unknown_semantic_is_not_substituted_with_averages(scenario):
    send, _, _, _, measure = scenario
    measure["item_id"] = "MISSING"
    result = send("PRODA PC CD 샷별 Trend 보여줘")
    assert result["ok"] is False
    assert not result["tool"].get("chart_result")
    assert not fb._chart_builder_history_entries()


def test_shot_identifier_without_type_column(scenario):
    send, _, _, rawdir, _ = scenario
    p = rawdir / "part.parquet"
    pl.read_parquet(p).drop("data_type").write_parquet(p)
    result = send("PRODA PC CD 샷별 Trend 보여줘")
    assert result["ok"], result
    assert result["tool"]["query_scope"]["site_filter"]["column"] == "subitem_id"
    assert len(result["tool"]["chart_result"]["points"]) == 3
