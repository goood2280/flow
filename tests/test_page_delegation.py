import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi import HTTPException


def test_page_delegation_preset_replaces_one_user_in_one_write(monkeypatch):
    from core.auth import require_admin
    from routers import admin

    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[require_admin] = lambda: {"username": "root", "role": "admin"}
    client = TestClient(app)
    current = {"filebrowser": ["alice", "bob"], "tracker": ["alice"]}
    writes = []
    monkeypatch.setattr(admin, "read_users", lambda: [
        {"username": "alice", "status": "approved", "role": "user"},
        {"username": "bob", "status": "approved", "role": "user"},
    ])
    monkeypatch.setattr(admin, "get_page_admins", lambda: current)
    monkeypatch.setattr(admin, "load_json", lambda *_args, **_kwargs: {"other_setting": 1})
    monkeypatch.setattr(admin, "save_json", lambda _path, data: writes.append(data))
    monkeypatch.setattr(admin, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(admin, "current_user", lambda _request: {"username": "root", "role": "admin"})

    response = client.post("/api/admin/page-admins/user", json={
        "username": "alice", "pages": ["tracker", "ramcache"],
    })
    assert response.status_code == 200
    assert response.json()["page_admins"] == {
        "filebrowser": ["bob"], "tracker": ["alice"], "ramcache": ["alice"],
    }
    assert writes == [{"other_setting": 1, "page_admins": response.json()["page_admins"]}]

    for invalid in ("diagnosis", "messages", "admin", "made-up"):
        denied = client.post("/api/admin/page-admins/user", json={
            "username": "alice", "pages": [invalid],
        })
        assert denied.status_code == 400
    assert len(writes) == 1


def test_page_manager_is_scoped_to_delegated_page(monkeypatch):
    from core import auth

    monkeypatch.setattr(auth, "get_page_admins", lambda: {"tracker": ["alice"]})
    user = {"username": "alice", "role": "user"}
    assert auth.is_page_manager(user, "tracker")
    assert not auth.is_page_manager(user, "filebrowser")
    assert auth.is_page_manager({"username": "root", "role": "admin"}, "filebrowser")


def test_tab_grants_are_read_from_users_csv_when_session_has_no_tabs(monkeypatch):
    from core import auth
    from routers import auth as auth_router

    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "alice", "role": "user", "tabs": "teg,inform:matrix"},
    ])
    tabs, subtabs = auth.user_tab_tokens({"username": "alice", "role": "user"})
    assert tabs == ["teg", "inform"]
    assert subtabs["inform"] == ["matrix"]
    assert auth.user_tab_tokens({"username": "unknown", "role": "user"}) == ([], {})


def test_page_manager_dependency_allows_only_its_page(monkeypatch):
    from core import auth

    user = {"username": "alice", "role": "user"}
    monkeypatch.setattr(auth, "current_user", lambda _request: user)
    monkeypatch.setattr(auth, "get_page_admins", lambda: {"tracker": ["alice"]})
    assert auth.require_page_manager("tracker")(object()) is user
    with pytest.raises(HTTPException) as denied:
        auth.require_page_manager("filebrowser")(object())
    assert denied.value.status_code == 403
    assert auth.require_page_manager_any("ramcache", "tracker")(object()) is user
