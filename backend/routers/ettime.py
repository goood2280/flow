import logging
from pathlib import Path
import datetime as dt
import json
import io
import math
import re
import subprocess
import tempfile

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from core.long_pivot import scan_long_et
from core.paths import PATHS
from core.product_config import load as load_product_config
from core.notify import send_to_admins
from core.reformatter import available_report_profiles, filter_rules_for_report, load_rules, apply_rules
from core.utils import _STR, read_one_file, jsonl_append, jsonl_read

router = APIRouter(prefix="/api/ettime", tags=["ettime"])
logger = logging.getLogger("flow.ettime")

ET_FEATURE_FILE = "features_et_wafer.parquet"
STEP_MATCHING_FILES = ("step_matching.csv", "matching_step.csv")
REFORMATTER_BASE = PATHS.data_root / "reformatter"
PROBE_ALERT_STATE = PATHS.data_root / "et_probe_alert_state.json"
ET_DOWNLOAD_LOG = PATHS.data_root / "logs" / "ettime_downloads.jsonl"
ET_REPORT_DIR = PATHS.data_root / "et_reports"
PPTX_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_et_pptx.js"


def _db_root() -> Path:
    try:
        from app_v2.shared.source_adapter import resolve_existing_root
        return resolve_existing_root("db", PATHS.db_root)
    except Exception:
        return PATHS.db_root


def _base_root() -> Path:
    try:
        from app_v2.shared.source_adapter import resolve_existing_root
        return resolve_existing_root("base", PATHS.base_root)
    except Exception:
        return PATHS.base_root


def _et_feature_path() -> Path:
    for root in (_base_root(), _db_root()):
        fp = Path(root) / ET_FEATURE_FILE
        if fp.is_file():
            return fp
    raise HTTPException(404, f"{ET_FEATURE_FILE} not found")


def _matching_step_paths() -> list[Path]:
    out: list[Path] = []
    for root in (_base_root(), _db_root()):
        for name in STEP_MATCHING_FILES:
            fp = Path(root) / name
            if fp.is_file() and fp not in out:
                out.append(fp)
    return out


