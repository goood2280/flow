from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app_v2.modules.semantic_learning import extractor as semantic_extractor
from app_v2.modules.semantic_learning import inbox as semantic_inbox
from app_v2.modules.semantic_learning import proposer as semantic_proposer
from app_v2.modules.semantic_lexicon import service as semantic_lexicon_service
from app_v2.modules.semantic_lexicon import store as semantic_lexicon_store
from core import home_orchestrator
from core import agent_feedback_penalties
from core import agent_prompt_overrides
from core import semantic_source_catalog
from core import semantic_measure_catalog
from core.auth import current_user, is_page_manager
from core.flowi_units import all_unit_ais, get_unit_ai
from core.flowi_units.change_management_runtime import (
    UNIT_AI_KEY as CHANGE_MANAGEMENT_UNIT_KEY,
    change_management_graph,
    list_change_management_history,
    run_change_management_runtime,
)
from core.flowi_units.dashboard_agent_runtime import (
    UNIT_AI_KEY as DASHBOARD_AGENT_UNIT_KEY,
    dashboard_agent_graph,
    list_dashboard_agent_history,
    record_dashboard_agent_history,
    run_dashboard_agent_runtime,
)
from core.flowi_units.deterministic_lookup_runtime import (
    RUNTIME_UNIT_KEYS as DETERMINISTIC_LOOKUP_UNIT_KEYS,
    deterministic_lookup_graph,
    list_deterministic_lookup_history,
    run_deterministic_lookup_runtime,
)
from core.flowi_units.filebrowser_ai_sql_runtime import (
    UNIT_AI_KEY as FILEBROWSER_AI_SQL_UNIT_KEY,
    filebrowser_ai_sql_graph,
    run_filebrowser_ai_sql_runtime,
)
from core.flowi_units.inform_registration_runtime import (
    UNIT_AI_KEY as INFORM_REGISTRATION_UNIT_KEY,
    inform_registration_graph,
    list_inform_registration_history,
    run_inform_registration_runtime,
)

router = APIRouter(prefix="/api/agent", tags=["agent"])

_APP_ROOT = Path(__file__).resolve().parents[2]

_ACTIVE_UNIT_ENDPOINTS = {
    FILEBROWSER_AI_SQL_UNIT_KEY: {
        "graph": "/api/agent/unit-ai/filebrowser_ai_sql/runtime/graph",
        "run": "/api/agent/unit-ai/filebrowser_ai_sql/runtime/run",
    },
    INFORM_REGISTRATION_UNIT_KEY: {
        "graph": "/api/agent/unit-ai/inform_registration/runtime/graph",
        "run": "/api/agent/unit-ai/inform_registration/runtime/run",
        "history": "/api/agent/unit-ai/inform_registration/runtime/history",
    },
    CHANGE_MANAGEMENT_UNIT_KEY: {
        "graph": "/api/agent/unit-ai/change_management/runtime/graph",
        "run": "/api/agent/unit-ai/change_management/runtime/run",
        "history": "/api/agent/unit-ai/change_management/runtime/history",
    },
    DASHBOARD_AGENT_UNIT_KEY: {
        "graph": "/api/agent/unit-ai/dashboard_agent/runtime/graph",
        "run": "/api/agent/unit-ai/dashboard_agent/runtime/run",
        "history": "/api/agent/unit-ai/dashboard_agent/runtime/history",
        "overrides": "/api/agent/unit-ai/dashboard_agent/runtime/overrides",
    },
    "step_lookup": {
        "graph": "/api/agent/unit-ai/step_lookup/runtime/graph",
        "run": "/api/agent/unit-ai/step_lookup/runtime/run",
        "history": "/api/agent/unit-ai/step_lookup/runtime/history",
    },
    "ppid_knob": {
        "graph": "/api/agent/unit-ai/ppid_knob/runtime/graph",
        "run": "/api/agent/unit-ai/ppid_knob/runtime/run",
        "history": "/api/agent/unit-ai/ppid_knob/runtime/history",
    },
}


def _unit_v2_endpoints(unit_key: str) -> dict[str, str]:
    base = f"/api/agent/unit/{unit_key}"
    return {
        "graph": f"{base}/graph",
        "run": f"{base}/run",
        "history": f"{base}/history",
        "overrides": f"/api/agent/unit-ai/{unit_key}/runtime/overrides",
    }


def _clean_commit(value: str) -> str:
    commit = str(value or "").strip()
    if len(commit) >= 7 and all(ch in "0123456789abcdef" for ch in commit.lower()):
        return commit
    return ""


def _backend_commit(root: Path) -> str:
    for key in ("FLOW_BUILD_COMMIT", "GIT_COMMIT", "COMMIT_SHA"):
        commit = _clean_commit(os.environ.get(key, ""))
        if commit:
            return commit
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        )
    except Exception:
        return ""
    return _clean_commit(raw)


