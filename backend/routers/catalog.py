"""routers/catalog.py v7.3 — Unified admin catalog for:
  • Matching tables (CSV)     — <db_root>/matching/
  • Reformatter rules (JSON)  — data/flow-data/reformatter/
  • Product YAML configs       — data/flow-data/product_config/
  • S3 sync status + action

All read/write happens here so admin UI has one tab for "data artifacts".
"""
import json
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any
import polars as pl

from core.paths import PATHS
from core.domain import (
    MATCHING_TABLES,
    PROCESS_AREAS,
    classify_process_area,
    seed_area_rows,
)
from core import s3_sync as _s3
from core import product_config as _pc

logger = logging.getLogger("flow.catalog")
router = APIRouter(prefix="/api/catalog", tags=["catalog"])

# Separate router for the top-level `/api/match/*` endpoints (area-rollup etc.)
match_router = APIRouter(prefix="/api/match", tags=["match"])

def _match_dir(create: bool = False) -> Path:
    d = PATHS.db_root / "matching"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────────────────────
# Matching tables
# ─────────────────────────────────────────────────────────────
@router.get("/matching/schema")
def matching_schema():
    return {"tables": MATCHING_TABLES}


@router.get("/matching/list")
def matching_list():
    out = []
    for name, meta in MATCHING_TABLES.items():
        fp = _match_dir() / meta["file"]
        exists = fp.exists()
        rows = 0; cols = []
        if exists:
            try:
                df = pl.read_csv(fp, infer_schema_length=200)
                rows = df.height
                cols = list(df.columns)
            except Exception as e:
                logger.warning(f"read {fp}: {e}")
        missing = [c for c in meta.get("required_cols", []) if c not in cols] if cols else meta.get("required_cols", [])
        out.append({
            "name": name, "file": meta["file"],
            "description": meta["description"],
            "applies_to": meta["applies_to"],
            "required_cols": meta.get("required_cols", []),
            "exists": exists, "rows": rows, "cols": cols,
            "missing_cols": missing,
            "size_kb": round(fp.stat().st_size / 1024, 1) if exists else 0,
        })
    return {"tables": out}


@router.get("/matching/preview")
def matching_preview(name: str = Query(...), rows: int = Query(30)):
    meta = MATCHING_TABLES.get(name)
    if not meta:
        raise HTTPException(404, "unknown matching table")
    fp = _match_dir() / meta["file"]
    if not fp.exists():
        return {"columns": [], "rows": []}
    try:
        df = pl.read_csv(fp, infer_schema_length=500)
        return {"columns": list(df.columns), "rows": df.head(rows).to_dicts(), "total": df.height}
    except Exception as e:
        raise HTTPException(500, str(e))


class MatchSave(BaseModel):
    name: str
    rows: List[Dict[str, Any]]


@router.post("/matching/save")
def matching_save(req: MatchSave):
    meta = MATCHING_TABLES.get(req.name)
    if not meta:
        raise HTTPException(404, "unknown matching table")
    fp = _match_dir(create=True) / meta["file"]
    fp.parent.mkdir(parents=True, exist_ok=True)
    try:
        rows = req.rows
        # v8.2.1: matching_step — auto-fill `area` from canonical_step where blank.
        if req.name == "matching_step" and rows:
            rows = seed_area_rows(rows)
        # Ensure all required cols are present in at least one row (or create from keys)
        df = pl.from_dicts(rows) if rows else pl.DataFrame(schema={c: pl.Utf8 for c in meta.get("required_cols", [])})
        df.write_csv(fp)
        sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, fp)
        return {"ok": True, "rows": df.height, "path": str(fp), "s3_sync": sync_result}
    except Exception as e:
        raise HTTPException(500, f"save failed: {e}")


@router.get("/matching/download")
def matching_download(name: str = Query(...)):
    meta = MATCHING_TABLES.get(name)
    if not meta:
        raise HTTPException(404)
    fp = _match_dir() / meta["file"]
    if not fp.exists():
        raise HTTPException(404, "file not found")
    return FileResponse(str(fp), filename=meta["file"], media_type="text/csv")


# ─────────────────────────────────────────────────────────────
# Product YAML
# ─────────────────────────────────────────────────────────────
PC_ROOT = PATHS.data_root


@router.get("/product/list")
def product_list():
    return {"products": _pc.list_products(PC_ROOT), "template": _pc.TEMPLATE, "schema": _pc.SCHEMA}


@router.get("/product/load")
def product_load(product: str = Query(...)):
    return {"product": product, "config": _pc.load(PC_ROOT, product)}


class ProductSave(BaseModel):
    product: str
    config: Dict[str, Any]


