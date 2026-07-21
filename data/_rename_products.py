#!/usr/bin/env python3
"""One-shot in-place product rename/split for flow dummy data.

Transforms on-disk layout from the legacy 2-product scheme
  {GAA2N_A, GAA2N_B}
into the new 3-product scheme
  {PRODUCT_A0, PRODUCT_A1, PRODUCT_B}

Rules:
  - GAA2N_A's lots (sorted) are split deterministically:
      even indices → PRODUCT_A0,  odd indices → PRODUCT_A1
  - GAA2N_B → PRODUCT_B (straight rename, no split).
  - PPIDs are renamed in lockstep: PP_GAA2N_A_NN → PP_PRODUCT_{A0|A1}_NN
    (the same six NN slots are duplicated into both A0 and A1).
  - Base/ rulebook CSVs: A-rows are duplicated into A0+A1; B-rows are renamed.
  - Parquet feature tables are rewritten via polars.

Idempotent: running twice is a no-op (products are already renamed).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parent  # .../flow/data/
DB = ROOT / "DB"
BASE = ROOT / "Base"

OLD_A = "GAA2N_A"
OLD_B = "GAA2N_B"
NEW_A0 = "PRODUCT_A0"
NEW_A1 = "PRODUCT_A1"
NEW_B  = "PRODUCT_B"
NEW_PRODUCTS = [NEW_A0, NEW_A1, NEW_B]


# ─────────────────────────────────────────────────────────────────────
# Lot → new-product mapping (deterministic split of A's lots)
# ─────────────────────────────────────────────────────────────────────
def build_lot_map() -> dict[str, str]:
    """Return {lot_id: new_product} for every lot in LOTS/part-0.csv.

    Idempotent — if the lots CSV is already migrated (products are already
    PRODUCT_A0/A1/B), the existing lot→product mapping is returned verbatim.
    """
    lots_fp = DB / "LOTS" / "lots" / "part-0.csv"
    lots = pl.read_csv(lots_fp)
    present = set(lots["product"].unique().to_list())
    if present.issubset(set(NEW_PRODUCTS)):
        # Already migrated — return the existing mapping.
        return {r["lot_id"]: r["product"] for r in lots.iter_rows(named=True)}
    mapping: dict[str, str] = {}
    # B lots → PRODUCT_B
    for lot in lots.filter(pl.col("product") == OLD_B)["lot_id"].to_list():
        mapping[lot] = NEW_B
    # A lots (sorted) → even=A0, odd=A1
    a_lots = sorted(lots.filter(pl.col("product") == OLD_A)["lot_id"].to_list())
    for i, lot in enumerate(a_lots):
        mapping[lot] = NEW_A0 if (i % 2 == 0) else NEW_A1
    return mapping


def ppid_rename(old_ppid: str, new_prod: str) -> str:
    """PP_GAA2N_A_03 → PP_PRODUCT_A0_03 etc. B follows the same shape."""
    if old_ppid.startswith("PP_" + OLD_A + "_"):
        suffix = old_ppid.removeprefix("PP_" + OLD_A + "_")
        return f"PP_{new_prod}_{suffix}"
    if old_ppid.startswith("PP_" + OLD_B + "_"):
        suffix = old_ppid.removeprefix("PP_" + OLD_B + "_")
        return f"PP_{new_prod}_{suffix}"
    return old_ppid


# ─────────────────────────────────────────────────────────────────────
# 1. Hive-flat partitions under DB/
# ─────────────────────────────────────────────────────────────────────
def rewrite_hive_partition(table_dir: Path, lot_map: dict[str, str],
                           has_ppid_col: bool):
    """Re-split product=GAA2N_A/*.csv + product=GAA2N_B/*.csv into the new
    3-product partition layout.  Deletes the old product=<OLD> directories
    once the new ones have been written.
    """
    old_a_dir = table_dir / f"product={OLD_A}"
    old_b_dir = table_dir / f"product={OLD_B}"
    if not old_a_dir.exists() and not old_b_dir.exists():
        # Nothing to migrate — assume already done.
        return

    # Concat old partitions, tag with new_product via lot_map
    frames = []
    for pdir, _prod in ((old_a_dir, OLD_A), (old_b_dir, OLD_B)):
        if not pdir.exists():
            continue
        for csv_fp in sorted(pdir.glob("*.csv")):
            frames.append(pl.read_csv(csv_fp))
    if not frames:
        return
    df = pl.concat(frames, how="diagonal_relaxed")

    # Map new_product from lot_id
    lot_map_pl = pl.DataFrame(
        {"lot_id": list(lot_map.keys()),
         "__new_product": list(lot_map.values())}
    )
    df = df.join(lot_map_pl, on="lot_id", how="left")
    # Rewrite ppid column if present
    if has_ppid_col and "ppid" in df.columns:
        df = df.with_columns(
            pl.struct(["ppid", "__new_product"]).map_elements(
                lambda r: ppid_rename(r["ppid"], r["__new_product"]),
                return_dtype=pl.Utf8,
            ).alias("ppid")
        )

    # Write new partitions
    for new_prod in NEW_PRODUCTS:
        sub = df.filter(pl.col("__new_product") == new_prod).drop("__new_product")
        part_dir = table_dir / f"product={new_prod}"
        part_dir.mkdir(parents=True, exist_ok=True)
        sub.write_csv(part_dir / "part-0.csv")

    # Remove old partitions
    for pdir in (old_a_dir, old_b_dir):
        if pdir.exists():
            shutil.rmtree(pdir)


def rewrite_lots(lot_map: dict[str, str]):
    """Rewrite LOTS/lots/part-0.csv: product column + ppid column remapped."""
    fp = DB / "LOTS" / "lots" / "part-0.csv"
    df = pl.read_csv(fp)
    # Idempotency: if already migrated, nothing to do.
    if set(df["product"].unique().to_list()).issubset(set(NEW_PRODUCTS)):
        return
    lot_map_pl = pl.DataFrame(
        {"lot_id": list(lot_map.keys()),
         "__new_product": list(lot_map.values())}
    )
    df = df.join(lot_map_pl, on="lot_id", how="left")
    df = df.with_columns(
        pl.struct(["ppid", "__new_product"]).map_elements(
            lambda r: ppid_rename(r["ppid"], r["__new_product"]),
            return_dtype=pl.Utf8,
        ).alias("ppid")
    )
    df = df.drop("product").rename({"__new_product": "product"})
    # Column order: lot_id, product, ppid, start_ts, end_ts, knob
    df = df.select(["lot_id", "product", "ppid", "start_ts", "end_ts", "knob"])
    df.write_csv(fp)


# ─────────────────────────────────────────────────────────────────────
# 2. Base/ rulebook CSVs — duplicate A → {A0, A1}, rename B → PRODUCT_B
# ─────────────────────────────────────────────────────────────────────
def duplicate_a_rename_b(df: pl.DataFrame,
                         ppid_col: str | None = None) -> pl.DataFrame:
    """Given a frame with a 'product' column:
      - rows where product == OLD_A  → duplicated into two frames, one each
        for PRODUCT_A0 and PRODUCT_A1 (with optional ppid rewrite).
      - rows where product == OLD_B  → product replaced with PRODUCT_B
        (ppid rewrite if ppid_col given).
    """
    a_rows = df.filter(pl.col("product") == OLD_A)
    b_rows = df.filter(pl.col("product") == OLD_B)

    out_frames = []
    for new_prod in (NEW_A0, NEW_A1):
        sub = a_rows.with_columns(pl.lit(new_prod).alias("product"))
        if ppid_col and ppid_col in sub.columns:
            sub = sub.with_columns(
                pl.col(ppid_col).map_elements(
                    lambda v, np=new_prod: ppid_rename(v, np),
                    return_dtype=pl.Utf8,
                ).alias(ppid_col)
            )
        out_frames.append(sub)

    b_sub = b_rows.with_columns(pl.lit(NEW_B).alias("product"))
    if ppid_col and ppid_col in b_sub.columns:
        b_sub = b_sub.with_columns(
            pl.col(ppid_col).map_elements(
                lambda v: ppid_rename(v, NEW_B),
                return_dtype=pl.Utf8,
            ).alias(ppid_col)
        )
    out_frames.append(b_sub)

    return pl.concat(out_frames, how="diagonal_relaxed")


def rewrite_base_csv(name: str, ppid_col: str | None = None):
    fp = BASE / name
    if not fp.exists():
        return
    df = pl.read_csv(fp)
    if "product" not in df.columns:
        return
    # Skip if already migrated
    prods = set(df["product"].unique().to_list())
    if prods.issubset(set(NEW_PRODUCTS)):
        return
    out = duplicate_a_rename_b(df, ppid_col=ppid_col)
    out.write_csv(fp)


# ─────────────────────────────────────────────────────────────────────
# 3. features_et_wafer.parquet, features_inline_agg.parquet
# ─────────────────────────────────────────────────────────────────────
def rewrite_et_parquet(lot_map: dict[str, str]):
    fp = BASE / "features_et_wafer.parquet"
    df = pl.read_parquet(fp)
    # Skip if already migrated
    prods = set(df["product"].unique().to_list())
    if prods.issubset(set(NEW_PRODUCTS)):
        return

    lot_map_pl = pl.DataFrame(
        {"lot_id": list(lot_map.keys()),
         "__new_product": list(lot_map.values())}
    )
    df = df.join(lot_map_pl, on="lot_id", how="left")
    # Prefer new_product from lot_map (covers both A0/A1 split and B rename)
    df = df.with_columns(pl.col("__new_product").alias("product"))
    df = df.drop("__new_product")

    if "ppid" in df.columns:
        df = df.with_columns(
            pl.struct(["ppid", "product"]).map_elements(
                lambda r: ppid_rename(r["ppid"], r["product"]),
                return_dtype=pl.Utf8,
            ).alias("ppid")
        )

    # Recompute product-scoped z-scores since product grouping changed.
    if "Rc_zscore" in df.columns and "Rc" in df.columns:
        df = df.with_columns([
            ((pl.col("Rc") - pl.col("Rc").mean().over("product"))
             / pl.col("Rc").std().over("product")).alias("Rc_zscore"),
            ((pl.col("Ion_n") - pl.col("Ion_n").mean().over("product"))
             / pl.col("Ion_n").std().over("product")).alias("Ion_n_zscore"),
        ])

    df.write_parquet(fp)


def rewrite_inline_parquet(lot_map: dict[str, str]):
    fp = BASE / "features_inline_agg.parquet"
    df = pl.read_parquet(fp)
    prods = set(df["product"].unique().to_list())
    if prods.issubset(set(NEW_PRODUCTS)):
        return
    lot_map_pl = pl.DataFrame(
        {"lot_id": list(lot_map.keys()),
         "__new_product": list(lot_map.values())}
    )
    df = df.join(lot_map_pl, on="lot_id", how="left")
    df = df.with_columns(pl.col("__new_product").alias("product")).drop("__new_product")
    df.write_parquet(fp)


# ─────────────────────────────────────────────────────────────────────
# 4. _uniques.json — rebuild `products` + `ppids` sections
# ─────────────────────────────────────────────────────────────────────
def rewrite_uniques():
    fp = BASE / "_uniques.json"
    with fp.open(encoding="utf-8") as f:
        u = json.load(f)
    # Skip if already migrated
    if set(u.get("products", [])) == set(NEW_PRODUCTS):
        return
    u["products"] = list(NEW_PRODUCTS)
    old_ppids = u.get("ppids", {})
    new_ppids: dict[str, list[str]] = {}
    a_ppids = old_ppids.get(OLD_A, [])
    b_ppids = old_ppids.get(OLD_B, [])
    for new_prod in (NEW_A0, NEW_A1):
        new_ppids[new_prod] = sorted(
            ppid_rename(p, new_prod) for p in a_ppids
        )
    new_ppids[NEW_B] = sorted(ppid_rename(p, NEW_B) for p in b_ppids)
    u["ppids"] = new_ppids
    # mask reticles: A reticles (RAA*) are now shared by A0+A1 — no rename
    # needed; the reticle_id is still unique per (product, photo_step) after
    # the dup. Keep reticles list as-is (sorted already via uniques).
    with fp.open("w", encoding="utf-8") as f:
        json.dump(u, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────────────────────────────
# 5. wafer_maps/*.json
# ─────────────────────────────────────────────────────────────────────
def rewrite_wafer_maps(lot_map: dict[str, str]):
    wm_dir = DB / "wafer_maps"
    if not wm_dir.is_dir():
        return
    for fp in sorted(wm_dir.glob("*.json")):
        with fp.open(encoding="utf-8") as f:
            obj = json.load(f)
        old_prod = obj.get("product", "")
        if old_prod in NEW_PRODUCTS:
            continue  # already migrated
        lot = obj.get("lot_id", "")
        new_prod = lot_map.get(lot)
        if new_prod is None:
            continue
        obj["product"] = new_prod
        with fp.open("w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────
def main():
    print("── Renaming products: 2 → 3 ──")
    lot_map = build_lot_map()
    counts = {p: 0 for p in NEW_PRODUCTS}
    for v in lot_map.values():
        counts[v] += 1
    print("Lot split:", counts)

    print("[1/6] Rewriting Hive-flat partitions")
    # (table_dir, has_ppid_col)
    partitions = [
        (DB / "FAB" / "fab_history",   True),   # ppid col exists
        (DB / "INLINE" / "inline_meas", False),
        (DB / "ET" / "et_wafer",        True),   # ppid col
        (DB / "EDS" / "eds_die",        False),
    ]
    for tdir, has_ppid in partitions:
        print(f"     · {tdir.relative_to(ROOT)}")
        rewrite_hive_partition(tdir, lot_map, has_ppid)

    print("[2/6] Rewriting LOTS/lots/part-0.csv")
    rewrite_lots(lot_map)

    print("[3/6] Rewriting Base/ rulebook CSVs")
    # (filename, ppid_col_name_or_None)
    rulebooks = [
        ("matching_step.csv",       None),
        ("knob_ppid.csv",           "ppid"),
        ("mask.csv",                None),
        ("inline_step_match.csv",   None),
        ("inline_item_map.csv",     None),
        ("yld_shot_agg.csv",        None),
    ]
    for name, ppid_col in rulebooks:
        print(f"     · Base/{name}")
        rewrite_base_csv(name, ppid_col=ppid_col)

    print("[4/6] Rewriting features_et_wafer.parquet")
    rewrite_et_parquet(lot_map)

    print("[5/6] Rewriting features_inline_agg.parquet")
    rewrite_inline_parquet(lot_map)

    print("[6/6] Rewriting _uniques.json")
    rewrite_uniques()

    print("[7/7] Rewriting wafer_maps/*.json")
    rewrite_wafer_maps(lot_map)

    print("── DONE ──")
    # Summary
    u = json.load(open(BASE / "_uniques.json", encoding="utf-8"))
    print("products:", u["products"])
    et = pl.read_parquet(BASE / "features_et_wafer.parquet")
    print("features_et_wafer rows:", et.height,
          "product distribution:", dict(
              et.group_by("product").agg(pl.len().alias("n"))
                .sort("product").iter_rows()
          ))


if __name__ == "__main__":
    main()
