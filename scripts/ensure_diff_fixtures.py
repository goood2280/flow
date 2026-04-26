#!/usr/bin/env python3
"""v8.4.4 — diff-mode 시연 용 데이터 픽스처.

파일탐색기에서 보이는 현재 DB 루트의 ML_TABLE 파일을 직접 보정한다.
각 root_lot 에 대해 minimum 3 개 이상의 파라미터가 wafer 간에 **명확하게 다른**
값을 갖도록 ML_TABLE_PROD*.parquet 를 보정. plan vs actual diff 뿐 아니라
wafer-split 시나리오 (같은 root 내에서 KNOB 일부가 2-3 갈래로 분기) 를
재현한다.

규칙:
  - 각 lot 에서 KNOB_GATE_PPID / KNOB_ETCH_PPID / KNOB_CVD_PPID 3 개 컬럼은
    wafer 25개를 3 그룹 (8/8/9) 으로 나눠 서로 다른 값 할당 → diff 모드에서 무조건 남음.
  - KNOB_SPACER_PPID / KNOB_LITHO_PPID / KNOB_ANNEAL_RECIPE / KNOB_SD_EPI_RECIPE
    는 이전 lot-dominant 유지 (대부분 같음 → diff 에서 제외되는 쪽 시연).
"""
from __future__ import annotations
import random
import sys
from pathlib import Path
import polars as pl

SEED = 20260420
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from core.paths import PATHS  # noqa: E402

BASE = PATHS.db_root

SPLIT_COLS_A = ["KNOB_GATE_PPID", "KNOB_ETCH_PPID", "KNOB_CVD_PPID"]
GROUPED_PALETTE = {
    "KNOB_GATE_PPID": ["PPID_GATE_002", "PPID_GATE_005", "PPID_GATE_007"],
    "KNOB_ETCH_PPID": ["PPID_ETCH_003", "PPID_ETCH_004", "PPID_ETCH_006"],
    "KNOB_CVD_PPID":  ["PPID_CVD_003",  "PPID_CVD_001",  "PPID_CVD_005"],
}

def group_for_wafer(wid: int, groups: int = 3) -> int:
    # 25 wafer → 3 그룹 (1-8, 9-17, 18-25)
    if wid <= 8: return 0
    if wid <= 17: return 1
    return 2

def process(fp: Path):
    if not fp.exists(): return
    df = pl.read_parquet(fp)
    rows = df.to_dicts()
    for r in rows:
        wid = r.get("wafer_id")
        if wid is None: continue
        g = group_for_wafer(int(wid))
        for col in SPLIT_COLS_A:
            if col not in r: continue
            # null 유지 (plan test) — 약 20% 비율
            if r[col] is None: continue
            pal = GROUPED_PALETTE.get(col)
            if pal: r[col] = pal[g % len(pal)]
    pl.DataFrame(rows, schema=df.schema).write_parquet(fp)
    print(f"  {fp.name}: {len(rows)} rows, split cols={SPLIT_COLS_A}")

def main():
    for fn in ("ML_TABLE_PRODA.parquet", "ML_TABLE_PRODB.parquet"):
        process(BASE / fn)

if __name__ == "__main__":
    main()
