#!/usr/bin/env python3
"""Generate 20-50MB parquet fixtures and benchmark Flow data browsing paths.

The script uses a temporary FLOW_DB_ROOT/FLOW_DATA_ROOT so it does not touch the
operator DB. It measures the paths that must stay responsive on real data:

  - FileBrowser single-file metadata and first-page reads
  - FileBrowser partitioned DB metadata and first-page reads
  - SplitTable product list, schema, lot candidates, and one lot view

Example:
  python3 scripts/perf_flow_data_paths.py --sizes-mb 20 50
"""
from __future__ import annotations

import argparse
import gc
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

import polars as pl

try:
    import numpy as np
except Exception:  # pragma: no cover - fallback only for stripped envs
    np = None


ROOT = Path(__file__).resolve().parents[1]


def _size_mb(path: Path) -> float:
    return path.stat().st_size / (1024 * 1024)


def _rss_mb() -> float:
    try:
        import psutil  # type: ignore

        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def _rand_float_columns(rows: int, count: int, seed: int, prefix: str) -> dict[str, object]:
    if np is not None:
        rng = np.random.default_rng(seed)
        return {
            f"{prefix}_{i:03d}": rng.normal(loc=i * 0.1, scale=1.0, size=rows).round(6)
            for i in range(count)
        }
    import random

    rng = random.Random(seed)
    return {
        f"{prefix}_{i:03d}": [round(rng.gauss(i * 0.1, 1.0), 6) for _ in range(rows)]
        for i in range(count)
    }


