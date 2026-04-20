#!/usr/bin/env python3
"""One-shot restructure of FabCanvas dummy data (v2).

Reads the existing flat CSVs under data/DB/ (legacy layout produced by
_gen_gaa2n.py) and re-emits them as:

  data/DB/<MODULE>/<table>/product=<P>/part-0.csv     # Hive-flat raw
  data/DB/LOTS/lots/part-0.csv                        # not partitioned
  data/DB/wafer_maps/*.json                           # left untouched
  data/Base/<matching CSVs>                           # rulebook-style
  data/Base/dvc_rulebook.csv                          # DVC 방향성 룰
  data/Base/_uniques.json                             # UI catalog
  data/Base/features_et_wafer.parquet                 # ML wide form
  data/Base/features_inline_agg.parquet               # ML wide form

Schema v2 highlights
  inline_meas  : lot_id, root_lot_id, wafer_id, step_id, tkin_time, tkout_time,
                 item_id, subitem_id, value
  et_wafer     : lot_id, root_lot_id, wafer_id, pgm, eqp, chamber,
                 shot_x, shot_y, item_id, value    (long format)
  eds_die      : lot_id, wafer_id, die_x, die_y, bin_id, value    (0/1)
  inline_subitem_pos: item_id, subitem_id, shot_x, shot_y
  inline_item_map   : product, step_id, item_id, canonical_item

ML_TABLE column conventions
  인라인 피처  : {KIND}_{step_num}_{func_name}_{stat}
                 예: THK_1.6M_STI_CMP_THK_MEAS_mean, CD_3.3M_PC_FIN_CD_MEAS_std
  KNOB 컬럼   : {knob_name}_Split  (기존 knob_ prefix 폐기)
                 예: GATE_PROFILE_Split, SD_EPI_BORON_Split

Idempotent: deletes the legacy flat CSVs and the matching/ folder when done.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parent  # .../FabCanvas.ai/data/
DB = ROOT / "DB"
BASE = ROOT / "Base"

BASE.mkdir(exist_ok=True)
for sub in ("FAB/fab_history", "INLINE/inline_meas", "ET/et_wafer",
            "EDS/eds_die", "LOTS/lots"):
    (DB / sub).mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────
# MEAS_STEPS lookup (inline column naming)
# KIND_step_num_func_name → column prefix
# ─────────────────────────────────────────────────────────────────────
MEAS_STEPS_META = [
    # (step_num, func_name, item_id, kind)
    ("1.6M",  "STI_CMP_THK_MEAS",         "THK_STI_OX",    "THK"),
    ("3.0M",  "PC_NS_EPI_THK_MEAS",       "THK_NS_STACK",  "THK"),
    ("3.3M",  "PC_FIN_CD_MEAS",           "CD_FIN",        "CD"),
    ("4.3M",  "PC_DUMMY_GATE_CD_OCD",     "CD_DGATE",      "OCD"),
    ("5.1M",  "SPACER_THK_MEAS",          "THK_SPACER",    "THK"),
    ("5.3M",  "INNER_SPACER_OCD",         "OCD_INSPCR",    "OCD"),
    ("6.2M",  "SD_EPI_OCD",               "OCD_SDEPI",     "OCD"),
    ("7.3M",  "GATE_CHANNEL_RELEASE_OCD", "OCD_NS_REL",    "OCD"),
    ("7.7M",  "HKMG_CMP_THK",             "THK_HKMG",      "THK"),
    ("8.1M",  "CT_CD_OCD",                "CD_CT",         "OCD"),
    ("8.1M2", "CT_OVL_GATE",              "OVL_CT_GATE",   "OVL"),
    ("8.4M",  "CT_RS_MEAS",               "RS_CT",         "RS"),
    ("9.2M",  "M1_CD_OCD",                "CD_M1",         "OCD"),
    ("9.3M",  "M1_THK_MEAS",              "THK_M1",        "THK"),
    ("10.2M", "M2_OVL_M1",                "OVL_M2_M1",     "OVL"),
    ("11.2M", "M3_CD_OCD",                "CD_M3",         "OCD"),
    ("12.1M", "M4_OVL_M3",                "OVL_M4_M3",     "OVL"),
    ("13.1M", "M5_THK_MEAS",              "THK_M5",        "THK"),
]

# item_id → column_prefix:  THK_STI_OX → "THK_1.6M_STI_CMP_THK_MEAS"
ITEM_COL_PREFIX = {
    item_id: f"{kind}_{step_num}_{func_name}"
    for step_num, func_name, item_id, kind in MEAS_STEPS_META
}

DVC_PARAMS = ["Rc", "Rch", "ACint", "AChw", "Vth_n", "Vth_p",
              "Ion_n", "Ion_p", "Ioff_n", "Ioff_p", "lkg"]


# ─────────────────────────────────────────────────────────────────────
# Phase 1: split flat raw CSVs by product → Hive-flat layout
# ─────────────────────────────────────────────────────────────────────
def split_by_product(src: Path, table_dir: Path, drop_product: bool = True) -> dict:
    df = pl.read_csv(src)
    out = {}
    for prod in sorted(df["product"].unique().to_list()):
        sub = df.filter(pl.col("product") == prod)
        if drop_product:
            sub = sub.drop("product")
        part_dir = table_dir / f"product={prod}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / "part-0.csv"
        sub.write_csv(part_path)
        out[prod] = (sub.height, part_path)
    return out


def split_fab_history():
    fh = pl.read_csv(DB / "fab_history.csv")
    lots_prod = pl.read_csv(DB / "lots.csv").select(["lot_id", "product"])
    fh = fh.join(lots_prod, on="lot_id", how="left")
    out = {}
    for prod in sorted(fh["product"].drop_nulls().unique().to_list()):
        sub = fh.filter(pl.col("product") == prod).drop("product")
        part_dir = DB / "FAB" / "fab_history" / f"product={prod}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / "part-0.csv"
        sub.write_csv(part_path)
        out[prod] = (sub.height, part_path)
    return out


def split_inline_meas():
    """inline_meas: lot_id, root_lot_id, wafer_id, step_id, tkin_time, tkout_time,
    item_id, subitem_id, value.  product column 없으므로 lots join 으로 복원."""
    im = pl.read_csv(DB / "inline_meas.csv")
    lots_prod = pl.read_csv(DB / "lots.csv").select(["lot_id", "product"])
    im = im.join(lots_prod, on="lot_id", how="left")
    out = {}
    for prod in sorted(im["product"].drop_nulls().unique().to_list()):
        sub = im.filter(pl.col("product") == prod).drop("product")
        part_dir = DB / "INLINE" / "inline_meas" / f"product={prod}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / "part-0.csv"
        sub.write_csv(part_path)
        out[prod] = (sub.height, part_path)
    return out


def split_et_wafer():
    """et_wafer: long format. lot_id, root_lot_id, wafer_id, pgm, eqp, chamber,
    shot_x, shot_y, item_id, value.  product column 없으므로 lots join."""
    et = pl.read_csv(DB / "et_wafer.csv")
    lots_prod = pl.read_csv(DB / "lots.csv").select(["lot_id", "product"])
    et = et.join(lots_prod, on="lot_id", how="left")
    out = {}
    for prod in sorted(et["product"].drop_nulls().unique().to_list()):
        sub = et.filter(pl.col("product") == prod).drop("product")
        part_dir = DB / "ET" / "et_wafer" / f"product={prod}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / "part-0.csv"
        sub.write_csv(part_path)
        out[prod] = (sub.height, part_path)
    return out


def split_eds_die():
    """eds_die: lot_id, wafer_id, die_x, die_y, bin_id, value (0/1).
    product column 없으므로 lots join."""
    eds = pl.read_csv(DB / "eds_die.csv")
    lots_prod = pl.read_csv(DB / "lots.csv").select(["lot_id", "product"])
    eds = eds.join(lots_prod, on="lot_id", how="left")
    out = {}
    for prod in sorted(eds["product"].drop_nulls().unique().to_list()):
        sub = eds.filter(pl.col("product") == prod).drop("product")
        part_dir = DB / "EDS" / "eds_die" / f"product={prod}"
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / "part-0.csv"
        sub.write_csv(part_path)
        out[prod] = (sub.height, part_path)
    return out


def copy_lots():
    src = DB / "lots.csv"
    dst_dir = DB / "LOTS" / "lots"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "part-0.csv"
    shutil.copyfile(src, dst)
    return dst


# ─────────────────────────────────────────────────────────────────────
# Phase 2: move matching/ → Base/
# ─────────────────────────────────────────────────────────────────────
def move_matching():
    src = DB / "matching"
    moved = []
    for p in sorted(src.glob("*.csv")):
        dst = BASE / p.name
        shutil.copyfile(p, dst)
        moved.append(dst)
    return moved


# ─────────────────────────────────────────────────────────────────────
# Phase 3: dvc_rulebook.csv
# ─────────────────────────────────────────────────────────────────────
DVC_RULEBOOK_ROWS = [
    ("Rc",     "lower_is_better",    "Ω",      "0",     "50",    "25",    "접촉저항. 낮을수록 소자 성능"),
    ("Rch",    "target_centered",    "Ω",      "100",   "300",   "200",   "채널저항. 편차가 중요"),
    ("ACint",  "lower_is_better",    "fF/μm",  "0",     "5",     "2",     "기생 인터커넥트 커패시턴스"),
    ("AChw",   "context_dependent",  "fF",     "-",     "-",     "-",     "핫와이어 AC. 구조/목적에 따라"),
    ("Vth_n",  "target_centered",    "V",      "0.18",  "0.32",  "0.25",  "NFET 문턱전압"),
    ("Vth_p",  "target_centered",    "V",      "-0.32", "-0.18", "-0.25", "PFET 문턱전압"),
    ("Ion_n",  "higher_is_better",   "μA/μm",  "800",   "-",     "1000",  "NFET 온전류"),
    ("Ion_p",  "higher_is_better",   "μA/μm",  "700",   "-",     "900",   "PFET 온전류"),
    ("Ioff_n", "lower_is_better",    "nA/μm",  "0",     "10",    "2",     "NFET 오프전류"),
    ("Ioff_p", "lower_is_better",    "nA/μm",  "0",     "10",    "2",     "PFET 오프전류"),
    ("lkg",    "lower_is_better",    "nA",     "0",     "100",   "20",    "누설전류"),
]


def write_dvc_rulebook():
    df = pl.DataFrame(
        DVC_RULEBOOK_ROWS,
        schema=["param", "direction", "unit", "spec_lo", "spec_hi", "target", "note"],
        orient="row",
    )
    df.write_csv(BASE / "dvc_rulebook.csv")
    return df


# ─────────────────────────────────────────────────────────────────────
# Phase 4: features_et_wafer.parquet
#   ET long format → wafer-level 집계 → wide → knob join → z-score
# ─────────────────────────────────────────────────────────────────────
def build_features_et_wafer():
    et = pl.read_csv(DB / "et_wafer.csv")
    lots = pl.read_csv(DB / "lots.csv").select(["lot_id", "product", "start_ts", "end_ts"])
    knob = pl.read_csv(DB / "matching" / "knob_ppid.csv")  # product, ppid, knob_name, knob_value

    # value 컬럼: scientific notation 문자열 포함 → Float64
    et = et.with_columns(pl.col("value").cast(pl.Float64, strict=False))

    # shot 위치별 집계 → wafer-level mean (per lot_id, wafer_id, pgm, eqp, chamber, item_id)
    et_wafer_agg = (
        et.group_by(["lot_id", "root_lot_id", "wafer_id", "pgm", "eqp", "chamber", "item_id"])
        .agg(pl.col("value").mean().alias("value_mean"))
    )

    # product join (lots 에서)
    et_wafer_agg = et_wafer_agg.join(
        lots.select(["lot_id", "product", "start_ts", "end_ts"]),
        on="lot_id", how="left"
    )

    # long → wide: item_id 컬럼으로 pivot
    meta_cols = ["lot_id", "root_lot_id", "wafer_id", "product", "pgm", "eqp", "chamber",
                 "start_ts", "end_ts"]
    et_wide = et_wafer_agg.pivot(
        values="value_mean",
        index=meta_cols,
        on="item_id"
    )

    # knob → wide per (product, ppid) → rename to {name}_Split
    knob_wide = knob.pivot(values="knob_value", index=["product", "ppid"], on="knob_name")
    knob_rename = {}
    for c in knob_wide.columns:
        if c in ("product", "ppid"):
            continue
        safe = c.replace("/", "_")
        knob_rename[c] = f"{safe}_Split"
    knob_wide = knob_wide.rename(knob_rename)

    # join: et_wide (pgm = ppid)
    et_wide = et_wide.join(
        knob_wide.rename({"ppid": "pgm"}),
        on=["product", "pgm"],
        how="left"
    )

    # z-score per product for key DVC params (Rc, Ion_n)
    et_wide = et_wide.with_columns([
        ((pl.col("Rc") - pl.col("Rc").mean().over("product"))
         / pl.col("Rc").std().over("product")).alias("Rc_zscore"),
        ((pl.col("Ion_n") - pl.col("Ion_n").mean().over("product"))
         / pl.col("Ion_n").std().over("product")).alias("Ion_n_zscore"),
    ])

    # 컬럼 정렬: meta → DVC params → split knobs → derived
    split_cols = sorted(knob_rename.values())
    dvc_present = [c for c in DVC_PARAMS if c in et_wide.columns]
    derived_cols = ["Rc_zscore", "Ion_n_zscore"]
    ordered = meta_cols + dvc_present + split_cols + derived_cols
    ordered = [c for c in ordered if c in et_wide.columns]
    et_wide = et_wide.select(ordered)

    out = BASE / "features_et_wafer.parquet"
    et_wide.write_parquet(out)
    return et_wide, out


# ─────────────────────────────────────────────────────────────────────
# Phase 5: features_inline_agg.parquet
#   inline long format → (lot, wafer, item) 집계 → wide
#   컬럼명: {KIND}_{step_num}_{func_name}_{stat}
# ─────────────────────────────────────────────────────────────────────
def build_features_inline_agg():
    im = pl.read_csv(DB / "inline_meas.csv")
    # Schema: lot_id, root_lot_id, wafer_id, step_id, tkin_time, tkout_time,
    #         item_id, subitem_id, value
    lots_prod = pl.read_csv(DB / "lots.csv").select(["lot_id", "product"])

    # subitem 간 집계 → wafer × step × item_id level mean
    agg = im.group_by(["lot_id", "wafer_id", "item_id"]).agg([
        pl.col("value").mean().alias("mean"),
        pl.col("value").std().alias("std"),
        pl.col("value").quantile(0.10).alias("p10"),
        pl.col("value").quantile(0.90).alias("p90"),
    ])

    # long stat → wide, 컬럼명에 KIND_stepnum_funcname 접두어 붙이기
    pieces = []
    for stat in ("mean", "std", "p10", "p90"):
        w = agg.select(["lot_id", "wafer_id", "item_id", stat]).pivot(
            values=stat, index=["lot_id", "wafer_id"], on="item_id"
        )
        # rename: {item_id} → {KIND_step_func}_{stat}
        rename = {}
        for c in w.columns:
            if c in ("lot_id", "wafer_id"):
                continue
            prefix = ITEM_COL_PREFIX.get(c, c)  # fallback: item_id 그대로
            rename[c] = f"{prefix}_{stat}"
        w = w.rename(rename)
        pieces.append(w)

    wide = pieces[0]
    for w in pieces[1:]:
        wide = wide.join(w, on=["lot_id", "wafer_id"], how="full", coalesce=True)
    wide = wide.join(lots_prod, on="lot_id", how="left")

    feature_cols = sorted(c for c in wide.columns
                          if c not in ("lot_id", "wafer_id", "product"))
    wide = wide.select(["lot_id", "wafer_id", "product"] + feature_cols)

    out = BASE / "features_inline_agg.parquet"
    wide.write_parquet(out)
    return wide, out


# ─────────────────────────────────────────────────────────────────────
# Phase 6: _uniques.json
# ─────────────────────────────────────────────────────────────────────
def build_uniques(et_features: pl.DataFrame, inline_features: pl.DataFrame):
    knob = pl.read_csv(DB / "matching" / "knob_ppid.csv")
    mask = pl.read_csv(DB / "matching" / "mask.csv")
    matching_step = pl.read_csv(DB / "matching" / "matching_step.csv")
    item_map = pl.read_csv(DB / "matching" / "inline_item_map.csv")
    # inline_item_map v2: product, step_id, item_id, canonical_item
    fab_hist = pl.read_csv(DB / "fab_history.csv")

    products = sorted(matching_step["product"].unique().to_list())

    ppids: dict[str, list[str]] = {}
    for p in products:
        ppids[p] = sorted(
            knob.filter(pl.col("product") == p)["ppid"].unique().to_list()
        )

    knobs: dict[str, list[str]] = {}
    for kn in sorted(knob["knob_name"].unique().to_list()):
        knobs[kn] = sorted(
            knob.filter(pl.col("knob_name") == kn)["knob_value"].unique().to_list()
        )

    masks = {
        "reticles":      sorted(mask["reticle_id"].unique().to_list()),
        "photo_steps":   sorted(mask["photo_step"].unique().to_list()),
        "mask_versions": sorted(mask["mask_version"].unique().to_list()),
        "mask_vendors":  sorted(mask["mask_vendor"].unique().to_list()),
    }

    areas = sorted(matching_step["area"].unique().to_list())
    canonical_steps = sorted(matching_step["canonical_step"].unique().to_list())

    # equipments: eqp (not eqp_chamber) from fab_history
    equipments = sorted(fab_hist["eqp_chamber"].unique().to_list())

    # ET eqp/chamber (from et_wafer.csv long format)
    et_raw = pl.read_csv(DB / "et_wafer.csv")
    et_eqps = sorted(et_raw["eqp"].unique().to_list()) if "eqp" in et_raw.columns else []
    et_chambers = sorted(et_raw["chamber"].unique().to_list()) if "chamber" in et_raw.columns else []

    dvc_features = [
        {"name": p, "direction": d, "unit": u,
         "spec_lo": lo, "spec_hi": hi, "target": tgt, "note": note}
        for p, d, u, lo, hi, tgt, note in DVC_RULEBOOK_ROWS
    ]

    # inline_features: ML_TABLE column names (KIND_step_func_stat 형식)
    items = sorted(item_map["canonical_item"].unique().to_list())
    inline_feats = []
    for it in items:
        prefix = ITEM_COL_PREFIX.get(it, it)
        for stat in ("mean", "std", "p10", "p90"):
            inline_feats.append({
                "name": f"{prefix}_{stat}",
                "agg": stat,
                "source": it,
                "col_prefix": prefix,
            })

    # split_knobs: {name}_Split 목록
    split_knob_names = sorted(
        f"{kn.replace('/', '_')}_Split"
        for kn in knob["knob_name"].unique().to_list()
    )

    out = {
        "products": products,
        "ppids": ppids,
        "knobs": knobs,
        "split_knobs": split_knob_names,
        "masks": masks,
        "areas": areas,
        "canonical_steps": canonical_steps,
        "equipments": equipments,
        "et_eqps": et_eqps,
        "et_chambers": et_chambers,
        "dvc_features": dvc_features,
        "inline_features": inline_feats,
    }
    path = BASE / "_uniques.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out, path


# ─────────────────────────────────────────────────────────────────────
# Phase 7: cleanup legacy flat files
# ─────────────────────────────────────────────────────────────────────
def cleanup_legacy():
    for name in ("fab_history.csv", "inline_meas.csv", "et_wafer.csv",
                 "eds_die.csv", "lots.csv"):
        p = DB / name
        if p.exists():
            p.unlink()
    matching_dir = DB / "matching"
    if matching_dir.exists():
        shutil.rmtree(matching_dir)


# ─────────────────────────────────────────────────────────────────────
# READMEs
# ─────────────────────────────────────────────────────────────────────
README_DB = """# data/DB — Hive-flat raw (v2)

