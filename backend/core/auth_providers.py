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
from dataclasses import dataclass, field
from typing import Any, Iterable

from fastapi import HTTPException

from core import auth as auth_core
from core.audit import record_user as _audit_user

# 신규 계정(SSO 자동 프로비저닝 포함)은 관리자가 권한을 부여하기 전까지 접근 가능한 탭이 없다.
DEFAULT_TABS = ""

# 하위 호환 export. 빈 tabs는 이제 명시적으로 "권한 없음"을 뜻한다.
LEGACY_LOGIN_TABS = ""


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
        raw = str(os.environ.get("FLOW_PASSWORD_LOGIN_ENABLED", "true") or "").strip().lower()
        return raw not in {"0", "false", "no", "off"}

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