def _product_aliases(product: str) -> set[str]:
    raw = str(product or "").strip()
    if not raw:
        return set()
    out = {raw.upper()}
    if raw.upper().startswith("ML_TABLE_"):
        raw = raw[len("ML_TABLE_"):].strip()
        out.add(raw.upper())
    if raw.upper() == "PRODA":
        out.update({"PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif raw.upper() == "PRODA0":
        out.update({"PRODA", "PRODUCT_A0"})
    elif raw.upper() == "PRODA1":
        out.update({"PRODA", "PRODUCT_A1"})
    elif raw.upper().startswith("PRODUCT_A"):
        if raw.upper().endswith("0"):
            out.update({"PRODA", "PRODA0", "PRODUCT_A0"})
        elif raw.upper().endswith("1"):
            out.update({"PRODA", "PRODA1", "PRODUCT_A1"})
        else:
            out.update({"PRODA", "PRODA0", "PRODA1", "PRODUCT_A0", "PRODUCT_A1"})
    elif raw.upper() == "PRODB":
        out.update({"PRODUCT_B"})
    elif raw.upper().startswith("PRODUCT_B"):
        out.update({"PRODB", "PRODUCT_B"})
    return out


def _load_et_features() -> pl.DataFrame:
    df = read_one_file(_et_feature_path())
    if df is None or df.is_empty():
        raise HTTPException(400, "ET feature dataset is empty")
    key_cols = [c for c in ("lot_id", "root_lot_id", "wafer_id", "product", "pgm", "eqp", "chamber", "start_ts", "end_ts") if c in df.columns]
    cast_cols = [pl.col(c).cast(_STR, strict=False) for c in key_cols]
    if cast_cols:
        df = df.with_columns(cast_cols)
    return df


def _collect_raw_et(product: str) -> pl.DataFrame:
    for cand in [product, *_product_aliases(product)]:
        lf = scan_long_et(cand, _db_root())
        if lf is None:
            continue
        try:
            cols = lf.collect_schema().names()
        except Exception:
            cols = []
        keep = [c for c in (
            "product", "root_lot_id", "lot_id", "wafer_id", "step_id", "step_seq",
            "process_id", "flat_zone", "flat", "probe_card", "time", "tkin_time", "tkout_time",
            "item_id", "shot_x", "shot_y", "value",
            "request_id", "request_no", "measure_group_id", "job_id", "eqp_id", "eqp", "chamber",
            "fab_lot_id",
        ) if c in cols]
        if not keep:
            continue
        df = lf.select(keep).collect()
        cast_cols = [pl.col(c).cast(_STR, strict=False) for c in (
            "product", "root_lot_id", "lot_id", "fab_lot_id", "wafer_id", "process_id",
            "step_id", "step_seq", "flat_zone", "flat", "probe_card", "time",
            "request_id", "request_no", "measure_group_id", "job_id", "eqp_id", "eqp", "chamber",
        ) if c in df.columns]
        return df.with_columns(cast_cols) if cast_cols else df
    return pl.DataFrame()


def _add_et_report_keys(df: pl.DataFrame) -> pl.DataFrame:
    """Normalize ET identity columns for report views.

    `measurement_key` keeps the raw request/measure identity, while
    `package_key` is the row key users expect in ET Report: product + root lot
    + fab lot + ET step. Different step_seq values are aggregated below.
    """
    if df.is_empty():
        return df
    cols = set(df.columns)
    exprs: list[pl.Expr] = []
    if "fab_lot_id" not in cols:
        exprs.append((pl.col("lot_id") if "lot_id" in cols else pl.lit("")).cast(_STR, strict=False).alias("fab_lot_id"))
    for c in ("product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id", "step_id", "step_seq"):
        if c in cols:
            exprs.append(pl.col(c).cast(_STR, strict=False).fill_null("").alias(c))
        elif c != "fab_lot_id":
            exprs.append(pl.lit("").alias(c))
    if exprs:
        df = df.with_columns(exprs)

    time_exprs = []
    for c in ("tkout_time", "time", "tkin_time"):
        if c in df.columns:
            time_exprs.append(pl.col(c).cast(_STR, strict=False))
    req_exprs = []
    for c in ("request_id", "measure_group_id", "request_no", "job_id"):
        if c in df.columns:
            req_exprs.append(pl.col(c).cast(_STR, strict=False))

    measurement_key = (
        pl.when((pl.col("request_id").cast(_STR, strict=False).fill_null("") != "") if "request_id" in df.columns else pl.lit(False))
        .then(pl.lit("REQ|") + pl.col("request_id").cast(_STR, strict=False).fill_null(""))
        .when((pl.col("measure_group_id").cast(_STR, strict=False).fill_null("") != "") if "measure_group_id" in df.columns else pl.lit(False))
        .then(pl.lit("MEAS|") + pl.col("measure_group_id").cast(_STR, strict=False).fill_null(""))
        .otherwise(
            pl.lit("SEQ|") +
            pl.col("step_id").fill_null("") + pl.lit("|") +
            pl.col("step_seq").fill_null("") + pl.lit("|") +
            ((pl.col("tkout_time").cast(_STR, strict=False).fill_null("")) if "tkout_time" in df.columns else pl.lit(""))
        )
        .alias("measurement_key")
    )
    lot_step_key = pl.concat_str([
        pl.lit("LOTSTEP|"),
        pl.col("product").fill_null(""),
        pl.lit("|"),
        pl.col("root_lot_id").fill_null(""),
        pl.lit("|"),
        pl.col("fab_lot_id").fill_null(""),
        pl.lit("|"),
        pl.col("step_id").fill_null(""),
    ]).alias("package_key")
    return df.with_columns([
        (pl.coalesce(time_exprs) if time_exprs else pl.lit("")).fill_null("").alias("package_time"),
        (pl.coalesce(req_exprs) if req_exprs else pl.lit("")).fill_null("").alias("request_key"),
        measurement_key,
        lot_step_key,
    ])


def _join_unique(values, limit: int = 8) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        s = str(v or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    if len(out) > limit:
        return ", ".join(out[:limit]) + f" +{len(out) - limit}"
    return ", ".join(out)


def _read_probe_state() -> dict:
    try:
        if PROBE_ALERT_STATE.is_file():
            return json.loads(PROBE_ALERT_STATE.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return {}


def _write_probe_state(state: dict) -> None:
    try:
        PROBE_ALERT_STATE.parent.mkdir(parents=True, exist_ok=True)
        PROBE_ALERT_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _normalize_probe_rules(product: str) -> dict:
    cfg = load_product_config(PATHS.data_root, product) if product else {}
    probe = cfg.get("probe_card_watch") if isinstance(cfg, dict) else {}
    if not isinstance(probe, dict):
        probe = {}
    items = []
    for raw in probe.get("items") or []:
        if not isinstance(raw, dict):
            continue
        items.append({
            "item_id": str(raw.get("item_id") or "").strip(),
            "alias": str(raw.get("alias") or raw.get("item_id") or "").strip(),
            "step_ids": [str(v).strip() for v in (raw.get("step_ids") or []) if str(v).strip()],
            "step_seqs": [str(v).strip() for v in (raw.get("step_seqs") or []) if str(v).strip()],
            "spec": str(raw.get("spec") or "none").strip().lower(),
            "lsl": raw.get("lsl"),
            "usl": raw.get("usl"),
            "severity": str(raw.get("severity") or "warn").strip().lower(),
        })
    return {
        "enabled": bool(probe.get("enabled", bool(items))),
        "notify_admin": bool(probe.get("notify_admin", True)),
        "items": [x for x in items if x.get("item_id")],
    }


def _probe_watch_report(df: pl.DataFrame, product: str) -> dict:
    rules = _normalize_probe_rules(product)
    out = {
        "enabled": bool(rules.get("enabled")),
        "notify_admin": bool(rules.get("notify_admin")),
        "flagged_packages": 0,
        "critical_packages": 0,
        "latest_package_time": "",
        "package_summary": [],
        "latest_flags": [],
        "selected_package": None,
    }
    if not out["enabled"] or df.is_empty() or "item_id" not in df.columns:
        return out
    flagged = []
    for rule in rules.get("items") or []:
        sub = df.filter(pl.col("item_id") == rule["item_id"])
        if rule.get("step_ids") and "step_id" in sub.columns:
            sub = sub.filter(pl.col("step_id").is_in(rule["step_ids"]))
        if rule.get("step_seqs") and "step_seq" in sub.columns:
            sub = sub.filter(pl.col("step_seq").is_in(rule["step_seqs"]))
        if sub.is_empty():
            continue
        grp = (
            sub.group_by(["package_key", "product", "root_lot_id", "fab_lot_id", "step_id", "step_seq", "package_time"])
            .agg([
                pl.col("value").cast(pl.Float64, strict=False).mean().alias("mean_value"),
                pl.col("value").cast(pl.Float64, strict=False).max().alias("max_value"),
                pl.col("value").cast(pl.Float64, strict=False).min().alias("min_value"),
                pl.len().alias("pt_count"),
            ])
        )
        for row in grp.to_dicts():
            mean_v = row.get("mean_value")
            if mean_v is None:
                continue
            bad = False
            if rule["spec"] in {"both", "lsl"} and rule.get("lsl") is not None and float(mean_v) < float(rule["lsl"]):
                bad = True
            if rule["spec"] in {"both", "usl"} and rule.get("usl") is not None and float(mean_v) > float(rule["usl"]):
                bad = True
            if not bad:
                continue
            flagged.append({
                "package_key": str(row.get("package_key") or ""),
                "product": str(row.get("product") or ""),
                "root_lot_id": str(row.get("root_lot_id") or ""),
                "fab_lot_id": str(row.get("fab_lot_id") or ""),
                "step_id": str(row.get("step_id") or ""),
                "step_seq": str(row.get("step_seq") or ""),
                "package_time": str(row.get("package_time") or ""),
                "item_id": rule["item_id"],
                "alias": rule["alias"],
                "severity": rule["severity"],
                "spec": rule["spec"],
                "lsl": rule.get("lsl"),
                "usl": rule.get("usl"),
                "mean_value": round(float(mean_v), 5),
                "max_value": round(float(row.get("max_value") or mean_v), 5),
                "min_value": round(float(row.get("min_value") or mean_v), 5),
                "pt_count": int(row.get("pt_count") or 0),
            })
    if not flagged:
        return out
    flagged = sorted(flagged, key=lambda x: (x.get("package_time") or "", x.get("severity") == "critical"), reverse=True)
    by_pkg: dict[str, dict] = {}
    for row in flagged:
        pkg = row["package_key"]
        cur = by_pkg.setdefault(pkg, {
            "package_key": pkg,
            "product": row["product"],
            "root_lot_id": row["root_lot_id"],
            "fab_lot_id": row["fab_lot_id"],
            "step_id": row["step_id"],
            "package_time": row["package_time"],
            "health_status": "warn",
            "flagged_count": 0,
            "critical_count": 0,
            "step_seqs": [],
            "bad_items": [],
        })
        cur["flagged_count"] += 1
        if row["severity"] == "critical":
            cur["critical_count"] += 1
            cur["health_status"] = "critical"
        if row["step_seq"] and row["step_seq"] not in cur["step_seqs"]:
            cur["step_seqs"].append(row["step_seq"])
        cur["bad_items"].append({
            "alias": row["alias"],
            "item_id": row["item_id"],
            "step_seq": row["step_seq"],
            "mean_value": row["mean_value"],
            "lsl": row["lsl"],
            "usl": row["usl"],
            "severity": row["severity"],
            "pt_count": row["pt_count"],
        })
    pkg_rows = sorted(by_pkg.values(), key=lambda x: (x.get("package_time") or "", x.get("critical_count") or 0), reverse=True)
    out.update({
        "flagged_packages": len(pkg_rows),
        "critical_packages": sum(1 for x in pkg_rows if x.get("critical_count")),
        "latest_package_time": pkg_rows[0].get("package_time") or "",
        "package_summary": pkg_rows[:20],
        "latest_flags": flagged[:30],
    })
    return out


def _maybe_notify_probe_watch(product: str, probe_watch: dict) -> None:
    if not probe_watch.get("enabled") or not probe_watch.get("notify_admin"):
        return
    packages = probe_watch.get("package_summary") or []
    if not packages:
        return
    state = _read_probe_state()
    sent = set(state.get("sent") or [])
    changed = False
    for pkg in packages[:5]:
        if not pkg.get("critical_count"):
            continue
        key = f"{product}|{pkg.get('package_key')}"
        if key in sent:
            continue
        items = ", ".join(
            f"{x.get('alias') or x.get('item_id')}@seq{x.get('step_seq') or '-'}"
            for x in (pkg.get("bad_items") or [])[:4]
        )
        title = f"ET Probe Card Warning · {pkg.get('fab_lot_id') or pkg.get('root_lot_id') or '-'} · {pkg.get('step_id') or '-'}"
        body = (
            f"Probe card check abnormal before ET root-cause review.\n"
            f"product={product}\n"
            f"root_lot_id={pkg.get('root_lot_id') or '-'}\n"
            f"fab_lot_id={pkg.get('fab_lot_id') or '-'}\n"
            f"step_id={pkg.get('step_id') or '-'}\n"
            f"step_seq={','.join(pkg.get('step_seqs') or []) or '-'}\n"
            f"bad_items={items or '-'}"
        )
        send_to_admins(title, body, "warn")
        sent.add(key)
        changed = True
    if changed:
        state["sent"] = sorted(sent)
        _write_probe_state(state)


def _step_search_ids(product: str, query: str) -> tuple[list[str], str]:
    raw = str(query or "").strip()
    if not raw:
        return [], ""
    paths = _matching_step_paths()
    if not paths:
        return [raw], raw
    frames = []
    base_cols = ["product", "raw_step_id", "step_id", "canonical_step", "area", "module", "function_step", "func_step"]
    for fp in paths:
        try:
            frame = pl.read_csv(fp, infer_schema_length=500)
        except Exception:
            continue
        if frame.is_empty():
            continue
        for col in base_cols:
            if col not in frame.columns:
                frame = frame.with_columns(pl.lit("").alias(col))
        frame = frame.with_columns([
            pl.when(pl.col("raw_step_id").cast(_STR, strict=False).str.strip_chars() != "")
            .then(pl.col("raw_step_id").cast(_STR, strict=False))
            .otherwise(pl.col("step_id").cast(_STR, strict=False))
            .alias("raw_step_id"),
            pl.when(pl.col("function_step").cast(_STR, strict=False).str.strip_chars() != "")
            .then(pl.col("function_step").cast(_STR, strict=False))
            .when(pl.col("func_step").cast(_STR, strict=False).str.strip_chars() != "")
            .then(pl.col("func_step").cast(_STR, strict=False))
            .otherwise(pl.col("canonical_step").cast(_STR, strict=False))
            .alias("function_step"),
            pl.when(pl.col("area").cast(_STR, strict=False).str.strip_chars() != "")
            .then(pl.col("area").cast(_STR, strict=False))
            .otherwise(pl.col("module").cast(_STR, strict=False))
            .alias("area"),
        ]).select(["product", "raw_step_id", "canonical_step", "area", "function_step"])
        frames.append(frame)
    if not frames:
        return [raw], raw
    try:
        df = pl.concat(frames, how="vertical_relaxed")
    except Exception:
        return [raw], raw
    if df.is_empty():
        return [raw], raw
    aliases = _product_aliases(product)
    if "product" in df.columns and aliases:
        df = df.filter(pl.col("product").cast(_STR, strict=False).str.to_uppercase().is_in(sorted(aliases)))
    if df.is_empty():
        return [raw], raw
    for col in ("raw_step_id", "canonical_step", "area", "function_step"):
        if col not in df.columns:
            df = df.with_columns(pl.lit("").alias(col))
    q = raw.upper()
    matched = df.filter(
        pl.any_horizontal([
            pl.col("raw_step_id").cast(_STR, strict=False).str.to_uppercase() == q,
            pl.col("canonical_step").cast(_STR, strict=False).str.to_uppercase().str.contains(q, literal=True),
            pl.col("area").cast(_STR, strict=False).str.to_uppercase() == q,
            pl.col("function_step").cast(_STR, strict=False).str.to_uppercase() == q,
        ])
    )
    if matched.is_empty():
        return [raw], raw
    ids = [str(v) for v in matched["raw_step_id"].drop_nulls().unique().to_list() if str(v).strip()]
    label = raw
    try:
        canon = next((str(v) for v in matched["canonical_step"].drop_nulls().unique().to_list() if str(v).strip()), "")
        area = next((str(v) for v in matched["area"].drop_nulls().unique().to_list() if str(v).strip()), "")
        func = next((str(v) for v in matched["function_step"].drop_nulls().unique().to_list() if str(v).strip()), "")
        label = func or area or canon or raw
    except Exception:
        pass
    return ids, label


def _load_reporting_rules(product: str) -> list[dict]:
    for cand in [product, *_product_aliases(product)]:
        if not str(cand or "").strip():
            continue
        rules = load_rules(REFORMATTER_BASE, str(cand))
        if rules:
            return rules
    return []


def _selected_reporting_rules(product: str, report_variant: str, point_mode: str, audience: str) -> list[dict]:
    rules = _load_reporting_rules(product) if product else []
    selected = filter_rules_for_report(rules, report_variant=report_variant, point_mode=point_mode)
    if audience in {"internal", "external"}:
        selected = [r for r in selected if (r.get("report_audience") in (audience, "both"))]
    return selected


def _to_float(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        out = float(v)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _rule_label(rule: dict) -> str:
    return str(rule.get("alias") or rule.get("name") or rule.get("rawitem_id") or "").strip()


def _raw_item_ids_for_rule(rule: dict) -> list[str]:
    out: list[str] = []
    item_map = rule.get("item_map")
    if isinstance(item_map, dict):
        out.extend(str(v).strip() for v in item_map.values() if str(v).strip())
    raw = str(rule.get("rawitem_id") or "").strip()
    if raw:
        out.extend(x.strip() for x in re.split(r"[/,;|]+", raw) if x.strip())
    flt = str(rule.get("filter") or "")
    for m in re.finditer(r"(?i)\bitem_id\s*==\s*['\"]([^'\"]+)['\"]", flt):
        out.append(m.group(1).strip())
    seen = set()
    clean = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            clean.append(item)
    return clean


def _pseudo_rule_for_item(item_id: str) -> dict:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", str(item_id or "").strip()).strip("_") or "ITEM"
    escaped = str(item_id or "").replace("'", "\\'")
    return {
        "name": f"RAW_{safe}",
        "type": "scale_abs",
        "source_col": "value",
        "filter": f"item_id == '{escaped}'",
        "scale": 1.0,
        "abs": False,
        "offset": 0.0,
        "rawitem_id": item_id,
        "alias": item_id,
        "report_order": 9999,
        "y_axis": "linear",
        "spec": "none",
        "use": True,
    }


def _top_item_rules(df: pl.DataFrame, max_items: int = 12) -> list[dict]:
    if df.is_empty() or "item_id" not in df.columns:
        return []
    item_counts = (
        df.group_by("item_id")
        .agg(pl.len().alias("n"))
        .sort(["n", "item_id"], descending=[True, False])
        .head(max(1, min(40, int(max_items or 12))))
    )
    return [_pseudo_rule_for_item(str(v)) for v in item_counts["item_id"].to_list() if str(v).strip()]


def _prepare_report_rules(df: pl.DataFrame, rules: list[dict]) -> list[dict]:
    prepared = []
    for raw in rules or []:
        rule = dict(raw or {})
        if not rule.get("name") and rule.get("rawitem_id"):
            rule["name"] = f"RAW_{re.sub(r'[^A-Za-z0-9_]+', '_', str(rule.get('rawitem_id')))}"
        if rule.get("type") == "shot_formula":
            group_by = list(rule.get("group_by") or [
                "product", "root_lot_id", "fab_lot_id", "lot_id", "wafer_id",
                "step_id", "step_seq", "shot_x", "shot_y",
            ])
            for col in ("package_key", "request_key", "package_time"):
                if col in df.columns and col not in group_by:
                    group_by.append(col)
            rule["group_by"] = [c for c in group_by if c in df.columns]
        prepared.append(rule)
    return prepared


def _apply_report_rules(df: pl.DataFrame, rules: list[dict]) -> tuple[pl.DataFrame, list[dict]]:
    prepared = _prepare_report_rules(df, rules)
    if not prepared:
        return df, []
    try:
        return apply_rules(df, prepared, enabled_only=True), prepared
    except Exception as exc:
        logger.warning("ET report rule application failed: %s", exc)
        return df, prepared


def _rule_value_df(applied: pl.DataFrame, rule: dict) -> pl.DataFrame:
    name = str(rule.get("name") or "").strip()
    if not name or name not in applied.columns:
        return pl.DataFrame()
    keep = [c for c in (
        "package_key", "package_time", "request_key", "product", "root_lot_id",
        "fab_lot_id", "lot_id", "wafer_id", "step_id", "step_seq",
        "shot_x", "shot_y", "item_id",
    ) if c in applied.columns]
    sub = (
        applied.select(keep + [pl.col(name).cast(pl.Float64, strict=False).alias("_value")])
        .drop_nulls("_value")
    )
    if sub.is_empty():
        return sub
    # shot_formula values are attached back to every source-item row in the group;
    # keep one derived point per package/wafer/shot/seq/value.
    if rule.get("type") == "shot_formula":
        uniq = [c for c in (
            "package_key", "wafer_id", "step_id", "step_seq", "shot_x", "shot_y", "_value"
        ) if c in sub.columns]
        if uniq:
            sub = sub.unique(subset=uniq, maintain_order=True)
    return sub


def _spec_out_expr(rule: dict) -> pl.Expr:
    spec = str(rule.get("spec") or "none").lower()
    lsl = _to_float(rule.get("lsl"))
    usl = _to_float(rule.get("usl"))
    cond = pl.lit(False)
    if spec in {"lsl", "both"} and lsl is not None:
        cond = cond | (pl.col("_value") < pl.lit(lsl))
    if spec in {"usl", "both"} and usl is not None:
        cond = cond | (pl.col("_value") > pl.lit(usl))
    return cond


def _seq_points_for_sub(sub: pl.DataFrame) -> dict[str, list[dict]]:
    if sub.is_empty() or "package_key" not in sub.columns or "step_seq" not in sub.columns:
        return {}
    out: dict[str, list[dict]] = {}
    rows = (
        sub.group_by(["package_key", "step_seq"])
        .agg(pl.len().alias("pt_count"))
        .sort(["package_key", "step_seq"])
        .to_dicts()
    )
    for row in rows:
        pkg = str(row.get("package_key") or "")
        seq = str(row.get("step_seq") or "-")
        pts = int(row.get("pt_count") or 0)
        if pkg and pts > 0:
            out.setdefault(pkg, []).append({"step_seq": seq, "pt_count": pts})
    return out


def _seq_points_label(rows: list[dict]) -> str:
    parts = []
    for row in rows or []:
        pts = int(row.get("pt_count") or 0)
        if pts <= 0:
            continue
        seq = str(row.get("step_seq") or "-")
        parts.append(f"{seq}({pts}pt)")
    return ", ".join(parts) or "-"


def _spec_status(rule: dict, mean_v: float | None, out_pts: int) -> str:
    if out_pts > 0:
        return "abnormal"
    if mean_v is None:
        return "missing"
    if str(rule.get("spec") or "none").lower() in {"none", ""}:
        return "reported"
    return "normal"


def _package_report_views(df: pl.DataFrame, rules: list[dict], recent_rows: list[dict], probe_watch: dict) -> tuple[list[dict], dict[str, dict]]:
    archive = []
    details_by_pkg: dict[str, dict] = {}
    if df.is_empty():
        return archive, details_by_pkg
    scoreboard_rules = [r for r in (rules or []) if bool(r.get("use", True))]
    applied, prepared_rules = _apply_report_rules(df, scoreboard_rules)
    rule_rows = []
    for r in prepared_rules:
        alias = _rule_label(r)
        name = str(r.get("name") or "").strip()
        if not alias or not name:
            continue
        sub = _rule_value_df(applied, r)
        if sub.is_empty():
            continue
        seq_map = _seq_points_for_sub(sub)
        grouped = (
            sub.group_by(["package_key"])
            .agg([
                pl.col("_value").mean().alias("mean_value"),
                pl.col("_value").min().alias("min_value"),
                pl.col("_value").max().alias("max_value"),
                pl.len().alias("pt_count"),
                _spec_out_expr(r).cast(pl.Int64).sum().alias("spec_out_points"),
            ])
        )
        for row in grouped.to_dicts():
            pkg = str(row.get("package_key") or "")
            if not pkg:
                continue
            mean_v = row.get("mean_value")
            out_pts = int(row.get("spec_out_points") or 0)
            seq_points = seq_map.get(pkg) or []
            rule_rows.append({
                "package_key": pkg,
                "alias": alias,
                "rawitem_id": "/".join(_raw_item_ids_for_rule(r)) or str(r.get("rawitem_id") or name),
                "report_order": int(r.get("report_order") or 9999),
                "cat": str(r.get("cat") or ""),
                "y_axis": str(r.get("y_axis") or "linear"),
                "spec": str(r.get("spec") or "none"),
                "use": bool(r.get("use")),
                "mean_value": round(float(mean_v), 6) if mean_v is not None else None,
                "min_value": round(float(row.get("min_value")), 6) if row.get("min_value") is not None else None,
                "max_value": round(float(row.get("max_value")), 6) if row.get("max_value") is not None else None,
                "pt_count": int(row.get("pt_count") or 0),
                "spec_out_points": out_pts,
                "lsl": r.get("lsl"),
                "usl": r.get("usl"),
                "status": _spec_status(r, mean_v, out_pts),
                "step_seq_points": _seq_points_label(seq_points),
                "step_seq_point_rows": seq_points,
            })
    per_pkg: dict[str, list[dict]] = {}
    for row in sorted(rule_rows, key=lambda x: (x["package_key"], x["report_order"], x["alias"])):
        per_pkg.setdefault(row["package_key"], []).append(row)
    probe_map = {str(x.get("package_key") or ""): x for x in (probe_watch.get("package_summary") or [])}
    for row in recent_rows:
        pkg = str(row.get("package_key") or "")
        items = per_pkg.get(pkg, [])
        abnormal = [x for x in items if int(x.get("spec_out_points") or 0) > 0]
        missing = max(0, len(scoreboard_rules) - len(items))
        probe = probe_map.get(pkg)
        health = "ok"
        if probe and probe.get("health_status") == "critical":
            health = "critical"
        elif probe and probe.get("health_status") == "warn":
            health = "warn"
        elif abnormal:
            health = "warn"
        archive.append({
            "package_key": pkg,
            "product": row.get("product") or "",
            "root_lot_id": row.get("root_lot_id") or "",
            "fab_lot_id": row.get("fab_lot_id") or "",
            "step_id": row.get("step_id") or "",
            "step_seq_combo": row.get("step_seq_combo") or "-",
            "package_time": row.get("package_time") or "",
            "request_key": row.get("request_key") or "",
            "health_status": health,
            "reported_items": len(items),
            "abnormal_items": len(abnormal),
            "spec_out_points": int(sum(int(x.get("spec_out_points") or 0) for x in abnormal)),
            "missing_items": missing,
            "top_flags": ", ".join((x.get("alias") or x.get("rawitem_id") or "") for x in abnormal[:4]) or (probe.get("bad_items")[0].get("alias") if probe and probe.get("bad_items") else ""),
            "mailing_mode": "auto-mail",
        })
        details_by_pkg[pkg] = {
            "package_key": pkg,
            "scoreboard": items,
            "abnormal_items": abnormal,
            "probe_watch": probe,
        }
    return archive, details_by_pkg


def _raw_et_report(product: str, root_lot_id: str, fab_lot_id: str, wafer_id: str, step_id: str, metric: str, limit: int, report_variant: str, point_mode: str, audience: str):
    df = _collect_raw_et(product)
    if df.is_empty():
        return None
    fab_col = "fab_lot_id" if "fab_lot_id" in df.columns else ("lot_id" if "lot_id" in df.columns else "")
    if root_lot_id and "root_lot_id" in df.columns:
        lot_q = str(root_lot_id)
        if not fab_lot_id and fab_col:
            df = df.filter((pl.col("root_lot_id") == lot_q) | (pl.col(fab_col) == lot_q))
        else:
            df = df.filter(pl.col("root_lot_id") == lot_q)
    if fab_lot_id and fab_col:
        df = df.filter(pl.col(fab_col) == str(fab_lot_id))
    if wafer_id and "wafer_id" in df.columns:
        df = df.filter(pl.col("wafer_id") == str(wafer_id))
    step_ids, step_label = _step_search_ids(product, step_id)
    if step_ids and "step_id" in df.columns:
        df = df.filter(pl.col("step_id").is_in(step_ids))
    if df.is_empty():
        return {
            "summary": {"packages": 0, "lots": 0, "repeat_lots": 0, "latest_package_time": "", "metric_name": metric, "step_id": step_id or "", "step_label": step_label or step_id or ""},
            "recent_packages": [],
            "report_archive": [],
            "report_details": {},
            "report_pages": [],
            "repeat_lots": [],
            "repeat_wafers": [],
            "metric_extremes": {"high": [], "low": []},
            "available_metrics": [],
            "source_mode": "db_flat_raw_et",
            "note": "DB flat ET 원천에서 조건에 맞는 lot / step 조건 데이터가 없습니다.",
        }

    value_col = "value"
    metric_name = metric if metric else ""
    if not metric_name:
        metric_name = str(df["item_id"].drop_nulls().unique().to_list()[0]) if "item_id" in df.columns and df["item_id"].drop_nulls().len() else ""

    df = _add_et_report_keys(df)
    metric_df = df.filter(pl.col("item_id") == metric_name) if metric_name and "item_id" in df.columns else pl.DataFrame()

    recent_base = (
        df.group_by(["product", "root_lot_id", "fab_lot_id", "step_id", "package_key"])
        .agg([
            pl.len().alias("pt_count"),
            pl.col("wafer_id").n_unique().alias("wafer_count"),
            pl.col("eqp").drop_nulls().last().alias("eqp") if "eqp" in df.columns else pl.lit("").alias("eqp"),
            pl.col("chamber").drop_nulls().last().alias("chamber") if "chamber" in df.columns else pl.lit("").alias("chamber"),
            pl.col("item_id").n_unique().alias("item_count"),
            pl.col("step_seq").drop_nulls().unique().sort().alias("step_seqs"),
            pl.col("package_time").drop_nulls().max().alias("package_time"),
            pl.col("package_time").drop_nulls().min().alias("first_package_time"),
            pl.col("request_key").drop_nulls().unique().sort().alias("request_keys"),
            pl.col("request_key").n_unique().alias("request_count"),
            pl.col("measurement_key").n_unique().alias("measurement_count"),
        ])
        .sort("package_time", descending=True)
        .head(limit)
    )
    item_points = (
        df.group_by(["package_key", "item_id"])
        .agg(pl.len().alias("pt_count"))
        .sort(["package_key", "item_id"])
        .to_dicts()
    )
    pkg_item_map: dict[str, list[str]] = {}
    for row in item_points:
        pkg = str(row.get("package_key") or "")
        item = str(row.get("item_id") or "").strip()
        pts = int(row.get("pt_count") or 0)
        if not pkg or not item:
            continue
        pkg_item_map.setdefault(pkg, []).append(f"{item}:{pts}pt")
    seq_points = (
        df.group_by(["package_key", "step_seq"])
        .agg(pl.len().alias("pt_count"))
        .sort(["package_key", "step_seq"])
        .to_dicts()
    )
    pkg_seq_map: dict[str, list[dict]] = {}
    for row in seq_points:
        pkg = str(row.get("package_key") or "")
        seq = str(row.get("step_seq") or "-")
        pts = int(row.get("pt_count") or 0)
        if pkg and pts > 0:
            pkg_seq_map.setdefault(pkg, []).append({"step_seq": seq, "pt_count": pts})

    probe_watch = _probe_watch_report(df, product)
    _maybe_notify_probe_watch(product, probe_watch)

    recent = []
    for row in recent_base.to_dicts():
        seqs = [str(v) for v in (row.get("step_seqs") or []) if str(v or "").strip()]
        row["step_seq_combo"] = ", ".join(seqs) if seqs else "-"
        row["request_key"] = _join_unique(row.get("request_keys") or [])
        row["step_seq_points"] = _seq_points_label(pkg_seq_map.get(str(row.get("package_key") or ""), []))
        row["step_seq_point_rows"] = pkg_seq_map.get(str(row.get("package_key") or ""), [])
        row["item_points"] = " | ".join(pkg_item_map.get(str(row.get("package_key") or ""), []))
        probe_pkg = next((x for x in (probe_watch.get("package_summary") or []) if str(x.get("package_key") or "") == str(row.get("package_key") or "")), None)
        row["probe_status"] = probe_pkg.get("health_status") if probe_pkg else "ok"
        row["probe_summary"] = ", ".join(
            f"{x.get('alias') or x.get('item_id')}={x.get('mean_value')}"
            for x in (probe_pkg.get("bad_items") or [])[:3]
        ) if probe_pkg else ""
        recent.append(row)

    selected_rules = _selected_reporting_rules(product, report_variant, point_mode, audience)
    report_archive, report_details = _package_report_views(df, selected_rules, recent, probe_watch)
    report_pages = _build_item_payloads_from_rules(df, selected_rules, max_items=16)
    if not report_pages:
        fallback_rules = _top_item_rules(df, max_items=16)
        report_pages = _build_item_payloads_from_rules(df, fallback_rules, max_items=16)
        if not report_archive:
            report_archive, report_details = _package_report_views(df, fallback_rules, recent, probe_watch)

    def _parse_ts(text):
        s = str(text or "").strip()
        if not s:
            return None
        for fn in (
            lambda x: dt.datetime.fromisoformat(x.replace("Z", "+00:00")),
            lambda x: dt.datetime.strptime(x[:19], "%Y-%m-%d %H:%M:%S"),
            lambda x: dt.datetime.strptime(x[:19], "%Y-%m-%dT%H:%M:%S"),
        ):
            try:
                return fn(s)
            except Exception:
                continue
        return None

    gantt_rows = []
    seq_summary = []
    if "tkin_time" in df.columns or "tkout_time" in df.columns:
        seq_base = (
            df.group_by(["product", "root_lot_id", "fab_lot_id", "step_id", "package_key", "step_seq"])
            .agg([
                pl.col("tkin_time").drop_nulls().min().alias("seq_tkin") if "tkin_time" in df.columns else pl.lit("").alias("seq_tkin"),
                pl.col("tkout_time").drop_nulls().max().alias("seq_tkout") if "tkout_time" in df.columns else pl.lit("").alias("seq_tkout"),
                pl.col("item_id").n_unique().alias("item_count"),
                pl.len().alias("pt_count"),
                pl.concat_str([pl.col("shot_x").cast(_STR, strict=False).fill_null(""), pl.lit(","), pl.col("shot_y").cast(_STR, strict=False).fill_null("")]).n_unique().alias("shot_count") if "shot_x" in df.columns and "shot_y" in df.columns else pl.lit(0).alias("shot_count"),
                pl.col("item_id").drop_nulls().unique().sort().alias("items"),
                pl.col("request_key").drop_nulls().unique().sort().alias("request_keys"),
            ])
            .sort(["package_key", "step_seq"])
        )
        seq_rows = []
        for row in seq_base.to_dicts():
            st = _parse_ts(row.get("seq_tkin"))
            ed = _parse_ts(row.get("seq_tkout"))
            dur = round(max(0.0, (ed - st).total_seconds() / 60.0), 2) if st and ed else None
            items = [str(v) for v in (row.get("items") or []) if str(v or "").strip()]
            seq_rows.append({
                **row,
                "duration_min": dur,
                "item_preview": ", ".join(items[:8]) + (f" +{len(items)-8}" if len(items) > 8 else ""),
            })
        pkg_map = {}
        global_start = None
        global_end = None
        for row in seq_rows:
            st = _parse_ts(row.get("seq_tkin"))
            ed = _parse_ts(row.get("seq_tkout"))
            if st and (global_start is None or st < global_start):
                global_start = st
            if ed and (global_end is None or ed > global_end):
                global_end = ed
            pkg_map.setdefault(str(row.get("package_key") or ""), []).append(row)
        total_span_min = max(1.0, ((global_end - global_start).total_seconds() / 60.0)) if global_start and global_end else 1.0
        for pkg, rows in pkg_map.items():
            rows = sorted(rows, key=lambda x: (float(str(x.get("step_seq") or "0").replace(",", ".")) if str(x.get("step_seq") or "").replace(".", "", 1).isdigit() else 9999, str(x.get("step_seq") or "")))
            segments = []
            for row in rows:
                st = _parse_ts(row.get("seq_tkin"))
                ed = _parse_ts(row.get("seq_tkout"))
                offset = round(max(0.0, ((st - global_start).total_seconds() / 60.0)) / total_span_min * 100.0, 2) if st and global_start else 0.0
                width = round(max(1.0, ((ed - st).total_seconds() / 60.0)) / total_span_min * 100.0, 2) if st and ed else 4.0
                segments.append({
                    "step_seq": row.get("step_seq") or "",
                    "duration_min": row.get("duration_min"),
                    "offset_pct": offset,
                    "width_pct": width,
                    "item_count": row.get("item_count") or 0,
                    "shot_count": row.get("shot_count") or 0,
                    "item_preview": row.get("item_preview") or "",
                    "seq_tkin": row.get("seq_tkin") or "",
                    "seq_tkout": row.get("seq_tkout") or "",
                })
            head = rows[0] if rows else {}
            gantt_rows.append({
                "product": head.get("product") or "",
                "root_lot_id": head.get("root_lot_id") or "",
                "fab_lot_id": head.get("fab_lot_id") or "",
                "step_id": head.get("step_id") or "",
                "package_key": pkg,
                "request_key": _join_unique(head.get("request_keys") or []),
                "segments": segments,
                "total_duration_min": round(sum(float(s.get("duration_min") or 0.0) for s in segments), 2),
            })
        seq_rollup = {}
        for row in seq_rows:
            key = str(row.get("step_seq") or "")
            seq_rollup.setdefault(key, []).append(row)
        for seq, rows in sorted(seq_rollup.items(), key=lambda kv: kv[0]):
            durs = [float(r.get("duration_min")) for r in rows if r.get("duration_min") is not None]
            seq_summary.append({
                "step_seq": seq,
                "packages": len(rows),
                "avg_duration_min": round(sum(durs) / len(durs), 2) if durs else None,
                "max_duration_min": round(max(durs), 2) if durs else None,
                "avg_items": round(sum(float(r.get("item_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
                "avg_shots": round(sum(float(r.get("shot_count") or 0) for r in rows) / len(rows), 2) if rows else 0,
                "sample_items": next((r.get("item_preview") for r in rows if r.get("item_preview")), ""),
            })

    lot_group = (
        df.group_by(["product", "root_lot_id", "fab_lot_id", "step_id"])
        .agg([
            pl.col("package_key").n_unique().alias("package_count"),
            pl.col("measurement_key").n_unique().alias("measurement_count"),
            pl.col("package_time").max().alias("last_package_time"),
            pl.col("package_time").min().alias("first_package_time"),
            pl.col("request_key").n_unique().alias("request_count"),
            pl.col("wafer_id").n_unique().alias("wafer_count"),
        ])
        .sort(["measurement_count", "last_package_time"], descending=[True, True])
    )
    repeats = lot_group.filter(pl.col("measurement_count") > 1).head(20).to_dicts()

    high = []
    low = []
    if metric_name and not metric_df.is_empty():
        metric_pkg = (
            metric_df.group_by(["product", "root_lot_id", "fab_lot_id", "step_id", "package_key", "package_time"])
            .agg([
                pl.col(value_col).cast(pl.Float64, strict=False).mean().alias(metric_name),
                pl.col("eqp").drop_nulls().last().alias("eqp") if "eqp" in metric_df.columns else pl.lit("").alias("eqp"),
            ])
            .drop_nulls(metric_name)
        )
        high = metric_pkg.sort(metric_name, descending=True).head(8).to_dicts()
        low = metric_pkg.sort(metric_name, descending=False).head(8).to_dicts()

    summary = {
        "packages": int(df["package_key"].n_unique()) if "package_key" in df.columns else 0,
        "lots": int(lot_group.height),
        "repeat_lots": len(repeats),
        "latest_package_time": str(df["package_time"].drop_nulls().max() or "") if "package_time" in df.columns else "",
        "metric_name": metric_name,
        "step_id": step_id or "",
        "step_label": step_label or step_id or "",
        "seq_count": len(seq_summary),
        "probe_flagged_packages": int(probe_watch.get("flagged_packages") or 0),
        "probe_critical_packages": int(probe_watch.get("critical_packages") or 0),
    }
    return {
        "summary": summary,
        "recent_packages": recent,
        "report_archive": report_archive,
        "report_details": report_details,
        "repeat_lots": repeats,
        "repeat_wafers": repeats,
        "metric_extremes": {"high": high, "low": low},
        "seq_timeline": seq_summary,
        "package_gantt": gantt_rows[: max(12, min(limit, 24))],
        "report_pages": report_pages,
        "available_metrics": sorted(str(v) for v in df["item_id"].drop_nulls().unique().to_list()) if "item_id" in df.columns else [],
        "source_mode": "db_flat_raw_et",
        "note": "DB ET 원천(flat 우선, 향후 hive 대응) 기준 fab_lot_id + step_id + request(package) 중심 화면입니다. request_id → measure_group_id → step_id+step_seq+tkout_time 우선순위로 package를 구분합니다.",
        "probe_watch": probe_watch,
    }


def _reporting_bundle(product: str, report_variant: str, point_mode: str, audience: str) -> dict:
    rules = _load_reporting_rules(product) if product else []
    profiles = available_report_profiles(rules)
    selected = filter_rules_for_report(rules, report_variant=report_variant, point_mode=point_mode)
    if audience in {"internal", "external"}:
        selected = [r for r in selected if (r.get("report_audience") in (audience, "both"))]
    items = []
    for r in sorted(selected, key=lambda x: (int(x.get("report_order") or 9999), str(x.get("alias") or x.get("name") or ""))):
        items.append({
            "alias": r.get("alias") or r.get("name") or "",
            "name": r.get("name") or "",
            "cat": r.get("cat") or "",
            "point_mode": r.get("point_mode") or "both",
            "report_variant": r.get("report_variant") or "default",
            "report_audience": r.get("report_audience") or "internal",
            "tracker_attach": bool(r.get("tracker_attach")),
            "point_selector": r.get("point_selector") or {},
            "rawitem_id": r.get("rawitem_id") or "",
            "y_axis": r.get("y_axis") or "linear",
            "spec": r.get("spec") or "none",
            "use": bool(r.get("use")),
        })
    return {
        "profiles": profiles,
        "selected_items": items,
        "selected_variant": report_variant or "all",
        "selected_point_mode": point_mode or "all",
        "selected_audience": audience or "internal",
        "tracker_attach_items": [x for x in items if x.get("tracker_attach")],
    }


def _metric_candidates(df: pl.DataFrame) -> list[str]:
    out = []
    skip = {"lot_id", "root_lot_id", "wafer_id", "product", "pgm", "eqp", "chamber", "start_ts", "end_ts"}
    for c, dt in df.schema.items():
        if c in skip or c.endswith("_Split"):
            continue
        if any(tok in str(dt) for tok in ("Int", "Float", "Decimal")):
            out.append(c)
    return out


def _tiny_pdf(lines: list[str]) -> bytes:
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    content_lines = ["BT", "/F1 12 Tf", "50 780 Td"]
    first = True
    for line in lines[:40]:
        if not first:
            content_lines.append("0 -16 Td")
        first = False
        content_lines.append(f"({esc(line)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1", errors="replace")
    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n",
        f"4 0 obj << /Length {len(content)} >> stream\n".encode() + content + b"\nendstream endobj\n",
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)
    xref_pos = len(out)
    out.extend(f"xref\n0 {len(objects)+1}\n".encode())
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode())
    out.extend(f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())
    return bytes(out)


def _log_download(username: str, root_lot_id: str, file_type: str, size_bytes: int) -> None:
    jsonl_append(ET_DOWNLOAD_LOG, {
        "username": username or "unknown",
        "root_lot_id": root_lot_id,
        "type": file_type,
        "size_bytes": int(size_bytes),
    })


def _safe_file_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "").strip()).strip("_") or "report"


def _collect_raw_et_for_report(product: str = "") -> pl.DataFrame:
    if product:
        return _collect_raw_et(product)
    raw_root = _db_root() / "1.RAWDATA_DB_ET"
    frames = []
    if raw_root.is_dir():
        for prod_dir in sorted(p for p in raw_root.iterdir() if p.is_dir()):
            df = _collect_raw_et(prod_dir.name)
            if not df.is_empty():
                frames.append(df)
    if frames:
        return pl.concat(frames, how="diagonal_relaxed")
    return _collect_raw_et(product)


def _percentile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = max(0.0, min(1.0, float(q))) * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_values[lo])
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def _sample_evenly(rows: list, limit: int) -> list:
    if len(rows) <= limit:
        return rows
    if limit <= 1:
        return rows[:1]
    out = []
    last = len(rows) - 1
    for i in range(limit):
        out.append(rows[round(i * last / (limit - 1))])
    return out


def _item_stats(values: list[float]) -> dict:
    vals = sorted(float(v) for v in values if v is not None and math.isfinite(float(v)))
    if not vals:
        return {}
    mean = sum(vals) / len(vals)
    var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1) if len(vals) > 1 else 0.0
    return {
        "min": vals[0],
        "q1": _percentile(vals, 0.25),
        "median": _percentile(vals, 0.50),
        "q3": _percentile(vals, 0.75),
        "max": vals[-1],
        "p95": _percentile(vals, 0.95),
        "mean": mean,
        "std": math.sqrt(var),
    }


def _build_item_payloads_from_rules(df: pl.DataFrame, rules: list[dict], max_items: int = 16) -> list[dict]:
    if df.is_empty() or not rules:
        return []
    applied, prepared_rules = _apply_report_rules(df, rules)
    payloads = []
    ordered = sorted(prepared_rules, key=lambda r: (int(r.get("report_order") or 9999), _rule_label(r)))
    for rule in ordered[: max(1, min(40, int(max_items or 16)))]:
        sub = _rule_value_df(applied, rule)
        if sub.is_empty():
            continue
        values = [float(v) for v in sub["_value"].to_list() if v is not None and math.isfinite(float(v))]
        if not values:
            continue
        stats = _item_stats(values)
        spec = str(rule.get("spec") or "none").lower()
        lsl = _to_float(rule.get("lsl"))
        usl = _to_float(rule.get("usl"))
        spec_out = 0
        for v in values:
            if spec in {"lsl", "both"} and lsl is not None and v < lsl:
                spec_out += 1
            elif spec in {"usl", "both"} and usl is not None and v > usl:
                spec_out += 1

        trend_rows = []
        if {"package_key", "package_time"}.issubset(set(sub.columns)):
            trend_rows = (
                sub.group_by(["package_key", "package_time"])
                .agg([pl.col("_value").mean().alias("mean"), pl.len().alias("n")])
                .sort("package_time")
                .to_dicts()
            )
        trend_rows = _sample_evenly(trend_rows, 18)

        seq_rows = []
        if "step_seq" in sub.columns:
            seq_rows = (
                sub.group_by("step_seq")
                .agg(pl.len().alias("pt_count"))
                .sort("step_seq")
                .to_dicts()
            )
        seq_points = [
            {"step_seq": str(r.get("step_seq") or "-"), "pt_count": int(r.get("pt_count") or 0)}
            for r in seq_rows if int(r.get("pt_count") or 0) > 0
        ]

        radius_rows = []
        wf_map = []
        if {"shot_x", "shot_y"}.issubset(set(sub.columns)):
            for row in sub.select([c for c in ("shot_x", "shot_y", "wafer_id", "_value") if c in sub.columns]).drop_nulls(["shot_x", "shot_y", "_value"]).to_dicts():
                x = float(row.get("shot_x") or 0.0)
                y = float(row.get("shot_y") or 0.0)
                val = float(row.get("_value") or 0.0)
                radius_rows.append({"radius": round(math.sqrt(x * x + y * y), 6), "value": val, "shot_x": x, "shot_y": y})
                wf_map.append({"shot_x": x, "shot_y": y, "value": val, "wafer_id": str(row.get("wafer_id") or "")})

        cdf_rows = []
        vals = sorted(values)
        sample_vals = _sample_evenly(vals, 90)
        sample_len = max(1, len(sample_vals))
        for idx, v in enumerate(sample_vals, start=1):
            cdf_rows.append({"x": float(v), "p": idx / sample_len})

        payloads.append({
            "item_id": str(rule.get("name") or rule.get("rawitem_id") or ""),
            "rawitem_id": "/".join(_raw_item_ids_for_rule(rule)) or str(rule.get("rawitem_id") or ""),
            "alias": _rule_label(rule),
            "cat": str(rule.get("cat") or ""),
            "report_order": int(rule.get("report_order") or 9999),
            "y_axis": str(rule.get("y_axis") or "linear"),
            "n": len(values),
            "package_count": int(sub["package_key"].n_unique()) if "package_key" in sub.columns else 0,
            "stats": stats,
            "lsl": lsl,
            "usl": usl,
            "spec": str(rule.get("spec") or "none"),
            "spec_out_points": spec_out,
            "step_seq_points": _seq_points_label(seq_points),
            "step_seq_point_rows": seq_points,
            "trend": [{"package_key": str(r.get("package_key") or ""), "package_time": str(r.get("package_time") or ""), "mean": float(r.get("mean") or 0.0), "n": int(r.get("n") or 0)} for r in trend_rows],
            "radius": _sample_evenly(radius_rows, 160),
            "wf_map": _sample_evenly(wf_map, 240),
            "cdf": cdf_rows,
        })
    return payloads


def _build_et_pptx_payload(
    product: str = "",
    root_lot_id: str = "",
    fab_lot_id: str = "",
    step_id: str = "",
    package_key: str = "",
    item_ids: list[str] | None = None,
    max_items: int = 6,
    report_variant: str = "",
    point_mode: str = "",
    audience: str = "internal",
) -> dict:
    df = _collect_raw_et_for_report(product)
    if df.is_empty():
        raise HTTPException(404, "ET raw dataset not found")
    if "fab_lot_id" not in df.columns:
        df = df.with_columns((pl.col("lot_id") if "lot_id" in df.columns else pl.lit("")).alias("fab_lot_id"))
    if root_lot_id and "root_lot_id" in df.columns:
        df = df.filter(pl.col("root_lot_id") == str(root_lot_id))
    if fab_lot_id and "fab_lot_id" in df.columns:
        df = df.filter(pl.col("fab_lot_id") == str(fab_lot_id))
    step_ids, step_label = _step_search_ids(product, step_id)
    if step_ids and "step_id" in df.columns:
        df = df.filter(pl.col("step_id").is_in(step_ids))
    if df.is_empty():
        raise HTTPException(404, "No ET rows matched the PPTX report filter")

    df = _add_et_report_keys(df)
    if "value" in df.columns:
        df = df.with_columns(pl.col("value").cast(pl.Float64, strict=False).alias("value"))
    if package_key and "package_key" in df.columns:
        df = df.filter(pl.col("package_key") == str(package_key))
        if df.is_empty():
            raise HTTPException(404, "No ET rows matched the selected lot / fab lot / ET step")
    for c in ("shot_x", "shot_y"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Float64, strict=False).alias(c))

    first = df.sort("package_time", descending=True).head(1).to_dicts()[0]
    if item_ids:
        rules = [_pseudo_rule_for_item(x) for x in item_ids if str(x).strip()]
    else:
        rules = _selected_reporting_rules(product, report_variant, point_mode, audience)
        if not rules:
            rules = _top_item_rules(df, max_items=max_items)
    if not rules:
        raise HTTPException(404, "No ET score items or item_id values available for PPTX")

    recent_base = (
        df.group_by(["product", "root_lot_id", "fab_lot_id", "step_id", "package_key"])
        .agg([
            pl.len().alias("pt_count"),
            pl.col("wafer_id").n_unique().alias("wafer_count") if "wafer_id" in df.columns else pl.lit(0).alias("wafer_count"),
            pl.col("item_id").n_unique().alias("item_count") if "item_id" in df.columns else pl.lit(0).alias("item_count"),
            pl.col("step_seq").drop_nulls().unique().sort().alias("step_seqs") if "step_seq" in df.columns else pl.lit([]).alias("step_seqs"),
            pl.col("package_time").drop_nulls().max().alias("package_time"),
            pl.col("package_time").drop_nulls().min().alias("first_package_time"),
            pl.col("request_key").drop_nulls().unique().sort().alias("request_keys"),
            pl.col("request_key").n_unique().alias("request_count"),
            pl.col("measurement_key").n_unique().alias("measurement_count"),
        ])
        .sort("package_time", descending=True)
        .head(max(1, min(50, int(max_items or 6))))
    )
    seq_points = (
        df.group_by(["package_key", "step_seq"])
        .agg(pl.len().alias("pt_count"))
        .sort(["package_key", "step_seq"])
        .to_dicts()
    ) if "step_seq" in df.columns else []
    pkg_seq_map: dict[str, list[dict]] = {}
    for row in seq_points:
        pkg = str(row.get("package_key") or "")
        pts = int(row.get("pt_count") or 0)
        if pkg and pts > 0:
            pkg_seq_map.setdefault(pkg, []).append({"step_seq": str(row.get("step_seq") or "-"), "pt_count": pts})
    recent_rows = []
    for row in recent_base.to_dicts():
        seqs = [str(v) for v in (row.get("step_seqs") or []) if str(v or "").strip()]
        pkg = str(row.get("package_key") or "")
        row["step_seq_combo"] = ", ".join(seqs) if seqs else "-"
        row["step_seq_points"] = _seq_points_label(pkg_seq_map.get(pkg, []))
        row["request_key"] = _join_unique(row.get("request_keys") or [])
        recent_rows.append(row)

    probe_watch = _probe_watch_report(df, product)
    report_archive, report_details = _package_report_views(df, rules, recent_rows, probe_watch)
    if not any((d.get("scoreboard") or []) for d in report_details.values()) and not item_ids:
        fallback_rules = _top_item_rules(df, max_items=max_items)
        report_archive, report_details = _package_report_views(df, fallback_rules, recent_rows, probe_watch)

    active_package = recent_rows[0] if recent_rows else first
    active_key = str(active_package.get("package_key") or "")
    active_detail = report_details.get(active_key) or next(iter(report_details.values()), {})
    scoreboard = active_detail.get("scoreboard") or []
    if not scoreboard and not item_ids:
        fallback_rules = _top_item_rules(df, max_items=max_items)
        report_archive, report_details = _package_report_views(df, fallback_rules, recent_rows, probe_watch)
        active_detail = report_details.get(active_key) or next(iter(report_details.values()), {})
        scoreboard = active_detail.get("scoreboard") or []

    if not scoreboard:
        raise HTTPException(404, "No numeric ET item values available for PPTX")
    meta_product = product or str(first.get("product") or "")
    meta_root = root_lot_id or str(first.get("root_lot_id") or "")
    meta_fab = fab_lot_id or str(first.get("fab_lot_id") or first.get("lot_id") or "")
    meta_step = step_id or str(first.get("step_id") or "")
    return {
        "title": "ET Measurement Report",
        "mode": "scoreboard",
        "meta": {
            "product": meta_product,
            "root_lot_id": meta_root,
            "fab_lot_id": meta_fab,
            "step_id": meta_step,
            "step_label": step_label or meta_step,
            "package_key": active_key,
            "package_time": active_package.get("package_time") or "",
            "request_key": active_package.get("request_key") or "",
            "step_seq_points": active_package.get("step_seq_points") or "",
            "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
            "row_count": int(df.height),
        },
        "scoreboard": scoreboard,
        "report_archive": report_archive,
        "items": [],
    }


def _build_et_pptx_file(
    product: str = "",
    root_lot_id: str = "",
    fab_lot_id: str = "",
    step_id: str = "",
    package_key: str = "",
    item_ids: list[str] | None = None,
    max_items: int = 6,
    report_variant: str = "",
    point_mode: str = "",
    audience: str = "internal",
    filename: str = "",
) -> tuple[Path, dict]:
    if not PPTX_SCRIPT.is_file():
        raise HTTPException(500, f"PPTX builder not found: {PPTX_SCRIPT}")
    payload = _build_et_pptx_payload(
        product=product,
        root_lot_id=root_lot_id,
        fab_lot_id=fab_lot_id,
        step_id=step_id,
        package_key=package_key,
        item_ids=item_ids,
        max_items=max_items,
        report_variant=report_variant,
        point_mode=point_mode,
        audience=audience,
    )
    meta = payload.get("meta") or {}
    name = filename or (
        f"ET_Report_{_safe_file_part(meta.get('product'))}_"
        f"{_safe_file_part(meta.get('root_lot_id'))}_"
        f"{_safe_file_part(meta.get('step_id'))}_"
        f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
    )
    if not name.lower().endswith(".pptx"):
        name += ".pptx"
    ET_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_fp = ET_REPORT_DIR / _safe_file_part(name)
    with tempfile.TemporaryDirectory(prefix="et_pptx_", dir=str(ET_REPORT_DIR)) as tmp:
        payload_fp = Path(tmp) / "payload.json"
        payload_fp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(PPTX_SCRIPT), str(payload_fp), str(out_fp)],
            cwd=str(PPTX_SCRIPT.parents[1]),
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    if proc.returncode != 0:
        logger.error("ET PPTX build failed: %s %s", proc.stdout, proc.stderr)
        raise HTTPException(500, f"PPTX build failed: {(proc.stderr or proc.stdout or '').strip()[:300]}")
    if not out_fp.is_file() or out_fp.stat().st_size <= 0:
        raise HTTPException(500, "PPTX build produced no file")
    return out_fp, payload


def _package_df(df: pl.DataFrame) -> pl.DataFrame:
    cols = df.columns
    exprs = []
    if "end_ts" in cols:
        exprs.append(pl.col("end_ts").alias("package_time"))
    elif "tkout_time" in cols:
        exprs.append(pl.col("tkout_time").cast(_STR, strict=False).alias("package_time"))
    else:
        exprs.append(pl.lit("").alias("package_time"))
    exprs.append(pl.when(pl.col("pgm").is_not_null()).then(pl.col("pgm"))
                 .otherwise(pl.col("eqp") if "eqp" in cols else pl.lit("")).alias("request_key"))
    exprs.append(
        pl.concat_str([
            pl.col("root_lot_id") if "root_lot_id" in cols else pl.lit(""),
            pl.lit("|"),
            pl.col("wafer_id") if "wafer_id" in cols else pl.lit(""),
            pl.lit("|"),
            pl.col("end_ts") if "end_ts" in cols else pl.lit(""),
            pl.lit("|"),
            pl.col("pgm") if "pgm" in cols else pl.lit(""),
        ]).alias("package_key")
    )
    return df.with_columns(exprs)


@router.get("/products")
def et_products():
    raw_root = _db_root() / "1.RAWDATA_DB_ET"
    if raw_root.is_dir():
        prods = sorted(p.name for p in raw_root.iterdir() if p.is_dir())
        if prods:
            return {"products": prods, "source_mode": "db_flat_raw_et"}
    df = _load_et_features()
    products = sorted(str(v) for v in df["product"].drop_nulls().unique().to_list())
    return {"products": products, "source_mode": "wafer_feature_fallback"}


@router.get("/report")
def et_report(
    product: str = Query(""),
    root_lot_id: str = Query(""),
    fab_lot_id: str = Query(""),
    wafer_id: str = Query(""),
    step_id: str = Query(""),
    metric: str = Query("Rc"),
    report_variant: str = Query(""),
    point_mode: str = Query(""),
    audience: str = Query("internal"),
    limit: int = Query(80, ge=10, le=500),
):
    bundle = _reporting_bundle(product, report_variant, point_mode, audience)
    raw = _raw_et_report(product, root_lot_id, fab_lot_id, wafer_id, step_id, metric, limit, report_variant, point_mode, audience)
    if raw is not None:
        raw["report_bundle"] = bundle
        return raw

    df = _package_df(_load_et_features())
    aliases = _product_aliases(product)
    if aliases and "product" in df.columns:
        df = df.filter(pl.col("product").str.to_uppercase().is_in(sorted(aliases)))
    if root_lot_id:
        lot_q = str(root_lot_id)
        if not fab_lot_id and "lot_id" in df.columns:
            df = df.filter((pl.col("root_lot_id") == lot_q) | (pl.col("lot_id") == lot_q))
        else:
            df = df.filter(pl.col("root_lot_id") == lot_q)
    if fab_lot_id and "lot_id" in df.columns:
        df = df.filter(pl.col("lot_id") == str(fab_lot_id))
    if wafer_id:
        df = df.filter(pl.col("wafer_id") == str(wafer_id))
    if df.is_empty():
        return {
            "summary": {"packages": 0, "lots": 0, "repeat_lots": 0, "latest_package_time": "", "metric_name": metric, "step_id": step_id or ""},
            "recent_packages": [],
            "repeat_lots": [],
            "repeat_wafers": [],
            "metric_extremes": {"high": [], "low": []},
            "seq_timeline": [],
            "package_gantt": [],
            "report_pages": [],
            "available_metrics": [],
            "source_mode": "wafer_feature_fallback",
            "note": "ET feature dataset에서 조건에 맞는 데이터가 없습니다.",
        }

    metrics = _metric_candidates(df)
    metric_name = metric if metric in metrics else (metrics[0] if metrics else "")
    packages = df.sort("package_time", descending=True)

    lot_group = (
        packages.group_by(["product", "root_lot_id", "lot_id"])
        .agg([
            pl.len().alias("package_count"),
            pl.col("package_time").max().alias("last_package_time"),
            pl.col("package_time").min().alias("first_package_time"),
            pl.col("eqp").n_unique().alias("eqp_count"),
            pl.col("request_key").n_unique().alias("request_count"),
            pl.col("wafer_id").n_unique().alias("wafer_count"),
        ])
        .sort(["package_count", "last_package_time"], descending=[True, True])
    )
    repeats = lot_group.filter(pl.col("package_count") > 1).head(20)

    recent_cols = [c for c in ("product", "root_lot_id", "lot_id", "package_time", "eqp", "chamber", "request_key") if c in packages.columns]
    if metric_name:
        recent_cols.append(metric_name)
        zc = metric_name + "_zscore"
        if zc in packages.columns:
            recent_cols.append(zc)
    recent = packages.select(recent_cols).head(limit).to_dicts()

    high = []
    low = []
    if metric_name:
        base_cols = [c for c in ("product", "root_lot_id", "lot_id", "package_time", "eqp", metric_name) if c in packages.columns]
        metric_latest = (
            packages.group_by(["root_lot_id", "lot_id"])
            .agg([
                pl.col("product").first().alias("product"),
                pl.col("package_time").max().alias("package_time"),
                pl.col("eqp").last().alias("eqp"),
                pl.col(metric_name).drop_nulls().last().alias(metric_name),
            ])
            .drop_nulls(metric_name)
        )
        high = metric_latest.sort(metric_name, descending=True).head(8).select(base_cols).to_dicts()
        low = metric_latest.sort(metric_name, descending=False).head(8).select(base_cols).to_dicts()

    latest_time = packages["package_time"].drop_nulls().max() if "package_time" in packages.columns else ""
    summary = {
        "packages": int(packages.height),
        "lots": int(lot_group.height),
        "repeat_lots": int(repeats.height),
        "latest_package_time": latest_time or "",
        "metric_name": metric_name,
        "avg_metric": round(float(packages[metric_name].drop_nulls().mean()), 4) if metric_name and packages[metric_name].drop_nulls().len() else None,
        "step_id": step_id or "",
    }

    return {
        "summary": summary,
        "recent_packages": recent,
        "repeat_lots": repeats.to_dicts(),
        "repeat_wafers": repeats.to_dicts(),
        "metric_extremes": {"high": high, "low": low},
        "seq_timeline": [],
        "package_gantt": [],
        "report_pages": [],
        "available_metrics": metrics,
        "source_mode": "wafer_feature_fallback",
        "note": "fallback 모드에서는 wafer-feature 기반 근사 lot report 입니다. raw ET long이 들어오면 step_id / request 기준 lot-step package를 우선 사용합니다.",
        "report_bundle": bundle,
    }


@router.get("/lots")
def et_lots(product: str = Query(""), search: str = Query(""), days: int = Query(30), limit: int = Query(200)):
    limit_n = int(limit)
    data = et_report(product=product, limit=max(100, limit_n))
    rows = []
    for row in data.get("repeat_lots") or []:
        rows.append({
            "root_lot_id": row.get("root_lot_id") or "",
            "fab_lot_id": row.get("fab_lot_id") or row.get("lot_id") or "",
            "product": row.get("product") or "",
            "steps": [row.get("step_id")] if row.get("step_id") else [],
            "step_range": row.get("step_id") or "",
            "last_measured_at": row.get("last_package_time") or "",
            "point_count": int(row.get("request_count") or 0),
            "abnormal_count": int(row.get("measurement_count") or 0) - 1 if int(row.get("measurement_count") or 0) > 1 else 0,
        })
    # supplement from recent packages so non-repeat lots also show
    seen = {str(r["root_lot_id"]) for r in rows if r.get("root_lot_id")}
    for row in data.get("recent_packages") or []:
        root = str(row.get("root_lot_id") or "")
        if not root or root in seen:
            continue
        seen.add(root)
        rows.append({
            "root_lot_id": root,
            "fab_lot_id": row.get("fab_lot_id") or row.get("lot_id") or "",
            "product": row.get("product") or "",
            "steps": [row.get("step_id")] if row.get("step_id") else [],
            "step_range": row.get("step_id") or "",
            "last_measured_at": row.get("package_time") or "",
            "point_count": 1,
            "abnormal_count": 1 if str(row.get("probe_status") or "").lower() in {"warn", "critical"} else 0,
        })
    q = str(search or "").strip().lower()
    if q:
        rows = [r for r in rows if q in str(r.get("root_lot_id") or "").lower() or q in str(r.get("fab_lot_id") or "").lower() or q in str(r.get("product") or "").lower()]
    rows.sort(key=lambda r: str(r.get("last_measured_at") or ""), reverse=True)
    return {"ok": True, "days": int(days), "lots": rows[:limit_n]}


@router.get("/lot/{root_lot_id}")
def et_lot_detail(root_lot_id: str):
    data = et_report(root_lot_id=root_lot_id, limit=200)
    report_rows = data.get("report_archive") or []
    scoreboard = []
    abnormal = []
    detail_map = data.get("report_details") or {}
    for row in report_rows:
        detail = detail_map.get(row.get("package_key") or "") or {}
        scoreboard.extend(detail.get("scoreboard") or [])
        abnormal.extend(detail.get("abnormal_items") or [])
    abnormal = abnormal[:10]
    return {
        "ok": True,
        "root_lot_id": root_lot_id,
        "summary": data.get("summary") or {},
        "scoreboard": scoreboard[:40],
        "abnormal_top10": abnormal,
        "step_seq_timeline": data.get("seq_timeline") or [],
        "gantt_points": data.get("package_gantt") or [],
        "report_archive": report_rows,
    }


@router.get("/report/csv")
def et_report_csv(request: Request, root_lot_id: str = Query("")):
    payload = et_lot_detail(root_lot_id)
    rows = payload.get("scoreboard") or []
    csv_df = pl.DataFrame(rows or [{"alias": "", "rawitem_id": "", "status": "", "pt_count": 0, "spec_out_points": 0}])
    data = csv_df.write_csv().encode("utf-8-sig")
    filename = f"ET_Report_{root_lot_id}_{dt.datetime.now().strftime('%Y%m%d')}.csv"
    username = ""
    try:
        from core.auth import current_user
        username = current_user(request).get("username") or ""
    except Exception:
        username = ""
    _log_download(username, root_lot_id, "csv", len(data))
    return StreamingResponse(io.BytesIO(data), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/report/pdf")
def et_report_pdf(request: Request, root_lot_id: str = Query("")):
    payload = et_lot_detail(root_lot_id)
    summary = payload.get("summary") or {}
    abnormal = payload.get("abnormal_top10") or []
    lines = [
        f"ET Report {root_lot_id}",
        f"Generated {dt.datetime.now().isoformat(timespec='seconds')}",
        f"Packages {summary.get('packages', 0)} / Lots {summary.get('lots', 0)} / Repeat {summary.get('repeat_lots', 0)}",
        f"Latest {summary.get('latest_package_time', '-')}",
        "Top abnormal items:",
    ]
    for row in abnormal[:10]:
        lines.append(f"- {row.get('alias') or row.get('rawitem_id')} out={row.get('spec_out_points')} mean={row.get('mean_value')}")
    if len(lines) <= 5:
        lines.append("- no abnormal items")
    data = _tiny_pdf(lines)
    filename = f"ET_Report_{root_lot_id}_{dt.datetime.now().strftime('%Y%m%d')}.pdf"
    username = ""
    try:
        from core.auth import current_user
        username = current_user(request).get("username") or ""
    except Exception:
        username = ""
    _log_download(username, root_lot_id, "pdf", len(data))
    return StreamingResponse(io.BytesIO(data), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/report/pptx")
def et_report_pptx(
    request: Request,
    product: str = Query(""),
    root_lot_id: str = Query(""),
    fab_lot_id: str = Query(""),
    step_id: str = Query(""),
    package_key: str = Query(""),
    items: str = Query("", description="comma separated item_id list"),
    max_items: int = Query(6, ge=1, le=30),
    report_variant: str = Query(""),
    point_mode: str = Query(""),
    audience: str = Query("internal"),
):
    item_ids = [x.strip() for x in str(items or "").split(",") if x.strip()]
    fp, payload = _build_et_pptx_file(
        product=product,
        root_lot_id=root_lot_id,
        fab_lot_id=fab_lot_id,
        step_id=step_id,
        package_key=package_key,
        item_ids=item_ids or None,
        max_items=max_items,
        report_variant=report_variant,
        point_mode=point_mode,
        audience=audience,
    )
    data = fp.read_bytes()
    meta = payload.get("meta") or {}
    filename = fp.name
    username = ""
    try:
        from core.auth import current_user
        username = current_user(request).get("username") or ""
    except Exception:
        username = ""
    _log_download(username, meta.get("root_lot_id") or root_lot_id, "pptx", len(data))
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
