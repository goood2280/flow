"""core/long_pivot.py — datalake scan and wafer-level pivot adapters.

FAB 원천은 wafer 단위 공정 이력이다. 한 row 는 한 wafer 가 어떤 step 에서
어떤 장비/챔버/reticle/ppid 로 진행됐는지를 담는다. ET/INLINE 원천은
shot 단위 numerical 계측 long format 이다.

기존 SplitTable / Dashboard / ML_TABLE 는 wide format 의 컬럼명 규약(`INLINE_CD_GATE_MEAN` 등)
으로 동작하므로, 계측 long format 과 그 사이를 이어주는 pivot 계층이 필요하다.

제공 함수:
  - scan_long_fab(product, db_root)     → polars LazyFrame (FAB process history)
  - normalize_fab_history(lf)           → FAB aliases/legacy names normalized
  - scan_long_inline(product, db_root)  → polars LazyFrame (INLINE shot measurements)
  - scan_long_et(product, db_root)      → polars LazyFrame (ET shot measurements)
  - pivot_fab_wide(lf)                  → legacy FAB item/value only; otherwise history preview
  - pivot_inline_wafer(lf)              → (lot,wafer) 축, `INLINE_{item_id}_MEAN/_STD` 컬럼
  - pivot_et_wafer(lf)                  → (lot,wafer) 축, `ET_{item_id}_MEAN/_STD` 컬럼

Phase 1 (v8.8.29) 에선 기존 wide hive 와 공존. 이 모듈은 신규 long hive 가 존재할 때만
SplitTable 이 추가 옵션으로 호출. Phase 2 에서 primary 로 승격.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional

import polars as pl

logger = logging.getLogger("flow.long_pivot")


# ── 경로 규약 ──────────────────────────────────────────────────
# v8.8.31: LONG 포맷이 표준이 됨. 기존 wide `_FAB`/`_INLINE` 을 **대체**.
#   사내 FAB datalake 실환경과 동일한 이름 + 동일한 스키마.
#   구 wide 폴더는 삭제. ET 는 원래 long 구조라 이름 그대로.
FAB_ROOT    = "1.RAWDATA_DB_FAB"
INLINE_ROOT = "1.RAWDATA_DB_INLINE"
ET_ROOT     = "1.RAWDATA_DB_ET"

FAB_HISTORY_COLUMNS = (
    "root_lot_id",
    "lot_id",
    "wafer_id",
    "line_id",
    "process_id",
    "step_id",
    "tkin_time",
    "tkout_time",
    "eqp_id",
    "chamber_id",
    "reticle_id",
    "ppid",
)

ET_MEASUREMENT_COLUMNS = (
    "root_lot_id",
    "lot_id",
    "wafer_id",
    "process_id",
    "step_id",
    "step_seq",
    "eqp_id",
    "probe_card",
    "tkin_time",
    "tkout_time",
    "flat_zone",
    "item_id",
    "shot_x",
    "shot_y",
    "value",
)

INLINE_MEASUREMENT_COLUMNS = (
    "root_lot_id",
    "lot_id",
    "wafer_id",
    "process_id",
    "tkin_time",
    "tkout_time",
    "eqp_id",
    "subitem_id",
    "item_id",
    "value",
    "speclow",
    "target",
    "spechigh",
)

_NUMERIC_COLUMNS = {"shot_x", "shot_y", "value", "speclow", "target", "spechigh"}


def _scan_hive(db_root: Path, folder: str, product: str) -> Optional[pl.LazyFrame]:
    """Hive-partitioned parquet scan. 제품 폴더가 없으면 None."""
    base = db_root / folder / product
    if not base.exists():
        return None
    pattern = str(base / "date=*" / "*.parquet")
    try:
        return pl.scan_parquet(pattern, hive_partitioning=True)
    except Exception as e:
        logger.warning("scan_hive failed %s: %s", pattern, e)
        return None


def _scan_flat(db_root: Path, folder: str, product: str) -> Optional[pl.LazyFrame]:
    """Flat parquet scan.

    Example:
      1.RAWDATA_DB_ET/PRODA/PRODA_20260424.parquet
    """
    base = db_root / folder / product
    if not base.exists():
        return None
    files = sorted(base.glob("*.parquet"))
    if not files:
        return None
    try:
        return pl.scan_parquet([str(f) for f in files])
    except Exception as e:
        logger.warning("scan_flat failed %s: %s", base, e)
        return None


def normalize_fab_history(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize FAB process-history aliases without changing the source files.

    Canonical FAB columns:
      root_lot_id, lot_id, wafer_id, line_id, process_id, step_id,
      tkin_time, tkout_time, eqp_id, chamber_id, reticle_id, ppid

    Legacy/demo aliases still seen in local data are kept for compatibility:
      eqp -> eqp_id, chamber -> chamber_id, time -> tkout_time/time alias.
    """
    try:
        names = lf.collect_schema().names()
    except Exception as e:
        logger.warning("normalize_fab_history schema failed: %s", e)
        return lf
    available = set(names)
    planned = set(names)
    exprs = []

    def add_alias(src: str, dst: str) -> None:
        if dst not in planned and src in available:
            exprs.append(pl.col(src).alias(dst))
            planned.add(dst)

    add_alias("eqp", "eqp_id")
    add_alias("chamber", "chamber_id")
    add_alias("fab_lot_id", "lot_id")
    add_alias("time", "tkout_time")
    add_alias("time", "tkin_time")
    if "time" not in planned:
        if "tkout_time" in available:
            exprs.append(pl.col("tkout_time").alias("time"))
            planned.add("time")
        elif "tkin_time" in available:
            exprs.append(pl.col("tkin_time").alias("time"))
            planned.add("time")

    for col in FAB_HISTORY_COLUMNS:
        if col not in planned:
            exprs.append(pl.lit(None).cast(pl.Utf8).alias(col))
            planned.add(col)

    return lf.with_columns(exprs) if exprs else lf


