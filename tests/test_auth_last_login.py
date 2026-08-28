import datetime as dt

from core import auth_providers
from routers import auth as auth_router


def test_shared_session_start_records_last_login_for_password_and_sso(monkeypatch):
    recorded = []
    monkeypatch.setattr(auth_providers.auth_core, "issue_token", lambda *args, **kwargs: ("token", 123.0))
    monkeypatch.setattr(auth_router, "record_successful_login", recorded.append)
    identity = auth_providers.AuthIdentity(
        username="shared-user",
        provider="oidc",
        role="user",
        status="approved",
    )

    result = auth_providers.start_session(identity, audit=False)

    assert result["token"] == "token"
    assert recorded == ["shared-user"]


def test_record_successful_login_updates_matching_user(monkeypatch):
    users = [{"username": "target", "last_login": ""}, {"username": "other", "last_login": "old"}]
    written = []
    monkeypatch.setattr(auth_router, "read_users", lambda: [dict(row) for row in users])
    monkeypatch.setattr(auth_router, "write_users", lambda rows: written.append(rows))
    when = dt.datetime(2026, 8, 27, 9, 30, 0)

    assert auth_router.record_successful_login("target", when=when) is True
    assert written[0][0]["last_login"] == "2026-08-27T09:30:00"
    assert written[0][1]["last_login"] == "old"
