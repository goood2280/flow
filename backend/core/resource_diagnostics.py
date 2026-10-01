"""Read-only launch diagnostics; never start jobs or change operator settings."""
from __future__ import annotations

import os
import sys


RESOURCE_ENV_KEYS = (
    "FLOW_RESOURCE_PROFILE", "FLOW_SYSTEM_CPU_CORES", "FLOW_SYSTEM_MEMORY_TOTAL_GB",
    "FLOW_CPU_BUDGET_CORES", "POLARS_MAX_THREADS", "FLOW_DUCKDB_THREADS",
    "FLOW_PROCESS_MEMORY_LIMIT_GB", "FLOW_PROCESS_MEMORY_LIMIT_FRACTION",
    "FLOW_MEMORY_CEILING_GB", "FLOW_MEMORY_CEILING_FRACTION", "FLOW_PROCESS_CPU_GUARD_CORES",
    "FLOW_CACHE_TOTAL_BUDGET_FRACTION", "FLOW_CACHE_MEMORY_TARGET_RATIO",
    "FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB", "FLOW_PREVIEW_MEMORY_CACHE_GB", "FLOW_PROD",
    "FLOW_CACHE_READ_LANE_SLOTS", "FLOW_SPLITTABLE_PREWARM_PROCESS", "FLOW_SPLITTABLE_PREWARM_THREADS",
    "FLOW_HEAVY_REQUEST_CONCURRENCY", "FLOW_ESSENTIAL_REQUEST_CONCURRENCY",
    "FLOW_THREADPOOL_TOKENS", "FLOW_FILEBROWSER_SQL_CONCURRENCY", "FLOW_FILEBROWSER_SQL_PER_USER",
    "FLOW_GC_FREEZE", "FLOW_JSON_FAST_ROUTES", "FLOW_LOT_PROGRESS_VECTOR",
    "FLOW_SPLITTABLE_VIEW_PACK", "FLOW_ENABLE_HEAVY_BACKGROUND_JOBS",
    "FLOW_CACHE_CHANGE_DRIVEN", "FLOW_DUCKDB_MEMORY_LIMIT_GB", "FLOW_SYSMON_ENABLE_LOAD",
)
_OFF = {"0", "false", "no", "off"}