Raw fab data, partitioned by `product=<P>` per Hive convention.

```
DB/
├── FAB/fab_history/product={PRODUCT_A0,...}/part-0.csv
├── INLINE/inline_meas/product={PRODUCT_A0,...}/part-0.csv
├── ET/et_wafer/product={PRODUCT_A0,...}/part-0.csv
├── EDS/eds_die/product={PRODUCT_A0,...}/part-0.csv
├── LOTS/lots/part-0.csv
└── wafer_maps/*.json
```

## Schema v2

### FAB/fab_history
`lot_id, wafer_id, raw_step_id, eqp_chamber, in_ts, out_ts, ppid`

### INLINE/inline_meas
`lot_id, root_lot_id, wafer_id, step_id, tkin_time, tkout_time, item_id, subitem_id, value`
- shot 위치는 Base/inline_subitem_pos.csv 에서 (item_id, subitem_id) 로 조회
- tkin_time/tkout_time 으로 ET 데이터와 시간 정렬 가능

### ET/et_wafer  (long/column 형식)
`lot_id, root_lot_id, wafer_id, pgm, eqp, chamber, shot_x, shot_y, item_id, value`
- 5 test sites (ET_SITES) × 11 DVC params = 55 rows/wafer

### EDS/eds_die
`lot_id, wafer_id, die_x, die_y, bin_id, value`
- bin_id: BIN_01=PASS, BIN_02=Vth_fail, BIN_03=Ion_fail, BIN_04=Edge_fail
- value: 1(해당 bin 배정) / 0

