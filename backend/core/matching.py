"""core/matching.py v7.3 — Matching-table runtime joiners.

Three engines, all pure Polars, all cache-aware:

  step_matcher     — FAB/VM raw STEP_ID  →  canonical functional step (+ step_type)
  inline_coord     — INLINE (step_id, item_id, subitem_id)  →  (shot_x, shot_y, canonical_item)
  yld_agg          — YLD chip-level  →  per-shot aggregates + edge flag

All three read CSVs from `data/DB/matching/` by default. If a matching table is
missing, the function returns the DataFrame unchanged (graceful degradation) and
logs a warning. This matches reality: engineers may not have filled the table yet.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional, Set, Tuple
import polars as pl

logger = logging.getLogger("holweb.matching")


# ─────────────────────────────────────────────────────────────────────
# Cache (file mtime invalidates)
# ─────────────────────────────────────────────────────────────────────
_CSV_CACHE: dict = {}


def _read_csv_cached(fp: Path) -> Optional[pl.DataFrame]:
    if not fp.exists():
        return None
    try:
        mt = fp.stat().st_mtime
        cached = _CSV_CACHE.get(str(fp))
        if cached and cached[0] == mt:
            return cached[1]
        df = pl.read_csv(fp, infer_schema_length=500)
        _CSV_CACHE[str(fp)] = (mt, df)
        return df
    except Exception as e:
        logger.warning(f"matching CSV load failed {fp}: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────
# 1. Step matcher — raw STEP_ID → canonical
# ─────────────────────────────────────────────────────────────────────
def apply_step_match(df: pl.DataFrame, product: str, matching_root: Path,
                      step_col: str = "STEP_ID",
                      out_canonical: str = "CANONICAL_STEP",
                      out_step_type: str = "STEP_TYPE") -> pl.DataFrame:
    """Add canonical_step + step_type columns by joining matching_step.csv.

    If the CSV is missing OR has no rows for this product, columns become NULL
    but the DataFrame is returned unchanged otherwise.
    """
    if step_col not in df.columns:
        return df
    fp = matching_root / "matching_step.csv"
    tbl = _read_csv_cached(fp)
    if tbl is None:
        return df.with_columns([
            pl.lit(None, dtype=pl.Utf8).alias(out_canonical),
            pl.lit(None, dtype=pl.Utf8).alias(out_step_type),
        ])
    # Filter to product (if column present)
    if "product" in tbl.columns and product:
        tbl = tbl.filter(pl.col("product") == product)
    # Expected columns: raw_step_id, canonical_step, step_type
    needed = {"raw_step_id", "canonical_step"}
    if not needed.issubset(set(tbl.columns)):
        logger.warning(f"matching_step.csv missing cols: need {needed}")
        return df
    tbl = tbl.select([
        pl.col("raw_step_id").cast(pl.Utf8).alias("_raw"),
        pl.col("canonical_step").cast(pl.Utf8).alias(out_canonical),
        (pl.col("step_type").cast(pl.Utf8) if "step_type" in tbl.columns
         else pl.lit(None, dtype=pl.Utf8)).alias(out_step_type),
    ]).unique(subset=["_raw"])
    return df.join(tbl, left_on=pl.col(step_col).cast(pl.Utf8), right_on="_raw", how="left")


# Alias for INLINE step matching (same CSV shape but different filename)
def apply_inline_step_match(df: pl.DataFrame, product: str, matching_root: Path,
                              step_col: str = "STEP_ID",
                              out_canonical: str = "CANONICAL_STEP") -> pl.DataFrame:
    fp = matching_root / "inline_step_match.csv"
    tbl = _read_csv_cached(fp)
    if tbl is None or step_col not in df.columns:
        return df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(out_canonical))
    if "product" in tbl.columns and product:
        tbl = tbl.filter(pl.col("product") == product)
    if not {"raw_step_id", "canonical_step"}.issubset(set(tbl.columns)):
        return df
    tbl = tbl.select([
        pl.col("raw_step_id").cast(pl.Utf8).alias("_raw"),
        pl.col("canonical_step").cast(pl.Utf8).alias(out_canonical),
    ]).unique(subset=["_raw"])
    return df.join(tbl, left_on=pl.col(step_col).cast(pl.Utf8), right_on="_raw", how="left")


# ─────────────────────────────────────────────────────────────────────
# 2. INLINE coordinate mapper — (step_id, item_id, subitem_id) → (shot_x, shot_y)
# ─────────────────────────────────────────────────────────────────────
def apply_inline_coord(df: pl.DataFrame, product: str, matching_root: Path,
                        step_col: str = "STEP_ID",
                        item_col: str = "ITEM_ID",
                        subitem_col: str = "SUBITEM_ID",
                        out_canonical_item: str = "CANONICAL_ITEM",
                        out_map_id: str = "MAP_ID",
                        out_shot_x: str = "SHOT_X", out_shot_y: str = "SHOT_Y") -> pl.DataFrame:
    """Two-step join:
      1) inline_item_map.csv: (product, step_id, item_id) → canonical_item + map_id
      2) inline_subitem_pos.csv: (map_id, subitem_id) → (shot_x, shot_y)

    After this, INLINE rows have real ET-compatible coordinates and can be joined
    on (LOT_WF, SHOT_X, SHOT_Y). If tables are missing, new columns become NULL
    and existing SHOT_X/SHOT_Y (if any) are preserved.
    """
    out_df = df
    # Step 1: item → canonical + map_id
    im_fp = matching_root / "inline_item_map.csv"
    im_tbl = _read_csv_cached(im_fp)
    if im_tbl is not None and item_col in df.columns:
        if "product" in im_tbl.columns and product:
            im_tbl = im_tbl.filter(pl.col("product") == product)
        need = {"item_id", "canonical_item", "map_id"}
        if need.issubset(set(im_tbl.columns)):
            im_sel = im_tbl.select([
                pl.col("item_id").cast(pl.Utf8).alias("_item"),
                (pl.col("step_id").cast(pl.Utf8) if "step_id" in im_tbl.columns else pl.lit("")).alias("_step"),
                pl.col("canonical_item").cast(pl.Utf8).alias(out_canonical_item),
                pl.col("map_id").cast(pl.Utf8).alias(out_map_id),
            ]).unique(subset=["_item", "_step"])
            if step_col in df.columns and "_step" in im_sel.columns:
                out_df = out_df.join(
                    im_sel,
                    left_on=[pl.col(item_col).cast(pl.Utf8), pl.col(step_col).cast(pl.Utf8)],
                    right_on=["_item", "_step"], how="left",
                )
            else:
                im_sel2 = im_sel.drop("_step").unique(subset=["_item"])
                out_df = out_df.join(im_sel2,
                                     left_on=pl.col(item_col).cast(pl.Utf8),
                                     right_on="_item", how="left")
        else:
            logger.warning(f"inline_item_map missing {need}")
    # Step 2: (map_id, subitem_id) → (shot_x, shot_y)
    sp_fp = matching_root / "inline_subitem_pos.csv"
    sp_tbl = _read_csv_cached(sp_fp)
    if sp_tbl is not None and subitem_col in out_df.columns and out_map_id in out_df.columns:
        need = {"map_id", "subitem_id", "shot_x", "shot_y"}
        if need.issubset(set(sp_tbl.columns)):
            sp_sel = sp_tbl.select([
                pl.col("map_id").cast(pl.Utf8).alias("_map"),
                pl.col("subitem_id").cast(pl.Utf8).alias("_sub"),
                pl.col("shot_x").cast(pl.Int64, strict=False).alias(out_shot_x),
                pl.col("shot_y").cast(pl.Int64, strict=False).alias(out_shot_y),
            ]).unique(subset=["_map", "_sub"])
            out_df = out_df.join(
                sp_sel,
                left_on=[pl.col(out_map_id).cast(pl.Utf8), pl.col(subitem_col).cast(pl.Utf8)],
                right_on=["_map", "_sub"], how="left",
            )
    return out_df


# ─────────────────────────────────────────────────────────────────────
# 3. YLD chip → shot aggregator + edge flag
# ─────────────────────────────────────────────────────────────────────
def aggregate_chip_to_shot(
    df: pl.DataFrame,
    group_by: list = None,
    value_cols: Optional[list] = None,
    agg: str = "mean",
) -> pl.DataFrame:
    """Collapse chip rows to shot rows.

    group_by defaults to ["LOT_WF", "SHOT_X", "SHOT_Y"].
    value_cols defaults to numeric cols + YIELD.
    agg ∈ {mean, median, sum, pass_rate} — pass_rate = sum(YIELD>0) / n.

    Output columns: the group_by keys + one aggregate per value_col +
    `CHIP_N` (count) + `YIELD_PASS_RATE` (if YIELD present).
    """
    grp = group_by or [c for c in ("LOT_WF", "SHOT_X", "SHOT_Y") if c in df.columns]
    if not grp:
        return df
    if value_cols is None:
        value_cols = [c for c, t in df.schema.items()
                       if c not in grp and t in (pl.Float64, pl.Float32, pl.Int64, pl.Int32)]
    aggs = [pl.len().alias("CHIP_N")]
    for c in value_cols:
        expr = pl.col(c).cast(pl.Float64, strict=False)
        if agg == "mean":   aggs.append(expr.mean().alias(f"{c}_MEAN"))
        elif agg == "median": aggs.append(expr.median().alias(f"{c}_MEDIAN"))
        elif agg == "sum":  aggs.append(expr.sum().alias(f"{c}_SUM"))
        elif agg == "pass_rate": aggs.append(((expr > 0).sum() / pl.len()).alias(f"{c}_PASS_RATE"))
    if "YIELD" in df.columns:
        aggs.append(((pl.col("YIELD").cast(pl.Float64, strict=False) > 0).sum() / pl.len())
                    .alias("YIELD_PASS_RATE"))
    return df.group_by(grp).agg(aggs)


def mark_edge_chips(
    df: pl.DataFrame,
    measured_shots: Set[Tuple[int, int]],
    shot_x_col: str = "SHOT_X", shot_y_col: str = "SHOT_Y",
    out_col: str = "IS_EDGE",
) -> pl.DataFrame:
    """Flag chips whose SHOT is outside the ET/INLINE measured shot set.

    Edge chips can't be directly correlated with shot-level metrology; caller
    must decide whether to extrapolate or drop.
    """
    if shot_x_col not in df.columns or shot_y_col not in df.columns:
        return df.with_columns(pl.lit(None, dtype=pl.Boolean).alias(out_col))
    measured_keys = {f"{x}_{y}" for (x, y) in measured_shots}
    key = (pl.col(shot_x_col).cast(pl.Utf8) + "_" + pl.col(shot_y_col).cast(pl.Utf8))
    return df.with_columns((~key.is_in(list(measured_keys))).alias(out_col))


def measured_shots_from(df_et_or_inline: pl.DataFrame,
                          shot_x: str = "SHOT_X", shot_y: str = "SHOT_Y") -> Set[Tuple[int, int]]:
    """Derive the measurable-shot set from an ET or INLINE DataFrame.

    Useful for edge-chip detection when no config-specified coverage exists.
    """
    if shot_x not in df_et_or_inline.columns or shot_y not in df_et_or_inline.columns:
        return set()
    try:
        uniq = df_et_or_inline.select([pl.col(shot_x).cast(pl.Int64), pl.col(shot_y).cast(pl.Int64)]).unique()
        return {(r[shot_x], r[shot_y]) for r in uniq.to_dicts() if r[shot_x] is not None}
    except Exception:
        return set()
