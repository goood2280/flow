"""ETA chat tests use synthetic rows and the real tracker calculation/router."""
import datetime as dt

import pytest

from core import data_chat_eta, lot_tracker
from core import lot_progress_cache
from routers import lot_tracker as tracker_router


@pytest.fixture
def tracker_data(monkeypatch):
    histories = {
        ("PRODC1", "AVDFS.1"): [
            {"step_id": "CS080000", "tkout_time": dt.datetime(2026, 9, 18)},
            {"step_id": "CS090000", "tkout_time": dt.datetime(2026, 9, 20)},
        ],
        ("PRODC1", "AZCVC.1"): [
            {"step_id": "CS090000", "tkout_time": dt.datetime(2026, 9, 10)},
            {"step_id": "CS100000", "tkout_time": dt.datetime(2026, 9, 13)},
        ],
        ("PRODC1", "AZBAD.1"): [
            {"step_id": "CS090000", "tkout_time": dt.datetime(2026, 9, 10)},
            {"step_id": "CS095000", "tkout_time": dt.datetime(2026, 9, 12)},
        ],
        ("PRODC2", "BZDFS.1"): [
            {"step_id": "CS190000", "tkout_time": dt.datetime(2026, 9, 19)},
        ],
    }

    def history(lot_id, candidates):
        product = candidates[0]
        rows = histories.get((product, lot_id), [])
        return (product, rows) if rows else ("", [])

    monkeypatch.setattr(lot_tracker, "_history", history)
    monkeypatch.setattr(lot_tracker, "lookup_lot_progress", lambda **kwargs: [])
    monkeypatch.setattr(lot_tracker, "describe_step", lambda step, product: {"step_desc": step})
    monkeypatch.setattr(tracker_router, "current_user", lambda request: {
        "username": "allowed", "role": "admin", "tabs": ""})
    monkeypatch.setattr(lot_progress_cache, "lookup_lot_progress", lambda **kwargs: [
        {"product": "PRODC1", "lot_id": "AZCVC.1"},
        {"product": "PRODC1", "lot_id": "AZBAD.1"},
        {"product": "PRODC1", "lot_id": "AVDFS.1"},
        {"product": "PRODC2", "lot_id": "BZREF.1"},
        {"product": "PRODC1", "lot_id": "DEMO-LOT-01"},
    ])
    return histories


