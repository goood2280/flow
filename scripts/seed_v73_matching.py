#!/usr/bin/env python3
"""v7.3 seeder: create demo matching_step / inline_step_match / inline_item_map /
inline_subitem_pos / yld_shot_agg CSVs and PRODUCT_A/B YAML configs.

Run: python scripts/seed_v73_matching.py
"""
import csv, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MATCH = ROOT / "data" / "DB" / "matching"
PCDIR = ROOT / "data" / "holweb-data" / "product_config"
MATCH.mkdir(parents=True, exist_ok=True)
PCDIR.mkdir(parents=True, exist_ok=True)


def write_csv(path, rows, headers):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  + {path.name}  ({len(rows)} rows)")


# ──── matching_step.csv ────
# Same functional step has different raw STEP_IDs across products
step_rows = []
functional_steps = [
    ("OX_M1",     "main", 1),  ("PHOTO_M1",  "main", 2),
    ("ETCH_POLY", "main", 3),  ("CLEAN_1",   "main", 4),
    ("DEPO_OX1",  "main", 5),  ("CMP_1",     "main", 6),
    ("IMPLANT_1", "main", 7),  ("ASH_1",     "main", 8),
    ("STRIP_1",   "main", 9),  ("PHOTO_M2",  "main", 10),
    ("ETCH_MET",  "main", 11), ("DEPO_MET",  "main", 12),
    ("MEAS_INLINE_CD",  "meas", 13), ("MEAS_INLINE_OCD", "meas", 14),
    ("MEAS_INLINE_THK", "meas", 15), ("MEAS_DC",         "meas", 16),
]
for prod_idx, prod in enumerate(["PRODUCT_A", "PRODUCT_B"]):
    for canon, ttype, seq in functional_steps:
        # Different raw id per product (simulating silo'd naming)
        raw = f"{canon[:3]}_{prod_idx}{seq:02d}"
        step_rows.append({
            "product": prod, "raw_step_id": raw,
            "canonical_step": canon, "step_type": ttype, "seq": seq,
        })
write_csv(MATCH / "matching_step.csv", step_rows,
          ["product", "raw_step_id", "canonical_step", "step_type", "seq"])

# ──── inline_step_match.csv ────
inline_step_rows = [r for r in step_rows if r["step_type"] == "meas"]
for r in inline_step_rows: r.pop("step_type", None); r.pop("seq", None)
write_csv(MATCH / "inline_step_match.csv", inline_step_rows,
          ["product", "raw_step_id", "canonical_step"])

# ──── inline_item_map.csv ────
item_map_rows = []
inline_items = [
    ("CD_GATE",    "CD",    1), ("CD_ACT",     "CD",    2),
    ("OCD_SLOPE",  "OCD",   1), ("OCD_HEIGHT", "OCD",   2),
    ("THK_OX",     "THK",   1), ("THK_NIT",    "THK",   2),
]
for prod_idx, prod in enumerate(["PRODUCT_A", "PRODUCT_B"]):
    for canon_item, family, map_idx in inline_items:
        raw_item = f"{family}_{prod_idx}{map_idx:02d}"
        item_map_rows.append({
            "product": prod,
            "step_id": f"MEAS_INLINE_{family}",  # canonical step (post-match)
            "item_id": raw_item,
            "canonical_item": canon_item,
            "map_id": f"MAP_{family}_{map_idx}",
        })
write_csv(MATCH / "inline_item_map.csv", item_map_rows,
          ["product", "step_id", "item_id", "canonical_item", "map_id"])

# ──── inline_subitem_pos.csv ────
# Each map has 5 subitem positions → shot coordinates (5×5 wafer grid subset)
subitem_rows = []
map_positions = {
    "MAP_CD_1":   [(-2, 0), (0, -2), (0, 0), (0, 2), (2, 0)],
    "MAP_CD_2":   [(-1, -1), (-1, 1), (0, 0), (1, -1), (1, 1)],
    "MAP_OCD_1":  [(-2, -2), (-2, 2), (0, 0), (2, -2), (2, 2)],
    "MAP_OCD_2":  [(-1, 0), (0, -1), (0, 0), (0, 1), (1, 0)],
    "MAP_THK_1":  [(0, -2), (0, -1), (0, 0), (0, 1), (0, 2)],
    "MAP_THK_2":  [(-2, 0), (-1, 0), (0, 0), (1, 0), (2, 0)],
}
for map_id, positions in map_positions.items():
    for i, (x, y) in enumerate(positions, 1):
        subitem_rows.append({"map_id": map_id, "subitem_id": f"S{i:02d}",
                              "shot_x": x, "shot_y": y})
