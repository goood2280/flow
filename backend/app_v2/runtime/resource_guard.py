from __future__ import annotations

import asyncio
import os
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.runtime_limits import is_small_profile, process_cpu_snapshot, process_memory_high, process_memory_snapshot


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
    "/api/splittable/lot-candidates",
    "/api/splittable/lot-ids",
    "/api/splittable/ml-table-match",
    "/api/splittable/fab-roots",
    "/api/splittable/knob-meta",
    "/api/splittable/vm-meta",
    "/api/splittable/inline-meta",
    "/api/splittable/precision",
    "/api/splittable/notes",
    "/api/splittable/history",
    "/api/splittable/operational-history",
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
        self._memory_reserve_gb = _float_env("FLOW_MEMORY_RESERVE_GB", 1.0, 0.0, 8.0)
        self._guard_recheck_delay_sec = _float_env("FLOW_RESOURCE_GUARD_RECHECK_DELAY_SEC", 1.0, 0.0, 30.0)
        self._guard_retry_after_sec = _int_env("FLOW_RESOURCE_GUARD_RETRY_AFTER_SEC", 15, 1, 300)
        self._prefixes = _prefixes()
        self._light_paths = _light_paths()
        self._flowi_chat_paths = _flowi_chat_paths()
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._flowi_semaphore = asyncio.Semaphore(self._flowi_concurrency)
        self._active = 0
        self._flowi_active = 0

    def _is_light_request(self, request: Request) -> bool:
        path = request.url.path
        if _matches(path, self._light_paths):
            return True
        if path in META_ONLY_PATHS and _truthy(request.query_params.get("meta_only")):
            return True
        return False

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
        if path.startswith("/api/") and self._is_light_request(request):
            return await call_next(request)
        if not path.startswith("/api/"):
            return await call_next(request)
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
