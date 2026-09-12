import pytest
from core import data_chat, data_chat_features
from routers import yield_map, tracker, watchlist, informs, splittable


def test_new_actions_in_schema():
    expected = ["yield_map.map", "tracker.issues", "tracker.issue", "watchlist.lots", "informs.recent", "informs.by_lot"]
    for act in expected:
        assert act in data_chat_features.ACTIONS
        assert data_chat_features.ACTIONS[act]["description"]
        assert data_chat_features.ACTIONS[act]["parameters"]["type"] == "object"


def test_yield_map_execution(monkeypatch):
    request = object()
    calls = []

    def fake_get_map(product, kind, root_lot_id, lot_id, wafer_id, bin_name, user):
        calls.append((product, kind, root_lot_id, lot_id, wafer_id, bin_name))
        return {
            "ok": True,
            "product": product,
            "wafers": [
                {"wafer_id": "1", "bin": "1", "yield": 98.5, "total_dies": 500},
                {"wafer_id": "2", "bin": "1", "yield": 97.2, "total_dies": 500},
            ],
            "bins": [{"bin_id": "1", "count": 980, "ratio": 0.98}],
        }

    monkeypatch.setattr(yield_map, "get_map", fake_get_map)
    res = data_chat_features.execute_feature(
        "yield_map.map",
        {"product": "PRODA", "root_lot_id": "A1001", "wafer_id": "1", "bin_name": "1"},
        request,
    )
    assert res["feature"] == "yield_map"
    assert len(res["table"]["rows"]) == 2
    assert res["table"]["rows"][0]["wafer_id"] == "1"
    assert res["context"]["product"] == "PRODA"
    assert calls == [("PRODA", "yield", "A1001", "", "1", "1")]


def test_tracker_issues_execution(monkeypatch):
    request = object()

    def fake_list_issues(request, status, limit):
        return {
            "issues": [
                {"id": "ISS-1", "title": "ET Outlier", "status": "open", "priority": "high", "category": "Analysis", "root_lot_ids": ["A1001"], "summary": "PRODA ET test"},
                {"id": "ISS-2", "title": "Normal Check", "status": "closed", "priority": "normal", "category": "Monitor", "root_lot_ids": ["A1002"], "summary": "PRODB check"},
            ]
        }

    monkeypatch.setattr(tracker, "list_issues", fake_list_issues)
    res = data_chat_features.execute_feature(
        "tracker.issues",
        {"product": "PRODA", "status": "open"},
        request,
    )
    assert res["feature"] == "tracker"
    assert len(res["table"]["rows"]) == 1
    assert res["table"]["rows"][0]["id"] == "ISS-1"


def test_watchlist_lots_execution(monkeypatch):
    request = object()

    def fake_list_lots(request):
        return {"ok": True, "username": "admin", "lots": ["A1001", "A1005"]}

    monkeypatch.setattr(watchlist, "list_watchlist_lots", fake_list_lots)
    res = data_chat_features.execute_feature("watchlist.lots", {}, request)
    assert res["feature"] == "watchlist"
    assert len(res["table"]["rows"]) == 2
    assert res["context"]["watched_lots"] == ["A1001", "A1005"]


def test_informs_recent_and_by_lot_execution(monkeypatch):
    request = object()

    def fake_recent(request, limit):
        return {
            "informs": [
                {"id": "INF-1", "product": "PRODA", "lot_id": "A1001.1", "module": "ETCH", "reason": "Test", "author": "admin", "created_at": "2026-09-12", "flow_status": "registered"},
            ]
        }

    def fake_by_lot(request, lot_id):
        return {
            "informs": [
                {"id": "INF-1", "product": "PRODA", "lot_id": lot_id, "module": "ETCH", "reason": "Test", "author": "admin", "created_at": "2026-09-12", "flow_status": "registered"},
            ]
        }

    monkeypatch.setattr(informs, "recent_roots", fake_recent)
    monkeypatch.setattr(informs, "by_lot", fake_by_lot)

    res_recent = data_chat_features.execute_feature("informs.recent", {"product": "PRODA"}, request)
    assert res_recent["feature"] == "informs"
    assert len(res_recent["table"]["rows"]) == 1

    res_lot = data_chat_features.execute_feature("informs.by_lot", {"lot_id": "A1001.1"}, request)
    assert res_lot["feature"] == "informs"
    assert res_lot["table"]["rows"][0]["lot_id"] == "A1001.1"


