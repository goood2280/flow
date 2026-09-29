import copy
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core import auth as auth_core, auth_providers as providers
from routers import auth, admin


@pytest.fixture
def manager_env(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_core.PATHS, "data_root", tmp_path)
    monkeypatch.setenv("FLOW_DATA_KEY_FILE", str(tmp_path / "key"))
    monkeypatch.delenv("FLOW_DATA_KEY", raising=False)
    monkeypatch.setattr(admin, "ADMIN_SETTINGS_FILE", tmp_path / "admin_settings.json")
    users = [{"username": "root", "role": "admin", "status": "approved", "tabs": "__all__"}]
    def write(rows):
        users[:] = copy.deepcopy(rows)
    monkeypatch.setattr(auth, "read_users", lambda: copy.deepcopy(users))
    monkeypatch.setattr(admin, "read_users", lambda: copy.deepcopy(users))
    monkeypatch.setattr(auth, "write_users", write)
    monkeypatch.setattr(admin, "write_users", write)
    revoked = []
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: revoked.append(username))
    monkeypatch.setattr(admin, "_audit", lambda *a, **k: None)
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[auth_core.require_admin] = lambda: {"username": "root", "role": "admin"}
    return TestClient(app), users, revoked


def profile(username="delegate", **kw):
    return {"username": username, "name": "Manager Name", "email": "contact@example.com",
            "role": "user", "pages": ["tracker"], "department": "Team", **kw}


def test_register_delegate_contacts_login_and_mail(manager_env, monkeypatch):
    client, users, revoked = manager_env
    res = client.post("/api/admin/manager-profiles", json={"profiles": [profile()]})
    assert res.status_code == 200, res.text
    row = next(u for u in users if u["username"] == "delegate")
    assert row["password_hash"] == "" and row["role"] == "user"
    assert not row.get("email") and not row.get("name")
    assert auth_core.is_page_manager(row, "tracker")
    assert not auth_core.is_page_manager(row, "filebrowser")
    assert revoked == ["delegate"]
    raw = (auth_core.PATHS.data_root / "auth" / "people.enc").read_bytes()
    assert b"contact@example.com" not in raw
    identity = providers._identity_for_company_user("delegate", "websocket", name="SSO Name", email="sso@example.com")
    assert (identity.name, identity.email) == ("Manager Name", "contact@example.com")
    assert providers.read_people()["delegate"]["manual"]
    from core import mail
    monkeypatch.setattr(mail, "load_mail_cfg", lambda: {})
    assert mail.resolve_usernames_to_emails(["delegate"]) == (["contact@example.com"], [])
    monkeypatch.setattr(auth_core, "validate_token", lambda _: {"username": "delegate", "role": "user", "auth_method": "websocket"})
    from starlette.requests import Request
    assert auth.me(Request({"type": "http", "headers": []}))["name"] == "Manager Name"


def test_upsert_preserves_others_and_updates_permissions(manager_env):
    client, users, revoked = manager_env
    assert client.post("/api/admin/manager-profiles", json={"profiles": [profile(), profile("other", role="admin", pages=[])]}).status_code == 200
    assert client.post("/api/admin/manager-profiles", json={"profiles": [profile(pages=["filebrowser"])]}).status_code == 200
    assert providers.read_people()["other"]["email"] == "contact@example.com"
    assert auth_core.get_page_admins() == {"filebrowser": ["delegate"]}
    identity = providers._identity_for_company_user("other", "websocket")
    assert identity.role == "admin"


def test_validation_is_before_writes_and_self_demotion_blocked(manager_env):
    client, users, revoked = manager_env
    for bad in [profile(email="bad"), profile(pages=["unknown"]), profile("root"), profile(name="")]:
        res = client.post("/api/admin/manager-profiles", json={"profiles": [profile("valid"), bad]})
        assert res.status_code == 400, res.text
    assert len(users) == 1 and not revoked and not providers.read_people()


def test_profiles_require_admin(manager_env):
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        assert client.get("/api/admin/manager-profiles").status_code == 401
        assert client.post("/api/admin/manager-profiles", json={"profiles": [profile()]}).status_code == 401


def test_bad_encryption_key_does_not_overwrite_profiles(manager_env, monkeypatch):
    client, users, revoked = manager_env
    assert client.post("/api/admin/manager-profiles", json={"profiles": [profile()]}).status_code == 200
    original = (auth_core.PATHS.data_root / "auth" / "people.enc").read_bytes()
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FLOW_DATA_KEY", Fernet.generate_key().decode())
    assert client.post("/api/admin/manager-profiles", json={"profiles": [profile("new")]}).status_code == 503
    assert (auth_core.PATHS.data_root / "auth" / "people.enc").read_bytes() == original
    assert not any(u["username"] == "new" for u in users)


@pytest.mark.parametrize("endpoint", ["forgot-password", "reset-request"])
@pytest.mark.parametrize("emergency_password", ["0", "1"])
def test_websocket_disables_recovery_before_any_side_effect(monkeypatch, endpoint, emergency_password):
    monkeypatch.setenv("FLOW_WS_AUTH_URL", "wss://auth.example/login")
    monkeypatch.setenv("FLOW_PASSWORD_LOGIN_ENABLED", emergency_password)
    def forbidden(*a, **kw):
        pytest.fail("Recovery must stop before accessing accounts or sending mail")
    monkeypatch.setattr(auth, "read_users", forbidden)
    monkeypatch.setattr(auth, "_send_mail", forbidden)
    app = FastAPI()
    app.include_router(auth.router)
    assert TestClient(app).post("/api/auth/" + endpoint, json={"username": "root"}).status_code == 403


def test_password_only_recovery_remains_available(monkeypatch):
    monkeypatch.delenv("FLOW_WS_AUTH_URL", raising=False)
    monkeypatch.setenv("FLOW_PASSWORD_LOGIN_ENABLED", "1")
    monkeypatch.setattr(auth, "read_users", lambda: [])
    assert auth.forgot_password(auth.ForgotPasswordReq(username="missing"))["ok"]


def test_legacy_contact_editor_updates_encrypted_profile(manager_env):
    client, users, _ = manager_env
    assert client.post("/api/admin/manager-profiles", json={"profiles": [profile()]}).status_code == 200
    assert client.post("/api/admin/set-name", json={"username": "delegate", "name": "Edited Name"}).status_code == 200
    assert client.post("/api/admin/set-email", json={"username": "delegate", "email": "edited@example.com"}).status_code == 200
    data = client.get("/api/admin/manager-profiles").json()["profiles"]
    saved = next(p for p in data if p["username"] == "delegate")
    assert (saved["name"], saved["email"]) == ("Edited Name", "edited@example.com")
    user = next(u for u in users if u["username"] == "delegate")
    assert not user.get("name") and not user.get("email")


def test_company_id_mapping_matches_login_identity(manager_env, monkeypatch):
    client, _, _ = manager_env
    monkeypatch.setenv("FLOW_WS_AUTH_USER_MAP", '{"company.user": "flow.manager"}')
    res = client.post("/api/admin/manager-profiles", json={"profiles": [profile("company.user", role="admin", pages=[])]})
    assert res.status_code == 200, res.text
    identity = providers._identity_for_company_user("company.user", "websocket")
    assert identity.username == "flow.manager" and identity.name == "Manager Name"