write_csv(MATCH / "inline_subitem_pos.csv", subitem_rows,
          ["map_id", "subitem_id", "shot_x", "shot_y"])

# ──── yld_shot_agg.csv ────
yld_rows = [
    {"product": "PRODUCT_A", "shot_group_cols": "LOT_WF,SHOT_X,SHOT_Y", "agg_method": "mean"},
    {"product": "PRODUCT_B", "shot_group_cols": "LOT_WF,SHOT_X,SHOT_Y", "agg_method": "mean"},
]
write_csv(MATCH / "yld_shot_agg.csv", yld_rows,
          ["product", "shot_group_cols", "agg_method"])

# ──── knob_ppid.csv (canonicalize existing knob_pppid.csv) ────
knob_rows = []
ppid_knobs = {
    "PPID_100": ("KNOB_RECIPE", "STD_A"),
    "PPID_101": ("KNOB_RECIPE", "OPT_B"),
    "PPID_102": ("KNOB_RECIPE", "OPT_C"),
    "PPID_200": ("KNOB_DOSE",   "LOW"),
    "PPID_201": ("KNOB_DOSE",   "MID"),
    "PPID_202": ("KNOB_DOSE",   "HIGH"),
    "PPID_300": ("KNOB_TEMP",   "T_LOW"),
    "PPID_301": ("KNOB_TEMP",   "T_MID"),
    "PPID_302": ("KNOB_TEMP",   "T_HIGH"),
}
for prod in ("PRODUCT_A", "PRODUCT_B"):
    for ppid, (kn, kv) in ppid_knobs.items():
        knob_rows.append({"product": prod, "ppid": ppid, "knob_name": kn, "knob_value": kv})
write_csv(MATCH / "knob_ppid.csv", knob_rows,
          ["product", "ppid", "knob_name", "knob_value"])

# ──── mask.csv (with product column) ────
mask_rows = []
for prod in ("PRODUCT_A", "PRODUCT_B"):
    for rid in range(1000, 1008):
        mask_rows.append({
            "product": prod, "reticle_id": f"R{rid:04d}",
            "mask_version": f"V{rid % 3 + 1}.0",
            "mask_vendor": ["VENDOR_A", "VENDOR_B", "VENDOR_C"][rid % 3],
            "photo_step": ["PHOTO_M1", "PHOTO_M2"][rid % 2],
        })
write_csv(MATCH / "mask.csv", mask_rows,
          ["product", "reticle_id", "mask_version", "mask_vendor", "photo_step"])

# ──── Product YAML configs ────
import sys
sys.path.insert(0, str(ROOT / "backend"))
from core.product_config import save, TEMPLATE  # noqa

for prod, proc_id, owner in [
    ("PRODUCT_A", "1Z_MAIN", "eng_a"),
    ("PRODUCT_B", "1Y_MAIN", "eng_b"),
]:
    cfg = dict(TEMPLATE)
    cfg.update({
        "product": prod,
        "process_id": proc_id,
        "description": f"{prod} auto-seeded config",
        "owner": owner,
        "canonical_knobs": ["KNOB_RECIPE", "KNOB_DOSE", "KNOB_TEMP",
                             "KNOB_PRESSURE", "KNOB_CHAMBER"],
        "canonical_inline_items": ["CD_GATE", "CD_ACT", "OCD_SLOPE",
                                     "OCD_HEIGHT", "THK_OX", "THK_NIT"],
        "et_key_items": ["VTH", "IDSAT", "LEAKAGE", "BVDSS"],
        "yld_metric": "YIELD",
        "perf_metric": "VTH" if prod == "PRODUCT_A" else "IDSAT",
        "target_spec": {
            "VTH": [0.3, 0.8, 0.55], "IDSAT": [40.0, 80.0, 60.0],
            "LEAKAGE": [0.0, 0.05, 0.02], "BVDSS": [15.0, 25.0, 20.0],
        },
    })
    save(ROOT / "data" / "holweb-data", prod, cfg)
    print(f"  + product_config/{prod}.yaml")

print("\nDone. v7.3 matching tables + product YAMLs seeded.")
