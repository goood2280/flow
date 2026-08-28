import datetime as dt

from routers import admin


def test_activity_summary_counts_unique_authenticated_users_by_day_and_month(monkeypatch, tmp_path):
    now = dt.datetime.now().replace(microsecond=0)
    yesterday = now - dt.timedelta(days=1)
    previous_month = now.replace(day=1) - dt.timedelta(days=1)
    rows = [
        {"timestamp": now.isoformat(), "username": "alice", "action": "nav:home", "tab": "home"},
        {"timestamp": (now - dt.timedelta(minutes=3)).isoformat(), "username": "alice", "action": "nav:dashboard", "tab": "dashboard"},
        {"timestamp": (now - dt.timedelta(minutes=6)).isoformat(), "username": "bob", "action": "nav:home", "tab": "home"},
        {"timestamp": yesterday.isoformat(), "actor": "alice", "action": "inform:view", "tab": "inform"},
        {"timestamp": previous_month.isoformat(), "username": "carol", "action": "auth:login", "tab": "auth"},
        {"timestamp": now.isoformat(), "username": "anonymous", "action": "health", "tab": ""},
    ]
    monkeypatch.setattr(admin, "jsonl_read", lambda *args, **kwargs: rows)
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    result = admin.activity_summary(days=90, _admin={"role": "admin"})

    assert result["active_users_by_day"][now.strftime("%Y-%m-%d")] == 2
    assert result["active_users_by_day"][yesterday.strftime("%Y-%m-%d")] == 1
    assert len(result["active_users_by_day"]) == 30
    assert result["active_users_by_month"][now.strftime("%Y-%m")] == 2
    assert result["active_users_by_month"][previous_month.strftime("%Y-%m")] == 1
    assert len(result["active_users_by_month"]) == 12


def test_activity_summary_returns_up_to_3000_recent_events(monkeypatch, tmp_path):
    now = dt.datetime.now().replace(microsecond=0)
    rows = [
        {
            "timestamp": (now - dt.timedelta(seconds=index)).isoformat(),
            "username": "alice",
            "action": "nav:home",
            "tab": "home",
        }
        for index in range(3001)
    ]
    monkeypatch.setattr(admin, "jsonl_read", lambda *args, **kwargs: rows)
    monkeypatch.setattr(admin, "ACTIVITY_LOG", tmp_path / "activity.jsonl")

    result = admin.activity_summary(days=1, _admin={"role": "admin"})

    assert len(result["recent"]) == 3000
    assert result["recent"][0]["timestamp"] == rows[0]["timestamp"]
    assert result["recent"][-1]["timestamp"] == rows[2999]["timestamp"]
