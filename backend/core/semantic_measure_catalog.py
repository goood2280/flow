"""Semantic measurement term catalog.

This catalog maps user-facing measurement names such as "CA BCD" to concrete
source DB, product scope, step_id/item_id, default aggregation, spec metadata,
and evidence. Runtime data is operator-owned under FLOW_DATA_ROOT.
"""
from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl

from core.paths import PATHS
from core.utils import _STR, jsonl_append, load_json, save_json


TERMS_FILE = PATHS.data_root / "semantic" / "measurement_terms.json"
CHANGES_FILE = PATHS.data_root / "semantic" / "measurement_terms.changes.jsonl"
CHANGE_MANAGEMENT_HISTORY = PATHS.data_root / "agent_unit_ai_sessions" / "change_management" / "history.jsonl"
DEFAULT_TERMS_FILE = Path(__file__).with_name("semantic_measure_defaults.json")
SCHEMA_VERSION = 1

DEFAULT_MEASUREMENT_TERMS: list[dict[str, Any]] = [
    {
        "id": "measure_inline_proda_ca_bcd",
        "term": "CA BCD",
        "aliases": ["CA BCD", "CA_BCD", "CABCD"],
        "source_type": "INLINE",
        "product": "PRODA",
        "step_id": "",
        "item_id": "CA_BCD",
        "value_column": "",
        "default_agg": "avg",
        "target": None,
        "spec_low": None,
        "spec_high": None,
        "evidence": [
            {"type": "operator_seed", "label": "Flow-i default semantic measurement example", "source": "default_seed"}
        ],
    },
    {
        "id": "measure_et_pccb_chain",
        "term": "PCCB Chain",
        "aliases": ["PCCB Chain", "PCCB_CHAIN", "PC CB Chain"],
        "source_type": "ET",
        "product": "",
        "step_id": "",
        "item_id": "PCCB_CHAIN",
        "value_column": "",
        "default_agg": "median",
        "target": None,
        "spec_low": None,
        "spec_high": None,
        "evidence": [
            {"type": "operator_seed", "label": "Flow-i default ET semantic measurement example", "source": "default_seed"}
        ],
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()[: max(1, limit)]


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _safe_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9가-힣._-]+", "_", str(value or "").strip()).strip("._-").lower()
    return ("measure_" + (text or uuid.uuid4().hex[:10]))[:100]


def _list(value: Any, limit: int = 20) -> list[str]:
    raw = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    out: list[str] = []
    for item in raw:
        text = _clean(item, 120)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _evidence_list(value: Any) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            item = {"label": item}
        if not isinstance(item, dict):
            continue
        row = {
            "type": _clean(item.get("type") or item.get("kind") or "manual", 80),
            "label": _clean(item.get("label") or item.get("title") or item.get("id") or "", 240),
            "source": _clean(item.get("source") or item.get("path") or item.get("url") or "", 300),
            "changed_at": _clean(item.get("changed_at") or item.get("updated_at") or "", 80),
        }
        out.append({k: v for k, v in row.items() if v})
    return out[:20]


def normalize_term(raw: dict[str, Any], *, actor: str = "system", base: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    base = deepcopy(base) if isinstance(base, dict) else {}
    term = _clean(raw.get("term") or base.get("term") or raw.get("id") or "Measurement term", 160)
    source_type = _upper(raw.get("source_type") or base.get("source_type") or "INLINE")
    product = _upper(raw.get("product") if "product" in raw else base.get("product"))
    default_agg = _clean(raw.get("default_agg") or base.get("default_agg") or ("avg" if source_type == "INLINE" else "median"), 40).lower()
    if default_agg not in {"avg", "mean", "median", "min", "max"}:
        default_agg = "avg" if source_type == "INLINE" else "median"
    aliases = _list(raw.get("aliases") if "aliases" in raw else base.get("aliases"), 30)
    if term not in aliases:
        aliases.insert(0, term)
    now = _now()
    return {
        "id": _clean(raw.get("id") or base.get("id") or _safe_id(f"{source_type}_{product}_{term}"), 100),
        "term": term,
        "aliases": aliases,
        "source_type": source_type,
        "product": product,
        "step_id": _upper(raw.get("step_id") if "step_id" in raw else base.get("step_id")),
        "item_id": _clean(raw.get("item_id") if "item_id" in raw else base.get("item_id"), 160),
        "value_column": _clean(raw.get("value_column") if "value_column" in raw else base.get("value_column"), 120),
        "default_agg": default_agg,
        "target": raw.get("target", base.get("target")),
        "spec_low": raw.get("spec_low", base.get("spec_low")),
        "spec_high": raw.get("spec_high", base.get("spec_high")),
        "evidence": _evidence_list(raw.get("evidence") if "evidence" in raw else base.get("evidence")),
        "created_at": str(base.get("created_at") or raw.get("created_at") or now),
        "updated_at": now,
        "updated_by": _clean(actor or raw.get("updated_by") or base.get("updated_by") or "system", 80),
    }


def default_terms() -> list[dict[str, Any]]:
    payload = load_json(DEFAULT_TERMS_FILE, {})
    raw_terms = payload.get("terms") if isinstance(payload, dict) and isinstance(payload.get("terms"), list) else DEFAULT_MEASUREMENT_TERMS
    return [normalize_term(row, actor="default_seed") for row in raw_terms if isinstance(row, dict)]


def _payload(terms: list[dict[str, Any]], *, actor: str = "system", created_at: str = "") -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "description": "Semantic measurement names mapped to source DB, step_id, item_id, specs, update metadata, and evidence.",
        "created_at": created_at or _now(),
        "updated_at": _now(),
        "updated_by": actor,
        "terms": sorted(terms, key=lambda r: (str(r.get("source_type") or ""), str(r.get("product") or ""), str(r.get("term") or ""))),
    }


def ensure_catalog(*, actor: str = "runtime") -> dict[str, Any]:
    existing = load_json(TERMS_FILE, None)
    defaults = default_terms()
    if not isinstance(existing, dict) or not isinstance(existing.get("terms"), list):
        payload = _payload(defaults, actor=actor)
        save_json(TERMS_FILE, payload, indent=2)
        return {**deepcopy(payload), "installed_defaults": len(defaults), "preserved": 0}
    by_id = {
        str(row.get("id") or ""): normalize_term(row, actor=str(row.get("updated_by") or actor))
        for row in existing.get("terms", [])
        if isinstance(row, dict) and row.get("id")
    }
    added = 0
    for row in defaults:
        if row["id"] not in by_id:
            by_id[row["id"]] = row
            added += 1
    payload = _payload(list(by_id.values()), actor=actor, created_at=str(existing.get("created_at") or ""))
    save_json(TERMS_FILE, payload, indent=2)
    return {**deepcopy(payload), "installed_defaults": added, "preserved": len(by_id) - added}


def load_catalog(*, ensure: bool = True) -> dict[str, Any]:
    if ensure:
        ensure_catalog(actor="runtime")
    data = load_json(TERMS_FILE, {})
    if not isinstance(data, dict) or not isinstance(data.get("terms"), list):
        data = _payload(default_terms())
    out = deepcopy(data)
    out["terms"] = [normalize_term(row, actor=str(row.get("updated_by") or "runtime")) for row in out.get("terms", []) if isinstance(row, dict)]
    out["path"] = str(TERMS_FILE)
    out["change_log_path"] = str(CHANGES_FILE)
    return out


def _log_change(action: str, term: dict[str, Any], *, actor: str) -> None:
    row = {
        "action": action,
        "actor": actor,
        "term_id": term.get("id") or "",
        "term": term.get("term") or "",
        "source_type": term.get("source_type") or "",
        "product": term.get("product") or "",
        "item_id": term.get("item_id") or "",
        "evidence": deepcopy(term.get("evidence") or []),
    }
    jsonl_append(CHANGES_FILE, row)
    jsonl_append(CHANGE_MANAGEMENT_HISTORY, {
        "history_id": "semantic_measure_" + uuid.uuid4().hex[:10],
        "run_id": "semantic_measure_" + uuid.uuid4().hex[:10],
        "timestamp": _now(),
        "username": actor,
        "prompt": f"semantic measurement term {action}: {term.get('term') or ''}",
        "natural_language": f"semantic measurement term {action}: {term.get('term') or ''}",
        "status": "success",
        "answer": f"{term.get('term') or ''} -> {term.get('source_type') or ''} {term.get('product') or ''} {term.get('item_id') or ''}",
        "needs_clarification": False,
        "meeting_reference": {},
        "meeting": {},
        "meetings": [],
        "sources": deepcopy(term.get("evidence") or []),
        "calendar_events": [],
        "llm": {"used": False},
        "warnings": [],
    }, add_timestamp=False)


def save_term(term: dict[str, Any], *, actor: str = "admin") -> dict[str, Any]:
    catalog = load_catalog(ensure=True)
    existing = {str(row.get("id") or ""): row for row in catalog.get("terms", []) if isinstance(row, dict)}
    term_id = str(term.get("id") or "").strip()
    base = existing.get(term_id) if term_id else None
    normalized = normalize_term(term, actor=actor, base=base)
    existing[normalized["id"]] = normalized
    save_json(TERMS_FILE, _payload(list(existing.values()), actor=actor, created_at=str(catalog.get("created_at") or "")), indent=2)
    _log_change("save", normalized, actor=actor)
    return normalized


def match_terms(prompt: str, *, product: str = "", limit: int = 6) -> list[dict[str, Any]]:
    prompt_norm = _norm(prompt)
    product_u = _upper(product)
    prompt_product = _first_product(prompt)
    matches: list[dict[str, Any]] = []
    for term in load_catalog(ensure=True).get("terms", []):
        score = 0.0
        for alias in [term.get("term"), *list(term.get("aliases") or [])]:
            alias_norm = _norm(alias)
            if len(alias_norm) >= 2 and alias_norm in prompt_norm:
                score += 5.0 if alias == term.get("term") else 4.0
                break
        term_product = _upper(term.get("product"))
        if term_product:
            if product_u and product_u == term_product:
                score += 1.0
            if prompt_product and prompt_product == term_product:
                score += 1.0
        source_type = _upper(term.get("source_type"))
        if source_type and source_type.casefold() in str(prompt or "").casefold():
            score += 1.5
        if score <= 0:
            continue
        row = deepcopy(term)
        row["score"] = round(score, 3)
        matches.append(row)
    return sorted(matches, key=lambda r: (-float(r.get("score") or 0), str(r.get("term") or "")))[: max(1, int(limit or 6))]


def _first_product(prompt: str) -> str:
    for token in re.findall(r"\b[A-Z]{3,}[A-Z0-9_]*\b", str(prompt or "")):
        if token.upper() not in {"INLINE", "ET", "FAB", "VM", "MASK", "KNOB", "CSV"}:
            return token.upper()
    return ""


def _first_root_lot(prompt: str) -> str:
    for token in re.findall(r"\b[A-Z][A-Z0-9]?\d{3,}[A-Z0-9_.-]*\b", str(prompt or ""), flags=re.I):
        if "." not in token:
            return token.upper()
    return ""


def _wafer_tokens(prompt: str) -> list[str]:
    out = []
    for token in re.findall(r"(?:#|WF|WAFER|W)\s*(\d{1,2})\b", str(prompt or ""), flags=re.I):
        n = int(token)
        if 1 <= n <= 25 and str(n) not in out:
            out.append(str(n))
    return out


def _source_files(source_type: str, product: str = "") -> list[Path]:
    source_u = _upper(source_type)
    root = PATHS.db_root
    if not root.exists():
        return []
    files = [p for p in list(root.rglob("*.parquet")) + list(root.rglob("*.csv")) if source_u in _upper("/".join(p.parts[-6:]))]
    product_u = _upper(product)
    if product_u:
        filtered = [p for p in files if product_u in _upper("/".join(p.parts[-6:])) or product_u in _upper(p.name)]
        if filtered:
            files = filtered
    return sorted(set(files))


def _scan_files(files: list[Path]) -> pl.LazyFrame | None:
    parquet = [str(p) for p in files if p.suffix.lower() == ".parquet"]
    csv = [str(p) for p in files if p.suffix.lower() == ".csv"]
    if parquet:
        return pl.scan_parquet(parquet)
    if csv:
        frames = [pl.scan_csv(path, infer_schema_length=200) for path in csv]
        return pl.concat(frames, how="diagonal_relaxed") if len(frames) > 1 else frames[0]
    return None


def _ci_col(cols: list[str], *names: str) -> str:
    lookup = {_upper(c): c for c in cols}
    for name in names:
        hit = lookup.get(_upper(name))
        if hit:
            return hit
    return ""


def _root_expr(col: str) -> pl.Expr:
    return pl.col(col).cast(_STR, strict=False).str.strip_chars().str.to_uppercase()


def _wafer_expr(col: str) -> pl.Expr:
    return (
        pl.col(col)
        .cast(_STR, strict=False)
        .str.strip_chars()
        .str.to_uppercase()
        .str.replace(r"^(?:#|WAFER|WF|W)\s*", "")
        .cast(pl.Int64, strict=False)
        .cast(_STR, strict=False)
    )


def _item_candidates(item_id: str, aliases: list[str]) -> list[str]:
    values = [item_id, item_id.replace(" ", "_"), item_id.replace("_", " ")]
    values.extend(aliases or [])
    out = []
    for value in values:
        text = _clean(value, 160)
        if text and text not in out:
            out.append(text)
    return out


def query_measurement(prompt: str, *, product: str = "", max_rows: int = 25) -> dict[str, Any] | None:
    matches = match_terms(prompt, product=product, limit=1)
    if not matches:
        return None
    term = matches[0]
    product_u = _upper(product) or _first_product(prompt) or _upper(term.get("product"))
    root_lot = _first_root_lot(prompt)
    wafers = _wafer_tokens(prompt)
    source_type = _upper(term.get("source_type"))
    item_id = _clean(term.get("item_id"), 160)
    files = _source_files(source_type, product_u)
    if not files:
        return _measurement_result(prompt, term, product_u, root_lot, [], warnings=[f"{source_type} source files not found"])
    lf = _scan_files(files)
    if lf is None:
        return _measurement_result(prompt, term, product_u, root_lot, [], warnings=[f"{source_type} scan failed"])
    cols = lf.collect_schema().names()
    product_col = _ci_col(cols, "product", "PRODUCT", "product_id", "PRODUCT_ID")
    root_col = _ci_col(cols, "root_lot_id", "ROOT_LOT_ID", "root_lot", "ROOT_LOT")
    wafer_col = _ci_col(cols, "wafer_id", "WAFER_ID", "wf_id", "WF_ID", "wafer", "WAFER")
    item_col = _ci_col(cols, "item_id", "ITEM_ID", "rawitem_id", "RAWITEM_ID", "item", "ITEM")
    step_col = _ci_col(cols, "step_id", "STEP_ID", "function_step", "FUNCTION_STEP")
    value_col = _clean(term.get("value_column"), 120) if _clean(term.get("value_column"), 120) in cols else ""
    if not value_col:
        value_col = _ci_col(cols, "value", "VALUE", "measure_value", "MEASURE_VALUE", "result", "RESULT", "val", "VAL")
    if not value_col and item_id:
        value_col = _ci_col(cols, item_id, item_id.replace(" ", "_"), item_id.replace("_", " "))
    warnings = []
    if not root_col:
        warnings.append("root_lot_id column not found")
    if not wafer_col:
        warnings.append("wafer_id column not found")
    if not value_col:
        warnings.append("measurement value column not found")
    if warnings:
        return _measurement_result(prompt, term, product_u, root_lot, [], warnings=warnings)

    filters: list[pl.Expr] = []
    if product_col and product_u:
        filters.append(pl.col(product_col).cast(_STR, strict=False).str.to_uppercase() == product_u)
    if root_lot and root_col:
        filters.append(_root_expr(root_col) == root_lot)
    if wafers and wafer_col:
        filters.append(_wafer_expr(wafer_col).is_in(wafers))
    if item_col and item_id:
        candidates = [_norm(v) for v in _item_candidates(item_id, term.get("aliases") or []) if _norm(v)]
        if candidates:
            item_text = pl.col(item_col).cast(_STR, strict=False).str.replace_all(r"[^0-9A-Za-z가-힣]+", "").str.to_lowercase()
            filters.append(pl.any_horizontal([item_text.str.contains(c, literal=True) for c in candidates]))
    if step_col and term.get("step_id"):
        filters.append(pl.col(step_col).cast(_STR, strict=False).str.to_uppercase() == _upper(term.get("step_id")))
    for expr in filters:
        lf = lf.filter(expr)

    value_alias = "value_avg" if term.get("default_agg") in {"avg", "mean"} else f"value_{term.get('default_agg') or 'median'}"
    exprs: list[pl.Expr] = [
        _root_expr(root_col).alias("root_lot_id"),
        _wafer_expr(wafer_col).alias("wafer_id"),
        pl.col(value_col).cast(pl.Float64, strict=False).alias("_value"),
    ]
    if product_col:
        exprs.append(pl.col(product_col).cast(_STR, strict=False).alias("product"))
    else:
        exprs.append(pl.lit(product_u).alias("product"))
    if item_col:
        exprs.append(pl.col(item_col).cast(_STR, strict=False).alias("item_id"))
    else:
        exprs.append(pl.lit(item_id).alias("item_id"))
    if step_col:
        exprs.append(pl.col(step_col).cast(_STR, strict=False).alias("step_id"))
    else:
        exprs.append(pl.lit(_upper(term.get("step_id"))).alias("step_id"))
    scoped = lf.select(exprs).drop_nulls(subset=["_value"])
    agg = str(term.get("default_agg") or "").lower()
    value_expr = pl.col("_value").mean().alias(value_alias) if agg in {"avg", "mean"} else getattr(pl.col("_value"), agg, pl.col("_value").median)().alias(value_alias)
    try:
        df = (
            scoped.group_by(["root_lot_id", "wafer_id"])
            .agg([
                value_expr,
                pl.len().alias("n"),
                pl.col("product").drop_nulls().first().alias("product"),
                pl.col("item_id").drop_nulls().first().alias("item_id"),
                pl.col("step_id").drop_nulls().first().alias("step_id"),
            ])
            .sort("wafer_id")
            .limit(max(1, min(int(max_rows or 25), 200)))
            .collect()
        )
    except Exception as exc:
        return _measurement_result(prompt, term, product_u, root_lot, [], warnings=[f"measurement query failed: {str(exc)[:160]}"])
    rows = df.to_dicts()
    for row in rows:
        row["source_type"] = source_type
        row["term"] = term.get("term") or ""
        row["target"] = term.get("target")
        row["spec_low"] = term.get("spec_low")
        row["spec_high"] = term.get("spec_high")
        row["semantic_updated_at"] = term.get("updated_at") or ""
    return _measurement_result(
        prompt,
        term,
        product_u,
        root_lot,
        rows,
        warnings=[],
        source_files=[str(p) for p in files[:6]],
        value_alias=value_alias,
    )


def _measurement_result(
    prompt: str,
    term: dict[str, Any],
    product: str,
    root_lot: str,
    rows: list[dict[str, Any]],
    *,
    warnings: list[str],
    source_files: list[str] | None = None,
    value_alias: str = "value_avg",
) -> dict[str, Any]:
    source_type = _upper(term.get("source_type"))
    agg = term.get("default_agg") or ("avg" if source_type == "INLINE" else "median")
    columns = [
        {"key": "root_lot_id", "label": "root_lot_id"},
        {"key": "wafer_id", "label": "wafer_id"},
        {"key": value_alias, "label": f"{agg} value"},
        {"key": "n", "label": "n"},
        {"key": "source_type", "label": "source"},
        {"key": "item_id", "label": "item_id"},
        {"key": "step_id", "label": "step_id"},
        {"key": "target", "label": "target"},
        {"key": "spec_low", "label": "spec_low"},
        {"key": "spec_high", "label": "spec_high"},
    ]
    answer = (
        f"{product or term.get('product') or '-'} {root_lot or '-'} {term.get('term')} 값을 "
        f"{source_type} source에서 wafer별 {agg}로 조회했습니다. 결과 {len(rows)}건."
    )
    if warnings:
        answer = f"{term.get('term')} semantic term은 찾았지만 조회가 완료되지 않았습니다: " + "; ".join(warnings[:3])
    return {
        "handled": True,
        "intent": "semantic_measurement_lookup",
        "action": "query_semantic_measurement",
        "feature": "filebrowser_ai_sql",
        "answer": answer,
        "slots": {
            "product": product or term.get("product") or "",
            "root_lot_id": root_lot,
            "source_type": source_type,
            "item_id": term.get("item_id") or "",
            "semantic_term": term.get("term") or "",
            "agg": agg,
        },
        "table": {
            "kind": "semantic_measurement",
            "title": f"{term.get('term')} measurement by wafer",
            "columns": columns,
            "rows": rows,
            "total": len(rows),
        },
        "source_ids": [source_type, *(source_files or [])],
        "term_resolution": [
            {
                "token": term.get("term") or "",
                "meaning": f"{source_type} {term.get('product') or product or ''} item_id={term.get('item_id') or ''}",
                "updated_at": term.get("updated_at") or "",
                "evidence": deepcopy(term.get("evidence") or []),
            }
        ],
        "measurement_term": deepcopy(term),
        "warnings": warnings,
    }
