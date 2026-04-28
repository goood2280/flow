"""flow Backend v8.7.3 — uvicorn app:app --host 0.0.0.0 --port 8080.

v8.4.6 보안 패치:
  - 세션 토큰 기반 인증 미들웨어: 모든 /api/* 호출은 X-Session-Token 필요
    (login/register/reset-request/logout 만 exempt).
  - FastAPI OpenAPI/docs 비활성화 (내부 API 스펙 노출 차단).
  - 보안 헤더 추가: X-Content-Type-Options, X-Frame-Options, Referrer-Policy.
  - Seed admin 비밀번호는 환경변수 FLOW_ADMIN_PW 우선, 미지정 시 임시값 + 경고.
  - Password 해시: SHA-256 → PBKDF2-HMAC-SHA256 (salted). 레거시 해시는 로그인 시 자동 업그레이드.

v8.7.3 hotfix:
  - admin.py `Any` import 누락으로 admin 라우터 전체가 import-time NameError 던지던
    치명적 버그 수정. 유저/관리자 단위기능 전수 점검 통과.
"""
import logging
import os
from pathlib import Path
import sys

_BACKEND_ROOT = Path(__file__).resolve().parent
_APP_ROOT = _BACKEND_ROOT.parent


def _prepend_sys_path(path: Path) -> None:
    raw = str(path)
    sys.path[:] = [p for p in sys.path if p != raw]
    sys.path.insert(0, raw)


def _package_paths(module: object) -> list[Path]:
    paths = getattr(module, "__path__", None)
    if not paths:
        return []
    out: list[Path] = []
    for raw in paths:
        try:
            out.append(Path(raw).resolve())
        except OSError:
            continue
    return out


def _clear_stale_package(package_name: str, package_dir: Path) -> None:
    """Drop cached top-level packages from a different Flow checkout."""
    package_dir = package_dir.resolve()
    existing = sys.modules.get(package_name)
    if existing is None or package_dir in _package_paths(existing):
        return
    for name in list(sys.modules):
        if name == package_name or name.startswith(package_name + "."):
            sys.modules.pop(name, None)


for _path in (_APP_ROOT, _BACKEND_ROOT):
    _prepend_sys_path(_path)
for _package, _dir in (
    ("core", _BACKEND_ROOT / "core"),
    ("app_v2", _BACKEND_ROOT / "app_v2"),
    ("routers", _BACKEND_ROOT / "routers"),
):
    if _dir.is_dir():
        _clear_stale_package(_package, _dir)

try:
    from core.runtime_limits import apply_runtime_limits
    apply_runtime_limits()
except Exception:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match
from core.paths import PATHS
from app_v2.runtime.router_loader import include_router_modules
from app_v2.runtime.security import AuthMiddleware
from app_v2.runtime.startup import ensure_seed_admin, start_background_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flow")

