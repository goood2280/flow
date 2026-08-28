import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from core import auth as auth_core
from core import background_owner
from app_v2.runtime import startup
from routers import admin, splittable


@pytest.mark.parametrize("username", ["user01", "홍길동", "first.last", "user@corp.com", "a_b-c"])
def test_username_validation_accepts_safe_account_ids(username):
    assert auth_core.validate_username(f"  {username}  ") == username


@pytest.mark.parametrize(
    "username",
    ["../escape", r"..\escape", "C:drive", "a/b", "a,b", "a\nname", "=formula", "+formula", "-formula", "@formula", ".", "..", "CON", "LPT1.txt"],
)
def test_username_validation_rejects_path_and_csv_injection(username):
    with pytest.raises(ValueError):
        auth_core.validate_username(username)


def test_bulk_user_creation_requires_explicit_strong_password():
    request = object()
    for password in ("", "1111", "hol12345!"):
        with pytest.raises(HTTPException) as exc:
            admin.bulk_create_users(
                admin.BulkUsersReq(rows=[{"username": "safeuser"}], default_password=password),
                request,
                _admin={"role": "admin"},
            )
        assert exc.value.status_code == 400


def test_bulk_user_creation_skips_unsafe_username(monkeypatch):
    written = []
    monkeypatch.setattr(admin, "read_users", lambda: [])
    monkeypatch.setattr(admin, "write_users", lambda rows: written.append(rows))
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    result = admin.bulk_create_users(
        admin.BulkUsersReq(
            rows=[{"username": "../escape"}],
            default_password="Strong-Temp-2026!",
        ),
        object(),
        _admin={"role": "admin"},
    )
    assert result["created"] == []
    assert result["skipped"] and "username" in result["skipped"][0]["reason"].lower()
    assert written == [[]]


def test_bulk_user_creation_always_starts_without_permissions(monkeypatch):
    written = []
    monkeypatch.setattr(admin, "read_users", lambda: [])
    monkeypatch.setattr(admin, "write_users", lambda rows: written.append(rows))
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)

    result = admin.bulk_create_users(
        admin.BulkUsersReq(
            rows=[{"username": "newuser", "role": "admin", "tabs": "dashboard,filebrowser"}],
            default_password="Strong-Temp-2026!",
            default_tabs=["dashboard", "filebrowser"],
        ),
        object(),
        _admin={"role": "admin"},
    )

    assert result["created"] == [
        {"username": "newuser", "name": "", "role": "user", "tabs": ""}
    ]
    assert written[0][0]["role"] == "user"
    assert written[0][0]["tabs"] == ""


def test_permission_group_list_prunes_deleted_users_and_admins(monkeypatch):
    groups = [
        {"name": "engineers", "tabs": ["dashboard"], "members": ["active", "admin01", "deleted"]},
        {"name": "empty", "tabs": [], "members": ["admin01", "deleted"]},
    ]
    saved = []
    monkeypatch.setattr(admin, "read_users", lambda: [
        {"username": "active", "role": "user"},
        {"username": "admin01", "role": "admin"},
    ])
    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [dict(g, members=list(g["members"])) for g in groups])
    monkeypatch.setattr(admin, "_save_perm_groups", lambda value: saved.append(value))

    result = admin.perm_groups_list(_admin={"role": "admin"})

    assert result["groups"] == [
        {"name": "engineers", "tabs": ["dashboard"], "members": ["active"]},
        {"name": "empty", "tabs": [], "members": []},
    ]
    assert saved == [result["groups"]]


def test_promoting_user_removes_groups_and_grants_all_permissions(monkeypatch):
    rows = [{"username": "promoted", "role": "user", "tabs": "dashboard", "permission_source": "group:engineers"}]
    written = []
    audited = []
    monkeypatch.setattr(admin, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(admin, "write_users", lambda value: written.append(value))
    monkeypatch.setattr(admin, "_remove_from_perm_groups", lambda username: ["engineers"])
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: audited.append((args, kwargs)))
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 2)

    result = admin.set_role(
        admin.SetRoleReq(username="promoted", role="admin"),
        object(),
        _admin={"role": "admin"},
    )

    assert written[0][0]["role"] == "admin"
    assert written[0][0]["tabs"] == "__all__"
    assert written[0][0]["permission_source"] == "admin"
    assert result["removed_from_permission_groups"] == ["engineers"]
    assert result["revoked_sessions"] == 2
    assert result["unchanged"] is False
    assert audited


def test_saving_existing_admin_repairs_stale_permission_state(monkeypatch):
    rows = [{"username": "admin01", "role": "admin", "tabs": "dashboard", "permission_source": "group:legacy"}]
    written = []
    monkeypatch.setattr(admin, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(admin, "write_users", lambda value: written.append(value))
    monkeypatch.setattr(admin, "_remove_from_perm_groups", lambda username: ["legacy"])
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 1)

    result = admin.set_role(
        admin.SetRoleReq(username="admin01", role="admin"),
        object(),
        _admin={"role": "admin"},
    )

    assert written[0][0]["tabs"] == "__all__"
    assert written[0][0]["permission_source"] == "admin"
    assert result["removed_from_permission_groups"] == ["legacy"]
    assert result["unchanged"] is False


def test_demoting_admin_clears_admin_permissions_and_stale_groups(monkeypatch):
    rows = [
        {"username": "admin01", "role": "admin", "tabs": "__all__", "permission_source": "admin"},
        {"username": "admin02", "role": "admin", "tabs": "__all__", "permission_source": "admin"},
    ]
    written = []
    monkeypatch.setattr(admin, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(admin, "write_users", lambda value: written.append(value))
    monkeypatch.setattr(admin, "_remove_from_perm_groups", lambda username: ["legacy"])
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 1)

    result = admin.set_role(
        admin.SetRoleReq(username="admin01", role="user"),
        object(),
        _admin={"role": "admin"},
    )

    demoted = written[0][0]
    assert (demoted["role"], demoted["tabs"], demoted["permission_source"]) == ("user", "", "")
    assert result["removed_from_permission_groups"] == ["legacy"]


