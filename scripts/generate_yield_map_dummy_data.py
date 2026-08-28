"""Generate deterministic BIN/MSR wafer data for the Flow Yield Map demo."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import polars as pl


ROOT = Path(__file__).resolve().parents[1]
DB_ROOT = ROOT / "data" / "Fab"
FLOW_DATA_ROOT = ROOT / "data" / "flow-data"

BIN_PRODUCT = "YMAP_BIN_DEMO"
MSR_PRODUCT = "YMAP_MSR_DEMO"
BIN_ROOT_LOT = "YMAP-BIN-240821"
MSR_ROOT_LOT = "YMAP-MSR-240821"
SHOT_VEHICLE = "VH_SAMPLE"
INLINE_MAP_TABLE = "YMAP_SHOT_DEMO_MAP"


def wafer_coordinates(radius: int = 20):
    for y in range(-radius, radius + 1):
        for x in range(-radius, radius + 1):
            if x * x + y * y <= radius * radius:
                yield x, y


def bin_number(wafer: int, x: int, y: int) -> int:
    radius = math.hypot(x, y)
    value = 1
    if radius > 18.1:
        value = 2
    if wafer == 2 and abs(y - (0.42 * x + 2)) < 1.15 and -15 <= x <= 15:
        value = 3
    if wafer == 3 and (x + 6) ** 2 + (y - 5) ** 2 < 24:
        value = 4
    if wafer == 4 and ((x - 7) ** 2 + (y + 7) ** 2 < 18 or (x + 8) ** 2 + (y - 6) ** 2 < 14):
        value = 5
    if (x * 31 + y * 17 + wafer * 13) % 197 == 0:
        value = 9
    return value


def measurement_bin(wafer: int, x: int, y: int, value: float) -> str:
    radius = math.hypot(x, y)
    if wafer == 3 and (x - 5) ** 2 + (y + 4) ** 2 < 20:
        return "LEAK"
    if radius > 18.1:
        return "EDGE"
    if value < 97.0:
        return "LOW"
    if value > 107.5:
        return "HIGH"
    return "PASS"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def generate_bin_rows() -> list[dict]:
    rows = []
    for wafer in range(1, 5):
        for x, y in wafer_coordinates():
            bin_no = bin_number(wafer, x, y)
            rows.append({
                "product": BIN_PRODUCT,
                "root_lot_id": BIN_ROOT_LOT,
                "wafer_id": f"{wafer:02d}",
                "chip_x_pos": x,
                "chip_y_pos": y,
                "bin_no": bin_no,
                "msr": round(100 + math.sin(x / 3.4) * 2.2 + math.cos(y / 4.1) * 1.6 - (bin_no != 1) * 7.5, 3),
            })
    return rows


def generate_msr_rows() -> list[dict]:
    rows = []
    for wafer in range(1, 5):
        for x, y in wafer_coordinates():
            value = (
                101.8
                + math.sin((x + wafer) / 4.2) * 4.0
                + math.cos((y - wafer) / 5.1) * 3.0
                + (wafer - 2.5) * 0.65
                - math.hypot(x, y) * 0.08
            )
            label = measurement_bin(wafer, x, y, value)
            rows.append({
                "product": MSR_PRODUCT,
                "root_lot_id": MSR_ROOT_LOT,
                "wafer_id": f"{wafer:02d}",
                "chip_x_pos": x,
                "chip_y_pos": y,
                "bin": label,
                "msr": round(value, 3),
            })
    return rows


def full_shot_metrics(bin_rows: list[dict]) -> list[dict]:
    """Aggregate the 5×5 full shots used by the Yield comparison metric."""
    grouped: dict[tuple[str, int, int], list[dict]] = {}
    for row in bin_rows:
        shot_x = math.floor((int(row["chip_x_pos"]) + 20) / 5)
        shot_y = math.floor((int(row["chip_y_pos"]) + 20) / 5)
        grouped.setdefault((str(int(row["wafer_id"])), shot_x, shot_y), []).append(row)
    return [
        {
            "wafer_id": wafer, "shot_x": shot_x, "shot_y": shot_y,
            "yield": 100.0 * sum(str(row["bin_no"]) == "1" for row in rows) / len(rows),
        }
        for (wafer, shot_x, shot_y), rows in sorted(grouped.items())
        if len(rows) == 25
    ]


def generate_shot_comparison_rows(bin_rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    et_rows, inline_rows = [], []
    shots = full_shot_metrics(bin_rows)
    for shot in shots:
        wafer = int(shot["wafer_id"])
        shot_x, shot_y, shot_yield = shot["shot_x"], shot["shot_y"], shot["yield"]
        deterministic_noise = ((shot_x * 17 + shot_y * 11 + wafer * 7) % 9 - 4) * 0.0015
        et_linear = 0.18 + (100.0 - shot_yield) * 0.012 + deterministic_noise
        et_threshold = 0.45 + shot_x * 0.025 + shot_y * 0.008 + (0.55 if shot_x >= 4 else 0.0)
        inline_linear = 8.0 + et_linear * 22.0 + wafer * 0.035
        inline_threshold = 25.0 + et_linear * 7.5 + (4.5 if et_linear >= 0.30 else 0.0) + (0.35 if wafer in {3, 4} else -0.15)
        common_et = {
            "root_lot_id": BIN_ROOT_LOT, "lot_id": f"{BIN_ROOT_LOT}.{wafer}",
            "wafer_id": str(wafer), "step_id": "ET900", "step_seq": "D01",
            "shot_x": shot_x, "shot_y": shot_y,
            "tkout_time": f"2026-08-23T{10 + wafer:02d}:20:00",
        }
        et_rows.extend([
            {**common_et, "item_id": "ET_LINEAR", "value": round(et_linear, 6)},
            {**common_et, "item_id": "ET_THRESHOLD", "value": round(et_threshold, 6)},
        ])
        site = f"SHOT_{shot_x}_{shot_y}"
        common_inline = {
            "root_lot_id": BIN_ROOT_LOT, "lot_id": f"{BIN_ROOT_LOT}.{wafer}",
            "wafer_id": str(wafer), "process_id": "YMAP", "step_id": "IN900",
            "subitem_id": site, "shot_x": shot_x, "shot_y": shot_y,
            "tkout_time": f"2026-08-23T{12 + wafer:02d}:10:00",
        }
        inline_rows.extend([
            {**common_inline, "item_id": "IN_LINEAR", "value": round(inline_linear, 6)},
            {**common_inline, "item_id": "IN_THRESHOLD", "value": round(inline_threshold, 6)},
        ])
    ml_rows = [
        {
            "PRODUCT": BIN_PRODUCT, "ROOT_LOT_ID": BIN_ROOT_LOT, "WAFER_ID": str(wafer),
            "FAB_1.0 STI": "STI_A" if wafer <= 2 else "STI_B",
            "FAB_2.0 WELL": "WELL_LOW" if wafer % 2 else "WELL_HIGH",
            "SPLIT_GROUP": "BASE" if wafer in {1, 3} else "SPLIT_X",
        }
        for wafer in range(1, 5)
    ]
    return et_rows, inline_rows, ml_rows


def update_inline_settings(inline_rows: list[dict]) -> None:
    settings_path = DB_ROOT / "credential" / "inline_map_settings.json"
    settings = {"version": 1, "tables": []}
    if settings_path.exists():
        try:
            loaded = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings = loaded
        except (OSError, json.JSONDecodeError):
            pass
    tables = [row for row in settings.get("tables") or [] if row.get("table_name") != INLINE_MAP_TABLE]
    sites = {
        (int(row["shot_x"]), int(row["shot_y"]), str(row["subitem_id"]))
        for row in inline_rows
    }
    tables.append({
        "table_name": INLINE_MAP_TABLE, "vehicle": SHOT_VEHICLE,
        "shots": [
            {"shot_x": shot_x, "shot_y": shot_y, "name": name}
            for shot_x, shot_y, name in sorted(sites, key=lambda row: (row[1], row[0]))
        ],
        "updated_at": "2026-08-23T00:00:00+00:00", "updated_by": "dummy-generator",
    })
    settings["version"], settings["tables"] = 1, tables
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rule_path = DB_ROOT / "inline_matching.csv"
    existing = []
    if rule_path.exists():
        with rule_path.open("r", encoding="utf-8-sig", newline="") as handle:
            existing = [row for row in csv.DictReader(handle) if str(row.get("product") or "") != BIN_PRODUCT]
    existing.extend([
        {"product": BIN_PRODUCT, "step_id": "IN900", "item_id": item, "matching_table": INLINE_MAP_TABLE}
        for item in ("IN_LINEAR", "IN_THRESHOLD")
    ])
    write_csv(rule_path, ["product", "step_id", "item_id", "matching_table"], existing)


def update_yield_map_config() -> None:
    path = FLOW_DATA_ROOT / "yield_map.json"
    config = {"version": 2, "products": {}}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
        except (OSError, json.JSONDecodeError):
            pass
    products = config.setdefault("products", {})
    products[BIN_PRODUCT] = {
        "source": "1.RAWDATA_DB_BIN",
        "vehicle": SHOT_VEHICLE,
        "fields": {
            "x": "chip_x_pos", "y": "chip_y_pos", "bin": "bin_no", "msr": "msr",
            "lot": "root_lot_id", "wafer": "wafer_id", "product": "product",
        },
        "bin_map": [
            {"bin": "1", "bin_color": "#22C55E"},
            {"bin": "2", "bin_color": "#F59E0B"},
            {"bin": "3", "bin_color": "#EF4444"},
            {"bin": "4", "bin_color": "#8B5CF6"},
            {"bin": "5", "bin_color": "#06B6D4"},
            {"bin": "9", "bin_color": "#334155"},
        ],
        "bin_colors": {
            "1": "#22C55E", "2": "#F59E0B", "3": "#EF4444",
            "4": "#8B5CF6", "5": "#06B6D4", "9": "#334155",
        },
        "shot_layout": {"enabled": True, "cols": 5, "rows": 5, "origin_x": -20, "origin_y": -20, "good_bins": ["1"]},
    }
    products[MSR_PRODUCT] = {
        "source": "1.RAWDATA_DB_MSR",
        "fields": {
            "x": "chip_x_pos", "y": "chip_y_pos", "bin": "bin", "msr": "msr",
            "lot": "root_lot_id", "wafer": "wafer_id", "product": "product",
        },
        "bin_map": [
            {"bin": "PASS", "bin_color": "#22C55E"},
            {"bin": "LOW", "bin_color": "#3B82F6"},
            {"bin": "HIGH", "bin_color": "#EF4444"},
            {"bin": "EDGE", "bin_color": "#F59E0B"},
            {"bin": "LEAK", "bin_color": "#A855F7"},
        ],
        "bin_colors": {
            "PASS": "#22C55E", "LOW": "#3B82F6", "HIGH": "#EF4444",
            "EDGE": "#F59E0B", "LEAK": "#A855F7",
        },
        "shot_layout": {"enabled": True, "cols": 5, "rows": 5, "origin_x": -20, "origin_y": -20, "good_bins": ["PASS"]},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    bin_rows = generate_bin_rows()
    msr_rows = generate_msr_rows()
    write_csv(
        DB_ROOT / "1.RAWDATA_DB_BIN" / f"product={BIN_PRODUCT}" / "yield_map_bin_demo.csv",
        ["product", "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos", "bin_no", "msr"],
        bin_rows,
    )
    write_csv(
        DB_ROOT / "1.RAWDATA_DB_MSR" / f"product={MSR_PRODUCT}" / "yield_map_msr_demo.csv",
        ["product", "root_lot_id", "wafer_id", "chip_x_pos", "chip_y_pos", "bin", "msr"],
        msr_rows,
    )
    update_yield_map_config()
    et_rows, inline_rows, ml_rows = generate_shot_comparison_rows(bin_rows)
    et_path = DB_ROOT / "1.RAWDATA_DB_ET" / BIN_PRODUCT / f"{BIN_PRODUCT}_2026-08-23.parquet"
    inline_path = DB_ROOT / "1.RAWDATA_DB_INLINE" / BIN_PRODUCT / "date=20260823" / "part_0.parquet"
    et_path.parent.mkdir(parents=True, exist_ok=True)
    inline_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(et_rows).write_parquet(et_path)
    pl.DataFrame(inline_rows).write_parquet(inline_path)
    pl.DataFrame(ml_rows).write_parquet(DB_ROOT / f"ML_TABLE_{BIN_PRODUCT}.parquet")
    update_inline_settings(inline_rows)
    print(f"BIN: {len(bin_rows):,} rows | product={BIN_PRODUCT} | root_lot_id={BIN_ROOT_LOT}")
    print(f"MSR: {len(msr_rows):,} rows | product={MSR_PRODUCT} | root_lot_id={MSR_ROOT_LOT}")
    print(f"SHOT: {len(et_rows):,} ET + {len(inline_rows):,} Inline rows | {len(ml_rows)} FAB rows")


if __name__ == "__main__":
    main()
