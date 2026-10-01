import json
import threading
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


def test_verified_ws_department_uses_existing_permission_group(ws_env, monkeypatch):
    from routers import admin

    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [{
        "name": "process", "tabs": ["teg", "splittable"], "members": [],
        "departments": ["Process Team"],
    }])
    ap.write_department_rules([
        {"department": "Process Team", "allow_login": True, "tabs": "dashboard"},
        {"department": "Other Team", "allow_login": True, "tabs": "lotlocation"},
    ])
    ws_env["kim"] = {"user_id": "kim", "department": "Process  Team"}
    identity = ap.get_provider("websocket").authenticate({"user_id": "kim", "token": "t"})
    assert identity.ephemeral
    assert identity.tabs == "teg,splittable"
    assert identity.claims["permission_source"] == "department:process"

    # A browser-supplied department cannot change the verified group grant.
    ws_env["kim"] = {"user_id": "kim", "department": "Other Team"}
    identity = ap.get_provider("websocket").authenticate({
        "user_id": "kim", "token": "t", "department": "Process Team",
    })
    assert identity.tabs == "lotlocation"


def test_verified_ws_department_refreshes_stored_group_without_overwriting_individual(ws_env, monkeypatch):
    from routers import admin, auth as auth_router

    rows = [
        {"username": "group.user", "role": "user", "tabs": "dashboard",
         "department": "Old Team", "permission_source": "department:old"},
        {"username": "individual.user", "role": "user", "tabs": "inform",
         "department": "Old Team", "permission_source": "individual"},
    ]
    monkeypatch.setattr(auth_router, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(auth_router, "write_users", lambda users: rows.__setitem__(slice(None), [dict(row) for row in users]))
    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [{
        "name": "new", "tabs": ["teg"], "members": [], "departments": ["New Team"],
    }])
    ws_env["group.user"] = {"user_id": "group.user", "department": "New Team"}
    ws_env["individual.user"] = {"user_id": "individual.user", "department": "New Team"}
    provider = ap.get_provider("websocket")
    group_identity = provider.authenticate({"user_id": "group.user", "token": "t"})
    individual_identity = provider.authenticate({"user_id": "individual.user", "token": "t"})
    assert (group_identity.tabs, rows[0]["tabs"], rows[0]["permission_source"]) == (
        "teg", "teg", "department:new",
    )
    assert (individual_identity.tabs, rows[1]["tabs"], rows[1]["permission_source"]) == (
        "inform", "inform", "individual",
    )
    assert [row["department"] for row in rows] == ["New Team", "New Team"]


def test_verified_ws_direct_group_member_overrides_department_default(ws_env, monkeypatch):
    from routers import admin, auth as auth_router

    rows = [{
        "username": "member.user", "role": "user", "tabs": "",
        "department": "Old Team", "permission_source": "",
    }]
    monkeypatch.setattr(auth_router, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(
        auth_router,
        "write_users",
        lambda users: rows.__setitem__(slice(None), [dict(row) for row in users]),
    )
    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [{
        "name": "direct", "tabs": ["teg"], "members": ["member.user"],
        "departments": [],
    }, {
        "name": "department-default", "tabs": ["dashboard"], "members": [],
        "departments": ["Process Team"],
    }])
    ws_env["member.user"] = {"user_id": "member.user", "department": "Process Team"}

    identity = ap.get_provider("websocket").authenticate({"user_id": "member.user", "token": "t"})

    assert identity.tabs == "teg"
    assert rows[0]["tabs"] == "teg"
    assert rows[0]["permission_source"] == "group:direct"