def test_data_chat_intent_and_guide_routing(monkeypatch):
    request = object()
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "PRODA"}, {"name": "PRODB"}]})
    monkeypatch.setattr(yield_map, "get_map", lambda **kw: {"ok": True, "wafers": [{"wafer_id": "1", "yield": 99.1}]})
    monkeypatch.setattr(watchlist, "list_watchlist_lots", lambda request: {"ok": True, "username": "admin", "lots": ["A1001"]})
    monkeypatch.setattr(tracker, "list_issues", lambda request, status, limit: {"issues": [{"id": "ISS-1", "title": "Test"}]})
    monkeypatch.setattr(informs, "by_lot", lambda request, lot_id: {"informs": [{"id": "INF-1", "lot_id": lot_id}]})

    # 1. Yield Map intent
    out_ym = data_chat.execute("PRODA A1001 수율 맵 보여줘", {}, request)
    assert out_ym["ok"] is True
    assert "[도메인 해석 가이드]" in out_ym["reply"]
    assert "Yield Map" in out_ym["reply"]
    assert out_ym["tool"]["feature"] == "yield_map"
    assert out_ym["context"]["last_feature"] == "yield_map"

    # 2. Watchlist intent
    out_wl = data_chat.execute("내 관심 랏 보여줘", {}, request)
    assert out_wl["ok"] is True
    assert "관심 랏" in out_wl["reply"]
    assert out_wl["tool"]["feature"] == "watchlist"
    assert out_wl["context"]["last_feature"] == "watchlist"

    # 3. Tracker intent
    out_trk = data_chat.execute("ET 트래커 이슈 목록 보여줘", {}, request)
    assert out_trk["ok"] is True
    assert "트래커" in out_trk["reply"]
    assert out_trk["tool"]["feature"] == "tracker"

    # 4. Informs by lot intent
    out_inf = data_chat.execute("PRODA A1001 인폼 내역 조회", {}, request)
    assert out_inf["ok"] is True
    assert "인폼" in out_inf["reply"]
    assert out_inf["tool"]["feature"] == "informs"


def test_execution_trace_and_compact_prose(monkeypatch):
    request = object()
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "PRODA"}]})
    monkeypatch.setattr(yield_map, "get_map", lambda **kw: {"ok": True, "wafers": [{"wafer_id": "1", "yield": 99.1}]})

    out = data_chat.execute("PRODA A1001 수율 맵 보여줘", {}, request)
    assert out["ok"] is True
    tool = out["tool"]
    assert "execution_trace" in tool
    trace = tool["execution_trace"]
    assert trace["intent"]
    assert len(trace["sources"]) > 0
    assert len(trace["steps"]) > 0
    assert trace["query"]
    assert "수율 맵" in trace["action"]

    # Compact prose verification
    long_prose = "총 120개 랏이 검색되었습니다:\n1. A1001 - STEP_01\n2. A1002 - STEP_02\n3. A1003 - STEP_03\n4. A1004 - STEP_04\n5. A1005 - STEP_05\n6. A1006 - STEP_06"
    compacted = data_chat.compact_text_prose(long_prose)
    assert "1. A1001" not in compacted
    assert "추출 데이터셋 및 라이브 작업창" in compacted


def test_teg_related_and_trace(monkeypatch):
    import types
    request = types.SimpleNamespace(state=types.SimpleNamespace(user={"username": "admin", "role": "admin"}))
    from core import teg_map, data_chat_teg

    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "PRODA"}]})
    monkeypatch.setattr(data_chat_teg, "_catalog", lambda req: [{"vehicle": "PRODA"}])
    fake_payload = {
        "vehicle": "VH_PRODA",
        "tegs": [
            {"teg": "TEG_GATE", "ebeam_x": 1.2, "ebeam_y": 3.4},
            {"teg": "TEG_CONTACT", "ebeam_x": 2.2, "ebeam_y": 4.4},
            {"teg": "TEG_VIA", "ebeam_x": 3.2, "ebeam_y": 5.4},
        ],
    }
    monkeypatch.setattr(teg_map, "map_payload", lambda p: fake_payload)

    out = data_chat.execute("PRODA TEG_GATE 위치 보여줘", {}, request)
    assert out["ok"] is True
    tool = out["tool"]
    assert "execution_trace" in tool
    assert tool["execution_trace"]["action"] == "TEG 위치 조회"
    assert "related_tegs" in tool
    assert "TEG_CONTACT" in tool["related_tegs"]

