"""Inline desc 별칭 + 값 조회 + Radius plot + flow-data 매칭 스토어 검증."""
import json

import pytest

from core import home_orchestrator, inline_alias, matching_store
from core.paths import PATHS


@pytest.fixture
def tmp_roots(monkeypatch, tmp_path):
    """data_root 만 tmp 로 (실측 db_root 유지). 매칭 seed 복사는 tmp 로만."""
    data = tmp_path / "flow-data"
    db = tmp_path / "Fab"
    db.mkdir(parents=True)
    monkeypatch.setattr(PATHS, "data_root", data)
    import core.paths as paths_mod
    monkeypatch.setattr(paths_mod, "_get_db_root", lambda: db)
    monkeypatch.setattr(paths_mod, "_get_base_root", lambda: db)
    return data, db


@pytest.fixture
def tmp_data(monkeypatch, tmp_path):
    """flow-data 만 tmp 로. DB 의존 테스트용 (seed 복사는 tmp 로만)."""
    data = tmp_path / "flow-data"
    monkeypatch.setattr(PATHS, "data_root", data)
    return data


@pytest.fixture
def no_side_effects(monkeypatch):
    monkeypatch.setattr(home_orchestrator, "_react_loop_enabled", lambda: False)
    monkeypatch.setattr(home_orchestrator, "_llm_planner_enabled", lambda: False)
    monkeypatch.setattr(
        home_orchestrator, "build_home_runtime_snapshot",
        lambda **kwargs: {"run_id": "test", "graph": {}, "action_log": {}, "status": "ok"},
    )
    monkeypatch.setattr(home_orchestrator.home_memory, "remember_turn", lambda **kwargs: None)


def test_matching_store_seed_copy_and_save(tmp_roots):
    data, db = tmp_roots
    legacy = db / "Inline_matching.csv"
    legacy.write_text("product,step_id,item_id,item_desc,step_desc\nPRODA,AA1,I1,desc,sd\n", encoding="utf-8-sig")
    resolved = matching_store.resolve("Inline_matching.csv")
    assert resolved == data / "matching" / "Inline_matching.csv"
    assert resolved.is_file()
    rows, path = matching_store.read_csv_rows("Inline_matching.csv")
    assert path == resolved and rows[0]["item_id"] == "I1"
    matching_store.save_csv_rows("Inline_matching.csv", [{"product": "P", "step_id": "S", "item_id": "I", "item_desc": "d", "step_desc": ""}],
                                 ["product", "step_id", "item_id", "item_desc", "step_desc"])
    rows, _ = matching_store.read_csv_rows("Inline_matching.csv")
    assert len(rows) == 1 and rows[0]["product"] == "P"
    # 레거시는 그대로 (seed 복사만)
    assert "PRODA" in legacy.read_text(encoding="utf-8-sig")


def test_canonical_never_auto_overwritten(tmp_roots):
    data, db = tmp_roots
    (db / "Inline_matching.csv").write_text(
        "product,step_id,item_id\nLEGACY,S,I\n", encoding="utf-8-sig")
    admin = data / "matching" / "Inline_matching.csv"
    admin.parent.mkdir(parents=True, exist_ok=True)
    admin.write_text("product,step_id,item_id\nADMIN,S,I\n", encoding="utf-8-sig")
    rows, path = matching_store.read_csv_rows("Inline_matching.csv")
    assert path == admin
    assert rows[0]["product"] == "ADMIN"
    assert "LEGACY" in (db / "Inline_matching.csv").read_text(encoding="utf-8-sig")


def test_desc_alias_crud_and_backup(tmp_data):
    data = tmp_data
    entry = inline_alias.save_desc_alias("PRODA", "PC BCD", "AA100070", "5.0 PC", "tester")
    assert entry["desc"] == "PC BCD"
    rows = inline_alias.list_desc_aliases("PRODA")
    assert len(rows) == 1
    assert rows[0]["step_id"] == "AA100070"
    backup = data / "product_wiki" / "aliases_export.json"
    assert backup.is_file()
    doc = json.loads(backup.read_text(encoding="utf-8"))
    assert doc["desc_aliases"][0]["item_id"] == "5.0 PC"
    assert inline_alias.delete_desc_alias("PRODA", "PC BCD") == 1
    assert inline_alias.list_desc_aliases("PRODA") == []


def test_resolve_candidates_from_alias_and_matching(tmp_data):
    inline_alias.save_desc_alias("PRODA", "PC BCD", "AA100070", "5.0 PC", "tester")
    cands = inline_alias.resolve_candidates("PRODA", "PRODA A1021 PC BCD 보여줘")
    assert (cands[0]["step_id"], cands[0]["item_id"]) == ("AA100070", "5.0 PC")
    assert cands[0]["via"] == "desc_alias"