def _version_metadata(root: Path) -> dict[str, str]:
    version_file = root / "VERSION.json"
    modified_at = ""
    try:
        modified_at = datetime.datetime.fromtimestamp(version_file.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        pass

    meta: dict[str, Any] = {}
    try:
        loaded = json.loads(version_file.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            meta = loaded
    except Exception:
        meta = {}

    release_version = str(meta.get("version") or "").strip()
    return {
        "backend_version": modified_at or release_version or "unknown",
        "backend_release_version": release_version,
        "backend_version_source": "mtime" if modified_at else ("VERSION.json" if release_version else "unknown"),
        "backend_codename": str(meta.get("codename") or "").strip(),
    }


class FileBrowserAiSqlRuntimeRunReq(BaseModel):
    natural_language: str = ""
    scope: str = "db_product"
    root: str = ""
    product: str = ""
    file: str = ""
    columns: list[str] = Field(default_factory=list)
    dtypes: dict[str, str] = Field(default_factory=dict)
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    preferred_selected_columns: list[str] = Field(default_factory=list)


class UnitAiRuntimeRunReq(BaseModel):
    model_config = {"extra": "allow"}

    prompt: str = ""
    session_id: str = ""
    action: str = "continue"
    slot_overrides: dict[str, Any] = Field(default_factory=dict)


class SemanticAliasGroupReq(BaseModel):
    aliases: list[str] = Field(default_factory=list)
    semantic_class: str = ""
    normalization: Any = None
    value_domain: Any = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SemanticIntentHintReq(BaseModel):
    required_canonicals: list[str] = Field(default_factory=list)


class SemanticProposalDecisionReq(BaseModel):
    decision: str = "reject"
    canonical: str = ""


class SemanticDraftReq(BaseModel):
    text: str = ""


class SemanticMeasurementTermReq(BaseModel):
    term: dict[str, Any] = Field(default_factory=dict)


class SemanticSourceCatalogReq(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)


class UnitAiOverrideReq(BaseModel):
    nodes: dict[str, dict[str, Any]] = Field(default_factory=dict)


class UnitAiFeedbackReq(BaseModel):
    rating: str
    node_id: str = ""
    run_id: str = ""
    reason: str = ""


def _string_list(value: Any, limit: int = 80) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _semantic_seed_alias_groups() -> dict[str, list[str]]:
    try:
        from app_v2.modules.agent_runtime.semantic import _ALIAS_GROUPS  # type: ignore

        return {str(key): _string_list(value) for key, value in dict(_ALIAS_GROUPS).items()}
    except Exception:
        return {}


def _semantic_seed_intent_hints() -> dict[str, list[str]]:
    return {}


def _semantic_effective_alias_groups() -> dict[str, list[str]]:
    return semantic_lexicon_service.effective_alias_groups(_semantic_seed_alias_groups())


def _semantic_effective_alias_group_entries() -> dict[str, dict[str, Any]]:
    return semantic_lexicon_service.effective_alias_group_entries(_semantic_seed_alias_groups())


def _semantic_alias_group_meta() -> dict[str, Any]:
    return semantic_lexicon_service.effective_alias_group_meta(_semantic_seed_alias_groups())


def _semantic_effective_intent_hints() -> dict[str, list[str]]:
    return semantic_lexicon_service.effective_intent_hints(_semantic_seed_intent_hints())


def _require_semantic_writer(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user.get("role") == "admin":
        return user
    if any(is_page_manager(user, page_id) for page_id in ("agent", "diagnosis", "knowledge")):
        return user
    raise HTTPException(status_code=403, detail="Admin or Agent/Diagnosis/Knowledge page manager only")


def _find_semantic_proposal(proposal_id: str) -> dict[str, Any] | None:
    wanted = str(proposal_id or "").strip()
    if not wanted:
        return None
    for row in semantic_inbox.list_proposals(status=None, limit=10000):
        if str(row.get("id") or "") == wanted:
            return row
    return None


def _append_alias_term(canonical: str, term: str, *, by: str) -> dict[str, list[str]]:
    canonical = str(canonical or "").strip()
    term = str(term or "").strip()
    if not canonical or not term:
        raise HTTPException(status_code=400, detail="canonical and term are required")
    effective = _semantic_effective_alias_groups()
    aliases = _string_list([*(effective.get(canonical) or []), term])
    return semantic_lexicon_service.upsert_alias_group(
        canonical,
        aliases,
        by=by,
        seed=_semantic_seed_alias_groups(),
    )


def _alias_req_meta(req: SemanticAliasGroupReq) -> dict[str, Any]:
    meta = dict(req.meta or {})
    if req.semantic_class:
        meta["semantic_class"] = str(req.semantic_class or "").strip()
    if req.normalization is not None:
        meta["normalization"] = req.normalization
    if req.value_domain is not None:
        meta["value_domain"] = req.value_domain
    return {
        key: value
        for key, value in meta.items()
        if key in {"semantic_class", "normalization", "value_domain"}
    }


def _canonical_from_term(term: str) -> str:
    text = str(term or "").strip()
    if not text:
        return ""
    compact = "_".join(part for part in text.lower().replace("-", "_").split() if part)
    return compact[:80] or text[:80]


def _draft_split_list(value: Any, limit: int = 30) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        raw = value
    else:
        raw = re.split(r"[,|]+", str(value or ""))
    return _string_list(raw, limit=limit)


def _draft_number(value: Any) -> int | float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    number = float(match.group(0))
    return int(number) if number.is_integer() else number


def _parse_semantic_kv_text(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z가-힣_][A-Za-z0-9가-힣_ -]{0,40})\s*[:=]\s*([^;\n]+)", str(text or "")):
        key = re.sub(r"[\s-]+", "_", str(match.group(1) or "").strip().casefold())
        value = str(match.group(2) or "").strip()
        if key and value:
            fields[key] = value
    return fields


def _field_value(fields: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = fields.get(key)
        if value:
            return value
    return ""


def _path_tokens(text: str) -> list[str]:
    tokens = re.findall(
        r"(?:FLOW_(?:DB|DATA)_ROOT/[^\s;,]+|docs/semantic/[^\s;,]+|[A-Za-z]:\\[^\s;,]+|[^\s;,]+\.(?:parquet|csv|json|ya?ml|md))",
        str(text or ""),
        flags=re.IGNORECASE,
    )
    return _string_list(tokens, limit=20)


def _semantic_source_draft_from_text(text: str) -> dict[str, dict[str, Any]]:
    raw = str(text or "").strip()
    fields = _parse_semantic_kv_text(raw)
    has_source_signal = any(
        key in fields
        for key in (
            "source_id",
            "source",
            "title",
            "role",
            "path",
            "path_patterns",
            "docs_path",
            "columns",
            "search_terms",
        )
    )
    has_source_signal = has_source_signal or bool(re.search(r"\bsource\s+catalog\b|source\s+id\s*=|docs?\s+path\s*=", raw, re.IGNORECASE))
    if not has_source_signal:
        return {}

    source_id = _field_value(fields, "source_id", "source", "id")
    title = _field_value(fields, "title", "name", "이름", "제목")
    role = _field_value(fields, "role", "역할")
    explicit_paths = _draft_split_list(_field_value(fields, "path_patterns", "paths", "path", "경로"), limit=40)
    paths = explicit_paths or [token for token in _path_tokens(raw) if not token.casefold().endswith(".md")]
    docs_path = _field_value(fields, "docs_path", "doc_path", "docs", "문서")
    if not docs_path:
        docs_candidates = [token for token in _path_tokens(raw) if token.casefold().endswith(".md")]
        docs_path = docs_candidates[0] if docs_candidates else ""
    source = semantic_source_catalog.normalize_source(
        {
            "id": source_id or title or role,
            "title": title or source_id or "Semantic source",
            "role": role or "source_search",
            "roles": _draft_split_list(_field_value(fields, "roles"), limit=30) or ["source_search"],
            "path_patterns": paths,
            "fallback_path_patterns": _draft_split_list(_field_value(fields, "fallback_path_patterns", "fallback_paths"), limit=40),
            "owner": _field_value(fields, "owner", "소유자"),
            "write_policy": _field_value(fields, "write_policy", "write", "policy", "정책") or "Agent read-only. Update source data through owner feature APIs.",
            "docs_path": docs_path,
            "related_question_ids": _draft_split_list(_field_value(fields, "related_question_ids", "questions"), limit=30),
            "related_unit_keys": _draft_split_list(_field_value(fields, "related_unit_keys", "units"), limit=30),
            "columns": _draft_split_list(_field_value(fields, "columns", "컬럼"), limit=80),
            "search_terms": _draft_split_list(_field_value(fields, "search_terms", "terms", "검색어"), limit=80),
        },
        actor="draft",
    )
    return {str(source.get("id") or ""): source} if source.get("id") else {}


def _semantic_measurement_draft_from_text(text: str) -> dict[str, dict[str, Any]]:
    raw = str(text or "").strip()
    fields = _parse_semantic_kv_text(raw)
    source_type = _field_value(fields, "source_type", "source", "measurement_source")
    if not source_type:
        match = re.search(r"\b(INLINE|ET|VM|EDS)\b", raw, re.IGNORECASE)
        source_type = match.group(1) if match else ""
    term = _field_value(fields, "measurement_term", "term", "measurement", "측정명", "측정", "항목")
    item_id = _field_value(fields, "item_id", "item", "rawitem_id")
    if not (term or item_id) or not source_type:
        return {}

    evidence = _field_value(fields, "evidence", "근거", "source_ref")
    term_row = semantic_measure_catalog.normalize_term(
        {
            "id": _field_value(fields, "measurement_id", "term_id", "id"),
            "term": term or item_id,
            "aliases": _draft_split_list(_field_value(fields, "aliases", "alias", "별칭"), limit=30),
            "source_type": source_type,
            "product": _field_value(fields, "product", "제품"),
            "step_id": _field_value(fields, "step_id", "step"),
            "item_id": item_id,
            "value_column": _field_value(fields, "value_column", "value"),
            "default_agg": _field_value(fields, "default_agg", "agg") or ("avg" if source_type.upper() == "INLINE" else "median"),
            "target": _draft_number(_field_value(fields, "target", "목표")),
            "spec_low": _draft_number(_field_value(fields, "spec_low", "lsl")),
            "spec_high": _draft_number(_field_value(fields, "spec_high", "usl")),
            "evidence": [{"type": "manual", "label": evidence}] if evidence else [],
        },
        actor="draft",
    )
    return {str(term_row.get("id") or ""): term_row} if term_row.get("id") else {}


def _parse_semantic_json_draft(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except Exception:
        return {"alias_groups": {}, "intent_hints": {}, "source_catalog": {}, "measurement_terms": {}}
    if not isinstance(parsed, dict):
        return {"alias_groups": {}, "intent_hints": {}, "source_catalog": {}, "measurement_terms": {}}
    raw_alias = parsed.get("alias_groups", parsed.get("groups", {}))
    raw_intents = parsed.get("intent_hints", parsed.get("intents", {}))
    raw_sources = parsed.get("source_catalog", parsed.get("sources", {}))
    raw_measurements = parsed.get("measurement_terms", parsed.get("measurements", {}))
    alias_groups = {
        str(key).strip(): _string_list(value)
        for key, value in (raw_alias.items() if isinstance(raw_alias, dict) else [])
        if str(key).strip()
    }
    intent_hints = {
        str(key).strip(): _string_list(value)
        for key, value in (raw_intents.items() if isinstance(raw_intents, dict) else [])
        if str(key).strip()
    }
    source_catalog = {
        str(source.get("id") or key): source
        for key, value in (raw_sources.items() if isinstance(raw_sources, dict) else [])
        if isinstance(value, dict)
        for source in [semantic_source_catalog.normalize_source({**value, "id": value.get("id") or key}, actor="draft")]
        if source.get("id")
    }
    measurement_terms = {
        str(term.get("id") or key): term
        for key, value in (raw_measurements.items() if isinstance(raw_measurements, dict) else [])
        if isinstance(value, dict)
        for term in [semantic_measure_catalog.normalize_term({**value, "id": value.get("id") or key}, actor="draft")]
        if term.get("id")
    }
    return {
        "alias_groups": alias_groups,
        "intent_hints": intent_hints,
        "source_catalog": source_catalog,
        "measurement_terms": measurement_terms,
    }


def _semantic_draft_from_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    parsed = _parse_semantic_json_draft(raw)
    if parsed["alias_groups"] or parsed["intent_hints"] or parsed["source_catalog"] or parsed["measurement_terms"]:
        return {
            "alias_groups": parsed["alias_groups"],
            "intent_hints": parsed["intent_hints"],
            "source_catalog": parsed["source_catalog"],
            "measurement_terms": parsed["measurement_terms"],
            "terms": [],
            "classifications": [],
            "source": "json",
        }

    effective_aliases = _semantic_effective_alias_groups()
    terms = semantic_extractor.extract_terms(raw)
    alias_groups: dict[str, list[str]] = {}
    classifications: list[dict[str, Any]] = []
    for term in terms:
        classified = semantic_proposer.classify_proposal(term, alias_groups=effective_aliases)
        classifications.append(classified)
        category = str(classified.get("category") or "")
        if category == "mapping":
            canonical = str(classified.get("canonical_match") or "").strip()
            if canonical:
                alias_groups[canonical] = _string_list([*(effective_aliases.get(canonical) or []), term])
        elif category == "new_canonical":
            canonical = _canonical_from_term(term)
            if canonical:
                alias_groups[canonical] = _string_list([term])
    intent_hints: dict[str, list[str]] = {}
    for match in re.finditer(r"(?:intent|의도)\s*[:=]\s*([A-Za-z0-9가-힣_-]+)\s*(?:->|:|=)\s*([^;\n]+)", raw, re.IGNORECASE):
        intent = str(match.group(1) or "").strip()
        values = _string_list(re.split(r"[,/\s]+", str(match.group(2) or "")))
        if intent and values:
            intent_hints[intent] = values
    return {
        "alias_groups": alias_groups,
        "intent_hints": intent_hints,
        "source_catalog": _semantic_source_draft_from_text(raw),
        "measurement_terms": _semantic_measurement_draft_from_text(raw),
        "terms": terms,
        "classifications": classifications,
        "source": "text",
    }


def _unit_catalog_item(unit) -> dict[str, Any]:
    key = unit.key()
    return {
        "key": key,
        "title": unit.title(),
        "description": unit.description(),
        "llm_profile": unit.llm_profile(),
        "feature_md_path": str(unit.feature_md_path()),
        "prompt_template_path": str(unit.prompt_template_path() or ""),
        "input_schema": unit.input_schema(),
        "output_schema": unit.output_schema(),
        "examples": unit.examples(),
        "runtime_endpoints": _unit_v2_endpoints(key),
        "handler_entry": {
            "module": unit.handler_entry().module,
            "function": unit.handler_entry().function,
            "description": unit.handler_entry().description,
        },
        "data_sources": [
            {
                "kind": source.kind,
                "path": source.path,
                "description": source.description,
            }
            for source in unit.data_sources()
        ],
    }


@router.get("/status")
def agent_reset_status() -> dict[str, Any]:
    version_meta = _version_metadata(_APP_ROOT)
    return {
        "ok": True,
        "status": "active_unit_ai",
        "legacy_agent_studio": {
            "status": "archived_for_rebuild",
            "detail": "Legacy Agent Studio endpoints remain archived; Unit AI runtime routes are active.",
        },
        "settings_endpoint": "/api/llm/status",
        "unit_ai_endpoint": "/api/agent/unit-ai/catalog",
        "unit_endpoint": "/api/agent/catalog",
        "active_unit_endpoints": _ACTIVE_UNIT_ENDPOINTS,
        "active_unit_endpoints_v2": {key: _unit_v2_endpoints(key) for key in _ACTIVE_UNIT_ENDPOINTS},
        "home_flowi_runtime_endpoints": {
            "graph": "/api/agent/home-flowi/runtime/graph",
            "runs": "/api/agent/home-flowi/runtime/runs",
        },
        **version_meta,
        "backend_commit": _backend_commit(_APP_ROOT),
        "backend_agent_router": str(Path(__file__).resolve()),
    }


@router.get("/unit-ai/catalog")
def unit_ai_catalog(request: Request) -> dict[str, Any]:
    current_user(request)
    units = [_unit_catalog_item(unit) for unit in all_unit_ais()]
    return {
        "ok": True,
        "units": units,
    }


@router.get("/catalog")
def agent_unit_catalog(request: Request) -> dict[str, Any]:
    return unit_ai_catalog(request)


@router.get("/unit-ai/filebrowser_ai_sql/runtime/graph")
def filebrowser_ai_sql_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(FILEBROWSER_AI_SQL_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="filebrowser_ai_sql unit is not registered")
    return {
        "ok": True,
        "unit_ai": FILEBROWSER_AI_SQL_UNIT_KEY,
        "graph": agent_feedback_penalties.annotate_graph(FILEBROWSER_AI_SQL_UNIT_KEY, filebrowser_ai_sql_graph()),
    }


@router.post("/unit-ai/filebrowser_ai_sql/runtime/run")
def filebrowser_ai_sql_runtime_run(req: FileBrowserAiSqlRuntimeRunReq, request: Request) -> dict[str, Any]:
    from routers import filebrowser as filebrowser_router

    me = filebrowser_router._require_filebrowser_user(request)
    unit = get_unit_ai(FILEBROWSER_AI_SQL_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="filebrowser_ai_sql unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    result = run_filebrowser_ai_sql_runtime(payload, username=(me or {}).get("username") or "")
    try:
        filebrowser_router._record_filebrowser_ai_sql_history(
            (me or {}).get("username") or "",
            source="agent_test_prompt",
            request_payload=payload,
            result_payload=result,
        )
    except Exception:
        pass
    return agent_feedback_penalties.annotate_result(FILEBROWSER_AI_SQL_UNIT_KEY, result)


@router.get("/unit-ai/inform_registration/runtime/graph")
def inform_registration_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(INFORM_REGISTRATION_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="inform_registration unit is not registered")
    return {
        "ok": True,
        "unit_ai": INFORM_REGISTRATION_UNIT_KEY,
        "graph": agent_feedback_penalties.annotate_graph(INFORM_REGISTRATION_UNIT_KEY, inform_registration_graph()),
    }


@router.post("/unit-ai/inform_registration/runtime/run")
def inform_registration_runtime_run(req: UnitAiRuntimeRunReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(INFORM_REGISTRATION_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="inform_registration unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return agent_feedback_penalties.annotate_result(INFORM_REGISTRATION_UNIT_KEY, run_inform_registration_runtime(
        payload,
        username=(me or {}).get("username") or "",
        request=request,
    ))


@router.get("/unit-ai/inform_registration/runtime/history")
def inform_registration_runtime_history(request: Request, limit: int = 50) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(INFORM_REGISTRATION_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="inform_registration unit is not registered")
    return {
        "ok": True,
        "unit_ai": INFORM_REGISTRATION_UNIT_KEY,
        "history": list_inform_registration_history(limit=limit, username=(me or {}).get("username") or ""),
    }


@router.get("/unit-ai/change_management/runtime/graph")
def change_management_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(CHANGE_MANAGEMENT_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="change_management unit is not registered")
    return {
        "ok": True,
        "unit_ai": CHANGE_MANAGEMENT_UNIT_KEY,
        "graph": agent_feedback_penalties.annotate_graph(CHANGE_MANAGEMENT_UNIT_KEY, change_management_graph()),
    }


@router.post("/unit-ai/change_management/runtime/run")
def change_management_runtime_run(req: UnitAiRuntimeRunReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(CHANGE_MANAGEMENT_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="change_management unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return agent_feedback_penalties.annotate_result(CHANGE_MANAGEMENT_UNIT_KEY, run_change_management_runtime(
        payload,
        username=(me or {}).get("username") or "",
        request=request,
    ))


@router.get("/unit-ai/change_management/runtime/history")
def change_management_runtime_history(request: Request, limit: int = 50) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(CHANGE_MANAGEMENT_UNIT_KEY)
    if unit is None:
        raise HTTPException(status_code=404, detail="change_management unit is not registered")
    return {
        "ok": True,
        "unit_ai": CHANGE_MANAGEMENT_UNIT_KEY,
        "history": list_change_management_history(limit=limit, username=(me or {}).get("username") or ""),
    }


@router.get("/semantic/lexicon")
def semantic_lexicon(request: Request, limit: int = 100) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "alias_groups": {
            "effective": _semantic_effective_alias_groups(),
            "disk": semantic_lexicon_store.load_alias_groups(),
        },
        "alias_group_entries": {
            "effective": _semantic_effective_alias_group_entries(),
            "disk": semantic_lexicon_store.load_alias_group_entries(),
        },
        "alias_group_meta": {
            "effective": _semantic_alias_group_meta(),
            "disk": {
                key: {meta_key: value for meta_key, value in (entry or {}).items() if meta_key != "aliases"}
                for key, entry in semantic_lexicon_store.load_alias_group_entries().items()
            },
        },
        "intent_hints": {
            "effective": _semantic_effective_intent_hints(),
            "disk": semantic_lexicon_store.load_intent_hints(),
        },
        "changes": semantic_lexicon_store.list_changes(limit=max(1, min(int(limit or 100), 500))),
        "proposals": semantic_inbox.list_proposals(status="pending", limit=100),
    }


@router.get("/semantic/sources")
def semantic_sources(request: Request) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "sources": semantic_source_catalog.catalog_sources(),
        "disk": semantic_source_catalog.disk_sources(),
        "deleted_ids": semantic_source_catalog.deleted_source_ids(),
        "roles": semantic_source_catalog.catalog_roles(),
        "docs_base": semantic_source_catalog.DOCS_BASE,
        "path": str(semantic_source_catalog.SOURCE_FILE),
        "change_log_path": str(semantic_source_catalog.CHANGES_FILE),
    }


@router.put("/semantic/sources/{source_id}")
def semantic_source_upsert(source_id: str, req: SemanticSourceCatalogReq, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    source = dict(req.source or {})
    source["id"] = source_id
    saved = semantic_source_catalog.save_source(source, actor=str(user.get("username") or ""))
    return {
        "ok": True,
        "source": saved,
        "sources": semantic_source_catalog.catalog_sources(),
        "disk": semantic_source_catalog.disk_sources(),
        "deleted_ids": semantic_source_catalog.deleted_source_ids(),
        "roles": semantic_source_catalog.catalog_roles(),
    }


@router.delete("/semantic/sources/{source_id}")
def semantic_source_delete(source_id: str, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    deleted = semantic_source_catalog.delete_source(source_id, actor=str(user.get("username") or ""))
    return {
        "ok": True,
        "deleted": deleted,
        "sources": semantic_source_catalog.catalog_sources(),
        "disk": semantic_source_catalog.disk_sources(),
        "deleted_ids": semantic_source_catalog.deleted_source_ids(),
        "roles": semantic_source_catalog.catalog_roles(),
    }


@router.get("/semantic/measurements")
def semantic_measurements(request: Request) -> dict[str, Any]:
    current_user(request)
    catalog = semantic_measure_catalog.load_catalog(ensure=True)
    return {
        "ok": True,
        "catalog": catalog,
        "terms": catalog.get("terms") or [],
        "path": catalog.get("path") or "",
        "change_log_path": catalog.get("change_log_path") or "",
    }


@router.put("/semantic/measurements/{term_id}")
def semantic_measurement_upsert(term_id: str, req: SemanticMeasurementTermReq, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    term = dict(req.term or {})
    term["id"] = term_id
    saved = semantic_measure_catalog.save_term(term, actor=str(user.get("username") or ""))
    catalog = semantic_measure_catalog.load_catalog(ensure=True)
    return {"ok": True, "term": saved, "terms": catalog.get("terms") or []}


@router.delete("/semantic/measurements/{term_id}")
def semantic_measurement_delete(term_id: str, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    deleted = semantic_measure_catalog.delete_term(term_id, actor=str(user.get("username") or ""))
    catalog = semantic_measure_catalog.load_catalog(ensure=True)
    return {"ok": True, "deleted": deleted, "terms": catalog.get("terms") or []}


@router.post("/semantic/measurements/merge-defaults")
def semantic_measurements_merge_defaults(request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    catalog = semantic_measure_catalog.ensure_catalog(actor=str(user.get("username") or ""))
    return {"ok": True, **catalog}


@router.put("/semantic/alias-groups/{canonical}")
def semantic_alias_group_upsert(canonical: str, req: SemanticAliasGroupReq, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    try:
        semantic_lexicon_service.upsert_alias_group(
            canonical,
            req.aliases,
            by=str(user.get("username") or ""),
            seed=_semantic_seed_alias_groups(),
            meta=_alias_req_meta(req),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "alias_groups": {
            "effective": _semantic_effective_alias_groups(),
            "disk": semantic_lexicon_store.load_alias_groups(),
        },
        "alias_group_entries": {
            "effective": _semantic_effective_alias_group_entries(),
            "disk": semantic_lexicon_store.load_alias_group_entries(),
        },
    }


@router.delete("/semantic/alias-groups/{canonical}")
def semantic_alias_group_delete(canonical: str, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    deleted = semantic_lexicon_service.delete_alias_group(canonical, by=str(user.get("username") or ""))
    return {
        "ok": True,
        "deleted": deleted,
        "alias_groups": {
            "effective": _semantic_effective_alias_groups(),
            "disk": semantic_lexicon_store.load_alias_groups(),
        },
        "alias_group_entries": {
            "effective": _semantic_effective_alias_group_entries(),
            "disk": semantic_lexicon_store.load_alias_group_entries(),
        },
    }


@router.put("/semantic/intent-hints/{intent}")
def semantic_intent_hint_upsert(intent: str, req: SemanticIntentHintReq, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    try:
        semantic_lexicon_service.upsert_intent_hint(
            intent,
            req.required_canonicals,
            by=str(user.get("username") or ""),
            seed=_semantic_seed_intent_hints(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "intent_hints": {
            "effective": _semantic_effective_intent_hints(),
            "disk": semantic_lexicon_store.load_intent_hints(),
        },
    }


@router.delete("/semantic/intent-hints/{intent}")
def semantic_intent_hint_delete(intent: str, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    deleted = semantic_lexicon_service.delete_intent_hint(intent, by=str(user.get("username") or ""))
    return {
        "ok": True,
        "deleted": deleted,
        "intent_hints": {
            "effective": _semantic_effective_intent_hints(),
            "disk": semantic_lexicon_store.load_intent_hints(),
        },
    }


@router.get("/semantic/proposals")
def semantic_proposals(request: Request, status: str = "pending", limit: int = 200) -> dict[str, Any]:
    current_user(request)
    status_filter = (status or "").strip().lower() or None
    if status_filter == "all":
        status_filter = None
    return {
        "ok": True,
        "proposals": semantic_inbox.list_proposals(
            status=status_filter,
            limit=max(1, min(int(limit or 200), 1000)),
        ),
    }


@router.post("/semantic/proposals/{proposal_id}/decision")
def semantic_proposal_decision(proposal_id: str, req: SemanticProposalDecisionReq, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    decision = str(req.decision or "").strip().lower()
    if decision not in {"approve", "approved", "reject", "rejected"}:
        raise HTTPException(status_code=400, detail="decision must be approve or reject")
    proposal = _find_semantic_proposal(proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="semantic proposal not found")
    by = str(user.get("username") or "")
    if decision in {"approve", "approved"} and proposal.get("status") == "pending":
        category = str(proposal.get("category") or "")
        canonical = str(req.canonical or proposal.get("canonical_match") or "").strip()
        if category == "new_canonical" and not canonical:
            canonical = _canonical_from_term(str(proposal.get("term") or ""))
        if category in {"mapping", "new_canonical", "conflict"}:
            _append_alias_term(canonical, str(proposal.get("term") or ""), by=by)
        else:
            raise HTTPException(status_code=400, detail=f"proposal category cannot be approved: {category}")
        status_value = "approved"
    else:
        status_value = "rejected"
    decided = semantic_inbox.update_proposal_status(proposal_id, status=status_value, by=by)
    if not decided:
        raise HTTPException(status_code=404, detail="semantic proposal not found")
    return {
        "ok": True,
        "proposal": decided,
        "alias_groups": {
            "effective": _semantic_effective_alias_groups(),
            "disk": semantic_lexicon_store.load_alias_groups(),
        },
        "alias_group_entries": {
            "effective": _semantic_effective_alias_group_entries(),
            "disk": semantic_lexicon_store.load_alias_group_entries(),
        },
    }


@router.post("/semantic/draft")
def semantic_draft(req: SemanticDraftReq, request: Request) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "draft": _semantic_draft_from_text(req.text),
    }


@router.get("/unit-ai/{unit_key}/runtime/graph")
def unit_ai_runtime_graph(unit_key: str, request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    if unit_key == FILEBROWSER_AI_SQL_UNIT_KEY:
        graph = filebrowser_ai_sql_graph()
    elif unit_key == INFORM_REGISTRATION_UNIT_KEY:
        graph = inform_registration_graph()
    elif unit_key == CHANGE_MANAGEMENT_UNIT_KEY:
        graph = change_management_graph()
    elif unit_key == DASHBOARD_AGENT_UNIT_KEY:
        graph = dashboard_agent_graph()
    elif unit_key in DETERMINISTIC_LOOKUP_UNIT_KEYS:
        graph = deterministic_lookup_graph(unit_key)
    else:
        raise HTTPException(status_code=404, detail=f"{unit_key} runtime is not available")
    return {
        "ok": True,
        "unit_ai": unit_key,
        "graph": agent_feedback_penalties.annotate_graph(unit_key, graph),
    }


@router.post("/unit-ai/{unit_key}/runtime/run")
def unit_ai_runtime_run(unit_key: str, req: UnitAiRuntimeRunReq, request: Request) -> dict[str, Any]:
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    if unit_key == FILEBROWSER_AI_SQL_UNIT_KEY:
        from routers import filebrowser as filebrowser_router

        me = filebrowser_router._require_filebrowser_user(request)
        if not payload.get("natural_language") and payload.get("prompt"):
            payload["natural_language"] = payload.get("prompt")
        result = run_filebrowser_ai_sql_runtime(payload, username=(me or {}).get("username") or "")
        try:
            filebrowser_router._record_filebrowser_ai_sql_history(
                (me or {}).get("username") or "",
                source="agent_test_prompt",
                request_payload=payload,
                result_payload=result,
            )
        except Exception:
            pass
        return agent_feedback_penalties.annotate_result(FILEBROWSER_AI_SQL_UNIT_KEY, result)
    if unit_key == INFORM_REGISTRATION_UNIT_KEY:
        me = current_user(request)
        return agent_feedback_penalties.annotate_result(INFORM_REGISTRATION_UNIT_KEY, run_inform_registration_runtime(
            payload,
            username=(me or {}).get("username") or "",
            request=request,
        ))
    if unit_key == CHANGE_MANAGEMENT_UNIT_KEY:
        me = current_user(request)
        return agent_feedback_penalties.annotate_result(CHANGE_MANAGEMENT_UNIT_KEY, run_change_management_runtime(
            payload,
            username=(me or {}).get("username") or "",
            request=request,
        ))
    if unit_key == DASHBOARD_AGENT_UNIT_KEY:
        me = current_user(request)
        if not payload.get("natural_language") and payload.get("prompt"):
            payload["natural_language"] = payload.get("prompt")
        if home_orchestrator.dashboard_agent_should_use_source_runtime(payload, home_context=False):
            from core.flowi_units.home_sql_join_dashboard_runtime import run_home_sql_join_dashboard_runtime

            result = run_home_sql_join_dashboard_runtime(
                home_orchestrator.dashboard_agent_source_payload(payload),
                username=(me or {}).get("username") or "",
            )
            return agent_feedback_penalties.annotate_result(
                DASHBOARD_AGENT_UNIT_KEY,
                home_orchestrator.dashboard_agent_result_from_source_runtime_result(result),
            )
        result = run_dashboard_agent_runtime(
            payload,
            username=(me or {}).get("username") or "",
        )
        try:
            record_dashboard_agent_history(
                (me or {}).get("username") or "",
                source="agent_test_prompt",
                request_payload=payload,
                result_payload=result,
            )
        except Exception:
            pass
        return agent_feedback_penalties.annotate_result(DASHBOARD_AGENT_UNIT_KEY, result)
    if unit_key in DETERMINISTIC_LOOKUP_UNIT_KEYS:
        me = current_user(request)
        return run_deterministic_lookup_runtime(
            unit_key,
            payload,
            username=(me or {}).get("username") or "",
        )
    raise HTTPException(status_code=404, detail=f"{unit_key} runtime is not available")


@router.get("/unit-ai/{unit_key}/feedback-profile")
def unit_ai_feedback_profile(unit_key: str, request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    return {
        "ok": True,
        "unit_ai": unit_key,
        "profile": agent_feedback_penalties.feedback_profile(unit_key),
    }


@router.post("/unit-ai/{unit_key}/feedback")
def unit_ai_feedback(unit_key: str, req: UnitAiFeedbackReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    rating = str(req.rating or "").strip().lower()
    if rating not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="rating must be up or down")
    try:
        profile = agent_feedback_penalties.record_feedback(
            unit_key,
            rating,
            node_id=req.node_id,
            run_id=req.run_id,
            reason=req.reason,
            actor=str((me or {}).get("username") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "unit_ai": unit_key,
        "profile": profile,
    }


@router.get("/unit-ai/{unit_key}/runtime/overrides")
def unit_ai_runtime_overrides(unit_key: str, request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    return {
        "ok": True,
        "unit_ai": unit_key,
        "overrides": agent_prompt_overrides.load_unit(unit_key),
    }


@router.put("/unit-ai/{unit_key}/runtime/overrides")
def unit_ai_runtime_overrides_save(unit_key: str, req: UnitAiOverrideReq, request: Request) -> dict[str, Any]:
    user = _require_semantic_writer(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    try:
        saved = agent_prompt_overrides.save_unit(
            unit_key,
            req.model_dump() if hasattr(req, "model_dump") else req.dict(),
            by=str(user.get("username") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "unit_ai": unit_key,
        "overrides": saved,
    }


@router.get("/unit-ai/{unit_key}/runtime/history")
def unit_ai_runtime_history(unit_key: str, request: Request, limit: int = 50) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(unit_key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{unit_key} unit is not registered")
    if unit_key == INFORM_REGISTRATION_UNIT_KEY:
        return {
            "ok": True,
            "unit_ai": unit_key,
            "history": list_inform_registration_history(limit=limit, username=(me or {}).get("username") or ""),
        }
    if unit_key == CHANGE_MANAGEMENT_UNIT_KEY:
        return {
            "ok": True,
            "unit_ai": unit_key,
            "history": list_change_management_history(limit=limit, username=(me or {}).get("username") or ""),
        }
    if unit_key == FILEBROWSER_AI_SQL_UNIT_KEY:
        from routers import filebrowser as filebrowser_router

        payload = filebrowser_router.filebrowser_sql_history(request, limit=max(1, min(int(limit or 50), 200)))
        return {
            "ok": True,
            "unit_ai": unit_key,
            "history": payload.get("history") or [],
            "limit": payload.get("limit") or limit,
        }
    if unit_key == DASHBOARD_AGENT_UNIT_KEY:
        return {
            "ok": True,
            "unit_ai": unit_key,
            "history": list_dashboard_agent_history(limit=limit, username=(me or {}).get("username") or ""),
        }
    if unit_key in DETERMINISTIC_LOOKUP_UNIT_KEYS:
        return {
            "ok": True,
            "unit_ai": unit_key,
            "history": list_deterministic_lookup_history(unit_key, limit=limit, username=(me or {}).get("username") or ""),
        }
    raise HTTPException(status_code=404, detail=f"{unit_key} history is not available")


@router.get("/unit/{unit_key}/graph")
def unit_runtime_graph(unit_key: str, request: Request) -> dict[str, Any]:
    return unit_ai_runtime_graph(unit_key, request)


@router.post("/unit/{unit_key}/run")
def unit_runtime_run(unit_key: str, req: UnitAiRuntimeRunReq, request: Request) -> dict[str, Any]:
    return unit_ai_runtime_run(unit_key, req, request)


@router.get("/unit/{unit_key}/history")
def unit_runtime_history(unit_key: str, request: Request, limit: int = 50) -> dict[str, Any]:
    return unit_ai_runtime_history(unit_key, request, limit=limit)


@router.get("/home-flowi/runtime/graph")
def home_flowi_runtime_graph(request: Request) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "graph": home_orchestrator.build_home_runtime_graph(),
    }


@router.get("/home-flowi/tools")
def home_flowi_tools(request: Request) -> dict[str, Any]:
    """홈 Flow-i ReAct 오케스트레이터가 고를 수 있는 연결 기능(도구) 카탈로그.

    unit_ai + function-call 통합 목록 (tool_registry 읽기 전용)."""
    current_user(request)
    try:
        from core import tool_registry
        tools = tool_registry.list_tools(include_stats=False)
    except Exception:
        tools = []
    return {"ok": True, "tools": tools}


@router.get("/home-flowi/runtime/runs")
def home_flowi_runtime_runs(request: Request, limit: int = 20) -> dict[str, Any]:
    current_user(request)
    return {
        "ok": True,
        "runs": home_orchestrator.list_home_runtime_runs(limit=limit),
    }


@router.get("/home-flowi/runtime/runs/{run_id}")
def home_flowi_runtime_run(run_id: str, request: Request) -> dict[str, Any]:
    current_user(request)
    run = home_orchestrator.load_home_runtime_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="home Flow-i runtime run not found")
    return {
        "ok": True,
        "run": run,
    }


def _query_limit(request: Request, default: int) -> int:
    raw = request.query_params.get("limit")
    try:
        limit = int(raw or default)
    except (TypeError, ValueError):
        return default
    return max(1, min(200, limit))


def _request_json_payload(request: Request) -> dict[str, Any]:
    for attr in ("_json", "json_body", "body_json"):
        value = getattr(request, attr, None)
        if isinstance(value, dict):
            return value
    json_fn = getattr(request, "json", None)
    if not callable(json_fn):
        return {}
    try:
        import anyio

        value = anyio.from_thread.run(json_fn)
    except Exception:
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                return {}
            value = loop.run_until_complete(json_fn())
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


def _active_agent_get_fallback(path: str, request: Request) -> dict[str, Any] | None:
    """Serve active Agent GET endpoints even if the archived catch-all is hit."""
    if request.method != "GET":
        return None
    normalized = path.strip("/")
    if normalized == "unit-ai/catalog":
        return unit_ai_catalog(request)
    if normalized == "catalog":
        return agent_unit_catalog(request)
    if normalized == "semantic/sources":
        return semantic_sources(request)
    if normalized == "semantic/measurements":
        return semantic_measurements(request)
    if normalized == "semantic/lexicon":
        return semantic_lexicon(request, limit=_query_limit(request, 100))
    if normalized == "home-flowi/runtime/graph":
        return home_flowi_runtime_graph(request)
    if normalized == "home-flowi/runtime/runs":
        return home_flowi_runtime_runs(request, limit=_query_limit(request, 20))
    if normalized.startswith("home-flowi/runtime/runs/"):
        run_id = unquote(normalized.removeprefix("home-flowi/runtime/runs/"))
        if run_id:
            return home_flowi_runtime_run(run_id, request)

    parts = normalized.split("/")
    if len(parts) == 4 and parts[0] == "unit-ai" and parts[2] == "runtime":
        unit_key = unquote(parts[1])
        if parts[3] == "graph":
            return unit_ai_runtime_graph(unit_key, request)
        if parts[3] == "history":
            return unit_ai_runtime_history(unit_key, request, limit=_query_limit(request, 50))
        if parts[3] == "overrides":
            return unit_ai_runtime_overrides(unit_key, request)
    if len(parts) == 3 and parts[0] == "unit-ai" and parts[2] == "feedback-profile":
        return unit_ai_feedback_profile(unquote(parts[1]), request)
    if len(parts) == 3 and parts[0] == "unit":
        unit_key = unquote(parts[1])
        if parts[2] == "graph":
            return unit_runtime_graph(unit_key, request)
        if parts[2] == "history":
            return unit_runtime_history(unit_key, request, limit=_query_limit(request, 50))
    return None


def _active_agent_write_fallback(path: str, request: Request) -> dict[str, Any] | None:
    """Serve active Agent write endpoints even if route ordering reaches catch-all."""
    method = str(getattr(request, "method", "") or "").upper()
    if method not in {"POST", "PUT"}:
        return None
    normalized = path.strip("/")
    parts = normalized.split("/")
    body = _request_json_payload(request)
    if method == "POST" and len(parts) == 4 and parts[0] == "unit-ai" and parts[2] == "runtime" and parts[3] == "run":
        return unit_ai_runtime_run(unquote(parts[1]), UnitAiRuntimeRunReq(**body), request)
    if method == "POST" and len(parts) == 3 and parts[0] == "unit" and parts[2] == "run":
        return unit_runtime_run(unquote(parts[1]), UnitAiRuntimeRunReq(**body), request)
    if method == "POST" and len(parts) == 3 and parts[0] == "unit-ai" and parts[2] == "feedback":
        return unit_ai_feedback(unquote(parts[1]), UnitAiFeedbackReq(**body), request)
    if method == "PUT" and len(parts) == 4 and parts[0] == "unit-ai" and parts[2] == "runtime" and parts[3] == "overrides":
        return unit_ai_runtime_overrides_save(unquote(parts[1]), UnitAiOverrideReq(**body), request)
    return None


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def archived_agent_endpoint(path: str, request: Request) -> dict[str, Any] | None:
    active_payload = _active_agent_get_fallback(path, request)
    if active_payload is not None:
        return active_payload
    active_payload = _active_agent_write_fallback(path, request)
    if active_payload is not None:
        return active_payload
    raise HTTPException(
        status_code=410,
        detail=(
            "Legacy Agent Studio endpoint is archived for rebuild. "
            "The active Unit AI and Semantic layer routes remain available under "
            "/api/agent/unit-ai/*, /api/agent/unit/*, /api/agent/home-flowi/runtime/*, and /api/agent/semantic/*."
        ),
    )
