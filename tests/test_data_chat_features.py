import pytest

from core import data_chat_features
from routers import dashboard, lot_management, lot_progress, lot_requests


def test_actions_are_closed_object_schemas():
    assert "lot_management.table" in data_chat_features.ACTIONS
    assert "dashboard.chart_data" in data_chat_features.ACTIONS
    for action in data_chat_features.ACTIONS.values():
        assert action["description"]
        assert action["parameters"]["type"] == "object"
        assert action["parameters"]["additionalProperties"] is False


def test_lot_management_table_forwards_request_and_flattens_rows(monkeypatch):
    request = object()
    calls = []

    def get_table(*, request, product, include_status):
        calls.append((request, product, include_status))
        return {
            "product": product,
            "version": 7,
            "columns": [{"id": "lot_id", "label": "Lot"}, {"id": "qty", "label": "Qty"}],
            "rows": [
                {"id": "1", "values": {"lot_id": "AZ11", "qty": {"actual": 23}}},
                {"id": "2", "values": {"lot_id": "OTHER", "qty": 5}},
            ],
        }

    monkeypatch.setattr(lot_management, "get_table", get_table)
    result = data_chat_features.execute_feature(
        "lot_management.table",
        {"product": "PROD0", "lot_id": "az11", "include_status": False},
        request,
    )

    assert calls == [(request, "PROD0", False)]
    assert result["table"] == {
        "rows": [{"product": "PROD0", "lot_id": "AZ11", "qty": 23}],
        "columns": ["product", "lot_id", "qty"],
        "total": 1,
    }
    assert result["context"] == {"product": "PROD0", "lot_management_version": 7, "lot_id": "az11"}


def test_lot_management_my_lots_and_status_use_real_route_contracts(monkeypatch):
    request = object()
    calls = []
    monkeypatch.setattr(
        lot_management,
        "get_my_lots",
        lambda *, request: {
            "rows": [{"product": "PROD0", "values": {"lot_id": "AZ11", "current_step_id": "ST10"}}],
            "total": 1,
            "watched_lots": ["AZ11"],
        },
    )

    def get_status(*, request, product, lot_id):
        calls.append((request, product, lot_id))
        return {"product": product, "lot_id": lot_id, "current_step_id": "ST10", "qty": 20}

    monkeypatch.setattr(lot_management, "get_lot_status", get_status)
    mine = data_chat_features.execute_feature("lot_management.my_lots", {}, request)
    status = data_chat_features.execute_feature(
        "lot_management.status", {"product": "PROD0", "lot_id": "AZ11"}, request
    )

    assert mine["table"]["rows"] == [{"product": "PROD0", "lot_id": "AZ11", "current_step_id": "ST10"}]
    assert mine["context"]["watched_lots"] == ["AZ11"]
    assert calls == [(request, "PROD0", "AZ11")]
    assert status["table"]["rows"][0]["qty"] == 20


def test_dashboard_summary_and_stuck_lots_forward_explicit_arguments(monkeypatch):
    request = object()
    calls = []

    def summary(*, request, product):
        calls.append(("summary", request, product))
        return {"ok": True, "product": product, "wip_lots": 9, "stuck_lots": 2, "note": "cache age"}

    def stuck(*, request, product, days, limit, hours):
        calls.append(("stuck", request, product, days, limit, hours))
        return {"ok": True, "product": product, "hours": hours, "count": 1, "lots": [{"lot_id": "AZ11", "stuck_hours": 31}]}

    monkeypatch.setattr(dashboard, "dashboard_summary", summary)
    monkeypatch.setattr(dashboard, "stuck_lots", stuck)
    summary_result = data_chat_features.execute_feature("dashboard.summary", {"product": "PROD0"}, request)
    stuck_result = data_chat_features.execute_feature(
        "dashboard.stuck_lots", {"product": "PROD0", "days": 14, "limit": 12, "hours": 30}, request
    )

    assert calls == [
        ("summary", request, "PROD0"),
        ("stuck", request, "PROD0", 14, 12, 30),
    ]
    assert summary_result["table"]["rows"][0]["wip_lots"] == 9
    assert summary_result["warnings"] == ["cache age"]
    assert stuck_result["table"]["rows"] == [{"lot_id": "AZ11", "stuck_hours": 31}]


