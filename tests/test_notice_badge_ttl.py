import datetime

from core import notify
from routers import messages


def _iso(days_ago: float) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days_ago)).isoformat()


def test_message_notice_badge_counts_only_unread_notices_from_last_week(monkeypatch):
    notices = [
        {"id": "fresh", "author": "admin", "title": "fresh", "body": "body",
         "created_at": _iso(6), "read_by": []},
        {"id": "expired", "author": "admin", "title": "old", "body": "body",
         "created_at": _iso(8), "read_by": []},
        {"id": "read", "author": "admin", "title": "read", "body": "body",
         "created_at": _iso(1), "read_by": ["user"]},
        {"id": "own", "author": "user", "title": "own", "body": "body",
         "created_at": _iso(1), "read_by": []},
    ]
    monkeypatch.setattr(messages, "verify_owner", lambda request, username: None)
    monkeypatch.setattr(messages, "_load_thread", messages._empty_thread)
    monkeypatch.setattr(messages, "_load_notices", lambda: notices)

    result = messages.unread_count(None, "user")

    assert result["notice_unread"] == 1
    assert result["total"] == 1
    assert [item["id"] for item in result["unread_notices"]] == ["fresh"]
    # 7일이 지난 공지는 배지에서만 빠지고 공지 목록/이력에는 그대로 남는다.
    assert {item["id"] for item in messages.list_notices("user")["notices"]} == {
        "fresh", "expired", "read", "own",
    }


def test_bell_badge_expires_only_admin_notices_and_keeps_history(monkeypatch):
    notifications = [
        {"id": "fresh", "title": "New Notice", "type": "admin_notice",
         "timestamp": _iso(6), "read": False},
        {"id": "expired", "title": "New Notice", "type": "admin_notice",
         "timestamp": _iso(8), "read": False},
        # 기존 버전이 message 타입으로 저장한 공지도 같은 TTL을 적용한다.
        {"id": "legacy-expired", "title": "New Notice", "type": "message",
         "timestamp": _iso(8), "read": False},
        # 일반 업무 알림은 오래됐더라도 자동 만료하지 않는다.
        {"id": "ordinary", "title": "Admin replied", "type": "message",
         "timestamp": _iso(30), "read": False},
    ]
    monkeypatch.setattr(notify, "_read_all", lambda username: notifications)

    assert [item["id"] for item in notify.get_notifications("user", unread_only=True)] == [
        "fresh", "ordinary",
    ]
    assert [item["id"] for item in notify.get_notifications("user")] == [
        "fresh", "expired", "legacy-expired", "ordinary",
    ]


def test_admin_notice_badge_ttl_handles_iso_timezone_and_invalid_legacy_values():
    now = datetime.datetime(2026, 9, 3, 12, 0, tzinfo=datetime.timezone.utc)
    assert notify.is_fresh_admin_notice("2026-08-27T12:00:01Z", now=now)
    assert not notify.is_fresh_admin_notice("2026-08-27T12:00:00Z", now=now)
    assert not notify.is_fresh_admin_notice("")
    assert not notify.is_fresh_admin_notice("not-a-date")
