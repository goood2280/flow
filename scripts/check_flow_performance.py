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
    from core.paths import PATHS
    import polars as pl

    info, warnings = runtime_limits.host_diagnostics({
        "FLOW_DATA_ROOT": PATHS.data_root,
        "FLOW_DB_ROOT": PATHS.db_root,
        "FLOW_WAFER_MAP_ROOT": PATHS.wafer_map_root,
    })
    info["polars_actual_threads"] = pl.thread_pool_size()
    return {"basis": "next_launch_environment", "resources": info, "warnings": warnings}


def main() -> int:
    print(json.dumps(collect(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