def _normalize_measurement_aliases(
    lf: pl.LazyFrame,
    canonical_columns: tuple[str, ...],
    *,
    include_flat_alias: bool = False,
) -> pl.LazyFrame:
    try:
        names = lf.collect_schema().names()
    except Exception as e:
        logger.warning("normalize measurement schema failed: %s", e)
        return lf
    available = set(names)
    planned = set(names)
    exprs = []

    def add_alias(src: str, dst: str) -> None:
        if dst not in planned and src in available:
            exprs.append(pl.col(src).alias(dst))
            planned.add(dst)

    add_alias("fab_lot_id", "lot_id")
    add_alias("eqp", "eqp_id")
    add_alias("time", "tkout_time")
    add_alias("spec_low", "speclow")
    add_alias("spec_high", "spechigh")
    add_alias("usl", "spechigh")
    add_alias("lsl", "speclow")
    if include_flat_alias:
        add_alias("flat", "flat_zone")
        add_alias("flat_zone", "flat")
    if "time" not in planned:
        if "tkout_time" in available:
            exprs.append(pl.col("tkout_time").alias("time"))
            planned.add("time")
        elif "tkin_time" in available:
            exprs.append(pl.col("tkin_time").alias("time"))
            planned.add("time")
    if "eqp" not in planned and "eqp_id" in available:
        exprs.append(pl.col("eqp_id").alias("eqp"))
        planned.add("eqp")

    for col in canonical_columns:
        if col in planned:
            continue
        dtype = pl.Float64 if col in _NUMERIC_COLUMNS else pl.Utf8
        exprs.append(pl.lit(None).cast(dtype).alias(col))
        planned.add(col)

    return lf.with_columns(exprs) if exprs else lf


