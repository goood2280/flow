import datetime as dt
import json

from routers import admin


def test_activity_summary_counts_unique_authenticated_users_by_day_week_and_month(monkeypatch, tmp_path):
    now = dt.datetime.now().replace(microsecond=0)
    yesterday = now - dt.timedelta(days=1)
    # Use the first day of the previous month so this fixture never collides
    # with ``yesterday`` when the test runs on the first of a month.
    previous_month = (now.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    rows = [
        {"timestamp": now.isoformat(), "username": "alice", "action": "nav:home", "tab": "home"},
        {"timestamp": (now - dt.timedelta(minutes=3)).isoformat(), "username": "alice", "action": "nav:dashboard", "tab": "dashboard"},
        {"timestamp": (now - dt.timedelta(minutes=6)).isoformat(), "username": "bob", "action": "nav:home", "tab": "home"},
        {"timestamp": yesterday.isoformat(), "actor": "alice", "action": "inform:view", "tab": "inform"},
        {"timestamp": previous_month.isoformat(), "username": "carol", "action": "auth:login", "tab": "auth"},
        {"timestamp": now.isoformat(), "username": "anonymous", "action": "health", "tab": ""},
    ]
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = admin.activity_summary(days=90, _admin={"role": "admin"})

    assert result["active_users_by_day"][now.strftime("%Y-%m-%d")] == 2
    assert result["active_users_by_day"][yesterday.strftime("%Y-%m-%d")] == 1
    assert len(result["active_users_by_day"]) == 30
    now_week = (now.date() - dt.timedelta(days=now.weekday())).isoformat()
    yesterday_week = (yesterday.date() - dt.timedelta(days=yesterday.weekday())).isoformat()
    assert result["active_users_by_week"][now_week] == 2
    if yesterday_week != now_week:
        assert result["active_users_by_week"][yesterday_week] == 1
    assert len(result["active_users_by_week"]) == 26
    assert result["active_users_by_month"][now.strftime("%Y-%m")] == 2
    expected_previous_month_users = 1 + int(
        yesterday.strftime("%Y-%m") == previous_month.strftime("%Y-%m")
    )
    assert result["active_users_by_month"][previous_month.strftime("%Y-%m")] == expected_previous_month_users
    assert len(result["active_users_by_month"]) == 12
    assert result["total"] == 5
    assert result["by_week"][now_week] >= 3
    assert result["by_month"][now.strftime("%Y-%m")] >= 3
    assert result["unattributed_count"] == 1
    assert "anonymous" not in result["by_user"]
    assert all(row.get("username") != "anonymous" for row in result["recent"])


def test_activity_features_excludes_unattributed_events(monkeypatch):
    now = dt.datetime.now().replace(microsecond=0).isoformat()
    rows = [
        {"timestamp": now, "username": "hol", "action": "nav:admin", "tab": "admin"},
        {"timestamp": now, "username": "anonymous", "action": "dashboard:wip_split", "tab": "dashboard"},
    ]

    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = admin.activity_features(days=1, _admin={"role": "admin"})

    assert result["feature_count"] == 1
    assert result["features"][0]["feature"] == "nav"
    assert result["features"][0]["users"] == ["hol"]
    assert result["unattributed_count"] == 1


def test_activity_summary_returns_up_to_3000_recent_events(monkeypatch, tmp_path):
    now = dt.datetime.combine(dt.date.today(), dt.time(12, 0))
    rows = [
        {
            "timestamp": (now - dt.timedelta(seconds=index)).isoformat(),
            "username": "alice",
            "action": "nav:home",
            "tab": "home",
        }
        for index in range(3001)
    ]
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = admin.activity_summary(days=1, _admin={"role": "admin"})

    assert len(result["recent"]) == 3000
    assert result["recent"][0]["timestamp"] == rows[0]["timestamp"]
    assert result["recent"][-1]["timestamp"] == rows[2999]["timestamp"]


def test_activity_summary_uses_exact_calendar_day_window(monkeypatch, tmp_path):
    today = dt.date.today()
    rows = [
        {"timestamp": dt.datetime.combine(today - dt.timedelta(days=6), dt.time.min).isoformat(), "username": "alice", "action": "nav:home", "tab": "home"},
        {"timestamp": dt.datetime.combine(today - dt.timedelta(days=7), dt.time.max).isoformat(), "username": "bob", "action": "nav:home", "tab": "home"},
    ]
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = admin.activity_summary(days=7, _admin={"role": "admin"})

    assert result["total"] == 1
    assert result["by_day"] == {(today - dt.timedelta(days=6)).isoformat(): 1}


def test_activity_summary_groups_weeks_from_monday(monkeypatch, tmp_path):
    rows = [
        {"timestamp": "2026-09-20T10:00:00", "username": "alice", "action": "nav:home"},
        {"timestamp": "2026-09-20T11:00:00", "username": "bob", "action": "nav:home"},
        {"timestamp": "2026-09-21T09:00:00", "username": "alice", "action": "nav:admin"},
    ]
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")
    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    result = admin.activity_summary(days=0, _admin={"role": "admin"})

    assert result["active_users_by_week"]["2026-09-14"] == 2
    assert result["active_users_by_week"]["2026-09-21"] == 1
    assert result["by_week"] == {"2026-09-14": 2, "2026-09-21": 1}


def test_activity_summary_days_zero_includes_the_entire_preserved_history(monkeypatch, tmp_path):
    now = dt.datetime.now().replace(microsecond=0)
    first = now - dt.timedelta(days=400)
    rows = [
        {"timestamp": first.isoformat(), "username": "alice", "action": "nav:home", "tab": "home"},
        {"timestamp": now.isoformat(), "username": "bob", "action": "nav:admin", "tab": "admin"},
    ]
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = admin.activity_summary(days=0, _admin={"role": "admin"})

    assert result["window_days"] == 0
    assert result["activity_start"] == first.date().isoformat()
    assert result["activity_end"] == now.date().isoformat()
    assert result["total"] == 2
    assert result["by_day"] == {
        first.date().isoformat(): 1,
        now.date().isoformat(): 1,
    }
    assert len(result["active_users_by_day"]) == 401
    expected_weeks = ((now.date() - dt.timedelta(days=now.weekday())) - (first.date() - dt.timedelta(days=first.weekday()))).days // 7 + 1
    assert len(result["active_users_by_week"]) == expected_weeks
    assert len(result["active_users_by_month"]) > 12


def test_activity_features_days_zero_includes_old_records(monkeypatch):
    now = dt.datetime.now().replace(microsecond=0)
    rows = [
        {"timestamp": (now - dt.timedelta(days=500)).isoformat(), "username": "alice", "action": "inform:view", "tab": "inform"},
        {"timestamp": now.isoformat(), "username": "bob", "action": "nav:admin", "tab": "admin"},
    ]

    admin.ACTIVITY_LOG.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = admin.activity_features(days=0, _admin={"role": "admin"})

    assert result["window_days"] == 0
    assert {item["feature"] for item in result["features"]} == {"inform", "nav"}
