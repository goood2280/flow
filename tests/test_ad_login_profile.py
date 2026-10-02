"""AD response fields reach the Flow session, refresh, and mail recipient lookup."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import auth as auth_core, auth_providers as ap, mail
from routers import auth as auth_router, admin


@pytest.fixture
def ad_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_URL", "wss://auth.example/login")
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_URL", "https://auth.example/verify")
    monkeypatch.delenv("FLOW_WS_AUTH_USER_MAP", raising=False)
    monkeypatch.setenv("FLOW_DATA_KEY_FILE", str(tmp_path / "data.key"))
    monkeypatch.setattr(auth_core, "PATHS", SimpleNamespace(data_root=tmp_path, app_root=tmp_path))
    monkeypatch.setattr(auth_core, "_cache", {})
    monkeypatch.setattr(auth_core, "_cache_loaded", True)
    monkeypatch.setattr(auth_core, "_persist_snapshot", lambda snapshot: None)
    monkeypatch.setattr(auth_core, "_now", lambda: 100000.0)
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {})
    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [])
    monkeypatch.setattr(mail, "load_mail_cfg", lambda: {"domain": "fallback.example"})
    users = []
    monkeypatch.setattr(auth_router, "read_users", lambda: [dict(row) for row in users])
    monkeypatch.setattr(auth_router, "write_users", lambda rows: users.__setitem__(slice(None), rows))
    return users


@pytest.mark.parametrize("wrapper", [None, "data", "user", "ad"])
@pytest.mark.parametrize("stored", [False, True])
def test_ad_response_survives_login_refresh_and_mail_lookup(ad_env, monkeypatch, wrapper, stored):
    if stored:
        ad_env.append({"username": "ad.user", "role": "user", "status": "approved",
                       "name": "Old Name", "email": "old@old.example", "department": "Old Team"})
    profile = {"loginId": "ad.user", "displayName": "AD User", "name": "Legacy Name",
               "mail": "ad.user@full.example", "department": "Process Team"}
    verified = {wrapper: profile} if wrapper else profile
    frame = {wrapper: {"loginId": "ad.user"}} if wrapper else {"loginId": "ad.user"}
    frame.update(token="company-ticket", displayName="Forged", mail="fake@fake.example")

    def verify(user, token):
        assert (user, token) == ("ad.user", "company-ticket")
        assert ap._ws_credentials(verified)[0] == "ad.user"
        return user, verified

    monkeypatch.setattr(ap, "_ws_verify", verify)
    result = ap.start_session(ap.get_provider("websocket").authenticate(frame), audit=False)
    assert (result["username"], result["name"], result["email"], result["department"]) == (
        "ad.user", "AD User", "ad.user@full.example", "Process Team")
    assert result["sso_id"] == "ad.user"
    current = auth_router.me(SimpleNamespace(headers={"x-session-token": result["token"]}))
    for key in ("username", "name", "email", "department", "sso_id"):
        assert current[key] == result[key]
    assert mail.resolve_usernames_to_emails(["ad.user"]) == (["ad.user@full.example"], [])
    assert "company-ticket" not in str(auth_core._cache)
    assert all(row.get("email") != "ad.user@full.example" for row in ad_env)


def test_sam_account_name_and_full_mail_are_separate(ad_env, monkeypatch):
    monkeypatch.setattr(ap, "_ws_verify", lambda user, token: (user, {
        "sAMAccountName": "ad.user", "displayName": "AD User",
        "mail": "different.local@full.example", "department": "Process Team"}))
    identity = ap.get_provider("websocket").authenticate({"sAMAccountName": "ad.user", "token": "ticket"})
    assert identity.username == "ad.user"
    assert identity.email == "different.local@full.example"


def test_manual_manager_contact_survives_ad_response(ad_env, monkeypatch):
    ad_env.append({"username": "ad.user", "role": "admin", "status": "approved"})
    monkeypatch.setattr(ap, "read_people", lambda **kwargs: {"ad.user": {
        "manual": True, "name": "Manual Name", "email": "manual@full.example"}})
    monkeypatch.setattr(ap, "_ws_verify", lambda user, token: (user, {
        "loginId": user, "displayName": "AD Name", "mail": "ad@full.example", "department": "IT"}))
    result = ap.start_session(ap.get_provider("websocket").authenticate({"loginId": "ad.user", "token": "ticket"}), audit=False)
    current = auth_router.me(SimpleNamespace(headers={"x-session-token": result["token"]}))
    assert current["name"] == result["name"] == "Manual Name"
    assert current["email"] == result["email"] == "manual@full.example"
    assert mail.resolve_usernames_to_emails(["ad.user"]) == (["manual@full.example"], [])


def test_company_mail_profiles_exclude_expired_and_revoked_sessions(ad_env):
    auth_core.issue_token("ad.user", "user", auth_method="websocket", claims={"email": "old@full.example"})
    expired, _ = auth_core.issue_token("expired", "user", auth_method="websocket", claims={"email": "expired@full.example"})
    auth_core._cache[expired]["issued_at"] -= auth_core.SESSION_ABSOLUTE_MAX_SECONDS
    idle, _ = auth_core.issue_token("idle", "user", auth_method="websocket", claims={"email": "idle@full.example"})
    auth_core._cache[idle]["last_seen"] -= auth_core.SESSION_IDLE_SECONDS
    revoked, _ = auth_core.issue_token("revoked", "user", auth_method="websocket", claims={"email": "revoked@full.example"})
    auth_core.revoke_token(revoked)
    newest, _ = auth_core.issue_token("ad.user", "user", auth_method="websocket", claims={"email": "new@full.example"})
    auth_core._cache[newest]["issued_at"] += 1
    assert auth_core.active_company_profiles() == {"ad.user": {"name": "", "email": "new@full.example", "department": ""}}


def test_restored_password_provider_validates_password_and_approval(ad_env, monkeypatch):
    monkeypatch.setenv("FLOW_PASSWORD_LOGIN_ENABLED", "1")
    ad_env.append({"username": "local.user", "role": "user", "status": "approved",
                   "password_hash": auth_core.hash_password("sample-password")})
    provider = ap.get_provider("password")
    identity = provider.authenticate(SimpleNamespace(username="local.user", password="sample-password"))
    assert identity.username == "local.user" and identity.provider == "password"
    with pytest.raises(HTTPException) as exc:
        provider.authenticate(SimpleNamespace(username="local.user", password="wrong"))
    assert exc.value.status_code == 401
    ad_env[0]["status"] = "pending"
    with pytest.raises(HTTPException) as exc:
        ap.login_with("password", SimpleNamespace(username="local.user", password="sample-password"))
    assert exc.value.status_code == 403
