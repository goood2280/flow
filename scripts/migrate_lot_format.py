#!/usr/bin/env python3
"""v8.4.4 lot ID 포맷 마이그레이션.

Before: root_lot_id = "PRODUCT_A0_LOT000", fab_lot_id = "PRODUCT_A0_FLT000"
After:  root_lot_id = 5-char (e.g., "A0001"), fab_lot_id = "{root}{LETTER}.{N}"
        — 한 root 가 공정 중 여러 fab_lot (예: A0001B.1, A0001C.1) 으로 분기.

대상 파일:
  - data/Base/ML_TABLE_PRODA.parquet
  - data/Base/ML_TABLE_PRODB.parquet
  - data/Base/ET_DVC.csv
  - data/Base/speed.csv
  - data/Base/pred.csv
  - data/DB/FAB/fab_history/product=*/part-*.csv (root_lot_id + fab_lot_id 컬럼 추가)

결정론적 (seed 고정) — 재실행 시 동일 매핑.
"""
from __future__ import annotations
import csv, json, random
from pathlib import Path

import polars as pl

SEED = 20260420
random.seed(SEED)

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "Base"
DB = ROOT / "data" / "DB"

# Step 1 — Build deterministic old→new root lot ID map by reading ML_TABLE
def build_root_map():
    mp = {}
    # Read all unique (product, root_lot_id) across PRODA + PRODB
    for fn in ("ML_TABLE_PRODA.parquet", "ML_TABLE_PRODB.parquet"):
        fp = BASE / fn
        if not fp.exists(): continue
        df = pl.read_parquet(fp).select(["product", "root_lot_id"]).unique()
        for row in df.iter_rows(named=True):
            key = (row["product"], row["root_lot_id"])
            if key in mp: continue
            # 5-char code: first char by product family (A/B), then 4-digit seq
            fam = "A" if row["product"].startswith("PRODUCT_A") else "B"
            idx = sum(1 for k in mp if k[0].startswith(f"PRODUCT_{fam}"))
            mp[key] = f"{fam}{idx+1:04d}"
    return mp

# Step 2 — For each (new_root, wafer_id), deterministic fab_lot_id
FAB_SEGMENTS = ["B", "C", "A"]  # letters in rotation (B first for realism)
def fab_lot_for(new_root: str, wafer_id: int) -> str:
    # 25 wafers → 3 segments of ~8-9 wafers, letters cycle, iteration .1/.2 after 2 rounds
    # Deterministic hash: (wafer_id - 1) // 9 → letter; iteration = 1 (for now, all .1)
    if wafer_id is None: return new_root + "A.1"
    try: wid = int(wafer_id)
    except Exception: wid = hash(wafer_id) % 25 + 1
    seg = (wid - 1) // 9  # 0..2
    letter = FAB_SEGMENTS[seg % len(FAB_SEGMENTS)]
    # Iteration = 1 if wid <= 13, else 2 (simulates merged/re-split ops mid-way)
    itr = 1 if wid <= 17 else 2
    return f"{new_root}{letter}.{itr}"

def migrate_ml_table(fp: Path, root_map: dict):
    if not fp.exists(): return
    df = pl.read_parquet(fp)
    rows = df.to_dicts()
    for r in rows:
        key = (r.get("product"), r.get("root_lot_id"))
        new_root = root_map.get(key)
        if new_root:
            r["root_lot_id"] = new_root
            r["fab_lot_id"] = fab_lot_for(new_root, r.get("wafer_id"))
    new_df = pl.DataFrame(rows, schema=df.schema)
    new_df.write_parquet(fp)
    print(f"  [ML]  {fp.name}: {new_df.height} rows, "
          f"{new_df['root_lot_id'].n_unique()} roots, "
          f"{new_df['fab_lot_id'].n_unique()} fab_lots")

def migrate_csv_with_lots(fp: Path, root_map_flat: dict):
    """CSV files that have root_lot_id column (speed/ET_DVC/pred) — no product col,
    so use flat lookup (old root → new root)."""
    if not fp.exists(): return
    df = pl.read_csv(fp)
    if "root_lot_id" not in df.columns: return
    df = df.with_columns([
        pl.col("root_lot_id").map_elements(
            lambda v: root_map_flat.get(v, v), return_dtype=pl.String
        ).alias("root_lot_id")
    ])
    df.write_csv(fp)
    print(f"  [CSV] {fp.name}: {df.height} rows, "
          f"{df['root_lot_id'].n_unique()} roots")

def main():
    root_map = build_root_map()
    print(f"[1/3] Built root map — {len(root_map)} (product, old_root) pairs")
    root_map_flat = {old: new for (_, old), new in root_map.items()}
    # dump for reference
    (BASE / "_root_map.json").write_text(
        json.dumps({f"{p}|{o}": n for (p, o), n in root_map.items()}, indent=2)
    )

    print("[2/3] Migrating ML_TABLE_PROD*.parquet")
    migrate_ml_table(BASE / "ML_TABLE_PRODA.parquet", root_map)
    migrate_ml_table(BASE / "ML_TABLE_PRODB.parquet", root_map)

    print("[3/3] Migrating Base CSVs")
    for fn in ("ET_DVC.csv", "speed.csv", "pred.csv"):
        migrate_csv_with_lots(BASE / fn, root_map_flat)

    print("\nDone. Root map saved to data/Base/_root_map.json")
    print("Sample:")
    for (prod, old), new in list(root_map.items())[:5]:
        print(f"  {prod} / {old}  →  {new}")


if __name__ == "__main__":
    main()