def test_verified_ws_department_change_clears_stale_department_default(ws_env, monkeypatch):
    from routers import admin, auth as auth_router

    rows = [{
        "username": "moved.user", "role": "user", "tabs": "dashboard",
        "department": "Old Team", "permission_source": "department:old",
    }]
    monkeypatch.setattr(auth_router, "read_users", lambda: [dict(row) for row in rows])
    monkeypatch.setattr(
        auth_router,
        "write_users",
        lambda users: rows.__setitem__(slice(None), [dict(row) for row in users]),
    )
    monkeypatch.setattr(admin, "_load_perm_groups", lambda: [{
        "name": "old", "tabs": ["dashboard"], "members": [],
        "departments": ["Old Team"],
    }])
    ws_env["moved.user"] = {"user_id": "moved.user", "department": "New Team"}

    identity = ap.get_provider("websocket").authenticate({"user_id": "moved.user", "token": "t"})

    assert identity.tabs == ""
    assert rows[0]["department"] == "New Team"
    assert rows[0]["tabs"] == ""
    assert rows[0]["permission_source"] == "department:"


def test_concurrent_admin_tabs_and_verified_ws_refresh_preserve_both_permissions(
        ws_env, monkeypatch):
    """A concurrent manual edit must survive the next verified department refresh."""
    from routers import admin, auth as auth_router

    rows = [{
        "username": "race.user", "role": "user", "tabs": "dashboard",
        "department": "Old Team", "permission_source": "department:old",
    }]
    ws_in_group_lookup = threading.Event()
    release_ws = threading.Event()
    admin_done = threading.Event()
    writes = []
    errors = []

    def read_users():
        return [dict(row) for row in rows]

    def write_users(users):
        rows[:] = [dict(row) for row in users]
        writes.append(rows[0].copy())

    def load_groups():
        if threading.current_thread().name == "ws-login":
            ws_in_group_lookup.set()
            if not release_ws.wait(timeout=3):
                raise TimeoutError("WS group lookup was not released")
        return [{
            "name": "new", "tabs": ["teg"], "members": [],
            "departments": ["New Team"],
        }]

    monkeypatch.setattr(auth_router, "read_users", read_users)
    monkeypatch.setattr(auth_router, "write_users", write_users)
    monkeypatch.setattr(admin, "read_users", read_users)
    monkeypatch.setattr(admin, "write_users", write_users)
    monkeypatch.setattr(admin, "_load_perm_groups", load_groups)
    monkeypatch.setattr(admin, "_audit", lambda *args, **kwargs: None)
    ws_env["race.user"] = {"user_id": "race.user", "department": "New Team"}

    def admin_edit():
        try:
            admin.set_tabs(
                admin.PermReq(username="race.user", tabs=["inform"]),
                SimpleNamespace(),
                _admin={"username": "hol", "role": "admin"},
            )
        except Exception as exc:
            errors.append(exc)
        finally:
            admin_done.set()

    def ws_refresh():
        try:
            identity = ap.get_provider("websocket").authenticate({
                "user_id": "race.user", "token": "t",
            })
            assert identity.tabs == "teg"
        except Exception as exc:
            errors.append(exc)

    ws_thread = threading.Thread(target=ws_refresh, name="ws-login")
    admin_thread = threading.Thread(target=admin_edit, name="admin-edit")
    ws_thread.start()
    assert ws_in_group_lookup.wait(timeout=2)
    admin_thread.start()
    # WS has read the old row and holds the mutation lock. The admin edit must
    # wait until the verified department/group write has completed.
    admin_finished_before_release = admin_done.wait(timeout=0.2)
    release_ws.set()
    threads = [ws_thread, admin_thread]
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

    assert not errors
    assert not admin_finished_before_release
    assert len(writes) == 2
    assert rows[0]["department"] == "New Team"
    assert rows[0]["tabs"] == "inform"
    assert rows[0]["permission_source"] == "individual"


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


# ── 설치본은 ID/PW 입력칸 없이 버튼만 ───────────────────────────────────
@pytest.fixture
def no_login_env(monkeypatch):
    for name in ("FLOW_WS_AUTH_URL", "FLOW_IP_LOGIN_MAP", "FLOW_PASSWORD_LOGIN_ENABLED", "FLOW_PROD"):
        monkeypatch.delenv(name, raising=False)


def _password_on() -> bool:
    return "password" in [p["name"] for p in ap.describe_providers()]


