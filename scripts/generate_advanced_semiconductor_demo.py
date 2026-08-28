from __future__ import annotations

import math
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl


APP_ROOT = Path(__file__).resolve().parents[1]
FAB_ROOT = APP_ROOT / "data" / "Fab"
PRODUCT = "ADVDEMO"
BASE_TIME = datetime(2026, 8, 21, 18, 0, 0)


def metric_values(day_index: int, wafer: int, shot_x: int, shot_y: int, condition: str, lot_bias: float = 0.0) -> tuple[float, float, float, float, float]:
    radial = math.sqrt(shot_x * shot_x + shot_y * shot_y)
    wave = math.sin((day_index + 1) * 0.71 + wafer * 0.43 + shot_x * 0.37 - shot_y * 0.29)
    condition_bias = 0.0048 if condition == "B" else -0.0012
    vth = 0.515 + day_index * 0.00075 + (wafer - 2.5) * 0.0007 + radial * 0.00022 + condition_bias + lot_bias + wave * 0.0014
    idsat = 664.0 - (vth - 0.515) * 930.0 + (3.5 if condition == "B" else 0.0) + wave * 2.1
    leakage = 6.8 + radial * 0.31 + (0.95 if condition == "B" else 0.0) + abs(wave) * 0.55 + lot_bias * 90.0
    inline_cd = 45.2 - (vth - 0.515) * 31.0 + wave * 0.11
    film_thk = 102.0 + (0.9 if condition == "B" else -0.4) + radial * 0.08 + wave * 0.35
    return tuple(round(value, 5) for value in (vth, idsat, leakage, inline_cd, film_thk))


def append_rows(rows: list[dict], inline_rows: list[dict], *, root_lot_id: str, timestamp: datetime, day_index: int, wafers: range, shot_range: range, lot_bias: float = 0.0) -> None:
    for wafer in wafers:
        for shot_x in shot_range:
            for shot_y in shot_range:
                condition = "A" if (shot_x + shot_y + wafer) % 2 == 0 else "B"
                chamber = "ETCH-A" if condition == "A" else "ETCH-B"
                vth, idsat, leakage, inline_cd, film_thk = metric_values(day_index, wafer, shot_x, shot_y, condition, lot_bias)
                lot_id = f"{root_lot_id}.{wafer}"
                common = {
                    "root_lot_id": root_lot_id,
                    "lot_id": lot_id,
                    "wafer_id": str(wafer),
                    "tkout_time": timestamp.isoformat(timespec="seconds"),
                    "shot_x": shot_x,
                    "shot_y": shot_y,
                    "condition": condition,
                    "chamber_id": chamber,
                }
                rows.append({
                    **common,
                    "step_id": "ET-VTH-410",
                    "eqp_id": "ET-ADV-01",
                    "VTH": vth,
                    "IDSAT": idsat,
                    "LEAKAGE": leakage,
                    "yield_pct": round(99.4 - leakage * 0.085 + (0.12 if condition == "A" else -0.08), 4),
                })
                inline_rows.append({
                    **common,
                    "process_id": "ADV-28N",
                    "step_id": "INLINE-CD-220",
                    "INLINE_CD": inline_cd,
                    "FILM_THK": film_thk,
                })


def main() -> None:
    et_rows: list[dict] = []
    inline_rows: list[dict] = []

    start = BASE_TIME - timedelta(days=20)
    for day_index in range(21):
        timestamp = start + timedelta(days=day_index)
        append_rows(
            et_rows,
            inline_rows,
            root_lot_id=f"ADV-WIP-{timestamp:%y%m%d}",
            timestamp=timestamp,
            day_index=day_index,
            wafers=range(1, 5),
            shot_range=range(-2, 3),
        )

    append_rows(
        et_rows,
        inline_rows,
        root_lot_id="ADV-AB-001",
        timestamp=BASE_TIME - timedelta(hours=8),
        day_index=20,
        wafers=range(1, 9),
        shot_range=range(-3, 4),
        lot_bias=0.0005,
    )
    append_rows(
        et_rows,
        inline_rows,
        root_lot_id="ADV-AB-002",
        timestamp=BASE_TIME - timedelta(days=1, hours=3),
        day_index=19,
        wafers=range(1, 9),
        shot_range=range(-3, 4),
        lot_bias=-0.0018,
    )

    et_path = FAB_ROOT / "1.RAWDATA_DB_ET" / PRODUCT / f"{PRODUCT}_2026-08-21.parquet"
    inline_path = FAB_ROOT / "1.RAWDATA_DB_Inline" / PRODUCT / "date=20260821" / "part_0.parquet"
    et_path.parent.mkdir(parents=True, exist_ok=True)
    inline_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(et_rows).write_parquet(et_path)
    pl.DataFrame(inline_rows).write_parquet(inline_path)
    print(f"ET rows: {len(et_rows):,} -> {et_path}")
    print(f"INLINE rows: {len(inline_rows):,} -> {inline_path}")


if __name__ == "__main__":
    main()
