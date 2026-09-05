"""Synthetic SplitTable hot-cache benchmark (local, isolated, no network).

This measures the native pivot-cache path plus full JSON response serialization
for 2,000 KNOB columns x 25 wafers. It is intentionally synthetic and is not a
production latency guarantee.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path


def percentile(values: list[float], p: float) -> float:
    values = sorted(values)
    idx = max(0, min(len(values) - 1, round((len(values) - 1) * p)))
    return values[idx]


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="flow-splittable-bench-")
    try:
        root = Path(tmp)
        app_root = root / "app"
        data_root = root / "data"
        db_root = root / "db"
        app_root.mkdir()
        data_root.mkdir()
        db_root.mkdir()
        os.environ.update({
            "FLOW_APP_ROOT": str(app_root),
            "FLOW_DATA_ROOT": str(data_root),
            "FLOW_DB_ROOT": str(db_root),
            "FLOW_PROD": "0",
        })

        import polars as pl
        from fastapi.encoders import jsonable_encoder
        from starlette.responses import JSONResponse
        from routers import splittable

        product = "PRODA"
        root_lot = "RL_BENCH_0001"
        wafers = list(range(1, 26))
        knob_names = [f"KNOB_{i:04d}" for i in range(2000)]
        rows = {
            "root_lot_id": [root_lot] * len(wafers),
            "wafer_id": wafers,
            "fab_lot_id": ["FAB_BENCH"] * len(wafers),
        }
        for col_idx, name in enumerate(knob_names):
            rows[name] = [f"PP_{col_idx:04d}_{w:02d}" for w in wafers]
        frame = pl.DataFrame(rows)
        pivot_path = splittable._pivot_cache_path(product, root_lot)
        pivot_path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(pivot_path)
        # Product source is only used for dependency signatures in this path.
        frame.write_parquet(db_root / f"ML_TABLE_{product}.parquet")

        # Keep the benchmark free of audit/runtime writes and background work.
        splittable._audit_split_view_search = lambda *args, **kwargs: None
        splittable._enqueue_pivot_cache_build = lambda *args, **kwargs: None
        splittable._enqueue_view_revalidate = lambda *args, **kwargs: None
        splittable._ensure_knob_s0_snapshots_today = lambda: None
        splittable._VIEW_CACHE.clear()

        def call() -> tuple[float, int, int, int]:
            started = time.perf_counter()
            payload = splittable.view_split_core(
                product=product,
                root_lot_id=root_lot,
                wafer_ids="",
                prefix="KNOB",
                custom_name="",
                view_mode="all",
                history_mode="all",
                fab_lot_id="",
                custom_cols="",
                cache_first=False,
                include_related=False,
                request=None,
            )
            response, serialize_ms, body_bytes = splittable._view_orjson_response(payload)
            if not body_bytes:
                response = JSONResponse(jsonable_encoder(payload))
                body_bytes = len(response.body)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            rows_out = payload.get("rows_compact") or []
            wafer_ids = payload.get("wafer_keys") or []
            return elapsed_ms, sum(str(row.get("_param", "")).startswith("KNOB_") for row in rows_out), len(wafer_ids), int(body_bytes)

        cold = call()
        warm = [call() for _ in range(20)]
        latencies = [row[0] for row in warm]
        result = {
            "workload": {"knob_columns": 2000, "wafers": 25, "rows_expected": 2000},
            "cold_first_query": {
                "elapsed_ms": round(cold[0], 3),
                "rows": cold[1], "distinct_wafers": cold[2], "response_bytes": cold[3],
            },
            "warm_queries": {
                "samples": len(warm),
                "p50_ms": round(percentile(latencies, 0.50), 3),
                "p95_ms": round(percentile(latencies, 0.95), 3),
                "min_ms": round(min(latencies), 3), "max_ms": round(max(latencies), 3),
                "rows_all_valid": all(row[1] == 2000 and row[2] == 25 for row in warm),
            },
            "assumptions": [
                "Synthetic native-orientation pivot parquet; every wafer has every KNOB value.",
                "Audit, cache rebuild, and stale revalidation callbacks were replaced with no-ops.",
                "No network provider, FAB history, user/runtime data, or background task was used.",
                "This benchmark does not establish a production 0.5s guarantee.",
            ],
        }
        assert cold[1] == 2000 and cold[2] == 25, cold
        assert result["warm_queries"]["rows_all_valid"], result
        out = Path(__file__).resolve().parents[2] / "deliverables" / "flow-maintenance-20260905" / "splittable_pi_benchmark.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        resolved = Path(tmp).resolve()
        if resolved.is_relative_to(Path(tempfile.gettempdir()).resolve()) and resolved.name.startswith("flow-splittable-bench-"):
            shutil.rmtree(resolved)


if __name__ == "__main__":
    main()