def test_exact_example_chain_keeps_target_and_uses_native_tkout(tracker_data, monkeypatch):
    calls = []
    original = tracker_router.get_lot_tracker

    def checked_call(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(tracker_router, "get_lot_tracker", checked_call)
    first = data_chat_eta.dispatch("PRODC1 AVDFS.1 CS100000에 언제쯤 도착예정이야?",
                                   {"confirmed_product": "PRODC1"}, object())
    assert first["context"]["eta_query"] == {
        "product": "PRODC1", "lot_id": "AVDFS.1", "target_step_id": "CS100000",
        "reference_lot_id": ""}
    clarification = first["tool"]["clarification"]
    assert clarification["kind"] == "eta_reference"
    assert clarification["allow_other"] is True
    assert clarification["options"] == [
        {"label": "AZBAD.1", "value": "AZBAD.1"},
        {"label": "AZCVC.1", "value": "AZCVC.1"},
    ]
    second = data_chat_eta.dispatch("어떤랏으로", first["context"], object())
    assert second["tool"]["clarification"] == clarification
    third = data_chat_eta.dispatch("AZCVC.1 기준으로 산출해줘. 계산해줘", second["context"], object())
    assert third["tool"]["eta"] == "2026-09-23T00:00:00"
    assert third["tool"]["target_step_id"] == "CS100000"
    assert third["tool"]["reference_lot_id"] == "AZCVC.1"
    assert third["context"]["eta_query"]["lot_id"] == "AVDFS.1"
    assert third["context"]["pending_eta"] is False
    assert "완료(TKOUT)" in third["reply"]
    assert "도착 시각은" in third["reply"]
    assert calls[-1] == {"request": calls[-1]["request"], "lot_id": "AVDFS.1",
                         "reference_lot_id": "AZCVC.1", "target_step_id": "CS100000",
                         "product": "PRODC1", "dummy": False}


def test_missing_exact_target_history_never_uses_fallback_eta(tracker_data):
    first = data_chat_eta.dispatch("PRODC1 AVDFS.1 CS100000에 언제 도착?",
                                   {"confirmed_product": "PRODC1"}, object())
    bad = data_chat_eta.dispatch("AZBAD.1 기준으로 계산해줘", first["context"], object())
    assert bad["ok"] is False
    assert "eta" not in bad["tool"]
    assert bad["context"]["eta_query"]["reference_lot_id"] == ""
    assert bad["tool"]["missing"] == ["reference_lot_id"]


def test_router_permission_is_preserved_before_candidate_listing(tracker_data, monkeypatch):
    monkeypatch.setattr(tracker_router, "current_user", lambda request: {
        "username": "denied", "role": "user", "tabs": "dashboard"})
    monkeypatch.setattr(tracker_router, "is_page_manager", lambda user, page: False)
    with pytest.raises(Exception) as caught:
        data_chat_eta.dispatch("PRODC1 AVDFS.1 CS100000에 언제 도착?",
                               {"confirmed_product": "PRODC1"}, object())
    assert getattr(caught.value, "status_code", None) == 403


def test_product_switch_clears_old_reference_and_target(tracker_data):
    first = data_chat_eta.dispatch("PRODC1 AVDFS.1 CS100000에 언제 도착?",
                                   {"confirmed_product": "PRODC1"}, object())
    computed = data_chat_eta.dispatch("AZCVC.1 기준으로 계산해줘", first["context"], object())
    switched_context = {**computed["context"], "confirmed_product": "PRODC2"}
    switched = data_chat_eta.dispatch("PRODC2 BZDFS.1 CS200000에 언제 도착?",
                                     switched_context, object())
    assert switched["tool"]["clarification"]["kind"] == "eta_reference"
    assert switched["context"]["eta_query"] == {
        "product": "PRODC2", "lot_id": "BZDFS.1", "target_step_id": "CS200000",
        "reference_lot_id": ""}


def test_dummy_lot_never_exposes_simulated_forecast(tracker_data):
    result = data_chat_eta.dispatch("PRODC1 DEMO-LOT-01 CS100000에 언제 도착?",
                                    {"confirmed_product": "PRODC1"}, object())
    assert result["ok"] is False
    assert "eta" not in result["tool"]


def test_reference_button_value_keeps_target(tracker_data):
    first = data_chat_eta.dispatch("PRODC1 AVDFS.1 CS100000에 언제 도착?",
                                   {"confirmed_product": "PRODC1"}, object())
    result = data_chat_eta.dispatch("AZCVC.1", first["context"], object())
    assert result["tool"]["lot_id"] == "AVDFS.1"
    assert result["tool"]["reference_lot_id"] == "AZCVC.1"


def test_actual_tkin_supports_arrival_estimate(tracker_data):
    tracker_data[("PRODC1", "AZCVC.1")][-1]["tkin_time"] = dt.datetime(2026, 9, 12)
    first = data_chat_eta.dispatch("PRODC1 AVDFS.1 CS100000에 언제 도착?", {"confirmed_product": "PRODC1"}, object())
    out = data_chat_eta.dispatch("AZCVC.1", first["context"], object())
    assert out["tool"]["eta"] == "2026-09-22T00:00:00"
    assert out["tool"]["estimate_kind"] == "도착·진입(TKIN)"
    assert out["tool"]["forecast"]["eta"] == "2026-09-23T00:00:00"


def test_unrelated_calculation_is_not_eta(tracker_data):
    assert data_chat_eta.dispatch("차트 평균 계산해줘", {"confirmed_product": "PRODC1"}, object()) is None


def test_full_orchestrator_preserves_eta_chain(tracker_data, monkeypatch):
    from core import data_chat, llm_adapter, product_semantics
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODC1"])
    monkeypatch.setattr(data_chat, "split_table_product", lambda p: "")
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a: [])
    first = data_chat.execute("PRODC1 AVDFS.1 CS100000에 언제쯤 도착예정이야?", {}, object())
    second = data_chat.execute("어떤랏으로", first["context"], object())
    third = data_chat.execute("AZCVC.1 기준으로 산출해줘. 계산해줘", second["context"], object())
    assert third["tool"]["eta"] == "2026-09-23T00:00:00"
    assert third["tool"]["lot_id"] == "AVDFS.1"