def test_password_login_is_off_in_installed_copy(no_login_env, monkeypatch):
    from core import root_profile

    monkeypatch.setattr(root_profile, "is_source_checkout", lambda: False)
    assert not _password_on()                      # setup.py 로 푼 폴더(.git 없음)
    monkeypatch.setenv("FLOW_PROD", "0")           # VM 격리 테스트 명령
    assert _password_on()
    monkeypatch.setattr(root_profile, "is_source_checkout", lambda: True)
    monkeypatch.setenv("FLOW_PROD", "1")           # flow_env.bat 운영 기동(설치 폴더에 git init 해도)
    assert not _password_on()
    monkeypatch.delenv("FLOW_PROD")
    assert _password_on()                          # 개발 체크아웃
    monkeypatch.setenv("FLOW_PROD", "1")
    monkeypatch.setenv("FLOW_PASSWORD_LOGIN_ENABLED", "1")   # 비상 스위치가 이긴다
    assert _password_on()


def test_register_is_refused_when_password_login_is_off(no_login_env, monkeypatch):
    from routers import auth as auth_router

    monkeypatch.setenv("FLOW_PROD", "1")
    with pytest.raises(HTTPException) as exc:
        auth_router.register(auth_router.RegisterReq(username="new.user", password="longpassword"))
    assert exc.value.status_code == 403


# ── 주소만 온 프레임 ───────────────────────────────────────────────────
def test_url_only_frame_is_not_used_as_user_id(ws_env):
    provider = ap.get_provider("websocket")
    for frame in ("https://sso.example/login?ticket=abc", json.dumps({"type": "redirect", "url": "https://sso.example/login"})):
        with pytest.raises(ap.WsFollowUp) as exc:
            provider.authenticate(frame)
        assert exc.value.action == "open" and exc.value.url.startswith("https://sso.example/login")
    # ID 필드에 주소가 들어 있어도 ID 로 쓰지 않는다(주소 필드가 아니면 중간 프레임 취급).
    with pytest.raises(HTTPException) as exc:
        provider.authenticate({"user": "https://sso.example/who"})
    assert exc.value.status_code == 400
    # 인사말 프레임은 400(브라우저가 다음 프레임을 기다림)
    with pytest.raises(HTTPException) as exc:
        provider.authenticate(json.dumps({"type": "hello"}))
    assert exc.value.status_code == 400


