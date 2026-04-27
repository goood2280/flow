"""core/reformatter.py v7.3 — ET/INLINE reformatter engine.

Replaces Spotfire's "per-product calculated-column" workflow. An engineer
registers a set of RULES per product that derive INDICES from raw rows, then
File Browser download / Dashboard chart / ML training all apply the same logic.

Rule types (declarative JSON — no arbitrary code exec by default):

  scale_abs
    { "name": "VTH_IDX", "type": "scale_abs",
      "source_col": "VALUE", "filter": "ITEM_ID == 'VTH'",
      "scale": 1.0, "abs": true, "offset": 0 }
    → abs(VALUE * scale + offset) for rows matching filter

  chip_combo
    { "name": "CD_RANGE", "type": "chip_combo",
      "source_cols": ["A", "B", "C", "D"],
      "agg": "range"  // min|max|mean|median|std|range|sum
    }
    → per-row: agg(A,B,C,D)

  shot_agg
    { "name": "VTH_SHOT_MEAN", "type": "shot_agg",
      "source_col": "VTH_IDX",
      "group_by": ["LOT_WF", "SHOT_X", "SHOT_Y"],   // default shot-level
      "agg": "mean"                                  // mean|std|min|max|range|count
    }
    → aggregate VTH_IDX across rows sharing the group keys

  step_skew
    { "name": "VTH_STEP_SKEW", "type": "step_skew",
      "source_col": "VTH_IDX", "step_col": "STEP_ID",
      "group_by": ["LOT_WF", "SHOT_X", "SHOT_Y"], "baseline_step": "0" }
    → per group: value at step - value at baseline_step (for skew/shift analysis)

  poly2_window
    { "name": "VTH_OPT_WIN", "type": "poly2_window",
      "x_col": "X_INDEX", "y_col": "VTH_IDX",
      "group_by": ["LOT_WF", "SHOT_X", "SHOT_Y"],
      "usl": 0.8, "lsl": 0.2 }
    → fit y = a*x^2 + b*x + c per group; find x where y crosses USL/LSL;
      output window width = x_high - x_low (and midpoint as *_MID)

  bucket
    { "name": "VTH_GRADE", "type": "bucket",
      "source_col": "VTH_IDX",
      "edges": [0, 0.5, 0.7, 1.0], "labels": ["LOW", "MID", "HIGH"] }
    → categorical bucket

All operations use Polars expressions — no pickled code, no `eval` on user input.
`filter` strings go through `pl.sql_expr` (same parser as Dashboard).

Rules are stored per product as internal JSON, with CSV table fallback:
data/flow-data/reformatter/<product>.json or <product>.csv
"""
from __future__ import annotations
import ast
import importlib.util
import json
import logging
import math
import re
from pathlib import Path
from typing import List, Dict, Any
import polars as pl
from core.paths import PATHS

logger = logging.getLogger("flow.reformatter")

VALID_TYPES = {"scale_abs", "chip_combo", "shot_agg", "step_skew", "poly2_window", "bucket", "python_expr", "shot_formula"}
VALID_AGGS = {"mean", "sum", "count", "min", "max", "median", "std", "range"}
VALID_POINT_MODES = {"all_pt", "selected_pt", "both"}
SHOT_FORMULA_GROUP_BY = ["product", "root_lot_id", "wafer_id", "step_id", "step_seq", "shot_x", "shot_y", "flat", "flat_zone"]
REFORMATTER_TABLE_COLUMNS = [
    "no", "addp", "item_id", "alias", "addp_form", "abs", "scale_factor",
    "speclow", "target", "spechigh", "report_order", "y_axis", "spec_check",
    "report_cat1", "report_cat2", "use",
]


def _bool_value(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "y", "yes", "true", "t", "on", "use", "used"}:
        return True
    if text in {"0", "n", "no", "false", "f", "off", "unused", ""}:
        return False
    return default


def _float_or_none(value):
    try:
        if value is None or value == "":
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _spec_from_value(value, lsl=None, usl=None) -> str:
    text = str(value or "").strip().lower()
    if text in {"", "none", "n", "no", "false", "0", "-"}:
        return "none"
    if text in {"lsl", "low", "speclow"}:
        return "lsl"
    if text in {"usl", "high", "spechigh"}:
        return "usl"
    if text in {"both", "y", "yes", "true", "1", "check", "spec"}:
        if lsl is not None and usl is None:
            return "lsl"
        if usl is not None and lsl is None:
            return "usl"
        return "both"
    return text if text in {"lsl", "usl", "both"} else "none"


def _table_bool(value, default: bool = False) -> str:
    return "Y" if _bool_value(value, default) else "N"


def _split_item_ids(value: str) -> list[str]:
    return [x.strip() for x in re.split(r"[/,;|]+", str(value or "")) if x.strip()]


