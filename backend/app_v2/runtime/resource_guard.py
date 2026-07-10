from __future__ import annotations

import asyncio
import os
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.runtime_limits import (
    effective_cpu_count,
    is_small_profile,
    process_cpu_snapshot,
    process_memory_high,
    process_memory_snapshot,
)
from core import request_priority as _request_priority


# 사용자가 매 순간 필요로 하는 상호작용 read(스플릿테이블 불러오기, 파일 보기)는
# 큰 백그라운드 작업이 메모리/CPU를 점유해도 항상 열려 있어야 한다. 이 경로들은
# 메모리/CPU 가드의 "거절" 대상에서 제외하고, 대신 작은 전용 동시성 예약 레인으로
# 직렬화해 스스로 OOM 을 유발하지 않게 한다.
DEFAULT_ESSENTIAL_PREFIXES = (
    "/api/splittable/view",
    "/api/splittable/lot-ids",
    "/api/splittable/lot-candidates",
    "/api/splittable/column-values",
    "/api/splittable/cache/pivot/status",
    "/api/filebrowser/view",
    "/api/filebrowser/base-file-view",
    "/api/filebrowser/root-parquet-view",
)

# 스플릿테이블 다운로드는 현재 화면 단위(root lot 1개, wafer 최대 25행) 내보내기라
# 메모리가 가볍다. root_lot_id 가 지정된 요청만 essential 레인으로 항상 보장하고,
# root 범위가 없는 제품 전체 다운로드는 heavy 가드(실제 메모리 부족 시 거절)를 유지한다.
ESSENTIAL_IF_ROOT_SCOPED_PATHS = (
    "/api/splittable/download-csv",
    "/api/splittable/download-xlsx",
)

DEFAULT_HEAVY_PREFIXES = (
    "/api/filebrowser/view",
    "/api/filebrowser/base-file-view",
    "/api/filebrowser/root-parquet-view",
    "/api/filebrowser/download-csv",
    "/api/dashboard",
    "/api/splittable",
    "/api/tracker",
    "/api/llm/flowi",
    "/api/dbmap",
)

DEFAULT_LIGHT_PATHS = (
    "/api/splittable/match-cache/status",
    "/api/splittable/match-cache/refresh",
    "/api/splittable/products",
    "/api/splittable/source-config",
    "/api/splittable/prefixes",
    "/api/splittable/customs",
    "/api/splittable/schema",
    "/api/splittable/ml-table-match",
    "/api/splittable/fab-roots",
    "/api/splittable/knob-meta",
    "/api/splittable/vm-meta",
    "/api/splittable/inline-meta",
    "/api/splittable/precision",
    # v9.1.x: _uniques.json 파일 프록시 — 파일 read 뿐이라 light. 메모리 가드 503 시
    # 첫 화면 feature-select 카탈로그가 비는 회귀가 있어 명시적으로 통과시킨다.
    "/api/splittable/uniques",
    "/api/splittable/notes",
    "/api/splittable/history",
    "/api/splittable/operational-history",
    # v9.1.x: 스플릿테이블 편집(plan/tag/커스텀)은 작은 overlay 쓰기 — 메모리 가드로
    # 거절되면 안 되는 사용자 상호작용이므로 light 로 통과시킨다.
    "/api/splittable/plan",
    "/api/splittable/custom-tags",
    "/api/splittable/management-rows",
    "/api/splittable/paste-sets",
    "/api/splittable/rulebook",
    "/api/splittable/cache/pivot",
    "/api/tracker/et-lot-cache/status",
    "/api/llm/flowi/verify",
    "/api/llm/flowi/workflows",
)

DEFAULT_FLOWI_CHAT_PATHS = (
    "/api/llm/flowi/chat",
)

META_ONLY_PATHS = (
    "/api/filebrowser/view",
    "/api/filebrowser/base-file-view",
    "/api/filebrowser/root-parquet-view",
)

def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _float_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _prefixes() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_HEAVY_API_PREFIXES", "")
    if not raw.strip():
        return DEFAULT_HEAVY_PREFIXES
    out = tuple(p.strip() for p in raw.split(",") if p.strip())
    return out or DEFAULT_HEAVY_PREFIXES


def _essential_prefixes() -> tuple[str, ...]:
    """Interactive-read prefixes that keep a reserved lane even under load.

    Defaults cover SplitTable loading and file viewing. Operators can replace
    the set with FLOW_ESSENTIAL_API_PREFIXES or append with
    FLOW_ESSENTIAL_API_PREFIXES_EXTRA.
    """
    raw = os.environ.get("FLOW_ESSENTIAL_API_PREFIXES", "")
    if raw.strip():
        base = tuple(p.strip() for p in raw.split(",") if p.strip())
    else:
        base = DEFAULT_ESSENTIAL_PREFIXES
    extra_raw = os.environ.get("FLOW_ESSENTIAL_API_PREFIXES_EXTRA", "")
    extra = tuple(p.strip() for p in extra_raw.split(",") if p.strip())
    return (base + extra) or DEFAULT_ESSENTIAL_PREFIXES


