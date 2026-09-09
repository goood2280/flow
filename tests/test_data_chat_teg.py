from types import SimpleNamespace

import pytest

from core import data_chat_teg


TEGS = [
    {"teg": "TEG_A", "teg_src": "TEG_A", "ebeam_x": 1.25, "ebeam_y": -2.5,
     "teg_w": 0.2, "teg_h": 0.4, "flat_zone": "h"},
    {"teg": "TEG_B", "teg_src": "TEG_B", "ebeam_x": 3.5, "ebeam_y": 4.75,
     "teg_w": 0.3, "teg_h": 0.6, "flat_zone": "v_R"},
]


@pytest.fixture
def teg_sources(monkeypatch):
    monkeypatch.setattr(data_chat_teg.teg_map, "product_catalog", lambda: [
        {"vehicle": "PRODA"}, {"vehicle": "PRODB"},
    ])
    monkeypatch.setattr(data_chat_teg.teg_map, "map_payload", lambda product: {
        "vehicle": product, "tegs": TEGS, "geometry": {"fit": "radius"},
    })


def test_location_then_coordinate_followup_preserves_product_and_tegs(monkeypatch, teg_sources):
    calls = []

    def radius(product, teg):
        calls.append((product, teg))
        return {
            "vehicle": product,
            "teg": teg,
            "rows": [{"shot_x": 10, "shot_y": 20, "abs_x": 11.1 if teg == "TEG_A" else 13.3,
                      "abs_y": -4.2, "radius": 12.34}],
        }

    monkeypatch.setattr(data_chat_teg.teg_map, "teg_radius_table", radius)

    first = data_chat_teg.dispatch("PRODA TEG_A TEG_B 위치 어디야?", {})
    assert first["ok"] is True
    assert first["tool"]["action"] == "teg.locations"
    assert first["tool"]["table"]["rows"] == [
        {"product": "PRODA", "teg": "TEG_A", "ebeam_x": 1.25, "ebeam_y": -2.5,
         "teg_w": 0.2, "teg_h": 0.4, "direction": "h"},
        {"product": "PRODA", "teg": "TEG_B", "ebeam_x": 3.5, "ebeam_y": 4.75,
         "teg_w": 0.3, "teg_h": 0.6, "direction": "v_R"},
    ]
    assert first["context"]["product"] == "PRODA"
    assert first["context"]["teg_names"] == ["TEG_A", "TEG_B"]

    second = data_chat_teg.dispatch("좌표가 어떻게돼?", first["context"])
    assert calls == [("PRODA", "TEG_A"), ("PRODA", "TEG_B")]
    assert second["tool"]["action"] == "teg.coordinates"
    assert second["tool"]["table"]["columns"] == [
        "product", "teg", "shot_x", "shot_y", "abs_x", "abs_y", "radius",
    ]
    assert second["tool"]["table"]["rows"][0]["abs_x"] == 11.1
    assert second["tool"]["table"]["rows"][1]["teg"] == "TEG_B"
    assert "Chip_Radius + Teg_location" in second["tool"]["sources"][0]


def test_plain_wip_location_is_not_claimed(teg_sources):
    assert data_chat_teg.dispatch("긴 일반 질문 " * 100, {}) is None
    assert data_chat_teg.dispatch("AZ11A.1 지금 위치 어디야?", {}) is None
    assert data_chat_teg.dispatch(
        "AZ11A.1 지금 위치 어디야?",
        {"product": "PRODA", "teg_names": ["TEG_A"], "last_action": "teg.locations"},
    ) is None


def test_public_orchestrator_keeps_followup_and_routes_model_tools(monkeypatch, teg_sources):
    from core import data_chat, llm_adapter
    from routers import splittable
    monkeypatch.setattr(data_chat_teg.teg_map, "teg_radius_table", lambda product, name: {
        "teg": name, "rows": [{"shot_x": 0, "shot_y": 0, "abs_x": 1, "abs_y": 2, "radius": 2.236}]})
    first = data_chat.execute("PRODA TEG_A TEG_B 위치 어디야?", {}, None)
    second = data_chat.execute("좌표가 어떻게돼?", first["context"], None)
    assert second["context"]["teg_names"] == ["TEG_A", "TEG_B"]
    assert len(second["tool"]["table"]["rows"]) == 2
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": []})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    calls = []
    def plan(prompt, **kwargs):
        calls.append(prompt)
        assert "teg.coordinates" in prompt
        return {"ok": True, "obj": {"action": "teg.coordinates", "params": {"product": "PRODA", "tegs": ["TEG_B"]}}}
    monkeypatch.setattr(llm_adapter, "complete_json", plan)
    result = data_chat.execute("측정 구조의 절대 지점을 알려줘", {}, None)
    assert result["ok"] and result["context"]["teg_names"] == ["TEG_B"]
    assert len(calls) == 1


