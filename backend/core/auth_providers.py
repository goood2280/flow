"""core/auth_providers.py — 로그인 방식과 세션 발급을 분리하는 인증 레이어.

배경
────
v8.4.6 이후 세션 토큰은 `core/auth.py` 가 발급/검증하고, 로그인은 `routers/auth.py`
가 users.csv 를 직접 읽어 처리했다. 즉 "비밀번호 검증"과 "세션 시작"이 한 함수
(`/api/auth/login`) 안에 붙어 있어, SSO(OIDC/SAML) 를 붙이려면 그 함수를 갈라야 했다.

이 모듈은 그 두 가지를 갈라 놓는다:

  1) **인증 (누구인가)** — `AuthProvider.authenticate()` 가 자격증명을 검증하고
     `AuthIdentity` 를 돌려준다. 비밀번호든 SSO 든 여기까지만 다르다.
  2) **세션 시작 (토큰을 준다)** — `start_session(identity)` 하나뿐이다. 로그인
     방식을 모른다. 승인 상태 확인 → tabs 계산 → 토큰 발급 → 감사 로그.

따라서 SSO 를 붙일 때 건드릴 곳은 "새 provider 파일 + callback 라우터" 두 개이고,
세션/토큰/미들웨어/프런트 규약은 **전혀 바뀌지 않는다.**

SSO provider 추가 방법
──────────────────────
    class OidcAuthProvider(AuthProvider):
        name = "oidc"
        kind = "sso"
        label = "SSO"
        def enabled(self) -> bool:
            return bool(os.environ.get("FLOW_OIDC_CLIENT_ID"))
        def start_url(self) -> str:
            return "/api/auth/sso/oidc/start"
        def authenticate(self, credential):        # credential = callback 파라미터
            claims = _exchange_code(credential["code"])
            return self.resolve_identity(
                username=claims["preferred_username"], claims=claims,
            )

    register_provider(OidcAuthProvider())

그리고 `/api/auth/sso/oidc/{start,callback}` 라우터에서 callback 이
`start_session(provider.authenticate(...))` 를 호출하면 끝이다. `/api/auth/sso/*`
는 `core.auth.AUTH_EXEMPT_API_PREFIXES` 로 이미 미들웨어 인증에서 면제돼 있다.

계정 프로비저닝
───────────────
SSO 사용자도 users.csv 에 행이 있어야 role/tabs/status 를 관리할 수 있다.
`resolve_identity()` 가 기존 계정을 찾아 role/tabs 를 채우고, 계정이 없으면
`auto_provision` 설정에 따라 `status="pending"` 행을 만들거나 403 을 낸다.
어느 쪽이든 **관리자 승인 절차는 비밀번호 로그인과 동일하다.**
"""
from __future__ import annotations

import datetime
import os
import threading
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Iterable

from fastapi import HTTPException

from core import auth as auth_core
from core.audit import record_user as _audit_user

# 신규 계정(SSO 자동 프로비저닝 포함)은 관리자가 권한을 부여하기 전까지 접근 가능한 탭이 없다.
DEFAULT_TABS = ""

# 하위 호환 export. 빈 tabs는 이제 명시적으로 "권한 없음"을 뜻한다.
LEGACY_LOGIN_TABS = ""


def _with_users_mutation_lock(func):
    """Serialize a complete users.csv read/modify/write with WS login refreshes."""
    @wraps(func)
    def guarded(*args, **kwargs):
        from routers import auth as auth_router

        with auth_router.USERS_MUTATION_LOCK:
            return func(*args, **kwargs)

    return guarded


@dataclass(frozen=True)
class AuthIdentity:
    """인증에 성공한 주체. 어떤 provider 로 인증했는지만 다르고 나머지는 동일하다."""

    username: str
    provider: str = "password"
    role: str = "user"
    status: str = "approved"
    tabs: str = ""
    name: str = ""
    email: str = ""
    # provider 가 넘겨준 원본 클레임(OIDC id_token, SAML assertion attribute 등).
    # 세션 메타에 그대로 저장되므로 비밀/토큰 값을 넣지 않는다.
    claims: dict = field(default_factory=dict)
    # users.csv 에 행이 없는(저장하지 않는) 사용자면 True — 탭 권한을 세션 토큰에 담는다.
    ephemeral: bool = False


class AuthProvider:
    """인증 방식 하나. 하위 클래스는 `authenticate()` 만 구현하면 된다."""

    name: str = ""
    kind: str = "password"   # "password" | "sso"
    label: str = ""

    def enabled(self) -> bool:
        return True

    def start_url(self) -> str:
        """SSO redirect 진입 URL. 비밀번호 provider 는 없음("")."""
        return ""

    def authenticate(self, credential: Any) -> AuthIdentity:
        raise NotImplementedError

    # ── 공통 헬퍼 ────────────────────────────────────────────────────
    @_with_users_mutation_lock
    def resolve_identity(
        self,
        username: str,
        *,
        claims: dict | None = None,
        auto_provision: bool = False,
        name: str = "",
        email: str = "",
    ) -> AuthIdentity:
        """users.csv 의 계정 행을 identity 로 변환. SSO provider 가 재사용한다.

        계정이 없을 때 `auto_provision=True` 면 `status="pending"` 행을 만들어
        관리자 승인 대기 상태로 둔다 (비밀번호 없이 만들어지므로 password_hash 는
        빈 값 — `verify_password("", "")` 는 항상 False 라 비번 로그인은 불가).
        """
        # core → routers 역방향 import 회피 + 테스트의 monkeypatch 가 먹도록 지연 import.
        from routers import auth as auth_router

        login_id = (username or "").strip()
        if not login_id:
            raise HTTPException(401, "Invalid credentials")

        users = auth_router.read_users()
        identity_claims = dict(claims or {})

        def _claim_label(value: Any) -> str:
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    label = " ".join(str(item or "").strip().split())
                    if label:
                        return label
                return ""
            return " ".join(str(value or "").strip().split())

        sso_id = _claim_label(identity_claims.get("sub")) if self.kind == "sso" else ""
        department = ""
        if self.kind == "sso":
            for claim_name in ("department", "department_name", "dept", "org_name", "org"):
                department = _claim_label(identity_claims.get(claim_name))
                if department:
                    break
        # `hong` / `hong@corp.com` 은 같은 계정 (routers.auth.find_user_rows).
        row = auth_router._find_user_by_username(users, login_id)

        changed = False
        if row is None:
            if not auto_provision:
                raise HTTPException(403, "No local account for this identity")
            row = {
                # 비밀번호 가입과 같은 규칙으로 사내 id 형태로 저장한다.
                "username": auth_core.canonical_username(login_id) or login_id,
                "password_hash": "",
                "role": "user",
                "status": "pending",
                "created": datetime.datetime.now().isoformat(),
                "last_login": "",
                "tabs": DEFAULT_TABS,
                "email": (email or "").strip(),
                "name": (name or "").strip(),
                "sso_id": sso_id,
                "department": department,
                "permission_source": "",
            }
            users.append(row)
            changed = True
        elif self.kind == "sso":
            # Refresh non-secret SSO directory metadata on every successful
            # callback. Missing claims do not erase a previously known value.
            for field_name, value in (("sso_id", sso_id), ("department", department)):
                if value and str(row.get(field_name) or "") != value:
                    row[field_name] = value
                    changed = True
            if name and not str(row.get("name") or "").strip():
                row["name"] = str(name).strip()
                changed = True
            if email and not str(row.get("email") or "").strip():
                row["email"] = str(email).strip()
                changed = True

        if self.kind == "sso":
            # Delayed import keeps the provider independent at startup while
            # reusing the Admin permission-group source of truth at login time.
            from routers import admin as admin_router

            if admin_router.apply_sso_department_permissions(users, str(row.get("username") or login_id)):
                changed = True

        if changed:
            auth_router.write_users(users)

        return AuthIdentity(
            username=row.get("username") or login_id,
            provider=self.name,
            role=row.get("role", "user") or "user",
            status=row.get("status", "") or "",
            tabs=row.get("tabs", "") or "",
            name=row.get("name", "") or "",
            email=row.get("email", "") or "",
            claims=identity_claims,
        )

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label or self.name,
            "start_url": self.start_url(),
        }


