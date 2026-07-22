from __future__ import annotations

import asyncio
import os
from typing import Iterable

from fastapi import Request
from fastapi.responses import JSONResponse, Response
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
    # v9.4.x: RAM 캐시 관리 페이지 — 아래 5개는 상주 캐시의 크기/메타데이터만
    # 집계해 반환한다(신규 데이터 적재·parquet 스캔 없음). 서버가 바쁠 때일수록
    # 봐야 하는 모니터링 화면이라 CPU/메모리 가드 대상에서 제외한다. prefix 매칭이라
    # priority-lots/save·product-budgets/save(소규모 JSON 쓰기, page_manager 권한)도
    # 함께 light — plan/custom-tags 쓰기와 같은 선례. 실제 스캔 가능성이 있는
    # ram-cache/lot-status·knob-allocation 은 의도적으로 heavy 유지.
    "/api/splittable/ram-cache/overview",
    "/api/splittable/ram-cache/contents",
    "/api/splittable/ram-cache/priority-lots",
    "/api/splittable/ram-cache/product-budgets",
    "/api/splittable/memory/overview",
    "/api/splittable/product-cache/status",
    "/api/splittable/root-lot-cache/status",
    "/api/splittable/cache-event-log",
    "/api/splittable/query-workers",
    "/api/splittable/ram-cache/unified-scan",
    "/api/splittable/ram-cache/scan-status",
    "/api/splittable/cache-budget/settings",
    "/api/splittable/cache-budget/settings/save",
    # v9.1.x: 스플릿테이블 편집(plan/tag/커스텀)은 작은 overlay 쓰기 — 메모리 가드로
    # 거절되면 안 되는 사용자 상호작용이므로 light 로 통과시킨다.
    "/api/splittable/plan",
    "/api/splittable/custom-tags",
    "/api/splittable/management-rows",
    "/api/splittable/paste-sets",
    "/api/splittable/rulebook",
    "/api/splittable/cache/pivot",
    "/api/tracker/et-lot-cache/status",
    # 단일 관리 파일(rulebook/matching CSV, ~수 MB) 저장·검증·롤백은 사용자
    # 최우선 작업 — 파생 캐시/S3 sync 는 이미 백그라운드라 응답이 가볍다.
    # 메모리 가드와 무관하게 항상 처리되도록 명시적으로 light 계약에 둔다.
    "/api/filebrowser/base-file/save",
    "/api/filebrowser/base-file-save",
    "/api/filebrowser/base-file/text-save",
    "/api/filebrowser/base-file/validate",
    "/api/filebrowser/base-file/rollback",
    "/api/filebrowser/settings",
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

    Essential reads (SplitTable view, file viewer) are mostly I/O-bound
    (streaming cached parquet), so they can share cores without saturating
    CPU.  The previous formula ``(cores-1)//2`` gave only 2 slots on a
    5-core host, causing frequent 429s when a user opened several tabs.
    ``(cores+1)//2`` keeps at least one core free for the OS/event loop
    while giving enough slots to handle a small burst of UI reads:
    cores  1→1  2→1  3→2  4→2  5→3  6→3  7→4  8→4"""
    cores = int(effective_cpu_count())
    return max(1, min(4, (cores + 1) // 2))


def _light_paths() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_LIGHT_API_PATHS", "")
    extra = tuple(p.strip() for p in raw.split(",") if p.strip())
    return DEFAULT_LIGHT_PATHS + extra


def _flowi_chat_paths() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_FLOWI_CHAT_PATHS", "")
    extra = tuple(p.strip() for p in raw.split(",") if p.strip())
    return DEFAULT_FLOWI_CHAT_PATHS + extra


async def _try_upstream_proxy(request: Request) -> Response | None:
    """worker(개발서버) 역할이면 SplitTable 검색 read 를 운영서버로 위임.

    운영의 예열된 RAM 캐시를 활용해 개발서버가 자체 캐시 없이도 빠르게
    응답한다 (cache_budget worker 축소 계수와 한 쌍). 실패/비대상이면 None —
    호출측이 기존 로컬 경로를 그대로 탄다. 블로킹 urllib 호출은 스레드로
    내려 이벤트 루프를 막지 않는다."""
    try:
        from core import upstream_proxy

        if not upstream_proxy.should_proxy(
            request.url.path, request.method, request.headers
        ):
            return None
        result = await asyncio.to_thread(
            upstream_proxy.forward,
            request.url.path,
            request.url.query,
            request.headers.get("x-session-token", ""),
        )
    except Exception:
        return None
    if result is None:
        return None
    status_code, body, content_type = result
    return Response(
        content=body,
        status_code=status_code,
        media_type=content_type,
        headers={"X-Flow-Upstream-Proxy": "hit"},
    )


def _trigger_emergency_evict(reason: str) -> None:
    """메모리 가드 503 시 긴급 캐시 축출 요청 — 503 만 돌려주고 방치하면
    캐시가 점유한 메모리는 저절로 줄지 않아 계속 503 이다. 워치독에 축출을
    맡겨(자체 스레드·디바운스) 다음 재시도가 통과할 공간을 만든다."""
    try:
        from core import memory_watchdog

        memory_watchdog.request_emergency_evict(reason)
    except Exception:
        pass


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
        # Metadata/list requests stay light; heavy scans are serialised on tiny
        # hosts but get 2 slots once the host has ≥5 cores.
        # cores  ≤4 → 1  5-6 → 2  7+ → 3.  Override with env var if needed.
        default_concurrency = (
            max(1, min(3, (int(effective_cpu_count()) - 1) // 2))
            if is_small_profile()
            else 3
        )
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
            "FLOW_ESSENTIAL_REQUEST_QUEUE_TIMEOUT_SEC", 90.0, 1.0, 600.0
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
            # worker(개발서버) 역할이면 검색 read 를 운영서버로 먼저 위임 시도.
            proxied = await _try_upstream_proxy(request)
            if proxied is not None:
                return proxied
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
            _trigger_emergency_evict("memory_guard_503")
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
                _trigger_emergency_evict("memory_guard_503")
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
