"""core/audit.py v8.7.1 — centralized activity logging.

이전에는 `/api/admin/log` 를 유저가 직접 호출할 때만 activity.jsonl 에 기록됐다.
이제 서버측 주요 라이프사이클 이벤트(login/logout, 인폼 CRUD, 캘린더 CRUD,
SplitTable plan, admin settings 등) 에서도 동일 파일에 감사 로그를 남긴다.

사용:
    from core.audit import record
    record(request_or_username, "inform:create", detail="wafer=A0001B.1-W03", tab="inform")

- 첫 인자가 fastapi.Request 면 세션 토큰에서 유저를 뽑는다.
- 토큰이 없거나 무효하면 "anonymous" 로 기록 (실패해도 예외 전파 안 함).
- 문자열이면 해당 유저명으로 그대로 기록.
"""
from __future__ import annotations

from typing import Union

from core.paths import PATHS
from core.utils import jsonl_append

ACTIVITY_LOG = PATHS.activity_log


def _resolve_username(subject) -> str:
    try:
        # fastapi.Request duck-typing: state 속성 + headers
        state_user = getattr(getattr(subject, "state", None), "user", None)
        if isinstance(state_user, dict) and state_user.get("username"):
            return state_user["username"]
        headers = getattr(subject, "headers", None)
        if headers is not None:
            token = headers.get("x-session-token") or headers.get("X-Session-Token")
            if token:
                from core.auth import validate_token
                u = validate_token(token)
                if u and u.get("username"):
                    return u["username"]
        if isinstance(subject, str) and subject:
            return subject
    except Exception:
        pass
    return "anonymous"


def record(subject, action: str, detail: str = "", tab: str = "") -> None:
    """Append {username, action, tab, detail, timestamp} to activity.jsonl.

    예외는 절대 전파하지 않는다 — 감사 실패가 본래 요청을 깨뜨리면 안 됨.
    """
    try:
        jsonl_append(ACTIVITY_LOG, {
            "username": _resolve_username(subject),
            "action": str(action or "")[:160],
            "tab": str(tab or "")[:40],
            "detail": str(detail or "")[:500],
        })
    except Exception:
        pass


def record_user(username: str, action: str, detail: str = "", tab: str = "") -> None:
    """username 직접 지정 variant."""
    record(username or "anonymous", action, detail, tab)
