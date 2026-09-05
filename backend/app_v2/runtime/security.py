from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.auth import is_auth_exempt, validate_token


# Browsers cannot attach custom headers to these resource/stream URLs, so the
# API accepts ?t=<token> for this narrow set only.
QUERY_TOKEN_PREFIXES = (
    "/api/informs/files/",
    "/api/lot-requests/files/",
    "/api/tracker/image",
    "/api/meetings/stream",
    "/api/admin/my-notifications",
    "/api/admin/all-notifications",
    "/api/admin/mark-read",
    "/api/admin/mark-read-batch",
    "/api/admin/dismiss",
    "/api/admin/dismiss-batch",
    "/api/admin/notify-rules",
)


def _allow_query_token(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in QUERY_TOKEN_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """/api/* paths require a valid session token except auth bootstrap routes.

    로그인 방식과 무관하다 — 비밀번호 로그인이든 SSO 든 발급된 토큰은 같은 저장소를
    거치므로 여기는 그대로다. 면제 경로 판정은 `core.auth.is_auth_exempt` 한 곳에만
    있고, SSO callback(`/api/auth/sso/*`)은 그 안에서 이미 열려 있다.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        user = None
        if path.startswith("/api/") and not is_auth_exempt(path):
            token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")
            if not token and _allow_query_token(path):
                token = request.query_params.get("t", "")
            user = validate_token(token)
            if not user:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            request.state.user = user
            if not (path.startswith("/api/monitor") or path.startswith("/api/system")):
                try:
                    from core.sysmon import mark_user_activity

                    mark_user_activity()
                except Exception:
                    pass

        # One server-owned gate covers every current and future adapter caller.
        # Non-API work has no authenticated principal and therefore fails closed.
        from core import llm_adapter

        with llm_adapter.request_execution_scope(user):
            response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response
