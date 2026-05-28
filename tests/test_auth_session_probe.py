from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core  # noqa: E402
from routers import auth as auth_router  # noqa: E402


def _request(token: str = ""):
    headers = {"x-session-token": token} if token else {}
    return SimpleNamespace(headers=headers)


def test_auth_me_is_bootstrap_exempt_and_returns_false_without_token():
    assert "/api/auth/me" in auth_core.AUTH_EXEMPT_API_PATHS

    assert auth_router.me(_request()) == {"authenticated": False}


def test_auth_me_returns_current_user_for_valid_token(monkeypatch):
    monkeypatch.setattr(
        auth_core,
        "validate_token",
        lambda token: {"username": "alice", "role": "user"} if token == "tok" else None,
    )
    monkeypatch.setattr(
        auth_router,
        "read_users",
        lambda: [{
            "username": "alice",
            "role": "user",
            "status": "approved",
            "name": "Alice",
            "email": "alice@example.com",
            "tabs": "dashboard,inform",
        }],
    )

    out = auth_router.me(_request("tok"))

    assert out["authenticated"] is True
    assert out["username"] == "alice"
    assert out["tabs"] == "dashboard,inform"


def test_session_token_expires_after_six_hours_idle(monkeypatch):
    now = 100_000.0
    token = "tok_idle"
    monkeypatch.setattr(auth_core, "_cache_loaded", True)
    monkeypatch.setattr(auth_core, "_cache", {
        token: {
            "username": "alice",
            "role": "user",
            "issued_at": now - 60,
            "last_seen": now - auth_core.SESSION_IDLE_SECONDS - 1,
        },
    })
    saves = []
    monkeypatch.setattr(auth_core, "_now", lambda: now)
    monkeypatch.setattr(auth_core, "_save_tokens", lambda: saves.append(dict(auth_core._cache)))

    assert auth_core.SESSION_IDLE_SECONDS == 6 * 3600
    assert auth_core.validate_token(token) is None
    assert token not in auth_core._cache
    assert saves


def test_change_password_wrong_current_password_is_local_form_error(monkeypatch):
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "alice", "role": "user"})
    monkeypatch.setattr(
        auth_router,
        "read_users",
        lambda: [{"username": "alice", "password_hash": auth_core.hash_password("right")}],
    )

    with pytest.raises(HTTPException) as exc:
        auth_router.change_password(
            auth_router.ChangePwReq(old_password="wrong", new_password="next"),
            _request("tok"),
        )

    assert exc.value.status_code == 400
