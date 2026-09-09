from core import data_chat, llm_adapter, data_chat_features
from routers import filebrowser, splittable


def offline(monkeypatch):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "PRODUCTA0"}]})


def test_inline_chart_then_font_preserves_points_without_query(monkeypatch):
    offline(monkeypatch)
    monkeypatch.setattr(filebrowser, "chart_builder_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("reran data")))
    context = {"table": {"rows": [{"lot": "AZ11", "qty": 12}, {"lot": "AZ12", "qty": 20}]}}
    created = data_chat.execute("x축 lot y축 qty 막대 차트 그려줘", context, None)
    assert created["tool"]["chart_result"]["points"] == [{"x": "AZ11", "y": 12}, {"x": "AZ12", "y": 20}]
    edited = data_chat.execute("x축 폰트 20으로 키워줘", created["context"], None)
    assert edited["tool"]["chart_result"]["x_font_size"] == 20
    assert edited["tool"]["chart_result"]["points"] == created["tool"]["chart_result"]["points"]
    assert "x_font_size" not in created["context"]["chart_result"]


def test_feature_followup_remembers_product(monkeypatch):
    offline(monkeypatch)
    calls = []
    def run(action, params, request):
        calls.append((action, params))
        return {"feature": "lot_management", "table": {"rows": [{"lot": "AZ11"}]}}
    monkeypatch.setattr(data_chat_features, "execute_feature", run)
    first = data_chat.execute("prod0 랏관리 보여줘", {}, None)
    data_chat.execute("다시 보여줘", first["context"], None)
    assert calls == [("lot_management.table", {"product": "PRODUCTA0"})] * 2


def test_dashboard_list_selection_uses_actual_id(monkeypatch):
    offline(monkeypatch)
    calls = []
    def run(action, params, request):
        calls.append((action, params))
        return {"feature": "dashboard", "chart_result": {"chart_type": "line", "points": [{"x": 1, "y": 2}]}}
    monkeypatch.setattr(data_chat_features, "execute_feature", run)
    context = {"last_action": "dashboard.charts", "table": {"rows": [{"id": "safe-id", "title": "수율"}]}}
    out = data_chat.execute("1번 차트 보여줘", context, None)
    assert calls == [("dashboard.chart_data", {"chart_id": "safe-id"})]
    assert out["tool"]["chart_result"]["points"]


def test_split_missing_lot_keeps_selected_product(monkeypatch):
    offline(monkeypatch)
    out = data_chat.execute("prod0 스플릿테이블 보여줘", {}, None)
    assert out["context"]["product"] == "PRODUCTA0"
    assert out["context"]["last_action"] == "splittable"
    assert out["tool"]["missing"] == ["lot"]
