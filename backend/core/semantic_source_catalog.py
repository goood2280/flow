"""Semantic data source catalog for Agent source search.

The catalog is intentionally dict-backed so API payloads, resolver hints, UI
panels, and docs links all read from one stable source of truth.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


DOCS_BASE = "docs/semantic"

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


def catalog_sources() -> dict[str, dict[str, Any]]:
    return deepcopy(SEMANTIC_SOURCE_CATALOG)


def catalog_roles() -> dict[str, list[str]]:
    roles: dict[str, list[str]] = {}
    for source_id, source in SEMANTIC_SOURCE_CATALOG.items():
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

    def add(source_id: str, confidence: float, reason: str) -> None:
        source = SEMANTIC_SOURCE_CATALOG.get(source_id)
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

    for source_id, source in SEMANTIC_SOURCE_CATALOG.items():
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
