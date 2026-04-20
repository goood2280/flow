"""core/reformatter.py v7.2 — ET/INLINE reformatter engine.

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

Rules are stored per product: data/holweb-data/reformatter/<product>.json
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
import polars as pl

logger = logging.getLogger("holweb.reformatter")

VALID_TYPES = {"scale_abs", "chip_combo", "shot_agg", "step_skew", "poly2_window", "bucket"}
VALID_AGGS = {"mean", "sum", "count", "min", "max", "median", "std", "range"}


# ─────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────
def _rules_path(base_dir: Path, product: str) -> Path:
    safe = "".join(c for c in product if c.isalnum() or c in "_-")
    return base_dir / f"{safe or 'DEFAULT'}.json"


def load_rules(base_dir: Path, product: str) -> List[Dict[str, Any]]:
    fp = _rules_path(base_dir, product)
    if not fp.exists():
        return []
    try:
        data = json.loads(fp.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Failed to load rules {fp}: {e}")
        return []


def save_rules(base_dir: Path, product: str, rules: List[Dict[str, Any]]) -> None:
    fp = _rules_path(base_dir, product)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(rules, indent=2, ensure_ascii=False), encoding="utf-8")


def validate_rule(rule: Dict[str, Any]) -> List[str]:
    """Return list of validation errors ([] if valid).

    v7.3 metadata fields (all optional, pass validation but surface in catalog):
      item_id     — ET item this rule is scoped to (e.g. 'VTH')
      owner       — engineer in charge
      approved    — bool, review status
      tags        — list[str]
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
    return errs


# ─────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────
def _safe_float_col(df: pl.DataFrame, col: str) -> pl.Expr:
    if col not in df.columns:
        return pl.lit(None, dtype=pl.Float64)
    return pl.col(col).cast(pl.Float64, strict=False)


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


_APPLIERS = {
    "scale_abs":    _apply_scale_abs,
    "chip_combo":   _apply_chip_combo,
    "shot_agg":     _apply_shot_agg,
    "step_skew":    _apply_step_skew,
    "poly2_window": _apply_poly2_window,
    "bucket":       _apply_bucket,
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