def _formula_placeholders(expr: str) -> list[str]:
    seen: list[str] = []
    for raw in re.findall(r"\{([^{}]+)\}", str(expr or "")):
        item = raw.strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def _unique_expr_token(raw: str, used: set[str]) -> str:
    base = _sanitize_expr_name(raw)
    token = base
    idx = 2
    while token in used:
        token = f"{base}_{idx}"
        idx += 1
    used.add(token)
    return token


def _normalize_addp_formula(addp_form: str, legacy_items: list[str] | None = None) -> tuple[str, dict[str, str], list[str]]:
    """Convert `{raw item}` references into safe expression variables.

    `addp` rows do not have their own real `item_id`. Raw item dependencies
    are declared inside `addp_form`, for example `max({A}, {B})`.
    """
    expr = str(addp_form or "").strip()
    items = _formula_placeholders(expr)
    if not items and legacy_items:
        items = [x for x in legacy_items if x]
    used: set[str] = set()
    item_map: dict[str, str] = {}
    for item in items:
        token = _unique_expr_token(item, used)
        item_map[token] = item
        expr = expr.replace("{" + item + "}", token)
    return expr, item_map, items


def _rule_name_from(alias: str, item_id: str, no: Any = "") -> str:
    seed = str(alias or item_id or no or "ITEM")
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", seed).strip("_").upper()
    if not safe:
        safe = "ITEM"
    if safe[0].isdigit():
        safe = "I_" + safe
    return safe


def _sql_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


# ─────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────
def _rules_path(base_dir: Path, product: str) -> Path:
    safe = "".join(c for c in product if c.isalnum() or c in "_-")
    return base_dir / f"{safe or 'DEFAULT'}.json"


def _rules_csv_path(base_dir: Path, product: str) -> Path:
    return _rules_path(base_dir, product).with_suffix(".csv")


def load_rules(base_dir: Path, product: str) -> List[Dict[str, Any]]:
    fp = _rules_path(base_dir, product)
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [normalize_rule_metadata(r) for r in data if isinstance(r, dict)]
        except Exception as e:
            logger.warning(f"Failed to load rules {fp}: {e}")
            return []
    csv_fp = _rules_csv_path(base_dir, product)
    if not csv_fp.exists():
        return []
    try:
        rows = pl.read_csv(str(csv_fp), infer_schema_length=5000, try_parse_dates=False).to_dicts()
        return reformatter_table_to_rules(rows)
    except Exception as e:
        logger.warning(f"Failed to load reformatter CSV {csv_fp}: {e}")
        return []