class PasswordAuthProvider(AuthProvider):
    """users.csv + PBKDF2 기반 기존 로그인. 동작은 v8.4.6 과 동일하다."""

    name = "password"
    kind = "password"
    label = "ID / PW"

    def enabled(self) -> bool:
        raw = os.environ.get("FLOW_PASSWORD_LOGIN_ENABLED")
        if raw is None or str(raw).strip() == "":
            return _password_login_default()
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}

    @_with_users_mutation_lock
    def authenticate(self, credential: Any) -> AuthIdentity:
        from routers import auth as auth_router

        username = str(getattr(credential, "username", "") or "")
        password = str(getattr(credential, "password", "") or "")

        users = auth_router.read_users()
        # 후보는 "같은 계정을 가리키는 행" 전부다 — 가입 때 `hong` 과
        # `hong@corp.com` 이 섞여 들어왔기 때문에 표기가 달라도 로그인은 통과해야
        # 한다. 정규화 이전에 만들어진 중복 계정(둘 다 존재)까지 고려해, 비밀번호가
        # 맞는 행을 고른다. 후보 순서는 정확 일치 우선(find_user_rows).
        candidates = auth_router.find_user_rows(users, username)
        for u in candidates:
            ok, needs_rehash = auth_core.verify_password(password, u.get("password_hash", ""))
            if not ok:
                continue
            # 레거시 sha256 해시 자동 업그레이드 (투명) — 승인 확인보다 앞서 두어
            # 기존 동작 순서를 유지한다.
            if needs_rehash:
                u["password_hash"] = auth_core.hash_password(password)
                auth_router.write_users(users)
            return AuthIdentity(
                username=u["username"],
                provider=self.name,
                role=u.get("role", "user") or "user",
                status=u.get("status", "") or "",
                tabs=u.get("tabs", "") or "",
                name=u.get("name", "") or "",
                email=u.get("email", "") or "",
            )
        # 계정 없음도 자격증명 오류와 같은 응답 — 계정 존재 여부를 노출하지 않는다.
        raise HTTPException(401, "Invalid credentials")


def _password_login_default() -> bool:
    """설치본·운영에서도 ID/PW 입력을 기본 제공한다. 명시한 0은 우선한다."""
    return True


# ── 레지스트리 ────────────────────────────────────────────────────────
_PROVIDERS: dict[str, AuthProvider] = {}


def register_provider(provider: AuthProvider) -> AuthProvider:
    if not provider.name:
        raise ValueError("AuthProvider.name is required")
    _PROVIDERS[provider.name] = provider
    return provider


def get_provider(name: str) -> AuthProvider:
    provider = _PROVIDERS.get((name or "").strip().lower())
    if provider is None or not provider.enabled():
        raise HTTPException(404, f"Unknown auth provider: {name}")
    return provider


def list_providers() -> list[AuthProvider]:
    return [p for p in _PROVIDERS.values() if p.enabled()]


def describe_providers() -> list[dict]:
    return [p.describe() for p in list_providers()]


register_provider(PasswordAuthProvider())


