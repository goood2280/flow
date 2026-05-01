from __future__ import annotations

import datetime as dt
import math
import re
import statistics
import uuid
from pathlib import Path
from typing import Any

import polars as pl

from core.paths import PATHS
from core.utils import _STR, load_json, save_json, serialize_rows


CHART_DEFAULTS_FILE = PATHS.data_root / "dashboard_chart_defaults.json"
CHART_SESSION_DIR = PATHS.data_root / "dashboard_chart_sessions"
CHART_SESSION_TTL_SECONDS = 3600

DASHBOARD_SOURCE_GROUPS = ["FAB", "ET", "INLINE", "VM", "EDS", "KNOB", "MASK", "SPC"]
DASHBOARD_CHART_TYPES = [
    "scatter",
    "boxplot",
    "trend",
    "correlation_matrix",
    "wafer_map",
    "classification",
    "stacked_bar",
]

DEFAULT_CHART_DEFAULTS: dict[str, dict[str, Any]] = {
    "scatter": {"x": "$item1", "y": "$item2", "color": "lot_id", "agg": "raw"},
    "boxplot": {"x": "step_id", "y": "$item1", "color": "product"},
    "trend": {"x": "tkout_time", "y": "$item1", "group": "lot_id", "agg": "median"},
    "correlation_matrix": {"items": "$selected", "method": "pearson"},
    "wafer_map": {"value": "$item1", "agg": "median"},
    "classification": {"x": "step_id", "y": "$item1", "group": "product"},
    "stacked_bar": {"x": "step_id", "y": "count", "group": "defect_type"},
}