def save_rules(base_dir: Path, product: str, rules: List[Dict[str, Any]]) -> None:
    fp = _rules_path(base_dir, product)
    fp.parent.mkdir(parents=True, exist_ok=True)
    clean = [normalize_rule_metadata(r) for r in rules if isinstance(r, dict)]
    fp.write_text(json.dumps(clean, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_rule_metadata(rule: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize reporting metadata so ET report can select versioned item sets.

    Fields:
      report_variant   — reporting bundle name, e.g. 'full', 'selected', 'mail_core'
      point_mode       — all_pt | selected_pt | both
      report_enabled   — whether ET report should expose this rule
      point_selector   — free-form selector metadata for selected-pt reporting
      report_audience  — internal | external | both
      tracker_attach   — include when tracker lot/fab_lot auto-attaches ET report
    """
    out = dict(rule or {})
    if out.get("rawitem_id") in (None, "") and out.get("item_id") not in (None, ""):
        out["rawitem_id"] = out.get("item_id")
    if out.get("expr") in (None, "") and out.get("addp_form") not in (None, ""):
        out["expr"] = out.get("addp_form")
    if out.get("scale") in (None, "") and out.get("scale_factor") not in (None, ""):
        out["scale"] = out.get("scale_factor")
    if out.get("lsl") in (None, "") and out.get("speclow") not in (None, ""):
        out["lsl"] = out.get("speclow")
    if out.get("usl") in (None, "") and out.get("spechigh") not in (None, ""):
        out["usl"] = out.get("spechigh")
    if out.get("cat") in (None, "") and out.get("report_cat1") not in (None, ""):
        out["cat"] = out.get("report_cat1")
    if out.get("report_cat1") in (None, "") and out.get("cat") not in (None, ""):
        out["report_cat1"] = out.get("cat")
    if out.get("spec") in (None, "") and out.get("spec_check") not in (None, ""):
        out["spec"] = _spec_from_value(out.get("spec_check"), _float_or_none(out.get("lsl")), _float_or_none(out.get("usl")))
    if out.get("spec_check") in (None, ""):
        out["spec_check"] = out.get("spec") or "none"
    if "use" not in out:
        out["use"] = True
    out["use"] = _bool_value(out.get("use"), True)
    if "abs" in out:
        out["abs"] = _bool_value(out.get("abs"), False)
    if "report_order" not in out and out.get("no") not in (None, ""):
        out["report_order"] = out.get("no")
    out["report_variant"] = str(out.get("report_variant") or "default").strip() or "default"
    pm = str(out.get("point_mode") or "both").strip().lower()
    out["point_mode"] = pm if pm in VALID_POINT_MODES else "both"
    out["report_enabled"] = _bool_value(out.get("report_enabled", True), True)
    if out.get("point_selector") is None:
        out["point_selector"] = {}
    aud = str(out.get("report_audience") or "internal").strip().lower()
    out["report_audience"] = aud if aud in {"internal", "external", "both"} else "internal"
    out["tracker_attach"] = _bool_value(out.get("tracker_attach", out["point_mode"] in ("all_pt", "both") and out["report_audience"] in ("internal", "both")), False)
    return out


def rules_to_reformatter_table(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Expose internal JSON rules as the product reformatter table users maintain."""
    rows: list[dict[str, Any]] = []
    for raw in rules or []:
        r = normalize_rule_metadata(raw)
        addp = str(r.get("addp") or "").strip().lower()
        if addp not in {"real", "addp"}:
            addp = "addp" if r.get("type") in {"shot_formula", "python_expr", "chip_combo"} else "real"
        item_id = "" if addp == "addp" else str(r.get("rawitem_id") or "").strip()
        row = {
            "no": r.get("no", ""),
            "addp": addp,
            "item_id": item_id,
            "alias": r.get("alias") or r.get("name") or item_id,
            "addp_form": r.get("addp_form") or r.get("expr") or "",
            "abs": _table_bool(r.get("abs"), False),
            "scale_factor": r.get("scale_factor", r.get("scale", "")),
            "speclow": r.get("speclow", r.get("lsl", "")),
            "target": r.get("target", ""),
            "spechigh": r.get("spechigh", r.get("usl", "")),
            "report_order": r.get("report_order", r.get("no", "")),
            "y_axis": r.get("y_axis", "linear"),
            "spec_check": r.get("spec_check", r.get("spec", "none")),
            "report_cat1": r.get("report_cat1", r.get("cat", "")),
            "report_cat2": r.get("report_cat2", ""),
            "use": _table_bool(r.get("use"), True),
        }
        rows.append({col: row.get(col, "") for col in REFORMATTER_TABLE_COLUMNS})
    return sorted(rows, key=lambda x: (int(float(x["report_order"])) if str(x.get("report_order") or "").replace(".", "", 1).isdigit() else 9999, str(x.get("alias") or "")))


def reformatter_table_to_rules(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert the maintained table schema into executable reformatter rules."""
    rules: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("item_id") or row.get("rawitem_id") or "").strip()
        alias = str(row.get("alias") or item_id or "").strip()
        if not item_id and not alias:
            continue
        addp = str(row.get("addp") or "").strip().lower()
        addp_form = str(row.get("addp_form") or "").strip()
        if addp not in {"real", "addp"}:
            addp = "addp" if addp_form else "real"
        no = row.get("no", "")
        lsl = _float_or_none(row.get("speclow"))
        usl = _float_or_none(row.get("spechigh"))
        spec = _spec_from_value(row.get("spec_check"), lsl, usl)
        base = {
            "name": _rule_name_from(alias, item_id, no),
            "no": no,
            "addp": addp,
            "rawitem_id": "" if addp == "addp" else item_id,
            "alias": alias or item_id,
            "addp_form": addp_form,
            "abs": _bool_value(row.get("abs"), False),
            "scale_factor": _float_or_none(row.get("scale_factor")) if _float_or_none(row.get("scale_factor")) is not None else 1.0,
            "scale": _float_or_none(row.get("scale_factor")) if _float_or_none(row.get("scale_factor")) is not None else 1.0,
            "lsl": lsl,
            "target": _float_or_none(row.get("target")),
            "usl": usl,
            "speclow": row.get("speclow", ""),
            "spechigh": row.get("spechigh", ""),
            "report_order": int(float(row.get("report_order"))) if str(row.get("report_order") or "").replace(".", "", 1).isdigit() else (int(float(no)) if str(no).replace(".", "", 1).isdigit() else 9999),
            "y_axis": str(row.get("y_axis") or "linear").strip() or "linear",
            "spec": spec,
            "spec_check": row.get("spec_check") or spec,
            "cat": str(row.get("report_cat1") or "").strip(),
            "report_cat1": str(row.get("report_cat1") or "").strip(),
            "report_cat2": str(row.get("report_cat2") or "").strip(),
            "use": _bool_value(row.get("use"), True),
            "report_enabled": True,
            "report_variant": "default",
            "point_mode": "both",
            "report_audience": "internal",
        }
        if addp == "addp":
            expr, item_map, source_items = _normalize_addp_formula(addp_form, _split_item_ids(item_id))
            base.update({
                "type": "shot_formula",
                "item_col": "item_id",
                "value_col": "value",
                "group_by": SHOT_FORMULA_GROUP_BY,
                "item_map": item_map,
                "source_item_ids": source_items,
                "expr": expr,
                "agg": "mean",
            })
        else:
            first_item = _split_item_ids(item_id)[0] if _split_item_ids(item_id) else item_id
            base.update({
                "type": "scale_abs",
                "source_col": "value",
                "filter": f"item_id == '{_sql_string(first_item)}'" if first_item else "",
                "offset": 0.0,
            })
        rules.append(normalize_rule_metadata(base))
    return rules


def available_report_profiles(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets: Dict[tuple[str, str], Dict[str, Any]] = {}
    for raw in rules or []:
        r = normalize_rule_metadata(raw)
        if r.get("disabled") or not r.get("report_enabled", True):
            continue
        key = (r["report_variant"], r["point_mode"])
        b = buckets.setdefault(key, {
            "report_variant": r["report_variant"],
            "point_mode": r["point_mode"],
            "rule_count": 0,
            "aliases": [],
            "audiences": [],
            "tracker_attach_count": 0,
        })
        b["rule_count"] += 1
        alias = str(r.get("alias") or r.get("name") or "").strip()
        if alias and alias not in b["aliases"]:
            b["aliases"].append(alias)
        aud = r.get("report_audience") or "internal"
        if aud not in b["audiences"]:
            b["audiences"].append(aud)
        if r.get("tracker_attach"):
            b["tracker_attach_count"] += 1
    return sorted(buckets.values(), key=lambda x: (x["report_variant"], x["point_mode"]))


def filter_rules_for_report(
    rules: List[Dict[str, Any]],
    report_variant: str = "",
    point_mode: str = "",
) -> List[Dict[str, Any]]:
    want_variant = str(report_variant or "").strip()
    want_mode = str(point_mode or "").strip().lower()
    out = []
    for raw in rules or []:
        r = normalize_rule_metadata(raw)
        if r.get("disabled") or not r.get("report_enabled", True):
            continue
        if want_variant and r["report_variant"] != want_variant:
            continue
        if want_mode and want_mode in VALID_POINT_MODES:
            rule_mode = r["point_mode"]
            if rule_mode not in ("both", want_mode):
                continue
        out.append(r)
    return out


def validate_rule(rule: Dict[str, Any]) -> List[str]:
    """Return list of validation errors ([] if valid).

    Operational metadata fields are optional and passed through as-is:
      no            — ordering / human sequence number
      cat           — category or engineering family
      rawitem_id    — original ET/INLINE raw item identifier
      alias         — engineer-facing display name
      alias_form    — formatting hint for display/reporting
      abs           — sign normalization flag (mostly scale_abs)
      scale_factor  — raw scaling factor
      report_order  — report sort order
      y_axis        — linear | log10
      spec          — lsl | usl | both | none
      use           — mail/scoring participation flag
      spec_order    — scoring/report priority among spec-checked metrics
      report_variant — ET report version key (e.g. full / selected_pt_only)
      point_mode    — all_pt | selected_pt | both
      report_enabled — include in ET report candidate list
      point_selector — selected-point selector metadata
      report_audience — internal | external | both
      tracker_attach — tracker lot/fab lot auto-attach candidate
      owner         — engineer in charge
      approved      — review status
      tags          — free-form labels
    """
    errs = []
    if not rule.get("name"): errs.append("'name' required")
    t = rule.get("type")
    if t not in VALID_TYPES: errs.append(f"type must be one of {VALID_TYPES}")
    if t == "scale_abs":
        if not rule.get("source_col"): errs.append("scale_abs needs source_col")
    elif t == "chip_combo":
        if not rule.get("source_cols"): errs.append("chip_combo needs source_cols[]")
        if rule.get("agg") not in VALID_AGGS: errs.append(f"agg must be one of {VALID_AGGS}")
    elif t == "shot_agg":
        if not rule.get("source_col"): errs.append("shot_agg needs source_col")
        if rule.get("agg") not in VALID_AGGS: errs.append(f"agg must be one of {VALID_AGGS}")
    elif t == "step_skew":
        if not rule.get("source_col") or not rule.get("step_col"):
            errs.append("step_skew needs source_col + step_col")
    elif t == "poly2_window":
        for k in ("x_col", "y_col", "usl", "lsl"):
            if rule.get(k) is None: errs.append(f"poly2_window needs {k}")
    elif t == "bucket":
        if not rule.get("source_col") or not rule.get("edges"):
            errs.append("bucket needs source_col + edges")
    elif t == "python_expr":
        if not rule.get("expr"):
            errs.append("python_expr needs expr")
    elif t == "shot_formula":
        if not rule.get("expr"):
            errs.append("shot_formula needs expr")
        if not rule.get("item_col"):
            errs.append("shot_formula needs item_col")
        if not rule.get("value_col"):
            errs.append("shot_formula needs value_col")
    return errs


# ─────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────
def _safe_float_col(df: pl.DataFrame, col: str) -> pl.Expr:
    if col not in df.columns:
        return pl.lit(None, dtype=pl.Float64)
    return pl.col(col).cast(pl.Float64, strict=False)


_ALLOWED_AST_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load, ast.Constant,
    ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.USub,
    ast.UAdd, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.BoolOp, ast.And, ast.Or, ast.IfExp, ast.List, ast.Tuple,
)


def _expr_like(v) -> bool:
    return isinstance(v, pl.Expr)


def _f_abs(x):
    return x.abs() if _expr_like(x) else abs(x)


def _f_sqrt(x):
    return x.sqrt() if _expr_like(x) else math.sqrt(x)


def _f_log(x):
    return x.log() if _expr_like(x) else math.log(x)


def _f_exp(x):
    return x.exp() if _expr_like(x) else math.exp(x)


def _f_round(x, n=0):
    return x.round(int(n)) if _expr_like(x) else round(x, int(n))


def _f_clip(x, lo, hi):
    return x.clip(lo, hi) if _expr_like(x) else min(max(x, lo), hi)


def _f_min(*args):
    if any(_expr_like(a) for a in args):
        return pl.min_horizontal(list(args))
    return min(args)


def _f_max(*args):
    if any(_expr_like(a) for a in args):
        return pl.max_horizontal(list(args))
    return max(args)


def _f_where(cond, a, b):
    return pl.when(cond).then(a).otherwise(b)


_MANUAL_HOOK = None
_MANUAL_HOOK_LOADED = False
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")


def _load_manual_hook():
    global _MANUAL_HOOK, _MANUAL_HOOK_LOADED
    if _MANUAL_HOOK_LOADED:
        return _MANUAL_HOOK
    _MANUAL_HOOK_LOADED = True
    fp = PATHS.data_root / "reformatter" / "manual_functions.py"
    if not fp.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location("flow_reformatter_manual", str(fp))
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, "manual", None)
        if callable(fn):
            _MANUAL_HOOK = fn
            logger.info("Loaded reformatter manual hook from %s", fp)
    except Exception as e:
        logger.warning("Failed loading manual reformatter hook %s: %s", fp, e)
    return _MANUAL_HOOK


def _to_num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def _manual_default(*args):
    hook = _load_manual_hook()
    if hook is not None:
        return hook(*args)
    list_args = [a for a in args if isinstance(a, (list, tuple))]
    scalar_args = [a for a in args if not isinstance(a, (list, tuple))]
    numeric = [_to_num(v) for v in scalar_args]
    numeric = [v for v in numeric if v is not None]
    if not numeric:
        return None
    if len(list_args) > 0:
        coeffs = [_to_num(v) for v in list_args[0]]
        coeffs = [v for v in coeffs if v is not None]
    else:
        coeffs = []
    if len(numeric) >= 3:
        center = numeric[-2]
        scale = numeric[-1] if numeric[-1] not in (None, 0) else 1.0
        values = numeric[:-2]
    else:
        center = 0.0
        scale = 1.0
        values = numeric
    if not values:
        return None
    if not coeffs:
        coeffs = [1.0] * len(values)
    if len(coeffs) < len(values):
        coeffs = coeffs + [1.0] * (len(values) - len(coeffs))
    total = sum(v * coeffs[i] for i, v in enumerate(values))
    return (total - center) / (scale or 1.0)


def _f_manual(*args):
    if any(_expr_like(a) for a in args):
        raise ValueError("manual() is only supported in row-wise python_expr evaluation")
    return _manual_default(*args)


_SAFE_FUNCS = {
    "abs": _f_abs,
    "sqrt": _f_sqrt,
    "log": _f_log,
    "exp": _f_exp,
    "round": _f_round,
    "clip": _f_clip,
    "min": _f_min,
    "max": _f_max,
    "where": _f_where,
    "manual": _f_manual,
}


def _sanitize_expr_name(raw: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(raw or "").strip())
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        s = "X"
    if s[0].isdigit():
        s = "C_" + s
    return s


def _prepare_expr_inputs(df: pl.DataFrame, rule: Dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, str]]:
    expr_raw = str(rule.get("expr") or "").strip()
    env_expr: dict[str, Any] = {}
    row_sources: dict[str, str] = {}
    inputs = rule.get("inputs") or {}
    normalized_inputs: dict[str, str] = {}
    if isinstance(inputs, dict):
        for alias, col in inputs.items():
            a = str(alias or "").strip()
            c = str(col or "").strip()
            if a and c:
                normalized_inputs[a] = c
    placeholders = _PLACEHOLDER_RE.findall(expr_raw)
    placeholder_map: dict[str, str] = {}
    for raw_name in placeholders:
        base = _sanitize_expr_name(raw_name)
        token = base
        i = 2
        while token in placeholder_map.values() and placeholder_map.get(raw_name) != token:
            token = f"{base}_{i}"
            i += 1
        placeholder_map[raw_name] = token
        normalized_inputs.setdefault(token, raw_name)
    expr_norm = expr_raw
    for raw_name, token in placeholder_map.items():
        expr_norm = expr_norm.replace("{" + raw_name + "}", token)
    if normalized_inputs:
        for alias, col in normalized_inputs.items():
            row_sources[str(alias)] = str(col)
            env_expr[str(alias)] = _safe_float_col(df, str(col))
    else:
        for col in df.columns:
            if str(col).isidentifier():
                row_sources[str(col)] = str(col)
                env_expr[str(col)] = _safe_float_col(df, str(col))
    return expr_norm, env_expr, row_sources


def _validate_expr_ast(expr: str, names: set[str]) -> ast.AST:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"invalid expression syntax: {e}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ValueError(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Name):
            if node.id not in names and node.id not in _SAFE_FUNCS:
                raise ValueError(f"unknown variable/function: {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
                raise ValueError("only safe helper functions are allowed")
    return tree


def _eval_safe_expr(expr: str, env: dict[str, Any]):
    tree = _validate_expr_ast(expr, set(env.keys()))
    code = compile(tree, "<reformatter-expr>", "eval")
    return eval(code, {"__builtins__": {}}, {**_SAFE_FUNCS, **env})  # noqa: S307


def _apply_scale_abs(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    src = rule["source_col"]
    name = rule["name"]
    scale = float(rule.get("scale", 1.0))
    offset = float(rule.get("offset", 0.0))
    do_abs = bool(rule.get("abs", False))
    if src not in df.columns:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    expr = (pl.col(src).cast(pl.Float64, strict=False) * scale + offset)
    if do_abs:
        expr = expr.abs()
    # Optional filter — apply to rows, non-matching rows get NULL
    flt = rule.get("filter")
    if flt:
        try:
            mask = df.select(pl.sql_expr(flt)).to_series()
            return df.with_columns(
                pl.when(mask).then(expr).otherwise(pl.lit(None, dtype=pl.Float64)).alias(name)
            )
        except Exception as e:
            logger.warning(f"scale_abs filter error on {name}: {e}")
    return df.with_columns(expr.alias(name))


def _apply_chip_combo(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    cols = rule["source_cols"]
    agg = rule["agg"]
    name = rule["name"]
    present = [c for c in cols if c in df.columns]
    if not present:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    exprs = [pl.col(c).cast(pl.Float64, strict=False) for c in present]
    if agg == "mean":
        out = pl.sum_horizontal(exprs) / len(exprs)
    elif agg == "sum":
        out = pl.sum_horizontal(exprs)
    elif agg == "min":
        out = pl.min_horizontal(exprs)
    elif agg == "max":
        out = pl.max_horizontal(exprs)
    elif agg == "range":
        out = pl.max_horizontal(exprs) - pl.min_horizontal(exprs)
    elif agg == "median":
        # Approximate: average of all values (polars has no horizontal median)
        out = pl.sum_horizontal(exprs) / len(exprs)
    elif agg == "std":
        # Compute horizontal std manually: sqrt(mean(x^2) - mean(x)^2)
        mean = pl.sum_horizontal(exprs) / len(exprs)
        sq = pl.sum_horizontal([(e * e) for e in exprs]) / len(exprs)
        out = (sq - mean * mean).sqrt()
    else:
        out = pl.lit(None, dtype=pl.Float64)
    return df.with_columns(out.alias(name))


def _apply_shot_agg(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    src = rule["source_col"]
    name = rule["name"]
    agg = rule["agg"]
    grp = rule.get("group_by") or [c for c in ("LOT_WF", "SHOT_X", "SHOT_Y") if c in df.columns]
    if not grp or src not in df.columns:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    present_grp = [g for g in grp if g in df.columns]
    if not present_grp:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    s = pl.col(src).cast(pl.Float64, strict=False)
    if agg == "mean": expr = s.mean()
    elif agg == "sum": expr = s.sum()
    elif agg == "count": expr = s.count()
    elif agg == "min": expr = s.min()
    elif agg == "max": expr = s.max()
    elif agg == "median": expr = s.median()
    elif agg == "std": expr = s.std()
    elif agg == "range": expr = s.max() - s.min()
    else: expr = s.mean()
    return df.with_columns(expr.over(present_grp).alias(name))


def _apply_step_skew(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    src = rule["source_col"]
    step = rule["step_col"]
    name = rule["name"]
    baseline = str(rule.get("baseline_step", ""))
    grp = rule.get("group_by") or [c for c in ("LOT_WF", "SHOT_X", "SHOT_Y") if c in df.columns]
    present_grp = [g for g in grp if g in df.columns]
    if not present_grp or src not in df.columns or step not in df.columns:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    # Per group, find baseline value = row where step matches, then compute delta for every row
    baseline_val = (
        pl.when(pl.col(step).cast(pl.Utf8, strict=False) == baseline)
          .then(pl.col(src).cast(pl.Float64, strict=False))
          .otherwise(None)
    ).first().over(present_grp)
    return df.with_columns(
        (pl.col(src).cast(pl.Float64, strict=False) - baseline_val).alias(name)
    )


def _apply_poly2_window(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    """Fit y = a*x^2 + b*x + c per group; find window where y crosses USL/LSL.

    Outputs three columns: <name>_LO, <name>_HI, <name>_WIDTH (per row, copied across group).
    For groups with <3 points or no real crossing, outputs NULL.
    """
    x_col = rule["x_col"]; y_col = rule["y_col"]
    usl = float(rule["usl"]); lsl = float(rule["lsl"])
    base = rule["name"]
    grp = rule.get("group_by") or [c for c in ("LOT_WF", "SHOT_X", "SHOT_Y") if c in df.columns]
    present_grp = [g for g in grp if g in df.columns]
    if x_col not in df.columns or y_col not in df.columns or not present_grp:
        return df.with_columns([
            pl.lit(None, dtype=pl.Float64).alias(base + "_LO"),
            pl.lit(None, dtype=pl.Float64).alias(base + "_HI"),
            pl.lit(None, dtype=pl.Float64).alias(base + "_WIDTH"),
        ])

    # Iterate groups in Python — polars has no built-in polyfit
    out_lo, out_hi, out_width = {}, {}, {}
    agg_df = df.group_by(present_grp).agg([
        pl.col(x_col).cast(pl.Float64, strict=False).alias("_x"),
        pl.col(y_col).cast(pl.Float64, strict=False).alias("_y"),
    ])
    for row in agg_df.iter_rows(named=True):
        key = tuple(row[g] for g in present_grp)
        xs = row["_x"]; ys = row["_y"]
        if not xs or not ys or len(xs) < 3:
            out_lo[key] = None; out_hi[key] = None; out_width[key] = None
            continue
        try:
            # Fit ax^2 + bx + c via normal equations (no numpy needed)
            n = len(xs)
            sx = sum(xs); sx2 = sum(x*x for x in xs); sx3 = sum(x**3 for x in xs); sx4 = sum(x**4 for x in xs)
            sy = sum(ys); sxy = sum(x*y for x, y in zip(xs, ys)); sx2y = sum(x*x*y for x, y in zip(xs, ys))
            # Solve 3x3 system:
            # [ sx4 sx3 sx2 ] [a]   [sx2y]
            # [ sx3 sx2 sx  ] [b] = [sxy ]
            # [ sx2 sx  n   ] [c]   [sy  ]
            m = [[sx4, sx3, sx2, sx2y], [sx3, sx2, sx, sxy], [sx2, sx, n, sy]]
            # Gauss-Jordan
            for i in range(3):
                # Partial pivot
                piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
                m[i], m[piv] = m[piv], m[i]
                if abs(m[i][i]) < 1e-12: raise ValueError("singular")
                fac = m[i][i]
                for j in range(4): m[i][j] /= fac
                for k in range(3):
                    if k != i:
                        f = m[k][i]
                        for j in range(4): m[k][j] -= f * m[i][j]
            a, b, c = m[0][3], m[1][3], m[2][3]
            # y = USL crossings: a*x^2 + b*x + (c - USL) = 0
            def roots(target):
                A, B, C = a, b, c - target
                if abs(A) < 1e-12:
                    if abs(B) < 1e-12: return []
                    return [-C / B]
                disc = B * B - 4 * A * C
                if disc < 0: return []
                s = disc ** 0.5
                return [(-B - s) / (2 * A), (-B + s) / (2 * A)]
            r_usl = roots(usl); r_lsl = roots(lsl)
            crossings = sorted(r_usl + r_lsl)
            if len(crossings) >= 2:
                lo = crossings[0]; hi = crossings[-1]
                out_lo[key] = lo; out_hi[key] = hi; out_width[key] = hi - lo
            else:
                out_lo[key] = None; out_hi[key] = None; out_width[key] = None
        except Exception:
            out_lo[key] = None; out_hi[key] = None; out_width[key] = None

    def lookup(mapping):
        return [mapping.get(tuple(row[g] for g in present_grp)) for row in df.iter_rows(named=True)]

    return df.with_columns([
        pl.Series(base + "_LO", lookup(out_lo), dtype=pl.Float64),
        pl.Series(base + "_HI", lookup(out_hi), dtype=pl.Float64),
        pl.Series(base + "_WIDTH", lookup(out_width), dtype=pl.Float64),
    ])


def _apply_bucket(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    src = rule["source_col"]
    name = rule["name"]
    edges = rule["edges"]
    labels = rule.get("labels") or [f"B{i}" for i in range(len(edges) - 1)]
    if src not in df.columns or len(edges) < 2:
        return df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(name))
    v = pl.col(src).cast(pl.Float64, strict=False)
    expr = pl.lit(None, dtype=pl.Utf8)
    for i in range(len(edges) - 1):
        lo, hi = float(edges[i]), float(edges[i + 1])
        label = labels[i] if i < len(labels) else f"B{i}"
        cond = (v >= lo) & (v < hi) if i < len(edges) - 2 else (v >= lo) & (v <= hi)
        expr = pl.when(cond).then(pl.lit(label)).otherwise(expr)
    return df.with_columns(expr.alias(name))


def _apply_python_expr(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    name = rule["name"]
    expr_raw, env, row_sources = _prepare_expr_inputs(df, rule)
    if "manual(" in expr_raw:
        needed_cols = []
        for col in row_sources.values():
            if col in df.columns and col not in needed_cols:
                needed_cols.append(col)
        if not needed_cols:
            return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
        def _row_eval(row):
            local_env = {alias: row.get(col) for alias, col in row_sources.items()}
            try:
                return _eval_safe_expr(expr_raw, local_env)
            except Exception:
                return None
        return df.with_columns(
            pl.struct(needed_cols).map_elements(_row_eval, return_dtype=pl.Float64).alias(name)
        )
    out = _eval_safe_expr(expr_raw, env)
    if _expr_like(out):
        return df.with_columns(out.alias(name))
    return df.with_columns(pl.lit(out, dtype=pl.Float64).alias(name))


def _apply_shot_formula(df: pl.DataFrame, rule: Dict[str, Any]) -> pl.DataFrame:
    name = rule["name"]
    expr_raw = str(rule.get("expr") or "").strip()
    item_col = str(rule.get("item_col") or "item_id")
    value_col = str(rule.get("value_col") or "value")
    agg = str(rule.get("agg") or "mean").strip().lower()
    group_by = rule.get("group_by") or SHOT_FORMULA_GROUP_BY
    group_by = [g for g in group_by if g in df.columns]
    if item_col not in df.columns or value_col not in df.columns or not group_by:
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))

    work = df
    flt = str(rule.get("filter") or "").strip()
    if flt:
        try:
            work = work.filter(pl.sql_expr(flt))
        except Exception as e:
            logger.warning(f"shot_formula filter error on {name}: {e}")
            return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    if work.is_empty():
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))

    series = pl.col(value_col).cast(pl.Float64, strict=False)
    if agg == "mean":
        agg_expr = series.mean()
    elif agg == "sum":
        agg_expr = series.sum()
    elif agg == "min":
        agg_expr = series.min()
    elif agg == "max":
        agg_expr = series.max()
    elif agg == "median":
        agg_expr = series.median()
    elif agg == "std":
        agg_expr = series.std()
    elif agg == "range":
        agg_expr = series.max() - series.min()
    elif agg == "count":
        agg_expr = series.count()
    else:
        agg_expr = series.mean()

    grouped = (
        work.group_by(group_by + [item_col])
        .agg(agg_expr.alias("_v"))
        .pivot(index=group_by, on=item_col, values="_v", aggregate_function="first")
    )
    env: dict[str, Any] = {}
    item_map = rule.get("item_map") or {}
    if isinstance(item_map, dict) and item_map:
        for alias, item_name in item_map.items():
            col_name = str(item_name)
            env[str(alias)] = (
                pl.col(col_name).cast(pl.Float64, strict=False)
                if col_name in grouped.columns else pl.lit(None, dtype=pl.Float64)
            )
    else:
        for col in grouped.columns:
            if col in group_by:
                continue
            if str(col).isidentifier():
                env[str(col)] = pl.col(col).cast(pl.Float64, strict=False)
    try:
        derived = _eval_safe_expr(expr_raw, env)
    except Exception as e:
        logger.warning(f"shot_formula expr error on {name}: {e}")
        return df.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    grouped = grouped.with_columns(
        (derived if _expr_like(derived) else pl.lit(derived, dtype=pl.Float64)).alias(name)
    ).select(group_by + [name])
    return df.join(grouped, on=group_by, how="left")


_APPLIERS = {
    "scale_abs":    _apply_scale_abs,
    "chip_combo":   _apply_chip_combo,
    "shot_agg":     _apply_shot_agg,
    "step_skew":    _apply_step_skew,
    "poly2_window": _apply_poly2_window,
    "bucket":       _apply_bucket,
    "python_expr":  _apply_python_expr,
    "shot_formula": _apply_shot_formula,
}


def apply_rules(df: pl.DataFrame, rules: List[Dict[str, Any]], enabled_only: bool = True) -> pl.DataFrame:
    """Apply rules in order. Each rule can reference columns produced by previous rules.

    Errors in one rule don't abort others — bad output becomes NULL column.
    """
    for rule in rules:
        if enabled_only and rule.get("disabled"):
            continue
        errs = validate_rule(rule)
        if errs:
            logger.warning(f"Skipping invalid rule '{rule.get('name','?')}': {errs}")
            # Create placeholder null column so downstream doesn't break
            df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(rule.get("name", "_INVALID")))
            continue
        fn = _APPLIERS.get(rule["type"])
        if not fn:
            continue
        try:
            df = fn(df, rule)
        except Exception as e:
            logger.warning(f"Rule '{rule.get('name')}' failed: {e}")
            df = df.with_columns(pl.lit(None, dtype=pl.Float64).alias(rule.get("name", "_ERR")))
    return df
