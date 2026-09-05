from unittest.mock import MagicMock
from routers import home


def test_importance_feed_includes_general_messages_and_sorts_newest_within_priority(tmp_path, monkeypatch):
    from core import notify, fab_matching_alerts, home_dismissed_alerts
    from routers import lot_management
    monkeypatch.setattr(home, "current_user", lambda req: {"username": "u", "role": "user"})
    monkeypatch.setattr(home_dismissed_alerts, "DISMISSED_FILE", tmp_path / "dismissed.json")
    monkeypatch.setattr(lot_management, "TABLE_DIR", tmp_path / "tables")
    monkeypatch.setattr(fab_matching_alerts, "list_plan_knob_anomalies", lambda **k: {"items": []})
    monkeypatch.setattr(notify, "get_notifications", lambda *a, **k: [
        {"id": "old", "type": "warning", "timestamp": "2026-09-01"},
        {"id": "new", "type": "warning", "timestamp": "2026-09-05"},
        {"id": "message", "type": "message", "timestamp": "2026-09-05"},
        {"id": "critical", "type": "critical", "timestamp": "2026-08-01"},
        {"id": "notice", "type": "admin_notice", "timestamp": "2026-09-05"},
    ])
    feed = home.home_alerts(MagicMock(), limit=500)
    assert [a["id"] for a in feed["alerts"]] == ["notif-critical", "notif-new", "notif-old", "notif-message", "notif-notice"]
    assert feed["counts"] == {"critical": 1, "warning": 2, "info": 1, "notice": 1}


def test_confirming_hyphenated_lot_alert_does_not_read_unrelated_notes(tmp_path, monkeypatch):
    from core import notify, home_dismissed_alerts
    monkeypatch.setattr(home, "current_user", lambda req: {"username": "u"})
    monkeypatch.setattr(home_dismissed_alerts, "DISMISSED_FILE", tmp_path / "dismissed.json")
    monkeypatch.setattr(home, "home_alerts", lambda *a, **k: {"alerts": [{
        "id": "plan-SPA-1-LOT-A-2", "source_notification_ids": ["matching-notification"]
    }]})
    marked = []
    monkeypatch.setattr(notify, "mark_read_by_ids", lambda username, ids: marked.extend(ids))
    home.mark_home_alerts_read(home.HomeAlertMarkReadReq(ids=["plan-SPA-1-LOT-A-2", "lotstep-P-LOT-A-2-100"]), MagicMock())
    assert marked == ["matching-notification"]
    assert home_dismissed_alerts.is_alert_dismissed("u", "lotstep-P-LOT-A-2-100")


def test_process_category_is_not_mistaken_for_ro():
    from core.fab_matching_alerts import classify_plan_mismatch
    assert classify_plan_mismatch("A", "B", ["PROCESS", "POR"])[0] == "critical"
    assert classify_plan_mismatch("A", "B", ["RO_01"])[0] == "warning"


def test_home_alerts_includes_plan_anomalies_and_deeplink(monkeypatch):
    monkeypatch.setattr(home, "current_user", lambda req: {"username": "test_user", "role": "user"})

    fake_anomalies = {
        "items": [
            {
                "id": "SPA-12345",
                "product": "ML_TABLE_PRODA",
                "product_key": "PRODA",
                "feature_name": "10.0 CONTACT",
                "column": "KNOB_10.0 CONTACT",
                "plan": "PPID_01",
                "actual_ppid": "PPID_02",
                "plan_user": "eng1",
                "occurrences": 5,
                "locations": [{"root_lot_id": "A1001", "wafer_id": "1"}],
                "plan_updated": "2026-09-01T12:00:00",
            }
        ]
    }

    import core.fab_matching_alerts as fma
    monkeypatch.setattr(fma, "list_plan_knob_anomalies", lambda limit=100: fake_anomalies)

    import core.notify as notify_mod
    monkeypatch.setattr(notify_mod, "get_notifications", lambda username, unread_only=True: [])

    req = MagicMock()
    result = home.home_alerts(req)

    assert result["ok"] is True
    assert result["total"] == 1
    alert = result["alerts"][0]
    assert alert["type"] == "splittable_plan_mismatch"
    assert alert["root_lot_id"] == "A1001"
    assert alert["product"] == "ML_TABLE_PRODA"
    assert alert["target_tab"] == "splittable"
    assert alert["priority_group"] == "critical"
    assert alert["badge"] == "Split 불일치"
    assert "A1001" in alert["title"]
    assert "PPID_01" in alert["detail"]
    assert "PPID_02" in alert["detail"]


