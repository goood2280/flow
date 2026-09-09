from __future__ import annotations

import json

from core import mapfile_alerts


def _file(filename: str, signature: str, light: str, **extra) -> dict:
    return {
        "filename": filename,
        "signature": signature,
        "status": "ok",
        "traffic_light": light,
        "summary": {},
        "targets": {},
        "issues": [],
        **extra,
    }


def _inspection(files: list[dict]) -> dict:
    return {
        "vehicle": "VEH_A",
        "product_code": "PA100",
        "files": files,
    }


def test_publishes_only_to_teg_check_members_and_deduplicates_same_version(tmp_path):
    calls = []

    def emit(event_type, **kwargs):
        calls.append((event_type, kwargs))
        return True

    groups = lambda: [
        {"name": "OTHER", "members": ["outsider"]},
        {"name": "teg_check", "members": ["alice", "bob", "alice"]},
    ]
    inspection = _inspection([
        _file("PA100_red.txt", "sig-red", "red", summary={"red": 2}),
        _file("PA100_yellow.txt", "sig-yellow", "yellow", targets={"missing": 1}),
        _file("PA100_gray.txt", "sig-gray", "gray"),
        _file("PA100_green.txt", "sig-green", "green"),
    ])
    state_path = tmp_path / "mapfile_alert_state.json"

    first = mapfile_alerts.publish_mapfile_alerts(
        inspection, state_path=state_path, group_loader=groups, emitter=emit
    )
    second = mapfile_alerts.publish_mapfile_alerts(
        inspection, state_path=state_path, group_loader=groups, emitter=emit
    )

    assert first["published"] == 6
    assert first["abnormal_files"] == 3
    assert second["published"] == 0
    assert second["duplicates"] == 6
    assert {kwargs["target_user"] for _, kwargs in calls} == {"alice", "bob"}
    assert "outsider" not in {kwargs["target_user"] for _, kwargs in calls}
    assert all(event == mapfile_alerts.EVENT_TYPE for event, _ in calls)
    assert all(kwargs["payload"]["target_tab"] == "teg" for _, kwargs in calls)
    assert all(kwargs["payload"]["sidebar_group"] == "teg" for _, kwargs in calls)
    assert all(kwargs["notification_id"].startswith("teg-") for _, kwargs in calls)
    assert len({kwargs["notification_id"] for _, kwargs in calls}) == 6
    assert len(json.loads(state_path.read_text("utf-8"))["deliveries"]) == 6


def test_changed_signature_publishes_a_new_alert_for_each_member(tmp_path):
    calls = []
    emit = lambda event_type, **kwargs: calls.append(kwargs) or True
    groups = lambda: [{"name": "TEG_CHECK", "members": ["alice", "bob"]}]
    state_path = tmp_path / "state.json"

    mapfile_alerts.publish_mapfile_alerts(
        _inspection([_file("PA100.txt", "v1", "red")]),
        state_path=state_path,
        group_loader=groups,
        emitter=emit,
    )
    result = mapfile_alerts.publish_mapfile_alerts(
        _inspection([_file("PA100.txt", "v2", "yellow")]),
        state_path=state_path,
        group_loader=groups,
        emitter=emit,
    )

    assert result["published"] == 2
    assert len(calls) == 4


def test_gray_error_and_missing_target_are_critical_not_safe(tmp_path):
    calls = []
    groups = lambda: [{"name": "TEG_CHECK", "members": ["alice"]}]
    inspection = _inspection([
        _file("gray.txt", "g", "gray"),
        _file("error.txt", "e", "green", status="error", error="parse failed"),
        _file("missing.txt", "m", "green", targets={"missing": 2}),
        _file("clean.txt", "ok", "green"),
    ])

    result = mapfile_alerts.publish_mapfile_alerts(
        inspection,
        state_path=tmp_path / "state.json",
        group_loader=groups,
        emitter=lambda event_type, **kwargs: calls.append(kwargs) or True,
    )

    assert result["abnormal_files"] == 3
    assert result["published"] == 3
    assert all(call["payload"]["traffic_light"] in {"gray", "green"} for call in calls)
    assert all("Mapfile 이상" in call["title"] for call in calls)


