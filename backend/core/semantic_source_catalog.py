"""Semantic data source catalog for Agent source search.

The catalog is intentionally dict-backed so API payloads, resolver hints, UI
panels, and docs links all read from one stable source of truth.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from core.paths import PATHS
from core.utils import jsonl_append, load_json, save_json


DOCS_BASE = "docs/semantic"
SOURCE_FILE = PATHS.data_root / "semantic" / "source_catalog.json"
CHANGES_FILE = PATHS.data_root / "semantic" / "source_catalog.changes.jsonl"

SEMANTIC_SOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "rulebook": {
        "id": "rulebook",
        "title": "PPID knob rulebook",
        "role": "rulebook",
        "roles": ["rulebook", "knob_rulebook", "source_search"],
        "path_patterns": ["FLOW_DB_ROOT/ppid_knob.csv"],
        "fallback_path_patterns": [],
        "owner": "SplitTable rulebook and FileBrowser base-file owners",
        "write_policy": "Agent read-only. Update only through approved FileBrowser/SplitTable manager or admin save paths.",
        "docs_path": f"{DOCS_BASE}/rulebook.md",
        "related_question_ids": ["Q1"],
        "related_unit_keys": ["ppid_knob"],
        "columns": ["feature_name", "function_step", "rule_order", "operator", "value", "category"],
        "search_terms": [
            "ppid",
            "knob",
            "split",
            "rulebook",
            "feature_name",
            "category",
            "function_step",
            "rule_order",
            "노브",
            "분류",
        ],
        "base_confidence": 0.51,
    },
    "step_matching": {
        "id": "step_matching",
        "title": "Step matching CSV",
        "role": "step_matching",
        "roles": ["step_matching", "matching_csv", "source_search"],
        "path_patterns": ["FLOW_DB_ROOT/Vehicle_matching.csv"],
        "fallback_path_patterns": ["FLOW_DB_ROOT/step_matching.csv"],
        "owner": "SplitTable matching table and FileBrowser base-file owners",
        "write_policy": "Agent read-only. Update only through approved matching-table or base-file save paths.",
        "docs_path": f"{DOCS_BASE}/step_matching.md",
        "related_question_ids": ["Q2"],
        "related_unit_keys": ["step_lookup"],
        "columns": ["product", "step_id", "function_step", "step_desc"],
        "search_terms": [
            "step",
            "step_id",
            "function_step",
            "step_desc",
            "vehicle_matching",
            "matching",
            "공정",
            "스텝",
        ],
        "base_confidence": 0.49,
    },
    "split_base": {
        "id": "split_base",
        "title": "SplitTable base parquet",
        "role": "split_base",
        "roles": ["split_base", "raw_export", "filebrowser_source", "source_search"],
        "path_patterns": ["FLOW_DB_ROOT/ML_TABLE_<product>.parquet"],
        "fallback_path_patterns": [],
        "owner": "SplitTable and FileBrowser source owners",
        "write_policy": "Agent read-only. Raw export and preview are read paths; writes require owner feature APIs.",
        "docs_path": f"{DOCS_BASE}/split_base.md",
        "related_question_ids": ["Q4"],
        "related_unit_keys": ["filebrowser_ai_sql"],
        "columns": ["root_lot_id", "wafer_id", "fab_lot_id", "KNOB_*", "INLINE_*", "VM_*"],
        "search_terms": [
            "ml_table",
            "split table",
            "splittable",
            "split_base",
            "raw export",
            "export",
            "wafer",
            "root_lot",
            "root_lot_id",
            "split",
            "세트",
            "스플릿",
        ],
        "base_confidence": 0.47,
    },
    "fab_db": {
        "id": "fab_db",
        "title": "FAB raw parquet",
        "role": "fab_db",
        "roles": ["fab_db", "lot_progress", "current_location", "source_search"],
        "path_patterns": ["FLOW_DB_ROOT/1.RAWDATA_DB_FAB/<product>/**/*.parquet"],
        "fallback_path_patterns": [],
        "owner": "DB ops, FileBrowser, and lot-progress cache owners",
        "write_policy": "Agent read-only. Refresh caches or source data only through owner jobs and feature APIs.",
        "docs_path": f"{DOCS_BASE}/fab_db.md",
        "related_question_ids": ["Q3"],
        "related_unit_keys": ["filebrowser_ai_sql"],
        "columns": ["lot_id", "root_lot_id", "wafer_id", "step_id", "equipment", "tkout_time"],
        "search_terms": [
            "fab",
            "latest step",
            "current location",
            "current step",
            "lot progress",
            "progress",
            "equipment",
            "location",
            "진행",
            "현재위치",
            "위치",
        ],
        "base_confidence": 0.45,
    },
    "inline_db": {
        "id": "inline_db",
        "title": "Inline measurement DB",
        "role": "inline_db",
        "roles": ["inline_db", "measurement", "trend_source", "source_search"],
        "path_patterns": ["FLOW_DB_ROOT/**/INLINE*/<product>/**/*.parquet", "FLOW_DB_ROOT/1.RAWDATA_DB_INLINE/<product>/**/*.parquet"],
        "fallback_path_patterns": ["FLOW_DB_ROOT/**/*INLINE*<product>*.parquet"],
        "owner": "Inline measurement DB owners and FileBrowser source owners",
        "write_policy": "Agent read-only. Semantic aliases/specs are editable through Semantic layer; raw Inline files are source-owner managed.",
        "docs_path": f"{DOCS_BASE}/inline_db.md",
        "related_question_ids": ["Q11"],
        "related_unit_keys": ["filebrowser_ai_sql", "dashboard_agent"],
        "columns": ["product", "root_lot_id", "wafer_id", "step_id", "item_id", "value", "target", "spec_low", "spec_high", "tkout_time"],
        "search_terms": [
            "inline",
            "inline db",
            "measurement",
            "measure",
            "item_id",
            "target",
            "spec_low",
            "spec_high",
            "trend",
            "측정",
            "인라인",
        ],
        "base_confidence": 0.44,
    },
    "et_db": {
        "id": "et_db",
        "title": "ET measurement DB",
        "role": "et_db",
        "roles": ["et_db", "measurement", "electrical_test", "trend_source", "source_search"],
        "path_patterns": ["FLOW_DB_ROOT/**/ET*/<product>/**/*.parquet", "FLOW_DB_ROOT/1.RAWDATA_DB_ET/<product>/**/*.parquet"],
        "fallback_path_patterns": ["FLOW_DB_ROOT/**/*ET*<product>*.parquet"],
        "owner": "ET measurement DB owners and FileBrowser source owners",
        "write_policy": "Agent read-only. Semantic aliases/specs are editable through Semantic layer; raw ET files are source-owner managed.",
        "docs_path": f"{DOCS_BASE}/et_db.md",
        "related_question_ids": ["Q12"],
        "related_unit_keys": ["filebrowser_ai_sql", "dashboard_agent"],
        "columns": ["product", "root_lot_id", "wafer_id", "step_id", "item_id", "value", "target", "spec_low", "spec_high", "tkout_time"],
        "search_terms": [
            "et",
            "electrical test",
            "pccb",
            "chain",
            "measurement",
            "item_id",
            "target",
            "spec_low",
            "spec_high",
            "trend",
            "전기",
            "측정",
        ],
        "base_confidence": 0.44,
    },
}


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _clean_text(value: Any, limit: int = 240) -> str:
    return str(value or "").replace("\x00", " ").strip()[: max(1, limit)]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip()).strip("._-").lower()
    return (text or "semantic_source")[:100]


def _list_text(value: Any, limit: int = 80) -> list[str]:
    raw = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    out: list[str] = []
    for item in raw:
        text = _clean_text(item, 300)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _load_disk_payload() -> dict[str, Any]:
    data = load_json(SOURCE_FILE, {})
    return data if isinstance(data, dict) else {}


def _disk_sources_from_payload(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw_sources = payload.get("sources")
    if isinstance(raw_sources, dict):
        items = raw_sources.items()
    elif isinstance(raw_sources, list):
        items = ((row.get("id"), row) for row in raw_sources if isinstance(row, dict))
    else:
        items = []
    out: dict[str, dict[str, Any]] = {}
    for key, value in items:
        if not isinstance(value, dict):
            continue
        source_id = _safe_id(value.get("id") or key)
        if source_id:
            out[source_id] = value
    return out


def _deleted_ids(payload: dict[str, Any]) -> set[str]:
    return {_safe_id(value) for value in (payload.get("deleted_ids") or []) if _safe_id(value)}


def _save_disk_payload(sources: dict[str, dict[str, Any]], deleted_ids: set[str], *, actor: str) -> None:
    save_json(
        SOURCE_FILE,
        {
            "version": 1,
            "description": "Operator editable semantic source catalog overrides. Source data files remain owner-managed.",
            "updated_at": _now(),
            "updated_by": _clean_text(actor or "system", 80),
            "deleted_ids": sorted(deleted_ids),
            "sources": sorted(sources.values(), key=lambda row: str(row.get("id") or "")),
        },
        indent=2,
    )


def _log_change(action: str, source: dict[str, Any], *, actor: str) -> None:
    jsonl_append(CHANGES_FILE, {
        "action": action,
        "actor": _clean_text(actor or "system", 80),
        "source_id": source.get("id") or "",
        "title": source.get("title") or "",
        "role": source.get("role") or "",
    })


def normalize_source(raw: dict[str, Any], *, actor: str = "system", base: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    base = deepcopy(base) if isinstance(base, dict) else {}
    source_id = _safe_id(raw.get("id") or base.get("id") or raw.get("title") or base.get("title"))
    now = _now()
    return {
        "id": source_id,
        "title": _clean_text(raw.get("title") if "title" in raw else base.get("title") or source_id, 160),
        "role": _clean_text(raw.get("role") if "role" in raw else base.get("role") or source_id, 100),
        "roles": _list_text(raw.get("roles") if "roles" in raw else base.get("roles"), 30),
        "path_patterns": _list_text(raw.get("path_patterns") if "path_patterns" in raw else base.get("path_patterns"), 40),
        "fallback_path_patterns": _list_text(raw.get("fallback_path_patterns") if "fallback_path_patterns" in raw else base.get("fallback_path_patterns"), 40),
        "owner": _clean_text(raw.get("owner") if "owner" in raw else base.get("owner"), 240),
        "write_policy": _clean_text(raw.get("write_policy") if "write_policy" in raw else base.get("write_policy"), 320),
        "docs_path": _clean_text(raw.get("docs_path") if "docs_path" in raw else base.get("docs_path") or f"{DOCS_BASE}/{source_id}.md", 240),
        "related_question_ids": _list_text(raw.get("related_question_ids") if "related_question_ids" in raw else base.get("related_question_ids"), 30),
        "related_unit_keys": _list_text(raw.get("related_unit_keys") if "related_unit_keys" in raw else base.get("related_unit_keys"), 30),
        "columns": _list_text(raw.get("columns") if "columns" in raw else base.get("columns"), 80),
        "search_terms": _list_text(raw.get("search_terms") if "search_terms" in raw else base.get("search_terms"), 80),
        "base_confidence": max(0.01, min(float(raw.get("base_confidence", base.get("base_confidence", 0.42)) or 0.42), 0.99)),
        "created_at": str(base.get("created_at") or raw.get("created_at") or now),
        "updated_at": now,
        "updated_by": _clean_text(actor or raw.get("updated_by") or base.get("updated_by") or "system", 80),
    }


def catalog_sources() -> dict[str, dict[str, Any]]:
    payload = _load_disk_payload()
    deleted = _deleted_ids(payload)
    sources = {
        source_id: deepcopy(source)
        for source_id, source in SEMANTIC_SOURCE_CATALOG.items()
        if source_id not in deleted
    }
    for source_id, raw in _disk_sources_from_payload(payload).items():
        sources[source_id] = normalize_source(raw, actor=str(raw.get("updated_by") or "runtime"), base=sources.get(source_id) or SEMANTIC_SOURCE_CATALOG.get(source_id))
    return sources


def disk_sources() -> dict[str, dict[str, Any]]:
    payload = _load_disk_payload()
    return {
        source_id: normalize_source(raw, actor=str(raw.get("updated_by") or "runtime"), base=SEMANTIC_SOURCE_CATALOG.get(source_id))
        for source_id, raw in _disk_sources_from_payload(payload).items()
    }


def deleted_source_ids() -> list[str]:
    return sorted(_deleted_ids(_load_disk_payload()))


def save_source(source: dict[str, Any], *, actor: str = "admin") -> dict[str, Any]:
    payload = _load_disk_payload()
    sources = _disk_sources_from_payload(payload)
    deleted = _deleted_ids(payload)
    raw_id = _safe_id((source or {}).get("id"))
    base = (catalog_sources().get(raw_id) if raw_id else None) or (SEMANTIC_SOURCE_CATALOG.get(raw_id) if raw_id else None)
    normalized = normalize_source(source, actor=actor, base=base)
    sources[normalized["id"]] = normalized
    deleted.discard(normalized["id"])
    _save_disk_payload(sources, deleted, actor=actor)
    _log_change("save", normalized, actor=actor)
    return normalized


def delete_source(source_id: str, *, actor: str = "admin") -> bool:
    source_id = _safe_id(source_id)
    if not source_id:
        return False
    payload = _load_disk_payload()
    sources = _disk_sources_from_payload(payload)
    deleted = _deleted_ids(payload)
    existed = source_id in sources or source_id in SEMANTIC_SOURCE_CATALOG
    removed = sources.pop(source_id, None)
    if source_id in SEMANTIC_SOURCE_CATALOG:
        deleted.add(source_id)
    if not existed:
        return False
    _save_disk_payload(sources, deleted, actor=actor)
    _log_change("delete", removed or SEMANTIC_SOURCE_CATALOG.get(source_id) or {"id": source_id}, actor=actor)
    return True


def catalog_roles() -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for source_id, source in catalog_sources().items():
        for role in [source.get("role"), *list(source.get("roles") or [])]:
            role_text = _clean_text(role, 80)
            if not role_text:
                continue
            roles.setdefault(role_text, [])
            if source_id not in roles[role_text]:
                roles[role_text].append(source_id)
    return roles


def _source_ref_reasons(source_id: str, source_ref: dict[str, Any] | None, sample_profile: dict[str, Any] | None) -> list[str]:
    if not isinstance(source_ref, dict):
        source_ref = {}
    if not isinstance(sample_profile, dict):
        sample_profile = {}
    root = _clean_text(source_ref.get("root"), 160)
    file_name = _clean_text(source_ref.get("file"), 240)
    product = _clean_text(source_ref.get("product"), 160)
    sample_source = _clean_text(sample_profile.get("source"), 240)
    haystack = " ".join([root, file_name, product, sample_source]).casefold()
    reasons: list[str] = []
    if source_id == "rulebook" and "ppid_knob.csv" in haystack:
        reasons.append("source_ref:ppid_knob.csv")
    if source_id == "step_matching" and ("vehicle_matching.csv" in haystack or "step_matching.csv" in haystack):
        reasons.append("source_ref:step_matching")
    if source_id == "split_base" and ("ml_table" in haystack or root.casefold() == "ml_table"):
        reasons.append("source_ref:ML_TABLE")
    if source_id == "fab_db" and ("rawdata_db_fab" in haystack or root.casefold() == "fab" or "/fab/" in haystack):
        reasons.append("source_ref:FAB")
    return reasons


def source_catalog_matches(
    prompt: str,
    *,
    source_ref: dict[str, Any] | None = None,
    sample_profile: dict[str, Any] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    prompt_norm = _norm(prompt)
    matches: dict[str, dict[str, Any]] = {}
    sources = catalog_sources()

    def add(source_id: str, confidence: float, reason: str) -> None:
        source = sources.get(source_id)
        if not source:
            return
        row = matches.setdefault(
            source_id,
            {
                "source_id": source_id,
                "role": source.get("role") or source_id,
                "title": source.get("title") or source_id,
                "path_patterns": list(source.get("path_patterns") or []),
                "fallback_path_patterns": list(source.get("fallback_path_patterns") or []),
                "docs_path": source.get("docs_path") or f"{DOCS_BASE}/{source_id}.md",
                "related_question_ids": list(source.get("related_question_ids") or []),
                "confidence": 0.0,
                "match_reasons": [],
            },
        )
        row["confidence"] = max(float(row.get("confidence") or 0.0), confidence)
        if reason and reason not in row["match_reasons"]:
            row["match_reasons"].append(reason)

    for source_id, source in sources.items():
        token_hits = 0
        for term in source.get("search_terms") or []:
            term_norm = _norm(term)
            if len(term_norm) < 2:
                continue
            if prompt_norm and term_norm in prompt_norm:
                token_hits += 1
                add(
                    source_id,
                    min(0.91, float(source.get("base_confidence") or 0.42) + (token_hits * 0.05)),
                    f"term:{term}",
                )
        for reason in _source_ref_reasons(source_id, source_ref, sample_profile):
            add(source_id, max(0.36, float(source.get("base_confidence") or 0.42) - 0.08), reason)

    out = list(matches.values())
    out.sort(key=lambda item: (-float(item.get("confidence") or 0.0), str(item.get("source_id") or "")))
    return out[: max(1, min(int(limit or 8), 20))]


def search_priorities_for_term(
    term: str,
    *,
    source_ref: dict[str, Any] | None = None,
    sample_profile: dict[str, Any] | None = None,
    limit: int = 4,
) -> list[dict[str, Any]]:
    priorities: list[dict[str, Any]] = []
    for match in source_catalog_matches(
        term,
        source_ref=source_ref,
        sample_profile=sample_profile,
        limit=limit,
    ):
        paths = [*list(match.get("path_patterns") or []), *list(match.get("fallback_path_patterns") or [])]
        priorities.append({
            "location": match.get("role") or "source_catalog",
            "table_file": ", ".join(paths),
            "source_id": match.get("source_id") or "",
            "docs_path": match.get("docs_path") or "",
            "confidence": min(0.82, float(match.get("confidence") or 0.42)),
        })
    return priorities