def _make_ml_frame(product: str, rows: int, seed: int) -> pl.DataFrame:
    roots = max(1, (rows + 24) // 25)
    root_vals = [f"R{i:05d}" for i in range(roots) for _ in range(25)]
    root_vals = root_vals[:rows]
    wafer_vals = [(i % 25) + 1 for i in range(rows)]
    data: dict[str, object] = {
        "product": [product] * rows,
        "root_lot_id": root_vals,
        "lot_id": [f"{r}A.1" for r in root_vals],
        "wafer_id": wafer_vals,
    }
    for i in range(18):
        data[f"KNOB_{i:03d}"] = [f"K{(j + i) % 13:02d}" for j in range(rows)]
    for i in range(8):
        data[f"MASK_{i:03d}"] = [f"M{(j + i) % 7:02d}" for j in range(rows)]
    data.update(_rand_float_columns(rows, 42, seed, "INLINE"))
    data.update(_rand_float_columns(rows, 18, seed + 777, "VM"))
    return pl.DataFrame(data)


def _write_target_size(path: Path, product: str, target_mb: int, seed: int) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = max(2_000, int(target_mb * 1_800))
    last_size = 0.0
    for _ in range(8):
        df = _make_ml_frame(product, rows, seed)
        tmp = path.with_suffix(path.suffix + ".tmp")
        df.write_parquet(tmp)
        tmp.replace(path)
        last_size = _size_mb(path)
        del df
        gc.collect()
        if target_mb * 0.9 <= last_size <= target_mb * 1.25:
            return rows
        scale = target_mb / max(last_size, 0.1)
        rows = max(1_000, int(rows * max(0.4, min(2.8, scale))))
    return rows


def _make_fab_frame(product: str, rows: int, seed: int, day: int) -> pl.DataFrame:
    roots = max(1, (rows + 24) // 25)
    root_vals = [f"R{i:05d}" for i in range(roots) for _ in range(25)]
    root_vals = root_vals[:rows]
    wafer_vals = [(i % 25) + 1 for i in range(rows)]
    data: dict[str, object] = {
        "product": [product] * rows,
        "root_lot_id": root_vals,
        "lot_id": [f"{r}A.1" for r in root_vals],
        "fab_lot_id": [f"{r}A.1" for r in root_vals],
        "wafer_id": wafer_vals,
        "step_id": [f"ST{i % 80:03d}" for i in range(rows)],
        "process_id": [f"P{i % 12:02d}" for i in range(rows)],
        "tkout_time": [f"2026-04-{day:02d}T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}" for i in range(rows)],
        "eqp_id": [f"EQP{i % 20:02d}" for i in range(rows)],
        "ppid": [f"PPID{i % 31:02d}" for i in range(rows)],
    }
    data.update(_rand_float_columns(rows, 60, seed, "FAB_PARAM"))
    return pl.DataFrame(data)


def _write_fab_history(db_root: Path, product: str, target_mb: int, seed: int) -> tuple[Path, int, float]:
    product_dir = db_root / "1.RAWDATA_DB_FAB" / product
    product_dir.mkdir(parents=True, exist_ok=True)
    rows = max(2_000, int(target_mb * 1_700))
    first_path = product_dir / "date=20260427" / "part_0.parquet"
    last_size = 0.0
    for _ in range(8):
        df = _make_fab_frame(product, rows, seed, day=27)
        part_rows = max(1, (rows + 3) // 4)
        paths: list[Path] = []
        for idx in range(4):
            out_dir = product_dir / f"date=202604{27 + idx:02d}"
            out_dir.mkdir(parents=True, exist_ok=True)
            fp = out_dir / "part_0.parquet"
            df.slice(idx * part_rows, part_rows).write_parquet(fp)
            paths.append(fp)
        last_size = sum(_size_mb(p) for p in paths if p.exists())
        first_path = paths[0]
        del df
        gc.collect()
        if target_mb * 0.9 <= last_size <= target_mb * 1.25:
            return first_path, rows, last_size
        scale = target_mb / max(last_size, 0.1)
        rows = max(1_000, int(rows * max(0.4, min(2.8, scale))))
    return first_path, rows, last_size


def _measure(label: str, fn: Callable[[], object]) -> tuple[str, float, float, str]:
    gc.collect()
    before = _rss_mb()
    t0 = time.perf_counter()
    result = fn()
    dt_ms = (time.perf_counter() - t0) * 1000
    after = _rss_mb()
    summary = ""
    if isinstance(result, dict):
        if "showing" in result:
            summary = f"rows={result.get('showing')} cols={result.get('total_cols')} meta={bool(result.get('meta_only'))}"
        elif "products" in result:
            summary = f"products={len(result.get('products') or [])}"
        elif "candidates" in result:
            summary = f"candidates={len(result.get('candidates') or [])}"
        elif "headers" in result:
            summary = f"headers={len(result.get('headers') or [])} rows={len(result.get('rows') or [])}"
        elif "columns" in result:
            summary = f"columns={len(result.get('columns') or [])}"
    return label, dt_ms, after - before, summary


def _print_table(rows: list[tuple[str, float, float, str]]) -> None:
    print("| case | ms | rss_delta_mb | summary |")
    print("| --- | ---: | ---: | --- |")
    for label, ms, rss_delta, summary in rows:
        print(f"| {label} | {ms:.1f} | {rss_delta:.1f} | {summary} |")


def run(size_mb: int, db_root: Path, data_root: Path) -> None:
    product = f"PERF{size_mb}"
    ml_product = f"ML_TABLE_{product}"
    ml_path = db_root / f"{ml_product}.parquet"
    rows = _write_target_size(ml_path, product, size_mb, seed=7300 + size_mb)
    fab_path, fab_rows, fab_size = _write_fab_history(db_root, product, size_mb, seed=9100 + size_mb)
    print(f"\n## {size_mb}MB target")
    print(f"single_file={ml_path.name} size={_size_mb(ml_path):.1f}MB rows={rows:,}")
    print(f"db_product={fab_path.parent.parent.relative_to(db_root)} size={fab_size:.1f}MB rows={fab_rows:,} files=4")

    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "backend"))
    from routers import filebrowser, splittable  # noqa: WPS433

    for cache in (
        getattr(splittable, "_LOT_LOOKUP_CACHE", None),
        getattr(splittable, "_RGLOB_CACHE", None),
        getattr(splittable, "_DB_ROOTS_CACHE", None),
        getattr(splittable, "_FIRST_DATA_FILE_CACHE", None),
    ):
        if hasattr(cache, "clear"):
            cache.clear()

    selected_root = "R00000"
    rows_out = [
        _measure(
            "filebrowser single meta",
            lambda: filebrowser.base_file_view(
                file=ml_path.name,
                sql="",
                rows=200,
                cols=20,
                select_cols="",
                meta_only=True,
                page=0,
                page_size=200,
            ),
        ),
        _measure(
            "filebrowser single page",
            lambda: filebrowser.base_file_view(
                file=ml_path.name,
                sql="",
                rows=200,
                cols=20,
                select_cols="",
                meta_only=False,
                page=0,
                page_size=200,
            ),
        ),
        _measure(
            "filebrowser DB meta",
            lambda: filebrowser.view_product(
                root="1.RAWDATA_DB_FAB",
                product=product,
                sql="",
                rows=200,
                cols=20,
                select_cols="",
                meta_only=True,
                all_partitions=False,
                page=0,
                page_size=200,
            ),
        ),
        _measure(
            "filebrowser DB page",
            lambda: filebrowser.view_product(
                root="1.RAWDATA_DB_FAB",
                product=product,
                sql="",
                rows=200,
                cols=20,
                select_cols="",
                meta_only=False,
                all_partitions=False,
                page=0,
                page_size=200,
            ),
        ),
        _measure("splittable products", lambda: splittable.list_products()),
        _measure(
            "splittable schema",
            lambda: splittable.get_schema(
                product=ml_product,
                root_lot_id="",
                fab_lot_id="",
                wafer_ids="",
            ),
        ),
        _measure(
            "splittable lot candidates",
            lambda: splittable.get_lot_candidates(
                product=ml_product,
                col="root_lot_id",
                prefix="",
                limit=50,
                source="auto",
                root_lot_id="",
            ),
        ),
        _measure(
            "splittable view root",
            lambda: splittable.view_split(
                product=ml_product,
                root_lot_id=selected_root,
                wafer_ids="",
                prefix="KNOB",
                custom_name="",
                view_mode="all",
                history_mode="all",
                fab_lot_id="",
                custom_cols="",
                request=None,
            ),
        ),
    ]
    _print_table(rows_out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes-mb", nargs="+", type=int, default=[20, 50])
    ap.add_argument("--work-dir", default="")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    work_dir = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="flow_perf_"))
    db_root = work_dir / "DB"
    data_root = work_dir / "flow-data"
    os.environ["FLOW_DB_ROOT"] = str(db_root)
    os.environ["FLOW_DATA_ROOT"] = str(data_root)
    os.environ.setdefault("FLOW_SPLITTABLE_FOREGROUND_GLOBAL_FAB_SCAN", "0")
    db_root.mkdir(parents=True, exist_ok=True)
    data_root.mkdir(parents=True, exist_ok=True)

    print(f"work_dir={work_dir}")
    print(f"FLOW_DB_ROOT={db_root}")
    try:
        for size in args.sizes_mb:
            run(size, db_root, data_root)
    finally:
        if args.keep:
            print(f"\nkept {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
