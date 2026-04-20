"""core/auth.py — session tokens + password hashing (v8.4.6).

v8.4.6 보안 패치:
  - 모든 /api/* 는 이제 세션 토큰 검증. 토큰은 로그인 성공 시 발급되고 4h idle 만료.
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
from typing import Optional

from fastapi import HTTPException, Request
from core.paths import PATHS

# ── 상수 ─────────────────────────────────────────────────────────────
PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_BYTES = 16
SESSION_IDLE_SECONDS = 4 * 3600       # 4h idle timeout (FE 타이머와 동일)
SESSION_TOUCH_GRACE = 60              # 마지막 touch 이후 60초 내 재요청은 파일 쓰기 skip

# /api/* 중 인증을 요구하지 **않는** 경로.
# 나머지 /api/* 는 토큰 검증을 거친다.
AUTH_EXEMPT_API_PATHS = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/reset-request",
    "/api/auth/logout",        # logout 은 토큰이 이미 만료되었을 수도 있으므로 exempt
}

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
    # 시동 시 만료 토큰 정리
    now = _now()
    data = {t: m for t, m in data.items()
            if isinstance(m, dict) and (now - float(m.get("last_seen", 0))) < SESSION_IDLE_SECONDS}
    _cache = data
    _cache_loaded = True
    return _cache


def _save_tokens() -> None:
    tmp = TOKENS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache), "utf-8")
    tmp.replace(TOKENS_FILE)


def issue_token(username: str, role: str) -> tuple[str, float]:
    """새 세션 토큰 발급. 동일 유저의 기존 토큰은 유지 (다중 기기)."""
    token = secrets.token_urlsafe(32)
    now = _now()
    with _lock:
        _load_tokens()
        _cache[token] = {
            "username": username,
            "role": role or "user",
            "issued_at": now,
            "last_seen": now,
        }
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
    """토큰 유효 시 user dict 반환. 만료/없으면 None. last_seen 은 grace 초과 시 갱신."""
    if not token:
        return None
    with _lock:
        _load_tokens()
        meta = _cache.get(token)
        if not meta:
            return None
        now = _now()
        last = float(meta.get("last_seen", 0))
        if (now - last) >= SESSION_IDLE_SECONDS:
            _cache.pop(token, None)
            _save_tokens()
            return None
        # touch (60s grace 로 쓰기 I/O 최소화)
        if (now - last) > SESSION_TOUCH_GRACE:
            meta["last_seen"] = now
            _save_tokens()
        return dict(meta)


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


def verify_owner(request: Request, target_username: str) -> dict:
    """target_username 이 본인이거나 admin 이어야 함. 아니면 403."""
    u = current_user(request)
    if u.get("role") == "admin":
        return u
    if (u.get("username") or "") != (target_username or ""):
        raise HTTPException(403, "Forbidden (not owner)")
    return u


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