def test_query_values_real_db():
    result = inline_alias.query_values("PRODA", "A1021", "AA100070", "5.0 PC")
    assert result["ok"] is True
    assert result["total"] > 0
    assert result["avg_rows"], "wafer별 avg가 비어 있음"
    assert set(result["avg_rows"][0]) == {"wafer_id", "n", "avg"}
    assert result["filters"]["step_id"] == "AA100070"


def test_radius_panels_real_db(tmp_data):
    matching_store.save_json_doc("inline_map_settings.json", {"version": 1, "tables": [{
        "table_name": "T", "vehicle": "VH_PRODA",
        "shots": [{"shot_x": float(i), "shot_y": 1.0, "subitem_id": f"SHOT{i:02d}"} for i in range(1, 31)]}]})
    matching_store.save_csv_rows("Chip_Radius.csv",
        [{"Mask": "VH_PRODA", "chip_x_adj": str(float(i)), "chip_y_adj": "1.0", "Chip_Radius": str(100.0 + i)}
         for i in range(1, 31)], ["Mask", "chip_x_adj", "chip_y_adj", "Chip_Radius"])
    result = inline_alias.radius_panels("PRODA", "A1021", "AA100070", "5.0 PC", "T")
    assert result["ok"] is True
    assert result["panels"], "wafer 패널이 비어 있음"
    panel = result["panels"][0]
    assert panel["chart"]["cubic_fit"] is True
    points = panel["chart"]["points"]
    assert points and set(points[0]) >= {"x", "y"}
    assert all(isinstance(p["x"], (int, float)) and isinstance(p["y"], (int, float)) for p in points)


def _seed_inline_stubs():
    matching_store.save_json_doc("inline_map_settings.json", {"version": 1, "tables": [{
        "table_name": "T", "vehicle": "VH_PRODA",
        "shots": [{"shot_x": float(i), "shot_y": 1.0, "subitem_id": f"SHOT{i:02d}"} for i in range(1, 31)]}]})
    matching_store.save_csv_rows("inline_shot_matching.csv",
        [{"product": "PRODA", "step_id": "AA100070", "item_id": "5.0 PC", "map_name": "T"}],
        ["product", "step_id", "item_id", "map_name"])
    matching_store.save_csv_rows("Chip_Radius.csv",
        [{"Mask": "VH_PRODA", "chip_x_adj": str(float(i)), "chip_y_adj": "1.0", "Chip_Radius": str(100.0 + i)}
         for i in range(1, 31)], ["Mask", "chip_x_adj", "chip_y_adj", "Chip_Radius"])
    inline_alias.save_desc_alias("PRODA", "PC BCD", "AA100070", "5.0 PC", "tester")


def test_home_inline_values_fastpath(tmp_data, no_side_effects):
    _seed_inline_stubs()
    out = home_orchestrator.orchestrate("PRODA A1021 PC BCD wafer별 avg 보여줘",
                                        user={"username": "t", "role": "admin"})
    assert out["meta"]["planner"] == "fastpath:inline_values"
    tool = out["tool"]
    assert tool["feature"] == "inline"
    assert tool["table"]["kind"] == "inline_wafer_avg"
    assert tool["table"]["total"] >= 1


def test_home_radius_map_hitl_then_plot(tmp_data, no_side_effects):
    _seed_inline_stubs()
    out = home_orchestrator.orchestrate("PRODA A1021 PC BCD Radius plot 그려줘",
                                        user={"username": "t", "role": "admin"})
    assert out["meta"]["planner"] == "fastpath:inline_radius_plot_ask"
    clar = out["tool"]["clarification"]
    assert clar["kind"] == "inline_map"
    assert clar["options"] and clar["options"][0]["label"] == "T"
    followup = clar["options"][0]["value"]
    out2 = home_orchestrator.orchestrate(followup, user={"username": "t", "role": "admin"})
    assert out2["meta"]["planner"] == "fastpath:inline_radius_plot"
    tool = out2["tool"]
    assert tool["chart_panels"], "trellis 패널이 비어 있음"
    assert tool["chart_panels"][0]["chart"]["cubic_fit"] is True


def test_home_inline_candidate_hitl(tmp_data, no_side_effects, monkeypatch):
    _seed_inline_stubs()
    inline_alias.save_desc_alias("PRODA", "PC BCD", "AA100200", "9.0 SILICIDE", "tester")
    out = home_orchestrator.orchestrate("PRODA A1021 PC BCD 값 보여줘",
                                        user={"username": "t", "role": "admin"})
    assert out["meta"]["planner"] == "fastpath:inline_values_ask"
    assert out["tool"]["clarification"]["kind"] == "inline_candidate"
    assert len(out["tool"]["clarification"]["options"]) >= 2