def _auto_essential_concurrency() -> int:
    """Reserve a minimum interactive lane sized to the host's spare cores.

    Leaves compute for the OS/event loop and the heavy lane while still
    guaranteeing at least one dedicated slot for basic UI work."""
    cores = int(effective_cpu_count())
    # cores<=2 -> 1, 3-5 -> 2, 6+ -> 3. Interactive reads mostly stream cached
    # parquet, so a small reserve keeps the UI responsive without risking OOM.
    return max(1, min(3, (cores - 1) // 2))


def _light_paths() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_LIGHT_API_PATHS", "")
    extra = tuple(p.strip() for p in raw.split(",") if p.strip())
    return DEFAULT_LIGHT_PATHS + extra


def _flowi_chat_paths() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_FLOWI_CHAT_PATHS", "")
    extra = tuple(p.strip() for p in raw.split(",") if p.strip())
    return DEFAULT_FLOWI_CHAT_PATHS + extra


def _matches(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def _truthy(raw: str | None) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


class ResourceGuardMiddleware(BaseHTTPMiddleware):
    """Serialize heavy API work and reject it before the process reaches OOM.

    The app must keep manual screens usable on bounded shared hosts.  The risky
    pattern is concurrent manual data scans, not normal navigation.  This
    middleware lets light endpoints through, queues heavy endpoints, and refuses
    new heavy work when CPU or RSS is over the configured budget.
    """

    def __init__(self, app):
        super().__init__(app)
        # Metadata/list requests stay light; heavy scans run sequentially by
        # default on the bounded profile. Operators can still raise this via env.
        default_concurrency = 1 if is_small_profile() else 3
        self._concurrency = _int_env("FLOW_HEAVY_REQUEST_CONCURRENCY", default_concurrency, 1, 8)
        self._queue_timeout = _float_env("FLOW_HEAVY_REQUEST_QUEUE_TIMEOUT_SEC", 120.0, 1.0, 600.0)
        self._flowi_concurrency = _int_env("FLOW_FLOWI_CHAT_CONCURRENCY", 1, 1, 4)
        self._flowi_queue_timeout = _float_env("FLOW_FLOWI_CHAT_QUEUE_TIMEOUT_SEC", 8.0, 1.0, 60.0)
        # Essential interactive reads (SplitTable load, file view) get their own
        # reserved lane, sized from the host's spare cores, that is never blocked
        # by the memory/CPU guard.
        self._essential_concurrency = _int_env(
            "FLOW_ESSENTIAL_REQUEST_CONCURRENCY", _auto_essential_concurrency(), 1, 8
        )
        self._essential_queue_timeout = _float_env(
            "FLOW_ESSENTIAL_REQUEST_QUEUE_TIMEOUT_SEC", 60.0, 1.0, 600.0
        )
        self._memory_reserve_gb = _float_env("FLOW_MEMORY_RESERVE_GB", 1.0, 0.0, 8.0)
        self._guard_recheck_delay_sec = _float_env("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", 1.0, 0.0, 30.0)
        self._guard_retry_after_sec = _int_env("FLOW_RESOURCE_GUARD_RETRY_AFTER_SEC", 15, 1, 300)
        self._prefixes = _prefixes()
        self._light_paths = _light_paths()
        self._flowi_chat_paths = _flowi_chat_paths()
        self._essential_prefixes = _essential_prefixes()
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._flowi_semaphore = asyncio.Semaphore(self._flowi_concurrency)
        self._essential_semaphore = asyncio.Semaphore(self._essential_concurrency)
        self._active = 0
        self._flowi_active = 0
        self._essential_active = 0

    def _is_light_request(self, request: Request) -> bool:
        path = request.url.path
        if _matches(path, self._light_paths):
            return True
        if path in META_ONLY_PATHS and _truthy(request.query_params.get("meta_only")):
            return True
        return False

    def _is_essential_request(self, request: Request) -> bool:
        path = request.url.path
        if _matches(path, self._essential_prefixes):
            return True
        if path in ESSENTIAL_IF_ROOT_SCOPED_PATHS:
            return bool(str(request.query_params.get("root_lot_id") or "").strip())
        return False

    async def _run_essential(self, request: Request, call_next):
        """Serve an interactive read through the reserved lane.

        Never rejected by the memory/CPU guard — only queued behind the small
        reserved concurrency so a burst of clicks cannot exhaust RAM. Falls
        back to a clear 429 only when the reserved lane itself stays saturated."""
        try:
            await asyncio.wait_for(
                self._essential_semaphore.acquire(), timeout=self._essential_queue_timeout
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                {
                    "detail": "기본 화면 요청이 몰려 잠시 대기 중입니다. 잠시 후 다시 시도하세요.",
                    "error_code": "resource_queue_timeout",
                    "active_heavy_requests": self._essential_active,
                    "heavy_request_concurrency": self._essential_concurrency,
                    "heavy_request_group": "essential",
                },
                status_code=429,
                headers={"Retry-After": "5"},
            )
        self._essential_active += 1
        try:
            response = await call_next(request)
            response.headers.setdefault(
                "X-Flow-Heavy-Request-Concurrency", str(self._essential_concurrency)
            )
            response.headers.setdefault("X-Flow-Heavy-Request-Group", "essential")
            return response
        finally:
            self._essential_active = max(0, self._essential_active - 1)
            self._essential_semaphore.release()

    def _cpu_guard_snapshot(self) -> dict:
        snap = process_cpu_snapshot()
        return snap if bool(snap.get("process_cpu_over_limit")) else {}

    async def _delayed_cpu_guard_snapshot(self) -> dict:
        snap = self._cpu_guard_snapshot()
        if not snap:
            return {}
        if self._guard_recheck_delay_sec > 0:
            await asyncio.sleep(self._guard_recheck_delay_sec)
            snap = self._cpu_guard_snapshot()
        return snap if bool(snap.get("process_cpu_over_limit")) else {}

    async def _memory_high_after_delay(self) -> bool:
        if not process_memory_high(self._memory_reserve_gb):
            return False
        if self._guard_recheck_delay_sec > 0:
            await asyncio.sleep(self._guard_recheck_delay_sec)
            return process_memory_high(self._memory_reserve_gb)
        return True

    def _cpu_guard_response(self, snap: dict, group: str) -> JSONResponse:
        return JSONResponse(
            {
                "detail": "서버 CPU 보호로 큰 작업을 잠시 미뤘습니다. 현재 작업이 끝난 뒤 다시 실행하세요.",
                "error_code": "resource_cpu_guard",
                "heavy_request_group": group,
                **snap,
            },
            status_code=429,
            headers={"Retry-After": str(self._guard_retry_after_sec)},
        )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            # 백그라운드 작업(캐시 빌드, S3 주기 동기화)이 사용자 요청에 양보하도록
            # 사용자 활동 시각을 남긴다.
            _request_priority.note_api_request(path)
        if path.startswith("/api/") and self._is_light_request(request):
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)
        # 스플릿테이블 불러오기·파일 보기 등 기본 UI 작업은 메모리 보호 대상에서
        # 제외하고, 예약된 전용 레인으로 항상 처리한다.
        if self._is_essential_request(request):
            return await self._run_essential(request, call_next)
        is_flowi_chat = _matches(path, self._flowi_chat_paths)
        if is_flowi_chat:
            semaphore = self._flowi_semaphore
            queue_timeout = self._flowi_queue_timeout
            concurrency = self._flowi_concurrency
            group = "flowi_chat"
        elif _matches(path, self._prefixes):
            semaphore = self._semaphore
            queue_timeout = self._queue_timeout
            concurrency = self._concurrency
            group = "heavy"
        else:
            return await call_next(request)

        if await self._memory_high_after_delay():
            snap = process_memory_snapshot()
            return JSONResponse(
                {
                    "detail": "서버 메모리 보호로 큰 작업을 잠시 거절했습니다. 잠시 후 다시 실행하세요.",
                    "error_code": "resource_memory_guard",
                    **snap,
                },
                status_code=503,
                headers={"Retry-After": "30"},
            )
        cpu_snap = await self._delayed_cpu_guard_snapshot()
        if cpu_snap:
            return self._cpu_guard_response(cpu_snap, group)

        try:
            await asyncio.wait_for(semaphore.acquire(), timeout=queue_timeout)
        except asyncio.TimeoutError:
            active = self._flowi_active if group == "flowi_chat" else self._active
            return JSONResponse(
                {
                    "detail": (
                        "홈 Flow-i 요청이 이미 실행 중입니다. 현재 요청이 끝난 뒤 다시 실행하세요."
                        if group == "flowi_chat"
                        else "큰 작업이 이미 실행 중입니다. 현재 작업이 끝난 뒤 다시 실행하세요."
                    ),
                    "error_code": "resource_queue_timeout",
                    "active_heavy_requests": active,
                    "heavy_request_concurrency": concurrency,
                    "heavy_request_group": group,
                },
                status_code=429,
                headers={"Retry-After": "15"},
            )

        if group == "flowi_chat":
            self._flowi_active += 1
        else:
            self._active += 1
        try:
            if await self._memory_high_after_delay():
                snap = process_memory_snapshot()
                return JSONResponse(
                    {
                        "detail": "서버 메모리 보호로 큰 작업을 시작하지 않았습니다.",
                        "error_code": "resource_memory_guard",
                        **snap,
                    },
                    status_code=503,
                    headers={"Retry-After": "30"},
                )
            cpu_snap = await self._delayed_cpu_guard_snapshot()
            if cpu_snap:
                return self._cpu_guard_response(cpu_snap, group)
            response = await call_next(request)
            response.headers.setdefault("X-Flow-Heavy-Request-Concurrency", str(concurrency))
            response.headers.setdefault("X-Flow-Heavy-Request-Group", group)
            return response
        finally:
            if group == "flowi_chat":
                self._flowi_active = max(0, self._flowi_active - 1)
            else:
                self._active = max(0, self._active - 1)
            semaphore.release()
