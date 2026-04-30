from __future__ import annotations

import asyncio
import os
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from core.runtime_limits import is_small_profile, process_memory_high, process_memory_snapshot


DEFAULT_HEAVY_PREFIXES = (
    "/api/filebrowser/view",
    "/api/filebrowser/base-file-view",
    "/api/filebrowser/root-parquet-view",
    "/api/filebrowser/download-csv",
    "/api/dashboard",
    "/api/splittable",
    "/api/tracker",
    "/api/ettime",
    "/api/llm/flowi",
    "/api/dbmap",
    "/api/waferlayout",
)

DEFAULT_LIGHT_PATHS = (
    "/api/splittable/match-cache/status",
    "/api/splittable/match-cache/refresh",
    "/api/tracker/et-lot-cache/status",
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


def _matches(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


class ResourceGuardMiddleware(BaseHTTPMiddleware):
    """Serialize heavy API work and reject it before the process reaches OOM.

    The app must keep manual screens usable on small 4-core / 16GB hosts.  The
    risky pattern is concurrent manual data scans, not normal navigation.  This
    middleware lets light endpoints through, queues heavy endpoints, and refuses
    new heavy work when the process is near its configured RSS budget.
    """

    def __init__(self, app):
        super().__init__(app)
        default_concurrency = 1 if is_small_profile() else 2
        self._concurrency = _int_env("FLOW_HEAVY_REQUEST_CONCURRENCY", default_concurrency, 1, 8)
        self._queue_timeout = _float_env("FLOW_HEAVY_REQUEST_QUEUE_TIMEOUT_SEC", 120.0, 1.0, 600.0)
        self._memory_reserve_gb = _float_env("FLOW_MEMORY_RESERVE_GB", 1.0, 0.0, 8.0)
        self._prefixes = _prefixes()
        self._light_paths = _light_paths()
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._active = 0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and _matches(path, self._light_paths):
            return await call_next(request)
        if not path.startswith("/api/") or not _matches(path, self._prefixes):
            return await call_next(request)

        if process_memory_high(self._memory_reserve_gb):
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

        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self._queue_timeout)
        except asyncio.TimeoutError:
            return JSONResponse(
                {
                    "detail": "큰 작업이 이미 실행 중입니다. 현재 작업이 끝난 뒤 다시 실행하세요.",
                    "error_code": "resource_queue_timeout",
                    "active_heavy_requests": self._active,
                    "heavy_request_concurrency": self._concurrency,
                },
                status_code=429,
                headers={"Retry-After": "15"},
            )

        self._active += 1
        try:
            if process_memory_high(self._memory_reserve_gb):
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
            response = await call_next(request)
            response.headers.setdefault("X-Flow-Heavy-Request-Concurrency", str(self._concurrency))
            return response
        finally:
            self._active = max(0, self._active - 1)
            self._semaphore.release()
