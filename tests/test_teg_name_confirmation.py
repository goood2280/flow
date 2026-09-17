import pytest

from core import data_chat, data_chat_teg as teg


@pytest.fixture
def answers(monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_A"])
    monkeypatch.setattr(teg.teg_map, "product_catalog", lambda: [{"vehicle": "REAL_A"}])
    monkeypatch.setattr(teg.teg_map, "map_payload", lambda product: {
        "vehicle": product, "tegs": [{"teg": "H_HOL10", "teg_src": "HOL10", "ebeam_x": 1.0, "ebeam_y": 2.0, "teg_w": .2, "teg_h": .4}],
        "geometry": {},
    })
    monkeypatch.setattr(teg.teg_map, "teg_radius_table", lambda product, name: {
        "teg": name, "rows": [{"shot_x": 0, "shot_y": 0, "abs_x": 1, "abs_y": 2, "radius": 2.2}]})


def test_hol10_asks_before_prefixed_answer_is_used(answers):
    first = data_chat.execute("REAL_A HOL10 TEG 좌표 보여줘", {}, None)
    assert not first["ok"]
    assert first["tool"]["teg_candidates"] == [{"requested": "HOL10", "candidates": ["H_HOL10"]}]
    assert first["tool"]["missing"] == ["teg"]
    confirmed = data_chat.execute("H_HOL10", first["context"], None)
    assert confirmed["ok"]
    assert confirmed["tool"]["action"] == "teg.coordinates"
    assert confirmed["context"]["teg_names"] == ["H_HOL10"]
    assert "pending_teg_selection" not in confirmed["context"]


def test_short_name_followup_does_not_reuse_old_teg(answers):
    context = {"product": "REAL_A", "confirmed_product": "REAL_A", "teg_product": "REAL_A", "last_action": "teg.locations", "teg_names": ["OLD_NAME"]}
    first = data_chat.execute("HOL10 보여줘", context, None)
    assert first["tool"]["teg_candidates"][0]["candidates"] == ["H_HOL10"]
    confirmed = data_chat.execute("네", first["context"], None)
    assert confirmed["context"]["teg_names"] == ["H_HOL10"]


def test_multiple_similar_candidates_require_exact_choice(answers, monkeypatch):
    monkeypatch.setattr(teg.teg_map, "map_payload", lambda product: {
        "vehicle": product, "tegs": [{"teg": "H_HOL10"}, {"teg": "V_HOL10"}], "geometry": {}})
    first = data_chat.execute("REAL_A HOL10 TEG 위치", {}, None)
    assert set(first["tool"]["teg_candidates"][0]["candidates"]) == {"H_HOL10", "V_HOL10"}
    again = data_chat.execute("네", first["context"], None)
    assert not again["ok"] and again["tool"]["missing"] == ["teg"]
    selected = data_chat.execute("V_HOL10", again["context"], None)
    assert selected["ok"] and selected["context"]["teg_names"] == ["V_HOL10"]


def test_candidate_disappearing_does_not_query_stale_answer(answers, monkeypatch):
    first = data_chat.execute("REAL_A HOL10 TEG 위치", {}, None)
    monkeypatch.setattr(teg.teg_map, "map_payload", lambda product: {"vehicle": product, "tegs": [], "geometry": {}})
    second = data_chat.execute("H_HOL10", first["context"], None)
    assert not second["ok"] and second["tool"]["missing"] == ["teg"]


def test_cancel_teg_confirmation(answers):
    first = data_chat.execute("REAL_A HOL10 TEG 위치", {}, None)
    second = data_chat.execute("취소", first["context"], None)
    assert "pending_teg_selection" not in second["context"]


def geometry():
    return {"geometry": {"fit": "radius", "wafer_radius_mm": 150, "shot_w_mm": 26, "shot_h_mm": 32},
            "shots": [{"x": 1, "y": -2, "mm_x": 26, "mm_y": -64}],
            "tegs": [{"teg": "H_HOL10", "ebeam_x": 1.2, "ebeam_y": -2.1, "teg_w": .2, "teg_h": .4}]}


def test_maps_use_actual_geometry_and_cartesian_y():
    maps = teg._teg_maps_payload("REAL_A", ["H_HOL10"], geometry())
    assert maps["available"]
    assert maps["wafer"]["shots"][0]["center_y_mm"] == 64
    assert maps["within_shot"]["x_min_mm"] == -13
    assert maps["within_shot"]["tegs"][0]["y_mm"] == -2.1
    assert maps["within_shot"]["tegs"][0]["width_mm"] == .2


def test_missing_teg_size_is_a_point_without_fabricated_rectangle():
    data = geometry(); data["tegs"][0].pop("teg_w")
    maps = teg._teg_maps_payload("REAL_A", ["H_HOL10"], data)
    assert maps["available"]
    assert not maps["within_shot"]["tegs"][0]["geometry_available"]
    assert maps["within_shot"]["tegs"][0]["width_mm"] is None


def test_missing_wafer_geometry_retains_coordinates(answers):
    result = data_chat.execute("REAL_A H_HOL10 TEG 좌표", {}, None)
    assert result["ok"] and result["tool"]["table"]["rows"]
    assert result["tool"]["teg_maps"]["available"] is False


def test_visual_payload_bounded():
    data = geometry(); data["shots"] *= teg.MAX_VISUAL_SHOTS + 1
    assert not teg._teg_maps_payload("REAL_A", ["H_HOL10"], data)["available"]