# ── websocket 로그인 ──────────────────────────────────────────────────
# 브라우저가 사내 websocket 인증서버(FLOW_WS_AUTH_URL)에 직접 접속해 받은 메시지를
# /api/auth/sso/ws/login 으로 넘긴다. Flow 는 메시지의 사내 ID·토큰을 꺼내
# **서버 쪽에서 인증서버에 다시 확인**한 뒤에만 세션을 연다 — 브라우저가 보낸
# ID 를 그대로 믿으면 누구나 관리자 ID 를 적어 들어올 수 있다.
#
# 설정(모두 환경변수, 기본값은 코드):
#   FLOW_WS_AUTH_URL            브라우저가 접속할 ws(s):// 주소. 비우면 websocket 로그인 꺼짐.
#   FLOW_WS_AUTH_SEND           접속 직후 브라우저가 보낼 메시지(문자열/JSON). 비우면 보내지 않음.
#   FLOW_WS_AUTH_USER_FIELDS    메시지에서 사내 ID 를 찾을 필드(쉼표, 점 경로 가능)
#   FLOW_WS_AUTH_TOKEN_FIELDS   메시지에서 토큰을 찾을 필드
#   FLOW_WS_AUTH_VERIFY         ws | http | none  (기본: VERIFY_URL 이 있으면 ws/http, 없으면 거부)
#   FLOW_WS_AUTH_VERIFY_URL     서버 검증 주소(ws:// 또는 http(s)://). 비우면 ws 검증은 FLOW_WS_AUTH_URL.
#   FLOW_WS_AUTH_VERIFY_SEND    서버 검증 때 보낼 메시지 템플릿. {token}·{user} 치환. 기본 {"token": "{token}"}
#   FLOW_WS_AUTH_TRUST_CLIENT   1 이면 검증 없이 브라우저 메시지를 믿는다(폐쇄망 임시용, 권장하지 않음)
#   FLOW_WS_AUTH_USER_MAP       사내 ID → Flow 계정 매핑 JSON. 예) {"example.user": "hol"}. 기본 없음 —
#                               실제 사번은 현장 설정(flow_env.local.bat)에만 둔다(공개 저장소).
#   FLOW_WS_AUTH_DEFAULT_TABS   users.csv 에 없는 사용자의 탭 권한. 기본 __all_user__(관리자 탭 제외 전부)
#   FLOW_WS_AUTH_URL_FIELDS     ID·토큰 없이 주소만 온 프레임에서 주소를 찾을 필드. 문자열 프레임이 http(s):// 면 그 자체.
#   FLOW_WS_AUTH_URL_ACTION     주소만 온 프레임의 처리. open(기본: 브라우저가 인증 창을 열고 같은 연결에서 다음
#                               메시지를 기다림) | server(Flow 서버가 FETCH_ALLOW 안의 주소를 직접 GET 해서 그 응답으로
#                               로그인) | browser(브라우저가 쿠키·Windows 인증으로 GET 한 응답을 다시 넘김 → 재검증)
#   FLOW_WS_AUTH_FETCH_ALLOW    server 방식에서 읽어도 되는 주소 접두어(쉼표). 비면 server 방식은 거부.
#   FLOW_WS_AUTH_VERIFY_METHOD  http 재검증 방식 POST(기본) | GET.  VERIFY_URL 에도 {token}·{user} 치환.
#   FLOW_WS_AUTH_VERIFY_HEADERS http 재검증 헤더 JSON. 예) {"Authorization": "Bearer {token}"}
#   FLOW_WS_AUTH_VERIFY_MAX_FRAMES  ws 재검증에서 ID 가 든 응답을 기다릴 최대 프레임 수. 기본 3
#   FLOW_WS_AUTH_DEBUG          1 이면 받은 프레임의 모양(키 구조·값 종류, 값 자체는 가림)과 서버 판정을
#                               FLOW_DATA_ROOT/logs/ws_login_probe.jsonl 에 남긴다(관리자 GET /api/auth/sso/ws/probe).
#                               다른 사람 PC 에서 무엇이 왔는지 볼 때만 잠깐 켠다. 기본 꺼짐.
#
# 설정(URL·SEND·VERIFY_*)에 사용자 ID 를 적어 두면 누가 눌러도 그 사람으로 들어간다. 확인된 ID 가
# 설정 문자열에 그대로 있으면 로그인을 막는다(_ws_reject_fixed_user).
_WS_DEFAULT_USER_FIELDS = "user_id,userId,userid,username,user_name,user,loginId,login_id,sAMAccountName,ad.sAMAccountName,id,empNo,emp_no,sabun,sub,data.user_id,data.userId,data.loginId,data.sAMAccountName,data.id,user.loginId,user.sAMAccountName,user.id,ad.loginId,ad.user_id,ad.userId,ad.id,ad.mail"
_WS_DEFAULT_TOKEN_FIELDS = "token,access_token,accessToken,ticket,session,sessionId,session_id,data.token,data.ticket"
_WS_DEFAULT_USER_MAP: dict[str, str] = {}
_WS_DEFAULT_DEPT_FIELDS = "department,dept,deptName,dept_name,deptNm,orgName,org_name,org,team,data.department,data.dept,user.department,ad.department"
_WS_DEFAULT_NAME_FIELDS = "displayName,display_name,name,userName,user_name,korName,kor_name,data.displayName,data.name,user.displayName,user.name,ad.displayName,ad.name"
_WS_DEFAULT_EMAIL_FIELDS = "mail,email,emailAddress,email_address,data.mail,data.email,user.mail,user.email,ad.mail,ad.email"
_WS_DEFAULT_URL_FIELDS = "url,redirect,redirectUrl,redirect_url,redirectUri,redirect_uri,loginUrl,login_url,authUrl,auth_url,href,location,link,data.url,data.redirectUrl,data.loginUrl,data.authUrl"
_WS_URL_ACTIONS = ("open", "server", "browser")
_WS_FETCH_MAX_BYTES = 256 * 1024


# ── 부서별 로그인·권한 규칙 (관리자 편집, flow-data/auth/department_rules.json) ──
# [{department, match: exact|prefix, allow_login: bool, tabs: "a,b" | "__all_user__"}]
# 규칙이 하나도 없으면(이사 직후) 모두 기본 탭으로 로그인된다. 하나라도 있으면
# 허용 규칙에 맞는 부서만 로그인된다. 여러 규칙이 맞으면 탭은 합친다.
def _department_rules_path():
    return auth_core.PATHS.data_root / "auth" / "department_rules.json"


def read_department_rules() -> list[dict]:
    import json

    fp = _department_rules_path()
    try:
        data = json.loads(fp.read_text("utf-8")) if fp.is_file() else []
    except Exception:
        data = []
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []


def write_department_rules(rules: list[dict]) -> list[dict]:
    import json

    cleaned = []
    seen = set()
    for raw in rules or []:
        if not isinstance(raw, dict):
            continue
        dept = str(raw.get("department") or "").strip()
        if not dept:
            continue
        match = str(raw.get("match") or "exact").strip().lower()
        match = match if match in {"exact", "prefix"} else "exact"
        key = (dept.casefold(), match)
        if key in seen:
            raise HTTPException(400, f"부서 규칙이 중복됩니다: {dept}")
        seen.add(key)
        allow = raw.get("allow_login", True)
        allow = allow if isinstance(allow, bool) else str(allow).strip().lower() not in {"0", "false", "no", "n", "x", "거부", "불가"}
        tabs = str(raw.get("tabs") or "").strip()
        cleaned.append({"department": dept[:200], "match": match, "allow_login": allow, "tabs": tabs[:2000]})
    fp = _department_rules_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(fp)
    return cleaned


def _normalize_tab_list(raw: str) -> list[str]:
    if raw.strip() in {"", "__all_user__"}:
        return sorted(auth_core.GRANTABLE_TAB_IDS)
    tabs, _ = auth_core.parse_tab_tokens(raw)
    return tabs


def department_access(department: str) -> tuple[bool, str]:
    """(로그인 허용, 탭 권한) — 부서 규칙으로 정한다."""
    rules = read_department_rules()
    if not rules:
        return True, _ws_default_tabs()
    dept = str(department or "").strip().casefold()
    hits = []
    for rule in rules:
        target = str(rule.get("department") or "").strip().casefold()
        if not target or not dept:
            continue
        if (rule.get("match") == "prefix" and dept.startswith(target)) or dept == target:
            hits.append(rule)
    if any(r.get("allow_login") is False for r in hits):
        return False, ""
    allowed = [r for r in hits if r.get("allow_login", True)]
    if not allowed:
        return False, ""
    tabs: list[str] = []
    for rule in allowed:
        for tab in _normalize_tab_list(str(rule.get("tabs") or "")):
            if tab not in tabs:
                tabs.append(tab)
    return True, ",".join(tabs)


# ── 관리자·대리인 프로필 (이름·메일·부서) — 암호화 저장 ─────────────────
# flow-data/auth/people.enc (Fernet). 키는 데이터 폴더 밖: FLOW_DATA_KEY 환경변수,
# 없으면 앱 폴더의 .flow_data.key(처음 한 번 생성). 데이터 폴더만 복사해 가도
# 키 없이는 읽을 수 없고, 복호화는 로그인한 사용자의 API 요청으로만 한다.
def _people_path():
    return auth_core.PATHS.data_root / "auth" / "people.enc"


