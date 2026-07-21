"""Allowlisted DB-derived cache builders for FileBrowser.

These caches are read-only projections from ``PATHS.db_root``.  They are meant
to be generated explicitly from FileBrowser/Flow-i cache actions, not by
inventing arbitrary SQL or paths from an LLM response.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import polars as pl

from core.paths import PATHS

DB_CACHE_VERSION = 1

ET_LOT_STEP_SEQ = "et_lot_step_seq"
INLINE_LOT_ITEM = "inline_lot_item"
VM_LOT_MODEL = "vm_lot_model"

DB_CACHE_TARGETS = {
    ET_LOT_STEP_SEQ: {
        "label": "ET lot step_seq summary",
        "source_root": "1.RAWDATA_DB_ET",
        "filename": "et_lot_step_seq_summary.parquet",
    },
    INLINE_LOT_ITEM: {
        "label": "INLINE lot item summary",
        "source_root": "1.RAWDATA_DB_INLINE",
        "filename": "inline_lot_item_summary.parquet",
    },
    VM_LOT_MODEL: {
        "label": "VM lot model summary",
        "source_root": "1.RAWDATA_DB_VM",
        "filename": "vm_lot_model_summary.parquet",
    },
}

_SORT_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _safe_text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "nat", "none", "null"}:
        return ""
    return text


def _safe_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    return text.strip("._") or "cache"


def _cache_product_name(product: str) -> str:
    raw = str(product or "").strip()
    if raw.upper().startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
    return raw


def normalize_target(raw: str) -> str:
    target = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "et": ET_LOT_STEP_SEQ,
        "analysis": ET_LOT_STEP_SEQ,
        "tracker_et": ET_LOT_STEP_SEQ,
        "et_lot": ET_LOT_STEP_SEQ,
        "et_step_seq": ET_LOT_STEP_SEQ,
        "et_lot_step_seq_summary": ET_LOT_STEP_SEQ,
        "inline": INLINE_LOT_ITEM,
        "inline_item": INLINE_LOT_ITEM,
        "inline_lot_item_summary": INLINE_LOT_ITEM,
        "vm": VM_LOT_MODEL,
        "vm_model": VM_LOT_MODEL,
        "vm_lot_model_summary": VM_LOT_MODEL,
    }
    target = aliases.get(target, target)
    if target not in DB_CACHE_TARGETS:
        raise ValueError(
            "target must be one of: " + ", ".join(sorted(DB_CACHE_TARGETS))
        )
    return target


def cache_file(target: str) -> Path:
    target = normalize_target(target)
    return PATHS.db_cache_dir / DB_CACHE_TARGETS[target]["filename"]


def meta_file(target: str) -> Path:
    return cache_file(target).with_suffix(".json")


def _natural_key(value: str) -> tuple:
    parts = re.split(r"(\d+)", str(value or ""))
    out = []
    for part in parts:
        if not part:
            continue
        out.append(int(part) if part.isdigit() else part.upper())
    return tuple(out)


def _ci_col(names: Iterable[str], *candidates: str) -> str:
    by_lower = {str(n).lower(): str(n) for n in names or []}
    for cand in candidates:
        hit = by_lower.get(str(cand).lower())
        if hit:
            return hit
    return ""


def _find_product_dir(root: Path, product: str) -> Path | None:
    if not product or not root.is_dir():
        return None
    exact = root / product
    if exact.is_dir():
        return exact
    target = product.casefold()
    try:
        for child in root.iterdir():
            if child.is_dir() and child.name.casefold() == target:
                return child
    except Exception:
        return None
    return None


def _source_frames(source_root: str, product: str = ""):
    root = PATHS.db_root / source_root
    if not root.is_dir():
        return []
    prod = _cache_product_name(product)
    product_dirs: list[Path] = []
    if prod:
        found = _find_product_dir(root, prod)
        if found is not None:
            product_dirs = [found]
    else:
        try:
            product_dirs = [p for p in sorted(root.iterdir()) if p.is_dir()]
        except Exception:
            product_dirs = []
    frames = []
    for product_dir in product_dirs:
        files = sorted(product_dir.rglob("*.parquet"))
        if not files:
            continue
        frames.append(
            pl.scan_parquet([str(fp) for fp in files])
            .with_columns(pl.lit(product_dir.name).alias("__cache_product"))
        )
    if not frames and not prod:
        files = sorted(root.glob("*.parquet"))
        for fp in files:
            product_name = fp.stem.split("_", 1)[0] or "UNKNOWN"
            frames.append(
                pl.scan_parquet(str(fp)).with_columns(
                    pl.lit(product_name).alias("__cache_product")
                )
            )
    return frames


def _scan_source(source_root: str, product: str = ""):
    frames = _source_frames(source_root, product)
    if not frames:
        return None
    return pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]


def _write_parquet(target: Path, rows: list[dict], columns: list[str]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        df = pl.DataFrame(rows).select(columns)
    else:
        df = pl.DataFrame({col: [] for col in columns})
    tmp = target.with_suffix(target.suffix + ".tmp")
    df.write_parquet(tmp)
    tmp.replace(target)


def _write_meta(target: str, result: dict, columns: dict) -> None:
    meta = {
        "version": DB_CACHE_VERSION,
        "target": target,
        "label": DB_CACHE_TARGETS[target]["label"],
        "source_root": DB_CACHE_TARGETS[target]["source_root"],
        "built_at": result.get("updated_at") or _now_iso(),
        "built_epoch": time.time(),
        "row_count": int(result.get("row_count") or 0),
        "columns": columns,
    }
    fp = meta_file(target)
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(fp)


def _error_result(target: str, message: str, *, columns: list[str] | None = None) -> dict:
    return {
        "ok": False,
        "target": target,
        "label": DB_CACHE_TARGETS[target]["label"],
        "source_root": DB_CACHE_TARGETS[target]["source_root"],
        "cache_path": str(cache_file(target)),
        "row_count": 0,
        "products": [],
        "error": message,
        **({"columns": columns} if columns else {}),
    }


ET_COLUMNS = [
    "product", "root_lot_id", "lot_id", "wafer_count", "wafer_ids",
    "step_seq_list", "step_seq_pt_list", "step_id_list", "point_count",
    "tkout_time", "update_time", "source_root",
]
INLINE_COLUMNS = [
    "product", "root_lot_id", "lot_id", "wafer_count", "wafer_ids",
    "item_id_list", "item_pt_list", "subitem_count", "point_count",
    "tkout_time", "update_time", "source_root",
]
VM_COLUMNS = [
    "product", "root_lot_id", "lot_id", "wafer_count", "wafer_ids",
    "model_id_list", "model_run_list", "run_count", "point_count",
    "tkout_time", "update_time", "source_root",
]


def _base_lot_query(lf, names: list[str], *, need: tuple[str, ...]):
    product_col = "__cache_product"
    root_col = _ci_col(names, "root_lot_id", "ROOT_LOT_ID")
    lot_col = _ci_col(names, "lot_id", "LOT_ID", "fab_lot_id", "FAB_LOT_ID")
    wafer_col = _ci_col(names, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    ts_col = _ci_col(names, "tkout_time", "TKOUT_TIME", "time", "TIME", "measure_time", "MEASURE_TIME", "timestamp", "TIMESTAMP")
    missing = []
    if not root_col:
        missing.append("root_lot_id")
    if not lot_col:
        missing.append("lot_id")
    for col in need:
        if not _ci_col(names, col):
            missing.append(col)
    if missing:
        return None, {"missing": missing}
    exprs = [
        pl.col(product_col).cast(_SORT_STR, strict=False).alias("product"),
        pl.col(root_col).cast(_SORT_STR, strict=False).alias("root_lot_id"),
        pl.col(lot_col).cast(_SORT_STR, strict=False).alias("lot_id"),
        (
            pl.col(wafer_col).cast(_SORT_STR, strict=False).alias("wafer_id")
            if wafer_col else pl.lit("").alias("wafer_id")
        ),
        (
            pl.col(ts_col).cast(_SORT_STR, strict=False).alias("tkout_time")
            if ts_col else pl.lit("").alias("tkout_time")
        ),
    ]
    return exprs, {
        "product": product_col,
        "root_lot_id": root_col,
        "lot_id": lot_col,
        "wafer_id": wafer_col,
        "tkout_time": ts_col,
    }


def _lot_summary_rows(q) -> dict[tuple[str, str, str], dict]:
    df = (
        q.group_by(["product", "root_lot_id", "lot_id"])
        .agg([
            pl.col("wafer_id").filter(pl.col("wafer_id") != "").n_unique().alias("wafer_count"),
            pl.col("wafer_id").filter(pl.col("wafer_id") != "").unique().alias("wafer_ids"),
            pl.len().alias("point_count"),
            pl.col("tkout_time").max().alias("tkout_time"),
        ])
        .collect()
    )
    out = {}
    for row in df.to_dicts():
        key = (row["product"], row["root_lot_id"], row["lot_id"])
        wafers = sorted({_safe_text(v) for v in row.get("wafer_ids") or [] if _safe_text(v)}, key=_natural_key)
        out[key] = {
            "product": row["product"],
            "root_lot_id": row["root_lot_id"],
            "lot_id": row["lot_id"],
            "wafer_count": int(row.get("wafer_count") or 0),
            "wafer_ids": ",".join(wafers),
            "point_count": int(row.get("point_count") or 0),
            "tkout_time": _safe_text(row.get("tkout_time")),
        }
    return out


def _refresh_et_lot_step_seq(product: str = "") -> dict:
    target = ET_LOT_STEP_SEQ
    source_root = DB_CACHE_TARGETS[target]["source_root"]
    lf = _scan_source(source_root, product)
    if lf is None:
        return _error_result(target, "ET source parquet not found")
    names = lf.collect_schema().names()
    base, cols = _base_lot_query(lf, names, need=("step_seq",))
    if base is None:
        return _error_result(target, "required columns missing", columns=cols.get("missing") or [])
    step_seq_col = _ci_col(names, "step_seq", "STEP_SEQ")
    step_id_col = _ci_col(names, "step_id", "STEP_ID")
    q = lf.select([
        *base,
        pl.col(step_seq_col).cast(_SORT_STR, strict=False).alias("step_seq"),
        (
            pl.col(step_id_col).cast(_SORT_STR, strict=False).alias("step_id")
            if step_id_col else pl.lit("").alias("step_id")
        ),
    ]).filter((pl.col("lot_id") != "") & (pl.col("step_seq") != ""))
    lot_rows = _lot_summary_rows(q)
    seq_df = (
        q.group_by(["product", "root_lot_id", "lot_id", "step_seq", "step_id"])
        .agg([pl.len().alias("pt"), pl.col("tkout_time").max().alias("tkout_time")])
        .collect()
    )
    seqs = defaultdict(list)
    for row in seq_df.to_dicts():
        key = (row["product"], row["root_lot_id"], row["lot_id"])
        seqs[key].append({
            "step_seq": _safe_text(row.get("step_seq")),
            "step_id": _safe_text(row.get("step_id")),
            "pt": int(row.get("pt") or 0),
            "tkout_time": _safe_text(row.get("tkout_time")),
        })
    updated_at = _now_iso()
    rows = []
    for key, base_row in sorted(lot_rows.items()):
        parts = sorted(seqs.get(key) or [], key=lambda r: (_natural_key(r["step_seq"]), _natural_key(r["step_id"])))
        step_seq_values = [r["step_seq"] for r in parts if r["step_seq"]]
        step_id_values = []
        for item in parts:
            sid = item["step_id"]
            if sid and sid not in step_id_values:
                step_id_values.append(sid)
        rows.append({
            **base_row,
            "step_seq_list": ",".join(step_seq_values),
            "step_seq_pt_list": ", ".join(f"{r['step_seq']}({r['pt']}pt)" for r in parts if r["step_seq"]),
            "step_id_list": ",".join(step_id_values),
            "update_time": updated_at,
            "source_root": source_root,
        })
    _write_parquet(cache_file(target), rows, ET_COLUMNS)
    result = _status_payload(target, row_count=len(rows), updated_at=updated_at, products=sorted({r["product"] for r in rows}))
    _write_meta(target, result, {**cols, "step_seq": step_seq_col, "step_id": step_id_col})
    return result


def _refresh_inline_lot_item(product: str = "") -> dict:
    target = INLINE_LOT_ITEM
    source_root = DB_CACHE_TARGETS[target]["source_root"]
    lf = _scan_source(source_root, product)
    if lf is None:
        return _error_result(target, "INLINE source parquet not found")
    names = lf.collect_schema().names()
    base, cols = _base_lot_query(lf, names, need=("item_id",))
    if base is None:
        return _error_result(target, "required columns missing", columns=cols.get("missing") or [])
    item_col = _ci_col(names, "item_id", "ITEM_ID")
    subitem_col = _ci_col(names, "subitem_id", "SUBITEM_ID")
    q = lf.select([
        *base,
        pl.col(item_col).cast(_SORT_STR, strict=False).alias("item_id"),
        (
            pl.col(subitem_col).cast(_SORT_STR, strict=False).alias("subitem_id")
            if subitem_col else pl.lit("").alias("subitem_id")
        ),
    ]).filter((pl.col("lot_id") != "") & (pl.col("item_id") != ""))
    lot_rows = _lot_summary_rows(q)
    item_df = (
        q.group_by(["product", "root_lot_id", "lot_id", "item_id"])
        .agg([
            pl.len().alias("pt"),
            pl.col("subitem_id").filter(pl.col("subitem_id") != "").n_unique().alias("subitems"),
        ])
        .collect()
    )
    items = defaultdict(list)
    subitem_count = defaultdict(int)
    for row in item_df.to_dicts():
        key = (row["product"], row["root_lot_id"], row["lot_id"])
        items[key].append({"item_id": _safe_text(row.get("item_id")), "pt": int(row.get("pt") or 0)})
        subitem_count[key] += int(row.get("subitems") or 0)
    updated_at = _now_iso()
    rows = []
    for key, base_row in sorted(lot_rows.items()):
        parts = sorted(items.get(key) or [], key=lambda r: _natural_key(r["item_id"]))
        rows.append({
            **base_row,
            "item_id_list": ",".join(r["item_id"] for r in parts if r["item_id"]),
            "item_pt_list": ", ".join(f"{r['item_id']}({r['pt']}pt)" for r in parts if r["item_id"]),
            "subitem_count": int(subitem_count.get(key) or 0),
            "update_time": updated_at,
            "source_root": source_root,
        })
    _write_parquet(cache_file(target), rows, INLINE_COLUMNS)
    result = _status_payload(target, row_count=len(rows), updated_at=updated_at, products=sorted({r["product"] for r in rows}))
    _write_meta(target, result, {**cols, "item_id": item_col, "subitem_id": subitem_col})
    return result


def _refresh_vm_lot_model(product: str = "") -> dict:
    target = VM_LOT_MODEL
    source_root = DB_CACHE_TARGETS[target]["source_root"]
    lf = _scan_source(source_root, product)
    if lf is None:
        return _error_result(target, "VM source parquet not found")
    names = lf.collect_schema().names()
    base, cols = _base_lot_query(lf, names, need=("model_id",))
    if base is None:
        return _error_result(target, "required columns missing", columns=cols.get("missing") or [])
    model_col = _ci_col(names, "model_id", "MODEL_ID")
    run_col = _ci_col(names, "run_id", "RUN_ID")
    q = lf.select([
        *base,
        pl.col(model_col).cast(_SORT_STR, strict=False).alias("model_id"),
        (
            pl.col(run_col).cast(_SORT_STR, strict=False).alias("run_id")
            if run_col else pl.lit("").alias("run_id")
        ),
    ]).filter((pl.col("lot_id") != "") & (pl.col("model_id") != ""))
    lot_rows = _lot_summary_rows(q)
    model_df = (
        q.group_by(["product", "root_lot_id", "lot_id", "model_id"])
        .agg([pl.len().alias("pt"), pl.col("run_id").filter(pl.col("run_id") != "").n_unique().alias("runs")])
        .collect()
    )
    models = defaultdict(list)
    run_count = defaultdict(int)
    for row in model_df.to_dicts():
        key = (row["product"], row["root_lot_id"], row["lot_id"])
        models[key].append({"model_id": _safe_text(row.get("model_id")), "pt": int(row.get("pt") or 0), "runs": int(row.get("runs") or 0)})
        run_count[key] += int(row.get("runs") or 0)
    updated_at = _now_iso()
    rows = []
    for key, base_row in sorted(lot_rows.items()):
        parts = sorted(models.get(key) or [], key=lambda r: _natural_key(r["model_id"]))
        rows.append({
            **base_row,
            "model_id_list": ",".join(r["model_id"] for r in parts if r["model_id"]),
            "model_run_list": ", ".join(f"{r['model_id']}({r['runs']}run/{r['pt']}pt)" for r in parts if r["model_id"]),
            "run_count": int(run_count.get(key) or 0),
            "update_time": updated_at,
            "source_root": source_root,
        })
    _write_parquet(cache_file(target), rows, VM_COLUMNS)
    result = _status_payload(target, row_count=len(rows), updated_at=updated_at, products=sorted({r["product"] for r in rows}))
    _write_meta(target, result, {**cols, "model_id": model_col, "run_id": run_col})
    return result


def _status_payload(target: str, *, row_count: int, updated_at: str, products: list[str]) -> dict:
    fp = cache_file(target)
    return {
        "ok": True,
        "target": target,
        "label": DB_CACHE_TARGETS[target]["label"],
        "source_root": DB_CACHE_TARGETS[target]["source_root"],
        "mode": "manual",
        "manual_enabled": True,
        "schedule_enabled": False,
        "scheduler_enabled": False,
        "cache_path": str(fp),
        "cache_exists": fp.is_file(),
        "row_count": int(row_count),
        "total_row_count": int(row_count),
        "products": products,
        "updated_at": updated_at,
        "latest_updated_at": updated_at,
        "unit_action": "filebrowser.cache.db.status",
    }


def refresh_db_cache(target: str, *, product: str = "", force: bool = True) -> dict:
    target = normalize_target(target)
    builders = {
        ET_LOT_STEP_SEQ: _refresh_et_lot_step_seq,
        INLINE_LOT_ITEM: _refresh_inline_lot_item,
        VM_LOT_MODEL: _refresh_vm_lot_model,
    }
    result = builders[target](product=product)
    return {**result, "unit_action": "filebrowser.cache.db.refresh"}


def db_cache_status(target: str) -> dict:
    target = normalize_target(target)
    fp = cache_file(target)
    meta_fp = meta_file(target)
    meta = {}
    if meta_fp.is_file():
        try:
            meta = json.loads(meta_fp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    row_count = int(meta.get("row_count") or 0)
    products: list[str] = []
    updated_at = str(meta.get("built_at") or "")
    if fp.is_file():
        try:
            lf = pl.scan_parquet(str(fp))
            row_count = int(lf.select(pl.len().alias("row_count")).collect().item(0, 0) or row_count)
            names = lf.collect_schema().names()
            if "product" in names:
                df = (
                    lf.select(pl.col("product").cast(_SORT_STR, strict=False).alias("product"))
                    .filter(pl.col("product").is_not_null() & (pl.col("product") != ""))
                    .unique()
                    .sort("product")
                    .head(500)
                    .collect()
                )
                products = [str(v) for v in df["product"].to_list() if str(v or "").strip()]
            if not updated_at and "update_time" in names:
                value = lf.select(pl.col("update_time").cast(_SORT_STR, strict=False).max().alias("updated_at")).collect().item(0, 0)
                updated_at = str(value or "")
        except Exception as exc:
            return {
                **_status_payload(target, row_count=row_count, updated_at=updated_at, products=products),
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
    if not updated_at and fp.is_file():
        updated_at = dt.datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds")
    return _status_payload(target, row_count=row_count, updated_at=updated_at, products=products)


def all_statuses() -> list[dict]:
    return [db_cache_status(target) for target in DB_CACHE_TARGETS]
