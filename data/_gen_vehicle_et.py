#!/usr/bin/env python3
"""VEHICLE_A / VEHICLE_B 샘플 ET raw 데이터 생성 (ET Index 다운로드 시연용).

auto report 의 vehicle_*_reformatter.csv 가 참조하는 raw ITEMID
(ET_VTH_N, ET_VTH_P, ET_IDSAT_N, ET_IDSAT_P, ET_PCHK_CONT, ET_PCHK_LKG) 를
그대로 갖는 long-format ET 데이터를 flow ET DB 스키마로 만든다.

Run:  python data/_gen_vehicle_et.py   (flow/ 루트에서)
Out:  data/Fab/1.RAWDATA_DB_ET/VEHICLE_A/VEHICLE_A_2026-07-17.parquet (+ B)
"""
import random
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl

ROOT = Path(__file__).parent
ET_ROOT = ROOT / "Fab" / "1.RAWDATA_DB_ET"

# ITEMID → (mean, sigma). VTH_N 은 음수 raw 로 두어 ABSOLUTE=True 보정을 시연.
ITEMS = {
    "ET_VTH_N": (-0.50, 0.05),
    "ET_VTH_P": (0.50, 0.05),
    "ET_IDSAT_N": (555.0, 55.0),
    "ET_IDSAT_P": (545.0, 55.0),
    "ET_PCHK_CONT": (50.0, 8.0),
    "ET_PCHK_LKG": (1.0, 0.3),
}
SHOTS = [(x, y) for x in range(-4, 5) for y in range(-4, 5)]
BASE_TS = datetime(2026, 7, 10, 9, 0, 0)


def gen_product(product: str, lot_prefix: str, seed: int) -> Path:
    rng = random.Random(seed)
    rows = {k: [] for k in (
        "root_lot_id", "lot_id", "wafer_id", "step_id", "step_seq", "flat",
        "tkin_time", "tkout_time", "item_id", "shot_x", "shot_y", "value",
        "eqp_id", "chamber_id", "pgm")}
    ts = BASE_TS
    for li in range(3):                      # lot 3개
        root = f"{lot_prefix}{3000 + li}"
        for wf in range(1, 4):               # wafer 3장
            for si, step in enumerate(("EV100010", "EV100020"), start=1):
                ts += timedelta(minutes=17)
                tkin = ts.isoformat(timespec="seconds")
                tkout = (ts + timedelta(minutes=12)).isoformat(timespec="seconds")
                # lot/wafer 별 미세 오프셋 — 값이 전부 같아 보이지 않게.
                lot_off = (li - 1) * 0.01
                wf_off = (wf - 2) * 0.005
                for (sx, sy) in SHOTS:
                    radial = (sx * sx + sy * sy) ** 0.5 / 40.0   # 중심→가장자리 경사
                    for item, (mu, sig) in ITEMS.items():
                        v = rng.gauss(mu, sig)
                        v += (abs(mu) if abs(mu) > 10 else 1.0) * (lot_off + wf_off + radial) * (1 if mu >= 0 else -1)
                        rows["root_lot_id"].append(root)
                        rows["lot_id"].append(f"{root}A.{li + 1}")
                        rows["wafer_id"].append(str(wf))
                        rows["step_id"].append(step)
                        rows["step_seq"].append(f"N0{si}PDASF")
                        rows["flat"].append("0")
                        rows["tkin_time"].append(tkin)
                        rows["tkout_time"].append(tkout)
                        rows["item_id"].append(item)
                        rows["shot_x"].append(str(sx))
                        rows["shot_y"].append(str(sy))
                        rows["value"].append(round(v, 5))
                        rows["eqp_id"].append("ET-11")
                        rows["chamber_id"].append("DC-1")
                        rows["pgm"].append(f"{product}_{step}_PGM")
    df = pl.DataFrame(rows)
    out_dir = ET_ROOT / product
    out_dir.mkdir(parents=True, exist_ok=True)
    fp = out_dir / f"{product}_2026-07-17.parquet"
    df.write_parquet(fp)
    print(f"{product}: {df.height:,} rows -> {fp}")
    return fp


if __name__ == "__main__":
    gen_product("VEHICLE_A", "V", seed=41)
    gen_product("VEHICLE_B", "W", seed=42)
