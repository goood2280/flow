from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.auth import AUTH_EXEMPT_API_PATHS, validate_token


# Browsers cannot attach custom headers to these resource/stream URLs, so the
# API accepts ?t=<token> for this narrow set only.
QUERY_TOKEN_PREFIXES = (
    "/api/informs/files/",
    "/api/tracker/image",
    "/api/meetings/stream",
)


class AuthMiddleware(BaseHTTPMiddleware):
    """/api/* paths require a valid session token except auth bootstrap routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in AUTH_EXEMPT_API_PATHS:
            token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")
            if not token and any(path.startswith(prefix) for prefix in QUERY_TOKEN_PREFIXES):
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

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response
