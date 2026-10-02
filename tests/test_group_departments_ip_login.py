import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import auth as auth_core
from core import auth_providers as ap
from routers import groups


# ── 그룹: 사람이 아니라 부서 기준 ─────────────────────────────────────
@pytest.fixture
def dept_users(monkeypatch):
    users = {
        "kim": {"username": "kim", "department": "공정기술 1팀", "status": "approved", "name": "Kim"},
        "lee": {"username": "lee", "department": "공정기술  1팀", "status": "approved"},
        "park": {"username": "park", "department": "품질팀", "status": "approved"},
        "wait": {"username": "wait", "department": "공정기술 1팀", "status": "pending"},
        "testbot": {"username": "testbot", "department": "공정기술 1팀", "status": "approved"},
    }
    monkeypatch.setattr(groups, "_load_users_by_name", lambda: users)
    monkeypatch.setattr("routers.auth._users_csv_sig", lambda: None)
    return users


def test_department_members_are_resolved_and_manual_kept(dept_users):
    g = groups._resolve_members({"id": "g", "departments": ["공정기술 1팀"], "members": ["park"]})
    assert g["department_members"] == ["kim", "lee"]          # 대기·test 계정 제외, 공백·대소문자 무시
    assert g["manual_members"] == ["park"]
    assert g["members"] == ["kim", "lee", "park"]


def test_save_writes_only_manual_members(dept_users, monkeypatch):
    saved = []
    monkeypatch.setattr(groups, "save_json", lambda fp, data, **kw: saved.append(data))
    g = groups._resolve_members({"id": "g", "departments": ["품질팀"], "members": ["kim"]})
    groups._save([g])
    row = saved[-1][0]
    assert row["members"] == ["kim"] and row["departments"] == ["품질팀"]
    assert "department_members" not in row and "manual_members" not in row

    # mail_groups 처럼 members 를 직접 고치는 예전 호출부: 부서 소속을 뺀 나머지가 개별 멤버.
    g["members"] = ["park", "lee"]
    groups._save([g])
    assert saved[-1][0]["members"] == ["lee"]


def test_user_group_ids_follow_department(dept_users, monkeypatch):
    raw = [{"id": "g1", "owner": "x", "departments": ["품질팀"], "members": []}]
    monkeypatch.setattr(groups, "load_json", lambda fp, default: [dict(r) for r in raw])
    monkeypatch.setattr(groups, "_MIGRATION_DONE", True)
    assert groups.user_group_ids("park", "user") == {"g1"}
    assert groups.user_group_ids("kim", "user") == set()


# ── 접속 IP 로그인 ────────────────────────────────────────────────────
@pytest.fixture
def ip_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOW_WS_AUTH_URL", raising=False)
    monkeypatch.delenv("FLOW_PASSWORD_LOGIN_ENABLED", raising=False)
    monkeypatch.setenv("FLOW_WS_AUTH_USER_MAP", json.dumps({"example.user": "hol"}))
    monkeypatch.setenv("FLOW_IP_LOGIN_MAP", json.dumps({"127.0.0.1": "example.user"}))
    monkeypatch.setenv("FLOW_DATA_KEY_FILE", str(tmp_path / "key" / ".flow_data.key"))
    monkeypatch.setattr(auth_core, "PATHS", SimpleNamespace(data_root=tmp_path / "data", app_root=tmp_path))
    monkeypatch.setattr("routers.auth.read_users", lambda: [{"username": "hol", "role": "admin", "tabs": ""}])


def test_ip_login_keeps_password_and_maps_the_company_id(ip_env):
    names = [p["name"] for p in ap.describe_providers()]
    assert "ip" in names and "password" in names
    ident = ap.get_provider("ip").authenticate("::ffff:127.0.0.1")
    # 사내 ID 는 websocket 로그인과 같은 매핑(FLOW_WS_AUTH_USER_MAP → hol 관리자)을 탄다.
    assert (ident.username, ident.role, ident.provider) == ("hol", "admin", "ip")
    assert ident.claims["ws_user"] == "example.user"


def test_ip_login_rejects_unlisted_ip(ip_env):
    with pytest.raises(HTTPException) as exc:
        ap.get_provider("ip").authenticate("10.0.0.9")
    assert exc.value.status_code == 403


def test_ip_login_off_without_map(ip_env, monkeypatch):
    monkeypatch.delenv("FLOW_IP_LOGIN_MAP")
    names = [p["name"] for p in ap.describe_providers()]
    assert "ip" not in names and "password" in names
