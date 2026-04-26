#!/usr/bin/env python3
"""v8.4.4 — 현재 파일탐색기 DB 루트의 ML_TABLE_PROD*.parquet 를 재조정.

규칙:
  - 한 root_lot_id 내에서 각 KNOB_* 컬럼 값을 "lot 대표값" 으로 통일
  - 2-3 개 wafer 는 split 으로 다른 값 유지 (plan vs actual diff 시나리오용)
  - null 은 보존 (plan test 의도)

결정론적 (SEED 고정). 재실행 시 동일 결과.
"""
from __future__ import annotations
import random
import sys
from pathlib import Path

import polars as pl

SEED = 20260420
random.seed(SEED)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from core.paths import PATHS  # noqa: E402

BASE = PATHS.db_root

SPLIT_PER_LOT = 3  # lot 당 split 으로 남길 wafer 수

def process_file(fp: Path):
    if not fp.exists(): return
    df = pl.read_parquet(fp)
    knob_cols = [c for c in df.columns if c.startswith("KNOB_")]
    if not knob_cols:
        print(f"  {fp.name}: no KNOB cols, skip"); return

    rnd = random.Random(SEED)
    rows = df.to_dicts()
    # Group by root_lot_id
    by_root: dict = {}
    for i, r in enumerate(rows):
        by_root.setdefault(r["root_lot_id"], []).append(i)

    total_split = 0
    for root, idxs in by_root.items():
        if len(idxs) <= 1: continue
        for knob in knob_cols:
            # 해당 lot 의 값 후보
            vals = [rows[i].get(knob) for i in idxs]
            non_null = [v for v in vals if v is not None and str(v) not in ("None","null","")]
            if not non_null: continue
            # 대표값 = 가장 흔한 non-null 값
            uniq = {}
            for v in non_null: uniq[v] = uniq.get(v, 0) + 1
            dominant = max(uniq.items(), key=lambda kv: kv[1])[0]
            alternatives = [v for v in uniq if v != dominant]
            # split wafer 선정 (결정론적 per-lot)
            split_count = min(SPLIT_PER_LOT, len(idxs) - 1)
            lrnd = random.Random(f"{root}|{knob}|{SEED}")
            split_idxs = set(lrnd.sample(idxs, split_count))
            for i in idxs:
                # 기존 null 은 유지 (plan test)
                if rows[i].get(knob) is None: continue
                if i in split_idxs and alternatives:
                    # split — 다른 값 중 하나로
                    rows[i][knob] = lrnd.choice(alternatives) if alternatives else dominant
                    total_split += 1
                else:
                    rows[i][knob] = dominant

    new_df = pl.DataFrame(rows, schema=df.schema)
    new_df.write_parquet(fp)
    print(f"  {fp.name}: {len(rows)} rows × {len(knob_cols)} KNOB cols — {total_split} splits mixed")

def main():
    for fn in ("ML_TABLE_PRODA.parquet", "ML_TABLE_PRODB.parquet"):
        process_file(BASE / fn)

if __name__ == "__main__":
    main()
