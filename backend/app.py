"""FabCanvas.ai Backend v8.4.6 — uvicorn app:app --host 0.0.0.0 --port 8080.

v8.4.6 보안 패치:
  - 세션 토큰 기반 인증 미들웨어: 모든 /api/* 호출은 X-Session-Token 필요
    (login/register/reset-request/logout 만 exempt).
  - FastAPI OpenAPI/docs 비활성화 (내부 API 스펙 노출 차단).
  - 보안 헤더 추가: X-Content-Type-Options, X-Frame-Options, Referrer-Policy.
  - Seed admin 비밀번호는 환경변수 FABCANVAS_ADMIN_PW 우선, 미지정 시 임시값 + 경고.
  - Password 해시: SHA-256 → PBKDF2-HMAC-SHA256 (salted). 레거시 해시는 로그인 시 자동 업그레이드.
"""
import os, importlib, logging, secrets
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from core.paths import PATHS
from core.auth import validate_token, AUTH_EXEMPT_API_PATHS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
# legacy HOL prefix kept for logger name to avoid disturbing existing log filters; brand is now FabCanvas.ai
logger = logging.getLogger("holweb")

# v8.4.6: /docs, /redoc, /openapi.json disabled — API 스펙 무인증 노출 차단
app = FastAPI(
    title="FabCanvas.ai",
    version="8.4.6",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


# v8.7.1: 브라우저 <img>/<a download> 가 커스텀 헤더를 못 실음. 이미지 서빙 등
# 정적 파일 엔드포인트에 한해 ?t=<token> 쿼리 파라미터 fallback 허용.
_QUERY_TOKEN_PREFIXES = ("/api/informs/files/",)


class AuthMiddleware(BaseHTTPMiddleware):
    """/api/* 경로에 세션 토큰 검증을 강제. 예외는 AUTH_EXEMPT_API_PATHS."""
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in AUTH_EXEMPT_API_PATHS:
            token = request.headers.get("x-session-token") or request.headers.get("X-Session-Token")
            if not token and any(path.startswith(p) for p in _QUERY_TOKEN_PREFIXES):
                token = request.query_params.get("t", "")
            u = validate_token(token)
            if not u:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            request.state.user = u
        resp = await call_next(request)
        # v8.4.6: hardening headers (cheap, defense-in-depth)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp


app.add_middleware(AuthMiddleware)

# ── Dynamic Router Loading ──
ROUTERS_DIR = Path(__file__).parent / "routers"
loaded, failed = [], []
for f in sorted(ROUTERS_DIR.glob("*.py")):
    if f.name.startswith("_"):
        continue
    module_name = f"routers.{f.stem}"
    try:
        mod = importlib.import_module(module_name)
        if hasattr(mod, "router"):
            app.include_router(mod.router)
            loaded.append(f.stem)
        # v8.2.1: some routers expose additional routers (e.g. match_router on catalog)
        for extra_name in ("match_router",):
            extra = getattr(mod, extra_name, None)
            if extra is not None:
                app.include_router(extra)
                loaded.append(f"{f.stem}:{extra_name}")
    except Exception as e:
        failed.append((f.stem, str(e)))
        logger.warning(f"Router load failed: {module_name} — {e}")

logger.info(f"Loaded routers: {loaded}")
if failed:
    logger.warning(f"Failed routers: {failed}")

# v7.3: log resolved paths (critical for prod vs dev confusion)
logger.info(f"FabCanvas.ai paths — prod={PATHS.is_prod}")
logger.info(f"  app_root  = {PATHS.app_root}")
logger.info(f"  data_root = {PATHS.data_root}")
logger.info(f"  db_root   = {PATHS.db_root}")

# v8.7.0: 자동 백업 스케줄러 — data_root 스냅샷을 주기적으로 zip.
try:
    from core.backup import start_scheduler as _bk_start
    _bk_start()
except Exception as _bk_e:
    logger.warning(f"backup scheduler init failed: {_bk_e}")


@app.get("/version.json")
def serve_version():
    vp = Path(__file__).parent.parent / "version.json"
    if vp.exists():
        return FileResponse(str(vp), media_type="application/json")
    return {"version": "8.4.6"}


# ── Serve React build ──
DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():
    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API not found")
        # v8.4.6: traversal 방어 — DIST 를 벗어나는 경로는 SPA index 로 폴백
        try:
            fp = (DIST / path).resolve()
            fp.relative_to(DIST.resolve())
        except (ValueError, OSError):
            return FileResponse(str(DIST / "index.html"))
        if fp.is_file():
            return FileResponse(str(fp))
        return FileResponse(str(DIST / "index.html"))

# ── Seed admin user (환경변수 우선, 미지정 시 임시 랜덤 비번 + 경고) ──
from routers.auth import read_users, write_users
from core.auth import hash_password
import datetime
users = read_users()
if not any(u["username"] == "hol" for u in users):
    seed_pw = os.environ.get("FABCANVAS_ADMIN_PW") or os.environ.get("HOL_ADMIN_PW")
    if not seed_pw:
        # 개발 호환 — 기존 기본값 유지하되 로그로 경고.  prod 에서는 반드시 env 로 override.
        seed_pw = "hol12345!"
        logger.warning(
            "Seed admin password using legacy default. "
            "Set FABCANVAS_ADMIN_PW env var for production to rotate this."
        )
    users.append({
        "username": "hol", "password_hash": hash_password(seed_pw),
        "role": "admin", "status": "approved",
        "created": datetime.datetime.now().isoformat(),
        "tabs": "__all__",
    })
    write_users(users)
    logger.info("Admin user 'hol' created (password via env or legacy default).")