def _data_key() -> bytes:
    from cryptography.fernet import Fernet

    env = str(os.environ.get("FLOW_DATA_KEY", "") or "").strip()
    if env:
        return env.encode("ascii")
    from pathlib import Path

    key_file = Path(str(os.environ.get("FLOW_DATA_KEY_FILE", "") or "").strip() or (auth_core.PATHS.app_root / ".flow_data.key"))
    if key_file.is_file():
        return key_file.read_bytes().strip()
    key = Fernet.generate_key()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_bytes(key)
    return key


PEOPLE_LOCK = threading.RLock()


def read_people(*, strict: bool = False) -> dict:
    import json

    fp = _people_path()
    if not fp.is_file():
        return {}
    try:
        from cryptography.fernet import Fernet

        people = json.loads(Fernet(_data_key()).decrypt(fp.read_bytes()).decode("utf-8"))
        if not isinstance(people, dict):
            raise ValueError("Invalid people store")
        return people
    except Exception:
        if strict:
            raise HTTPException(503, "관리자 연락처를 읽을 수 없습니다. 암호화 키를 확인하세요.")
        return {}


def _write_people(people: dict) -> None:
    import json
    from cryptography.fernet import Fernet

    fp = _people_path()
    fp.parent.mkdir(parents=True, exist_ok=True)
    token = Fernet(_data_key()).encrypt(json.dumps(people, ensure_ascii=False).encode("utf-8"))
    tmp = fp.with_suffix(".tmp")
    tmp.write_bytes(token)
    tmp.replace(fp)


