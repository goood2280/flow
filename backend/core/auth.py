"""core/auth.py — session tokens + password hashing + 권한 판정.

레이어 경계 (SSO 도입 대비):
  - **이 파일** = 세션 토큰 저장/검증 + 비밀번호 해싱 + 권한 헬퍼.
    토큰 발급/검증은 로그인 방식을 모른다 (`issue_token` 참고).
  - `core/auth_providers.py` = "누구인가"를 판정하는 인증 레이어.
    비밀번호 provider 와 (앞으로) SSO provider 가 여기에 등록된다.
    세션 시작은 `auth_providers.start_session()` 하나로 모인다.
  - `routers/auth.py` = HTTP 표면 + users.csv 저장소.

새 로그인 방식(OIDC/SAML)을 붙일 때 이 파일은 손대지 않는다. 예외는 SSO callback
경로를 여는 `AUTH_EXEMPT_API_PREFIXES` 뿐이고 그것도 이미 열려 있다.

v8.4.6 보안 패치:
  - 모든 /api/* 는 이제 세션 토큰 검증. 토큰은 로그인 성공 시 발급되고 6h idle 만료.
  - 비밀번호는 PBKDF2-HMAC-SHA256 (salted). 기존 sha256(no-salt) 해시는 첫 로그인 시 자동 업그레이드.
  - FastAPI dependency: current_user(), require_admin(), verify_owner(username).
  - 토큰 store 는 {data_root}/sessions/tokens.json (atomic write, in-proc cache).

로그인 응답 스키마 변화:
  { ok, username, role, tabs, token, expires_at }

프론트 규약:
  - `localStorage.hol_user` = { username, role, tabs, token, expires_at }
  - 모든 fetch 호출에 `X-Session-Token: <token>` 헤더 필수.
  - 401/403 응답 → localStorage 제거 + 로그인 페이지로 리다이렉트.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import datetime
from typing import Any, Optional

from fastapi import HTTPException, Request
from core.paths import PATHS

# ── 상수 ─────────────────────────────────────────────────────────────
PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_BYTES = 16
# v8.8.33: 6시간 idle + 24시간 절대 상한. 오랜만에 돌아와도 재로그인 강제.
SESSION_IDLE_SECONDS = 6 * 3600       # 6h idle timeout (유휴 시간 초과 시 재로그인)
SESSION_ABSOLUTE_MAX_SECONDS = 24 * 3600  # 24h absolute — 발급 후 24h 넘으면 재로그인
SESSION_TOUCH_GRACE = 60              # 마지막 touch 이후 60초 내 재요청은 파일 쓰기 skip

# /api/* 중 인증을 요구하지 **않는** 경로.
# 나머지 /api/* 는 토큰 검증을 거친다.
AUTH_EXEMPT_API_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/me",
    "/api/auth/reset-request",
    "/api/auth/forgot-password",
    "/api/auth/logout",        # logout 은 토큰이 이미 만료되었을 수도 있으므로 exempt
    "/api/auth/providers",     # 로그인 화면이 쓸 수 있는 인증수단 목록 (로그인 전 호출)
}

# prefix 단위 면제. SSO redirect/callback 은 **정의상** 세션 토큰 없이 들어온다
# (IdP 가 브라우저를 되돌려 보내는 요청이라 커스텀 헤더를 붙일 수 없다).
# provider 별 경로를 미리 열어 두어, SSO 를 붙일 때 인증 미들웨어를 다시 손대지
# 않게 한다. 이 아래에는 **세션을 시작하는 라우트만** 두고, 자원 접근 라우트는
# 절대 두지 않는다.
AUTH_EXEMPT_API_PREFIXES = (
    "/api/auth/sso/",
)


def is_auth_exempt(path: str) -> bool:
    """미들웨어/라우터가 공유하는 단일 판정. 여기 아닌 곳에 예외를 만들지 않는다."""
    if path in AUTH_EXEMPT_API_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in AUTH_EXEMPT_API_PREFIXES)

# ── 토큰 스토어 ───────────────────────────────────────────────────────
_SESSIONS_DIR = PATHS.data_root / "sessions"
_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
TOKENS_FILE = _SESSIONS_DIR / "tokens.json"

_lock = threading.Lock()
_cache: dict = {}         # { token: {username, role, issued_at, last_seen} }
_cache_loaded = False


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _load_tokens() -> dict:
    global _cache, _cache_loaded
    if _cache_loaded:
        return _cache
    data = {}
    if TOKENS_FILE.exists():
        try:
            data = json.loads(TOKENS_FILE.read_text("utf-8")) or {}
        except Exception:
            data = {}
    # v8.8.33: 시동 시 idle 만료 + absolute 만료 토큰 정리.
    now = _now()
    def _alive(m: dict) -> bool:
        if not isinstance(m, dict):
            return False
        last = float(m.get("last_seen", 0))
        issued = float(m.get("issued_at", last))
        if (now - last) >= SESSION_IDLE_SECONDS:
            return False
        if (now - issued) >= SESSION_ABSOLUTE_MAX_SECONDS:
            return False
        return True
    data = {t: m for t, m in data.items() if _alive(m)}
    _cache = data
    _cache_loaded = True
    return _cache


def _save_tokens() -> None:
    tmp = TOKENS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache), "utf-8")
    tmp.replace(TOKENS_FILE)


def issue_token(
    username: str,
    role: str,
    auth_method: str = "password",
    claims: dict | None = None,
) -> tuple[str, float]:
    """새 세션 토큰 발급. 동일 유저의 기존 토큰은 유지 (다중 기기).

    **로그인 방식과 무관하다.** 비밀번호 로그인이든 SSO callback 이든 여기로
    들어오고, 세션 수명·저장소·검증 규칙은 완전히 동일하다. `auth_method` 는
    감사/UI 분기용 메타일 뿐이며 권한 판정에는 쓰이지 않는다 (권한은 users.csv 의
    role/tabs 가 단독 소유). 새 로그인 방식을 추가할 때 이 함수는 손대지 않는다.

    claims 는 provider 가 남기고 싶은 비민감 메타(예: OIDC sub, IdP 이름)만 담는다.
    tokens.json 은 평문이므로 access/refresh token 같은 비밀은 넣지 않는다.
    """
    token = secrets.token_urlsafe(32)
    now = _now()
    meta = {
        "username": username,
        "role": role or "user",
        "issued_at": now,
        "last_seen": now,
        "auth_method": (auth_method or "password"),
    }
    if claims:
        meta["claims"] = dict(claims)
    with _lock:
        _load_tokens()
        _cache[token] = meta
        _save_tokens()
    return token, now + SESSION_IDLE_SECONDS


def revoke_token(token: str) -> None:
    if not token:
        return
    with _lock:
        _load_tokens()
        if _cache.pop(token, None) is not None:
            _save_tokens()


def revoke_user_tokens(username: str) -> int:
    """유저의 모든 토큰 revoke (비번 변경/계정 삭제 시)."""
    n = 0
    with _lock:
        _load_tokens()
        for t in list(_cache.keys()):
            if _cache[t].get("username") == username:
                _cache.pop(t, None); n += 1
        if n:
            _save_tokens()
    return n


def validate_token(token: str) -> Optional[dict]:
    """토큰 유효 시 user dict 반환. 만료/없으면 None. last_seen 은 grace 초과 시 갱신.
    v8.8.33: idle 6h + absolute 24h 이중 만료 체크."""
    if not token:
        return None
    with _lock:
        _load_tokens()
        meta = _cache.get(token)
        if not meta:
            return None
        now = _now()
        last = float(meta.get("last_seen", 0))
        issued = float(meta.get("issued_at", last))
        # idle 만료
        if (now - last) >= SESSION_IDLE_SECONDS:
            _cache.pop(token, None)
            _save_tokens()
            return None
        # absolute 만료
        if (now - issued) >= SESSION_ABSOLUTE_MAX_SECONDS:
            _cache.pop(token, None)
            _save_tokens()
            return None
        # touch (60s grace 로 쓰기 I/O 최소화)
        if (now - last) > SESSION_TOUCH_GRACE:
            meta["last_seen"] = now
            _save_tokens()
        out = dict(meta)
        # 업그레이드 이전에 발급된 토큰에는 auth_method 가 없다. 그때는 비밀번호
        # 로그인뿐이었으므로 "password" 로 채운다 — 재로그인 없이 넘어간다.
        out.setdefault("auth_method", "password")
        return out


# ── FastAPI dependencies ──────────────────────────────────────────────
def current_user(request: Request) -> dict:
    """요청의 X-Session-Token 헤더로 현재 유저 반환. 실패 시 401."""
    # 미들웨어가 request.state.user 를 세팅해뒀으면 재사용
    u = getattr(request.state, "user", None)
    if u:
        return u
    token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")
    u = validate_token(token)
    if not u:
        raise HTTPException(401, "Authentication required")
    request.state.user = u
    return u


def require_admin(request: Request) -> dict:
    u = current_user(request)
    if u.get("role") != "admin":
        raise HTTPException(403, "Admin only")
    return u


# ── v8.8.14: Per-page admin delegation ────────────────────────────────
# 철학: "각 페이지의 관리는 최대한 각 페이지에서 수행한다." global admin 만 할 수 있던
# 페이지 내부 관리 작업(예: inform 카탈로그 CRUD, SplitTable prefix/override 편집,
# TableMap 그래프 수정 등) 을 특정 유저에게 페이지 단위로 위임할 수 있게 한다.
#
# 저장소: admin_settings.json 의 `page_admins: { "<page_id>": ["user1", "user2"] }`.
# page_id 는 프론트 탭 이름과 맞춘다. 과거 plural/legacy key 는 읽을 때 canonical key 로 흡수한다.
# global admin 은 언제나 통과 — 이 맵에 추가로 넣을 필요 없음.
CANONICAL_PAGE_IDS = (
    "filebrowser",
    "dashboard",
    "chartbuilder",
    "templatereport",
    "autoreport",
    "lotrequest",
    "splittable",
    "lotmanage",
    "tracker",
    "inform",
    "meeting",
    "calendar",
    "tablemap",
    "groups",
    "messages",
    "devguide",
    # Existing Agent tab key. Kept canonical for live deployments.
    "diagnosis",
    "knowledge",
    "agent",
    # v9.2.x: Valve 파이프라인 알람 판정 페이지.
    "valve",
    # v9.3.x: TEG 위치 조회 (WF MAP) 페이지.
    "teg",
    # 제품별 BIN/MSR TABLE을 TEG die 좌표에 컬러링하는 Yield Map.
    "yieldmap",
    # ET 측정시간 (root lot × step_id × PGM(pt) 소요시간) 페이지.
    "ettime",
    # ET 다운로드 (vehicle reformatter REAL/ADDP index 추출) 페이지.
    "reformatize",
    # 붙여넣은 표를 사용자 정의 규칙으로 검사하는 페이지.
    "dcop",
    # Flow-i 채팅 사용 권한 — 홈 채팅 + home agent orchestrate 진입 게이트.
    "flowi",
    # 매칭 CSV 의 product 열을 raw DB 스캔으로 채우는 페이지 (관리자 승인 후 반영).
    "matchfill",
)
PAGE_ID_ALIASES = {
    "flow-i": "flowi",
    "flow_i": "flowi",
    "informs": "inform",
    "informlog": "inform",
    "meetings": "meeting",
    "dbmap": "tablemap",
    "table_map": "tablemap",
    "table-map": "tablemap",
    "spc": "dashboard",
    "ml": "dashboard",
}


def canonical_page_id(page_id: str | None) -> str:
    raw = str(page_id or "").strip().lower()
    if not raw:
        return ""
    return PAGE_ID_ALIASES.get(raw, raw)


# v9.1.x: 소탭 단위 권한 — tabs CSV 토큰에 "tab:subtab" 지원.
# bare "tab" 토큰은 해당 탭의 모든 소탭 허용(하위호환). 소탭이 없는 탭은 bare 토큰만 유효.
# 프론트 config.js SUB_TABS 와 반드시 동기 유지 — 어긋나면 admin 이 저장한 토큰이 조용히 버려진다.
TAB_SUBTABS = {
    "filebrowser": ("db", "files"),
    "splittable": ("view", "history"),
    "inform": ("inform", "matrix", "audit"),
    # v9.2.x 에이전트 탭 재편: home-flowi/unit-ai → catalog/runtime, semantic/llm 은 관리 탭으로 이관.
    "diagnosis": ("catalog", "runtime", "workflows"),
}
# 재편 이전에 저장된 소탭 토큰을 새 키로 흡수. 관리 탭으로 이관된 소탭(semantic/llm)은 버린다.
SUBTAB_ALIASES = {
    "diagnosis": {"home-flowi": "catalog", "unit-ai": "runtime"},
}


def canonical_tab_token(token: str | None) -> str:
    """"tab" 또는 "tab:subtab" 토큰을 canonical 형태로. 유효하지 않으면 ""."""
    raw = str(token or "").strip().lower()
    if not raw:
        return ""
    tab, _, sub = raw.partition(":")
    tab = canonical_page_id(tab)
    if not tab:
        return ""
    if not sub:
        return tab
    sub = sub.strip()
    sub = SUBTAB_ALIASES.get(tab, {}).get(sub, sub)
    if sub in TAB_SUBTABS.get(tab, ()):  # 알 수 없는 소탭 토큰은 버림
        return f"{tab}:{sub}"
    return ""


def parse_tab_tokens(raw: Any) -> tuple[list[str], dict[str, list[str]]]:
    """tabs CSV/list → (main tab 목록, {tab: 허용 소탭 목록}).

    bare 토큰이면 해당 탭의 전체 소탭. "tab:sub" 토큰만 있으면 그 소탭들만.
    """
    parts = raw if isinstance(raw, list) else str(raw or "").split(",")
    tabs: list[str] = []
    subs: dict[str, list[str]] = {}
    bare: set[str] = set()
    for part in parts:
        token = canonical_tab_token(part)
        if not token:
            continue
        tab, _, sub = token.partition(":")
        if tab not in tabs:
            tabs.append(tab)
        if sub:
            cur = subs.setdefault(tab, [])
            if sub not in cur:
                cur.append(sub)
        else:
            bare.add(tab)
    for tab in tabs:
        if tab in bare or tab not in TAB_SUBTABS:
            subs[tab] = list(TAB_SUBTABS.get(tab, ()))
    return tabs, subs


def user_subtabs(user: dict) -> dict[str, list[str]]:
    """유저의 {tab: 허용 소탭} 맵. admin 은 전체."""
    if (user or {}).get("role") == "admin":
        return {tab: list(subs) for tab, subs in TAB_SUBTABS.items()}
    return parse_tab_tokens((user or {}).get("tabs", ""))[1]


def _clean_usernames(raw: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(raw, list):
        return out
    for item in raw:
        username = str(item or "").strip()
        if not username or username in seen:
            continue
        seen.add(username)
        out.append(username)
    return out


def get_page_admins() -> dict:
    """admin_settings.json 에서 page_admins 맵 반환. 파일 없거나 파싱 오류면 {}."""
    try:
        p = PATHS.data_root / "admin_settings.json"
        if p.is_file():
            data = json.loads(p.read_text("utf-8")) or {}
            pa = data.get("page_admins") or {}
            if isinstance(pa, dict):
                merged: dict[str, list[str]] = {}
                for raw_key, raw_users in pa.items():
                    key = canonical_page_id(raw_key)
                    if not key:
                        continue
                    cur = merged.setdefault(key, [])
                    for username in _clean_usernames(raw_users):
                        if username not in cur:
                            cur.append(username)
                return {k: sorted(v) for k, v in merged.items() if v}
    except Exception:
        pass
    return {}


def is_page_manager(user: dict | str, page_id: str) -> bool:
    """Return True when user can manage page_id. Global admin always passes."""
    if not user or not page_id:
        return False
    if isinstance(user, dict):
        username = str(user.get("username") or "").strip()
        role = str(user.get("role") or "").strip()
        if role == "admin":
            return True
    else:
        username = str(user or "").strip()
    pa = get_page_admins()
    return username in (pa.get(canonical_page_id(page_id)) or [])


def is_page_admin(username: str, page_id: str) -> bool:
    """Back-compat delegated-page check. Global admin cannot be inferred from username only."""
    if not username or not page_id:
        return False
    pa = get_page_admins()
    return username in (pa.get(canonical_page_id(page_id)) or [])


def _user_tabs(user: dict) -> list[str] | str:
    if user.get("role") == "admin":
        return "__all__"
    raw = user.get("tabs", "")
    if isinstance(raw, list):
        parts = raw
    else:
        parts = str(raw or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        # v9.1.x: "tab:subtab" 토큰 유지 — 유효하지 않은 토큰은 제거.
        tab = canonical_tab_token(part)
        if tab and tab not in seen:
            seen.add(tab)
            out.append(tab)
    return out


def _devguide_allowed(username: str, role: str) -> bool:
    # v9.3.x: DevGuide 는 global admin 전용 (devguide_user 위임 목록 폐기).
    return role == "admin"


def _group_permissions(username: str, role: str) -> dict:
    if role == "admin":
        return {"all": True, "owner": [], "member": []}
    try:
        fp = PATHS.data_root / "groups" / "groups.json"
        groups = json.loads(fp.read_text("utf-8")) if fp.is_file() else []
    except Exception:
        groups = []
    owner: list[str] = []
    member: list[str] = []
    if isinstance(groups, list):
        for group in groups:
            if not isinstance(group, dict):
                continue
            gid = str(group.get("id") or group.get("name") or "").strip()
            if not gid:
                continue
            if str(group.get("owner") or "").strip() == username:
                owner.append(gid)
            if username in [str(x or "").strip() for x in (group.get("members") or [])]:
                member.append(gid)
    return {"all": False, "owner": owner, "member": member}


def effective_permissions(user: dict) -> dict:
    """Compact effective permission summary for Admin UI and tests."""
    username = str((user or {}).get("username") or "").strip()
    role = str((user or {}).get("role") or "user").strip() or "user"
    pa = get_page_admins()
    manager_pages = list(CANONICAL_PAGE_IDS) if role == "admin" else sorted(
        page for page, users in pa.items() if username in (users or [])
    )
    return {
        "username": username,
        "role": role,
        "tabs": _user_tabs(user or {}),
        "subtabs": user_subtabs(user or {}),
        "page_manager": manager_pages,
        "devguide": _devguide_allowed(username, role),
        "groups": _group_permissions(username, role),
    }


def require_page_manager(page_id: str):
    """FastAPI dependency factory. global admin 이거나 해당 page 의 manager 면 통과."""
    canonical = canonical_page_id(page_id)

    def _dep(request: Request) -> dict:
        u = current_user(request)
        if is_page_manager(u, canonical):
            return u
        raise HTTPException(403, f"Admin or page manager ({canonical}) only")
    return _dep


def require_page_admin(page_id: str):
    """Back-compat alias for existing routers."""
    return require_page_manager(page_id)


def verify_owner(request: Request, target_username: str) -> dict:
    """target_username 이 본인이거나 admin 이어야 함. 아니면 403."""
    u = current_user(request)
    if u.get("role") == "admin":
        return u
    if (u.get("username") or "") != (target_username or ""):
        raise HTTPException(403, "Forbidden (not owner)")
    return u


# ── 로그인 식별자 정규화 ──────────────────────────────────────────────
# username 은 "사내 메일 id" 라는 규약이지만, 가입 화면에서 어떤 사람은 `hong`,
# 어떤 사람은 `hong@corp.com` 을 적는다. 둘을 다른 계정으로 두면 같은 사람이 계정
# 두 개를 갖고(가입 중복), 자기가 적은 형태를 잊으면 로그인이 안 된다.
# 그래서 **비교는 항상 canonical 형태로** 한다. 저장된 값(users.csv 의 username)은
# 건드리지 않는다 — 인폼/회의/권한그룹이 그 문자열을 참조하기 때문이다.
#
# 정규화 범위는 "@사내도메인" 하나뿐이다. **대소문자는 건드리지 않는다** —
# 아이디는 적힌 그대로 분류한다는 규칙이라 `Hong` 과 `hong` 은 서로 다른 계정이다.
# (도메인 부분만 메일 규약대로 대소문자를 무시하고 비교한다.)
def login_domains() -> set[str]:
    """사내 메일 도메인 집합. 이 도메인이 붙은 주소는 같은 id 로 본다.

    출처는 admin 메일 설정(Admin > 메일 API)의 `domain`, 그리고 보조로 `from_addr`
    의 도메인. 둘 다 비어 있으면 무엇이 사내 도메인인지 알 수 없으므로 **아무것도
    벗기지 않는다** — 모르는 상태에서 `a@x.com` 과 `a` 를 같은 사람으로 단정하면
    남의 계정에 로그인/비번재발급을 시도할 여지가 생긴다.
    """
    out: set[str] = set()
    try:
        from core import mail as _mail  # 지연 import: core.mail ↔ routers.auth 순환 회피
        cfg = _mail.load_mail_cfg()
    except Exception:
        return out
    d = (cfg.get("domain") or "").strip().lstrip("@").lower()
    if d:
        out.add(d)
    sender = (cfg.get("from_addr") or "").strip().lower()
    if "@" in sender:
        sd = sender.partition("@")[2].strip()
        if sd:
            out.add(sd)
    return out


def canonical_username(value: str, domains: set[str] | None = None) -> str:
    """로그인 식별자 비교용 키. `hong` 과 `hong@corp.com` → 둘 다 `hong`.

    사내 도메인(`login_domains()`)이 붙은 주소만 id 로 되돌린다. 그 밖의 도메인은
    별개 식별자로 남기고, 로컬 파트가 비면(`@corp.com`) 벗기지 않는다.
    대소문자는 보존한다 — `Hong` 과 `hong` 은 다른 계정이고, `Hong@corp.com` 의
    키는 `hong` 이 아니라 `Hong` 이다.

    `domains` 는 계정 목록을 훑는 호출부(로그인, 메일 수신자 해석)가 도메인 설정을
    한 번만 읽고 재사용하기 위한 것이다 — 행마다 admin_settings.json 을 다시 읽으면
    공유 드라이브 I/O 가 계정 수만큼 늘어난다.
    """
    v = (value or "").strip()
    if "@" not in v:
        return v
    local, _, domain = v.partition("@")
    if not local or not domain:
        return v
    # 도메인은 메일 규약대로 대소문자 무시 비교. 로컬 파트(=아이디)는 손대지 않는다.
    if domain.lower() not in (login_domains() if domains is None else domains):
        return v          # 사내 도메인이라는 근거가 없다 — 그대로 둔다
    return local


def validate_username(value: str, *, max_length: int = 128) -> str:
    """Return a trimmed username that is safe for CSV and file-path use."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) > max_length:
        raise ValueError(f"Username must be {max_length} characters or fewer")
    if raw in {".", ".."} or raw.startswith(".") or raw.endswith("."):
        raise ValueError("Invalid username")
    if raw[0] in {"=", "+", "-", "@"}:
        raise ValueError("Invalid username")
    if not all(ch.isalnum() or ch in "_-.@" for ch in raw):
        raise ValueError("Username may contain only letters, numbers, '_', '-', '.', and '@'")
    device = raw.partition("@")[0].partition(".")[0].upper()
    if device in {"CON", "PRN", "AUX", "NUL"} or (
        len(device) == 4 and device[:3] in {"COM", "LPT"} and device[3] in "123456789"
    ):
        raise ValueError("Invalid username")
    return raw


