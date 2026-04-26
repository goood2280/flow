"""routers/ml.py v6.0.0 — ML analysis on ML_TABLE wide feature tables.

Framework for TabPFN / TabICL / classical ML model integration.
For demo: uses correlation-based feature importance + simple train/test split metrics.
To plug in TabPFN/TabICL: replace _train_model with actual model calls.
"""
import logging, math
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import polars as pl
from core.paths import PATHS
from core.utils import (
    _STR, cast_cats, read_source, find_all_sources,
    load_json, save_json, serialize_rows, _glob_data_files, read_one_file,
)

logger = logging.getLogger("flow.ml")
router = APIRouter(prefix="/api/ml", tags=["ml"])
CONFIG_FILE = PATHS.data_root / "ml_config.json"


# ──────────────────────────────────────────────────────────────────
# Config / defaults
# ──────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {
    "default_source_root": "ML_TABLE",
    "default_product": "PRODUCT_A",
    "feature_prefixes": ["KNOB", "MASK", "INLINE", "VM", "FAB", "ET", "QTIME"],
    "target_candidates": ["RESULT", "FAB_YIELD", "FAB_BIN1_RATE"],
    "available_models": ["correlation", "tabpfn", "tabicl", "random_forest"],
}


def _config():
    return load_json(CONFIG_FILE, DEFAULT_CONFIG)


class MLTrainReq(BaseModel):
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    features: List[str] = []       # Selected feature columns
    target: str = ""               # Target column
    model: str = "correlation"     # correlation | tabpfn | tabicl | random_forest
    test_ratio: float = 0.2
    filter_expr: str = ""


class InlineETReq(BaseModel):
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    target_et: str = ""
    top_inline: int = 12
    top_knobs: int = 4
    filter_expr: str = ""


class MLSourceReq(BaseModel):
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    max_knobs: int = 8
    filter_expr: str = ""


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────
def _load_ml_data(src_type, root, product, file):
    """Load ML_TABLE data, return polars DataFrame."""
    df = read_source(src_type, root, product, file, max_files=20)
    if df.height == 0:
        raise HTTPException(400, "No data loaded")
    return cast_cats(df)


def _find_dataset_file(name: str) -> Path:
    for root in (PATHS.base_root, PATHS.db_root):
        fp = Path(root) / name
        if fp.is_file():
            return fp
    raise HTTPException(404, f"Dataset not found: {name}")


def _normalize_id_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize common ID columns from legacy uppercase ML_TABLE files."""
    aliases = {
        "product": {"product", "PRODUCT"},
        "root_lot_id": {"root_lot_id", "ROOT_LOT_ID"},
        "lot_id": {"lot_id", "LOT_ID"},
        "wafer_id": {"wafer_id", "WAFER_ID"},
    }
    rename = {}
    existing = set(df.columns)
    for canonical, candidates in aliases.items():
        if canonical in existing:
            continue
        found = next((c for c in df.columns if c in candidates), None)
        if found:
            rename[found] = canonical
    return df.rename(rename) if rename else df


def _load_inline_et_frames(ml_df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Prefer legacy feature parquets if present; otherwise derive from ML_TABLE."""
    try:
        et_fp = _find_dataset_file("features_et_wafer.parquet")
        inl_fp = _find_dataset_file("features_inline_agg.parquet")
        return (
            _normalize_id_columns(cast_cats(read_one_file(et_fp))),
            _normalize_id_columns(cast_cats(read_one_file(inl_fp))),
        )
    except HTTPException:
        pass
    id_cols = [c for c in ("root_lot_id", "lot_id", "wafer_id", "product") if c in ml_df.columns]
    et_cols = [c for c in ml_df.columns if str(c).upper().startswith(("ET_", "VM_"))]
    inline_cols = [c for c in ml_df.columns if str(c).upper().startswith("INLINE_")]
    if not et_cols or not inline_cols:
        raise HTTPException(404, "No ET/INLINE columns found in ML_TABLE and legacy feature datasets are absent")
    return ml_df.select(id_cols + et_cols), ml_df.select(id_cols + inline_cols)


def _derive_product_code(req_product: str, ml_df: pl.DataFrame) -> str:
    if "product" in ml_df.columns:
        vals = [str(v).strip() for v in ml_df["product"].drop_nulls().unique().to_list() if str(v).strip()]
        if vals:
            return vals[0]
    p = str(req_product or "").strip()
    return p.replace("ML_TABLE_", "") if p.startswith("ML_TABLE_") else p


def _numeric_feature_cols(df: pl.DataFrame, exclude: set[str]) -> list[str]:
    out = []
    for c, dt in df.schema.items():
        if c in exclude:
            continue
        if any(tok in str(dt) for tok in ("Int", "Float", "Decimal")):
            out.append(c)
    return out


def _feature_order(col: str) -> float:
    txt = str(col or "").strip()
    if " " in txt:
        head = txt.split(" ", 1)[0]
        try:
            return float(head)
        except Exception:
            return 10_000.0
    return 10_000.0


def _auto_detect_features(df: pl.DataFrame, prefixes: List[str]) -> dict:
    """Group columns by prefix for feature selection UI."""
    groups = {p: [] for p in prefixes}
    other = []
    for col in df.columns:
        matched = False
        for p in prefixes:
            if col.startswith(p + "_") or col.startswith(p):
                groups[p].append(col)
                matched = True
                break
        if not matched:
            other.append(col)
    groups["OTHER"] = other
    return {k: v for k, v in groups.items() if v}


def _correlation_importance(df: pl.DataFrame, features: List[str], target: str):
    """Pearson correlation between each feature and target."""
    importance = []
    try:
        tgt = df[target]
        # Convert target to numeric (binary for string targets)
        if tgt.dtype == pl.Utf8 or tgt.dtype == pl.String:
            vals = tgt.to_list()
            uniq = list(set(vals))
            if len(uniq) == 2:
                tgt_num = [1.0 if v == uniq[0] else 0.0 for v in vals]
            else:
                # Encode as index
                umap = {u: i for i, u in enumerate(uniq)}
                tgt_num = [float(umap.get(v, 0)) for v in vals]
            tgt_series = pl.Series(tgt_num)
        else:
            tgt_series = tgt.cast(pl.Float64, strict=False)

        for feat in features:
            if feat not in df.columns:
                continue
            try:
                f_series = df[feat].cast(pl.Float64, strict=False)
                # Pair-wise drop nulls
                mask = f_series.is_not_null() & tgt_series.is_not_null()
                if mask.sum() < 3:
                    continue
                corr = pl.DataFrame({"f": f_series.filter(mask), "t": tgt_series.filter(mask)}) \
                    .select(pl.corr("f", "t")).item()
                if corr is not None and not math.isnan(corr):
                    importance.append({"feature": feat, "importance": abs(round(corr, 4)), "direction": "+" if corr >= 0 else "-"})
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Correlation error: {e}")
    importance.sort(key=lambda x: x["importance"], reverse=True)
    return importance


