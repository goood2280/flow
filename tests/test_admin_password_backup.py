from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core, backup, mail  # noqa: E402
from routers import admin, auth as auth_router  # noqa: E402


def test_backup_default_root_is_shared_workspace(monkeypatch):
    monkeypatch.setattr(backup, "get_settings", lambda: {"path": ""})

    assert backup._resolve_backup_root() == Path("/config/work/sharedworkspace")


def test_backup_override_path_still_wins(monkeypatch, tmp_path):
    override = tmp_path / "custom-backups"
    monkeypatch.setattr(backup, "get_settings", lambda: {"path": str(override)})

    assert backup._resolve_backup_root() == override


def test_admin_reset_password_emails_domain_address(monkeypatch):
    users = [{
        "username": "alice",
        "password_hash": "old-hash",
        "role": "user",
        "status": "approved",
        "created": "",
        "tabs": "",
        "email": "",
        "name": "",
    }]
    writes = []
    sent = []

    def fake_write_users(next_users):
        writes.append([dict(u) for u in next_users])
        users[:] = [dict(u) for u in next_users]

    real_send_mail = mail.send_mail

    def spy_send_mail(*args, **kwargs):
        result = real_send_mail(*args, **kwargs)
        sent.append({"args": args, "kwargs": kwargs, "result": result})
        return result

    monkeypatch.setattr(admin, "read_users", lambda: users)
    monkeypatch.setattr(admin, "write_users", fake_write_users)
    monkeypatch.setattr(auth_router, "read_users", lambda: users)
    monkeypatch.setattr(admin, "current_user", lambda _request: {"username": "rootadmin"})
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin, "send_notify", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin.secrets, "token_urlsafe", lambda _n: "RESET_TOKEN")
    monkeypatch.setattr(auth_core, "hash_password", lambda pw: f"hashed:{pw}")
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 1)
    monkeypatch.setattr(mail, "send_mail", spy_send_mail)
    monkeypatch.setattr(mail, "load_mail_cfg", lambda: {
        "enabled": True,
        "api_url": "dry-run",
        "from_addr": "flow@example.com",
        "domain": "company.co.kr",
        "status_code": "auth",
        "headers": {},
        "extra_data": {},
    })

    result = admin.reset_password(admin.ApproveReq(username="alice"), object())

    assert result["ok"] is True
    assert result["mail_sent"] is True
    assert "new_password" not in result
    assert result["mail_to"] == ["alice@company.co.kr"]
    assert users[0]["password_hash"] == "hashed:RESET_TOKEN"
    assert writes
    assert sent[0]["kwargs"]["receiver_usernames"] == ["alice"]
    assert "RESET_TOKEN" in sent[0]["kwargs"]["content"]
    assert sent[0]["kwargs"]["files"] == []
    assert sent[0]["result"]["payload"]["receiverList"][0]["email"] == "alice@company.co.kr"
    assert sent[0]["result"]["attachments"] == []


def test_core_mail_does_not_add_placeholder_attachment_when_files_omitted():
    result = mail.send_mail(
        sender_username="flow@example.test",
        receiver_usernames=["alice@example.test"],
        title="No attachment",
        content="<p>body</p>",
        cfg_override={
            "enabled": True,
            "api_url": "dry-run",
            "from_addr": "flow@example.test",
            "domain": "",
            "status_code": "",
            "headers": {},
            "extra_data": {},
        },
    )

    assert result["ok"] is True
    assert result["attachments"] == []