def test_dashboard_chart_list_and_data_preserve_visibility_route_and_merge_config(monkeypatch):
    request = object()
    calls = []
    monkeypatch.setattr(
        dashboard,
        "get_charts",
        lambda *, request: {
            "charts": [{"id": "c1", "title": "WIP trend", "chart_type": "line", "product": "PROD0", "x_col": "time", "y_expr": "qty"}]
        },
    )

    def chart_data(*, request, chart_id):
        calls.append((request, chart_id))
        return {
            "chart_id": chart_id,
            "config": {"title": "WIP trend", "chart_type": "line", "x_label": "Time", "y_label": "Qty"},
            "points": [{"x": "2026-09-09", "y": 12}],
            "total": 1,
            "computed_at": "now",
            "error": None,
        }

    monkeypatch.setattr(dashboard, "get_chart_data", chart_data)
    listing = data_chat_features.execute_feature("dashboard.charts", {}, request)
    result = data_chat_features.execute_feature("dashboard.chart_data", {"chart_id": "c1"}, request)

    assert listing["table"]["rows"][0]["id"] == "c1"
    assert listing["context"]["charts"] == [{"id": "c1", "title": "WIP trend"}]
    assert calls == [(request, "c1")]
    assert result["chart_result"]["chart_type"] == "line"
    assert result["chart_result"]["x_label"] == "Time"
    assert result["chart_result"]["points"] == [{"x": "2026-09-09", "y": 12}]
    assert result["table"]["rows"] == [{"x": "2026-09-09", "y": 12}]


def test_lot_progress_lookup_requires_filter_and_forwards_all_defaults(monkeypatch):
    request = object()
    calls = []

    def lookup(**kwargs):
        calls.append(kwargs)
        return {"items": [{"lot_id": "AZ11.1", "step_id": "ST10"}], "count": 1}

    monkeypatch.setattr(lot_progress, "lookup", lookup)
    with pytest.raises(ValueError, match="at least one lookup filter"):
        data_chat_features.execute_feature("lot_progress.lookup", {}, request)
    result = data_chat_features.execute_feature("lot_progress.lookup", {"lot_id": "AZ11.1"}, request)

    assert calls == [{
        "request": request,
        "limit": 50,
        "product": "",
        "lot_id": "AZ11.1",
        "root_lot_id": "",
        "wafer_id": "",
        "lot_wf": "",
    }]
    assert result["table"]["rows"][0]["step_id"] == "ST10"


def test_lot_requests_list_is_forwarded_and_inline_output_is_bounded(monkeypatch):
    request = object()
    calls = []
    rows = [{"id": str(index), "title": f"request {index}"} for index in range(205)]

    def list_requests(**kwargs):
        calls.append(kwargs)
        return {"requests": rows, "total": 205, "can_process": True}

    monkeypatch.setattr(lot_requests, "list_requests", list_requests)
    result = data_chat_features.execute_feature(
        "lot_requests.list", {"product": "PROD0", "mine": True}, request
    )

    assert calls == [{
        "request": request,
        "mine": True,
        "status": "",
        "request_type": "",
        "product": "PROD0",
        "requester_team": "",
        "author": "",
        "q": "",
    }]
    assert len(result["table"]["rows"]) == 200
    assert result["table"]["total"] == 205
    assert result["warnings"]


def test_feature_dispatch_rejects_unknown_actions_and_parameters():
    with pytest.raises(ValueError, match="unsupported feature action"):
        data_chat_features.execute_feature("dashboard.refresh", {}, object())
    with pytest.raises(ValueError, match="unsupported parameters"):
        data_chat_features.execute_feature("dashboard.summary", {"path": "/api/admin"}, object())
