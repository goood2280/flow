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
    assert result["mail_to"] == ["alice@company.co.kr"]
    assert users[0]["password_hash"] == "hashed:RESET_TOKEN"
    assert writes
    assert sent[0]["kwargs"]["receiver_usernames"] == ["alice"]
    assert "RESET_TOKEN" in sent[0]["kwargs"]["content"]
    assert sent[0]["result"]["payload"]["receiverList"][0]["email"] == "alice@company.co.kr"