def test_forgot_password_sends_to_username_with_mail_domain(monkeypatch):
    users = [{
        "username": "alice",
        "password_hash": "old-hash",
        "role": "user",
        "status": "approved",
        "created": "",
        "tabs": "",
        "email": "",
        "name": "",
    }]
    writes = []
    sent = []

    def fake_write_users(next_users):
        writes.append([dict(u) for u in next_users])
        users[:] = [dict(u) for u in next_users]

    real_send_mail = mail.send_mail

    def spy_send_mail(*args, **kwargs):
        result = real_send_mail(*args, **kwargs)
        sent.append({"args": args, "kwargs": kwargs, "result": result})
        return result

    monkeypatch.setattr(auth_router, "read_users", lambda: users)
    monkeypatch.setattr(auth_router, "write_users", fake_write_users)
    monkeypatch.setattr(auth_router.secrets, "token_urlsafe", lambda _n: "TMP_TOKEN")
    monkeypatch.setattr(auth_core, "hash_password", lambda pw: f"hashed:{pw}")
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 1)
    monkeypatch.setattr(auth_router, "_audit_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_router, "_send_mail", spy_send_mail)
    monkeypatch.setattr(mail, "load_mail_cfg", lambda: {
        "enabled": True,
        "api_url": "dry-run",
        "from_addr": "flow@example.com",
        "domain": "company.co.kr",
        "status_code": "auth",
        "headers": {},
        "extra_data": {},
    })

    result = auth_router.forgot_password(auth_router.ForgotPasswordReq(username="alice"))

    assert result["ok"] is True
    assert "temporary password" in result["message"].lower()
    assert users[0]["password_hash"] == "hashed:TMP-TMPTOKEN"
    assert writes
    assert sent[0]["kwargs"]["receiver_usernames"] == ["alice"]
    assert "extra_emails" not in sent[0]["kwargs"]
    assert "TMP-TMPTOKEN" in sent[0]["kwargs"]["content"]
    assert sent[0]["kwargs"]["files"] == []
    assert sent[0]["result"]["payload"]["receiverList"][0]["email"] == "alice@company.co.kr"
    assert sent[0]["result"]["attachments"] == []


def test_forgot_password_does_not_lookup_by_email(monkeypatch):
    users = [{
        "username": "alice",
        "password_hash": "old-hash",
        "role": "user",
        "status": "approved",
        "created": "",
        "tabs": "",
        "email": "alice@example.test",
        "name": "",
    }]
    writes = []
    sent = []

    monkeypatch.setattr(auth_router, "read_users", lambda: users)
    monkeypatch.setattr(auth_router, "write_users", lambda next_users: writes.append([dict(u) for u in next_users]))
    monkeypatch.setattr(auth_router, "_send_mail", lambda **kwargs: sent.append(kwargs) or {"ok": True, "to": ["alice@example.test"]})

    result = auth_router.forgot_password(auth_router.ForgotPasswordReq(username="alice@example.test"))

    assert result["ok"] is True
    assert writes == []
    assert sent == []


def test_forgot_password_rolls_back_password_when_mail_fails(monkeypatch):
    users = [{
        "username": "alice",
        "password_hash": "old-hash",
        "role": "user",
        "status": "approved",
        "created": "",
        "tabs": "",
        "email": "alice@example.test",
        "name": "",
    }]
    writes = []

    def fake_write_users(next_users):
        writes.append([dict(u) for u in next_users])
        users[:] = [dict(u) for u in next_users]

    monkeypatch.setattr(auth_router, "read_users", lambda: users)
    monkeypatch.setattr(auth_router, "write_users", fake_write_users)
    monkeypatch.setattr(auth_router.secrets, "token_urlsafe", lambda _n: "TMP_TOKEN")
    monkeypatch.setattr(auth_core, "hash_password", lambda pw: f"hashed:{pw}")
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 1)
    monkeypatch.setattr(auth_router, "_audit_user", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_router, "_send_mail", lambda **_kwargs: {"ok": False, "reason": "HTTP 400"})

    try:
        auth_router.forgot_password(auth_router.ForgotPasswordReq(username="alice"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503
    else:
        raise AssertionError("forgot_password should fail when mail fails")

    assert users[0]["password_hash"] == "old-hash"
    assert len(writes) == 2


def test_admin_bulk_users_applies_selected_default_tabs(monkeypatch):
    users = []
    writes = []

    def fake_write_users(next_users):
        writes.append([dict(u) for u in next_users])
        users[:] = [dict(u) for u in next_users]

    monkeypatch.setattr(admin, "read_users", lambda: users)
    monkeypatch.setattr(admin, "write_users", fake_write_users)
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_core, "hash_password", lambda pw: f"hashed:{pw}")

    result = admin.bulk_create_users(
        admin.BulkUsersReq(
            text="name\tusername\trole\ttabs\nAlpha\talpha\tuser\tsplittable,calendar\nBeta\tbeta\tuser\t",
            default_password="1111",
            default_tabs=["dashboard", "inform"],
        ),
        object(),
    )

    assert result["ok"] is True
    assert [row["username"] for row in result["created"]] == ["alpha", "beta"]
    assert "default_password" not in result
    assert users[0]["tabs"] == "splittable,calendar"
    assert users[1]["tabs"] == "dashboard,inform"
    assert users[1]["password_hash"] == "hashed:1111"
    assert writes