# v8.4.6: /docs, /redoc, /openapi.json disabled — API 스펙 무인증 노출 차단
app = FastAPI(
    title="flow",
    version="9.0.3",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


app.add_middleware(AuthMiddleware)

# ── Dynamic Router Loading ──
ROUTERS_DIR = Path(__file__).parent / "routers"
loaded, failed = include_router_modules(app, ROUTERS_DIR, logger)

logger.info(f"Loaded routers: {loaded}")
if failed:
    logger.warning(f"Failed routers: {failed}")

# v7.3: log resolved paths (critical for prod vs dev confusion)
logger.info(f"flow paths — prod={PATHS.is_prod}")
logger.info(f"  app_root  = {PATHS.app_root}")
logger.info(f"  data_root = {PATHS.data_root}")
logger.info(f"  db_root   = {PATHS.db_root}")

start_background_services(logger)


def _allowed_methods_for_path(path: str, method: str) -> set[str]:
    """Return methods for a registered API path when the current method missed.

    This keeps the API fallback below from turning a real method mismatch into
    a misleading generic 404 while still preventing the SPA catch-all from
    surfacing as 405 for missing API POST routes.
    """
    scope = {"type": "http", "path": path, "method": method, "root_path": "", "headers": []}
    allowed: set[str] = set()
    for route in app.routes:
        endpoint_name = getattr(getattr(route, "endpoint", None), "__name__", "")
        if endpoint_name in {"api_not_found", "serve_spa"}:
            continue
        try:
            match, _child_scope = route.matches(scope)
        except Exception:
            continue
        if match is Match.PARTIAL:
            allowed.update(getattr(route, "methods", set()) or set())
    allowed.discard("HEAD")
    return allowed


def _compat_api_path(path: str) -> str:
    """Map legacy singular/plural API prefixes to their canonical routers."""
    compat_prefixes = {
        "inform": "/api/informs",
        "meeting": "/api/meetings",
        "trackers": "/api/tracker",
        "issue-tracker": "/api/tracker",
        "issue-tracking": "/api/tracker",
    }
    for prefix, target in compat_prefixes.items():
        if path == prefix or path.startswith(prefix + "/"):
            return target + path[len(prefix):]
    if path == "issues":
        return "/api/tracker/issues"
    if path.startswith("issues/"):
        return "/api/tracker/issues/" + path[len("issues/"):]
    return ""


def _router_error_summary(detail: str) -> str:
    error_type = ""
    error = ""
    for line in str(detail or "").splitlines():
        if line.startswith("error_type="):
            error_type = line.split("=", 1)[1].strip()
        elif line.startswith("error="):
            error = line.split("=", 1)[1].strip()
    if error_type and error:
        return f"{error_type}: {error}"
    if error:
        return error
    lines = [line.strip() for line in str(detail or "").splitlines() if line.strip()]
    return lines[-1] if lines else "unknown router import error"


def _router_failure_body(router_key: str, full_path: str, detail: str) -> dict:
    summary = _router_error_summary(detail)
    return {
        "detail": f"API router '{router_key}' failed to load: {summary}",
        "path": full_path,
        "error_code": "router_load_failed",
        "router": router_key,
        "router_error_summary": summary,
        "router_error": detail,
    }


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def api_not_found(path: str, request: Request):
    """JSON fallback for unmatched API calls.

    Without this route, an unmatched POST under /api can be reported by
    Starlette as Method Not Allowed because the SPA GET catch-all also matches
    the path. Operators then see a confusing 405 instead of the missing API
    route that actually needs attention.
    """
    full_path = f"/api/{path}"
    compat_path = _compat_api_path(path)
    if compat_path:
        return RedirectResponse(str(request.url.replace(path=compat_path)), status_code=307)

    allowed = _allowed_methods_for_path(full_path, request.method)
    if allowed:
        allow = ", ".join(sorted(allowed))
        return JSONResponse(
            {"detail": "Method Not Allowed", "path": full_path, "allowed_methods": sorted(allowed)},
            status_code=405,
            headers={"Allow": allow},
        )

    failed_map = {name: err for name, err in failed}
    router_key = (path.split("/", 1)[0] or "").strip()
    body = {"detail": "API not found", "path": full_path}
    if router_key in failed_map:
        body = _router_failure_body(router_key, full_path, failed_map[router_key])
    return JSONResponse(body, status_code=404)


@app.get("/version.json")
def serve_version():
    # v8.7.6: Linux case-sensitive FS 대응 — VERSION.json(대문자) / version.json(소문자) 모두 시도.
    base = Path(__file__).parent.parent
    for name in ("VERSION.json", "version.json"):
        vp = base / name
        if vp.exists():
            return FileResponse(str(vp), media_type="application/json")
    return {"version": "unknown"}


@app.get("/runtime-roots.json")
def runtime_roots():
    """Unauthenticated runtime path diagnostic for local deployment checks.

    This is intentionally outside /api so an operator can verify which checkout
    and DB root the currently running uvicorn process is using from a browser.
    """
    try:
        from core import roots
        snap = roots.snapshot()
    except Exception:
        snap = {}
    db_root = Path(snap.get("db_root") or PATHS.db_root)
    ml_files = []
    if db_root.is_dir():
        for fp in sorted(db_root.glob("ML_TABLE_*.parquet")):
            try:
                st = fp.stat()
                ml_files.append({
                    "name": fp.name,
                    "path": str(fp),
                    "size": st.st_size,
                    "modified": st.st_mtime,
                })
            except OSError:
                pass
    return {
        "app_file": str(Path(__file__).resolve()),
        "cwd": os.getcwd(),
        "env": {
            "FLOW_APP_ROOT": os.environ.get("FLOW_APP_ROOT", ""),
            "FLOW_DATA_ROOT": os.environ.get("FLOW_DATA_ROOT", ""),
            "FLOW_DB_ROOT": os.environ.get("FLOW_DB_ROOT", ""),
        },
        "paths": {
            "app_root": str(PATHS.app_root),
            "data_root": str(PATHS.data_root),
            "db_root": str(PATHS.db_root),
            "base_root": str(PATHS.base_root),
            **snap,
        },
        "ml_table_files": ml_files,
        "frontend_dist": str((Path(__file__).parent.parent / "frontend" / "dist").resolve()),
    }


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

ensure_seed_admin(logger)
