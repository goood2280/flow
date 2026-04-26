"""gen_long_sample.py — canonical FAB/INLINE/ET sample data generator.

FAB 는 wafer 단위 공정이력이고, INLINE/ET 는 shot 단위 numerical 계측 long format 이다.
별도 side root 를 만들지 않고 파일탐색기에서 보이는 현재 DB 루트의 canonical 원천 폴더에 쓴다.

입력: 파일탐색기에서 보이는 현재 DB 루트 (PATHS.db_root).
출력:
  <db_root>/1.RAWDATA_DB_FAB/<PROD>/date=YYYYMMDD/part_0.parquet
  <db_root>/1.RAWDATA_DB_INLINE/<PROD>/date=YYYYMMDD/part_0.parquet
  <db_root>/1.RAWDATA_DB_ET/<PROD>/date=YYYYMMDD/part_0.parquet

실행:
  cd flow && python3 scripts/gen_long_sample.py
"""
import sys, os
from pathlib import Path
# backend 경로 추가 (core.paths 사용).
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE / "backend"))
sys.path.insert(0, str(_HERE))

from core.paths import PATHS

DB_ROOT = PATHS.db_root

import polars as pl
import random
import datetime

random.seed(42)

PRODUCTS = ["PRODA", "PRODB"]
ROOT_LOTS = [f"A{str(1000 + i).zfill(4)}" for i in range(20)]  # A1000~A1019
WAFERS_PER_LOT = 25  # 1..25
DATE_PARTS = ["20260101", "20260301"]
INLINE_ITEMS = [
    ("CD_GATE", 19.5, 20.0, 20.5, "METRO_CD"),
    ("CD_SPACE", 17.6, 18.0, 18.4, "METRO_CD"),
    ("THK_OX", 97.0, 100.0, 103.0, "METRO_THK"),
    ("OVL_X", -3.0, 0.0, 3.0, "METRO_OVL"),
]
INLINE_SUBITEMS = {
    "CD_GATE": ["S01", "S02", "S03", "S04", "S05"],
    "CD_SPACE": ["S01", "S02", "S03", "S04", "S05", "S06", "S07"],
    "THK_OX": ["C", "N", "E", "S", "W"],
    "OVL_X": ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09"],
}


def fab_rows(product: str):
    """FAB process history: one row per wafer and process step."""
    steps = [
        ("ST010", "PHOTO"),
        ("ST020", "ETCH"),
        ("ST030", "CVD"),
        ("ST040", "CMP"),
        ("ST050", "IMPLANT"),
        ("ST060", "ANNEAL"),
        ("ST070", "METAL"),
    ]
    rows = []
    line_id = "LINE_A" if product == "PRODA" else "LINE_B"
    base_dt = datetime.datetime(2026, 1, 1, 8, 0, 0)
    for root_idx, root in enumerate(ROOT_LOTS):
        for branch_idx, branch in enumerate(("A", "B", "C")):
            lot_id = f"{root}{branch}.1_V1"
            for w in range(1, WAFERS_PER_LOT + 1):
                wafer_offset = datetime.timedelta(minutes=(w - 1) * 6)
                lot_offset = datetime.timedelta(hours=(root_idx * 10) + (branch_idx * 3))
                for seq, (step_id, process_id) in enumerate(steps):
                    tkin = base_dt + lot_offset + wafer_offset + datetime.timedelta(hours=seq * 7)
                    tkout = tkin + datetime.timedelta(hours=3 + (seq % 3))
                    rows.append({
                        "product":     product,
                        "root_lot_id": root,
                        "lot_id":      lot_id,
                        "wafer_id":    w,
                        "line_id":     line_id,
                        "process_id":  process_id,
                        "step_id":     step_id,
                        "tkin_time":   tkin.isoformat(),
                        "tkout_time":  tkout.isoformat(),
                        "eqp_id":      f"{line_id[-1]}-EQP-{(seq % 4) + 1:02d}",
                        "chamber_id":  f"CH-{(w + seq) % 4 + 1}",
                        "reticle_id":  f"RET-{product[-1]}-{seq + 1:03d}" if process_id == "PHOTO" else "",
                        "ppid":        f"{product}_{process_id}_PPID_{(seq % 5) + 1:02d}",
                    })
    return rows