Disclaimer: synthetic, academic-reference shaped — NOT actual process data.
"""

README_BASE = """# data/Base — rulebook + ML feature store (v2)

```
Base/
├── matching_step.csv          (product → raw_step_id ↔ canonical_step ↔ area)
├── knob_ppid.csv              (PPID → knob value)
├── mask.csv                   (photo step → reticle / mask)
├── inline_step_match.csv
├── inline_item_map.csv        (product, step_id, item_id, canonical_item)
├── inline_subitem_pos.csv     (item_id, subitem_id, shot_x, shot_y)
├── yld_shot_agg.csv
├── dvc_rulebook.csv
├── _uniques.json
├── features_et_wafer.parquet  (wafer-level ML, wide form)
└── features_inline_agg.parquet
```

## ML_TABLE column conventions

### features_et_wafer
- meta  : lot_id, root_lot_id, wafer_id, product, pgm, eqp, chamber, start_ts, end_ts
- DVC   : Rc, Rch, ACint, AChw, Vth_n, Vth_p, Ion_n, Ion_p, Ioff_n, Ioff_p, lkg
- knob  : {KNOB_NAME}_Split  (예: GATE_PROFILE_Split, SD_EPI_BORON_Split)
- derived: Rc_zscore, Ion_n_zscore

### features_inline_agg
- meta  : lot_id, wafer_id, product
- feats : {KIND}_{step_num}_{func_name}_{stat}
          예: THK_1.6M_STI_CMP_THK_MEAS_mean, CD_3.3M_PC_FIN_CD_MEAS_p90