@router.post("/product/save")
def product_save(req: ProductSave):
    errs = _pc.validate(req.config)
    _pc.save(PC_ROOT, req.product, req.config)
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, _pc.config_path(PC_ROOT, req.product))
    return {"ok": not errs, "errors": errs, "s3_sync": sync_result}


# ─────────────────────────────────────────────────────────────
# S3 sync
# ─────────────────────────────────────────────────────────────
@router.get("/s3/config")
def s3_config_get():
    cfg = _s3.load_config(PATHS.data_root)
    return {"config": cfg, "boto3_installed": _s3._HAS_BOTO}


class S3ConfigSave(BaseModel):
    config: Dict[str, Any]


@router.post("/s3/config/save")
def s3_config_save(req: S3ConfigSave):
    _s3.save_config(PATHS.data_root, req.config)
    return {"ok": True}


@router.get("/s3/artifacts")
def s3_artifacts():
    arts = _s3.list_artifacts(PATHS.data_root, PATHS.db_root)
    idx = _s3.last_sync_index(PATHS.data_root)
    for a in arts:
        last = idx.get(a["key"])
        a["last_sync"] = last
        a["in_sync"] = bool(last and last.get("sha1") == a.get("sha1") and last.get("status") == "uploaded")
    return {"artifacts": arts}


@router.post("/s3/sync")
def s3_sync(filter_type: str = Query("")):
    results = _s3.sync_all(PATHS.data_root, PATHS.db_root, filter_type or None)
    return {"results": results, "count": len(results)}


@router.get("/s3/status")
def s3_status(limit: int = Query(100)):
    return {"events": _s3.recent_status(PATHS.data_root, limit)}


# ─────────────────────────────────────────────────────────────
# v8.2.1: Process-area rollup (preemptive — SHAP importance grouping will call this)
# ─────────────────────────────────────────────────────────────
def _load_matching_step_rows() -> list:
    """Read matching_step.csv → list[dict] with blank `area` auto-filled.
    Returns [] if the file is missing or empty."""
    meta = MATCHING_TABLES.get("matching_step")
    if not meta:
        return []
    fp = _match_dir() / meta["file"]
    if not fp.exists():
        return []
    try:
        df = pl.read_csv(fp, infer_schema_length=500)
        rows = df.to_dicts()
    except Exception as e:
        logger.warning(f"matching_step read failed: {e}")
        return []
    # Ensure area field is present (older csvs may not have it) and auto-seed blanks.
    for r in rows:
        r.setdefault("area", None)
    return seed_area_rows(rows)


@match_router.get("/areas")
def match_areas():
    """Return the canonical ordered list of process areas (dropdown source)."""
    return {"areas": PROCESS_AREAS}


@match_router.get("/area-rollup")
def match_area_rollup(step_ids: str = Query("", description="Comma-separated list of raw_step_id. Empty = all.")):
    """Group matching_step rows by `area` and return per-area count + member step_ids.

    Query:
      - step_ids: optional comma-separated raw_step_id filter. Unknown ids are silently dropped.

    Response:
      {
        "total": <int>,            # number of matching_step rows considered
        "matched": <int>,          # rows with a non-null area
        "unmatched": <int>,
        "rollup": [
          {"area": "PC", "count": 42, "step_ids": ["AA100010", ...]},
          ...
        ]
      }

    Rollup is ordered by the canonical PROCESS_AREAS list (STI → BEOL-M6).
    Areas with 0 count are omitted. An "(unmatched)" bucket is appended when unmatched>0.
    """
    rows = _load_matching_step_rows()
    # Optional filter
    filter_ids = None
    if step_ids:
        filter_ids = {s.strip() for s in step_ids.split(",") if s.strip()}
    buckets: dict[str, list[str]] = {a: [] for a in PROCESS_AREAS}
    unmatched: list[str] = []
    total = 0
    for r in rows:
        raw = str(r.get("raw_step_id") or "")
        if filter_ids is not None and raw not in filter_ids:
            continue
        total += 1
        area = r.get("area")
        if area in buckets:
            buckets[area].append(raw)
        else:
            # Try on-the-fly classification if area cell was absent/stale
            guess = classify_process_area(r.get("canonical_step") or raw)
            if guess and guess in buckets:
                buckets[guess].append(raw)
            else:
                unmatched.append(raw)
    rollup = [
        {"area": a, "count": len(ids), "step_ids": ids}
        for a, ids in buckets.items() if ids
    ]
    if unmatched:
        rollup.append({"area": "(unmatched)", "count": len(unmatched), "step_ids": unmatched})
    matched = total - len(unmatched)
    return {
        "total": total,
        "matched": matched,
        "unmatched": len(unmatched),
        "rollup": rollup,
    }
