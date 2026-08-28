"""Browser endpoints for the optional corporate OIDC login."""
from __future__ import annotations

import json
import urllib.parse

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core import auth_providers
from core.oidc_provider import OIDC_PROVIDER, open_login_state, seal_login_state


router = APIRouter(prefix="/api/auth/sso/oidc", tags=["auth"])
STATE_COOKIE = "flow_oidc_state"


def _callback_url(request: Request) -> str:
    configured = OIDC_PROVIDER.config().redirect_uri
    return configured or str(request.url_for("oidc_callback"))


def _error_page(message: str, *, status_code: int = 400) -> HTMLResponse:
    safe = json.dumps(str(message or "SSO login failed")).replace("<", "\\u003c")
    html = f"""<!doctype html>
<html lang=\"ko\"><meta charset=\"utf-8\"><title>Flow SSO</title>
<body style=\"margin:0;background:#050508;color:#ddd;font:14px system-ui;display:grid;place-items:center;min-height:100vh\">
<main style=\"max-width:520px;padding:32px;border:1px solid #2a2a2a;border-radius:10px;background:#0c0c0f\">
<h2 style=\"color:#f97316\">SSO 로그인을 완료하지 못했습니다</h2>
<p id=\"message\"></p><p><a href=\"/\" style=\"color:#f97316\">로그인 화면으로 돌아가기</a></p>
</main><script>document.getElementById('message').textContent={safe};</script></body></html>"""
    response = HTMLResponse(html, status_code=status_code, headers={"Cache-Control": "no-store"})
    response.delete_cookie(STATE_COOKIE, path="/api/auth/sso/oidc")
    return response


@router.get("/start")
def oidc_start(request: Request):
    provider = auth_providers.get_provider("oidc")
    redirect_url, state = provider.begin(_callback_url(request))
    config = OIDC_PROVIDER.config()
    response = RedirectResponse(redirect_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        seal_login_state(state, config.state_secret),
        max_age=600,
        httponly=True,
        secure=urllib.parse.urlparse(state["redirect_uri"]).scheme == "https",
        samesite="lax",
        path="/api/auth/sso/oidc",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/callback", name="oidc_callback")
def oidc_callback(
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    flow_oidc_state: str | None = Cookie(default=None),
):
    if error:
        return _error_page(error_description or error, status_code=401)
    try:
        config = OIDC_PROVIDER.config()
        context = open_login_state(flow_oidc_state or "", config.state_secret)
        if not state or state != context.get("state"):
            return _error_page("SSO state 검증에 실패했습니다.", status_code=401)
        identity = OIDC_PROVIDER.authenticate(
            {
                "code": code,
                "nonce": context.get("nonce"),
                "redirect_uri": context.get("redirect_uri"),
                "code_verifier": context.get("code_verifier"),
            }
        )
        session = auth_providers.start_session(identity)
    except Exception as exc:
        detail = getattr(exc, "detail", None) or "SSO login failed"
        return _error_page(str(detail), status_code=int(getattr(exc, "status_code", 400)))

    # 같은 origin의 callback 문서가 Flow 세션을 localStorage에 저장한다. 토큰을
    # query string/redirect URL에 싣지 않으므로 브라우저 기록과 proxy log에 남지 않는다.
    session_json = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
    session_json = session_json.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = f"""<!doctype html>
<html lang=\"ko\"><meta charset=\"utf-8\"><title>Flow SSO</title>
<body style=\"margin:0;background:#050508;color:#ddd;font:14px system-ui;display:grid;place-items:center;min-height:100vh\">
<p>Flow 로그인을 완료하는 중입니다…</p>
<script>
localStorage.setItem('hol_user', JSON.stringify({session_json}));
location.replace('/');
</script></body></html>"""
    response = HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'",
            "Referrer-Policy": "no-referrer",
        },
    )
    response.delete_cookie(STATE_COOKIE, path="/api/auth/sso/oidc")
    return response
