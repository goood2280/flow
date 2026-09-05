"""tests/test_watchlist_and_home_alerts.py — 관심랏 저장/토글 및 홈 알람 연동 테스트."""
import json
import pytest
from starlette.requests import Request
from starlette.datastructures import Headers

from core import watchlist as wl
from core import notify
from routers import home
from routers import watchlist as watchlist_router


def _dummy_request(username="tester", role="user"):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", b"testserver")],
        "state": {"user": {"username": username, "role": role}},
    }
    return Request(scope)


def test_watchlist_crud(tmp_path, monkeypatch):
    test_file = tmp_path / "user_watchlist.json"
    monkeypatch.setattr(wl, "WATCHLIST_FILE", test_file)

    # 1. 초기 상태: 비어있음
    assert wl.get_user_watchlist("alice") == []
    assert wl.is_lot_watched("alice", "LOT-100") is False
    assert wl.get_users_watching_lot("LOT-100") == []

    # 2. 토글 추가
    added = wl.toggle_watchlist_lot("alice", "LOT-100", True)
    assert added is True
    assert wl.is_lot_watched("alice", "LOT-100") is True
    assert "LOT-100" in wl.get_user_watchlist("alice")
    assert wl.get_users_watching_lot("LOT-100") == ["alice"]

    # 3. 다른 유저도 동일 랏 추가
    wl.toggle_watchlist_lot("bob", "lot-100", True)
    watchers = wl.get_users_watching_lot("LOT-100")
    assert "alice" in watchers
    assert "bob" in watchers

    # 4. 토글 제거
    removed = wl.toggle_watchlist_lot("alice", "LOT-100", False)
    assert removed is False
    assert wl.is_lot_watched("alice", "LOT-100") is False
    assert wl.get_users_watching_lot("LOT-100") == ["bob"]


def test_watchlist_router(tmp_path, monkeypatch):
    test_file = tmp_path / "user_watchlist.json"
    monkeypatch.setattr(wl, "WATCHLIST_FILE", test_file)

    req = _dummy_request("test_engineer")
    # 조회
    res = watchlist_router.list_watchlist_lots(req)
    assert res["ok"] is True
    assert res["lots"] == []

    # 토글 추가
    t_req = watchlist_router.WatchlistToggleReq(lot_id="W-2002")
    res_toggle = watchlist_router.toggle_watchlist_lot(t_req, req)
    assert res_toggle["ok"] is True
    assert res_toggle["watched"] is True
    assert "W-2002" in res_toggle["lots"]

    # 다시 조회
    res2 = watchlist_router.list_watchlist_lots(req)
    assert "W-2002" in res2["lots"]


def test_home_alerts_categorization(tmp_path, monkeypatch):
    test_notify_dir = tmp_path / "notifications"
    test_notify_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(notify, "NOTIFY_DIR", test_notify_dir)

    username = "alert_user"
    # 1. 관심랏 스플릿 변경 알림 발행
    notify.emit_event(
        "watched_lot_split_changed",
        actor="colleague_a",
        target_user=username,
        title="[관심랏 스플릿 변경] LOT-8888",
        body="Plan이 변경되었습니다.",
        payload={"product": "ML_TABLE_PRODA", "root_lot_id": "LOT-8888", "category": "관심랏"},
    )

    # 2. 일반 경고 알림 발행
    notify.emit_event(
        "my_plan_actual_mismatch",
        actor="flow",
        target_user=username,
        title="[plan/actual 불일치] LOT-9999",
        body="불일치 발생",
        payload={"product": "ML_TABLE_PRODA", "root_lot_id": "LOT-9999"},
    )

    req = _dummy_request(username)
    data = home.home_alerts(req)
    assert data["ok"] is True
    alerts = data["alerts"]
    assert len(alerts) >= 2

    # 관심랏 카테고리 확인
    watched_alert = next((a for a in alerts if a.get("category") == "관심랏"), None)
    assert watched_alert is not None
    assert watched_alert["badge"] in ("Plan 변경", "관심랏")
    assert watched_alert["root_lot_id"] == "LOT-8888"

    # 일반 알림/이상 카테고리 확인
    warn_alert = next((a for a in alerts if a.get("category") in ("스플릿테이블", "알림/이상")), None)
    assert warn_alert is not None
    assert warn_alert["badge"] in ("Split 불일치", "Plan 불일치", "RO 진행", "Step 미매칭", "경고", "알림")
    assert warn_alert["priority_group"] in ("critical", "warning")