Disclaimer: synthetic, academic-reference shaped.
"""


def write_readmes():
    (DB / "README_GAA2N.md").write_text(README_DB, encoding="utf-8")
    (BASE / "README_GAA2N.md").write_text(README_BASE, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    print("── Restructuring FabCanvas dummy data (v2) ──")

    print("[1/7] split fab_history by product")
    print("     ", split_fab_history())
    print("[1/7] split inline_meas by product")
    print("     ", split_inline_meas())
    print("[1/7] split et_wafer by product (long format)")
    print("     ", split_et_wafer())
    print("[1/7] split eds_die by product (bin format)")
    print("     ", split_eds_die())
    print("[1/7] copy lots to LOTS/")
    print("     ", copy_lots())

    print("[2/7] move matching/ → Base/")
    print("     ", [str(p.relative_to(ROOT)) for p in move_matching()])

    print("[3/7] dvc_rulebook.csv")
    write_dvc_rulebook()

    print("[4/7] features_et_wafer.parquet")
    et_features, et_path = build_features_et_wafer()
    print("      shape =", et_features.shape, "→", et_path.name)

    print("[5/7] features_inline_agg.parquet")
    in_features, in_path = build_features_inline_agg()
    print("      shape =", in_features.shape, "→", in_path.name)

    print("[6/7] _uniques.json")
    uniques, up = build_uniques(et_features, in_features)
    print("      keys =", list(uniques.keys()))

    print("[7/7] cleanup legacy + READMEs")
    cleanup_legacy()
    write_readmes()

    total = 0
    for p in sorted(ROOT.rglob("*")):
        if p.is_file():
            total += p.stat().st_size
    print(f"── DONE. data/ total size = {total/1024/1024:.2f} MB ──")


if __name__ == "__main__":
    main()
