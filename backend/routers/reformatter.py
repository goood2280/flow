"""routers/reformatter.py v7.2 — Admin-editable ET/INLINE index reformatter.

Engineers register per-product JSON rules; File Browser download + Dashboard
chart + ML training all share the same logic.

Endpoints:
  GET  /api/reformatter/products                       — list products with rules
  GET  /api/reformatter/rules?product=                 — load rules
  POST /api/reformatter/rules/save                     — overwrite rules for a product
  POST /api/reformatter/validate                       — validate one rule (no save)
  POST /api/reformatter/preview                        — apply rules, return head(N) + derived col list
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Dict, Any
import polars as pl
from core.paths import PATHS
from core.reformatter import (
    load_rules, save_rules, validate_rule, apply_rules, VALID_TYPES, VALID_AGGS,
)
from core.utils import read_source, serialize_rows

logger = logging.getLogger("holweb.reformatter")
router = APIRouter(prefix="/api/reformatter", tags=["reformatter"])
BASE = PATHS.data_root / "reformatter"
BASE.mkdir(parents=True, exist_ok=True)


class RulesSave(BaseModel):
    product: str
    rules: List[Dict[str, Any]] = []


class PreviewReq(BaseModel):
    product: str
    source_type: str = "flat"
    root: str = "ET"
    product_folder: str = ""     # actual folder name (often same as product)
    file: str = ""
    rows: int = 20
    rules: List[Dict[str, Any]] = []  # optional inline rules (unsaved draft)


@router.get("/products")
def list_products():
    """List all product names that have rule files."""
    out = []
    for fp in sorted(BASE.glob("*.json")):
        try:
            rules = load_rules(BASE, fp.stem)
            out.append({"product": fp.stem, "rule_count": len(rules),
                         "enabled_count": sum(1 for r in rules if not r.get("disabled"))})
        except Exception:
            pass
    return {"products": out}


@router.get("/rules")
def get_rules(product: str = Query(...)):
    return {"product": product, "rules": load_rules(BASE, product)}


@router.post("/rules/save")
def save(req: RulesSave):
    # Validate all rules, but save regardless with `disabled` annotation for invalid ones
    all_errs = []
    for i, r in enumerate(req.rules):
        errs = validate_rule(r)
        if errs:
            all_errs.append({"index": i, "name": r.get("name", "?"), "errors": errs})
    save_rules(BASE, req.product, req.rules)
    return {"ok": True, "saved": len(req.rules), "validation_errors": all_errs}


@router.post("/validate")
def validate(rule: Dict[str, Any]):
    errs = validate_rule(rule)
    return {"ok": not errs, "errors": errs}


@router.post("/preview")
def preview(req: PreviewReq):
    """Apply rules to a sample of rows and return the resulting derived columns."""
    rules = req.rules if req.rules else load_rules(BASE, req.product)
    if not rules:
        raise HTTPException(400, "No rules provided or saved for this product")
    try:
        df = read_source(req.source_type, req.root,
                         req.product_folder or req.product, req.file, max_files=1)
    except Exception as e:
        raise HTTPException(400, f"Read error: {e}")
    if df.height == 0:
        raise HTTPException(400, "No rows")
    orig_cols = set(df.columns)
    df_out = apply_rules(df, rules, enabled_only=True)
    new_cols = [c for c in df_out.columns if c not in orig_cols]
    head = df_out.head(req.rows)
    return {
        "new_columns": new_cols,
        "rows_sampled": df.height,
        "preview": serialize_rows(head.to_dicts()),
        "columns": list(df_out.columns),
        "rule_count": len(rules),
    }


@router.get("/schema")
def schema_help():
    """Return the rule-type catalog for the admin UI."""
    return {
        "types": sorted(list(VALID_TYPES)),
        "aggs": sorted(list(VALID_AGGS)),
        "templates": {
            "scale_abs": {"name": "NEW_IDX", "type": "scale_abs", "source_col": "VALUE",
                           "filter": "", "scale": 1.0, "abs": True, "offset": 0},
            "chip_combo": {"name": "COMBO", "type": "chip_combo",
                            "source_cols": ["A", "B", "C", "D"], "agg": "range"},
            "shot_agg":   {"name": "SHOT_MEAN", "type": "shot_agg", "source_col": "VTH",
                            "group_by": ["LOT_WF", "SHOT_X", "SHOT_Y"], "agg": "mean"},
            "step_skew":  {"name": "STEP_DELTA", "type": "step_skew",
                            "source_col": "VTH", "step_col": "STEP_ID",
                            "group_by": ["LOT_WF", "SHOT_X", "SHOT_Y"], "baseline_step": "0"},
            "poly2_window": {"name": "OPT_WIN", "type": "poly2_window",
                              "x_col": "X_INDEX", "y_col": "VTH",
                              "group_by": ["LOT_WF", "SHOT_X", "SHOT_Y"],
                              "usl": 0.8, "lsl": 0.2},
            "bucket":     {"name": "GRADE", "type": "bucket", "source_col": "VTH",
                            "edges": [0, 0.5, 0.7, 1.0], "labels": ["LOW", "MID", "HIGH"]},
        },
    }
