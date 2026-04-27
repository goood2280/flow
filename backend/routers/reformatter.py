"""routers/reformatter.py v7.3 — Admin-editable ET/INLINE index reformatter.

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
from core import s3_sync as _s3
from core.reformatter import (
    load_rules, save_rules, validate_rule, apply_rules, VALID_TYPES, VALID_AGGS,
    VALID_POINT_MODES, REFORMATTER_TABLE_COLUMNS, available_report_profiles,
    filter_rules_for_report, reformatter_table_to_rules, rules_to_reformatter_table,
)
from core.utils import read_source, serialize_rows

logger = logging.getLogger("flow.reformatter")
router = APIRouter(prefix="/api/reformatter", tags=["reformatter"])
BASE = PATHS.data_root / "reformatter"
BASE.mkdir(parents=True, exist_ok=True)


class RulesSave(BaseModel):
    product: str
    rules: List[Dict[str, Any]] = []


class TableSave(BaseModel):
    product: str
    rows: List[Dict[str, Any]] = []


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
    seen: set[str] = set()
    files = sorted(
        [p for p in BASE.iterdir() if p.is_file() and p.suffix.lower() in (".json", ".csv")],
        key=lambda p: (p.stem.lower(), 0 if p.suffix.lower() == ".json" else 1),
    ) if BASE.exists() else []
    for fp in files:
        if fp.stem.lower() in seen:
            continue
        seen.add(fp.stem.lower())
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


@router.get("/table")
def get_table(product: str = Query(...)):
    rules = load_rules(BASE, product)
    return {
        "product": product,
        "columns": REFORMATTER_TABLE_COLUMNS,
        "rows": rules_to_reformatter_table(rules),
        "rule_count": len(rules),
    }


@router.post("/table/save")
def save_table(req: TableSave):
    rules = reformatter_table_to_rules(req.rows)
    all_errs = []
    for i, r in enumerate(rules):
        errs = validate_rule(r)
        if errs:
            all_errs.append({"index": i, "alias": r.get("alias") or r.get("name", "?"), "errors": errs})
    save_rules(BASE, req.product, rules)
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, BASE / f"{req.product}.json")
    return {
        "ok": True,
        "product": req.product,
        "saved": len(rules),
        "columns": REFORMATTER_TABLE_COLUMNS,
        "rows": rules_to_reformatter_table(rules),
        "validation_errors": all_errs,
        "s3_sync": sync_result,
    }


@router.get("/report-profiles")
def report_profiles(product: str = Query(...)):
    rules = load_rules(BASE, product)
    return {
        "product": product,
        "profiles": available_report_profiles(rules),
        "default_variant": "default",
        "point_modes": sorted(list(VALID_POINT_MODES)),
    }


@router.post("/rules/save")
def save(req: RulesSave):
    # Validate all rules, but save regardless with `disabled` annotation for invalid ones
    all_errs = []
    for i, r in enumerate(req.rules):
        errs = validate_rule(r)
        if errs:
            all_errs.append({"index": i, "name": r.get("name", "?"), "errors": errs})
    save_rules(BASE, req.product, req.rules)
    sync_result = _s3.sync_saved_path(PATHS.data_root, PATHS.db_root, BASE / f"{req.product}.json")
    return {"ok": True, "saved": len(req.rules), "validation_errors": all_errs, "s3_sync": sync_result}


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
        "table_columns": REFORMATTER_TABLE_COLUMNS,
        "metadata_fields": {
            "no": "human sequence/order number",
            "addp": "real = raw item, addp = calculated item from addp_form",
            "item_id": "raw item_id for real rows; leave blank for addp rows",
            "cat": "category/family for reporting groups",
            "rawitem_id": "original ET/INLINE raw item identifier",
            "alias": "engineer-facing display label",
            "addp_form": "Python-style addp expression; reference raw items with {item_id}, e.g. max({A}, {B})",
            "alias_form": "display formatting hint",
            "abs": "sign normalization flag",
            "scale_factor": "raw scaling factor metadata",
            "speclow": "lower spec limit",
            "target": "target spec value",
            "spechigh": "upper spec limit",
            "report_order": "report row ordering",
            "y_axis": "linear or log10",
            "spec": "none | lsl | usl | both",
            "spec_check": "none | lsl | usl | both",
            "report_cat1": "primary report category",
            "report_cat2": "secondary report category",
            "use": "include in ET report scoreboard",
            "spec_order": "priority order for scoring metrics",
            "report_variant": "ET report bundle/version key",
            "point_mode": "all_pt | selected_pt | both",
            "report_enabled": "expose in ET report item list",
            "point_selector": "selected-pt metadata/filter definition",
            "report_audience": "internal | external | both",
            "tracker_attach": "auto-attach candidate when tracker lot/fab_lot links ET report",
        },
        "templates": {
            "scale_abs": {"name": "NEW_IDX", "type": "scale_abs", "source_col": "VALUE",
                           "filter": "", "scale": 1.0, "abs": True, "offset": 0,
                           "no": 10, "cat": "ET", "rawitem_id": "Rc", "alias": "Rc_abs",
                           "alias_form": "fixed3", "scale_factor": 1.0, "report_order": 10,
                           "y_axis": "linear", "spec": "both", "use": True, "spec_order": 10,
                           "report_variant": "default", "point_mode": "all_pt", "report_enabled": True, "point_selector": {},
                           "report_audience": "internal", "tracker_attach": True},
            "chip_combo": {"name": "COMBO", "type": "chip_combo",
                            "source_cols": ["A", "B", "C", "D"], "agg": "range"},
            "python_expr": {"name": "INDEX_EXPR", "type": "python_expr",
                             "inputs": {"A": "ET_A", "B": "ET_B"},
                             "expr": "max({A}, {B})",
                             "examples": [
                               "max({A}, {B}, {C})",
                               "manual({A}, {B}, {C}, [], 20, 10)"
                             ]},
            "shot_formula": {"name": "DC_INDEX", "type": "shot_formula",
                              "item_col": "item_id", "value_col": "value",
                              "group_by": ["product", "root_lot_id", "wafer_id", "step_id", "step_seq", "shot_x", "shot_y", "flat"],
                              "item_map": {"A": "Rc", "B": "Vth_n", "C": "Vth_p"},
                              "expr": "abs(A) + (B - C)", "agg": "mean", "filter": "",
                              "no": 20, "cat": "DC", "rawitem_id": "Rc/Vth_n/Vth_p", "alias": "M1DC_INDEX",
                              "alias_form": "fixed4", "report_order": 20, "y_axis": "linear",
                              "spec": "both", "use": True, "spec_order": 20,
                              "report_variant": "selected_pt_review", "point_mode": "selected_pt",
                              "report_enabled": True, "point_selector": {"mode": "named_points", "points": []},
                              "report_audience": "external", "tracker_attach": False},
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
        "report_selection_example": {
            "description": "Use report_variant + point_mode to prepare different ET mail/report bundles.",
            "examples": [
                {
                    "report_variant": "default",
                    "point_mode": "all_pt",
                    "meaning": "internal full-point lot report",
                },
                {
                    "report_variant": "selected_pt_review",
                    "point_mode": "selected_pt",
                    "meaning": "customer/external selected-point review report",
                },
            ],
        },
    }