def diagnostics(*, large_capable: bool) -> tuple[dict, list[str]]:
    from core import runtime_limits as limits
    from core import duckdb_engine, filebrowser_query_queue, scan_gate, splittable_prewarm_process, sysmon
    from core.source_digest import change_driven_enabled
    from app_v2.runtime import resource_guard

    info = resource_guard.concurrency_limits()
    info.update(
        threadpool_tokens=limits.threadpool_tokens(),
        filebrowser_sql_concurrency=filebrowser_query_queue.max_concurrent(),
        filebrowser_sql_per_user=filebrowser_query_queue.per_user_limit(),
        cache_read_lane_slots=scan_gate.read_lane_slots(),
        prewarm_process_enabled=splittable_prewarm_process.enabled(),
        prewarm_child_threads=splittable_prewarm_process.child_threads(),
        heavy_background_jobs_enabled=limits.heavy_background_jobs_enabled(),
        cache_change_driven_enabled=change_driven_enabled(),
        duckdb_threads=duckdb_engine.thread_budget(),
        duckdb_memory_limit_gb=duckdb_engine.memory_limit_gb(),
    )
    warnings: list[str] = []
    polars = sys.modules.get("polars")
    if polars is not None:
        actual = polars.thread_pool_size()
        info["polars_actual_threads"] = actual
        try:
            requested = int(os.environ.get("POLARS_MAX_THREADS", ""))
        except ValueError:
            requested = None
        if requested is not None and requested != actual:
            warnings.append(
                f"POLARS_MAX_THREADS={requested}지만 초기화된 Polars 풀은 {actual}스레드입니다. "
                "풀은 시작 시 고정되므로 감시기까지 다시 기동해야 반영됩니다."
            )

    if large_capable:
        for env, field, default in (
            ("FLOW_HEAVY_REQUEST_CONCURRENCY", "heavy_request_concurrency", resource_guard._auto_heavy_concurrency()),
            ("FLOW_ESSENTIAL_REQUEST_CONCURRENCY", "essential_request_concurrency", resource_guard._auto_essential_concurrency()),
            ("FLOW_THREADPOOL_TOKENS", "threadpool_tokens", 240 if limits.is_large_profile() else 120),
            ("FLOW_FILEBROWSER_SQL_CONCURRENCY", "filebrowser_sql_concurrency", filebrowser_query_queue._default_concurrency()),
        ):
            raw = os.environ.get(env, "").strip()
            if raw and info[field] < default:
                warnings.append(
                    f"{env}={raw}로 동시성이 {info[field]}에 고정됩니다(자동값 {default}). "
                    "이전 VM의 값이면 제거 후 감시기까지 다시 기동하세요. 의도한 제한이면 유지하세요."
                )
        if os.environ.get("FLOW_CACHE_READ_LANE_SLOTS", "").strip() and info["cache_read_lane_slots"] == 0:
            warnings.append(
                "FLOW_CACHE_READ_LANE_SLOTS=0으로 조회 캐시가 공용 스캔 슬롯을 기다립니다. "
                "이전 VM의 값이면 제거해 large 기본 조회 레인 2칸을 사용하세요."
            )
        if os.environ.get("FLOW_SPLITTABLE_PREWARM_PROCESS", "").strip().lower() in _OFF:
            warnings.append(
                "FLOW_SPLITTABLE_PREWARM_PROCESS=0으로 예열이 API 스레드에서 GIL을 공유합니다. "
                "이전 VM의 값이면 제거해 large 기본 예열 프로세스를 사용하세요."
            )
        raw_memory = os.environ.get("FLOW_DUCKDB_MEMORY_LIMIT_GB", "").strip()
        try:
            memory_cap = float(raw_memory)
        except ValueError:
            memory_cap = -1
        if limits.is_large_profile() and 0 < memory_cap < 16:
            warnings.append(
                f"FLOW_DUCKDB_MEMORY_LIMIT_GB={raw_memory}가 large 기본 16GB보다 작습니다. "
                "큰 정렬·집계는 디스크로 넘길 수 있습니다. 이전 VM의 값인지 확인하세요."
            )

    for env, field in (
        ("FLOW_GC_FREEZE", "gc_freeze_enabled"),
        ("FLOW_JSON_FAST_ROUTES", "json_fast_routes_enabled"),
        ("FLOW_LOT_PROGRESS_VECTOR", "lot_progress_vector_enabled"),
        ("FLOW_SPLITTABLE_VIEW_PACK", "splittable_view_pack_enabled"),
    ):
        enabled = os.environ.get(env, "1").strip().lower() not in _OFF
        if field == "splittable_view_pack_enabled":
            enabled = limits._env_flag(env, True) and limits._module_available("orjson")
        info[field] = enabled
        if large_capable and os.environ.get(env, "").strip().lower() in _OFF:
            warnings.append(f"{env}=0으로 성능 최적화가 꺼져 있습니다. 오류 우회용 설정이 아직 필요한지 확인하세요.")

    schedule = sysmon.get_schedule()
    info.update(
        daily_synthetic_load_schedule_enabled=bool(schedule["enabled"]),
        daily_synthetic_load_enabled=sysmon.scheduled_load_enabled(schedule),
        daily_synthetic_load_time=schedule["time"],
        daily_synthetic_load_target_pct=schedule["target_pct"],
        automatic_synthetic_load_disabled=sysmon.automatic_load_disabled(),
    )
    if info["daily_synthetic_load_enabled"]:
        warnings.append(
            "관리자 모니터의 예약 합성 부하가 켜져 있습니다. 해당 시간의 지연이면 예약을 끄거나 "
            "FLOW_SYSMON_ENABLE_LOAD=0으로 자동 합성 부하를 차단하세요."
        )
    return info, warnings