def test_home_alerts_mark_read(tmp_path, monkeypatch):
    from core import home_dismissed_alerts as hda
    from core import fab_matching_alerts
    test_notify_dir = tmp_path / "notifications"
    test_notify_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(notify, "NOTIFY_DIR", test_notify_dir)
    monkeypatch.setattr(hda, "DISMISSED_FILE", tmp_path / "home_dismissed_alerts.json")
    monkeypatch.setattr(fab_matching_alerts, "list_plan_knob_anomalies", lambda **k: {"items": []})

    username = "read_tester"
    req = _dummy_request(username)

    # 1. 알림 생성
    notify.emit_event(
        "watched_lot_split_changed",
        actor="engineer_x",
        target_user=username,
        title="[관심랏 스플릿 변경] LOT-777",
        body="Plan 갱신",
        payload={"product": "ML_TABLE_PRODA", "root_lot_id": "LOT-777", "category": "관심랏"},
    )
    data = home.home_alerts(req)
    assert len(data["alerts"]) == 1
    target_alert_id = data["alerts"][0]["id"]
    assert target_alert_id.startswith("notif-")

    # 2. 홈 화면에서 읽음(확인) 처리 호출
    mark_req = home.HomeAlertMarkReadReq(ids=[target_alert_id])
    res = home.mark_home_alerts_read(mark_req, req)
    assert res["ok"] is True

    # 3. 홈 화면 알람 목록에서 제외되었는지 확인
    data_after = home.home_alerts(req)
    assert len(data_after["alerts"]) == 0

    # 4. notify 원장에서도 unread 알림이 0개인지 확인 (우상단 종 알림 동기화 원리)
    unread_notifs = notify.get_notifications(username, unread_only=True)
    assert len(unread_notifs) == 0

    # 5. Plan mismatch 알람 확인(dismiss) 처리 테스트
    plan_alert_id = "plan-12345-LOT-XYZ"
    assert hda.is_alert_dismissed(username, plan_alert_id) is False
    mark_req2 = home.HomeAlertMarkReadReq(ids=[plan_alert_id])
    home.mark_home_alerts_read(mark_req2, req)
    assert hda.is_alert_dismissed(username, plan_alert_id) is True


def test_watched_lot_specific_badges_and_self_notify(tmp_path, monkeypatch):
    test_notify_dir = tmp_path / "notifications"
    test_notify_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(notify, "NOTIFY_DIR", test_notify_dir)

    username = "hol"
    # Self-notification test for watched lot: actor == target_user should still be delivered
    ok1 = notify.emit_event(
        "watched_lot_split_changed",
        actor=username,
        target_user=username,
        title="[관심랏 Plan 추가] LOT-1111",
        body="Plan이 추가되었습니다.",
        payload={"product": "ML_TABLE_PRODA", "root_lot_id": "LOT-1111", "category": "관심랏", "badge": "Plan 추가"},
        allow_self=True,
    )
    assert ok1 is True

    ok2 = notify.emit_event(
        "watched_lot_management_updated",
        actor=username,
        target_user=username,
        title="[주요랏 코멘트 변경] Lot LOT-1111",
        body="코멘트를 변경했습니다.",
        payload={"product": "ML_TABLE_PRODA", "lot_id": "LOT-1111", "category": "관심랏", "badge": "코멘트 변경"},
        allow_self=True,
    )
    assert ok2 is True

    ok3 = notify.emit_event(
        "watched_lot_note_registered",
        actor=username,
        target_user=username,
        title="[관심랏 노트 등록] LOT-1111",
        body="노트를 등록했습니다.",
        payload={"product": "ML_TABLE_PRODA", "root_lot_id": "LOT-1111", "category": "관심랏", "badge": "노트 등록"},
        allow_self=True,
    )
    assert ok3 is True

    req = _dummy_request(username)
    data = home.home_alerts(req)
    assert data["ok"] is True
    badges = [a["badge"] for a in data["alerts"]]
    assert "Plan 추가" in badges
    assert "코멘트 변경" in badges
    assert "노트 등록" in badges
    # All of them belong to info priority group
    for a in data["alerts"]:
        if a["badge"] in ("Plan 추가", "코멘트 변경", "노트 등록"):
            assert a["priority_group"] == "info"


def test_lot_step_threshold_reached_notice_priority(tmp_path, monkeypatch):
    test_notify_dir = tmp_path / "notifications"
    test_notify_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(notify, "NOTIFY_DIR", test_notify_dir)

    username = "step_watcher"
    notify.emit_event(
        "lot_step_threshold_reached",
        actor="system",
        target_user=username,
        title="[기준 Step 도달] Lot LOT-9999",
        body="현step_id 2100 (기준 2000 이상)",
        payload={"product": "ML_TABLE_PRODA", "lot_id": "LOT-9999", "alert_step_id": "2000", "current_step_id": "2100"},
        allow_self=True,
    )

    req = _dummy_request(username)
    data = home.home_alerts(req)
    assert data["ok"] is True
    notice_alert = next((a for a in data["alerts"] if a.get("event") == "lot_step_threshold_reached"), None)
    assert notice_alert is not None
    assert notice_alert["priority_group"] == "notice"
    assert notice_alert["badge"] == "기준 Step 도달"
