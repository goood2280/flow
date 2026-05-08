from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

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