FALLBACK_ITEMS: dict[str, list[str]] = {
    "FAB": ["step_id", "eqp_id", "eqp_chamber", "tkout_time", "lot_id", "root_lot_id"],
    "ET": ["LKG", "VTH", "DIBL", "SS", "ION", "IOFF", "IGATE", "RSD"],
    "INLINE": ["CD", "GATE_CD", "CA_CD", "THK", "OVERLAY", "NS_WIDTH", "NS_THK"],
    "VM": ["SRAM_VMIN", "VMIN", "FAIL_COUNT"],
    "EDS": ["YIELD", "BIN", "DEFECT_COUNT"],
    "KNOB": ["STI", "PC", "SORT", "RECIPE", "TOOL"],
    "MASK": ["MASK", "RETICLE", "MASK_SET"],
    "SPC": ["count", "median", "avg", "std", "q1", "q3"],
}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _merge_nested(base: dict[str, Any], override: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (base or {}).items():
        out[key] = _merge_nested(value, {}) if isinstance(value, dict) else value
    if not isinstance(override, dict):
        return out
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = _merge_nested(out[key], value)
        else:
            out[key] = value
    return out


def load_chart_defaults(path: Path | None = None) -> dict[str, dict[str, Any]]:
    raw = load_json(path or CHART_DEFAULTS_FILE, {})
    merged = _merge_nested(DEFAULT_CHART_DEFAULTS, raw)
    return {k: dict(merged.get(k) or {}) for k in DASHBOARD_CHART_TYPES}


def save_chart_default(chart_type: str, config: dict[str, Any], path: Path | None = None) -> dict[str, Any]:
    ct = str(chart_type or "").strip()
    if ct not in DASHBOARD_CHART_TYPES:
        raise ValueError(f"unsupported chart_type: {chart_type}")
    if not isinstance(config, dict):
        raise ValueError("config must be an object")
    data = load_chart_defaults(path)
    data[ct] = dict(config)
    save_json(path or CHART_DEFAULTS_FILE, data, indent=2)
    return data[ct]


def apply_chart_defaults(
    chart_type: str,
    selected_items: list[Any] | None = None,
    overrides: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ct = str(chart_type or "scatter").strip() or "scatter"
    all_defaults = defaults if isinstance(defaults, dict) else load_chart_defaults()
    cfg = dict((all_defaults.get(ct) if isinstance(all_defaults.get(ct), dict) else {}) or {})
    if not cfg:
        cfg = dict(DEFAULT_CHART_DEFAULTS.get(ct) or {})
    items = [item_key(v) for v in (selected_items or []) if item_key(v)]

    def repl(value: Any) -> Any:
        if value == "$item1":
            return items[0] if items else ""
        if value == "$item2":
            return items[1] if len(items) > 1 else (items[0] if items else "")
        if value == "$selected":
            return items
        if isinstance(value, list):
            return [repl(v) for v in value]
        if isinstance(value, dict):
            return {k: repl(v) for k, v in value.items()}
        return value

    out = {k: repl(v) for k, v in cfg.items()}
    if overrides:
        out.update({k: v for k, v in overrides.items() if v is not None})
    out["chart_type"] = ct
    return out


def item_key(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("key") or item.get("canonical_item_id") or item.get("item_id") or item.get("label") or "").strip()
    return str(item or "").strip()


def _default_chart_type_for_group(group: str) -> str:
    g = _upper(group)
    if g in {"ET", "INLINE"}:
        return "scatter"
    if g == "FAB":
        return "trend"
    if g in {"KNOB", "MASK", "EDS"}:
        return "stacked_bar"
    if g == "VM":
        return "boxplot"
    if g == "SPC":
        return "trend"
    return "scatter"


def _add_item(out: list[dict[str, Any]], seen: set[str], key: str, label: str = "", source_type: str = "", default_chart_type: str = "") -> None:
    k = str(key or "").strip()
    if not k:
        return
    dedupe = f"{_upper(source_type)}:{_upper(k)}"
    if dedupe in seen:
        return
    seen.add(dedupe)
    source = _upper(source_type) or "INLINE"
    out.append({
        "key": k,
        "label": str(label or k),
        "source_type": source,
        "default_chart_type": default_chart_type or _default_chart_type_for_group(source),
    })


def dashboard_items(group: str = "", product: str = "", limit: int = 2000) -> list[dict[str, Any]]:
    group_u = _upper(group) or "ET"
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        from core import semiconductor_knowledge as semi_knowledge

        for item in getattr(semi_knowledge, "ITEM_MASTER", []) or []:
            if not isinstance(item, dict):
                continue
            source = _upper(item.get("source_type")) or "INLINE"
            if source != group_u:
                continue
            key = str(item.get("canonical_item_id") or "").strip()
            names = [key, *(item.get("raw_names") or [])]
            _add_item(out, seen, key, item.get("display_name") or key, source, _default_chart_type_for_group(source))
            for raw in names[1:4]:
                _add_item(out, seen, raw, f"{raw} ({key})", source, _default_chart_type_for_group(source))
    except Exception:
        pass

    try:
        from core import product_config

        cfg = product_config.load(PATHS.data_root, product) if product else {}
        if group_u == "INLINE":
            for item in cfg.get("canonical_inline_items") or []:
                _add_item(out, seen, str(item), str(item), "INLINE", "scatter")
        elif group_u == "ET":
            for item in cfg.get("et_key_items") or []:
                _add_item(out, seen, str(item), str(item), "ET", "scatter")
            perf = cfg.get("perf_metric")
            if perf:
                _add_item(out, seen, str(perf), str(perf), "ET", "scatter")
        elif group_u == "KNOB":
            for item in cfg.get("canonical_knobs") or []:
                _add_item(out, seen, str(item).replace("KNOB_", ""), str(item), "KNOB", "stacked_bar")
        elif group_u == "SPC":
            for item in (cfg.get("target_spec") or {}).keys():
                _add_item(out, seen, str(item), str(item), "SPC", "trend")
    except Exception:
        pass

    for item in FALLBACK_ITEMS.get(group_u, []):
        _add_item(out, seen, item, item, group_u, _default_chart_type_for_group(group_u))

    out.sort(key=lambda r: (str(r.get("source_type") or ""), str(r.get("label") or "").casefold()))
    try:
        cap = max(1, min(int(limit or 2000), 5000))
    except Exception:
        cap = 2000
    return out[:cap]


def infer_chart_type(prompt: str = "", selected_items: list[str] | None = None) -> str:
    text = str(prompt or "")
    low = text.lower()
    selected_count = len([x for x in (selected_items or []) if x])
    if any(t in low or t in text for t in ("wafer map", "wf map", "웨이퍼맵", "2d")):
        return "wafer_map"
    if any(t in low or t in text for t in ("boxplot", "box plot", "박스", "분포")):
        return "boxplot"
    if any(t in low or t in text for t in ("trend", "추세", "시계열")):
        return "trend"
    if any(t in low or t in text for t in ("classification", "분류", "step별")):
        return "classification"
    explicit_matrix = any(t in low or t in text for t in ("correlation matrix", "corr matrix", "상관행렬"))
    explicit_scatter = any(t in low or t in text for t in ("scatter", "산점도"))
    if explicit_matrix:
        return "correlation_matrix"
    if explicit_scatter:
        return "scatter"
    if any(t in low or t in text for t in ("correlation", "corr", "상관")) and selected_count >= 3:
        return "correlation_matrix"
    if any(t in low or t in text for t in ("correlation", "corr", "상관")):
        return "scatter"
    if selected_count >= 3:
        return "correlation_matrix"
    return "scatter"


def parse_color_by(prompt: str = "") -> str:
    text = str(prompt or "")
    low = text.lower()
    func_step = _func_step_token(text)
    if re.search(r"\blot\b", low) and any(t in text for t in ("별", "색")):
        return "lot"
    if re.search(r"\bwafer\b", low) and any(t in text for t in ("별", "색")):
        return "wafer"
    m = re.search(r"(?:KNOB|knob|노브)\s*([A-Za-z0-9_.-]{1,40})", text)
    if not m:
        m = re.search(r"\b([A-Za-z0-9_.-]{1,40})\s*(?:KNOB|knob|노브)\s*(?:별|에 따라|컬러|색)", text)
    if m and any(t in text for t in ("별", "컬러", "색", "따라")):
        return f"ml_knob:{m.group(1).strip()}"
    m = re.search(r"(?:MASK|mask)\s*([A-Za-z0-9_.-]{1,40})", text)
    if not m:
        m = re.search(r"\b([A-Za-z0-9_.-]{1,40})\s*(?:MASK|mask)\s*(?:별|컬러|색)", text)
    if m and any(t in text for t in ("별", "컬러", "색", "따라")):
        return f"ml_mask:{m.group(1).strip()}"
    if "eqp_chamber" in low and any(t in text for t in ("컬러", "색")):
        return f"fab_step_eqp_chamber:{func_step}" if func_step else "fab_step_eqp_chamber:"
    if re.search(r"\beqp\b", low) and any(t in text for t in ("컬러", "색", "color")):
        return f"fab_step_eqp:{func_step}" if func_step else "fab_step_eqp:"
    return "none"


def parse_group_by(prompt: str = "") -> str:
    text = str(prompt or "")
    low = text.lower()
    if not any(t in text for t in ("별로", "분리", "나눠")):
        return ""
    func_step = _func_step_token(text)
    if "eqp_chamber" in low or "chamber" in low or "챔버" in text:
        return f"eqp_chamber:{func_step}" if func_step else "eqp_chamber:"
    if re.search(r"\beqp\b", low) or "장비" in text:
        return f"eqp:{func_step}" if func_step else "eqp:"
    if "wafer" in low:
        return "wafer"
    if "lot" in low:
        return "lot"
    if "product" in low:
        return "product"
    m = re.search(r"(?:KNOB|knob|노브)\s*([A-Za-z0-9_.-]{1,40})", text)
    if not m:
        m = re.search(r"\b([A-Za-z0-9_.-]{1,40})\s*(?:KNOB|knob|노브)", text)
    if m:
        return f"knob:{m.group(1).strip()}"
    m = re.search(r"(?:MASK|mask)\s*([A-Za-z0-9_.-]{1,40})", text)
    if not m:
        m = re.search(r"\b([A-Za-z0-9_.-]{1,40})\s*(?:MASK|mask)", text)
    if m:
        return f"mask:{m.group(1).strip()}"
    return ""


def parse_fit(prompt: str = "") -> str:
    text = str(prompt or "")
    low = text.lower()
    if any(t in low or t in text for t in ("2차", "quadratic")):
        return "quadratic"
    if any(t in low or t in text for t in ("1차", "fitting", "linear", "r2", "r²", "피팅", "선형")):
        return "linear"
    return "none"


def parse_stats_columns(prompt: str = "") -> list[str] | None:
    text = str(prompt or "")
    low = text.lower()
    if any(t in text for t in ("통계표 빼", "통계표 제외")):
        return None
    allowed = ["count", "median", "avg", "min", "max", "std", "q1", "q3"]
    hits = [c for c in allowed if re.search(rf"(?<![a-z0-9_]){c}(?![a-z0-9_])", low)]
    if "평균" in text and "avg" not in hits:
        hits.append("avg")
    if "중앙" in text and "median" not in hits:
        hits.append("median")
    if hits and any(t in text for t in ("만", "only")):
        return hits
    return ["count", "median", "avg", "std"]


def _func_step_token(text: str) -> str:
    m = re.search(r"\b(\d+(?:\.\d+)?\s+[A-Z][A-Z0-9_./-]{1,30})\b", str(text or ""), flags=re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1).strip()).upper()
    m = re.search(r"\b([A-Z]{2}\d{6})\b", str(text or ""), flags=re.I)
    return m.group(1).upper() if m else ""


def _source_files(source: str, product: str = "") -> list[Path]:
    source_u = _upper(source)
    if source_u in {"KNOB", "MASK", "ML", "ML_TABLE"}:
        source_u = "ML_TABLE"
    root = PATHS.db_root
    if not root.exists():
        return []
    files = [p for p in root.rglob("*.parquet") if source_u in _upper("/".join(p.parts[-6:]))]
    if source_u == "ML_TABLE":
        files.extend([p for p in root.rglob("ML_TABLE*.parquet")])
    product_u = _upper(product)
    if product_u:
        filtered = [p for p in files if product_u in _upper("/".join(p.parts[-6:])) or product_u.replace("PRODUCT_", "PROD") in _upper(p.name)]
        if filtered:
            files = filtered
    return sorted(set(files))


def _scan_source(source: str, product: str = "") -> pl.LazyFrame | None:
    files = _source_files(source, product)
    if not files:
        return None
    return pl.scan_parquet([str(p) for p in files])


def _ci_col(cols: list[str], *names: str) -> str:
    lookup = {_upper(c): c for c in cols}
    for name in names:
        if _upper(name) in lookup:
            return lookup[_upper(name)]
    return ""


def _root_expr(col: str) -> pl.Expr:
    return pl.col(col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase()


def _wafer_expr(col: str) -> pl.Expr:
    return (
        pl.col(col)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .str.replace(r"^(WAFER|WF|W)", "")
        .cast(pl.Int64, strict=False)
        .cast(_STR, strict=False)
    )


def _item_terms(item: str) -> list[str]:
    raw = _upper(item)
    parts = [raw]
    if raw == "LKG":
        parts.extend(["LEAK", "LEAKAGE", "IGATE", "IOFF"])
    if raw == "CD":
        parts.extend(["CD_MEAN", "GATE_CD", "CRITICAL_DIMENSION"])
    if raw == "THK":
        parts.extend(["THICKNESS", "OX_THK", "NS_THK"])
    return [p for p in dict.fromkeys(parts) if p]


def _metric_lf(source: str, product: str, item: str, value_alias: str, root_lot_ids: list[str] | None = None) -> dict[str, Any]:
    lf = _scan_source(source, product)
    if lf is None:
        return {"ok": False, "error": f"{source} parquet not found", "source": source, "item": item}
    cols = lf.collect_schema().names()
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID", "lot_id", "LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    lot_wf_col = _ci_col(cols, "lot_wf", "LOT_WF")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    value_col = _ci_col(cols, "value", "VALUE", "_value", "val", "VAL")
    direct_col = _ci_col(cols, item)
    if not value_col and direct_col:
        value_col = direct_col
    if not value_col:
        return {"ok": False, "error": f"{source} value column not found", "columns": cols[:80]}
    if not lot_wf_col and not (root_col and wafer_col):
        return {"ok": False, "error": f"{source} join keys not found", "columns": cols[:80]}

    filters: list[pl.Expr] = []
    if product_col and product:
        aliases = {_upper(product), _upper(product).replace("PRODUCT_A", "PRODA").replace("PRODUCT_B", "PRODB")}
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    roots = [_upper(v) for v in (root_lot_ids or []) if _upper(v)]
    if roots and root_col:
        filters.append(_root_expr(root_col).is_in(roots))
    item_matches: list[str] = []
    if item_col:
        try:
            vals = (
                lf.select(pl.col(item_col).cast(_STR, strict=False).alias("_item"))
                .drop_nulls()
                .unique()
                .limit(2500)
                .collect()
                .get_column("_item")
                .to_list()
            )
        except Exception:
            vals = []
        terms = _item_terms(item)
        for val in vals:
            vu = _upper(val)
            if any(t and (vu == t or t in vu or vu in t) for t in terms):
                item_matches.append(str(val))
        if not item_matches and item:
            return {"ok": False, "error": f"{source} item {item} not found", "source": source, "item": item, "item_candidates": vals[:24]}
        filters.append(pl.col(item_col).cast(_STR, strict=False).is_in(item_matches))
    for expr in filters:
        lf = lf.filter(expr)

    exprs: list[pl.Expr] = []
    group_cols: list[str] = []
    if root_col:
        exprs.append(_root_expr(root_col).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    else:
        exprs.append(pl.lit("").alias("root_lot_id"))
    if fab_col:
        exprs.append(pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id"))
        group_cols.append("fab_lot_id")
    else:
        exprs.append(pl.lit("").alias("fab_lot_id"))
    if wafer_col:
        exprs.append(_wafer_expr(wafer_col).alias("wafer_id"))
        group_cols.append("wafer_id")
    else:
        exprs.append(pl.lit("").alias("wafer_id"))
    if lot_wf_col:
        exprs.append(pl.col(lot_wf_col).cast(_STR, strict=False).alias("lot_wf"))
    elif root_col and wafer_col:
        exprs.append((_root_expr(root_col) + pl.lit("_") + _wafer_expr(wafer_col)).alias("lot_wf"))
    else:
        exprs.append(pl.lit("").alias("lot_wf"))
    if "lot_wf" not in group_cols:
        group_cols.append("lot_wf")
    if product_col:
        exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
    else:
        exprs.append(pl.lit(product or "").alias("product"))
    exprs.append(pl.col(value_col).cast(pl.Float64, strict=False).alias("_metric_value"))
    scoped = lf.select(exprs).drop_nulls(subset=["_metric_value"])
    grouped = scoped.group_by(group_cols).agg([
        pl.col("_metric_value").median().alias(value_alias),
        pl.len().alias(f"{value_alias}_n"),
        pl.col("product").drop_nulls().first().alias("product"),
    ])
    return {
        "ok": True,
        "lf": grouped,
        "source": _upper(source),
        "item": item,
        "item_matches": item_matches,
        "join_cols": group_cols,
    }


def _find_metric(source_order: list[str], product: str, item: str, alias: str, root_lot_ids: list[str]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for source in source_order:
        out = _metric_lf(source, product, item, alias, root_lot_ids)
        if out.get("ok"):
            return out
        failures.append(out)
    return {"ok": False, "error": f"metric {item} not found", "failures": failures}


def _join_cols(left: list[str], right: list[str]) -> list[str]:
    lset = set(left)
    rset = set(right)
    for cand in (["root_lot_id", "fab_lot_id", "wafer_id"], ["root_lot_id", "wafer_id"], ["lot_wf"], ["root_lot_id"]):
        if set(cand).issubset(lset) and set(cand).issubset(rset):
            return cand
    return ["lot_wf"]


def _attach_fab_context(df: pl.DataFrame, product: str, step_id: str = "") -> pl.DataFrame:
    lf = _scan_source("FAB", product)
    if lf is None or df.is_empty():
        return df
    cols = lf.collect_schema().names()
    product_col = _ci_col(cols, "product", "PRODUCT")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    fab_col = _ci_col(cols, "fab_lot_id", "FAB_LOT_ID", "lot_id", "LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "func_step", "FUNC_STEP")
    eqp_col = _ci_col(cols, "eqp_id", "EQP_ID", "eqp", "EQP", "equipment_id")
    chamber_col = _ci_col(cols, "eqp_chamber", "EQP_CHAMBER", "chamber_id", "CHAMBER_ID", "chamber")
    time_col = _ci_col(cols, "tkout_time", "TKOUT_TIME", "time", "TIME", "timestamp")
    if not root_col and not fab_col:
        return df
    filters: list[pl.Expr] = []
    if product_col and product:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase().str.contains(_upper(product), literal=True))
    if step_id and step_col:
        filters.append(pl.col(step_col).cast(_STR, strict=False).str.to_uppercase().str.contains(_upper(step_id), literal=True))
    for expr in filters:
        lf = lf.filter(expr)
    exprs = []
    group_cols = []
    if root_col:
        exprs.append(_root_expr(root_col).alias("root_lot_id"))
        group_cols.append("root_lot_id")
    if fab_col:
        exprs.append(pl.col(fab_col).cast(_STR, strict=False).alias("fab_lot_id"))
        group_cols.append("fab_lot_id")
    if wafer_col:
        exprs.append(_wafer_expr(wafer_col).alias("wafer_id"))
        if "wafer_id" in df.columns:
            group_cols.append("wafer_id")
    exprs.append(pl.col(eqp_col).cast(_STR, strict=False).alias("eqp") if eqp_col else pl.lit("").alias("eqp"))
    exprs.append(pl.col(chamber_col).cast(_STR, strict=False).alias("eqp_chamber") if chamber_col else pl.lit("").alias("eqp_chamber"))
    exprs.append(pl.col(time_col).cast(_STR, strict=False).alias("_fab_time") if time_col else pl.lit("").alias("_fab_time"))
    try:
        ctx = (
            lf.select(exprs)
            .sort("_fab_time")
            .group_by(group_cols)
            .agg([
                pl.col("eqp").drop_nulls().last().alias("eqp"),
                pl.col("eqp_chamber").drop_nulls().last().alias("eqp_chamber"),
            ])
            .collect()
        )
    except Exception:
        return df
    on = [c for c in _join_cols(list(df.columns), group_cols) if c in ctx.columns and c in df.columns]
    if not on:
        return df
    return df.join(ctx, on=on, how="left")


def _attach_ml_context(df: pl.DataFrame, product: str, kind: str, name: str) -> pl.DataFrame:
    lf = _scan_source("ML_TABLE", product)
    if lf is None or df.is_empty():
        return df
    cols = lf.collect_schema().names()
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID")
    prefix = "MASK" if _upper(kind) == "MASK" else "KNOB"
    name_u = _upper(name)
    candidates = [c for c in cols if _upper(c).startswith(prefix) and (not name_u or name_u in _upper(c))]
    if not candidates or not root_col:
        return df
    col = candidates[0]
    exprs = [_root_expr(root_col).alias("root_lot_id")]
    group_cols = ["root_lot_id"]
    if wafer_col:
        exprs.append(_wafer_expr(wafer_col).alias("wafer_id"))
        if "wafer_id" in df.columns:
            group_cols.append("wafer_id")
    alias = f"{prefix.lower()}_{name or col}".replace(" ", "_")
    exprs.append(pl.col(col).cast(_STR, strict=False).alias(alias))
    try:
        ctx = lf.select(exprs).group_by(group_cols).agg(pl.col(alias).drop_nulls().first().alias(alias)).collect()
    except Exception:
        return df
    on = [c for c in group_cols if c in df.columns and c in ctx.columns]
    return df.join(ctx, on=on, how="left") if on else df


def _normalize_color_group_column(df: pl.DataFrame, color_by: str = "", group_by: str = "", product: str = "") -> tuple[pl.DataFrame, str, str]:
    color_col = ""
    group_col = ""
    for raw in [color_by or "", group_by or ""]:
        val = str(raw or "")
        low = val.lower()
        if low.startswith("fab_step_eqp_chamber:"):
            df = _attach_fab_context(df, product, val.split(":", 1)[1])
        elif low.startswith("fab_step_eqp:"):
            df = _attach_fab_context(df, product, val.split(":", 1)[1])
        elif low.startswith("eqp_chamber:") or low.startswith("eqp:"):
            df = _attach_fab_context(df, product, val.split(":", 1)[1])
        elif low.startswith("ml_knob:") or low.startswith("knob:"):
            df = _attach_ml_context(df, product, "KNOB", val.split(":", 1)[1])
        elif low.startswith("ml_mask:") or low.startswith("mask:"):
            df = _attach_ml_context(df, product, "MASK", val.split(":", 1)[1])
    def pick(raw: str) -> str:
        val = str(raw or "")
        low = val.lower()
        if low in {"lot", "root_lot_id"}:
            return "root_lot_id"
        if low in {"wafer", "wafer_id"}:
            return "wafer_id"
        if low == "product":
            return "product"
        if low.startswith(("fab_step_eqp_chamber:", "eqp_chamber:")):
            return "eqp_chamber"
        if low.startswith(("fab_step_eqp:", "eqp:")):
            return "eqp"
        if low.startswith(("ml_knob:", "knob:")):
            suffix = val.split(":", 1)[1].replace(" ", "_")
            matches = [c for c in df.columns if c.lower().startswith("knob_") and (not suffix or suffix.lower() in c.lower())]
            return matches[0] if matches else ""
        if low.startswith(("ml_mask:", "mask:")):
            suffix = val.split(":", 1)[1].replace(" ", "_")
            matches = [c for c in df.columns if c.lower().startswith("mask_") and (not suffix or suffix.lower() in c.lower())]
            return matches[0] if matches else ""
        return ""
    color_col = pick(color_by or "")
    group_col = pick(group_by or "")
    return df, color_col, group_col


def _linear_fit(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = min(len(xs), len(ys))
    if n < 2:
        return {}
    mx = sum(xs) / n
    my = sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return {}
    slope = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / denom
    intercept = my - slope * mx
    preds = [slope * x + intercept for x in xs]
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - preds[i]) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    residual_std = math.sqrt(ss_res / max(1, n - 2))
    return {
        "slope": round(slope, 8),
        "intercept": round(intercept, 8),
        "r2": round(r2, 6),
        "equation": f"y = {slope:.6g}*x + {intercept:.6g}",
        "residual_std": round(residual_std, 8),
    }


def _solve_3x3(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    rows = [list(matrix[i]) + [vector[i]] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return None
        if pivot != col:
            rows[col], rows[pivot] = rows[pivot], rows[col]
        div = rows[col][col]
        rows[col] = [v / div for v in rows[col]]
        for r in range(3):
            if r == col:
                continue
            factor = rows[r][col]
            rows[r] = [rows[r][c] - factor * rows[col][c] for c in range(4)]
    return [rows[i][3] for i in range(3)]


def _quadratic_fit(xs: list[float], ys: list[float]) -> dict[str, Any]:
    n = min(len(xs), len(ys))
    if n < 3:
        return {}
    sx = sum(xs)
    sx2 = sum(x ** 2 for x in xs)
    sx3 = sum(x ** 3 for x in xs)
    sx4 = sum(x ** 4 for x in xs)
    sy = sum(ys)
    sxy = sum(xs[i] * ys[i] for i in range(n))
    sx2y = sum((xs[i] ** 2) * ys[i] for i in range(n))
    coeffs = _solve_3x3(
        [[sx4, sx3, sx2], [sx3, sx2, sx], [sx2, sx, float(n)]],
        [sx2y, sxy, sy],
    )
    if coeffs is None:
        return {}
    a, b, c = coeffs
    preds = [a * x * x + b * x + c for x in xs]
    my = sum(ys) / n
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((ys[i] - preds[i]) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    residual_std = math.sqrt(ss_res / max(1, n - 3))
    return {
        "degree": 2,
        "a": round(a, 8),
        "b": round(b, 8),
        "c": round(c, 8),
        "r2": round(r2, 6),
        "equation": f"y = {a:.6g}*x^2 + {b:.6g}*x + {c:.6g}",
        "residual_std": round(residual_std, 8),
    }


def _fit_params(kind: str, xs: list[float], ys: list[float]) -> dict[str, Any]:
    if kind == "linear":
        return _linear_fit(xs, ys)
    if kind == "quadratic":
        return _quadratic_fit(xs, ys)
    return {}


def _percentile(vals: list[float], q: float) -> float | None:
    clean = sorted(v for v in vals if math.isfinite(v))
    if not clean:
        return None
    pos = (len(clean) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - pos) + clean[hi] * (pos - lo)


def _stat_value(vals: list[float], col: str) -> Any:
    clean = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    if col == "count":
        return len(clean)
    if not clean:
        return None
    if col == "median":
        return round(statistics.median(clean), 6)
    if col == "avg":
        return round(sum(clean) / len(clean), 6)
    if col == "min":
        return round(min(clean), 6)
    if col == "max":
        return round(max(clean), 6)
    if col == "std":
        return round(statistics.stdev(clean), 6) if len(clean) > 1 else 0.0
    if col == "q1":
        val = _percentile(clean, 0.25)
        return round(val, 6) if val is not None else None
    if col == "q3":
        val = _percentile(clean, 0.75)
        return round(val, 6) if val is not None else None
    return None


def stats_table_from_points(points: list[dict[str, Any]], columns: list[str] | None = None, group_key: str = "") -> list[dict[str, Any]]:
    cols = columns or ["count", "median", "avg", "std"]
    groups: dict[str, list[float]] = {}
    for p in points:
        try:
            y = float(p.get("y") if p.get("y") is not None else p.get("value"))
        except Exception:
            continue
        key = str(p.get(group_key) if group_key else p.get("group") or p.get("color_value") or "all")
        groups.setdefault(key or "all", []).append(y)
    rows = []
    for group, vals in sorted(groups.items()):
        row = {"group": group} if group != "all" or group_key else {}
        for col in cols:
            row[col] = _stat_value(vals, col)
        rows.append(row)
    return rows


def build_multi_db_chart(payload: dict[str, Any], username: str = "user") -> dict[str, Any]:
    product = str(payload.get("product") or "").strip()
    primary = _upper(payload.get("primary_source") or "ET")
    secondary = _upper(payload.get("secondary_source") or "")
    sources = [s for s in [primary, secondary] if s and s != "NONE"]
    if not sources:
        sources = ["ET", "INLINE"]
    x_item = item_key(payload.get("x_item") or payload.get("x") or "")
    y_item = item_key(payload.get("y_item") or payload.get("y") or "")
    selected = [x for x in [x_item, y_item, *(payload.get("items") or [])] if x]
    chart_type = str(payload.get("chart_type") or infer_chart_type("", selected)).strip() or "scatter"
    color_by = str(payload.get("color_by") or "none")
    group_by = str(payload.get("group_by") or "")
    fit = str(payload.get("fit") or "none").lower()
    root_lot_ids = [str(v).strip().upper() for v in (payload.get("root_lot_ids") or []) if str(v).strip()]
    stats_req = payload.get("stats", ["count", "median", "avg", "std"])
    stats_cols = stats_req.get("columns") if isinstance(stats_req, dict) else stats_req
    if stats_req is None:
        stats_cols = None
    elif isinstance(stats_cols, str):
        stats_cols = [stats_cols]
    if stats_cols is not None:
        stats_cols = [c for c in (stats_cols or []) if c in {"count", "median", "avg", "min", "max", "std", "q1", "q3"}]

    if chart_type == "correlation_matrix":
        items = [item_key(v) for v in (payload.get("items") or selected) if item_key(v)]
        rows = []
        matrices: dict[str, dict[str, float | None]] = {}
        for i, x in enumerate(items):
            matrices.setdefault(x, {})
            for y in items:
                matrices[x][y] = 1.0 if x == y else None
            for y in items[i + 1:]:
                sub = build_multi_db_chart({**payload, "chart_type": "scatter", "x_item": x, "y_item": y, "fit": "none", "items": []}, username=username)
                corr = sub.get("corr")
                matrices.setdefault(x, {})[y] = corr
                matrices.setdefault(y, {})[x] = corr
                rows.append({"x": x, "y": y, "corr": corr, "count": len(sub.get("data") or [])})
        session_id = save_chart_session({
            "username": username,
            "chart_type": chart_type,
            "config": {**apply_chart_defaults(chart_type, items), **payload},
            "base_data_query": payload,
            "data": rows,
        })
        return {"ok": True, "chart_type": chart_type, "data": rows, "matrix": matrices, "stats_table": rows, "chart_session_id": session_id}

    if not x_item and selected:
        x_item = selected[0]
    if not y_item and len(selected) > 1:
        y_item = selected[1]
    elif not y_item:
        y_item = x_item

    x_metric = _find_metric(sources, product, x_item, "x", root_lot_ids)
    y_metric = _find_metric(sources, product, y_item, "y", root_lot_ids)
    if not x_metric.get("ok") or not y_metric.get("ok"):
        empty = {
            "ok": False,
            "chart_type": chart_type,
            "error": x_metric.get("error") or y_metric.get("error") or "metric not found",
            "data": [],
            "fit_params": {},
            "stats_table": [],
            "panels": [],
            "chart_session_id": "",
        }
        return empty
    on = _join_cols(x_metric.get("join_cols") or [], y_metric.get("join_cols") or [])
    try:
        df = (
            x_metric["lf"]
            .join(y_metric["lf"], on=on, how="inner")
            .drop_nulls(subset=["x", "y"])
            .limit(5000)
            .collect()
        )
    except Exception as e:
        return {"ok": False, "chart_type": chart_type, "error": str(e), "data": [], "fit_params": {}, "stats_table": [], "panels": []}
    df, color_col, group_col = _normalize_color_group_column(df, color_by=color_by, group_by=group_by, product=product)
    rows = serialize_rows(df.to_dicts())
    points: list[dict[str, Any]] = []
    xs: list[float] = []
    ys: list[float] = []
    for row in rows:
        try:
            x = float(row.get("x"))
            y = float(row.get("y"))
        except Exception:
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        xs.append(x)
        ys.append(y)
        point = {
            "x": round(x, 6),
            "y": round(y, 6),
            "label": row.get("lot_wf") or row.get("root_lot_id") or "",
            "root_lot_id": row.get("root_lot_id") or "",
            "fab_lot_id": row.get("fab_lot_id") or "",
            "wafer_id": row.get("wafer_id") or "",
            "product": row.get("product") or product,
        }
        if color_col and color_col in row:
            point["color_by"] = color_by
            point["color_value"] = row.get(color_col)
        if group_col and group_col in row:
            point["group"] = row.get(group_col)
        points.append(point)
    corr = None
    if len(xs) > 1 and statistics.pstdev(xs) > 0 and statistics.pstdev(ys) > 0:
        corr = round(sum((xs[i] - sum(xs) / len(xs)) * (ys[i] - sum(ys) / len(ys)) for i in range(len(xs))) / math.sqrt(sum((x - sum(xs) / len(xs)) ** 2 for x in xs) * sum((y - sum(ys) / len(ys)) ** 2 for y in ys)), 6)
    fit_params = _fit_params(fit, xs, ys) if chart_type == "scatter" else {}
    stat_rows = [] if stats_cols is None else stats_table_from_points(points, stats_cols, "group" if group_col else "")
    panels: list[dict[str, Any]] = []
    if group_col:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for p in points:
            grouped.setdefault(str(p.get("group") or "(blank)"), []).append(p)
        for key, sub_points in sorted(grouped.items()):
            sub_xs = [float(p["x"]) for p in sub_points]
            sub_ys = [float(p["y"]) for p in sub_points]
            panels.append({
                "group": key,
                "points": sub_points,
                "data": sub_points,
                "fit_params": _fit_params(fit, sub_xs, sub_ys) if chart_type == "scatter" else {},
                "stats_table": [] if stats_cols is None else stats_table_from_points(sub_points, stats_cols),
            })
    config = {
        **apply_chart_defaults(chart_type, selected),
        **{k: v for k, v in payload.items() if k not in {"stats"}},
        "x_item": x_item,
        "y_item": y_item,
        "color_by": color_by,
        "group_by": group_by,
        "fit": fit,
        "font_size": int(payload.get("font_size") or 14),
    }
    session_id = save_chart_session({
        "username": username,
        "chart_type": chart_type,
        "config": config,
        "base_data_query": payload,
        "data": points,
    })
    return {
        "ok": True,
        "chart_type": chart_type,
        "config": config,
        "data": points,
        "points": points,
        "fit_params": fit_params,
        "fit": fit_params,
        "stats_table": stat_rows,
        "panels": panels,
        "corr": corr,
        "join_keys": on,
        "color_by": color_by if color_col else ("none" if color_by in {"", "none", None} else color_by),
        "group_by": group_by,
        "chart_session_id": session_id,
    }


def _session_path(session_id: str) -> Path:
    clean = re.sub(r"[^A-Za-z0-9_.-]", "", str(session_id or ""))
    return CHART_SESSION_DIR / f"{clean}.json"


def _evict_sessions() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    CHART_SESSION_DIR.mkdir(parents=True, exist_ok=True)
    for fp in CHART_SESSION_DIR.glob("*.json"):
        try:
            raw = load_json(fp, {})
            ref = raw.get("refined_at") or raw.get("created_at")
            age = (now - dt.datetime.fromisoformat(str(ref).replace("Z", "+00:00"))).total_seconds()
            if age > CHART_SESSION_TTL_SECONDS:
                fp.unlink(missing_ok=True)
        except Exception:
            pass


def save_chart_session(session: dict[str, Any]) -> str:
    _evict_sessions()
    sid = str(session.get("session_id") or uuid.uuid4().hex)
    now = _now_iso()
    data = {
        "session_id": sid,
        "username": session.get("username") or "user",
        "chart_type": session.get("chart_type") or "scatter",
        "config": session.get("config") or {},
        "base_data_query": session.get("base_data_query") or {},
        "data": session.get("data") or [],
        "created_at": session.get("created_at") or now,
        "refined_at": now,
        "history": session.get("history") or [],
    }
    save_json(_session_path(sid), data, indent=2)
    return sid


def load_chart_session(session_id: str) -> dict[str, Any]:
    data = load_json(_session_path(session_id), {})
    if not data:
        raise FileNotFoundError("chart session not found")
    return data


def refine_chart_session(session_id: str, action: str, value: Any = None, username: str = "user") -> dict[str, Any]:
    data = load_chart_session(session_id)
    cfg = dict(data.get("config") or {})
    act = str(action or "").strip()
    if act == "font_size_delta":
        cfg["font_size"] = max(10, min(22, int(cfg.get("font_size") or 14) + int(value or 0)))
    elif act == "axis_label_size_delta":
        cfg["axis_label_size"] = max(10, min(24, int(cfg.get("axis_label_size") or 12) + int(value or 0)))
    elif act == "legend":
        cfg["legend"] = bool(value)
    elif act == "theme":
        cfg["theme"] = "light" if str(value).lower() == "light" else "dark"
    elif act == "y_scale":
        cfg["y_scale"] = str(value or "linear")
    elif act == "title":
        cfg["title"] = str(value or "")
    else:
        cfg[act] = value
    hist = list(data.get("history") or [])
    hist.append({"action": act, "value": value, "at": _now_iso(), "username": username})
    data.update({"config": cfg, "refined_at": _now_iso(), "history": hist})
    save_json(_session_path(session_id), data, indent=2)
    return {
        "ok": True,
        "chart_session_id": session_id,
        "chart_type": data.get("chart_type") or cfg.get("chart_type") or "scatter",
        "config": cfg,
        "data": data.get("data") or [],
        "history": hist,
    }
