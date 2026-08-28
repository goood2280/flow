import time
import urllib.parse

import pytest
from fastapi import HTTPException

from core import auth_providers
from core import oidc_provider
from routers import admin as admin_router
from routers import auth as auth_router


OIDC_ENV = {
    "FLOW_OIDC_ISSUER": "https://sso.example.test/oidc",
    "FLOW_OIDC_CLIENT_ID": "flow-client",
    "FLOW_OIDC_CLIENT_SECRET": "test-secret",
    "FLOW_OIDC_REDIRECT_URI": "https://flow.example.test/api/auth/sso/oidc/callback",
}


def _configure(monkeypatch):
    for key, value in OIDC_ENV.items():
        monkeypatch.setenv(key, value)


def test_oidc_is_hidden_until_required_environment_exists(monkeypatch):
    for key in OIDC_ENV:
        monkeypatch.delenv(key, raising=False)
    names = [item["name"] for item in auth_providers.describe_providers()]
    assert "oidc" not in names
    assert "password" in names


def test_oidc_provider_appears_when_configured(monkeypatch):
    _configure(monkeypatch)
    descriptions = {item["name"]: item for item in auth_providers.describe_providers()}
    assert descriptions["oidc"] == {
        "name": "oidc",
        "kind": "sso",
        "label": "SSO",
        "start_url": "/api/auth/sso/oidc/start",
    }


def test_password_provider_can_be_hidden_for_sso_only(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setenv("FLOW_PASSWORD_LOGIN_ENABLED", "false")
    names = [item["name"] for item in auth_providers.describe_providers()]
    assert names == ["oidc"]


def test_login_state_is_signed_and_expires(monkeypatch):
    monkeypatch.setattr(oidc_provider.time, "time", lambda: 1_000)
    sealed = oidc_provider.seal_login_state(
        {"state": "state-1", "nonce": "nonce-1", "issued_at": 1_000},
        "state-secret",
    )
    assert oidc_provider.open_login_state(sealed, "state-secret")["nonce"] == "nonce-1"
    with pytest.raises(HTTPException):
        oidc_provider.open_login_state(sealed + "tampered", "state-secret")
    monkeypatch.setattr(oidc_provider.time, "time", lambda: 1_601)
    with pytest.raises(HTTPException, match="expired"):
        oidc_provider.open_login_state(sealed, "state-secret")


def test_authorization_url_contains_state_nonce_and_pkce(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        oidc_provider,
        "_discovery",
        lambda _config: {"authorization_endpoint": "https://sso.example.test/authorize"},
    )
    url, context = oidc_provider.OIDC_PROVIDER.begin(OIDC_ENV["FLOW_OIDC_REDIRECT_URI"])
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["flow-client"]
    assert query["state"] == [context["state"]]
    assert query["nonce"] == [context["nonce"]]
    assert query["code_challenge_method"] == ["S256"]
    assert context["code_verifier"]


def test_claim_validation_rejects_wrong_audience(monkeypatch):
    _configure(monkeypatch)
    config = oidc_provider.OidcConfig.load()
    claims = {
        "iss": config.issuer,
        "aud": "another-client",
        "exp": int(time.time()) + 300,
        "nonce": "expected",
        "sub": "employee-1",
    }
    with pytest.raises(HTTPException, match="audience"):
        oidc_provider._validate_claims(claims, config, "expected")


def test_sso_identity_persists_subject_department_and_department_default(monkeypatch):
    written = []
    monkeypatch.setattr(auth_router, "read_users", lambda: [])
    monkeypatch.setattr(auth_router, "write_users", lambda rows: written.append([dict(row) for row in rows]))
    monkeypatch.setattr(
        admin_router,
        "_load_perm_groups",
        lambda: [{
            "name": "process-default",
            "tabs": ["dashboard", "tracker"],
            "members": [],
            "departments": ["공정개발팀"],
        }],
    )

    identity = oidc_provider.OIDC_PROVIDER.resolve_identity(
        "worker01@corp.example",
        claims={"sub": "employee-00017", "department": " 공정개발팀 "},
        auto_provision=True,
        name="홍길동",
        email="worker01@corp.example",
    )

    row = written[-1][0]
    assert row["username"] == "worker01@corp.example"
    assert row["sso_id"] == "employee-00017"
    assert row["department"] == "공정개발팀"
    assert row["tabs"] == "dashboard,tracker"
    assert row["permission_source"] == "department:process-default"
    assert identity.tabs == "dashboard,tracker"
