"""routers/ettime.py v6.0.0 — ET Time analysis: bar chart per WF with CAT drill-down
Admin configures which CAT columns to use as groupby keys via settings gear.
v6: Returns wafer-level summary + per-step breakdown for horizontal bar chart.
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import polars as pl
from core.paths import PATHS
from core.utils import (
    _STR, cast_cats, read_source, find_all_sources,
    load_json, save_json, serialize_rows,
)

logger = logging.getLogger("holweb.ettime")
router = APIRouter(prefix="/api/ettime", tags=["ettime"])
DB_BASE = PATHS.db_root
CONFIG_FILE = PATHS.data_root / "ettime_config.json"

DEFAULT_CONFIG = {
    "source_root": "ET",
    "source_product": "PRODUCT_A",
    "source_file": "",
    "source_type": "hive",
    "lot_col": "ROOT_LOT_ID",
    "wafer_col": "WAFER_ID",
    "tkin_col": "TKIN_TIME",
    "tkout_col": "TKOUT_TIME",
    "step_col": "STEP",
    "groupby_cols": [],    # Admin selects which CAT columns to include
    "available_cats": [],  # Auto-detected
}


def _config():
    return load_json(CONFIG_FILE, DEFAULT_CONFIG)


class ETConfig(BaseModel):
    source_root: str = "ET"
    source_product: str = "PRODUCT_A"
    source_file: str = ""
    source_type: str = "hive"
    lot_col: str = "ROOT_LOT_ID"
    wafer_col: str = "WAFER_ID"
    tkin_col: str = "TKIN_TIME"
    tkout_col: str = "TKOUT_TIME"
    step_col: str = "STEP"
    groupby_cols: List[str] = []


@router.get("/config")
def get_config():
    cfg = _config()
    # Auto-detect available columns
    try:
        root = cfg.get("source_root", "ET")
        product = cfg.get("source_product", "PRODUCT_A")
        file = cfg.get("source_file", "")
        df = read_source(cfg.get("source_type", "hive"), root, product, file, max_files=1)
        cols = list(df.columns)
        # Filter to only CAT-like columns (exclude known time/id cols)
        known = {cfg.get("lot_col"), cfg.get("wafer_col"), cfg.get("tkin_col"),
                 cfg.get("tkout_col"), cfg.get("step_col"), "ELAPSED_MIN"}
        cfg["available_cats"] = [c for c in cols if c not in known]
        cfg["all_columns"] = cols
    except Exception:
        cfg["available_cats"] = []
        cfg["all_columns"] = []
    return cfg


@router.post("/config")
def save_config(cfg: ETConfig):
    data = cfg.dict()
    save_json(CONFIG_FILE, data, indent=2)
    return {"ok": True}


@router.get("/sources")
def list_sources():
    """List available ET data sources."""
    return {"products": find_all_sources()}


@router.get("/lot-ids")
def get_lot_ids(root: str = Query(""), product: str = Query(""), limit: int = Query(200)):
    """Get distinct lot IDs for autocomplete."""
    cfg = _config()
    src_root = root or cfg.get("source_root", "ET")
    src_prod = product or cfg.get("source_product", "PRODUCT_A")
    lot_col = cfg.get("lot_col", "ROOT_LOT_ID")
    try:
        df = read_source(cfg.get("source_type", "hive"), src_root, src_prod, "", max_files=5)
        if lot_col not in df.columns:
            return {"lot_ids": []}
        lots = df[lot_col].cast(_STR, strict=False).unique().sort().head(limit).to_list()
        return {"lot_ids": [l for l in lots if l]}
    except Exception:
        return {"lot_ids": []}


@router.get("/search")
def search_lot(lot: str = Query(...), root: str = Query(""), product: str = Query(""),
               max_files: int = Query(20)):
    """Search ET data by ROOT_LOT_ID, return elapsed times per wafer."""
    cfg = _config()
    lot_col = cfg.get("lot_col", "ROOT_LOT_ID")
    wafer_col = cfg.get("wafer_col", "WAFER_ID")
    tkin_col = cfg.get("tkin_col", "TKIN_TIME")
    tkout_col = cfg.get("tkout_col", "TKOUT_TIME")
    step_col = cfg.get("step_col", "STEP")
    groupby = cfg.get("groupby_cols", [])

    src_root = root or cfg.get("source_root", "ET")
    src_prod = product or cfg.get("source_product", "PRODUCT_A")
    try:
        df = read_source(
            cfg.get("source_type", "hive"),
            src_root, src_prod,
            cfg.get("source_file", ""),
            max_files=max_files,
        )
    except Exception as e:
        raise HTTPException(400, f"Data read error: {e}")

    # Filter by lot
    if lot_col not in df.columns:
        raise HTTPException(400, f"Column {lot_col} not found")

    df = df.filter(pl.col(lot_col).cast(_STR, strict=False).str.contains(lot))
    if df.height == 0:
        return {"results": [], "total": 0, "lot_query": lot, "groupby": groupby}

    df = cast_cats(df)

    # Compute elapsed if not present
    if "ELAPSED_MIN" not in df.columns:
        try:
            df = df.with_columns([
                ((pl.col(tkout_col).str.to_datetime() - pl.col(tkin_col).str.to_datetime())
                 .dt.total_minutes()).alias("ELAPSED_MIN")
            ])
        except Exception:
            try:
                df = df.with_columns([
                    ((pl.col(tkout_col).cast(pl.Datetime) - pl.col(tkin_col).cast(pl.Datetime))
                     .dt.total_minutes()).alias("ELAPSED_MIN")
                ])
            except Exception:
                pass

    # Build result columns
    select_cols = [lot_col, wafer_col]
    if step_col and step_col in df.columns:
        select_cols.append(step_col)
    select_cols.extend([c for c in [tkin_col, tkout_col] if c in df.columns])
    if "ELAPSED_MIN" in df.columns:
        select_cols.append("ELAPSED_MIN")
    # Add groupby columns
    for gc in groupby:
        if gc in df.columns and gc not in select_cols:
            select_cols.append(gc)

    df = df.select([c for c in select_cols if c in df.columns])
    # Sort by lot, wafer, tkin
    sort_cols = [c for c in [lot_col, wafer_col, tkin_col] if c in df.columns]
    if sort_cols:
        df = df.sort(sort_cols)

    data = serialize_rows(df.head(2000).to_dicts())

    # v6: Wafer-level summary with per-step breakdown for bar chart
    summary = []
    wafer_bars = []  # For horizontal bar chart
    by_cat = {}      # Breakdown by selected CAT
    if "ELAPSED_MIN" in df.columns:
        try:
            # Per-wafer total
            grp_cols = [lot_col, wafer_col] + [g for g in groupby if g in df.columns]
            agg = df.group_by(grp_cols).agg([
                pl.col("ELAPSED_MIN").sum().alias("TOTAL_MIN"),
                pl.col("ELAPSED_MIN").mean().alias("AVG_MIN"),
                pl.col("ELAPSED_MIN").count().alias("STEP_COUNT"),
            ]).sort(grp_cols)
            summary = serialize_rows(agg.to_dicts())

            # Per-wafer per-step breakdown (for stacked bar)
            if step_col in df.columns:
                wf_step = df.group_by([wafer_col, step_col]).agg(
                    pl.col("ELAPSED_MIN").sum().alias("elapsed"),
                ).sort([wafer_col, step_col])
                # Build wafer_bars: [{wf, total, steps:[{step, elapsed}]}]
                wb_map = {}
                for row in wf_step.to_dicts():
                    wf = str(row[wafer_col])
                    if wf not in wb_map:
                        wb_map[wf] = {"wf": wf, "total": 0, "steps": []}
                    el = round(float(row["elapsed"] or 0), 2)
                    wb_map[wf]["steps"].append({
                        "step": str(row[step_col]),
                        "elapsed": el,
                    })
                    wb_map[wf]["total"] += el
                # Sort wafers naturally
                for wf in wb_map.values():
                    wf["total"] = round(wf["total"], 2)
                wafer_bars = sorted(wb_map.values(), key=lambda w: w["wf"])

                # Per-CAT tool breakdown
                for gc in groupby:
                    if gc in df.columns:
                        cat_agg = df.group_by([wafer_col, gc]).agg(
                            pl.col("ELAPSED_MIN").sum().alias("elapsed"),
                        ).sort([wafer_col, gc])
                        cat_map = {}
                        for row in cat_agg.to_dicts():
                            wf = str(row[wafer_col])
                            if wf not in cat_map:
                                cat_map[wf] = {"wf": wf, "total": 0, "tools": []}
                            el = round(float(row["elapsed"] or 0), 2)
                            cat_map[wf]["tools"].append({
                                "tool": str(row[gc]),
                                "elapsed": el,
                            })
                            cat_map[wf]["total"] += el
                        for wf in cat_map.values():
                            wf["total"] = round(wf["total"], 2)
                        by_cat[gc] = sorted(cat_map.values(), key=lambda w: w["wf"])
        except Exception as e:
            logger.warning(f"ET summary error: {e}")

    # Steps and CAT unique values for frontend
    unique_steps = []
    if step_col in df.columns:
        unique_steps = df[step_col].cast(_STR, strict=False).unique().sort().to_list()

    return {
        "results": data, "total": df.height,
        "lot_query": lot, "groupby": groupby,
        "summary": summary,
        "wafer_bars": wafer_bars,
        "by_cat": by_cat,
        "steps": unique_steps,
        "columns": list(df.columns),
    }