def test_url_action_browser_is_forwarded(ws_env, monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_URL_ACTION", "browser")
    with pytest.raises(ap.WsFollowUp) as exc:
        ap.get_provider("websocket").authenticate({"loginUrl": "https://sso.example/me"})
    assert exc.value.payload()["action"] == "browser"


def test_server_fetch_uses_allowlisted_response_as_verified(ws_env, monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_URL_ACTION", "server")
    monkeypatch.setenv("FLOW_WS_AUTH_FETCH_ALLOW", "https://sso.example/api/")
    fetched = {"user_id": "kim", "department": "공정기술1팀", "name": "Kim"}
    monkeypatch.setattr(ap, "_ws_fetch_url", lambda url: fetched)
    ap.write_department_rules([{"department": "공정기술", "match": "prefix", "allow_login": True, "tabs": "splittable"}])
    ident = ap.get_provider("websocket").authenticate("https://sso.example/api/who?ticket=1")
    assert (ident.username, ident.tabs) == ("kim", "splittable")


def test_server_fetch_allowlist_blocks_other_hosts(monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_FETCH_ALLOW", "https://sso.example/api/")
    assert ap._ws_fetch_allowed("https://sso.example/api/who?t=1")
    assert ap._ws_fetch_allowed("https://SSO.example:443/api/x")
    for url in ("https://evil.example/api/who", "http://sso.example/api/who", "https://sso.example/admin",
                "https://sso.example@evil.example/api/who", "https://sso.example:8443/api/who",
                "https://sso.example/api/../admin", "https://sso.example/api/%2e%2e/admin"):
        assert not ap._ws_fetch_allowed(url), url
    with pytest.raises(HTTPException) as exc:
        ap._ws_fetch_url("https://evil.example/api/who")      # 네트워크 요청 전에 거부
    assert exc.value.status_code == 403
    monkeypatch.delenv("FLOW_WS_AUTH_FETCH_ALLOW")
    with pytest.raises(HTTPException) as exc:
        ap._ws_fetch_url("https://sso.example/api/who")
    assert exc.value.status_code == 503


# ── 설정에 고정된 사용자 ID (하드코딩) ───────────────────────────────────
def test_user_id_fixed_in_config_is_rejected(ws_env, monkeypatch):
    monkeypatch.setenv("FLOW_WS_AUTH_SEND", json.dumps({"cmd": "info", "sAMAccountName": "example.user"}))
    provider = ap.get_provider("websocket")
    for uid in ("example.user", "EXAMPLE.USER", "example.user@corp.example"):
        with pytest.raises(HTTPException) as exc:
            provider.authenticate({"user_id": uid, "token": "t"})
        assert exc.value.status_code == 403 and "고정" in exc.value.detail
    assert provider.authenticate({"user_id": "other.user", "token": "t"}).username == "other.user"


# ── 진단 보고는 값을 담지 않는다 ────────────────────────────────────────
def test_frame_report_masks_values():
    secret = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJleGFtcGxlIn0.c2lnbmF0dXJlLXZhbHVl"
    frame = json.dumps({"ad": {"mail": "example.user@corp.example", "department": "공정기술1팀"},
                        "token": secret, "next": "https://sso.example/cb?ticket=abc123secret"})
    report = ap.ws_frame_report(frame)
    text = json.dumps(report, ensure_ascii=False)
    assert report["format"] == "json"
    assert report["matched"]["user"] == "ad.mail" and report["matched"]["token"] == "token"
    assert report["matched"]["department"] == "ad.department"
    for raw in (secret, "example.user@", "abc123secret", "공정기술1팀"):
        assert raw not in text
    assert "JWT" in text and "sso.example/cb?ticket=…" in text
    plain = ap.ws_frame_report("https://sso.example/login?ticket=zzz999")
    assert plain["format"] == "text" and plain["matched"] == {"url": "*"}
    assert "zzz999" not in json.dumps(plain, ensure_ascii=False)


# ── 재검증 방식 ────────────────────────────────────────────────────────
_REAL_VERIFY = ap._ws_verify


def test_ad_frame_without_token_cannot_log_in(monkeypatch):
    """현장에서 본 형태(합성 값): sAMAccountName·client_id·ad{mail…}, 토큰 없음.

    ID 는 sAMAccountName 으로 읽히지만, 서버가 확인할 토큰이 없으니 로그인은 거부된다."""
    frame = json.dumps({"sAMAccountName": "example.user", "client_id": "c-0001",
                        "ad": {"mail": "example.user@corp.example", "department": "공정기술1팀", "name": "예시"}})
    assert ap._ws_credentials(ap._ws_parse(frame)) == ("example.user", "")
    report = ap.ws_frame_report(frame)
    assert report["matched"]["user"] == "sAMAccountName" and "token" not in report["matched"]
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_URL", "https://auth.example/verify")
    monkeypatch.delenv("FLOW_WS_AUTH_VERIFY", raising=False)
    with pytest.raises(HTTPException) as exc:
        _REAL_VERIFY("example.user", "")
    assert exc.value.status_code == 401
    # client_id 를 인증서버에 되물을 API 가 있을 때만 토큰으로 지정한다.
    monkeypatch.setenv("FLOW_WS_AUTH_TOKEN_FIELDS", "client_id")
    assert ap._ws_credentials(ap._ws_parse(frame)) == ("example.user", "c-0001")


def test_http_verify_get_with_bearer_header(monkeypatch):
    import urllib.request

    seen = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True, "user": {"id": "kim"}}).encode()

    def fake_urlopen(req, timeout=0):
        seen.update(url=req.full_url, method=req.get_method(), auth=req.get_header("Authorization"), data=req.data)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_URL", "https://auth.example/me?t={token}")
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_METHOD", "GET")
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_HEADERS", json.dumps({"Authorization": "Bearer {token}"}))
    monkeypatch.delenv("FLOW_WS_AUTH_VERIFY", raising=False)
    assert _REAL_VERIFY("", "a b&c")[0] == "kim"
    assert seen["method"] == "GET" and seen["data"] is None
    assert seen["url"] == "https://auth.example/me?t=a%20b%26c" and seen["auth"] == "Bearer a b&c"

    # POST JSON 템플릿은 따옴표가 든 토큰도 올바른 JSON 으로 보낸다.
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_METHOD", "POST")
    monkeypatch.delenv("FLOW_WS_AUTH_VERIFY_HEADERS")
    _REAL_VERIFY("", 'to"k\\en')
    assert json.loads(seen["data"]) == {"token": 'to"k\\en'}


def test_ws_verify_reads_past_greeting_frames(monkeypatch):
    import sys
    import types

    frames = [json.dumps({"type": "hello"}), b'{"result": "ok", "data": {"id": "lee"}}']
    sent = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send(self, payload):
            sent.append(payload)

        def recv(self, timeout=0):
            return frames.pop(0)

    client = types.ModuleType("websockets.sync.client")
    client.connect = lambda url, **kw: _Conn()
    monkeypatch.setitem(sys.modules, "websockets", types.ModuleType("websockets"))
    monkeypatch.setitem(sys.modules, "websockets.sync", types.ModuleType("websockets.sync"))
    monkeypatch.setitem(sys.modules, "websockets.sync.client", client)
    monkeypatch.setenv("FLOW_WS_AUTH_VERIFY_URL", "ws://auth.example/verify")
    monkeypatch.delenv("FLOW_WS_AUTH_VERIFY", raising=False)
    user, data = _REAL_VERIFY("lee", "tok")
    assert user == "lee" and data["data"]["id"] == "lee" and json.loads(sent[0]) == {"token": "tok"}


# ── 라우터: 202 다음 할 일 · 진단 응답 · 관리자 확인용 기록 ─────────────
def test_ws_login_route_follow_up_and_debug(ws_env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers import auth as auth_router

    app = FastAPI()
    app.include_router(auth_router.router)
    client = TestClient(app)
    monkeypatch.setenv("FLOW_WS_AUTH_DEBUG", "1")

    r = client.post("/api/auth/sso/ws/login", json={"message": "https://sso.example/login?ticket=s3cr3t", "debug": True, "step": 1})
    assert r.status_code == 202
    body = r.json()
    assert body["action"] == "open" and body["url"].startswith("https://sso.example/login")
    assert "s3cr3t" not in json.dumps(body["ws_debug"], ensure_ascii=False)

    r = client.post("/api/auth/sso/ws/login", json={"message": json.dumps({"type": "hello"}), "debug": True, "step": 2})
    assert r.status_code == 400 and "ws_debug" in r.json() and r.json()["detail"]
    r = client.post("/api/auth/sso/ws/login", json={"message": json.dumps({"type": "hello"})})
    assert r.status_code == 400 and "ws_debug" not in r.json()

    monkeypatch.setattr(ap, "start_session", lambda ident, **kw: {"ok": True, "username": ident.username, "role": ident.role})
    r = client.post("/api/auth/sso/ws/login", json={"message": json.dumps({"user_id": "lee", "token": "tok-9"}), "step": 3})
    assert r.status_code == 200 and r.json()["username"] == "lee"

    entries = ap.read_ws_probe()
    assert [e["status"] for e in entries] == [202, 400, 400, 200]
    assert "s3cr3t" not in json.dumps(entries, ensure_ascii=False)
    assert "tok-9" not in json.dumps(entries, ensure_ascii=False)
    assert ap.ws_config_summary()["url_action"] == "open"