def test_empty_group_is_visible_and_retries_after_member_is_added(tmp_path):
    state_path = tmp_path / "state.json"
    calls = []
    empty = lambda: [{"name": "TEG_CHECK", "members": []}]
    inspection = _inspection([_file("PA100.txt", "v1", "red")])

    waiting = mapfile_alerts.publish_mapfile_alerts(
        inspection,
        state_path=state_path,
        group_loader=empty,
        emitter=lambda event_type, **kwargs: calls.append(kwargs) or True,
    )
    retried = mapfile_alerts.publish_mapfile_alerts(
        inspection,
        state_path=state_path,
        group_loader=lambda: [{"name": "TEG_CHECK", "members": ["alice"]}],
        emitter=lambda event_type, **kwargs: calls.append(kwargs) or True,
    )

    assert waiting["ok"] is False
    assert waiting["group_status"] == "empty"
    assert waiting["retry_required"] is True
    assert "재시도" in waiting["warning"]
    assert retried["published"] == 1
    assert [call["target_user"] for call in calls] == ["alice"]


def test_opted_out_recipient_is_recorded_without_repeated_attempts(tmp_path):
    attempts = []
    groups = lambda: [{"name": "TEG_CHECK", "members": ["alice"]}]
    inspection = _inspection([_file("PA100.txt", "v1", "red")])
    state_path = tmp_path / "state.json"

    first = mapfile_alerts.publish_mapfile_alerts(
        inspection,
        state_path=state_path,
        group_loader=groups,
        emitter=lambda event_type, **kwargs: attempts.append(kwargs) or False,
    )
    second = mapfile_alerts.publish_mapfile_alerts(
        inspection,
        state_path=state_path,
        group_loader=groups,
        emitter=lambda event_type, **kwargs: attempts.append(kwargs) or False,
    )

    assert first["suppressed"] == 1
    assert second["duplicates"] == 1
    assert len(attempts) == 1


def test_scheduler_surfaces_empty_group_retry(monkeypatch):
    from core import mapfile_traffic_scheduler as scheduler

    monkeypatch.setattr(scheduler._tm, "product_catalog", lambda: [
        {"vehicle": "VEH_A", "product_code": "PA100"}
    ])
    monkeypatch.setattr(scheduler._mt, "inspect_mapfiles_for_product", lambda *a, **k: _inspection([
        _file("PA100.txt", "v1", "red")
    ]))
    monkeypatch.setattr(scheduler._ma, "publish_mapfile_alerts", lambda result: {
        "published": 0,
        "duplicates": 0,
        "suppressed": 0,
        "failed": 0,
        "retry_required": True,
        "warning": "TEG_CHECK 그룹에 멤버가 없습니다. 다음 검사에서 재시도합니다.",
    })

    result = scheduler.run_mapfile_traffic_once()

    assert result["alert_delivery"]["retry_required"] is True
    assert "재시도" in result["alert_delivery"]["warnings"][0]


def test_home_feed_keeps_teg_critical_deep_link(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from core import fab_matching_alerts, home_dismissed_alerts, notify
    from routers import home, lot_management

    monkeypatch.setattr(home, "current_user", lambda req: {"username": "alice", "role": "user"})
    monkeypatch.setattr(home_dismissed_alerts, "DISMISSED_FILE", tmp_path / "dismissed.json")
    monkeypatch.setattr(lot_management, "TABLE_DIR", tmp_path / "tables")
    monkeypatch.setattr(fab_matching_alerts, "list_plan_knob_anomalies", lambda **k: {"items": []})
    monkeypatch.setattr(notify, "get_notifications", lambda *a, **k: [{
        "id": "teg-1",
        "title": "[TEG_CHECK] PA100 · PA100.txt Mapfile 이상",
        "body": "신호등 red · red 1건",
        "type": "critical",
        "event": mapfile_alerts.EVENT_TYPE,
        "payload": {
            "product": "VEH_A",
            "vehicle": "VEH_A",
            "filename": "PA100.txt",
            "target_tab": "teg",
            "target_search": "?vehicle=VEH_A&view=traffic&filename=PA100.txt",
        },
        "timestamp": "2026-09-10T09:00:00",
    }])

    result = home.home_alerts(MagicMock(), limit=50)
    alert = result["alerts"][0]

    assert alert["category"] == "TEG_CHECK"
    assert alert["priority_group"] == "critical"
    assert alert["target_tab"] == "teg"
    assert alert["target_search"] == "?vehicle=VEH_A&view=traffic&filename=PA100.txt"
    assert alert["action_label"] == "TEG Mapfile에서 확인"