def test_unknown_product_does_not_fall_back_to_previous(teg_sources):
    result = data_chat_teg.dispatch("PRODX TEG_A 위치", {"product": "PRODA", "teg_names": ["TEG_A"]})
    assert not result["ok"]


def test_unknown_teg_is_explicit_and_never_calls_coordinate_api(monkeypatch, teg_sources):
    monkeypatch.setattr(
        data_chat_teg.teg_map,
        "teg_radius_table",
        lambda *args: pytest.fail("unknown TEG must not reach the coordinate source"),
    )
    out = data_chat_teg.dispatch("PRODA TEG_MISSING 좌표 알려줘", {})
    assert out["ok"] is False
    assert out["tool"]["missing"] == ["teg"]
    assert "TEG_MISSING" in out["reply"]
    assert "table" not in out["tool"]


def test_multiple_products_are_ambiguous(teg_sources):
    out = data_chat_teg.dispatch("PRODA PRODB TEG_A 위치", {})
    assert out["ok"] is False
    assert out["tool"]["missing"] == ["product"]
    assert out["tool"]["table"]["rows"] == [{"product": "PRODA"}, {"product": "PRODB"}]


def test_duplicate_source_teg_requires_numbered_display_name(monkeypatch, teg_sources):
    duplicate_rows = [
        {**TEGS[0], "teg": "TEG_A_1", "teg_src": "TEG_A"},
        {**TEGS[0], "teg": "TEG_A_2", "teg_src": "TEG_A", "ebeam_x": 9.0},
    ]
    monkeypatch.setattr(data_chat_teg.teg_map, "map_payload", lambda product: {
        "vehicle": product, "tegs": duplicate_rows, "geometry": {"fit": "radius"},
    })
    out = data_chat_teg.dispatch("PRODA TEG_A 위치", {})
    assert out["ok"] is False
    assert out["tool"]["table"]["rows"] == [{"teg": "TEG_A_1"}, {"teg": "TEG_A_2"}]


def test_explicit_execute_is_allowlisted_and_uses_context_product(monkeypatch, teg_sources):
    monkeypatch.setattr(data_chat_teg.teg_map, "teg_radius_table", lambda product, teg: {
        "vehicle": product, "teg": teg, "rows": [],
    })
    out = data_chat_teg.execute("teg.coordinates", {"tegs": ["TEG_A"]}, {"product": "PRODA"})
    assert out["ok"] is True
    assert out["context"]["last_action"] == "teg.coordinates"
    with pytest.raises(ValueError, match="unsupported parameters"):
        data_chat_teg.execute("teg.coordinates", {"tegs": ["TEG_A"], "force": True}, {"product": "PRODA"})
    with pytest.raises(ValueError, match="unsupported TEG action"):
        data_chat_teg.execute("teg.write", {}, {"product": "PRODA"})


def test_visible_catalog_is_used_when_request_is_present(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "u", "role": "user"}))
    monkeypatch.setattr(data_chat_teg.teg_map, "visible_product_catalog", lambda user: [{"vehicle": "PRODA"}])
    monkeypatch.setattr(data_chat_teg.teg_map, "product_catalog", lambda: pytest.fail("must use visible catalog"))
    monkeypatch.setattr(data_chat_teg.teg_map, "map_payload", lambda product: {
        "vehicle": product, "tegs": TEGS, "geometry": {"fit": "radius"},
    })
    assert data_chat_teg.dispatch("PRODA TEG_A 위치", {}, request)["ok"] is True


def test_mapfile_results_are_summary_only_and_bounded(monkeypatch, teg_sources):
    from core import mapfile_traffic

    files = [{
        "filename": f"P_{index}.txt", "status": "ok", "traffic_light": "green",
        "sl": {"light": "green"}, "main": {"light": "green"}, "issues": [],
        "verified_at": "2026-09-10 10:00:00", "is_cached": True,
        "content": "must not leak into chat",
    } for index in range(data_chat_teg.MAX_MAPFILE_ROWS + 1)]
    monkeypatch.setattr(mapfile_traffic, "inspect_mapfiles_for_product", lambda product, force=False: {
        "files": files, "summary": {"total_files": len(files)},
    })
    out = data_chat_teg.dispatch("PRODA 맵파일 결과 보여줘", {})
    assert out["tool"]["action"] == "teg.mapfiles"
    assert len(out["tool"]["table"]["rows"]) == data_chat_teg.MAX_MAPFILE_ROWS
    assert out["tool"]["table"]["total"] == len(files)
    assert "content" not in out["tool"]["table"]["rows"][0]
    assert out["tool"]["warnings"]
