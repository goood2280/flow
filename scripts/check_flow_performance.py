"""Inspect launch-time Flow resource limits without starting scans or services.

Run through scripts/windows/flow_ctl.bat perf so flow_env.local.bat is loaded.
This reports the next launch's environment; it cannot inspect an already
running supervisor's inherited environment. No operator setting is changed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def collect() -> dict:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    from core import runtime_limits

    # Native pools must be configured before importing DuckDB/Polars.
    runtime_limits.apply_runtime_limits()
    from core import cache_budget, duckdb_engine, sysmon
    from core.paths import PATHS
    from app_v2.runtime.resource_guard import ResourceGuardMiddleware
    import polars as pl

    info, warnings = runtime_limits.host_diagnostics({
        "FLOW_DATA_ROOT": PATHS.data_root,
        "FLOW_DB_ROOT": PATHS.db_root,
        "FLOW_WAFER_MAP_ROOT": PATHS.wafer_map_root,
    })
    guard = ResourceGuardMiddleware(lambda *args: None)
    schedule = sysmon.get_schedule()
    info.update(
        polars_actual_threads=pl.thread_pool_size(),
        duckdb_threads=duckdb_engine._thread_count(),
        cache_pool_gb=round(cache_budget.pool_bytes() / 1024**3, 2),
        heavy_request_concurrency=guard._concurrency,
        essential_request_concurrency=guard._essential_concurrency,
        daily_synthetic_load_enabled=bool(schedule["enabled"] and PATHS.is_prod),
        daily_synthetic_load_time=schedule["time"],
        daily_synthetic_load_target_pct=schedule["target_pct"],
    )
    if info["daily_synthetic_load_enabled"]:
        warnings.append(
            "관리자 모니터의 예약 합성 부하가 켜져 있습니다. 해당 시간의 지연이면 예약 설정을 확인하세요. "
            "FLOW_SYSMON_ENABLE_LOAD=0 은 이 예약을 끄지 않습니다."
        )
    return {"basis": "next_launch_environment", "resources": info, "warnings": warnings}


def main() -> int:
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
