import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import auth as auth_core
from core import auth_providers as ap


@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_URL", "ws://auth.example/login")
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_URL", "ws://auth.example/verify")
    monkeypatch.delenv("FLOW_PASSWORD_LOGIN_ENABLED", raising=False)
    monkeypatch.setenv("FLOW_DATA_KEY_FILE", str(tmp_path / "key" / ".flow_data.key"))
    monkeypatch.setattr(auth_core, "PATHS", SimpleNamespace(data_root=tmp_path / "data", app_root=tmp_path))
    monkeypatch.setattr("routers.auth.read_users", lambda: [{"username": "hol", "role": "admin", "tabs": "", "name": "Hol"}])
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {"splittable": ["kim.dev"]})
    verified = {}
    monkeypatch.setattr(ap, "_ws_verify", lambda user, token: (user, verified.get(user, {"user_id": user})))
    return verified


def test_websocket_replaces_password_login_by_default(ws_env, monkeypatch):
    names = [p["name"] for p in ap.describe_providers()]
    assert "websocket" in names and "password" not in names
    monkeypatch.setenv("FLOW_PASSWORD_LOGIN_ENABLED", "1")
    assert "password" in [p["name"] for p in ap.describe_providers()]


def test_mapped_user_becomes_hol_admin(ws_env, monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_USER_MAP", json.dumps({"example.user": "hol"}))
    ws_env["example.user"] = {"user_id": "example.user", "department": "IT", "name": "Woo", "email": "w@corp"}
    ident = ap.get_provider("websocket").authenticate(json.dumps({"user_id": "example.user", "token": "t"}))
    assert (ident.username, ident.role) == ("hol", "admin")
    # 관리자 프로필은 암호화 저장되고, 복호화하면 읽힌다.
    raw = (auth_core.PATHS.data_root / "auth" / "people.enc").read_bytes()
    assert b"Woo" not in raw
    assert ap.read_people()["hol"]["email"] == "w@corp"


def test_department_rules_allow_deny_and_tabs(ws_env):
    # 규칙이 없으면 모두 기본 탭으로 로그인
    ident = ap.get_provider("websocket").authenticate({"user_id": "lee", "token": "t"})
    assert ident.ephemeral and ident.role == "user" and "splittable" in ident.tabs

    ap.write_department_rules([
        {"department": "공정기술", "match": "prefix", "allow_login": True, "tabs": "splittable,filebrowser"},
        {"department": "외주", "match": "exact", "allow_login": False},
    ])
    ws_env["kim"] = {"user_id": "kim", "department": "공정기술1팀"}
    ident = ap.get_provider("websocket").authenticate({"user_id": "kim", "token": "t"})
    assert set(ident.tabs.split(",")) == {"splittable", "filebrowser"}

    for dept in ("외주", "영업"):
        ws_env["park"] = {"user_id": "park", "department": dept}
        with pytest.raises(HTTPException) as exc:
            ap.get_provider("websocket").authenticate({"user_id": "park", "token": "t"})
        assert exc.value.status_code == 403


def test_client_supplied_department_is_not_trusted(ws_env):
    ap.write_department_rules([{"department": "공정기술", "allow_login": True, "tabs": "splittable"}])
    # 인증서버 확인 응답에는 부서가 없고, 브라우저 메시지에만 부서가 있다 → 거부
    ws_env["spoof"] = {"user_id": "spoof"}
    with pytest.raises(HTTPException):
        ap.get_provider("websocket").authenticate({"user_id": "spoof", "token": "t", "department": "공정기술"})


def test_ephemeral_session_carries_tabs_without_users_csv(ws_env):
    ident = ap.get_provider("websocket").authenticate({"user_id": "lee", "token": "t"})
    out = ap.start_session(ident, audit=False)
    user = auth_core.validate_token(out["token"])
    tabs, _ = auth_core.user_tab_tokens(user)
    assert "splittable" in tabs


def test_verification_is_required_unless_trust_flag(monkeypatch):
    monkeypatch.delenv("FLOW_WS_AUTH_VERIFY_URL", raising=False)
    monkeypatch.delenv("FLOW_WS_AUTH_VERIFY", raising=False)
    monkeypatch.delenv("FLOW_WS_AUTH_TRUST_CLIENT", raising=False)
    with pytest.raises(HTTPException) as exc:
        ap._ws_verify("x", "t")
    assert exc.value.status_code == 503
    monkeypatch.setenv("FLOW_WS_AUTH_TRUST_CLIENT", "1")
    assert ap._ws_verify("x", "") == ("x", None)