def update_manual_contact(username: str, field_name: str, value: str) -> bool:
    """Keep legacy contact editors connected to explicitly registered profiles."""
    if field_name not in {"name", "email"}:
        raise ValueError("Unknown contact field")
    with PEOPLE_LOCK:
        people = read_people(strict=True)
        profile = people.get(username, {})
        if not profile.get("manual"):
            return False
        profile[field_name] = value
        profile["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        _write_people(people)
        return True


def _is_manager(username: str, role: str) -> bool:
    if role == "admin":
        return True
    try:
        admins = auth_core.get_page_admins() or {}
    except Exception:
        admins = {}
    return any(username in (users or []) for users in admins.values())


def _remember_manager_profile(identity: "AuthIdentity", department: str) -> None:
    """관리자·페이지 대리인만 이름·메일·부서를 암호화해 남긴다(일반 사용자는 저장 안 함)."""
    if not _is_manager(identity.username, identity.role):
        return
    if not (identity.name or identity.email or department):
        return
    try:
        with PEOPLE_LOCK:
            people = read_people(strict=True)
            previous = people.get(identity.username, {})
            # Explicit administrator contact edits survive later SSO logins.
            if previous.get("manual"):
                return
            people[identity.username] = {
                "name": identity.name or previous.get("name", ""),
                "email": identity.email or previous.get("email", ""),
                "department": department or previous.get("department", ""),
                "updated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            _write_people(people)
    except Exception:
        pass


def _ws_auth_url() -> str:
    return str(os.environ.get("FLOW_WS_AUTH_URL", "") or "").strip()


def _ws_env_list(name: str, default: str) -> list[str]:
    raw = str(os.environ.get(name, "") or "").strip() or default
    return [part.strip() for part in raw.split(",") if part.strip()]


def _ws_user_map() -> dict[str, str]:
    import json

    raw = str(os.environ.get("FLOW_WS_AUTH_USER_MAP", "") or "").strip()
    if not raw:
        return dict(_WS_DEFAULT_USER_MAP)
    try:
        data = json.loads(raw)
    except ValueError:
        return dict(_WS_DEFAULT_USER_MAP)
    return {str(k).strip().casefold(): str(v).strip() for k, v in (data or {}).items() if str(k).strip() and str(v).strip()}


def _ws_parse(message: Any) -> Any:
    import json

    if isinstance(message, (dict, list)):
        return message
    text = str(message or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        return text


def _ws_pick_path(data: Any, fields: list[str]) -> tuple[str, str]:
    """(값, 찾은 경로). 점 경로 지원. 문자열 메시지면 그 자체를 값으로 본다(경로 "*")."""
    if isinstance(data, str):
        return (data.strip(), "*") if data.strip() else ("", "")
    if not isinstance(data, dict):
        return "", ""
    for path in fields:
        cur: Any = data
        for part in path.split("."):
            if isinstance(cur, dict):
                hit = next((v for k, v in cur.items() if str(k).casefold() == part.casefold()), None)
                cur = hit
            else:
                cur = None
                break
        if isinstance(cur, (str, int)) and not isinstance(cur, bool) and str(cur).strip():
            return str(cur).strip(), path
    return "", ""


def _ws_pick(data: Any, fields: list[str]) -> str:
    """메시지에서 필드 값을 찾는다(점 경로 지원). 문자열 메시지면 그 자체를 값으로 본다."""
    return _ws_pick_path(data, fields)[0]


def _looks_like_url(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def _ws_fields(kind: str) -> list[str]:
    defaults = {
        "user": _WS_DEFAULT_USER_FIELDS, "token": _WS_DEFAULT_TOKEN_FIELDS,
        "department": _WS_DEFAULT_DEPT_FIELDS, "name": _WS_DEFAULT_NAME_FIELDS,
        "email": _WS_DEFAULT_EMAIL_FIELDS, "url": _WS_DEFAULT_URL_FIELDS,
    }
    env = {"department": "FLOW_WS_AUTH_DEPT_FIELDS"}.get(kind, f"FLOW_WS_AUTH_{kind.upper()}_FIELDS")
    return _ws_env_list(env, defaults[kind])


def _ws_credentials(data: Any) -> tuple[str, str]:
    """프레임에서 (사내 ID, 토큰). 주소(http/https)는 ID 로 쓰지 않는다 — 주소만 온 프레임을
    사용자 ID 로 읽으면 그 주소 문자열이 계정이 된다."""
    if isinstance(data, str):
        text = data.strip()
        return ("", "") if _looks_like_url(text) else (text, "")
    user_id = _ws_pick(data, _ws_fields("user"))
    if _looks_like_url(user_id):
        user_id = ""
    return user_id, _ws_pick(data, _ws_fields("token"))


def _ws_frame_url(data: Any) -> str:
    """ID·토큰 대신 온 주소(http/https 만)."""
    value = _ws_pick(data, _ws_fields("url")) if isinstance(data, dict) else str(data or "").strip()
    return value if _looks_like_url(value) else ""


def _ws_url_action() -> str:
    raw = str(os.environ.get("FLOW_WS_AUTH_URL_ACTION", "") or "").strip().lower()
    return raw if raw in _WS_URL_ACTIONS else "open"


class WsFollowUp(Exception):
    """프레임에 ID·토큰이 없고 주소만 있을 때 브라우저가 이어서 할 일.

    라우터가 HTTP 202 {action, url, detail} 로 돌려준다. action=open 이면 브라우저가 인증 창을
    열고 같은 websocket 에서 다음 메시지를 기다린다. browser 면 그 주소를 브라우저 자격으로
    읽어 응답 본문을 다시 /api/auth/sso/ws/login 에 넘긴다(그 본문도 서버 재검증을 거친다)."""

    def __init__(self, action: str, url: str):
        super().__init__(action)
        self.action = action
        self.url = url

    def payload(self) -> dict:
        detail = ("인증 창에서 로그인을 마치면 자동으로 들어갑니다. 창이 안 열리면 [인증 창 열기]를 누르세요."
                  if self.action == "open" else "인증 주소를 브라우저에서 확인하는 중입니다.")
        return {"action": self.action, "url": self.url, "detail": detail}


def _ws_is_failure(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    ok = data.get("ok", data.get("success", data.get("result", True)))
    return ok is False or str(ok).strip().lower() in {"false", "fail", "error", "0"}


def _ws_fetch_allowed(url: str) -> bool:
    """server 방식에서 Flow 서버가 읽어도 되는 주소인가(FLOW_WS_AUTH_FETCH_ALLOW 접두어).

    scheme·host·port 가 같고 경로가 접두어로 시작해야 한다. user@host 형태·'..' 경로는 거부."""
    from urllib.parse import unquote, urlsplit

    try:
        target = urlsplit(url)
        target_port = target.port
    except ValueError:
        return False
    if target.scheme.lower() not in {"http", "https"} or not target.hostname or "@" in target.netloc:
        return False
    path = target.path or "/"
    if ".." in unquote(path).split("/"):
        return False
    default_port = {"http": 80, "https": 443}
    for prefix in _ws_env_list("FLOW_WS_AUTH_FETCH_ALLOW", ""):
        try:
            allow = urlsplit(prefix)
            allow_port = allow.port
        except ValueError:
            continue
        if allow.scheme.lower() != target.scheme.lower():
            continue
        if (allow.hostname or "").lower() != target.hostname.lower():
            continue
        if (allow_port or default_port[target.scheme.lower()]) != (target_port or default_port[target.scheme.lower()]):
            continue
        if path.startswith(allow.path or "/"):
            return True
    return False


def _ws_fetch_url(url: str) -> Any:
    """server 방식: 허용 목록 안의 주소를 Flow 서버가 직접 GET. 리다이렉트는 따라가지 않는다."""
    import urllib.error
    import urllib.request

    if not _ws_env_list("FLOW_WS_AUTH_FETCH_ALLOW", ""):
        raise HTTPException(503, "FLOW_WS_AUTH_URL_ACTION=server 인데 FLOW_WS_AUTH_FETCH_ALLOW 가 비어 있습니다.")
    if not _ws_fetch_allowed(url):
        raise HTTPException(403, "인증서버가 보낸 주소가 FLOW_WS_AUTH_FETCH_ALLOW 목록에 없습니다.")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):  # noqa: D401 - urllib hook
            return None

    timeout = float(os.environ.get("FLOW_WS_AUTH_VERIFY_TIMEOUT_SEC", "") or 10.0)
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"Accept": "application/json, text/plain, */*"}, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(_WS_FETCH_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise HTTPException(502, f"인증 주소 응답 오류: HTTP {exc.code}") from exc
    except Exception as exc:
        raise HTTPException(502, f"인증 주소를 읽지 못했습니다: {type(exc).__name__}") from exc
    if len(body) > _WS_FETCH_MAX_BYTES:
        raise HTTPException(502, "인증 주소 응답이 너무 큽니다.")
    return _ws_parse(body.decode("utf-8", errors="replace"))


def _ws_fixed_literals() -> set[str]:
    """설정 문자열(URL·SEND·VERIFY_*)에 적힌 낱말. {token} 같은 치환 자리는 뺀다."""
    import re

    names = ("FLOW_WS_AUTH_URL", "FLOW_WS_AUTH_SEND", "FLOW_WS_AUTH_VERIFY_URL",
             "FLOW_WS_AUTH_VERIFY_SEND", "FLOW_WS_AUTH_VERIFY_HEADERS")
    raw = " ".join(str(os.environ.get(name, "") or "") for name in names)
    raw = re.sub(r"\{(token|user|nonce|origin)\}", " ", raw)
    return {part.casefold() for part in re.split(r"[^0-9A-Za-z._@-]+", raw) if len(part) >= 3}


def _ws_reject_fixed_user(user_id: str) -> None:
    uid = str(user_id or "").strip().casefold()
    if not uid:
        return
    literals = _ws_fixed_literals()
    local = uid.split("@", 1)[0]
    if uid in literals or (len(local) >= 3 and local in literals):
        raise HTTPException(
            403,
            "사내 로그인 설정(FLOW_WS_AUTH_URL·SEND·VERIFY_*)에 사용자 ID 가 고정돼 있어 로그인을 막았습니다. "
            "이대로면 누가 눌러도 같은 사람으로 들어갑니다. 설정에서 ID 를 빼세요.",
        )


# ── 진단: 프레임 모양(값은 가림) ─────────────────────────────────────
def _ws_mask_url(url: str) -> str:
    import re
    from urllib.parse import parse_qsl, urlsplit

    try:
        parts = urlsplit(url)
    except ValueError:
        return "주소(해석 불가)"
    segments = ["…" if re.fullmatch(r"[0-9A-Za-z_\-=.%]{16,}", seg or "") else seg
                for seg in (parts.path or "").split("/")]
    keys = [k for k, _ in parse_qsl(parts.query, keep_blank_values=True)][:12]
    query = ("?" + "&".join(f"{k}=…" for k in keys)) if keys else ""
    return f"주소 {parts.scheme}://{parts.hostname or ''}{(':' + str(parts.port)) if parts.port else ''}{'/'.join(segments)}{query}"


def _ws_mask_text(value: str) -> str:
    import re

    text = str(value)
    if _looks_like_url(text):
        return _ws_mask_url(text.strip())
    n = len(text)
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", text):
        local, domain = text.split("@", 1)
        return f"메일({n}자, {local[:2]}…@{domain})"
    if re.fullmatch(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", text):
        return f"JWT({n}자)"
    if text.isdigit():
        return f"숫자문자열({n}자리)"
    if n >= 24 and not re.search(r"\s", text):
        return f"토큰형({n}자)"
    if n <= 2:
        return f"텍스트({n}자)"
    return f"텍스트({n}자, {text[:2]}…)"


def _ws_shape(data: Any, depth: int = 0) -> Any:
    if isinstance(data, dict):
        if depth >= 5:
            return "{…}"
        return {str(k)[:40]: _ws_shape(v, depth + 1) for k, v in list(data.items())[:40]}
    if isinstance(data, list):
        head = [_ws_shape(data[0], depth + 1)] if data else []
        return [f"배열 {len(data)}개"] + head
    if isinstance(data, bool) or data is None:
        return data
    if isinstance(data, (int, float)):
        return f"숫자({len(str(data))}자리)"
    return _ws_mask_text(str(data))


def ws_frame_report(message: Any) -> dict:
    """진단용: 프레임 형식·모양·설정 필드 중 어느 경로가 맞았는지. 값은 담지 않는다."""
    data = _ws_parse(message)
    if isinstance(data, (dict, list)):
        fmt = "json"
    else:
        fmt = "text" if str(data or "").strip() else "empty"
    matched: dict[str, str] = {}
    user_id, _ = _ws_credentials(data)
    if isinstance(data, dict):
        for kind in ("user", "token", "url", "department", "name", "email"):
            value, path = _ws_pick_path(data, _ws_fields(kind))
            if kind == "user" and not user_id:
                path = ""
            if kind == "url" and not _looks_like_url(value):
                path = ""
            if path:
                matched[kind] = path
    elif fmt == "text":
        matched["url" if _frame_is_url(data) else "user"] = "*"
    return {
        "format": fmt,
        "length": len(message) if isinstance(message, str) else None,
        "shape": _ws_shape(data) if fmt != "text" else {"(문자열 프레임)": _ws_mask_text(str(data))},
        "matched": matched,
    }


def _frame_is_url(data: Any) -> bool:
    return isinstance(data, str) and _looks_like_url(data)


def ws_debug_enabled() -> bool:
    return str(os.environ.get("FLOW_WS_AUTH_DEBUG", "") or "").strip().lower() in {"1", "true", "yes", "on"}


_WS_PROBE_LOCK = threading.Lock()


def _ws_probe_path():
    return auth_core.PATHS.data_root / "logs" / "ws_login_probe.jsonl"


def record_ws_probe(entry: dict) -> None:
    """FLOW_WS_AUTH_DEBUG=1 일 때만 프레임 모양 기록(값 없음). 최근 200건 남짓만 유지."""
    import json

    if not ws_debug_enabled():
        return
    try:
        fp = _ws_probe_path()
        fp.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with _WS_PROBE_LOCK:
            lines = fp.read_text(encoding="utf-8").splitlines() if fp.exists() else []
            lines = (lines + [line])[-200:]
            fp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


def read_ws_probe(limit: int = 50) -> list[dict]:
    import json

    fp = _ws_probe_path()
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out = []
    for line in lines[-max(1, min(int(limit or 50), 200)):]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def ws_config_summary() -> dict:
    """진단용 현재 서버 설정(비밀 값 없이). 감시기 재기동 없이 .local.bat 만 고쳤는지 확인할 때 본다."""
    def _set(name: str) -> bool:
        return bool(str(os.environ.get(name, "") or "").strip())

    verify_url = str(os.environ.get("FLOW_WS_AUTH_VERIFY_URL", "") or "").strip()
    return {
        "ws_url": _ws_mask_url(_ws_auth_url().replace("ws://", "http://", 1).replace("wss://", "https://", 1)) if _ws_auth_url() else "",
        "send_set": _set("FLOW_WS_AUTH_SEND"),
        "verify": str(os.environ.get("FLOW_WS_AUTH_VERIFY", "") or "").strip().lower()
                  or (("http" if verify_url.lower().startswith("http") else "ws") if verify_url else ""),
        "verify_url_set": bool(verify_url),
        "verify_method": str(os.environ.get("FLOW_WS_AUTH_VERIFY_METHOD", "") or "POST").strip().upper(),
        "trust_client": _set("FLOW_WS_AUTH_TRUST_CLIENT"),
        "url_action": _ws_url_action(),
        "fetch_allow": len(_ws_env_list("FLOW_WS_AUTH_FETCH_ALLOW", "")),
        "user_map": len(_ws_user_map()),
        "custom_fields": sorted(k for k in ("USER", "TOKEN", "DEPT", "NAME", "EMAIL", "URL")
                                if _set(f"FLOW_WS_AUTH_{k}_FIELDS")),
        "password_login": PasswordAuthProvider().enabled(),
        "debug": ws_debug_enabled(),
    }


def _ws_verify(user_id: str, token: str) -> tuple[str, Any]:
    """인증서버에 토큰을 다시 확인하고 (사내 ID, 인증서버 확인 응답)을 돌려준다."""
    import json

    mode = str(os.environ.get("FLOW_WS_AUTH_VERIFY", "") or "").strip().lower()
    verify_url = str(os.environ.get("FLOW_WS_AUTH_VERIFY_URL", "") or "").strip()
    trust = str(os.environ.get("FLOW_WS_AUTH_TRUST_CLIENT", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    if not mode:
        mode = ("http" if verify_url.lower().startswith("http") else "ws") if verify_url else ("none" if trust else "")
    if mode == "none":
        if not trust:
            raise HTTPException(403, "websocket 로그인 검증이 꺼져 있습니다. FLOW_WS_AUTH_VERIFY_URL 을 설정하세요.")
        return user_id, None
    if not mode:
        raise HTTPException(503, "websocket 로그인 서버 검증 설정(FLOW_WS_AUTH_VERIFY_URL)이 없습니다.")
    if not token:
        raise HTTPException(401, "인증서버 응답에 토큰이 없습니다.")
    template = str(os.environ.get("FLOW_WS_AUTH_VERIFY_SEND", "") or "").strip() or '{"token": "{token}"}'
    if template.startswith(("{", "[")):
        # JSON 템플릿이면 값의 따옴표·역슬래시를 JSON 규칙으로 넣는다.
        def _fill(text: str) -> str:
            return (text.replace("{token}", json.dumps(token)[1:-1])
                        .replace("{user}", json.dumps(user_id)[1:-1]))
    else:
        def _fill(text: str) -> str:
            return text.replace("{token}", token).replace("{user}", user_id)
    payload = _fill(template)
    timeout = float(os.environ.get("FLOW_WS_AUTH_VERIFY_TIMEOUT_SEC", "") or 10.0)
    user_fields = _ws_fields("user")
    try:
        if mode == "http":
            import urllib.request
            from urllib.parse import quote

            method = str(os.environ.get("FLOW_WS_AUTH_VERIFY_METHOD", "") or "POST").strip().upper()
            url = verify_url.replace("{token}", quote(token, safe="")).replace("{user}", quote(user_id, safe=""))
            headers = {"Accept": "application/json, text/plain, */*"}
            raw_headers = str(os.environ.get("FLOW_WS_AUTH_VERIFY_HEADERS", "") or "").strip()
            if raw_headers:
                try:
                    extra = json.loads(raw_headers)
                except ValueError as exc:
                    raise HTTPException(503, "FLOW_WS_AUTH_VERIFY_HEADERS 가 JSON 객체가 아닙니다.") from exc
                if not isinstance(extra, dict):
                    raise HTTPException(503, "FLOW_WS_AUTH_VERIFY_HEADERS 가 JSON 객체가 아닙니다.")
                headers.update({str(k): str(v).replace("{token}", token).replace("{user}", user_id)
                                for k, v in extra.items()})
            if method == "GET":
                req = urllib.request.Request(url, headers=headers, method="GET")
            else:
                headers.setdefault("Content-Type", "application/json")
                req = urllib.request.Request(url, data=payload.encode("utf-8"), headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        else:
            from websockets.sync.client import connect

            max_frames = max(1, int(os.environ.get("FLOW_WS_AUTH_VERIFY_MAX_FRAMES", "") or 3))
            with connect(verify_url or _ws_auth_url(), open_timeout=timeout, close_timeout=2) as ws:
                ws.send(payload)
                body = ""
                # 인사말·상태 프레임이 먼저 오는 서버가 있어 ID(또는 거부)가 든 프레임까지 몇 개 읽는다.
                for _ in range(max_frames):
                    frame = ws.recv(timeout=timeout)
                    body = frame.decode("utf-8", errors="replace") if isinstance(frame, (bytes, bytearray)) else str(frame)
                    parsed = _ws_parse(body)
                    if _ws_is_failure(parsed) or (isinstance(parsed, dict) and _ws_pick(parsed, user_fields)):
                        break
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, f"인증서버 확인 실패: {type(exc).__name__}: {exc}") from exc
    data = _ws_parse(body)
    if _ws_is_failure(data):
        raise HTTPException(401, "인증서버가 토큰을 거부했습니다.")
    verified = _ws_pick(data, user_fields)
    if _looks_like_url(verified):
        verified = ""
    if not verified:
        raise HTTPException(401, "인증서버 확인 응답에 사용자 ID 가 없습니다.")
    if user_id and verified.casefold() != user_id.casefold():
        raise HTTPException(401, "인증서버가 확인한 사용자와 로그인 사용자가 다릅니다.")
    return verified, data


def _ws_login_contact() -> str:
    return str(os.environ.get("FLOW_WS_AUTH_CONTACT", "") or "").strip()


def _ws_default_tabs() -> str:
    raw = str(os.environ.get("FLOW_WS_AUTH_DEFAULT_TABS", "") or "").strip()
    if raw and raw != "__all_user__":
        return raw
    return ",".join(sorted(auth_core.GRANTABLE_TAB_IDS))


class WebsocketAuthProvider(AuthProvider):
    """사내 websocket 인증서버 로그인. 사용자 정보는 저장하지 않는다."""

    name = "websocket"
    kind = "websocket"
    label = "사내 로그인"

    def enabled(self) -> bool:
        return bool(_ws_auth_url())

    def describe(self) -> dict:
        out = super().describe()
        contact = _ws_login_contact()
        out.update({
            "ws_url": _ws_auth_url(),
            "send": str(os.environ.get("FLOW_WS_AUTH_SEND", "") or ""),
            "login_url": "/api/auth/sso/ws/login",
            # 기본은 [사내 로그인] 버튼을 눌러야 연결한다(인증 창 팝업은 클릭 뒤에만 열린다).
            "auto": str(os.environ.get("FLOW_WS_AUTH_AUTO", "0") or "").strip().lower() in {"1", "true", "yes", "on"},
            "url_action": _ws_url_action(),
        })
        if contact:
            out["contact"] = contact
        return out

    def authenticate(self, credential: Any) -> AuthIdentity:
        """프레임 하나 → identity. 처리 순서:

        1) ID·토큰이 있으면 인증서버 재검증(_ws_verify)
        2) 없고 주소만 있으면 FLOW_WS_AUTH_URL_ACTION 대로: server 는 Flow 서버가 그 주소를 읽어
           로그인, open/browser 는 WsFollowUp(→ HTTP 202)으로 브라우저에 다음 할 일을 돌려준다
        3) 아무것도 없으면 400 — 브라우저는 다음 프레임을 기다린다
        """
        data = _ws_parse(credential)
        user_id, token = _ws_credentials(data)
        if user_id or token:
            user_id, verified = _ws_verify(user_id, token)
        else:
            url = _ws_frame_url(data)
            if not url:
                raise HTTPException(400, "인증서버 메시지에서 사용자 ID/토큰을 찾지 못했습니다.")
            if _ws_url_action() != "server":
                raise WsFollowUp(_ws_url_action(), url)
            # 허용 목록 주소를 Flow 서버가 직접 읽은 응답은 인증서버의 답으로 본다.
            fetched = _ws_fetch_url(url)
            if _ws_is_failure(fetched):
                raise HTTPException(401, "인증 주소가 로그인을 거부했습니다.")
            user_id, token = _ws_credentials(fetched) if isinstance(fetched, dict) else ("", "")
            if user_id:
                verified = fetched
            elif token:
                user_id, verified = _ws_verify("", token)
            else:
                raise HTTPException(401, "인증 주소 응답에 사용자 ID/토큰이 없습니다.")
            data = fetched
        _ws_reject_fixed_user(user_id)
        # 부서·이름·메일은 인증서버가 확인해 준 응답에서만 믿는다. 브라우저가 보낸
        # 부서를 쓰면 부서를 속여 로그인·권한을 얻을 수 있다(신뢰 모드만 예외).
        profile_src = verified if isinstance(verified, dict) else (data if verified is None else {})
        department = _ws_pick(profile_src, _ws_fields("department"))
        name = _ws_pick(profile_src, _ws_fields("name"))
        email = _ws_pick(profile_src, _ws_fields("email"))
        return _identity_for_company_user(user_id, self.name, department=department, name=name, email=email)


def _identity_for_company_user(user_id: str, provider: str, *, department: str = "",
                               name: str = "", email: str = "") -> AuthIdentity:
    """확인된 사내 ID → Flow identity. websocket 로그인과 IP 로그인이 같은 규칙을 쓴다.

    FLOW_WS_AUTH_USER_MAP 매핑 → 기존 계정 → 부서 규칙 순."""
    from routers import auth as auth_router

    mapped = _ws_user_map().get(user_id.casefold())
    username = mapped or user_id
    department = " ".join(str(department or "").split())
    claims = {"ws_user": user_id, "department": department, "name": name, "email": email}
    if provider == "websocket":
        # The verified department is also the source for the existing permission
        # groups. Refresh stored users before applying their group defaults; an
        # explicit individual grant keeps its established precedence.
        with auth_router.USERS_MUTATION_LOCK:
            users = auth_router.read_users()
            rows = auth_router.find_user_rows(users, username)
            row = rows[0] if rows else None
            if row is not None and str(row.get("role") or "user") != "admin":
                from routers import admin as admin_router

                changed = str(row.get("department") or "") != department
                if changed:
                    row["department"] = department
                if admin_router.apply_sso_department_permissions(users, row["username"]):
                    changed = True
                if changed:
                    auth_router.write_users(users)
    else:
        rows = auth_router.find_user_rows(auth_router.read_users(), username)
        row = rows[0] if rows else None
    profile = read_people().get((row or {}).get("username") or username, {})
    if profile.get("manual"):
        name, email = profile.get("name", ""), profile.get("email", "")
    if row is not None:
        # 기존 계정(매핑된 hol 등)은 그 계정의 역할·탭을 그대로 쓴다.
        identity = AuthIdentity(
            username=row["username"], provider=provider,
            role=row.get("role", "user") or "user", status="approved",
            tabs=row.get("tabs", "") or "", name=name or row.get("name", "") or "",
            email=email or row.get("email", "") or "", claims=claims,
        )
    elif mapped:
        # 매핑 대상 계정이 아직 없으면(새 서버) 관리자로 연다 — 매핑은 관리자 지정용이다.
        identity = AuthIdentity(username=username, provider=provider, role="admin",
                                status="approved", tabs="__all__", name=name, email=email,
                                claims=claims, ephemeral=True)
    else:
        allowed, tabs = department_access(department)
        if not allowed:
            contact = _ws_login_contact()
            dept_label = department or '부서 정보 없음'
            msg = f"'{dept_label}' 부서는 Flow 로그인 대상이 아닙니다."
            if contact:
                msg += f" 문의 {contact}"
            else:
                msg += " 관리자에게 문의하세요."
            raise HTTPException(403, msg)
        # WebSocket users without a users.csv row still inherit the same
        # permission group as stored SSO users. Keep their grant in the session,
        # with the department taken only from the server-verified response.
        if provider == "websocket":
            from routers import admin as admin_router

            group_user = {"username": username, "role": "user", "department": department,
                          "tabs": "", "permission_source": ""}
            admin_router.apply_sso_department_permissions([group_user], username)
            if group_user["permission_source"]:
                tabs = group_user["tabs"]
                claims["permission_source"] = group_user["permission_source"]
        identity = AuthIdentity(username=username, provider=provider, role="user", status="approved",
                                tabs=tabs, name=name, email=email, claims=claims, ephemeral=True)
    # 세션과 로그인 응답도 최종 연락처를 사용한다(수동 관리자 연락처 우선).
    claims.update(name=identity.name, email=identity.email)
    if row is not None:
        claims["permission_source"] = row.get("permission_source", "") or ""
    _remember_manager_profile(identity, department)
    return identity


register_provider(WebsocketAuthProvider())


# ── 접속 IP 로그인 ────────────────────────────────────────────────────
# 지정한 PC(접속 IP)에서 [로그인] 을 누르면 그 IP 에 묶인 사내 ID 로 들어온다.
# 사내 인증서버를 아직 붙이지 못한 설치·개인 서버용이다. 기본은 꺼져 있다.
#
#   FLOW_IP_LOGIN_MAP   {"접속 IP": "사내 ID"} JSON. 예) {"127.0.0.1": "example.user"}
#                       비우면 IP 로그인 꺼짐. ID/PW 로그인은 별도 설정으로 유지한다.
#
# IP 는 TCP 접속 주소(request.client.host)만 본다. X-Forwarded-For 같은 헤더는
# 브라우저가 마음대로 적을 수 있어 믿지 않는다 — 프록시 뒤에 두면 쓰지 말 것.
def _ip_login_map() -> dict[str, str]:
    import json

    raw = str(os.environ.get("FLOW_IP_LOGIN_MAP", "") or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip(): str(v).strip() for k, v in data.items() if str(k).strip() and str(v).strip()}


def _normalize_ip(value: str) -> str:
    ip = str(value or "").strip()
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip


class IpLoginAuthProvider(AuthProvider):
    """접속 IP → 사내 ID. 버튼 하나로 로그인한다."""

    name = "ip"
    kind = "ip"
    label = "로그인"

    def enabled(self) -> bool:
        return bool(_ip_login_map())

    def describe(self) -> dict:
        out = super().describe()
        out["login_url"] = "/api/auth/sso/ip/login"
        return out

    def authenticate(self, credential: Any) -> AuthIdentity:
        client_ip = _normalize_ip(credential)
        mapping = {_normalize_ip(k): v for k, v in _ip_login_map().items()}
        user_id = mapping.get(client_ip)
        if not user_id:
            raise HTTPException(403, f"이 PC({client_ip or '알 수 없음'})는 로그인 대상으로 등록되어 있지 않습니다.")
        return _identity_for_company_user(user_id, self.name)


register_provider(IpLoginAuthProvider())


# ── 세션 시작 — 로그인 방식과 무관한 유일한 경로 ────────────────────────
def effective_tabs(identity: AuthIdentity) -> str:
    if identity.role == "admin":
        return "__all__"
    return identity.tabs or ""


def start_session(identity: AuthIdentity, *, audit: bool = True) -> dict:
    """인증된 identity → 세션 토큰 + 로그인 응답.

    비밀번호 로그인과 SSO callback 이 **모두 이 함수만** 호출한다. 세션 수명,
    토큰 저장소, 프런트 응답 스키마를 한 곳에 묶어 두어 로그인 방식이 늘어도
    세션 규약이 갈라지지 않게 한다.
    """
    if identity.status != "approved":
        raise HTTPException(403, "Pending admin approval")

    token, expires_at = auth_core.issue_token(
        identity.username,
        identity.role,
        auth_method=identity.provider,
        claims=identity.claims,
        tabs=identity.tabs if identity.ephemeral else None,
    )
    # Password and SSO must advance the same inactivity clock. Failure to write
    # this auxiliary timestamp must not invalidate an authenticated session;
    # the auth:login audit record remains a migration fallback.
    try:
        from routers import auth as auth_router
        auth_router.record_successful_login(identity.username)
    except Exception:
        pass
    if audit:
        try:
            _audit_user(
                identity.username,
                "auth:login",
                detail=f"role={identity.role};via={identity.provider}",
                tab="auth",
            )
        except Exception:
            pass
    return {
        "ok": True,
        "username": identity.username,
        "name": identity.name,
        "email": identity.email,
        "sso_id": str(identity.claims.get("ws_user") or ""),
        "department": str(identity.claims.get("department") or ""),
        "role": identity.role,
        "tabs": effective_tabs(identity),
        "token": token,
        "expires_at": datetime.datetime.fromtimestamp(expires_at).isoformat(timespec="seconds"),
        # 추가 필드 — 기존 프런트는 무시한다. SSO 세션에서 "비밀번호 변경" UI 를
        # 숨기는 등의 분기에 쓴다.
        "auth_method": identity.provider,
    }


def login_with(provider_name: str, credential: Any) -> dict:
    """provider 이름으로 인증 후 세션 시작. 라우터가 쓰는 단축 경로."""
    return start_session(get_provider(provider_name).authenticate(credential))


# 환경변수가 없으면 비활성 provider 로만 등록된다. import 시 네트워크 요청은 없다.
from core import oidc_provider as _oidc_provider  # noqa: E402,F401


__all__: Iterable[str] = (
    "AuthIdentity",
    "AuthProvider",
    "PasswordAuthProvider",
    "DEFAULT_TABS",
    "LEGACY_LOGIN_TABS",
    "register_provider",
    "get_provider",
    "list_providers",
    "describe_providers",
    "effective_tabs",
    "start_session",
    "login_with",
)