def test_home_alerts_classifies_ro_and_unmatched_as_warning(monkeypatch):
    monkeypatch.setattr(home, "current_user", lambda req: {"username": "test_user", "role": "user"})

    fake_anomalies = {
        "items": [
            {
                "id": "SPA-RO-1",
                "product": "ML_TABLE_PRODA",
                "product_key": "PRODA",
                "feature_name": "RO STEP",
                "column": "KNOB_RO",
                "plan": "MAIN_PPID",
                "actual_ppid": "RO_PPID_01",
                "occurrences": 2,
                "locations": [{"root_lot_id": "LOT-RO", "wafer_id": "1"}],
            },
            {
                "id": "SPA-UNMATCHED-2",
                "product": "ML_TABLE_PRODA",
                "product_key": "PRODA",
                "feature_name": "EMPTY STEP",
                "column": "KNOB_EMPTY",
                "plan": "SPLIT_A",
                "actual_ppid": "-",
                "occurrences": 1,
                "locations": [{"root_lot_id": "LOT-EMPTY", "wafer_id": "2"}],
            }
        ]
    }

    import core.fab_matching_alerts as fma
    monkeypatch.setattr(fma, "list_plan_knob_anomalies", lambda limit=100: fake_anomalies)

    import core.notify as notify_mod
    monkeypatch.setattr(notify_mod, "get_notifications", lambda username, unread_only=True: [])

    req = MagicMock()
    result = home.home_alerts(req)

    assert result["ok"] is True
    assert result["total"] == 2
    by_lot = {a["root_lot_id"]: a for a in result["alerts"]}

    assert by_lot["LOT-RO"]["priority_group"] == "warning"
    assert by_lot["LOT-RO"]["badge"] == "RO 진행"

    assert by_lot["LOT-EMPTY"]["priority_group"] == "warning"
    assert by_lot["LOT-EMPTY"]["badge"] == "Step 미매칭"



def test_home_alerts_includes_warning_notifications(monkeypatch):
    monkeypatch.setattr(home, "current_user", lambda req: {"username": "test_user", "role": "user"})

    import core.fab_matching_alerts as fma
    monkeypatch.setattr(fma, "list_plan_knob_anomalies", lambda limit=100: {"items": []})

    import core.notify as notify_mod
    fake_notifs = [
        {
            "id": "notif-99",
            "title": "[경고] Lot 진행 이상 감지",
            "body": "Lot B2000 step 진행 이상",
            "type": "warning",
            "event": "my_plan_actual_mismatch",
            "payload": {"product": "ML_TABLE_PRODB", "root_lot_id": "B2000"},
            "timestamp": "2026-09-02T15:00:00",
        }
    ]
    monkeypatch.setattr(notify_mod, "get_notifications", lambda username, unread_only=True: fake_notifs)

    req = MagicMock()
    result = home.home_alerts(req)

    assert result["ok"] is True
    assert result["total"] == 1
    alert = result["alerts"][0]
    assert alert["type"] == "notification"
    assert alert["target_tab"] == "splittable"
    assert alert["target_search"] == "?product=ML_TABLE_PRODB&root=B2000"
    assert alert["root_lot_id"] == "B2000"