def inline_rows(product: str):
    """INLINE shot measurements. subitem_id is the item-specific shot-position key."""
    rows = []
    process_id = "INLINE_METRO"
    base_dt = datetime.datetime(2026, 1, 1, 11, 0, 0)
    for root_idx, root in enumerate(ROOT_LOTS):
        for branch_idx, branch in enumerate(("A", "B", "C")):
            lot_id = f"{root}{branch}.1_V1"
            for w in range(1, WAFERS_PER_LOT + 1):
                lot_offset = datetime.timedelta(hours=(root_idx * 10) + branch_idx)
                wafer_offset = datetime.timedelta(minutes=(w - 1) * 5)
                for item_idx, (item_id, lo, tgt, hi, eqp_id) in enumerate(INLINE_ITEMS):
                    for subitem_id in INLINE_SUBITEMS[item_id]:
                        tkin = base_dt + lot_offset + wafer_offset + datetime.timedelta(minutes=item_idx * 20)
                        tkout = tkin + datetime.timedelta(minutes=8)
                        rows.append({
                            "product":     product,
                            "root_lot_id": root,
                            "lot_id":      lot_id,
                            "wafer_id":    w,
                            "process_id":  process_id,
                            "tkin_time":   tkin.isoformat(),
                            "tkout_time":  tkout.isoformat(),
                            "eqp_id":      eqp_id,
                            "subitem_id":  subitem_id,
                            "item_id":     item_id,
                            "value":       round(random.gauss(tgt, max((hi - lo) / 6.0, 0.01)), 4),
                            "speclow":     lo,
                            "target":      tgt,
                            "spechigh":    hi,
                        })
    return rows


def et_rows(product: str):
    """ET shot measurements with numerical value per item and shot coordinate."""
    items = [("VT", 0.55, 0.035), ("IDSAT", 60.0, 3.0), ("IOFF", 0.02, 0.004), ("ROFF", 120.0, 8.0)]
    rows = []
    process_id = "ET_PARAM"
    base_dt = datetime.datetime(2026, 1, 1, 13, 0, 0)
    for root_idx, root in enumerate(ROOT_LOTS):
        for branch_idx, branch in enumerate(("A", "B", "C")):
            lot_id = f"{root}{branch}.1_V1"
            for w in range(1, WAFERS_PER_LOT + 1):
                lot_offset = datetime.timedelta(hours=(root_idx * 10) + branch_idx)
                wafer_offset = datetime.timedelta(minutes=(w - 1) * 6)
                for seq, step_id in enumerate(("ET01", "ET02")):
                    tkin = base_dt + lot_offset + wafer_offset + datetime.timedelta(hours=seq * 5)
                    tkout = tkin + datetime.timedelta(minutes=35)
                    flat_zone = ["CENTER", "MID", "EDGE"][seq % 3]
                    probe_card = f"PC-{product[-1]}-{seq + 1:02d}"
                    eqp_id = f"ET-EQP-{seq + 1:02d}"
                    step_seq = str(seq + 1)
                    for item_id, mean, sigma in items:
                        item_offset = (0.01 if item_id == "VT" else 1.0) * seq
                        center = mean + item_offset
                        for x in range(-2, 3):
                            for y in range(-2, 3):
                                rows.append({
                                    "product":     product,
                                    "root_lot_id": root,
                                    "lot_id":      lot_id,
                                    "wafer_id":    w,
                                    "process_id":  process_id,
                                    "step_id":     step_id,
                                    "step_seq":    step_seq,
                                    "eqp_id":      eqp_id,
                                    "probe_card":  probe_card,
                                    "tkin_time":   tkin.isoformat(),
                                    "tkout_time":  tkout.isoformat(),
                                    "flat_zone":   flat_zone,
                                    "item_id":     item_id,
                                    "shot_x":      x,
                                    "shot_y":      y,
                                    "value":       round(random.gauss(center, sigma), 5),
                                })
    return rows


def write(rows: list, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(rows)
    df.write_parquet(str(path))
    print(f"  wrote {path.relative_to(DB_ROOT.parent)} ({len(rows):,} rows, {path.stat().st_size:,} bytes)")


def main():
    DB_ROOT.mkdir(parents=True, exist_ok=True)
    print(f"[gen_long_sample] db_root = {DB_ROOT}")
    print(f"[gen_long_sample] products = {PRODUCTS}, root_lots = {len(ROOT_LOTS)}, wafers/lot = {WAFERS_PER_LOT}")
    for prod in PRODUCTS:
        print(f"\n=== {prod} ===")
        # FAB process history
        for date_s in DATE_PARTS:
            rows = fab_rows(prod)
            # split across dates by half
            half = len(rows) // 2
            sub = rows[:half] if date_s == DATE_PARTS[0] else rows[half:]
            path = DB_ROOT / "1.RAWDATA_DB_FAB" / prod / f"date={date_s}" / "part_0.parquet"
            write(sub, path)
        # INLINE long
        for date_s in DATE_PARTS:
            rows = inline_rows(prod)
            half = len(rows) // 2
            sub = rows[:half] if date_s == DATE_PARTS[0] else rows[half:]
            path = DB_ROOT / "1.RAWDATA_DB_INLINE" / prod / f"date={date_s}" / "part_0.parquet"
            write(sub, path)
        # ET long
        for date_s in DATE_PARTS:
            rows = et_rows(prod)
            half = len(rows) // 2
            sub = rows[:half] if date_s == DATE_PARTS[0] else rows[half:]
            path = DB_ROOT / "1.RAWDATA_DB_ET" / prod / f"date={date_s}" / "part_0.parquet"
            write(sub, path)
    print("\n[gen_long_sample] done.")


if __name__ == "__main__":
    main()
