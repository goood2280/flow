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
import datetime
import json
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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.routing import Match
from core.paths import PATHS
from core.auth import require_admin
from app_v2.runtime.router_loader import include_router_modules
from app_v2.runtime.resource_guard import ResourceGuardMiddleware
from app_v2.runtime.security import AuthMiddleware
from app_v2.runtime import startup as _startup_module
from app_v2.runtime.startup import ensure_seed_admin, start_background_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("flow")


def _release_version() -> str:
    """Return the semantic release version from the repository metadata."""
    for name in ("VERSION.json", "version.json"):
        version_file = _APP_ROOT / name
        if not version_file.is_file():
            continue
        try:
            payload = json.loads(version_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(payload, dict):
            value = str(payload.get("version") or "").strip()
            if value:
                return value
    return "unknown"


def _no_store_file_response(path: Path, media_type: str | None = None) -> FileResponse:
    response = FileResponse(str(path), media_type=media_type)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


class FlowStaticFiles(StaticFiles):
    """Serve built assets without sticky browser caching.

    Vite file names are hash-based, but this internal app is frequently rebuilt
    in-place while a browser tab stays open. Revalidation prevents a stale main
    bundle from asking for page chunks that no longer exist after a rebuild.
    """

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "no-cache, max-age=0, must-revalidate"
        return response

# v8.4.6: /docs, /redoc, /openapi.json disabled — API 스펙 무인증 노출 차단
app = FastAPI(
    title="flow",
    version=_release_version(),
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


app.add_middleware(AuthMiddleware)
app.add_middleware(ResourceGuardMiddleware)

# ── backend 소스 자가복구 ──
# 운영 Docker 서버에서 "덮어쓰기는 되는데 **새 파일 생성만** 막히는" 환경이
# 반복 확인됐다(dist 자가복구가 만들어진 이유와 같은 원인). 그 환경에서는
# 이미 있던 .py 는 새 버전으로 갱신되는데 **그 릴리스에서 새로 생긴 모듈만
# 안 생긴다.** 결과는 조용한 부분 장애다 — 앱은 뜨고, 그 모듈을 import 하는
# 라우터만 통째로 죽어서 해당 탭이 전부 실패한다(2026-07 `latest_lot_cache_format`
# 없음 → splittable 라우터 사망).
#
# 그래서 라우터 import 가 실패했을 때 번들에서 빠진 소스를 복원하고 재시도한다.
# 단, 라우터 파일 자체가 없으면 glob 에 잡히지 않아 import 실패도 발생하지 않는다.
# 운영에 반드시 있어야 하는 신규 기능 라우터는 discovery 전에 존재 여부를 확인한다.
# 정상 기동에는 비용이 0 이다(번들을 아예 열지 않는다).
_SOURCE_REPAIR: dict | None = None

# app.py 는 기존 파일이라 운영 배포에서 덮어써지지만, 아래 기능 파일은 해당
# 릴리스에서 처음 생긴 파일이라 생성이 막힌 운영 볼륨에 남지 않을 수 있다.
# 이 목록은 그 조용한 부분 배포를 라우터 discovery 전에 잡는 최소 부트 계약이다.
_REQUIRED_BUNDLED_BACKEND_SOURCES = (
    "backend/routers/template_report.py",
    "backend/core/matching_fill.py",
    "backend/routers/matching_fill.py",
    "backend/core/teg_map.py",
    "backend/routers/teg_map.py",
    "backend/core/yield_map.py",
    "backend/routers/yield_map.py",
    "backend/core/auto_report.py",
    "backend/core/auto_report_child.py",
    "backend/core/auto_report_history.py",
    "backend/routers/auto_report.py",
)


def _missing_required_backend_sources(app_root: Path | None = None) -> list[str]:
    root = app_root or Path(__file__).parent.parent
    return [rel for rel in _REQUIRED_BUNDLED_BACKEND_SOURCES if not (root / rel).is_file()]


def _bundle_files() -> dict:
    """setup.py 번들의 FILES 를 읽는다 (6MB — 복구 경로에서만 호출)."""
    bundle = Path(__file__).parent.parent / "setup.py"
    if not bundle.is_file():
        raise FileNotFoundError("setup.py 번들이 app 루트에 없습니다")
    ns: dict = {"__name__": "flow_source_repair", "__file__": str(bundle)}
    try:
        exec(compile(bundle.read_text(encoding="utf-8", errors="replace"), str(bundle), "exec"), ns)
    except SystemExit:
        pass
    return ns.get("FILES") or {}


def _repair_backend_sources_from_bundle() -> dict:
    """번들에는 있는데 디스크에 없는 backend 소스를 복원한다.

    **없는 파일만 만든다.** 있는 파일은 건드리지 않는다 — 배포된 소스를
    번들 스냅샷으로 되돌리는 사고를 막기 위해서다(`setup.py extract` 함정과
    같은 이유).
    """
    global _SOURCE_REPAIR
    result: dict = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "restored": [], "errors": []}
    _SOURCE_REPAIR = result
    try:
        files = _bundle_files()
    except Exception as e:
        result["errors"].append(f"번들 로드 실패: {type(e).__name__}: {e}")
        return result
    import base64 as _b64
    import gzip as _gz
    app_root = Path(__file__).parent.parent
    for rel, payload in files.items():
        rel_posix = rel.replace("\\", "/")
        if not rel_posix.endswith(".py"):
            continue
        # backend 패키지와 루트 import shim 만 — 그 밖은 extract 의 몫이다.
        if not (rel_posix.startswith("backend/")
                or rel_posix in {"core/__init__.py", "app_v2/__init__.py", "routers/__init__.py"}):
            continue
        target = app_root / rel_posix
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            blob = "".join(payload) if isinstance(payload, (list, tuple)) else payload
            target.write_bytes(_gz.decompress(_b64.b64decode(blob)))
            result["restored"].append(rel_posix)
        except OSError as e:
            result["errors"].append(f"{rel_posix}: {type(e).__name__}: {e}")
    return result


# ── Dynamic Router Loading ──
ROUTERS_DIR = Path(__file__).parent / "routers"
_missing_at_boot = _missing_required_backend_sources()
if _missing_at_boot:
    logger.warning("Required backend sources missing before router discovery — 번들 복원 시도: %s",
                   _missing_at_boot)
    try:
        _repair_backend_sources_from_bundle()
    except Exception as exc:                 # 복구는 어떤 경우에도 기동을 막지 않는다
        logger.error("Required backend source repair failed: %s: %s", type(exc).__name__, exc)
loaded, failed = include_router_modules(app, ROUTERS_DIR, logger)

if failed:
    # 라우터가 죽은 채로 서비스하지 않는다 — 빠진 모듈을 복원하고 실패분만 재시도.
    logger.warning("Router load failed (%d) — 번들에서 누락 소스 복원 시도: %s",
                   len(failed), [stem for stem, _ in failed])
    try:
        repair = _repair_backend_sources_from_bundle()
    except Exception as exc:                 # 복구는 어떤 경우에도 기동을 막지 않는다
        logger.warning(f"backend 소스 자가복구 실패(무시): {exc}")
        repair = {"restored": [], "errors": [str(exc)]}
    if repair.get("restored"):
        logger.warning("backend 소스 %d개 복원: %s — 실패 라우터 재시도",
                       len(repair["restored"]), repair["restored"][:20])
        retry_loaded, failed = include_router_modules(
            app, ROUTERS_DIR, logger, only=[stem for stem, _ in failed])
        loaded = list(loaded) + list(retry_loaded)
    elif repair.get("errors"):
        logger.error("backend 소스 복원 불가 — /deploy-info.json 의 source_repair 참고: %s",
                     repair["errors"][:5])

logger.info(f"Loaded routers: {loaded}")
if failed:
    logger.warning(f"Failed routers: {failed}")

# v7.3: log resolved paths (critical for prod vs dev confusion)
logger.info(f"flow paths — prod={PATHS.is_prod}")
logger.info(f"  app_root  = {PATHS.app_root}")
logger.info(f"  data_root = {PATHS.data_root}")
logger.info(f"  db_root   = {PATHS.db_root}")

start_background_services(logger)
try:
    from core import llm_adapter
    if llm_adapter.is_available():
        cfg = llm_adapter.get_config(redact=True)
        logger.info(f"LLM available: provider={cfg.get('provider')}, model={cfg.get('model')}")
    else:
        logger.info("LLM not configured — AI-assisted features disabled (app runs normally without LLM)")
except Exception as exc:
    logger.info(f"LLM status check skipped: {exc}")


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


def _api_route_exists(full_path: str) -> bool:
    target = f"/{full_path.lstrip('/')}"
    norm_target = target.rstrip("/")
    for route in app.routes:
        route_path = getattr(route, "path", None)
        route_fmt = getattr(route, "path_format", None)
        for candidate in (route_path, route_fmt):
            if not candidate:
                continue
            if candidate.rstrip("/") == norm_target:
                return True
    return False


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

    if path.startswith("filebrowser/base-file-save"):
        suffix = path[len("filebrowser/base-file-save"):]
        canonical = "/api/filebrowser/base-file/save" + suffix
        legacy = "/api/filebrowser/base-file-save" + suffix
        if _api_route_exists(canonical):
            return canonical
        if _api_route_exists(legacy):
            return legacy
        return canonical
    if path.startswith("filebrowser/base-file/save"):
        suffix = path[len("filebrowser/base-file/save"):]
        canonical = "/api/filebrowser/base-file/save" + suffix
        legacy = "/api/filebrowser/base-file-save" + suffix
        if _api_route_exists(canonical):
            return canonical
        if _api_route_exists(legacy):
            return legacy
        return legacy
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
    router_key = (path.split("/", 1)[0] or "").strip().replace("-", "_")
    body = {"detail": "API not found", "path": full_path}
    if router_key in failed_map:
        body = _router_failure_body(router_key, full_path, failed_map[router_key])
    return JSONResponse(body, status_code=404)


_PROCESS_STARTED_AT = datetime.datetime.now()


@app.get("/health")
def health():
    """무인증 헬스체크 — 프로세스 관리자·외부 모니터링용.

    인증을 요구하면 감시 도구가 쓸 수 없으므로 `/api/*` 밖에 둔다. 대신
    경로·환경변수 같은 내부 정보는 절대 넣지 않는다 (그건 공격자에게 지도를
    주는 셈이다 — `/runtime-roots.json` 이 그런 경우다).

    반환은 "이 프로세스가 요청을 처리할 수 있는가" 만 답한다. 200 이 아니거나
    응답이 없으면 재시작 대상이다.
    """
    uptime = (datetime.datetime.now() - _PROCESS_STARTED_AT).total_seconds()
    return JSONResponse(
        {
            "status": "ok",
            "uptime_sec": int(uptime),
            "started_at": _PROCESS_STARTED_AT.isoformat(timespec="seconds"),
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/version.json")
def serve_version():
    # Linux case-sensitive FS 대응 — VERSION.json(대문자) / version.json(소문자) 모두 시도.
    # Display version is mtime-based; keep semantic VERSION.json history under release_version.
    base = Path(__file__).parent.parent
    for name in ("VERSION.json", "version.json"):
        vp = base / name
        if vp.exists():
            try:
                modified_at = datetime.datetime.fromtimestamp(vp.stat().st_mtime).isoformat(timespec="seconds")
            except OSError:
                modified_at = ""
            try:
                meta = json.loads(vp.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            if not isinstance(meta, dict):
                meta = {}
            release_version = str(meta.get("version") or "").strip()
            if release_version:
                meta.setdefault("release_version", release_version)
            meta["version"] = modified_at or release_version or "unknown"
            meta["version_source"] = "mtime"
            if modified_at:
                meta["modified_at"] = modified_at
            response = JSONResponse(meta)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response
    return {"version": "unknown"}


@app.get("/runtime-roots.json")
def runtime_roots(_admin=Depends(require_admin)):
    """Admin-only runtime path diagnostic for deployment checks.

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


def _scheduler_health_snapshot() -> dict:
    """스케줄러 지속 실패 현황. 진단 응답이 이것 때문에 500 나면 안 된다."""
    try:
        from core.scheduler_health import snapshot
        return snapshot()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "failed": []}


@app.get("/deploy-info.json")
def deploy_info(_admin=Depends(require_admin)):
    """무인증 배포 진단 — 운영 Docker 서버에 셸 접근이 없는 환경용.

    index.html 의 부팅 진단 패널이 이 응답으로 "extract 부분 실패 / 프록시 차단 /
    프록시의 낡은 index.html 캐시" 를 브라우저 화면에서 가려낸다. 절대 경로·환경변수는
    넣지 않는다(/health 원칙) — asset 파일명은 어차피 브라우저에 내려가는 공개 정보다.
    """
    import re as _re
    dist = Path(__file__).parent.parent / "frontend" / "dist"
    index = dist / "index.html"
    refs: list[str] = []
    if index.is_file():
        try:
            html = index.read_text(encoding="utf-8", errors="replace")
            refs = sorted(set(_re.findall(r"assets/[A-Za-z0-9_.-]+[.](?:js|css)", html)))
        except OSError:
            pass
    assets_dir = dist / "assets"
    present: list[str] = []
    if assets_dir.is_dir():
        try:
            present = sorted(p.name for p in assets_dir.iterdir() if p.is_file())
        except OSError:
            pass
    version = None
    for name in ("VERSION.json", "version.json"):
        vp = Path(__file__).parent.parent / name
        if vp.is_file():
            try:
                version = json.loads(vp.read_text(encoding="utf-8")).get("version")
                break
            except (OSError, ValueError):
                continue
    report = None
    rp = Path(__file__).parent.parent / "extract_report.json"
    if rp.is_file():
        try:
            report = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {"error": "extract_report.json 파싱 실패"}
    disk_free_mb = None
    try:
        import shutil as _sh
        disk_free_mb = _sh.disk_usage(str(Path(__file__).parent.parent)).free // (1024 * 1024)
    except OSError:
        pass
    # 서빙 프로세스가 assets 디렉터리에 "새 파일"을 만들 수 있는지 직접 검사.
    # 덮어쓰기는 되는데 생성만 막히는 권한/읽기전용/볼륨 환경을 가려낸다.
    can_create: dict = {"ok": False, "error": None}
    try:
        probe = assets_dir / ".flow_write_probe.tmp"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"probe")
        probe.unlink()
        can_create["ok"] = True
    except OSError as e:
        can_create["error"] = f"{type(e).__name__}: {e}"
    return JSONResponse(
        {
            "version": version,
            "index_exists": index.is_file(),
            "index_refs": refs,
            "assets_present": present,
            "missing": [r for r in refs if not (dist / r).is_file()],
            "extract_report": report,
            "disk_free_mb": disk_free_mb,
            "self_repair": _SELF_REPAIR,
            "source_repair": _SOURCE_REPAIR,
            # 라우터가 죽으면 해당 탭이 통째로 실패한다. 셸 없는 운영 서버에서
            # "왜 이 탭만 안 되나"를 브라우저만으로 가려내려면 여기 있어야 한다.
            "routers_loaded": len(loaded),
            "routers_failed": [
                {"router": stem, "error": _router_error_summary(detail)}
                for stem, detail in failed
            ],
            # 스케줄러가 죽으면 화면은 멀쩡한데 캐시만 안 채워진다(신규 제품 랏
            # 목록·WIP 이 계속 비는 증상). 로그가 아니라 여기서 보여야 잡힌다.
            "schedulers_failed": list(_startup_module.SCHEDULER_ERRORS),
            # 며칠째 죽어 있는지 + 관리자 알림 발송 여부. 재시작하면
            # SCHEDULER_ERRORS 는 이번 기동 기준으로 초기화되지만 이쪽은 누적된다.
            "scheduler_health": _scheduler_health_snapshot(),
            "background_owner": __import__(
                "core.background_owner", fromlist=["snapshot"]
            ).snapshot(),
            "can_create_in_assets": can_create,
            "server_time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        headers={"Cache-Control": "no-store, max-age=0"},
    )


# ── dist 자가복구 ──
# 운영 Docker 서버에서 "index.html 은 새 버전인데 그 짝인 새 해시의 JS 만 계속
# 없다"는 장애가 반복됐다. extract 로그도 report 도 남지 않아(새 파일 생성이
# 막히는 환경으로 추정) 원인을 밖에서 고칠 수 없다 — 대신 앱 기동 시 setup.py
# 번들에서 빠진 dist 파일을 실행 중인 프로세스가 직접 복원한다. 이 프로세스가
# 서빙하는 바로 그 경로에 쓰므로, 볼륨 마운트든 경로 불일치든 이 위치가 진실이다.
# 결과는 /deploy-info.json 의 self_repair 로 노출된다.
_SELF_REPAIR: dict | None = None


def _repair_dist_from_bundle() -> None:
    global _SELF_REPAIR
    dist = Path(__file__).parent.parent / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return
    import re as _re
    try:
        refs = _re.findall(r"assets/[A-Za-z0-9_.-]+[.](?:js|css)", index.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return
    if all((dist / r).is_file() for r in refs):
        return  # 정상 배포 — 아무 것도 하지 않는다 (일반 기동 경로에 비용 없음)

    result: dict = {"at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "repaired": [], "errors": []}
    _SELF_REPAIR = result
    bundle = Path(__file__).parent.parent / "setup.py"
    if not bundle.is_file():
        result["errors"].append("setup.py 번들이 app 루트에 없어 자가복구 불가")
        return
    try:
        import base64 as _b64
        import gzip as _gz
        ns: dict = {"__name__": "flow_dist_repair", "__file__": str(bundle)}
        exec(compile(bundle.read_text(encoding="utf-8", errors="replace"), str(bundle), "exec"), ns)
        files = ns.get("FILES") or {}
    except SystemExit:
        files = ns.get("FILES") or {}
    except Exception as e:
        result["errors"].append(f"번들 로드 실패: {type(e).__name__}: {e}")
        return
    for rel, payload in files.items():
        rel_posix = rel.replace("\\", "/")
        if not rel_posix.startswith("frontend/dist/"):
            continue
        target = Path(__file__).parent.parent / rel_posix
        if target.is_file():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            blob = "".join(payload) if isinstance(payload, (list, tuple)) else payload
            target.write_bytes(_gz.decompress(_b64.b64decode(blob)))
            result["repaired"].append(rel_posix)
        except OSError as e:
            result["errors"].append(f"{rel_posix}: {type(e).__name__}: {e}")
    logger.warning(
        f"dist self-repair: {len(result['repaired'])}개 복원, "
        f"{len(result['errors'])}개 실패 — /deploy-info.json 참고"
    )


try:
    _repair_dist_from_bundle()
except Exception as _e:  # 자가복구는 어떤 경우에도 앱 기동을 막지 않는다
    logger.warning(f"dist self-repair 실패(무시): {_e}")

# ── Serve React build ──
DIST = Path(__file__).parent.parent / "frontend" / "dist"
if DIST.exists():
    if (DIST / "assets").exists():
        app.mount("/assets", FlowStaticFiles(directory=str(DIST / "assets")), name="assets")

    # 정적 자산은 SPA 로 폴백하지 않는다. 폴백하면 없는 .js 요청에 index.html 이
    # 200 으로 돌아가고, 브라우저는 MIME 불일치로 모듈을 거부해 "흰 화면 + 네트워크는
    # 전부 200" 이라는 진단 불가능한 상태가 된다. 없으면 404 로 드러낸다.
    _STATIC_SUFFIXES = {
        ".js", ".mjs", ".cjs", ".css", ".map", ".wasm",
        ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".avif",
        ".woff", ".woff2", ".ttf", ".eot",
    }

    def _looks_static(path: str) -> bool:
        return path.startswith("assets/") or Path(path).suffix.lower() in _STATIC_SUFFIXES

    def _spa_index():
        index = DIST / "index.html"
        if not index.is_file():
            raise HTTPException(
                503,
                "frontend/dist/index.html 이 없습니다. 배포가 완료되지 않았거나 "
                "프런트엔드 빌드 산출물이 누락됐습니다.",
            )
        return _no_store_file_response(index)

    @app.get("/{path:path}")
    def serve_spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API not found")
        # v8.4.6: traversal 방어 — DIST 를 벗어나는 경로는 SPA index 로 폴백
        try:
            fp = (DIST / path).resolve()
            fp.relative_to(DIST.resolve())
        except (ValueError, OSError):
            return _spa_index()
        if fp.is_file():
            if fp.name == "index.html":
                return _no_store_file_response(fp)
            return FileResponse(str(fp))
        if _looks_static(path):
            raise HTTPException(404, f"static asset not found: {path}")
        return _spa_index()

ensure_seed_admin(logger)
