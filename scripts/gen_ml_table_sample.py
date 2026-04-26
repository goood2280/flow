#!/usr/bin/env python3
"""Generate wafer-level ML_TABLE_<PRODUCT>.parquet samples in the active DB root.

The ML_TABLE layer is a wide derived table, one row per wafer:

  PRODUCT, ROOT_LOT_ID, WAFER_ID,
  KNOB_<function step>, INLINE_<function step>, MASK_<function step>,
  FAB_<function step>, VM_<function step>, QTIME_<function step>

Production data can have thousands of feature columns. This test generator keeps
the shape realistic but bounded: 24 function-step columns per prefix.
Existing ML_TABLE parquet files are copied to data_root/_backups before replace.
"""
from __future__ import annotations

import datetime as dt
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

import polars as pl  # noqa: E402
from core.paths import PATHS  # noqa: E402


PRODUCTS = ("PRODA", "PRODB")
ROOT_LOTS_PER_PRODUCT = 30
WAFERS_PER_LOT = 25
FEATURE_PREFIXES = ("KNOB", "INLINE", "MASK", "FAB", "VM", "QTIME")

FUNCTION_STEPS = (
    "1.0 STI",
    "2.0 WELL",
    "3.0 VTN",
    "4.0 GATE_OX",
    "5.0 PC",
    "6.0 LDD",
    "7.0 SPACER",
    "8.0 SD_EPI",
    "9.0 SILICIDE",
    "10.0 CONTACT",
    "11.0 M0",
    "12.0 VIA0",
    "13.0 M1",
    "14.0 VIA1",
    "15.0 M2",
    "16.0 VIA2",
    "17.0 M3",
    "18.0 VIA3",
    "19.0 M4",
    "20.0 PAD",
    "21.0 PASSIVATION",
    "22.0 ETEST_PREP",
    "23.0 RELIABILITY",
    "24.0 SORT",
)


def _backup_existing(paths: list[Path]) -> Path | None:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PATHS.data_root / "_backups" / "ml_table_samples" / stamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    for src in existing:
        shutil.copy2(src, backup_dir / src.name)
    return backup_dir


def _root_lot(product: str, idx: int) -> str:
    prefix = "A" if product == "PRODA" else "B"
    return f"{prefix}{1000 + idx:04d}"


def _feature_values(product: str, root_idx: int, wafer: int, rng: random.Random) -> dict:
    row: dict[str, object] = {}
    product_offset = 0.4 if product == "PRODB" else 0.0
    lot_band = root_idx % 6
    wafer_band = wafer % 5

    for step_idx, step in enumerate(FUNCTION_STEPS, start=1):
        knob_variant = (root_idx + step_idx) % 4
        mask_variant = (root_idx + step_idx + wafer_band) % 5
        eqp_variant = (step_idx + wafer_band) % 6
        chamber_variant = (root_idx + wafer + step_idx) % 4
        mean = 10.0 + step_idx * 0.35 + product_offset + lot_band * 0.08
        inline_sigma = 0.06 + (step_idx % 5) * 0.01
        vm_sigma = 0.09 + (step_idx % 4) * 0.015
        qtime_base = 2.0 + (step_idx % 7) * 0.7 + lot_band * 0.12

        row[f"KNOB_{step}"] = f"PPID_{step_idx:02d}_{knob_variant}"
        row[f"INLINE_{step}"] = round(rng.gauss(mean, inline_sigma), 5)
        row[f"MASK_{step}"] = f"MASK_V{mask_variant + 1}"
        row[f"FAB_{step}"] = f"EQP{eqp_variant + 1:02d}_CH{chamber_variant + 1}"
        row[f"VM_{step}"] = round(rng.gauss(mean * 1.15 + 0.02 * wafer_band, vm_sigma), 5)
        row[f"QTIME_{step}"] = round(max(0.05, rng.gauss(qtime_base, 0.18)), 4)
    return row


def _rows_for_product(product: str) -> list[dict]:
    rng = random.Random(7300 + sum(ord(c) for c in product))
    rows: list[dict] = []
    for root_idx in range(ROOT_LOTS_PER_PRODUCT):
        root = _root_lot(product, root_idx)
        lot_suffix = ("A", "B", "C")[root_idx % 3]
        lot_id = f"{root}{lot_suffix}.1"
        for wafer in range(1, WAFERS_PER_LOT + 1):
            row = {
                "PRODUCT": product,
                "ROOT_LOT_ID": root,
                "LOT_ID": lot_id,
                "WAFER_ID": wafer,
            }
            row.update(_feature_values(product, root_idx, wafer, rng))
            rows.append(row)
    return rows


def _write_product(product: str) -> Path:
    out = PATHS.db_root / f"ML_TABLE_{product}.parquet"
    rows = _rows_for_product(product)
    df = pl.DataFrame(rows)
    tmp = out.with_suffix(".parquet.tmp")
    df.write_parquet(tmp)
    tmp.replace(out)
    prefix_counts = {
        p: len([c for c in df.columns if c.startswith(f"{p}_")])
        for p in FEATURE_PREFIXES
    }
    print(f"wrote {out} rows={df.height:,} cols={df.width:,} prefix_counts={prefix_counts}")
    return out


def main() -> int:
    PATHS.db_root.mkdir(parents=True, exist_ok=True)
    targets = [PATHS.db_root / f"ML_TABLE_{product}.parquet" for product in PRODUCTS]
    backup_dir = _backup_existing(targets)
    if backup_dir:
        print(f"backed up existing ML_TABLE parquet files to {backup_dir}")
    print(f"db_root = {PATHS.db_root}")
    for product in PRODUCTS:
        _write_product(product)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