def normalize_et_measurements(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize ET shot-measurement aliases.

    Canonical ET columns:
      root_lot_id, lot_id, wafer_id, process_id, step_id, step_seq, eqp_id,
      probe_card, tkin_time, tkout_time, flat_zone, item_id, shot_x, shot_y, value
    """
    return _normalize_measurement_aliases(lf, ET_MEASUREMENT_COLUMNS, include_flat_alias=True)


def normalize_inline_measurements(lf: pl.LazyFrame) -> pl.LazyFrame:
    """Normalize INLINE shot-measurement aliases.

    INLINE uses subitem_id as the shot-position key. The actual shot_x/shot_y
    coordinates come from item-specific matching tables.
    """
    return _normalize_measurement_aliases(lf, INLINE_MEASUREMENT_COLUMNS)


def scan_long_fab(product: str, db_root: Path) -> Optional[pl.LazyFrame]:
    lf = _scan_hive(db_root, FAB_ROOT, product)
    return normalize_fab_history(lf) if lf is not None else None


def scan_long_inline(product: str, db_root: Path) -> Optional[pl.LazyFrame]:
    lf = _scan_hive(db_root, INLINE_ROOT, product)
    return normalize_inline_measurements(lf) if lf is not None else None


def scan_long_et(product: str, db_root: Path) -> Optional[pl.LazyFrame]:
    # ET is currently delivered as DB flat, but may later move to hive.
    flat = _scan_flat(db_root, ET_ROOT, product)
    if flat is not None:
        return normalize_et_measurements(flat)
    lf = _scan_hive(db_root, ET_ROOT, product)
    return normalize_et_measurements(lf) if lf is not None else None


# ── Pivot 변환 ─────────────────────────────────────────────────
def pivot_fab_wide(lf: pl.LazyFrame) -> pl.DataFrame:
    """FAB preview/pivot adapter.

    FAB canonical data is process history, not item/value measurement long data.
    If legacy item/value rows are provided, keep the previous wide pivot behavior.
    Otherwise return the normalized process-history columns for preview.
    """
    df = normalize_fab_history(lf).collect()
    if df.is_empty():
        return df
    if not {"item_id", "value"}.issubset(set(df.columns)):
        cols = [c for c in ("product", *FAB_HISTORY_COLUMNS, "time") if c in df.columns]
        return df.select(cols) if cols else df
    if "subitem_id" not in df.columns:
        df = df.with_columns(pl.lit("").alias("subitem_id"))
    # 유니크 키 = (product, lot_id, root_lot_id, wafer_id). 나머지는 pivot 대상.
    # col 이름 합성: step + "_" + item + (subitem 있을 때 "_" + subitem).
    with_col = df.with_columns(
        pl.when(pl.col("subitem_id") == "")
        .then(pl.col("step_id") + "_" + pl.col("item_id"))
        .otherwise(pl.col("step_id") + "_" + pl.col("item_id") + "_" + pl.col("subitem_id"))
        .alias("_col")
    )
    # pivot: (lot,wafer) × _col → value.
    wide = with_col.pivot(
        values="value",
        index=["product", "root_lot_id", "lot_id", "wafer_id"],
        on="_col",
        aggregate_function="last",
    )
    return wide


def pivot_inline_wafer(lf: pl.LazyFrame) -> pl.DataFrame:
    """INLINE long → wafer-level MEAN/STD wide.

    컬럼명 규약:
      - INLINE_{item_id}_MEAN  (shot 평균)
      - INLINE_{item_id}_STD   (shot 표준편차)
    """
    df = lf.collect()
    if df.is_empty():
        return df
    agg = (
        df.group_by(["product", "root_lot_id", "lot_id", "wafer_id", "item_id"])
          .agg([
              pl.col("value").mean().alias("_mean"),
              pl.col("value").std().alias("_std"),
          ])
    )
    # wide MEAN
    wide_mean = agg.pivot(
        values="_mean",
        index=["product", "root_lot_id", "lot_id", "wafer_id"],
        on="item_id",
        aggregate_function="last",
    )
    wide_mean = wide_mean.rename({c: f"INLINE_{c}_MEAN"
                                  for c in wide_mean.columns
                                  if c not in ("product", "root_lot_id", "lot_id", "wafer_id")})
    # wide STD
    wide_std = agg.pivot(
        values="_std",
        index=["product", "root_lot_id", "lot_id", "wafer_id"],
        on="item_id",
        aggregate_function="last",
    )
    wide_std = wide_std.rename({c: f"INLINE_{c}_STD"
                                for c in wide_std.columns
                                if c not in ("product", "root_lot_id", "lot_id", "wafer_id")})
    return wide_mean.join(wide_std, on=["product", "root_lot_id", "lot_id", "wafer_id"], how="left")


def pivot_et_wafer(lf: pl.LazyFrame) -> pl.DataFrame:
    """ET long → wafer-level MEAN/STD wide.

    컬럼명 규약:
      - ET_{item_id}_MEAN
      - ET_{item_id}_STD
    die-level wafer map 은 pivot 하지 않고 원본 lf 를 consumer 가 직접 읽는다 (shot_x/shot_y).
    """
    df = lf.collect()
    if df.is_empty():
        return df
    agg = (
        df.group_by(["product", "root_lot_id", "lot_id", "wafer_id", "item_id"])
          .agg([
              pl.col("value").mean().alias("_mean"),
              pl.col("value").std().alias("_std"),
          ])
    )
    wide_mean = agg.pivot(
        values="_mean",
        index=["product", "root_lot_id", "lot_id", "wafer_id"],
        on="item_id",
        aggregate_function="last",
    )
    wide_mean = wide_mean.rename({c: f"ET_{c}_MEAN"
                                  for c in wide_mean.columns
                                  if c not in ("product", "root_lot_id", "lot_id", "wafer_id")})
    wide_std = agg.pivot(
        values="_std",
        index=["product", "root_lot_id", "lot_id", "wafer_id"],
        on="item_id",
        aggregate_function="last",
    )
    wide_std = wide_std.rename({c: f"ET_{c}_STD"
                                for c in wide_std.columns
                                if c not in ("product", "root_lot_id", "lot_id", "wafer_id")})
    return wide_mean.join(wide_std, on=["product", "root_lot_id", "lot_id", "wafer_id"], how="left")


def list_items(lf: pl.LazyFrame) -> list[str]:
    """long LazyFrame 에서 고유한 item_id 목록을 추출 (Dashboard item 선택 UI 용)."""
    try:
        if "item_id" not in lf.collect_schema().names():
            return []
        rows = lf.select(pl.col("item_id").unique().sort()).collect()
        return [str(v) for v in rows["item_id"].to_list() if v]
    except Exception as e:
        logger.warning("list_items failed: %s", e)
        return []