def _train_model(df: pl.DataFrame, features: List[str], target: str, model: str, test_ratio: float):
    """Train model and return predictions + metrics.

    For demo: correlation-based linear combination as a stand-in for TabPFN/TabICL.
    Replace this with actual model calls when integrating real models.
    """
    # Always compute importance via correlation first
    importance = _correlation_importance(df, features, target)

    # Build numeric feature matrix
    try:
        tgt_col = df[target]
        if tgt_col.dtype == pl.Utf8 or tgt_col.dtype == pl.String:
            vals = tgt_col.to_list()
            uniq = sorted(set(str(v) for v in vals))
            label_map = {u: i for i, u in enumerate(uniq)}
            y = [label_map.get(str(v), 0) for v in vals]
            is_classification = True
        else:
            y = tgt_col.cast(pl.Float64, strict=False).fill_null(0).to_list()
            uniq = []
            is_classification = False

        # Stack features as numeric matrix
        X = []
        for feat in features:
            if feat not in df.columns:
                continue
            col = df[feat].cast(pl.Float64, strict=False).fill_null(0).to_list()
            X.append(col)
        if not X:
            raise HTTPException(400, "No valid numeric features")
        n = len(X[0])
        # Transpose: X[i] = row i features
        X_rows = [[X[j][i] for j in range(len(X))] for i in range(n)]

        # Simple train/test split (last test_ratio as test)
        split = max(1, int(n * (1 - test_ratio)))
        X_train, X_test = X_rows[:split], X_rows[split:]
        y_train, y_test = y[:split], y[split:]

        # Weighted linear combination using correlation importance (stand-in for real model)
        weights = []
        used_features = [f for f in features if f in df.columns][:len(X)]
        imp_map = {i["feature"]: i["importance"] * (1 if i["direction"] == "+" else -1) for i in importance}
        for f in used_features:
            weights.append(imp_map.get(f, 0))

        # Normalize weights
        w_sum = sum(abs(w) for w in weights) or 1
        weights = [w / w_sum for w in weights]

        # Predict as weighted sum
        def predict(row):
            s = sum(row[j] * weights[j] for j in range(len(row)))
            if is_classification:
                # Binary threshold at median of training predictions
                return 1 if s > 0 else 0
            return s

        # Normalize target scale for regression
        if not is_classification and y_train:
            y_mean = sum(y_train) / len(y_train)
            y_std = (sum((v - y_mean) ** 2 for v in y_train) / len(y_train)) ** 0.5 or 1
            preds_train_raw = [predict(r) for r in X_train]
            pred_mean = sum(preds_train_raw) / len(preds_train_raw) if preds_train_raw else 0
            pred_std = (sum((v - pred_mean) ** 2 for v in preds_train_raw) / len(preds_train_raw)) ** 0.5 or 1
            adjust = lambda p: (p - pred_mean) / pred_std * y_std + y_mean
        else:
            adjust = lambda p: p

        preds_test = [adjust(predict(r)) for r in X_test]
        preds_train = [adjust(predict(r)) for r in X_train]

        # Metrics
        metrics = {}
        if is_classification and y_test:
            correct = sum(1 for i in range(len(y_test)) if preds_test[i] == y_test[i])
            metrics["accuracy"] = round(correct / len(y_test), 4)
            metrics["n_test"] = len(y_test)
            metrics["classes"] = uniq
        elif y_test:
            ss_res = sum((y_test[i] - preds_test[i]) ** 2 for i in range(len(y_test)))
            y_mean = sum(y_test) / len(y_test)
            ss_tot = sum((y - y_mean) ** 2 for y in y_test) or 1
            metrics["r2"] = round(1 - ss_res / ss_tot, 4)
            metrics["rmse"] = round((ss_res / len(y_test)) ** 0.5, 4)
            metrics["n_test"] = len(y_test)

        # Prediction scatter (sampled to 500)
        scatter = []
        for i in range(len(y_test)):
            scatter.append({"actual": y_test[i], "predicted": round(preds_test[i], 4), "set": "test"})
        for i in range(min(500 - len(scatter), len(y_train))):
            scatter.append({"actual": y_train[i], "predicted": round(preds_train[i], 4), "set": "train"})

        return {
            "model": model,
            "is_classification": is_classification,
            "classes": uniq,
            "metrics": metrics,
            "importance": importance,
            "scatter": scatter[:500],
            "feature_count": len(used_features),
            "n_train": len(X_train),
            "note": "Correlation-based linear combo (stand-in). Plug TabPFN/TabICL in backend/routers/ml.py::_train_model.",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Train error: {e}")
        raise HTTPException(500, f"Train error: {e}")


# ──────────────────────────────────────────────────────────────────
# API Endpoints
# ──────────────────────────────────────────────────────────────────
@router.get("/config")
def get_config():
    return _config()


@router.get("/sources")
def list_sources():
    """List ML_TABLE sources available."""
    all_sources = find_all_sources()
    # Filter to ML_TABLE or wide tables only
    ml_sources = [s for s in all_sources if "ML" in s.get("label", "").upper() or s.get("root", "").startswith("ML")]
    # Fallback: include all if no ML_TABLE detected
    return {"sources": ml_sources if ml_sources else all_sources}


@router.get("/columns")
def get_columns(source_type: str = Query("flat"), root: str = Query("ML_TABLE"),
                product: str = Query("PRODUCT_A"), file: str = Query("")):
    """Get columns grouped by prefix (KNOB_*, MASK_*, etc.)."""
    try:
        df = read_source(source_type, root, product, file, max_files=1)
    except Exception as e:
        raise HTTPException(400, f"Cannot read source: {e}")
    cfg = _config()
    groups = _auto_detect_features(df, cfg["feature_prefixes"])
    return {
        "groups": groups,
        "all_columns": list(df.columns),
        "target_candidates": [c for c in cfg["target_candidates"] if c in df.columns],
    }


@router.post("/inline_et_overview")
def inline_et_overview(req: InlineETReq):
    ml_df = _normalize_id_columns(_load_ml_data(req.source_type, req.root, req.product, req.file))
    if req.filter_expr.strip():
        try:
            ml_df = ml_df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")
    product_code = _derive_product_code(req.product, ml_df)
    et_df, inl_df = _load_inline_et_frames(ml_df)
    if product_code and "product" in et_df.columns:
        et_df = et_df.filter(pl.col("product").cast(_STR, strict=False) == product_code)
    if product_code and "product" in inl_df.columns:
        inl_df = inl_df.filter(pl.col("product").cast(_STR, strict=False) == product_code)

    knob_cols = [c for c in ml_df.columns if str(c).startswith("KNOB_")]
    if not knob_cols:
        raise HTTPException(400, "No KNOB columns in ML table")
    need_ml = ["root_lot_id", "wafer_id", "product"] + knob_cols
    need_ml = [c for c in need_ml if c in ml_df.columns]
    ml_df = ml_df.select(need_ml).unique(subset=[c for c in ("root_lot_id", "wafer_id", "product") if c in need_ml], keep="first")

    joined = et_df.join(inl_df, on=[c for c in ("lot_id", "wafer_id", "product") if c in et_df.columns and c in inl_df.columns], how="left")
    join_keys = [c for c in ("root_lot_id", "wafer_id", "product") if c in joined.columns and c in ml_df.columns]
    if not join_keys:
        raise HTTPException(400, "root_lot_id/wafer_id/product join keys missing for ML join")
    joined = joined.with_columns([pl.col(c).cast(_STR, strict=False) for c in join_keys])
    ml_df = ml_df.with_columns([pl.col(c).cast(_STR, strict=False) for c in join_keys])
    joined = joined.join(ml_df, on=join_keys, how="left")
    if joined.height < 5:
        raise HTTPException(400, f"Not enough joined rows ({joined.height})")

    id_cols = {"lot_id", "root_lot_id", "wafer_id", "product", "pgm", "eqp", "chamber", "start_ts", "end_ts"}
    et_numeric = _numeric_feature_cols(et_df, id_cols)
    if not et_numeric:
        raise HTTPException(400, "No numeric ET targets found")
    target_et = req.target_et if req.target_et in joined.columns else et_numeric[0]
    inline_numeric = [c for c in _numeric_feature_cols(inl_df, {"lot_id", "wafer_id", "product"}) if c in joined.columns]
    top_inline = _correlation_importance(joined, inline_numeric, target_et)[: max(1, req.top_inline)]
    top_inline_names = [r["feature"] for r in top_inline[:3]]

    knob_cards = []
    earliest_map = {}
    try:
        from routers.splittable import _build_knob_meta
        km = _build_knob_meta(product_code)
        for kc in knob_cols:
            meta = km.get(kc) or km.get(kc.replace("KNOB_", ""))
            steps = []
            for g in (meta or {}).get("groups", []):
                steps.extend(g.get("step_ids") or [])
            steps = [s for s in steps if s]
            if steps:
                earliest_map[kc] = sorted(set(steps))[0]
    except Exception:
        earliest_map = {}

    for kc in knob_cols[: max(1, req.top_knobs)]:
        if kc not in joined.columns:
            continue
        counts = (joined.select(pl.col(kc).cast(_STR, strict=False).alias("value"))
                  .drop_nulls().group_by("value").len().sort("len", descending=True).head(6))
        rows = []
        for rec in counts.to_dicts():
            val = rec.get("value")
            subset = joined.filter(pl.col(kc).cast(_STR, strict=False) == val)
            row = {
                "value": val,
                "count": int(rec.get("len", 0)),
                "target_mean": round(float(subset[target_et].cast(pl.Float64, strict=False).drop_nulls().mean()), 4) if target_et in subset.columns and subset[target_et].drop_nulls().len() else None,
                "earliest_step_id": earliest_map.get(kc, ""),
                "inline_means": {},
            }
            for feat in top_inline_names:
                try:
                    sub = subset[feat].cast(pl.Float64, strict=False).drop_nulls()
                    if sub.len():
                        row["inline_means"][feat] = round(float(sub.mean()), 4)
                except Exception:
                    pass
            rows.append(row)
        knob_cards.append({
            "knob": kc,
            "earliest_step_id": earliest_map.get(kc, ""),
            "groups": rows,
        })

    return {
        "product": product_code,
        "target_et": target_et,
        "rows": joined.height,
        "top_inline": top_inline,
        "top_inline_features": top_inline_names,
        "knob_cards": knob_cards,
        "note": "ET target 기준으로 INLINE 상관을 보고, 같은 wafer의 ML_TABLE KNOB 그룹으로 평균을 나눈 요약입니다.",
    }


@router.post("/knob_lineage_summary")
def knob_lineage_summary(req: MLSourceReq):
    ml_df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.filter_expr.strip():
        try:
            ml_df = ml_df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")
    product_code = _derive_product_code(req.product, ml_df)
    knob_cols = [c for c in ml_df.columns if str(c).startswith("KNOB_")]
    if not knob_cols:
        raise HTTPException(400, "No KNOB columns in ML table")

    try:
        from routers.splittable import _build_knob_meta
        meta_map = _build_knob_meta(product_code)
    except Exception as e:
        logger.warning("knob lineage meta build failed: %s", e)
        meta_map = {}

    rows = []
    for col in sorted(knob_cols, key=lambda c: (_feature_order(c), str(c))):
        meta = meta_map.get(col) or meta_map.get(str(col).replace("KNOB_", "")) or {}
        groups = meta.get("groups") or []
        step_ids = []
        function_steps = []
        modules = []
        for g in groups:
            fs = str(g.get("func_step") or "").strip()
            if fs and fs not in function_steps:
                function_steps.append(fs)
            for mod in (g.get("modules") or []):
                mod = str(mod or "").strip()
                if mod and mod not in modules:
                    modules.append(mod)
            for sid in (g.get("step_ids") or []):
                sid = str(sid or "").strip()
                if sid and sid not in step_ids:
                    step_ids.append(sid)
        value_counts = (
            ml_df.select(pl.col(col).cast(_STR, strict=False).alias("value"))
            .drop_nulls()
            .group_by("value")
            .len()
            .sort("len", descending=True)
            .head(3)
            .to_dicts()
        )
        rows.append({
            "knob": col,
            "display_name": str(col).replace("KNOB_", "", 1),
            "feature_order": _feature_order(col),
            "earliest_step_id": sorted(step_ids)[0] if step_ids else "",
            "function_steps": function_steps,
            "modules": modules,
            "step_ids": step_ids,
            "rule_label": meta.get("label") or "",
            "group_count": len(groups),
            "top_values": [{"value": r.get("value"), "count": int(r.get("len", 0))} for r in value_counts],
        })

    rows.sort(key=lambda r: (r["feature_order"], r["earliest_step_id"] or "ZZZZZZZZ", r["knob"]))
    rows = rows[: max(1, req.max_knobs)]
    return {
        "product": product_code,
        "source": {
            "source_type": req.source_type,
            "root": req.root,
            "product": req.product,
            "file": req.file,
        },
        "row_count": ml_df.height,
        "knobs": rows,
        "note": "KNOB은 function_step과 실제 step_id를 함께 보여줘야 현장 적용과 분석 해석이 동시에 가능합니다.",
    }


@router.post("/train")
def train_model(req: MLTrainReq):
    """Train a model on selected features + target. Returns importance + metrics."""
    if not req.features:
        raise HTTPException(400, "No features selected")
    if not req.target:
        raise HTTPException(400, "No target selected")

    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.target not in df.columns:
        raise HTTPException(400, f"Target column '{req.target}' not found")

    # Apply filter
    if req.filter_expr.strip():
        try:
            df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")

    if df.height < 10:
        raise HTTPException(400, f"Not enough data ({df.height} rows, need >= 10)")

    result = _train_model(df, req.features, req.target, req.model, req.test_ratio)
    result["total_rows"] = df.height
    return result


@router.post("/predict")
def predict(req: MLTrainReq):
    """Predict on recent data (uses same feature set). Returns per-wafer predictions."""
    # Same as train but returns predictions for ALL rows
    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    result = _train_model(df, req.features, req.target, req.model, test_ratio=0.0)
    return result


# ──────────────────────────────────────────────────────────────────
# v7.1: Process-Window aware analysis — L0/L1/L2/L3 causality hierarchy
# ──────────────────────────────────────────────────────────────────
from core.domain import classify_column, db_level, can_cause, DB_REGISTRY, target_level as _target_level


def _parse_step(col: str):
    """Delegate to domain registry. Returns {family, major, minor, level, db, col}."""
    c = classify_column(col)
    return {
        "family": c["family"], "major": c["step_major"], "minor": c["step_minor"],
        "level": c["level"], "db": c["db"], "col": col,
    }


def _process_window_importance(df: pl.DataFrame, features: List[str], target: str, target_level: Optional[int] = None):
    """L0→L1→L2→L3 causality-aware importance.

    Physics:
      - L0 (FAB/VM/MASK/KNOB) can cause L1/L2/L3
      - L1 (INLINE) can cause L2/L3 (not L0)
      - L2 (ET) can cause L3 (not L0/L1)
      - L3 (YLD) is terminal; features at L3 are only co-observed, not causal
      - Within same level: correlated covariates, kept with 0.7× weight (not full causal)

    Step-distance decay applies ONLY within the FAB family (process step order matters there).
    Other families get full weight if causally valid.
    """
    base_imp = _correlation_importance(df, features, target)
    imp_map = {i["feature"]: i for i in base_imp}

    # Determine target level — use semantic override for yield/outcome columns
    tgt_meta = _parse_step(target)
    if target_level is None:
        target_level = _target_level(target)
    # Target FAB step (for FAB distance decay only)
    tgt_fab_step = tgt_meta["major"] if tgt_meta["family"] == "FAB" else 999

    out = []
    for f in features:
        if f not in df.columns:
            continue
        meta = _parse_step(f)
        info = imp_map.get(f, {"importance": 0.0, "direction": "+"})
        raw = info["importance"]
        f_level = meta["level"] if meta["level"] >= 0 else 0

        # Causal validity: feature level must be <= target level
        causal_valid = can_cause(f_level, target_level)

        # Within-level (covariate) — mark as "co-level", not fully causal
        same_level = (f_level == target_level and f_level >= 0)

        # Weight assignment
        if not causal_valid:
            weight = 0.0
        elif same_level:
            weight = 0.7  # covariate at same level
        elif meta["family"] == "FAB" and meta["major"] > 0:
            # FAB step-distance decay (only meaningful when target is also FAB-step-aware)
            if tgt_fab_step < 999:
                dist = abs(tgt_fab_step - meta["major"])
                weight = math.exp(-dist / 5.0)
            else:
                weight = 1.0
        else:
            weight = 1.0  # Pure upstream causal

        weighted = raw * weight

        out.append({
            "feature": f,
            "family": meta["family"],
            "db": meta["db"],
            "level": f_level,
            "level_label": f"L{f_level}" if f_level >= 0 else "?",
            "major": meta["major"],
            "minor": meta["minor"],
            "raw_importance": raw,
            "direction": info.get("direction", "+"),
            "distance": (target_level - f_level) if f_level >= 0 else None,
            "causal_valid": causal_valid,
            "same_level": same_level,
            "weight": round(weight, 4),
            "importance": round(weighted, 4),
        })

    out.sort(key=lambda x: x["importance"], reverse=True)
    return {
        "target_level": target_level,
        "target_family": tgt_meta["family"],
        "target_db": tgt_meta["db"],
        "target_fab_step": tgt_fab_step,
        "features": out,
    }


def _step_group_summary(pw_features: List[dict]) -> List[dict]:
    """Group features by (family, step) and return per-group aggregate importance.

    Each bucket carries level, level_label, and the top-5 contributing features.
    Sort by level first, then FAB step within level."""
    buckets = {}
    for f in pw_features:
        key = (f["family"], f["major"])
        if key not in buckets:
            buckets[key] = {
                "family": f["family"], "db": f.get("db", f["family"]),
                "level": f.get("level", 0), "level_label": f.get("level_label", "?"),
                "major": f["major"], "count": 0, "sum_imp": 0.0, "max_imp": 0.0,
                "top_features": [], "same_level_count": 0, "blocked_count": 0,
            }
        b = buckets[key]
        b["count"] += 1
        b["sum_imp"] += f["importance"]
        b["max_imp"] = max(b["max_imp"], f["importance"])
        if f.get("same_level"):
            b["same_level_count"] += 1
        if not f.get("causal_valid", True):
            b["blocked_count"] += 1
        b["top_features"].append({
            "feature": f["feature"], "importance": f["importance"],
            "direction": f["direction"], "causal_valid": f.get("causal_valid", True),
        })

    out = []
    for b in buckets.values():
        b["top_features"].sort(key=lambda x: x["importance"], reverse=True)
        b["top_features"] = b["top_features"][:5]
        b["mean_imp"] = round(b["sum_imp"] / max(1, b["count"]), 4)
        b["sum_imp"] = round(b["sum_imp"], 4)
        out.append(b)
    # Order: level 0 first (upstream), then FAB step order within level
    fam_order = {"KNOB": 0, "MASK": 1, "FAB": 2, "VM": 3, "INLINE": 4, "ET": 5, "YLD": 6, "OTHER": 9}
    out.sort(key=lambda x: (x["level"] if x["level"] >= 0 else 99, fam_order.get(x["family"], 99), x["major"]))
    return out


@router.post("/process_window")
def process_window(req: MLTrainReq):
    """v7: Process-window aware analysis. Returns:
      - features[]: each with family, step position, causal_valid, weighted importance
      - steps[]: aggregated per-step (major) importance
      - knob_splits{}: KNOB distribution (experiment splits)
      - target_info: parsed target step
      - causality_note: plain-language summary
    """
    if not req.features:
        raise HTTPException(400, "No features selected")
    if not req.target:
        raise HTTPException(400, "No target selected")

    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.target not in df.columns:
        raise HTTPException(400, f"Target column '{req.target}' not found")

    if req.filter_expr.strip():
        try:
            df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")
    if df.height < 10:
        raise HTTPException(400, f"Not enough data ({df.height} rows)")

    pw = _process_window_importance(df, req.features, req.target)
    steps = _step_group_summary(pw["features"])

    # Parsimony score — how concentrated is importance in top-K features
    sorted_imp = sorted([f["importance"] for f in pw["features"] if f["causal_valid"]], reverse=True)
    total_imp = sum(sorted_imp) or 1e-9
    cumulative = []
    acc = 0.0
    for v in sorted_imp:
        acc += v
        cumulative.append(round(acc / total_imp, 4))
    # Find k for 80% coverage
    k80 = next((i + 1 for i, c in enumerate(cumulative) if c >= 0.8), len(cumulative))
    parsimony = {
        "top5_coverage": round(sum(sorted_imp[:5]) / total_imp, 4) if total_imp else 0,
        "top10_coverage": round(sum(sorted_imp[:10]) / total_imp, 4) if total_imp else 0,
        "k_for_80pct": k80,
        "total_features": len(sorted_imp),
    }

    # KNOB split distribution + per-split target statistics (DOE-style)
    knob_splits = {}
    # Numeric target for split stats
    try:
        tgt_numeric = df[req.target].cast(pl.Float64, strict=False)
        has_numeric_target = tgt_numeric.drop_nulls().len() > 0
    except Exception:
        has_numeric_target = False
        tgt_numeric = None
    for f in req.features:
        if f.startswith("KNOB") and f in df.columns:
            try:
                key_col = df[f].cast(_STR, strict=False)
                vc = key_col.value_counts().sort("count", descending=True).head(8)
                rows = []
                for d in vc.to_dicts():
                    val = str(d.get(f, "") or "(null)")
                    cnt = int(d.get("count", d.get("counts", 0)))
                    row = {"value": val, "count": cnt}
                    if has_numeric_target:
                        try:
                            mask = key_col == val if val != "(null)" else key_col.is_null()
                            sub = tgt_numeric.filter(mask).drop_nulls()
                            if sub.len() > 0:
                                row["target_mean"] = round(float(sub.mean()), 4)
                                row["target_std"] = round(float(sub.std()) if sub.len() > 1 else 0.0, 4)
                                row["target_n"] = int(sub.len())
                        except Exception:
                            pass
                    rows.append(row)
                knob_splits[f] = rows
            except Exception:
                pass

    # Blocked (non-causal) feature count
    blocked = sum(1 for f in pw["features"] if not f["causal_valid"])
    same_lvl = sum(1 for f in pw["features"] if f.get("same_level"))
    kept = len(pw["features"]) - blocked

    target_lvl = pw["target_level"]
    lvl_name = {0: "L0 (FAB/VM/MASK/KNOB)", 1: "L1 (INLINE)", 2: "L2 (ET)", 3: "L3 (YLD)"}.get(target_lvl, f"L{target_lvl}")
    causality_note = (
        f"Target: {req.target} → {lvl_name}. "
        f"{kept} features are causally admissible (level ≤ target level); "
        f"{blocked} were masked (downstream of target). "
        f"{same_lvl} same-level covariates kept at 0.7× weight. "
        f"FAB step-distance decay (exp(-d/5)) applies only when target is a FAB step. "
        f"KNOB/MASK retain full weight as pre-process split/design axes."
    )

    # Per-level aggregate
    per_level = {}
    for f in pw["features"]:
        lvl = f.get("level_label", "?")
        per_level.setdefault(lvl, {"count": 0, "sum_imp": 0.0, "kept": 0})
        per_level[lvl]["count"] += 1
        per_level[lvl]["sum_imp"] += f["importance"]
        if f["causal_valid"]:
            per_level[lvl]["kept"] += 1
    for lvl in per_level:
        per_level[lvl]["sum_imp"] = round(per_level[lvl]["sum_imp"], 4)

    # Top contributing step-groups
    top_steps = sorted(steps, key=lambda s: s["sum_imp"], reverse=True)[:5]

    return {
        "target": req.target,
        "target_level": target_lvl,
        "target_level_label": lvl_name,
        "target_family": pw["target_family"],
        "target_db": pw["target_db"],
        "target_fab_step": pw["target_fab_step"],
        "features": pw["features"][:200],
        "steps": steps,
        "top_steps": top_steps,
        "per_level": per_level,
        "knob_splits": knob_splits,
        "parsimony": parsimony,
        "blocked_count": blocked,
        "same_level_count": same_lvl,
        "kept_count": kept,
        "total_rows": df.height,
        "causality_note": causality_note,
        "recommended_model": (
            f"Hierarchical tree/GBM (e.g. LightGBM) or TabPFN with L0-only features for predicting "
            f"{pw['target_db']} ({lvl_name}). For parsimony: top-{parsimony['k_for_80pct']} features "
            f"cover 80% of explained variance. Prefer these + KNOB as group variable. For PRODA-PRODB "
            f"transfer: re-rank importance under PRODB distribution and keep features with stable rank."
        ),
    }


# ──────────────────────────────────────────────────────────────────
# v7.1: PRODA → PRODB transfer knowledge + Performance vs Yield Pareto
# ──────────────────────────────────────────────────────────────────
class TransferReq(BaseModel):
    source_type: str = "flat"
    source_root: str = "ML_TABLE"
    source_product: str = "PRODUCT_A"
    source_file: str = ""
    target_root: str = "ML_TABLE"
    target_product: str = "PRODUCT_B"
    target_file: str = ""
    features: List[str] = []
    target: str = ""
    filter_expr: str = ""


@router.post("/transfer")
def transfer(req: TransferReq):
    """PRODA → PRODB knowledge transfer analysis.

    Computes process-window importance on BOTH products and returns:
      - rank_shift[]: feature, rank_src, rank_tgt, delta_rank, delta_imp, stable?
      - distribution_shift[]: feature, src_mean, tgt_mean, src_std, tgt_std, ks_proxy
      - invariant_features[]: features with |delta_rank|<=3 AND |z_delta_imp|<=0.2 (stable across products)
      - novel_features[]: important in TGT but not SRC (rank_tgt <10 and rank_src >20)
      - vanishing_features[]: important in SRC but not TGT (rank_src <10 and rank_tgt >20)
    """
    if not req.features or not req.target:
        raise HTTPException(400, "features + target required")

    src_df = _load_ml_data(req.source_type, req.source_root, req.source_product, req.source_file)
    tgt_df = _load_ml_data(req.source_type, req.target_root, req.target_product, req.target_file)

    if req.target not in src_df.columns or req.target not in tgt_df.columns:
        raise HTTPException(400, f"Target '{req.target}' not in both products")

    for ex in (req.filter_expr.strip(),):
        if ex:
            try:
                src_df = src_df.filter(pl.sql_expr(ex))
                tgt_df = tgt_df.filter(pl.sql_expr(ex))
            except Exception as e:
                raise HTTPException(400, f"Filter error: {e}")

    src_pw = _process_window_importance(src_df, req.features, req.target)
    tgt_pw = _process_window_importance(tgt_df, req.features, req.target)

    # Build rank maps (by importance, descending)
    def rank_map(pw):
        ranked = sorted(pw["features"], key=lambda f: f["importance"], reverse=True)
        return {f["feature"]: (i + 1, f["importance"], f.get("causal_valid", True)) for i, f in enumerate(ranked)}
    src_rank = rank_map(src_pw)
    tgt_rank = rank_map(tgt_pw)

    # Rank shift table
    rank_shift = []
    for f in req.features:
        if f not in src_rank or f not in tgt_rank:
            continue
        sr, si, sv = src_rank[f]
        tr, ti, tv = tgt_rank[f]
        d_rank = tr - sr
        d_imp = ti - si
        rank_shift.append({
            "feature": f,
            "rank_src": sr, "imp_src": round(si, 4),
            "rank_tgt": tr, "imp_tgt": round(ti, 4),
            "delta_rank": d_rank,
            "delta_imp": round(d_imp, 4),
            "causal_both": sv and tv,
        })
    rank_shift.sort(key=lambda r: r["rank_src"])

    # Distribution shift (per numeric feature)
    dist_shift = []
    for f in req.features:
        if f not in src_df.columns or f not in tgt_df.columns:
            continue
        try:
            s = src_df[f].cast(pl.Float64, strict=False).drop_nulls()
            t = tgt_df[f].cast(pl.Float64, strict=False).drop_nulls()
            if s.len() < 5 or t.len() < 5:
                continue
            s_mean = float(s.mean()); s_std = float(s.std()) if s.len() > 1 else 0.0
            t_mean = float(t.mean()); t_std = float(t.std()) if t.len() > 1 else 0.0
            # KS-like proxy: normalized mean-shift
            pooled_std = ((s_std ** 2 + t_std ** 2) / 2) ** 0.5 or 1
            z = abs(t_mean - s_mean) / pooled_std
            dist_shift.append({
                "feature": f,
                "src_mean": round(s_mean, 4), "src_std": round(s_std, 4),
                "tgt_mean": round(t_mean, 4), "tgt_std": round(t_std, 4),
                "z_shift": round(z, 3),
            })
        except Exception:
            pass
    dist_shift.sort(key=lambda r: r["z_shift"], reverse=True)

    # Classify features
    invariant = [r["feature"] for r in rank_shift if abs(r["delta_rank"]) <= 3 and abs(r["delta_imp"]) <= 0.1 and r["causal_both"]]
    novel = [r["feature"] for r in rank_shift if r["rank_tgt"] <= 10 and r["rank_src"] > 20]
    vanishing = [r["feature"] for r in rank_shift if r["rank_src"] <= 10 and r["rank_tgt"] > 20]

    return {
        "source_label": f"{req.source_root}/{req.source_product}" if req.source_product else req.source_file,
        "target_label": f"{req.target_root}/{req.target_product}" if req.target_product else req.target_file,
        "target": req.target,
        "src_rows": src_df.height,
        "tgt_rows": tgt_df.height,
        "rank_shift": rank_shift[:50],
        "distribution_shift": dist_shift[:30],
        "invariant_features": invariant,
        "novel_features": novel,
        "vanishing_features": vanishing,
        "src_importance": src_pw["features"][:30],
        "tgt_importance": tgt_pw["features"][:30],
        "note": (
            f"Stable features ({len(invariant)}) are your safest transfer prior. "
            f"Novel-in-PRODB ({len(novel)}) need fresh exploration. "
            f"Vanishing-from-PRODA ({len(vanishing)}) indicate process changes — "
            f"verify via KNOB/MASK distribution check."
        ),
    }


class ModelFlowReq(BaseModel):
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    features: List[str] = []
    target: str = ""
    filter_expr: str = ""
    # Engineer prior about lookback uncertainty (± steps from the dominant upstream).
    # Default 3 reflects typical "3-8 upstream steps matter, exact bound unknown".
    lookback_margin: int = 3


@router.post("/model_flow")
def model_flow(req: ModelFlowReq):
    """Produce a model-flow diagram description for the given feature + target set.

    Reflects the team's actual process semantics:
      • Upstream steps influence downstream (monotone in step order).
      • We DON'T know exactly how many upstream steps matter — emit `lookback_range`
        with a margin of uncertainty around the inferred dominant upstream.
      • INLINE (L1) is an ACCUMULATOR of upstream L0 effects.
        If L1 features are present, flag them as an intermediate aggregation node.
      • Back→front influence is rare; we still show it as a thin dashed edge when
        a same-level or nominally downstream feature carries non-trivial correlation
        (with "weak reverse" label).

    Returns:
      nodes[]        — {id, kind (group|inline_accumulator|target), level, family, major, count, sum_imp, label}
      edges[]        — {from, to, weight, causal_valid, kind (direct|via_inline|weak_reverse)}
      lookback       — {dominant_upstream_step, min_step, max_step, margin, note}
      recommendation — plain-language summary of model structure
    """
    if not req.features or not req.target:
        raise HTTPException(400, "features + target required")

    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.target not in df.columns:
        raise HTTPException(400, f"target '{req.target}' not found")
    if req.filter_expr.strip():
        try:
            df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")

    pw = _process_window_importance(df, req.features, req.target)
    # Inject target_level_label (not set by helper)
    _lvl_name_map = {0: "L0 (FAB/VM/MASK/KNOB)", 1: "L1 (INLINE)", 2: "L2 (ET)", 3: "L3 (YLD)"}
    pw["target_level_label"] = _lvl_name_map.get(pw["target_level"], f"L{pw['target_level']}")
    steps = _step_group_summary(pw["features"])
    target_lvl = pw["target_level"]

    # Identify INLINE accumulator presence
    has_inline = any(s["family"] == "INLINE" for s in steps)
    has_vm     = any(s["family"] == "VM" for s in steps)
    has_et     = any(s["family"] == "ET" for s in steps)

    # Lookback inference — find highest-FAB-step group with material importance among upstream features
    fab_groups = [s for s in steps if s["family"] == "FAB" and s["level"] <= target_lvl and s["sum_imp"] > 0]
    if fab_groups:
        # Sort by step descending, take the highest-step group whose sum_imp is ≥ 10% of max
        fab_sorted = sorted(fab_groups, key=lambda s: s["major"])
        max_imp = max(s["sum_imp"] for s in fab_sorted)
        material = [s for s in fab_sorted if s["sum_imp"] >= 0.1 * max_imp]
        dominant = max(material, key=lambda s: s["major"]) if material else fab_sorted[-1]
        min_step = material[0]["major"] if material else fab_sorted[0]["major"]
        max_step = dominant["major"]
    else:
        dominant = None
        min_step = None
        max_step = None

    margin = int(req.lookback_margin)
    lookback = {
        "dominant_upstream_step": max_step,
        "min_step_with_signal": min_step,
        "margin": margin,
        "range_low": (min_step - margin) if min_step is not None else None,
        "range_high": (max_step + margin) if max_step is not None else None,
        "note": (
            f"Material upstream influence detected across FAB steps "
            f"{min_step}-{max_step}. Actual bound is uncertain; lookback window "
            f"padded by +/-{margin} steps."
            if max_step is not None else
            "No FAB-step-ordered upstream signal -- model is driven by KNOB/MASK/INLINE/VM."
        ),
    }

    # Build nodes
    nodes = []
    for s in steps:
        nodes.append({
            "id": f"{s['family']}_{s['major']}",
            "kind": "group",
            "family": s["family"], "db": s.get("db", s["family"]),
            "level": s["level"], "level_label": s.get("level_label", "?"),
            "major": s["major"],
            "count": s["count"], "sum_imp": s["sum_imp"],
            "max_imp": s.get("max_imp", 0),
            "same_level_count": s.get("same_level_count", 0),
            "blocked_count": s.get("blocked_count", 0),
            "top_features": s.get("top_features", [])[:3],
            "label": f"{s['family']}{('_' + str(s['major'])) if s['family'] == 'FAB' and s['major'] > 0 else ''}",
        })
    # INLINE accumulator node (synthetic) — shown as the L1 waypoint
    if has_inline:
        inline_nodes = [n for n in nodes if n["family"] == "INLINE"]
        acc_sum = sum(n["sum_imp"] for n in inline_nodes)
        nodes.append({
            "id": "INLINE_ACCUMULATOR",
            "kind": "accumulator",
            "family": "INLINE", "db": "INLINE",
            "level": 1, "level_label": "L1",
            "major": 500, "count": sum(n["count"] for n in inline_nodes),
            "sum_imp": acc_sum,
            "label": "INLINE (accumulates upstream)",
            "note": "Treats upstream L0 effects as a latent state measured at INLINE.",
        })
    # Target node
    nodes.append({
        "id": "TARGET",
        "kind": "target",
        "family": pw["target_family"], "db": pw["target_db"],
        "level": target_lvl,
        "level_label": pw["target_level_label"],
        "label": req.target,
    })

    # Build edges
    edges = []
    for s in steps:
        nid = f"{s['family']}_{s['major']}"
        weight = round(s["sum_imp"], 4)
        causal = s["level"] <= target_lvl
        reverse = s["level"] > target_lvl and weight > 0.05
        if has_inline and s["family"] in ("FAB", "KNOB", "MASK", "VM") and s["level"] == 0 and causal:
            # L0 → INLINE → target (also direct edge with lower weight)
            edges.append({"from": nid, "to": "INLINE_ACCUMULATOR",
                          "weight": weight * 0.7, "causal_valid": True, "kind": "via_inline"})
            edges.append({"from": nid, "to": "TARGET",
                          "weight": weight * 0.3, "causal_valid": True, "kind": "direct"})
        else:
            edges.append({
                "from": nid, "to": "TARGET",
                "weight": weight, "causal_valid": causal and not reverse,
                "kind": "weak_reverse" if reverse else ("direct" if causal else "blocked"),
            })
    if has_inline:
        inline_acc_sum = sum(n["sum_imp"] for n in nodes if n.get("id") == "INLINE_ACCUMULATOR")
        edges.append({
            "from": "INLINE_ACCUMULATOR", "to": "TARGET",
            "weight": inline_acc_sum, "causal_valid": True, "kind": "accumulator_out",
        })

    # Recommendation
    layers = []
    if any(n["family"] == "KNOB" for n in nodes if n["kind"] == "group"): layers.append("KNOB (split)")
    if any(n["family"] == "MASK" for n in nodes if n["kind"] == "group"): layers.append("MASK")
    if any(n["family"] == "FAB"  for n in nodes if n["kind"] == "group"): layers.append(f"FAB ({min_step}–{max_step})" if max_step is not None else "FAB")
    if has_vm: layers.append("VM")
    if has_inline: layers.append("INLINE (latent state)")
    if has_et: layers.append("ET (covariate)")
    rec = (
        f"Recommended structure: hierarchical GBM/TabPFN with input layers "
        f"[{ ' + '.join(layers) }] → {req.target} ({pw['target_level_label']}). "
        f"Lookback window: {lookback['range_low']}–{lookback['range_high']} (FAB step), "
        f"uncertainty ±{margin}. "
        + ("INLINE treated as intermediate aggregation node (pre-aggregates upstream L0 effects before reaching target). "
           if has_inline else "")
        + "Same-level features at 0.7× (covariate), downstream features masked."
    )

    return {
        "target": req.target,
        "target_level": target_lvl,
        "target_level_label": pw["target_level_label"],
        "nodes": nodes,
        "edges": edges,
        "lookback": lookback,
        "has_inline": has_inline,
        "has_vm": has_vm,
        "has_et": has_et,
        "recommendation": rec,
        "total_rows": df.height,
    }


class PpidGroupReq(BaseModel):
    """Split analysis by PPID (or any stratifier). Returns per-group summary so
    the user can decide whether to pool or to build separate sub-models."""
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    stratifier: str = "KNOB_RECIPE_1"   # PPID / KNOB / MASK column to split on
    features: List[str] = []
    target: str = ""
    filter_expr: str = ""
    min_group_size: int = 5


@router.post("/ppid_stratify")
def ppid_stratify(req: PpidGroupReq):
    """Per-PPID (or per-KNOB) importance comparison.

    Engineers often see DIFFERENT trends per PPID — this endpoint computes
    feature importance INDEPENDENTLY inside each stratifier value and reports:
      - whether top features are stable across groups
      - whether a single pooled model is safe, or per-group sub-models needed

    Parsimony preserved. Heavy compute deferred — we only rank correlations here.
    """
    if not req.features or not req.target or not req.stratifier:
        raise HTTPException(400, "features + target + stratifier required")
    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.stratifier not in df.columns:
        raise HTTPException(400, f"stratifier '{req.stratifier}' not in source")
    if req.target not in df.columns:
        raise HTTPException(400, f"target '{req.target}' not in source")
    if req.filter_expr.strip():
        try: df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e: raise HTTPException(400, f"Filter: {e}")

    # Enumerate groups (cast to string + fill null)
    keyed = df.with_columns(
        pl.col(req.stratifier).cast(pl.Utf8, strict=False).fill_null("(null)").alias("_k")
    )
    vc = keyed["_k"].value_counts().sort("count", descending=True)
    groups = []
    for r in vc.to_dicts():
        val = r["_k"]; n = int(r.get("count", r.get("counts", 0)))
        if n < req.min_group_size:
            continue
        sub = keyed.filter(pl.col("_k") == val)
        imp = _correlation_importance(sub, req.features, req.target)
        top_imp = imp[:5]
        groups.append({
            "group": val, "n": n,
            "top_features": [{"feature": i["feature"], "importance": i["importance"],
                               "direction": i["direction"]} for i in top_imp],
            "full_rank": [i["feature"] for i in imp],
        })

    if not groups:
        return {"groups": [], "note": "No group has >= min_group_size rows."}

    # Stability: for each feature, compare rank across groups
    feat_set = set()
    for g in groups: feat_set.update(g["full_rank"][:10])
    rank_stability = []
    for f in feat_set:
        ranks = []
        for g in groups:
            try: ranks.append(g["full_rank"].index(f) + 1)
            except ValueError: ranks.append(len(g["full_rank"]) + 1)
        if len(ranks) < 2:
            continue
        mean_r = sum(ranks) / len(ranks)
        span = max(ranks) - min(ranks)
        rank_stability.append({
            "feature": f, "ranks": ranks, "mean_rank": round(mean_r, 1),
            "span": span, "stable": span <= 3,
        })
    rank_stability.sort(key=lambda r: r["mean_rank"])
    stable_count = sum(1 for r in rank_stability if r["stable"])
    unstable_count = len(rank_stability) - stable_count

    recommendation = (
        f"Analyzed {len(groups)} groups of {req.stratifier}. "
        f"{stable_count} features stable across groups (|Δrank|<=3), "
        f"{unstable_count} vary significantly. "
        + ("POOLED model likely OK." if stable_count > unstable_count
           else "RECOMMEND per-group sub-models — PPIDs exhibit distinct mechanisms.")
    )

    return {
        "stratifier": req.stratifier,
        "target": req.target,
        "groups": groups,
        "rank_stability": rank_stability[:20],
        "stable_count": stable_count,
        "unstable_count": unstable_count,
        "recommendation": recommendation,
    }


class ShotInterpReq(BaseModel):
    """Fill in missing INLINE/ET shots from a reference full-map + partial coverage.

    Inputs:
      reference_source   — a wafer (or set of wafers) with FULL shot coverage
      target_source      — the wafer with partial coverage (missing shots)

    Uses weighted nearest-neighbor interpolation on the reference ensemble,
    scaled by the partial-measurement residual on matching shots. Memory-light
    (never loads more than needed).
    """
    # Reference (full-map) source
    ref_source_type: str = "flat"
    ref_root: str = "INLINE"
    ref_product: str = "PRODUCT_A"
    ref_file: str = ""
    ref_filter: str = ""
    # Target (partial) source — same source, different filter by default
    tgt_filter: str = ""
    value_col: str = "VALUE"
    shot_x_col: str = "SHOT_X"
    shot_y_col: str = "SHOT_Y"
    wafer_col: str = "LOT_WF"
    k_neighbors: int = 3


@router.post("/shot_interp")
def shot_interp(req: ShotInterpReq):
    """Interpolate missing shots using weighted kNN on a reference full-map.

    Algorithm:
      1. Build ensemble-mean map M(x,y) from reference (full-coverage) rows.
      2. For target wafer(s), compute residual r(x,y) = target(x,y) - M(x,y)
         on the SHOTS THAT EXIST.
      3. Estimate missing shot (x0,y0) as M(x0,y0) + mean(residual of k nearest
         measured neighbors). Neighbors by Euclidean shot-distance.

    This is the classic "partial-measurement corrected by full-map prior" trick.
    Ideal for INLINE/ET where only some shots are captured.
    """
    # Load reference
    try:
        ref = _load_ml_data(req.ref_source_type, req.ref_root, req.ref_product, req.ref_file)
    except Exception as e:
        raise HTTPException(400, f"ref load: {e}")
    if req.ref_filter.strip():
        try: ref = ref.filter(pl.sql_expr(req.ref_filter))
        except Exception as e: raise HTTPException(400, f"ref filter: {e}")
    # Reference ensemble mean map
    for c in (req.value_col, req.shot_x_col, req.shot_y_col):
        if c not in ref.columns:
            raise HTTPException(400, f"'{c}' not in reference source")
    ens = ref.group_by([req.shot_x_col, req.shot_y_col]).agg(
        pl.col(req.value_col).cast(pl.Float64, strict=False).mean().alias("m")
    ).drop_nulls("m").to_dicts()
    M = {(int(r[req.shot_x_col]), int(r[req.shot_y_col])): float(r["m"]) for r in ens}
    if not M:
        raise HTTPException(400, "reference has no valid shots")

    # Load target (same source) with tgt_filter
    tgt = ref
    if req.tgt_filter.strip():
        try: tgt = ref.filter(pl.sql_expr(req.tgt_filter))
        except Exception as e: raise HTTPException(400, f"tgt filter: {e}")

    # Per-wafer interpolation
    wafer_col = req.wafer_col if req.wafer_col in tgt.columns else None
    if wafer_col:
        wafers = tgt[wafer_col].cast(pl.Utf8, strict=False).drop_nulls().unique().to_list()
    else:
        wafers = ["(all)"]

    full_grid = sorted(M.keys())
    results = []
    for w in wafers[:8]:  # cap to 8 wafers per response
        sub = tgt.filter(pl.col(wafer_col).cast(pl.Utf8, strict=False) == str(w)) if wafer_col else tgt
        measured = sub.group_by([req.shot_x_col, req.shot_y_col]).agg(
            pl.col(req.value_col).cast(pl.Float64, strict=False).mean().alias("v")
        ).drop_nulls("v").to_dicts()
        obs = {(int(r[req.shot_x_col]), int(r[req.shot_y_col])): float(r["v"]) for r in measured}
        # Residual on overlapping shots
        residuals = {xy: obs[xy] - M[xy] for xy in obs if xy in M}
        measured_set = set(obs.keys())
        missing = [xy for xy in full_grid if xy not in measured_set]
        filled = []
        for (x0, y0) in missing:
            # Nearest k measured shots by Euclidean dist
            dists = sorted([(((x0 - x) ** 2 + (y0 - y) ** 2) ** 0.5, (x, y))
                             for (x, y) in obs.keys()])
            nbrs = dists[: req.k_neighbors]
            if not nbrs:
                est = M[(x0, y0)]
                conf = 0.2
            else:
                w_sum = 0; num = 0
                for d, xy in nbrs:
                    wgt = 1.0 / (d + 0.5)
                    num += wgt * residuals.get(xy, 0); w_sum += wgt
                corrected_residual = num / w_sum if w_sum else 0
                est = M[(x0, y0)] + corrected_residual
                conf = min(1.0, 1.0 / (nbrs[0][0] + 0.5))
            filled.append({"x": x0, "y": y0, "value": round(est, 4), "confidence": round(conf, 3), "interpolated": True})
        # Observed points (pass through)
        kept = [{"x": x, "y": y, "value": round(obs[(x, y)], 4), "confidence": 1.0, "interpolated": False}
                 for (x, y) in obs]
        results.append({
            "wafer": str(w),
            "observed": len(kept),
            "interpolated": len(filled),
            "total_grid": len(full_grid),
            "shots": kept + filled,
            "mean_residual": round(sum(residuals.values()) / max(1, len(residuals)), 4) if residuals else 0,
        })

    return {
        "reference_full_grid": full_grid,
        "reference_grid_size": len(full_grid),
        "wafers": results,
        "note": (
            f"kNN interpolation (k={req.k_neighbors}) on reference ensemble of "
            f"{len(full_grid)} shots. Per wafer: filled missing shots using "
            "nearest observed residuals + reference mean. Confidence drops with distance."
        ),
    }


class WfMapReq(BaseModel):
    source_type: str = "flat"
    root: str = ""
    product: str = "PRODUCT_A"
    file: str = ""
    value_col: str = ""          # what to map (e.g. VTH_IDX, INLINE_MEAS_1)
    wafer_col: str = "LOT_WF"
    shot_x_col: str = "SHOT_X"
    shot_y_col: str = "SHOT_Y"
    filter_expr: str = ""
    max_wafers: int = 12


@router.post("/wf_map")
def wf_map(req: WfMapReq):
    """Wafer-map consistency + trend analysis for shot-level data (ET / INLINE / YLD).

    Returns:
      ensemble       — per-(shot_x, shot_y) {mean, std, n} across all wafers
      consistency    — 1 - CV computed globally (std / |mean|), higher = more consistent
      pattern        — {center_minus_edge, radial_slope, tilt_x, tilt_y}
      wafers[]       — up to max_wafers {wafer, shots[{x,y,value}], deviation_score}
    """
    if not req.value_col:
        raise HTTPException(400, "value_col required")
    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    for c in (req.value_col, req.wafer_col, req.shot_x_col, req.shot_y_col):
        if c not in df.columns:
            raise HTTPException(400, f"column '{c}' not found")
    if req.filter_expr.strip():
        try:
            df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")

    # Ensemble: per-(x,y) mean/std/n
    ens = df.group_by([req.shot_x_col, req.shot_y_col]).agg([
        pl.col(req.value_col).cast(pl.Float64, strict=False).mean().alias("mean"),
        pl.col(req.value_col).cast(pl.Float64, strict=False).std().alias("std"),
        pl.col(req.value_col).cast(pl.Float64, strict=False).drop_nulls().len().alias("n"),
    ]).sort([req.shot_x_col, req.shot_y_col])
    ensemble = []
    all_means = []
    for r in ens.to_dicts():
        m = r["mean"]; s = r.get("std") or 0
        if m is None:
            continue
        ensemble.append({
            "x": int(r[req.shot_x_col]), "y": int(r[req.shot_y_col]),
            "mean": round(float(m), 4), "std": round(float(s), 4), "n": int(r["n"]),
        })
        all_means.append(float(m))

    # Handle empty ensemble (shot cols missing or all-null)
    if not ensemble:
        return {
            "value_col": req.value_col, "total_rows": df.height,
            "ensemble": [], "consistency": 0, "pattern": {"label": "no_data"},
            "wafers": [], "wafer_count": 0,
            "note": "No shot-level data available for this value_col (check shot_x/shot_y columns or filter).",
        }
    # Global consistency: 1 - std(means) / |mean(means)|
    gm = sum(all_means) / len(all_means)
    gs = (sum((v - gm) ** 2 for v in all_means) / len(all_means)) ** 0.5
    consistency = max(0.0, 1.0 - (gs / (abs(gm) or 1)))

    # Spatial pattern: center vs edge, radial slope, tilt
    def _radius(p): return (p["x"] ** 2 + p["y"] ** 2) ** 0.5
    center_pts = [p for p in ensemble if _radius(p) <= 1.5]
    edge_pts = [p for p in ensemble if _radius(p) >= 2.0]
    center_mean = sum(p["mean"] for p in center_pts) / len(center_pts) if center_pts else 0
    edge_mean = sum(p["mean"] for p in edge_pts) / len(edge_pts) if edge_pts else 0
    # Radial slope via least-squares on (radius, mean)
    if len(ensemble) > 3:
        xs = [_radius(p) for p in ensemble]; ys = [p["mean"] for p in ensemble]
        n = len(xs); sx = sum(xs); sy = sum(ys); sxy = sum(a * b for a, b in zip(xs, ys)); sx2 = sum(a * a for a in xs)
        denom = (n * sx2 - sx * sx) or 1
        slope = (n * sxy - sx * sy) / denom
    else:
        slope = 0
    # Tilt: per-axis slope
    def _axis_slope(axis):
        pts = [(p[axis], p["mean"]) for p in ensemble]
        if len(pts) < 3: return 0
        n = len(pts); sx = sum(a for a, _ in pts); sy = sum(b for _, b in pts)
        sxy = sum(a * b for a, b in pts); sx2 = sum(a * a for a, _ in pts)
        return (n * sxy - sx * sy) / ((n * sx2 - sx * sx) or 1)
    tilt_x = _axis_slope("x"); tilt_y = _axis_slope("y")

    pattern = {
        "center_mean": round(center_mean, 4),
        "edge_mean": round(edge_mean, 4),
        "center_minus_edge": round(center_mean - edge_mean, 4),
        "radial_slope": round(slope, 4),
        "tilt_x": round(tilt_x, 4),
        "tilt_y": round(tilt_y, 4),
    }
    # Classify pattern
    label = "uniform"
    if abs(pattern["center_minus_edge"]) > 0.1 * (abs(edge_mean) or 1):
        label = "center-hot" if pattern["center_minus_edge"] > 0 else "edge-hot"
    elif abs(slope) > 0.05:
        label = "radial-in" if slope > 0 else "radial-out"
    elif max(abs(tilt_x), abs(tilt_y)) > 0.05:
        label = f"tilt-{'x' if abs(tilt_x) > abs(tilt_y) else 'y'}"
    pattern["label"] = label

    # Per-wafer maps (sample up to max_wafers)
    ens_by_xy = {(p["x"], p["y"]): p["mean"] for p in ensemble}
    wafer_ids = df[req.wafer_col].cast(pl.Utf8, strict=False).drop_nulls().unique().to_list()
    wafer_ids = wafer_ids[: req.max_wafers]
    wafers = []
    for w in wafer_ids:
        sub = df.filter(pl.col(req.wafer_col).cast(pl.Utf8, strict=False) == str(w))
        shots = []
        devsum = 0; devn = 0
        for r in sub.group_by([req.shot_x_col, req.shot_y_col]).agg(
            pl.col(req.value_col).cast(pl.Float64, strict=False).mean().alias("v")
        ).to_dicts():
            v = r.get("v")
            x = r[req.shot_x_col]; y = r[req.shot_y_col]
            if v is None or x is None or y is None:
                continue
            shots.append({"x": int(x), "y": int(y), "value": round(float(v), 4)})
            em = ens_by_xy.get((int(x), int(y)))
            if em is not None:
                devsum += abs(float(v) - em); devn += 1
        wafers.append({
            "wafer": str(w),
            "shots": shots,
            "deviation_score": round(devsum / devn, 4) if devn else 0,
        })
    # Sort wafers by deviation (best-aligned first → worst last)
    wafers.sort(key=lambda w: w["deviation_score"])

    return {
        "value_col": req.value_col,
        "total_rows": df.height,
        "ensemble": ensemble,
        "consistency": round(consistency, 4),
        "pattern": pattern,
        "wafers": wafers,
        "wafer_count": len(wafer_ids),
        "note": (
            f"Wafer-map consistency {consistency*100:.1f}%; pattern: {label}. "
            f"Center − edge = {pattern['center_minus_edge']}; radial slope {pattern['radial_slope']}. "
            f"Wafers sorted by ensemble-deviation (top = most typical)."
        ),
    }


class InlineCorrReq(BaseModel):
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    target: str = ""                      # ET target column (e.g. VTH or derived ET_IDX)
    inline_features: List[str] = []       # restrict to these; empty = auto-detect INLINE_*
    max_pairs: int = 50                   # how many pair combinations to try
    top_k: int = 25
    filter_expr: str = ""
    # WF map bonus
    wafer_col: str = "LOT_WF"
    shot_x_col: str = "SHOT_X"
    shot_y_col: str = "SHOT_Y"
    use_wf_map_bonus: bool = True


def _pair_expressions():
    """Return list of (label, polars_expr_builder(a, b)). Simple 2-combo transforms."""
    return [
        ("ratio",     lambda a, b: a / (b + 1e-9)),
        ("diff",      lambda a, b: a - b),
        ("sum",       lambda a, b: a + b),
        ("product",   lambda a, b: a * b),
        ("abs_diff",  lambda a, b: (a - b).abs()),
    ]


def _quick_corr(df: pl.DataFrame, col_a: str, col_b: str):
    """Pearson correlation between two numeric columns (dropna pair)."""
    try:
        a = df[col_a].cast(pl.Float64, strict=False)
        b = df[col_b].cast(pl.Float64, strict=False)
        mask = a.is_not_null() & b.is_not_null()
        if mask.sum() < 3:
            return 0.0, 0
        c = pl.DataFrame({"a": a.filter(mask), "b": b.filter(mask)}) \
              .select(pl.corr("a", "b")).item()
        if c is None or (isinstance(c, float) and (c != c)):
            return 0.0, int(mask.sum())
        return float(c), int(mask.sum())
    except Exception:
        return 0.0, 0


def _shot_map_label(df: pl.DataFrame, col: str, sx: str, sy: str) -> str:
    """Compact pattern label (uniform/center-hot/edge-hot/radial/tilt) — matches /wf_map."""
    if col not in df.columns or sx not in df.columns or sy not in df.columns:
        return "n/a"
    try:
        ens = df.group_by([sx, sy]).agg(
            pl.col(col).cast(pl.Float64, strict=False).mean().alias("m")
        ).drop_nulls("m").to_dicts()
        if len(ens) < 5: return "sparse"
        means = [r["m"] for r in ens]
        gm = sum(means) / len(means)
        center = [r["m"] for r in ens if (r[sx] ** 2 + r[sy] ** 2) ** 0.5 <= 1.5]
        edge   = [r["m"] for r in ens if (r[sx] ** 2 + r[sy] ** 2) ** 0.5 >= 2.0]
        cm = sum(center) / len(center) if center else gm
        em = sum(edge) / len(edge) if edge else gm
        scale = abs(gm) or 1
        if abs(cm - em) > 0.1 * scale:
            return "center-hot" if cm > em else "edge-hot"
        return "uniform"
    except Exception:
        return "n/a"


@router.post("/inline_corr_search")
def inline_corr_search(req: InlineCorrReq):
    """Scan INLINE features (singles + pair combinations) for correlation with an ET target.

    Returns ranked list:
      score = |pearson_corr| + 0.15 * WF_map_pattern_match (if enabled)

    Best candidates have strong corr AND consistent wafer-map pattern with the target.
    Report also includes each candidate's shot-map pattern label for interpretability.
    """
    if not req.target:
        raise HTTPException(400, "target required")
    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.target not in df.columns:
        raise HTTPException(400, f"target '{req.target}' not found")
    if req.filter_expr.strip():
        try:
            df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")
    # Auto-detect INLINE features if not specified
    feats = req.inline_features or [c for c in df.columns if c.startswith("INLINE")]
    feats = [c for c in feats if c != req.target and c in df.columns]
    if not feats:
        raise HTTPException(400, "no INLINE features found — specify inline_features or use an ML_TABLE source")

    # Target pattern on WF map
    tgt_pattern = _shot_map_label(df, req.target, req.shot_x_col, req.shot_y_col) if req.use_wf_map_bonus else "n/a"

    candidates = []
    # ── singles ──
    for f in feats:
        c, n = _quick_corr(df, f, req.target)
        if n < 5:
            continue
        f_pat = _shot_map_label(df, f, req.shot_x_col, req.shot_y_col) if req.use_wf_map_bonus else "n/a"
        map_match = 1.0 if (f_pat == tgt_pattern and f_pat not in ("n/a", "sparse")) else 0.0
        score = abs(c) + (0.15 * map_match if req.use_wf_map_bonus else 0)
        candidates.append({
            "kind": "single",
            "expr": f,
            "corr": round(c, 4), "n": n,
            "tgt_pattern": tgt_pattern, "feat_pattern": f_pat,
            "map_match": bool(map_match), "score": round(score, 4),
        })

    # ── pairs (limit to top features by |corr| to keep combo count bounded) ──
    singles_sorted = sorted(candidates, key=lambda x: abs(x["corr"]), reverse=True)
    top_pool = [c["expr"] for c in singles_sorted[: min(15, len(singles_sorted))]]
    pair_count = 0
    pair_ops = _pair_expressions()
    for i in range(len(top_pool)):
        for j in range(i + 1, len(top_pool)):
            if pair_count >= req.max_pairs:
                break
            a, b = top_pool[i], top_pool[j]
            for op_name, op_fn in pair_ops:
                try:
                    expr_col = "__pair__"
                    tmp = df.with_columns(
                        op_fn(
                            pl.col(a).cast(pl.Float64, strict=False),
                            pl.col(b).cast(pl.Float64, strict=False),
                        ).alias(expr_col)
                    )
                    c, n = _quick_corr(tmp, expr_col, req.target)
                    if n < 5:
                        continue
                    f_pat = _shot_map_label(tmp, expr_col, req.shot_x_col, req.shot_y_col) if req.use_wf_map_bonus else "n/a"
                    map_match = 1.0 if (f_pat == tgt_pattern and f_pat not in ("n/a", "sparse")) else 0.0
                    score = abs(c) + (0.15 * map_match if req.use_wf_map_bonus else 0)
                    candidates.append({
                        "kind": "pair",
                        "expr": f"{op_name}({a}, {b})",
                        "a": a, "b": b, "op": op_name,
                        "corr": round(c, 4), "n": n,
                        "tgt_pattern": tgt_pattern, "feat_pattern": f_pat,
                        "map_match": bool(map_match), "score": round(score, 4),
                    })
                except Exception:
                    pass
                pair_count += 1
                if pair_count >= req.max_pairs:
                    break
        if pair_count >= req.max_pairs:
            break

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return {
        "target": req.target,
        "target_pattern": tgt_pattern,
        "singles_tested": len(feats),
        "pairs_tested": pair_count,
        "top": candidates[: req.top_k],
        "note": (
            f"Ranked by |corr| + 0.15×pattern_match. Target WF-map pattern: {tgt_pattern}. "
            f"Pair operators: {[n for n, _ in pair_ops]}. "
            "Strong corr + matching pattern = highest priority candidates."
        ),
    }


class ParetoReq(BaseModel):
    source_type: str = "flat"
    root: str = "ML_TABLE"
    product: str = "PRODUCT_A"
    file: str = ""
    performance_col: str = ""      # e.g. ET_VTH or any performance metric
    yield_col: str = "FAB_YIELD"
    group_cols: List[str] = []     # KNOB columns for grouping
    filter_expr: str = ""
    higher_is_better_perf: bool = True
    higher_is_better_yield: bool = True


@router.post("/pareto")
def pareto(req: ParetoReq):
    """Performance vs Yield Pareto frontier analysis.

    Goal: find KNOB splits (or wafers) in the upper-right quadrant — high performance AND high yield.

    Returns:
      - points[]: {group, perf_mean, perf_std, yield_mean, yield_std, n, is_pareto}
      - frontier[]: subset of points that are Pareto-optimal
      - recommendation: plain-language summary of the dominant split(s)
    """
    if not req.performance_col or not req.yield_col:
        raise HTTPException(400, "performance_col + yield_col required")

    df = _load_ml_data(req.source_type, req.root, req.product, req.file)
    if req.performance_col not in df.columns:
        raise HTTPException(400, f"performance_col '{req.performance_col}' not found")
    if req.yield_col not in df.columns:
        raise HTTPException(400, f"yield_col '{req.yield_col}' not found")
    if req.filter_expr.strip():
        try:
            df = df.filter(pl.sql_expr(req.filter_expr))
        except Exception as e:
            raise HTTPException(400, f"Filter error: {e}")
    if df.height < 5:
        raise HTTPException(400, "Not enough rows")

    # Default grouping: all KNOB columns
    grp_cols = req.group_cols or [c for c in df.columns if c.startswith("KNOB")]
    if not grp_cols:
        # No groups — return single "all" point
        grp_cols = []

    perf = df[req.performance_col].cast(pl.Float64, strict=False)
    yld = df[req.yield_col].cast(pl.Float64, strict=False)

    points = []
    if grp_cols:
        # Aggregate per unique tuple of group values (limit to top 40 groups).
        # Fill nulls with sentinel so null-groups still show up as distinct splits.
        try:
            work = df.with_columns([
                pl.col(c).cast(_STR, strict=False).fill_null("(null)").alias(c) for c in grp_cols
            ])
            agg = work.group_by(grp_cols).agg([
                pl.col(req.performance_col).cast(pl.Float64, strict=False).mean().alias("perf_mean"),
                pl.col(req.performance_col).cast(pl.Float64, strict=False).std().alias("perf_std"),
                pl.col(req.yield_col).cast(pl.Float64, strict=False).mean().alias("yield_mean"),
                pl.col(req.yield_col).cast(pl.Float64, strict=False).std().alias("yield_std"),
                pl.len().alias("n"),
            ]).filter(pl.col("n") >= 3).sort("n", descending=True).head(40).to_dicts()
            for r in agg:
                parts = [f"{k}={r.get(k, '(null)')}" for k in grp_cols]
                group_label = " × ".join(parts)
                pm = r.get("perf_mean"); ym = r.get("yield_mean")
                if pm is None or ym is None:
                    continue
                points.append({
                    "group": group_label,
                    "group_values": {k: r.get(k) for k in grp_cols},
                    "perf_mean": round(float(pm), 4),
                    "perf_std": round(float(r["perf_std"] or 0), 4),
                    "yield_mean": round(float(ym), 4),
                    "yield_std": round(float(r["yield_std"] or 0), 4),
                    "n": int(r["n"]),
                })
        except Exception as e:
            logger.warning(f"Pareto grouping failed: {e}")

    if not points:
        # Single overall point fallback
        points.append({
            "group": "(all)", "group_values": {},
            "perf_mean": round(float(perf.mean() or 0), 4),
            "perf_std": round(float(perf.std() or 0) if perf.len() > 1 else 0, 4),
            "yield_mean": round(float(yld.mean() or 0), 4),
            "yield_std": round(float(yld.std() or 0) if yld.len() > 1 else 0, 4),
            "n": int(perf.drop_nulls().len()),
        })

    # Pareto frontier — a point is non-dominated if no other point is better in BOTH axes
    def better(a, b):
        """Is a strictly better than b in BOTH dimensions (considering higher_is_better)?"""
        p_cmp = (a["perf_mean"] > b["perf_mean"]) if req.higher_is_better_perf else (a["perf_mean"] < b["perf_mean"])
        y_cmp = (a["yield_mean"] > b["yield_mean"]) if req.higher_is_better_yield else (a["yield_mean"] < b["yield_mean"])
        p_eq = (a["perf_mean"] >= b["perf_mean"]) if req.higher_is_better_perf else (a["perf_mean"] <= b["perf_mean"])
        y_eq = (a["yield_mean"] >= b["yield_mean"]) if req.higher_is_better_yield else (a["yield_mean"] <= b["yield_mean"])
        return p_eq and y_eq and (p_cmp or y_cmp)

    frontier = []
    for p in points:
        dominated = any(better(q, p) for q in points if q is not p)
        p["is_pareto"] = not dominated
        if not dominated:
            frontier.append(p)
    # Sort frontier by performance
    frontier.sort(key=lambda r: r["perf_mean"], reverse=req.higher_is_better_perf)

    # Best single point (highest perf+yield normalized sum)
    def score(p):
        # z-normalize within points
        return p["perf_mean"] + p["yield_mean"] * 10  # weight yield higher
    best = max(frontier, key=score) if frontier else None

    rec = ""
    if best:
        rec = (
            f"Pareto-optimal split: {best['group']} — perf={best['perf_mean']:.3f}±{best['perf_std']:.3f}, "
            f"yield={best['yield_mean']:.3f}±{best['yield_std']:.3f} (n={best['n']}). "
            f"{len(frontier)} frontier points / {len(points)} total groups."
        )

    return {
        "points": points,
        "frontier": frontier,
        "best": best,
        "recommendation": rec,
        "performance_col": req.performance_col,
        "yield_col": req.yield_col,
        "group_cols": grp_cols,
        "total_rows": df.height,
    }
