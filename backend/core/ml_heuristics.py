"""core/ml_heuristics.py — engineer-prior heuristic scoring for process ML.

This layer does not replace model importance. It rescales confidence using
domain priors that engineers trust in practice:

- clean split priority within lots
- repeatability across multiple lots
- module-local consistency
- incoming dominance (upstream > downstream)
- known sign priors (expected + / - relationship)
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import polars as pl

from core.domain import classify_column, classify_process_area, target_level
from core.utils import _STR, load_json


DEFAULT_HEURISTICS = {
    "clean_split_weight": 0.35,
    "repeatability_weight": 0.25,
    "incoming_weight": 0.2,
    "module_weight": 0.1,
    "sign_weight": 0.1,
    "related_modules": {
        "PC": ["Gate", "MOL"],
        "Gate": ["PC", "Spacer", "MOL"],
        "Spacer": ["Gate", "S/D Epi"],
        "S/D Epi": ["Spacer", "MOL"],
        "MOL": ["PC", "Gate", "S/D Epi", "BEOL-M1"],
        "BEOL-M1": ["MOL", "BEOL-M2"],
    },
    "sign_priors": [
        {
            "target_pattern": "PC_CA",
            "feature_pattern": "RS",
            "expected_direction": "-",
            "note": "CA/contact resistance should usually decrease when PC_CA improves.",
        },
        {
            "target_pattern": "PC_CA",
            "feature_pattern": "CD",
            "expected_direction": "+",
            "note": "Larger contact-related CD often improves PC_CA under a nominal geometry regime.",
        },
        {
            "target_pattern": "RC",
            "feature_pattern": "RS",
            "expected_direction": "+",
            "note": "Resistance-family metrics usually move together unless geometry regime changed.",
        },
    ],
}


def load_heuristics_config(path: Path) -> dict:
    cfg = load_json(path, DEFAULT_HEURISTICS)
    if not isinstance(cfg, dict):
        return dict(DEFAULT_HEURISTICS)
    out = dict(DEFAULT_HEURISTICS)
    out.update(cfg)
    return out


def _module_of(col: str) -> str | None:
    area = classify_process_area(str(col or ""))
    return area or None


def _match_sign_prior(target: str, feature: str, cfg: dict) -> dict | None:
    t = str(target or "").upper()
    f = str(feature or "").upper()
    for row in cfg.get("sign_priors") or []:
        tp = str(row.get("target_pattern") or "").upper()
        fp = str(row.get("feature_pattern") or "").upper()
        if tp and tp in t and fp and fp in f:
            return row
    return None


def _clean_split_score(df: pl.DataFrame, feature: str) -> tuple[float, dict]:
    if "root_lot_id" not in df.columns or feature not in df.columns:
        return 0.5, {"mode": "neutral"}
    try:
        col = df[feature]
        dtype = str(df.schema.get(feature, ""))
        if not any(tok in dtype for tok in ("String", "Utf8", "Categorical")):
            return 0.5, {"mode": "non_categorical"}
        work = df.select([
            pl.col("root_lot_id").cast(_STR, strict=False).alias("root_lot_id"),
            pl.col(feature).cast(_STR, strict=False).fill_null("(null)").alias(feature),
        ])
        lot_nuniq = work.group_by("root_lot_id").agg(pl.col(feature).n_unique().alias("nuniq"))
        clean_lots = lot_nuniq.filter(pl.col("nuniq") <= 1).height
        total_lots = max(1, lot_nuniq.height)
        clean_ratio = clean_lots / total_lots
        distinct = work.filter(pl.col("root_lot_id").is_in(lot_nuniq.filter(pl.col("nuniq") <= 1)["root_lot_id"])).select(pl.col(feature).n_unique().alias("d")).item()
        diversity = min(1.0, float(distinct or 0) / 2.0)
        return round(clean_ratio * diversity, 4), {
            "mode": "categorical",
            "clean_ratio": round(clean_ratio, 4),
            "distinct_values": int(distinct or 0),
            "lots": total_lots,
        }
    except Exception:
        return 0.5, {"mode": "error"}


def _repeatability_score(df: pl.DataFrame, feature: str, target: str) -> tuple[float, dict]:
    if "root_lot_id" not in df.columns or feature not in df.columns or target not in df.columns:
        return 0.5, {"mode": "neutral"}
    try:
        work = df.select([
            pl.col("root_lot_id").cast(_STR, strict=False).alias("root_lot_id"),
            pl.col(feature).cast(_STR, strict=False).fill_null("(null)").alias(feature),
            pl.col(target).cast(pl.Float64, strict=False).alias(target),
        ]).drop_nulls(target)
        if work.is_empty():
            return 0.5, {"mode": "no_target"}
        per_lot = work.group_by([feature, "root_lot_id"]).agg(pl.col(target).mean().alias("target_mean"))
        global_std = float(per_lot["target_mean"].std() or 0.0)
        if global_std <= 1e-12:
            return 0.7, {"mode": "stable_global"}
        grp = per_lot.group_by(feature).agg([
            pl.col("target_mean").std().alias("within_std"),
            pl.len().alias("lot_count"),
        ]).filter(pl.col("lot_count") >= 2)
        if grp.is_empty():
            return 0.5, {"mode": "insufficient_repeat"}
        mean_within = float(grp["within_std"].fill_null(0).mean() or 0.0)
        score = max(0.0, min(1.0, 1.0 - (mean_within / global_std)))
        return round(score, 4), {
            "mode": "categorical_target_mean",
            "global_std": round(global_std, 4),
            "mean_within_std": round(mean_within, 4),
            "groups": int(grp.height),
        }
    except Exception:
        return 0.5, {"mode": "error"}


def _incoming_score(feature_info: dict, target_info: dict) -> tuple[float, dict]:
    f_lvl = int(feature_info.get("level", -1))
    t_lvl = int(target_info.get("level", -1))
    if f_lvl < 0 or t_lvl < 0:
        return 0.6, {"mode": "unknown_level"}
    if f_lvl > t_lvl:
        return 0.0, {"mode": "downstream_block"}
    base = 0.75 + 0.1 * max(0, t_lvl - f_lvl)
    if feature_info.get("family") == "FAB" and feature_info.get("major", 0) and target_info.get("family") == "FAB":
        f_major = int(feature_info.get("major", 0))
        t_major = int(target_info.get("major", 0))
        if f_major <= t_major:
            dist = max(0, t_major - f_major)
            base *= math.exp(-dist / 8.0)
        else:
            base *= 0.1
    return round(max(0.0, min(1.0, base)), 4), {"mode": "incoming"}


def _module_score(feature: str, target: str, cfg: dict) -> tuple[float, dict]:
    fm = _module_of(feature)
    tm = _module_of(target)
    if not fm or not tm:
        return 0.6, {"feature_module": fm, "target_module": tm, "mode": "unknown"}
    if fm == tm:
        return 1.0, {"feature_module": fm, "target_module": tm, "mode": "same_module"}
    related = set(cfg.get("related_modules", {}).get(tm, []))
    if fm in related:
        return 0.8, {"feature_module": fm, "target_module": tm, "mode": "related_module"}
    return 0.45, {"feature_module": fm, "target_module": tm, "mode": "distant_module"}


def _sign_score(target: str, feature: str, observed_direction: str, cfg: dict) -> tuple[float, dict]:
    prior = _match_sign_prior(target, feature, cfg)
    if not prior:
        return 0.6, {"mode": "no_prior"}
    exp_dir = str(prior.get("expected_direction") or "").strip()
    ok = exp_dir == str(observed_direction or "").strip()
    return (1.0 if ok else 0.0), {
        "mode": "sign_prior",
        "expected_direction": exp_dir,
        "observed_direction": observed_direction,
        "note": prior.get("note") or "",
        "violation": not ok,
    }


def review_features(df: pl.DataFrame, pw_features: list[dict], target: str, cfg: dict) -> dict:
    target_info = classify_column(target)
    out = []
    for row in pw_features or []:
        feat = str(row.get("feature") or "")
        if not feat or feat not in df.columns:
            continue
        clean_score, clean_meta = _clean_split_score(df, feat)
        repeat_score, repeat_meta = _repeatability_score(df, feat, target)
        incoming_score, incoming_meta = _incoming_score(row, target_info)
        module_score, module_meta = _module_score(feat, target, cfg)
        sign_score, sign_meta = _sign_score(target, feat, row.get("direction", "+"), cfg)
        confidence = (
            clean_score * float(cfg.get("clean_split_weight", 0.35))
            + repeat_score * float(cfg.get("repeatability_weight", 0.25))
            + incoming_score * float(cfg.get("incoming_weight", 0.2))
            + module_score * float(cfg.get("module_weight", 0.1))
            + sign_score * float(cfg.get("sign_weight", 0.1))
        )
        raw_imp = float(row.get("importance") or 0.0)
        priority = round(raw_imp * confidence, 4)
        flags = []
        if sign_meta.get("violation"):
            flags.append("sign_violation")
        if clean_meta.get("mode") == "categorical" and float(clean_meta.get("clean_ratio", 0)) < 0.4:
            flags.append("dirty_split")
        if repeat_meta.get("mode") == "categorical_target_mean" and float(repeat_meta.get("mean_within_std", 0)) > float(repeat_meta.get("global_std", 1)):
            flags.append("low_repeatability")
        out.append({
            "feature": feat,
            "family": row.get("family"),
            "module": module_meta.get("feature_module"),
            "importance": raw_imp,
            "confidence": round(confidence, 4),
            "priority": priority,
            "clean_split_score": clean_score,
            "repeatability_score": repeat_score,
            "incoming_score": incoming_score,
            "module_score": module_score,
            "sign_score": sign_score,
            "direction": row.get("direction", "+"),
            "flags": flags,
            "details": {
                "clean": clean_meta,
                "repeatability": repeat_meta,
                "incoming": incoming_meta,
                "module": module_meta,
                "sign": sign_meta,
            },
        })
    out.sort(key=lambda x: (x["priority"], x["confidence"], x["importance"]), reverse=True)
    violations = [r for r in out if "sign_violation" in (r.get("flags") or [])]
    clean = [r for r in out if r.get("clean_split_score", 0) >= 0.7]
    return {
        "features": out,
        "top_reliable": out[:20],
        "sign_violations": violations[:20],
        "clean_split_candidates": clean[:20],
        "note": (
            "Priority = weighted importance × engineer-prior confidence. "
            "Confidence rewards clean split, repeatability across lots, upstream direction, "
            "module plausibility, and expected sign consistency."
        ),
    }