# ── 비밀번호 해싱 (PBKDF2 + 레거시 sha256 자동 업그레이드) ────────────
def hash_password(pw: str) -> str:
    """PBKDF2-HMAC-SHA256 with random per-user salt."""
    salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2$sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def _legacy_sha256(pw: str) -> str:
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()


def verify_password(pw: str, stored: str) -> tuple[bool, bool]:
    """(ok, needs_rehash) 반환. needs_rehash=True 면 호출자가 새 해시로 교체해야 함."""
    if not stored:
        return False, False
    if stored.startswith("pbkdf2$sha256$"):
        try:
            _, _, iters_s, salt_hex, digest_hex = stored.split("$", 4)
            iters = int(iters_s)
            digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                                         bytes.fromhex(salt_hex), iters)
            ok = hmac.compare_digest(digest.hex(), digest_hex)
            return ok, (ok and iters < PBKDF2_ITERATIONS)
        except Exception:
            return False, False
    # Legacy: 64-char hex = plain sha256 no-salt
    if len(stored) == 64 and all(c in "0123456789abcdef" for c in stored.lower()):
        ok = hmac.compare_digest(_legacy_sha256(pw).lower(), stored.lower())
        return ok, ok  # 성공하면 반드시 업그레이드 필요
    return False, False


# ── Back-compat helper: 기존 호출부 ─────────────────────────────────
def hash_pw(pw: str) -> str:
    """Deprecated — use hash_password. 기존 `from routers.auth import hash_pw` 유지."""
    return hash_password(pw)