def test_admin_tab_edit_preserves_all_permissions_and_removes_groups(monkeypatch):
    rows = [{"username": "admin01", "role": "admin", "tabs": "dashboard", "permission_source": "group:legacy"}]
    written = []
    monkeypatch.setattr(admin, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(admin, "write_users", lambda value: written.append(value))
    monkeypatch.setattr(admin, "_remove_from_perm_groups", lambda username: ["legacy"])
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)

    result = admin.set_tabs(
        admin.PermReq(username="admin01", tabs=["dashboard"]),
        object(),
        _admin={"role": "admin"},
    )

    assert written[0][0]["tabs"] == "__all__"
    assert written[0][0]["permission_source"] == "admin"
    assert result == {
        "ok": True,
        "admin_all_permissions": True,
        "removed_from_groups": ["legacy"],
    }


def test_delete_user_removes_permission_group_membership(monkeypatch):
    user_rows = [
        {"username": "active", "role": "user"},
        {"username": "deleted", "role": "user"},
    ]
    groups = [
        {"name": "engineers", "tabs": ["dashboard"], "members": ["active", "deleted"]},
        {"name": "reviewers", "tabs": ["tracker"], "members": ["deleted"]},
    ]
    written_users = []
    saved_groups = []
    monkeypatch.setattr(admin, "read_users", lambda: [dict(u) for u in user_rows])
    monkeypatch.setattr(admin, "write_users", lambda rows: written_users.append(rows))
    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [dict(g, members=list(g["members"])) for g in groups])
    monkeypatch.setattr(admin, "_save_perm_groups", lambda value: saved_groups.append(value))
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(auth_core, "revoke_user_tokens", lambda username: 1)

    result = admin.delete_user(
        admin.ApproveReq(username="deleted"),
        object(),
        _admin={"role": "admin"},
    )

    assert [u["username"] for u in written_users[0]] == ["active"]
    assert saved_groups == [[
        {"name": "engineers", "tabs": ["dashboard"], "members": ["active"]},
        {"name": "reviewers", "tabs": ["tracker"], "members": []},
    ]]
    assert result == {
        "ok": True,
        "removed_from_permission_groups": ["engineers", "reviewers"],
    }


def test_department_default_never_overwrites_individual_or_explicit_group_permissions():
    users = [
        {"username": "fresh", "role": "user", "department": "Process Team", "tabs": "", "permission_source": ""},
        {"username": "legacy", "role": "user", "department": "Process Team", "tabs": "inform", "permission_source": ""},
        {"username": "individual", "role": "user", "department": "Process Team", "tabs": "", "permission_source": "individual"},
        {"username": "manual", "role": "user", "department": "Process Team", "tabs": "", "permission_source": ""},
    ]
    groups = [
        {"name": "dept-default", "tabs": ["dashboard"], "members": [], "departments": ["process team"]},
        {"name": "manual-group", "tabs": ["tracker"], "members": ["manual"], "departments": []},
    ]

    changed = admin._apply_department_permission_defaults(users, groups)

    assert changed == 3
    assert (users[0]["tabs"], users[0]["permission_source"]) == ("dashboard", "department:dept-default")
    assert (users[1]["tabs"], users[1]["permission_source"]) == ("inform", "individual")
    assert (users[2]["tabs"], users[2]["permission_source"]) == ("", "individual")
    assert (users[3]["tabs"], users[3]["permission_source"]) == ("tracker", "group:manual-group")


def test_seed_admin_requires_explicit_non_default_password(monkeypatch):
    writes = []
    monkeypatch.setattr("routers.auth.read_users", lambda: [])
    monkeypatch.setattr("routers.auth.write_users", lambda rows: writes.append(rows))
    monkeypatch.delenv("FLOW_ADMIN_PW", raising=False)
    logger = type("Logger", (), {"error": lambda *args, **kwargs: None, "info": lambda *args, **kwargs: None})()
    startup.ensure_seed_admin(logger)
    assert writes == []

    monkeypatch.setenv("FLOW_ADMIN_PW", "Strong-Admin-2026!")
    startup.ensure_seed_admin(logger)
    assert len(writes) == 1 and writes[0][0]["username"] == "hol"


def test_split_view_identity_fails_closed(monkeypatch):
    assert splittable._split_view_request_user(None) == ("", "user")
    monkeypatch.setattr(splittable, "current_user", lambda request: (_ for _ in ()).throw(RuntimeError("boom")))
    assert splittable._split_view_request_user(object()) == ("", "user")


def test_diagnostic_routes_require_admin_dependency():
    source = (Path(__file__).parents[1] / "backend" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    protected = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {"runtime_roots", "deploy_info"}:
            protected[node.name] = any(
                isinstance(default, ast.Call)
                and isinstance(default.func, ast.Name)
                and default.func.id == "Depends"
                and default.args
                and isinstance(default.args[0], ast.Name)
                and default.args[0].id == "require_admin"
                for default in node.args.defaults
            )
    assert protected == {"runtime_roots": True, "deploy_info": True}


def test_background_owner_fails_closed_when_shared_lease_is_lost(monkeypatch):
    background_owner._OWNER.set()
    monkeypatch.setattr(background_owner.shared_lease, "owned", lambda name: False)
    assert background_owner.is_owner() is False
    assert not background_owner._OWNER.is_set()
