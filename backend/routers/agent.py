from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app_v2.modules.agent_runtime import (
    AgentRuntimeRequest,
    SemanticResolveRequest,
    build_action_plans,
    build_runtime_blueprint,
    encode_sse_event,
    resolve_semantic_frame,
    run_agent_runtime_once,
    stream_agent_runtime,
)
from app_v2.modules.agent_runtime.actions import compact_plan_rows, guardrail_summary_from_plans
from app_v2.modules.agent_runtime.semantic import _ALIAS_GROUPS as _SEMANTIC_ALIAS_SEED
from app_v2.modules.agent_runtime.semantic import _INTENT_HINTS as _SEMANTIC_INTENT_SEED
from app_v2.modules.semantic_lexicon import (
    delete_alias_group as _lex_delete_alias_group,
    delete_intent_hint as _lex_delete_intent_hint,
    effective_alias_groups as _lex_effective_alias_groups,
    effective_intent_hints as _lex_effective_intent_hints,
    list_changes as _lex_list_changes,
    load_alias_groups as _lex_load_alias_groups,
    load_intent_hints as _lex_load_intent_hints,
    upsert_alias_group as _lex_upsert_alias_group,
    upsert_intent_hint as _lex_upsert_intent_hint,
)
from app_v2.modules.semantic_learning import (
    list_proposals as _learn_list_proposals,
    submit_activity_log_batch as _learn_submit_activity_log_batch,
    update_proposal_status as _learn_update_proposal_status,
)
from app_v2.shared.contracts import FlowEntityKey, KnowledgeDoc
from core import audit
from core import knowledge_vault as kv
from core import llm_adapter
from core import semiconductor_knowledge as semi
from core import flowi_workflow_templates as wf_templates
from core.auth import current_user, is_page_admin, is_page_manager, require_admin
from core.flowi_units import UNIT_AIS, get_unit_ai
from core.paths import PATHS
from core.utils import load_json, save_json
from routers import llm as flowi_llm


router = APIRouter(prefix="/api/agent", tags=["agent"])

AGENT_BACKUP_DIR = PATHS.data_root / "agent_backups"
AGENT_ADMIN_STATE_FILE = PATHS.data_root / "agent_admin_tools.json"
AGENT_KNOWLEDGE_RAW_DIR = PATHS.data_root / "knowledge" / "raw"
SCHEMA_RELATION_FILE = PATHS.data_root / "schema_relations.json"


class PromptPreviewReq(BaseModel):
    prompt: str = ""
    product: str = ""
    max_rows: int = 20


class PromptReviewReq(BaseModel):
    prompt: str = ""
    product: str = ""
    max_rows: int = 12
    preview_row: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class PromoteReq(BaseModel):
    id: str
    kind: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str = ""
    promoted: bool = True


class MatchingSuggestReq(BaseModel):
    product: str = ""
    source_table: str = ""


class MatchingApplyReq(BaseModel):
    product: str = ""
    source_table: str = ""
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class RulebookSuggestReq(BaseModel):
    product: str = ""
    knob: str = ""
    mask: str = ""
    change_summary: str = ""


class RulebookApplyReq(RulebookSuggestReq):
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class KnowledgeIngestReq(BaseModel):
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    doc_type: str = "internal_knowledge"
    content: str = ""
    file_name: str = ""


class AgentWikiSourceReq(BaseModel):
    source_type: str = "markdown"
    source_id: str = ""
    title: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)


class AgentWikiIngestReq(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    source_type: str = "markdown"
    doc_id: str = ""
    title: str = ""
    summary: str = ""
    body: str = ""
    content: str = ""
    tags: list[str] = Field(default_factory=list)
    related_doc_ids: list[str] = Field(default_factory=list)
    relations: dict[str, str] = Field(default_factory=dict)


class AgentWikiPageSaveReq(BaseModel):
    doc_id: str = ""
    kind: str = "agent_wiki"
    title: str = ""
    summary: str = ""
    body: str = ""
    tags: list[str] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)


class AgentWikiPageDeleteReq(BaseModel):
    doc_id: str = ""


class LexiconUpsertReq(BaseModel):
    key: str = ""
    values: list[str] = Field(default_factory=list)


class LexiconDeleteReq(BaseModel):
    key: str = ""


class SchemaRelationSource(BaseModel):
    source_type: str = "file"  # file | db
    root: str = ""
    product: str = ""
    file: str = ""
    label: str = ""


class SchemaRelationPreviewReq(BaseModel):
    sources: list[SchemaRelationSource] = Field(default_factory=list)
    max_candidates: int = 30
    sample_rows: int = 20


class SchemaRelationScanReq(BaseModel):
    max_sources: int = 24
    max_candidates: int = 100
    sample_rows: int = 20


class SchemaRelationSaveReq(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class SchemaRelationDeleteReq(BaseModel):
    relation_ids: list[str] = Field(default_factory=list)
    note: str = ""


class SchemaDocAiDraftReq(BaseModel):
    body: str = ""
    hint_relation_id: str = ""
    hint_columns: list[str] = Field(default_factory=list)


class SchemaDocAiUpsertReq(SchemaDocAiDraftReq):
    wiki_doc: dict[str, Any] = Field(default_factory=dict)
    column_catalog_stubs: list[dict[str, Any]] = Field(default_factory=list)


class SchemaDocScanSourcesReq(BaseModel):
    max_sources: int = 24
    sample_rows: int = 20


class SchemaSingleFilePreviewReq(BaseModel):
    source: SchemaRelationSource = Field(default_factory=SchemaRelationSource)
    sample_rows: int = 20


class SchemaSingleFileRegisterReq(SchemaSingleFilePreviewReq):
    purpose: str = "lookup_table"
    key_columns: list[str] = Field(default_factory=list)
    output_columns: list[str] = Field(default_factory=list)
    column_roles: dict[str, str] = Field(default_factory=dict)
    doc_id: str = ""
    title: str = ""
    summary: str = ""


@router.get("/runtime/blueprint")
def agent_runtime_blueprint(request: Request) -> dict[str, Any]:
    current_user(request)
    return build_runtime_blueprint()


@router.post("/runtime/semantic/resolve")
def agent_runtime_semantic_resolve(req: SemanticResolveRequest, request: Request) -> dict[str, Any]:
    current_user(request)
    frame = resolve_semantic_frame(req.goal, max_terms=req.max_terms)
    return {"ok": True, "semantic": frame.model_dump(mode="json")}


def _lexicon_view(*, seed: dict[str, list[str]], disk: dict[str, list[str]]) -> dict[str, Any]:
    """Render a unified lexicon view for the UI: seed / disk override / effective."""
    effective: dict[str, list[str]] = {}
    for key, aliases in seed.items():
        effective[key] = list(aliases or [])
    for key, aliases in disk.items():
        effective[key] = list(aliases or [])
    rows: list[dict[str, Any]] = []
    for key in sorted(set(seed.keys()) | set(disk.keys())):
        seed_aliases = list(seed.get(key) or [])
        disk_aliases = disk.get(key)
        rows.append({
            "key": key,
            "seed": seed_aliases,
            "disk": list(disk_aliases or []) if disk_aliases is not None else None,
            "effective": list(effective.get(key) or []),
            "source": "disk" if key in disk else "seed",
        })
    return {"rows": rows, "seed_keys": sorted(seed.keys()), "disk_keys": sorted(disk.keys())}


@router.get("/semantic/alias-groups")
def agent_semantic_alias_groups(request: Request) -> dict[str, Any]:
    """List the alias-group lexicon (seed + disk override + effective merge)."""
    current_user(request)
    disk = _lex_load_alias_groups()
    return {"ok": True, **_lexicon_view(seed=dict(_SEMANTIC_ALIAS_SEED), disk=disk)}


@router.put("/semantic/alias-groups")
def agent_semantic_alias_groups_upsert(req: LexiconUpsertReq, request: Request) -> dict[str, Any]:
    me = _require_agent_wiki_admin(request)
    key = str(req.key or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    by = str(me.get("username") or "")
    _lex_upsert_alias_group(key, list(req.values or []), by=by, seed=dict(_SEMANTIC_ALIAS_SEED))
    audit.record(request, action=f"semantic:alias_group:upsert:{key}", detail=f"values={len(req.values or [])}", tab="ai_hub")
    disk = _lex_load_alias_groups()
    return {"ok": True, **_lexicon_view(seed=dict(_SEMANTIC_ALIAS_SEED), disk=disk)}


@router.post("/semantic/alias-groups/delete")
def agent_semantic_alias_groups_delete(req: LexiconDeleteReq, request: Request) -> dict[str, Any]:
    me = _require_agent_wiki_admin(request)
    key = str(req.key or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    removed = _lex_delete_alias_group(key, by=str(me.get("username") or ""))
    audit.record(request, action=f"semantic:alias_group:delete:{key}", detail=f"removed={bool(removed)}", tab="ai_hub")
    disk = _lex_load_alias_groups()
    return {"ok": True, "removed": bool(removed), **_lexicon_view(seed=dict(_SEMANTIC_ALIAS_SEED), disk=disk)}


@router.get("/semantic/intent-hints")
def agent_semantic_intent_hints(request: Request) -> dict[str, Any]:
    current_user(request)
    disk = _lex_load_intent_hints()
    return {"ok": True, **_lexicon_view(seed=dict(_SEMANTIC_INTENT_SEED), disk=disk)}


@router.put("/semantic/intent-hints")
def agent_semantic_intent_hints_upsert(req: LexiconUpsertReq, request: Request) -> dict[str, Any]:
    me = _require_agent_wiki_admin(request)
    key = str(req.key or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    by = str(me.get("username") or "")
    _lex_upsert_intent_hint(key, list(req.values or []), by=by, seed=dict(_SEMANTIC_INTENT_SEED))
    audit.record(request, action=f"semantic:intent_hint:upsert:{key}", detail=f"values={len(req.values or [])}", tab="ai_hub")
    disk = _lex_load_intent_hints()
    return {"ok": True, **_lexicon_view(seed=dict(_SEMANTIC_INTENT_SEED), disk=disk)}


@router.post("/semantic/intent-hints/delete")
def agent_semantic_intent_hints_delete(req: LexiconDeleteReq, request: Request) -> dict[str, Any]:
    me = _require_agent_wiki_admin(request)
    key = str(req.key or "").strip()
    if not key:
        raise HTTPException(400, "key is required")
    removed = _lex_delete_intent_hint(key, by=str(me.get("username") or ""))
    audit.record(request, action=f"semantic:intent_hint:delete:{key}", detail=f"removed={bool(removed)}", tab="ai_hub")
    disk = _lex_load_intent_hints()
    return {"ok": True, "removed": bool(removed), **_lexicon_view(seed=dict(_SEMANTIC_INTENT_SEED), disk=disk)}


@router.get("/semantic/changes")
def agent_semantic_changes(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
    current_user(request)
    return {"ok": True, "changes": _lex_list_changes(limit=limit)}


# ── Semantic learning proposals queue (P4-wire-up) ───────────────────
class ProposalDecideReq(BaseModel):
    id: str = ""
    status: str = ""
    canonical: str = ""  # optional override — admin can re-route a "new_canonical" proposal


@router.get("/semantic/proposals")
def agent_semantic_proposals(request: Request, status: str = Query(default=""),
                             limit: int = Query(default=200, ge=1, le=500)) -> dict[str, Any]:
    """List pending/approved/rejected proposals from the semantic learning queue."""
    current_user(request)
    status_filter = status.strip() or None
    rows = _learn_list_proposals(status=status_filter, limit=limit)
    return {"ok": True, "proposals": rows, "count": len(rows)}


@router.post("/semantic/proposals/decide")
def agent_semantic_proposals_decide(req: ProposalDecideReq, request: Request) -> dict[str, Any]:
    """Approve or reject a proposal. Approval applies it to the runtime lexicon."""
    me = _require_agent_wiki_admin(request)
    proposal_id = str(req.id or "").strip()
    if not proposal_id:
        raise HTTPException(400, "id is required")
    status = (req.status or "").strip().lower()
    if status not in {"approved", "rejected"}:
        raise HTTPException(400, "status must be 'approved' or 'rejected'")
    by = str(me.get("username") or "")
    updated = _learn_update_proposal_status(proposal_id, status=status, by=by)
    if not updated:
        raise HTTPException(404, "proposal not found")
    applied: dict[str, Any] = {"upserted": False, "canonical": ""}
    if status == "approved":
        term = str(updated.get("term") or "").strip()
        category = str(updated.get("category") or "")
        override_canonical = str(req.canonical or "").strip()
        canonical = override_canonical or str(updated.get("canonical_match") or "").strip()
        if category == "mapping" and canonical and term:
            current = _lex_load_alias_groups().get(canonical) or list(_SEMANTIC_ALIAS_SEED.get(canonical) or [])
            if term not in current:
                current = list(current) + [term]
            _lex_upsert_alias_group(canonical, current, by=by, seed=dict(_SEMANTIC_ALIAS_SEED))
            applied = {"upserted": True, "canonical": canonical}
        elif category == "new_canonical" and term:
            target = override_canonical or term.lower()
            current = _lex_load_alias_groups().get(target) or []
            if term not in current:
                current = list(current) + [term]
            _lex_upsert_alias_group(target, current, by=by, seed=dict(_SEMANTIC_ALIAS_SEED))
            applied = {"upserted": True, "canonical": target}
    audit.record(
        request,
        action=f"semantic:proposal:{status}:{proposal_id}",
        detail=f"term={updated.get('term') or ''};canonical={applied.get('canonical') or ''};upserted={bool(applied.get('upserted'))}",
        tab="ai_hub",
    )
    return {"ok": True, "proposal": updated, "applied": applied}


@router.post("/semantic/proposals/run-batch")
def agent_semantic_proposals_run_batch(request: Request) -> dict[str, Any]:
    """Admin-triggered batch — scan flowi_activity.jsonl for low-coverage tokens."""
    me = _require_agent_wiki_admin(request)
    activity_path = flowi_llm.FLOWI_ACTIVITY_FILE if hasattr(flowi_llm, "FLOWI_ACTIVITY_FILE") else (
        PATHS.data_root / "flowi_activity.jsonl"
    )
    enqueued = _learn_submit_activity_log_batch(activity_path)
    audit.record(request, action="semantic:proposals:run_batch", detail=f"enqueued={int(enqueued)}", tab="ai_hub")
    return {"ok": True, "enqueued": int(enqueued), "by": str(me.get("username") or "")}


@router.post("/runtime/run")
async def agent_runtime_run(req: AgentRuntimeRequest, request: Request) -> dict[str, Any]:
    me = current_user(request)
    result = await run_agent_runtime_once(req, str(me.get("username") or "user"))
    return {"ok": True, "run": result.model_dump(mode="json")}


@router.get("/runtime/stream")
async def agent_runtime_stream(
    request: Request,
    goal: str = Query(..., min_length=1, max_length=4000),
    use_llm: bool = Query(False),
    max_terms: int = Query(24, ge=1, le=80),
):
    me = current_user(request)
    req = AgentRuntimeRequest(goal=goal, use_llm=use_llm, max_terms=max_terms)

    async def _gen():
        async for event in stream_agent_runtime(req, str(me.get("username") or "user")):
            yield encode_sse_event(event)
            if await request.is_disconnected():
                break

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_slug(raw: Any, fallback: str = "agent") -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(raw or "").strip()).strip("._-")
    return (text or fallback)[:120]


def _listify(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _summary_text(*values: Any, limit: int = 240) -> str:
    text = " ".join(
        json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else str(v or "")
        for v in values
    )
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _prompt_review_missing(req: PromptReviewReq, row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for value in [*(req.missing or []), *((row or {}).get("missing") or [])]:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _prompt_review_question(field: str) -> str:
    key = flowi_llm._flowi_missing_key(field) if hasattr(flowi_llm, "_flowi_missing_key") else str(field or "").lower()
    labels = {
        "product": "제품명을 명시하세요. 예: PRODA",
        "root_lot_ids": "root lot 또는 fab lot을 명시하세요. 예: A1000, A1000A.3",
        "fab_lot_ids": "fab lot을 명시하세요. 예: A1000A.3",
        "wafer_ids": "wafer 번호를 명시하세요. 예: #6, WF6",
        "step": "step 또는 function step을 명시하세요. 예: 24.0 SORT",
        "module": "Inform 모듈을 명시하세요. 예: GATE, STI",
        "note": "Inform 본문에 넣을 내용을 한 문장으로 적으세요.",
        "recipient": "수신자, 모듈, 또는 메일 그룹을 명시하세요.",
        "metric": "확인할 item/metric을 명시하세요. 예: CD, VIA2 Avg",
        "knob_value": "찾을 KNOB 값을 명시하세요. 예: PPID_24_3",
    }
    return labels.get(key, f"{field} 값을 명시하세요.")


def _fallback_prompt_review(prompt: str, row: dict[str, Any], missing: list[str], error: str = "") -> dict[str, Any]:
    action = row.get("action") or row.get("unit_action") or row.get("intent") or "flowi action"
    questions = [_prompt_review_question(field) for field in missing]
    if not questions:
        questions = ["조회 기준, 결과 범위, 정렬 기준이 중요하면 prompt에 직접 적으세요."]
    improved = prompt
    if missing:
        improved = f"{prompt} (보강 필요: {', '.join(missing)})"
    tips = [
        f"dry-run action은 {action}입니다.",
        "실행 가능 여부는 기존 deterministic preview와 guardrail이 판단합니다.",
    ]
    if error:
        tips.append(f"LLM 점검 실패: {error[:180]}")
    return {
        "improved_prompt": improved,
        "ambiguous_questions": questions[:5],
        "tips": tips[:5],
        "missing": missing,
    }


def _clean_prompt_review_obj(obj: dict[str, Any], prompt: str, row: dict[str, Any], missing: list[str]) -> dict[str, Any]:
    fallback = _fallback_prompt_review(prompt, row, missing)
    improved = str(obj.get("improved_prompt") or obj.get("revised_prompt") or fallback["improved_prompt"]).strip()
    questions = obj.get("ambiguous_questions") or obj.get("questions") or fallback["ambiguous_questions"]
    tips = obj.get("tips") or obj.get("notes") or fallback["tips"]
    if not isinstance(questions, list):
        questions = [questions] if questions else []
    if not isinstance(tips, list):
        tips = [tips] if tips else []
    return {
        "improved_prompt": improved[:1200] or fallback["improved_prompt"],
        "ambiguous_questions": [str(x).strip()[:320] for x in questions if str(x).strip()][:5] or fallback["ambiguous_questions"],
        "tips": [str(x).strip()[:320] for x in tips if str(x).strip()][:5] or fallback["tips"],
        "missing": missing,
    }


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _relation_safe_relpath(raw: str) -> Path:
    text = str(raw or "").strip().replace("\\", "/")
    if not text or text.startswith("/") or text.startswith(".") or ".." in Path(text).parts:
        raise HTTPException(400, "invalid relation source path")
    return Path(text)


def _relation_source_id(src: SchemaRelationSource, resolved: Path | None = None) -> str:
    source_type = str(src.source_type or "file").strip().lower()
    if source_type == "db":
        label = "::".join([source_type, src.root or "db_root", src.product or (resolved.name if resolved else "")])
    else:
        label = "::".join([source_type, src.root or "base_root", src.file or (resolved.name if resolved else "")])
    return _safe_slug(label, "schema_source")


def _relation_resolve_root(raw: str, *, default: Path | None = None) -> Path:
    text = str(raw or "").strip()
    if not text or text in {"db", "db_root"}:
        return PATHS.db_root.resolve()
    if text in {"base", "base_root"}:
        return PATHS.base_root.resolve()
    rel = Path(text.replace("\\", "/"))
    if rel.is_absolute():
        cand = rel.resolve()
        if _is_relative_to(cand, PATHS.db_root) or _is_relative_to(cand, PATHS.data_root):
            return cand
        raise HTTPException(400, "root must be inside configured data/db roots")
    base = (default or PATHS.db_root).resolve()
    cand = (base / rel).resolve()
    if not _is_relative_to(cand, base):
        raise HTTPException(400, "invalid root path")
    return cand


def _relation_resolve_source_files(src: SchemaRelationSource, *, limit: int = 8) -> tuple[Path, list[Path]]:
    source_type = str(src.source_type or "file").strip().lower()
    if source_type == "db":
        root = _relation_resolve_root(src.root, default=PATHS.db_root)
        product = str(src.product or "").strip()
        target = (root / product).resolve() if product else root.resolve()
        if not _is_relative_to(target, root) or not target.exists():
            raise HTTPException(404, f"DB schema source not found: {src.root}/{src.product}".strip("/"))
        if target.is_file():
            files = [target] if target.suffix.lower() in {".csv", ".parquet"} else []
        else:
            files = sorted(target.rglob("*.parquet"))[:limit]
            if not files:
                files = sorted(target.rglob("*.csv"))[:limit]
        if not files:
            raise HTTPException(404, f"No csv/parquet files found for schema source: {target}")
        return target, files

    rel = _relation_safe_relpath(src.file)
    roots = [_relation_resolve_root(src.root, default=PATHS.db_root)] if src.root else [PATHS.base_root.resolve(), PATHS.db_root.resolve()]
    for root in roots:
        cand = (root / rel).resolve()
        if _is_relative_to(cand, root) and cand.is_file() and cand.suffix.lower() in {".csv", ".parquet"}:
            return cand, [cand]
    raise HTTPException(404, f"Single-file schema source not found: {src.file}")


def _relation_scan_file(fp: Path) -> pl.LazyFrame:
    suffix = fp.suffix.lower()
    if suffix == ".parquet":
        return pl.scan_parquet(str(fp))
    if suffix == ".csv":
        return pl.scan_csv(str(fp), infer_schema_length=5000, try_parse_dates=False)
    raise HTTPException(400, f"Unsupported schema source: {fp.suffix}")


def _file_sha256(fp: Path) -> str:
    h = hashlib.sha256()
    with fp.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _lazy_row_count(lf: pl.LazyFrame) -> int | None:
    try:
        return int(lf.select(pl.len().alias("row_count")).collect().item())
    except Exception:
        return None


def _relation_dtype_family(dtype: Any) -> str:
    text = str(dtype or "").lower()
    if any(x in text for x in ("int", "float", "decimal", "number")):
        return "number"
    if any(x in text for x in ("date", "time")):
        return "time"
    if any(x in text for x in ("bool",)):
        return "bool"
    return "string"


_RELATION_ALIASES = {
    "product": {"product", "prod", "device"},
    "root_lot_id": {"rootlotid", "root_lot_id", "rootlot", "lot", "lot_id", "lotid"},
    "fab_lot_id": {"fablotid", "fab_lot_id", "currentlotid", "current_lot_id"},
    "wafer_id": {"waferid", "wafer_id", "wf", "wf_id", "wfid"},
    "lot_wf": {"lotwf", "lot_wf", "lotwafer", "lot_wafer"},
    "step_id": {"stepid", "step_id", "processstep", "process_step"},
    "function_step": {"functionstep", "function_step", "funcstep", "func_step", "module"},
    "ppid": {"ppid", "recipe", "recipe_id"},
    "knob": {"knob", "knob_name", "split", "split_id"},
    "item": {"item", "item_id", "parameter", "metric"},
}


def _relation_norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _schema_column_name(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    return text[:120]


def _relation_canonical_col(name: str) -> str:
    norm = _relation_norm_col(name)
    for canonical, aliases in _RELATION_ALIASES.items():
        if norm in aliases:
            return canonical
    if norm.endswith("id") and len(norm) > 3:
        return _schema_column_name(name) or norm
    return ""


def _schema_column_aliases(row: dict[str, Any]) -> set[str]:
    aliases = {
        _relation_norm_col(row.get("column") or ""),
        _relation_norm_col(row.get("canonical_alias") or ""),
    }
    for raw in row.get("raw_names") or []:
        aliases.add(_relation_norm_col(raw))
    return {x for x in aliases if x}


def _source_relation_id_candidates(src: SchemaRelationSource, target: Path | None = None) -> set[str]:
    values = {
        src.label,
        src.product,
        src.file,
        target.name if target else "",
        target.stem if target and target.is_file() else "",
    }
    out: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        out.add(text)
        if "." in text:
            out.add(text.rsplit(".", 1)[0])
    return {x for x in out if x}


def _catalog_entries_for_source(src: SchemaRelationSource, target: Path | None = None) -> list[dict[str, Any]]:
    candidates = {x.lower() for x in _source_relation_id_candidates(src, target)}
    if not candidates:
        return []
    return [
        row for row in _public_column_catalog()
        if str(row.get("relation_id") or "").strip().lower() in candidates
    ]


def _catalog_find_column(relation_id: str, column: str) -> dict[str, Any] | None:
    rel = str(relation_id or "").strip().lower()
    col_norm = _relation_norm_col(column)
    if not rel or not col_norm:
        return None
    for row in _public_column_catalog():
        if str(row.get("relation_id") or "").strip().lower() != rel:
            continue
        if col_norm in _schema_column_aliases(row):
            return row
    return None


def _schema_known_relation_ids(*, include_discovered: bool = False) -> list[str]:
    payload = _schema_relations_payload()
    ids: set[str] = set()
    for row in payload.get("relations") or []:
        if not isinstance(row, dict):
            continue
        for key in ("relation_id", "left_label", "right_label", "left_source_id", "right_source_id"):
            value = str(row.get(key) or "").strip()
            if value:
                ids.add(value)
    for row in payload.get("column_catalog") or []:
        if isinstance(row, dict) and str(row.get("relation_id") or "").strip():
            ids.add(str(row.get("relation_id") or "").strip())
    if include_discovered:
        try:
            for src in _relation_discover_sources(max_sources=80):
                ids.update(_source_relation_id_candidates(src))
        except Exception:
            pass
    return sorted(ids, key=str.lower)


def _profile_relation_id(profile: dict[str, Any]) -> str:
    source_type = str(profile.get("source_type") or "").strip().lower()
    if source_type == "file":
        file_name = str(profile.get("file") or "").strip()
        if file_name:
            return Path(file_name).stem
    for key in ("product", "label", "source_id"):
        value = str(profile.get(key) or "").strip()
        if value:
            return Path(value).stem if "." in value else value
    return ""


def _catalog_stubs_from_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stubs: list[dict[str, Any]] = []
    for profile in profiles:
        relation_id = _profile_relation_id(profile)
        if not relation_id:
            continue
        dtypes = profile.get("dtypes") if isinstance(profile.get("dtypes"), dict) else {}
        samples = profile.get("sample_values") if isinstance(profile.get("sample_values"), dict) else {}
        for col in profile.get("columns") or []:
            column = _relation_canonical_col(col) or _schema_column_name(col)
            if not column:
                continue
            stubs.append({
                "relation_id": relation_id,
                "column": column,
                "raw_names": [col],
                "dtype": _relation_dtype_family(dtypes.get(col)),
                "canonical_alias": column,
                "unit": None,
                "fk": None,
                "sample_values": samples.get(col) or [],
                "wiki_doc_id": "",
            })
    return stubs


def _column_candidate(row: dict[str, Any], *, source: str, score: float, wiki_doc: dict[str, Any] | None = None) -> dict[str, Any]:
    doc = wiki_doc or {}
    wiki_doc_id = str(row.get("wiki_doc_id") or doc.get("doc_id") or "").strip()
    return {
        "relation_id": row.get("relation_id") or "",
        "column": row.get("column") or "",
        "raw_names": row.get("raw_names") or [],
        "dtype": row.get("dtype") or "",
        "canonical_alias": row.get("canonical_alias") or row.get("column") or "",
        "unit": row.get("unit"),
        "fk": row.get("fk"),
        "sample_values": row.get("sample_values") or [],
        "wiki_doc_id": wiki_doc_id,
        "wiki_title": doc.get("title") or "",
        "wiki_summary": doc.get("summary") or "",
        "source": source,
        "score": score,
    }


def _fallback_alias_candidates(term: str, *, limit: int = 5) -> list[dict[str, Any]]:
    term_norm = _relation_norm_col(term)
    if not term_norm:
        return []
    canonical_matches: list[str] = []
    for canonical, aliases in _RELATION_ALIASES.items():
        alias_norms = {_relation_norm_col(x) for x in aliases}
        if term_norm == _relation_norm_col(canonical) or term_norm in alias_norms or any(alias and (alias in term_norm or term_norm in alias) for alias in alias_norms):
            canonical_matches.append(canonical)
    out: list[dict[str, Any]] = []
    for canonical in canonical_matches:
        canonical_norm = _relation_norm_col(canonical)
        matched = [
            row for row in _public_column_catalog()
            if canonical_norm in _schema_column_aliases(row)
        ]
        if matched:
            out.extend(_column_candidate(row, source="alias_catalog", score=0.55) for row in matched)
        else:
            out.append(_column_candidate(
                {
                    "relation_id": "",
                    "column": canonical,
                    "canonical_alias": canonical,
                    "raw_names": [],
                    "dtype": "",
                    "sample_values": [],
                    "wiki_doc_id": "",
                },
                source="alias_fallback",
                score=0.35,
            ))
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in out:
        dedup.setdefault((str(row.get("relation_id") or ""), str(row.get("column") or ""), str(row.get("source") or "")), row)
    return sorted(dedup.values(), key=lambda r: float(r.get("score") or 0), reverse=True)[: max(1, min(limit, 50))]


def resolve_term_to_columns(term: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Resolve a natural-language term to schema column candidates.

    Wiki schema_doc hits are the primary signal. The hardcoded alias table is
    only used when no schema_doc matches the term.
    """
    q = str(term or "").strip()
    if not q:
        return []
    docs = kv.list_docs(kind="schema_doc", q=q, limit=max(limit, 20))
    if len(docs) < limit:
        term_norm = _relation_norm_col(q)
        seen_doc_ids = {str(row.get("doc_id") or "") for row in docs}
        for row in kv.list_docs(kind="schema_doc", limit=1000):
            doc_id = str(row.get("doc_id") or "")
            if not doc_id or doc_id in seen_doc_ids:
                continue
            hay_norm = _relation_norm_col(" ".join([
                str(row.get("doc_id") or ""),
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                " ".join(map(str, row.get("tags") or [])),
                str(row.get("relation_id") or ""),
                " ".join(map(str, row.get("column_refs") or [])),
            ]))
            if term_norm and term_norm in hay_norm:
                docs.append(row)
                seen_doc_ids.add(doc_id)
            if len(docs) >= limit:
                break
    candidates: list[dict[str, Any]] = []
    for brief in docs:
        doc = kv.get_doc(str(brief.get("doc_id") or "")) or brief
        fm = doc.get("frontmatter") if isinstance(doc.get("frontmatter"), dict) else {}
        relation_id = str(fm.get("relation_id") or brief.get("relation_id") or "").strip()
        refs = fm.get("column_refs") if isinstance(fm.get("column_refs"), list) else brief.get("column_refs") if isinstance(brief.get("column_refs"), list) else []
        if not refs and relation_id:
            refs = [f"{relation_id}.{row.get('column')}" for row in _public_column_catalog() if str(row.get("relation_id") or "").strip() == relation_id]
        for ref in refs:
            text = str(ref or "").strip()
            if "." not in text:
                continue
            rel, col = text.split(".", 1)
            catalog_row = _catalog_find_column(rel, col) or {
                "relation_id": rel,
                "column": col,
                "canonical_alias": col,
                "raw_names": [],
                "dtype": "",
                "unit": None,
                "fk": None,
                "sample_values": [],
                "wiki_doc_id": doc.get("doc_id") or "",
            }
            candidates.append(_column_candidate(catalog_row, source="schema_doc", score=0.9, wiki_doc=doc))
    if not docs:
        candidates.extend(_fallback_alias_candidates(q, limit=limit))
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in candidates:
        key = (str(row.get("relation_id") or ""), str(row.get("column") or ""))
        if not key[1]:
            continue
        if key not in dedup or float(row.get("score") or 0) > float(dedup[key].get("score") or 0):
            dedup[key] = row
    return sorted(dedup.values(), key=lambda r: float(r.get("score") or 0), reverse=True)[: max(1, min(limit, 50))]


def _relation_read_source(src: SchemaRelationSource, *, sample_rows: int = 20) -> dict[str, Any]:
    target, files = _relation_resolve_source_files(src)
    first = files[0]
    lf = _relation_scan_file(first)
    schema = lf.collect_schema()
    columns = list(schema.names())
    dtypes = {name: str(schema[name]) for name in columns}
    catalog_entries = _catalog_entries_for_source(src, target)
    catalog_aliases = {alias for row in catalog_entries for alias in _schema_column_aliases(row)}
    key_columns = [c for c in columns if _relation_canonical_col(c) or _relation_norm_col(c) in catalog_aliases][:30]
    sample_values: dict[str, list[str]] = {}
    sample_columns = list(dict.fromkeys([*key_columns, *columns[:120]]))
    if sample_columns and sample_rows > 0:
        try:
            df = lf.select(sample_columns).head(min(max(int(sample_rows), 1), 50)).collect()
            for col in sample_columns:
                vals = []
                for value in df.get_column(col).drop_nulls().unique().head(6).to_list():
                    text = str(value).strip()
                    if text:
                        vals.append(text[:80])
                sample_values[col] = vals
        except Exception:
            sample_values = {}
    catalog_warnings: list[dict[str, Any]] = []
    for row in catalog_entries:
        matched = [col for col in columns if _relation_norm_col(col) in _schema_column_aliases(row)]
        if not matched:
            catalog_warnings.append({
                "relation_id": row.get("relation_id") or "",
                "column": row.get("column") or "",
                "warning": "catalog column not found in scanned source",
            })
            continue
        scanned_dtype = dtypes.get(matched[0]) or ""
        catalog_dtype = str(row.get("dtype") or "").strip()
        if catalog_dtype and scanned_dtype and _relation_dtype_family(catalog_dtype) != _relation_dtype_family(scanned_dtype):
            catalog_warnings.append({
                "relation_id": row.get("relation_id") or "",
                "column": row.get("column") or "",
                "warning": "catalog dtype differs from scanned dtype",
                "catalog_dtype": catalog_dtype,
                "scanned_dtype": scanned_dtype,
            })
    source_id = _relation_source_id(src, target)
    row_count = _lazy_row_count(lf)
    checksum = ""
    try:
        checksum = _file_sha256(first) if first.is_file() else ""
    except Exception:
        checksum = ""
    return {
        "source_id": source_id,
        "source_type": str(src.source_type or "file").strip().lower(),
        "label": src.label or src.product or src.file or target.name,
        "root": src.root,
        "product": src.product,
        "file": src.file,
        "resolved_path": str(target),
        "files_scanned": len(files),
        "row_count": row_count,
        "checksum": checksum,
        "columns": columns,
        "dtypes": dtypes,
        "key_columns": key_columns,
        "sample_values": sample_values,
        "column_catalog": catalog_entries,
        "catalog_warnings": catalog_warnings,
    }


def _relation_discover_sources(*, max_sources: int = 24) -> list[SchemaRelationSource]:
    sources: list[SchemaRelationSource] = []
    seen: set[str] = set()

    def add(src: SchemaRelationSource) -> None:
        key = json.dumps(src.model_dump() if hasattr(src, "model_dump") else src.dict(), sort_keys=True, ensure_ascii=False)
        if key in seen:
            return
        seen.add(key)
        sources.append(src)

    db_root = PATHS.db_root.resolve()
    try:
        for db_dir in sorted([p for p in db_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            name_u = _safe_slug(db_dir.name).upper()
            if db_dir.name.startswith((".", "_")) or name_u in {"CACHE", "HISTORY", "BACKUPS"}:
                continue
            products = [p for p in db_dir.iterdir() if p.is_dir()] if "RAWDATA_DB" in name_u else []
            for product_dir in sorted(products, key=lambda p: p.name.lower()):
                if product_dir.name.startswith((".", "_")):
                    continue
                add(SchemaRelationSource(
                    source_type="db",
                    root=db_dir.name,
                    product=product_dir.name,
                    label=f"{db_dir.name.replace('1.RAWDATA_DB_', '')} {product_dir.name}",
                ))
    except Exception:
        pass

    for root_label, root in (("base_root", PATHS.base_root.resolve()), ("db_root", db_root)):
        try:
            for fp in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if not fp.is_file() or fp.name.startswith((".", "_")) or fp.suffix.lower() not in {".csv", ".parquet"}:
                    continue
                add(SchemaRelationSource(
                    source_type="file",
                    root=root_label,
                    file=fp.name,
                    label=fp.stem,
                ))
        except Exception:
            continue

    return sources[:max(2, min(int(max_sources or 24), 80))]


def _relation_candidate_id(candidate: dict[str, Any]) -> str:
    raw = "::".join([
        str(candidate.get("left_source_id") or ""),
        str(candidate.get("left_column") or ""),
        str(candidate.get("right_source_id") or ""),
        str(candidate.get("right_column") or ""),
        str(candidate.get("relation_type") or ""),
    ])
    import hashlib
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]


def _relation_candidate_rationale(row: dict[str, Any]) -> str:
    evidence = [str(x) for x in (row.get("evidence") or []) if str(x or "").strip()]
    left = f"{row.get('left_label') or row.get('left_source_id')}.{row.get('left_column')}"
    right = f"{row.get('right_label') or row.get('right_source_id')}.{row.get('right_column')}"
    score = float(row.get("confidence") or 0)
    if evidence:
        return f"{left} 와 {right} 는 {', '.join(evidence[:3])} 근거로 join key 후보입니다. 신뢰도 {score:.2f}."
    return f"{left} 와 {right} 는 컬럼명/타입 유사도 기반 join key 후보입니다. 신뢰도 {score:.2f}."


def _relation_candidate_graph(relations: list[dict[str, Any]]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for row in relations:
        left_id = str(row.get("left_source_id") or "")
        right_id = str(row.get("right_source_id") or "")
        if not left_id or not right_id:
            continue
        nodes.setdefault(left_id, {"id": left_id, "label": row.get("left_label") or left_id, "type": row.get("left_source_type") or "source"})
        nodes.setdefault(right_id, {"id": right_id, "label": row.get("right_label") or right_id, "type": row.get("right_source_type") or "source"})
        edges.append({
            "id": row.get("relation_id") or _relation_candidate_id(row),
            "source": left_id,
            "target": right_id,
            "label": f"{row.get('left_column')} = {row.get('right_column')}",
            "relation_type": row.get("relation_type") or "join_key",
            "confidence": row.get("confidence"),
            "status": row.get("status") or "candidate",
        })
    return {"nodes": list(nodes.values()), "edges": edges, "layout": "schema_relation_graph"}


def _relation_candidates(profiles: list[dict[str, Any]], *, limit: int = 30) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for i, left in enumerate(profiles):
        for right in profiles[i + 1:]:
            right_by_norm: dict[str, list[str]] = {}
            right_by_canon: dict[str, list[str]] = {}
            for col in right.get("columns") or []:
                right_by_norm.setdefault(_relation_norm_col(col), []).append(col)
                canon = _relation_canonical_col(col)
                if canon:
                    right_by_canon.setdefault(canon, []).append(col)
            for left_col in (left.get("columns") or [])[:180]:
                norm = _relation_norm_col(left_col)
                canon = _relation_canonical_col(left_col)
                matches = set(right_by_norm.get(norm, []))
                if canon:
                    matches.update(right_by_canon.get(canon, []))
                for right_col in list(matches)[:4]:
                    left_dtype = (left.get("dtypes") or {}).get(left_col)
                    right_dtype = (right.get("dtypes") or {}).get(right_col)
                    dtype_match = _relation_dtype_family(left_dtype) == _relation_dtype_family(right_dtype)
                    exact = norm == _relation_norm_col(right_col)
                    confidence = 0.72
                    evidence = []
                    if exact:
                        confidence += 0.18
                        evidence.append("normalized column name match")
                    if canon:
                        confidence += 0.08
                        evidence.append(f"known join-key alias: {canon}")
                    if dtype_match:
                        confidence += 0.03
                        evidence.append(f"dtype compatible: {_relation_dtype_family(left_dtype)}")
                    left_samples = (left.get("sample_values") or {}).get(left_col) or []
                    right_samples = (right.get("sample_values") or {}).get(right_col) or []
                    overlap = sorted(set(left_samples) & set(right_samples))[:5]
                    if overlap:
                        confidence += 0.05
                        evidence.append(f"sample overlap: {', '.join(overlap[:3])}")
                    row = {
                        "left_source_id": left.get("source_id"),
                        "left_label": left.get("label"),
                        "left_source_type": left.get("source_type"),
                        "left_column": left_col,
                        "left_dtype": left_dtype,
                        "right_source_id": right.get("source_id"),
                        "right_label": right.get("label"),
                        "right_source_type": right.get("source_type"),
                        "right_column": right_col,
                        "right_dtype": right_dtype,
                        "canonical_key": canon or norm,
                        "relation_type": "join_key",
                        "confidence": round(min(confidence, 0.99), 2),
                        "evidence": evidence or ["heuristic column similarity"],
                        "left_sample": left_samples[:5],
                        "right_sample": right_samples[:5],
                        "status": "preview",
                    }
                    row["rationale"] = _relation_candidate_rationale(row)
                    row["relation_id"] = _relation_candidate_id(row)
                    candidates.append(row)
    candidates.sort(key=lambda r: (float(r.get("confidence") or 0), bool(r.get("left_sample") and r.get("right_sample"))), reverse=True)
    dedup: dict[str, dict[str, Any]] = {}
    for row in candidates:
        dedup.setdefault(str(row.get("relation_id")), row)
    return list(dedup.values())[: max(1, min(int(limit or 30), 100))]


def _relation_preview_from_sources(
    sources: list[SchemaRelationSource],
    *,
    max_candidates: int = 30,
    sample_rows: int = 20,
    db_file_only: bool = False,
) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for src in sources[:80]:
        try:
            profiles.append(_relation_read_source(src, sample_rows=sample_rows))
        except HTTPException as exc:
            errors.append({
                "source": src.model_dump() if hasattr(src, "model_dump") else src.dict(),
                "status": exc.status_code,
                "detail": exc.detail,
            })
    if len(profiles) < 2:
        raise HTTPException(400, {"message": "not enough readable schema sources", "errors": errors})
    candidate_limit = max_candidates * 3 if db_file_only else max_candidates
    candidates = _relation_candidates(profiles, limit=candidate_limit)
    if db_file_only:
        candidates = [
            row for row in candidates
            if {str(row.get("left_source_type") or ""), str(row.get("right_source_type") or "")} == {"db", "file"}
        ][:max(1, min(int(max_candidates or 30), 100))]
    return {
        "profiles": profiles,
        "errors": errors,
        "candidates": candidates,
    }


def _schema_relations_payload() -> dict[str, Any]:
    data = load_json(SCHEMA_RELATION_FILE, {"relations": [], "column_catalog": []})
    if not isinstance(data, dict):
        data = {"relations": [], "column_catalog": []}
    if not isinstance(data.get("relations"), list):
        data["relations"] = []
    if not isinstance(data.get("column_catalog"), list):
        data["column_catalog"] = []
    return data


def _public_schema_relations() -> list[dict[str, Any]]:
    payload = _schema_relations_payload()
    return [row for row in payload.get("relations") or [] if isinstance(row, dict)]


def _public_column_catalog() -> list[dict[str, Any]]:
    payload = _schema_relations_payload()
    return [row for row in payload.get("column_catalog") or [] if isinstance(row, dict)]


def _hit(item: dict[str, Any], needle: str, tag: str) -> bool:
    if tag:
        tags = " ".join(str(x).lower() for x in _listify(item.get("tags")))
        if tag not in tags and tag not in str(item.get("summary") or "").lower() and tag not in str(item.get("title") or "").lower():
            return False
    if not needle:
        return True
    text = json.dumps(item, ensure_ascii=False, default=str).lower()
    return needle in text


def _promoted_payload() -> dict[str, Any]:
    data = load_json(flowi_llm.FLOWI_PROMOTED_KNOWLEDGE_FILE, {"items": []})
    if isinstance(data, list):
        data = {"items": data}
    if not isinstance(data, dict):
        data = {"items": []}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def _promoted_ids() -> set[str]:
    return {str(item.get("id") or "") for item in flowi_llm._flowi_promoted_knowledge_items(limit=200)}


def _backup_state(kind: str, current: Any) -> str:
    AGENT_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    fp = AGENT_BACKUP_DIR / f"{stamp}_{_safe_slug(kind)}.json"
    save_json(fp, current if current not in (None, "") else {}, indent=2)
    return str(fp)


def _require_agent_admin(request: Request) -> dict[str, Any]:
    return require_admin(request)


def _can_manage_agent_knowledge(me: dict[str, Any]) -> bool:
    username = me.get("username") or ""
    return (
        is_page_manager(me, "diagnosis")
        or is_page_manager(me, "agent")
        or is_page_manager(me, "knowledge")
        or is_page_admin(username, "diagnosis")
        or is_page_admin(username, "agent")
        or is_page_admin(username, "knowledge")
    )


def _require_agent_wiki_admin(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if _can_manage_agent_knowledge(me):
        return me
    raise HTTPException(403, "admin or diagnosis/agent/knowledge page manager only")


_AGENT_WIKI_PAGE_KINDS = {"product", "lot", "wafer", "knob", "issue", "meeting", "report", "decision", "agent_wiki", "schema_doc", "ontology", "manual"}
_WIKI_MANAGED_FRONTMATTER_KEYS = {
    "doc_id",
    "kind",
    "title",
    "summary",
    "actor",
    "created_at",
    "updated_at",
    "product",
    "root_lot_id",
    "wafer_id",
    "tags",
    "source_event_ids",
}


def _custom_wiki_frontmatter(*payloads: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if key in _WIKI_MANAGED_FRONTMATTER_KEYS:
                continue
            if value in ("", None, [], {}):
                continue
            out[key] = value
    return out


def _strip_generated_h1(body: str, title: str = "", previous_title: str = "") -> str:
    text = str(body or "").lstrip()
    if not text.startswith("# "):
        return str(body or "").rstrip() + ("\n" if str(body or "").strip() else "")
    lines = text.splitlines()
    first_title = lines[0].lstrip("#").strip()
    allowed = {
        str(title or "").strip().lower(),
        str(previous_title or "").strip().lower(),
    }
    allowed = {x for x in allowed if x}
    if allowed and first_title.lower() not in allowed:
        return str(body or "").rstrip() + "\n"
    rest = lines[1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(rest).rstrip() + ("\n" if rest else "")


def _require_agent_schema_admin(request: Request) -> dict[str, Any]:
    return _require_agent_wiki_admin(request)


def _activity_rows(limit: int = 1000) -> list[dict[str, Any]]:
    return flowi_llm._read_jsonl(flowi_llm.FLOWI_ACTIVITY_FILE, limit=limit)


def _workflow_stages() -> list[dict[str, Any]]:
    task2_functions = [
        "query_lot_knobs_from_ml_table",
        "query_current_fab_lot",
        "query_fab_progress",
        "compose_inform_module_mail",
        "query_wafer_split_at_step",
        "find_lots_by_knob_value",
        "query_metric_at_step",
        "register_inform_log",
        "route_flowi_feature",
        "register_inform_walkthrough",
    ]
    return [
        {
            "key": "input_prompt",
            "label": "입력 prompt",
            "description": "홈 Flowi와 외부 agent chat이 같은 prompt/context를 받습니다.",
            "modules": ["frontend My_Home FlowiConsole", "POST /api/llm/flowi/chat", "POST /api/llm/flowi/agent/chat"],
            "knowledge_sources": ["사용자 메모", "최근 Flowi 기록", "agent entrypoint guide"],
        },
        {
            "key": "slot_extract",
            "label": "slot extract(rule)",
            "description": "product, root/fab lot, wafer, step, metric, module, source를 deterministic rule로 먼저 분리합니다.",
            "modules": ["_slot_summary", "_classified_lot_tokens", "_flowi_func_step_token", "_flowi_metric_token"],
            "knowledge_sources": ["FLOWI_NAMING_RULES", "product_config/products.yaml", "ML_TABLE/FAB product directory"],
        },
        {
            "key": "intent_infer",
            "label": "intent infer(rule)",
            "description": "feature alias와 trigger term으로 가장 가까운 단위기능을 고릅니다.",
            "modules": ["_matched_feature_entrypoints", "_flowi_infer_function_call"],
            "knowledge_sources": ["FLOWI_FEATURE_ENTRYPOINTS", "FLOWI_FEATURE_ALIASES", "flowi_agent_features/*.md"],
        },
        {
            "key": "arguments",
            "label": "arguments 정형화",
            "description": "선택된 함수 schema에 맞춰 arguments JSON과 missing field 선택지를 구성합니다.",
            "modules": ["_structure_flowi_function_call", "_flowi_arguments_choices"],
            "knowledge_sources": ["FLOWI_FUNCTION_FEW_SHOTS", "task #2 Q1-Q8 acceptance prompts"],
        },
        {
            "key": "dispatch",
            "label": "action 실행/dispatch",
            "description": "허용된 backend tool만 호출하고 쓰기 작업은 confirm-before-write로 제한합니다.",
            "modules": task2_functions,
            "knowledge_sources": ["Flowi whitelist tools", "read-only guardrail", "page permission"],
        },
        {
            "key": "cross_db_join",
            "label": "cross-DB join",
            "description": "Dashboard 요청은 FAB/ET/INLINE grain을 맞춰 cross-DB chart plan과 join 결과로 넘깁니다.",
            "modules": ["dashboard_join", "_augment_dashboard_tool", "build_metric_scatter", "flowi_chart_plan"],
            "knowledge_sources": ["task #5 Dashboard", "source profile", "join key registry"],
        },
        {
            "key": "polish",
            "label": "polish(LLM, 선택)",
            "description": "LLM 사용 가능 시 로컬 결과 JSON과 promoted 사내 지식을 system prompt 끝에 붙여 짧게 정리합니다.",
            "modules": ["llm_adapter.complete", "_flowi_system_prompt"],
            "knowledge_sources": ["promoted_knowledge", "persona", "few-shot examples"],
        },
        {
            "key": "response",
            "label": "응답",
            "description": "answer, table/chart, workflow_state, next_actions, public trace를 같은 응답으로 반환합니다.",
            "modules": ["_finalize_flowi_tool", "_attach_flowi_trace", "_flowi_home_response_for_role"],
            "knowledge_sources": ["flowi_activity.jsonl", "retrieved_ids trace"],
        },
    ]


@router.get("/workflow")
def agent_workflow(request: Request):
    current_user(request)
    stages = _workflow_stages()
    return {
        "ok": True,
        "stages": stages,
        "stage_count": len(stages),
        "chain": " -> ".join(stage["label"] for stage in stages),
    }


@router.get("/persona")
def agent_persona(request: Request):
    me = current_user(request)
    username = me.get("username") or "user"
    rows = [r for r in _activity_rows(800) if r.get("username") == username]
    module_counter: Counter[str] = Counter()
    product_counter: Counter[str] = Counter()
    last_actions: list[dict[str, Any]] = []
    for rec in reversed(rows):
        fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        prompt = str(fields.get("prompt") or fields.get("prompt_excerpt") or "")
        for key in ("module", "feature", "intent", "selected_function"):
            value = str(fields.get(key) or "").strip()
            if value:
                module_counter[value] += 1
        for value in re.findall(r"\bPROD[A-Z0-9_]*\b", prompt.upper()):
            product_counter[value] += 1
        product = str(fields.get("product") or "").strip().upper()
        if product:
            product_counter[product] += 1
        if len(last_actions) < 8:
            last_actions.append({
                "timestamp": rec.get("timestamp") or "",
                "event": rec.get("event") or "",
                "prompt": prompt[:180],
                "selected_function": fields.get("selected_function") or fields.get("action") or fields.get("intent") or "",
                "result_status": fields.get("result_status") or "",
            })
    notes = ""
    try:
        notes = flowi_llm._notes_from_md(flowi_llm._read_user_md(username, create=False))
    except Exception:
        notes = ""
    style_hints = [
        "필수 slot이 애매하면 1/2/3 선택지로 되묻기",
        "표와 JSON은 짧게, 근거 id는 남기기",
    ]
    if notes:
        style_hints.insert(0, notes[:240])
    return {
        "ok": True,
        "username": username,
        "role": me.get("role") or "user",
        "recent_modules": [{"name": k, "count": v} for k, v in module_counter.most_common(8)],
        "frequent_products": [{"product": k, "count": v} for k, v in product_counter.most_common(8)],
        "last_actions": last_actions,
        "style_hints": style_hints,
        "admin_persona": flowi_llm.FLOWI_AGENT_PERSONA,
    }


@router.post("/prompt-preview")
def prompt_preview(req: PromptPreviewReq, request: Request):
    current_user(request)
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    out = flowi_llm._structure_flowi_function_call(prompt, product=req.product, max_rows=req.max_rows)
    selected = out.get("selected_function") if isinstance(out.get("selected_function"), dict) else {}
    out["few_shot_examples"] = [
        item for item in flowi_llm.FLOWI_FUNCTION_FEW_SHOTS
        if item.get("name") == selected.get("name") or item.get("intent") == selected.get("intent")
    ][:3]
    return out


@router.post("/prompt-review")
def prompt_review(req: PromptReviewReq, request: Request):
    current_user(request)
    prompt = (req.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt is required")
    row = req.preview_row if isinstance(req.preview_row, dict) else {}
    if not row:
        rows = flowi_llm._flowi_orchestrator_activation_previews(
            [prompt],
            product=(req.product or "").strip(),
            max_rows=req.max_rows,
        )
        row = rows[0] if rows else {}
    missing = _prompt_review_missing(req, row)
    fallback = _fallback_prompt_review(prompt, row, missing)
    llm_info = {"available": llm_adapter.is_available(), "used": False, "error": ""}
    if not llm_adapter.is_available():
        return {
            "ok": True,
            "source": "fallback",
            "prompt": prompt,
            "preview_row": row,
            "review": fallback,
            "llm": {**llm_info, "error": "llm unavailable"},
            "deterministic_status": row.get("status") or "",
        }

    schema = {
        "type": "object",
        "properties": {
            "improved_prompt": {"type": "string"},
            "ambiguous_questions": {"type": "array"},
            "tips": {"type": "array"},
        },
        "required": ["improved_prompt", "ambiguous_questions", "tips"],
    }
    ask = (
        "아래 Flow-i 프롬프트를 실행하지 말고 문장만 점검하세요.\n"
        "역할: 반도체 업무 앱 사용자에게 더 명확한 한국어 프롬프트와 확인 질문을 제안합니다.\n"
        "금지: 실행 가능/불가능 판정, 권한 판정, 데이터 변경 제안. 실행 판단은 deterministic preview가 합니다.\n"
        "JSON keys: improved_prompt, ambiguous_questions, tips.\n\n"
        f"prompt:\n{prompt[:2000]}\n\n"
        f"deterministic_preview:\n{json.dumps(row, ensure_ascii=False, default=str)[:4000]}\n\n"
        f"missing_slots:\n{json.dumps(missing, ensure_ascii=False)}"
    )
    out = llm_adapter.complete_json(
        ask,
        system="Flow-i 프롬프트 점검은 한국어 업무 문장 개선과 모호점 질문만 반환합니다. 실행 판단을 하지 마세요.",
        schema=schema,
        timeout=12,
        max_retries=1,
    )
    if not out.get("ok"):
        return {
            "ok": True,
            "source": "fallback",
            "prompt": prompt,
            "preview_row": row,
            "review": _fallback_prompt_review(prompt, row, missing, error=str(out.get("error") or "")),
            "llm": {**llm_info, "error": str(out.get("error") or "llm review failed")},
            "deterministic_status": row.get("status") or "",
        }
    return {
        "ok": True,
        "source": "llm",
        "prompt": prompt,
        "preview_row": row,
        "review": _clean_prompt_review_obj(out.get("obj") or {}, prompt, row, missing),
        "llm": {"available": True, "used": True, "error": ""},
        "deterministic_status": row.get("status") or "",
    }


@router.post("/schema-relations/preview")
def schema_relation_preview(req: SchemaRelationPreviewReq, request: Request):
    """Preview join/relation candidates from DB and single-file schemas.

    This endpoint only reads configured DB/base files. It never writes relation
    definitions or mutates source data; `/schema-relations/save` is the admin
    confirmation boundary.
    """
    current_user(request)
    sources = [src for src in (req.sources or []) if (src.product or src.file or src.root)]
    if len(sources) < 2:
        raise HTTPException(400, "at least two schema sources are required")
    preview = _relation_preview_from_sources(
        sources[:6],
        max_candidates=req.max_candidates,
        sample_rows=req.sample_rows,
    )
    candidates = preview["candidates"]
    return {
        "ok": True,
        "preview_only": True,
        "saved": False,
        "sources": preview["profiles"],
        "errors": preview["errors"],
        "candidates": candidates,
        "graph": _relation_candidate_graph(candidates),
        "saved_graph": _relation_candidate_graph(_public_schema_relations()),
    }


@router.post("/schema-relations/scan")
def schema_relation_scan(req: SchemaRelationScanReq, request: Request):
    """Scan configured DB products and root-level single files for relation candidates."""
    current_user(request)
    sources = _relation_discover_sources(max_sources=req.max_sources)
    if len(sources) < 2:
        raise HTTPException(400, "not enough DB/file schema sources to scan")
    preview = _relation_preview_from_sources(
        sources,
        max_candidates=req.max_candidates,
        sample_rows=req.sample_rows,
        db_file_only=True,
    )
    candidates = preview["candidates"]
    return {
        "ok": True,
        "mode": "scan",
        "preview_only": True,
        "saved": False,
        "discovered_count": len(sources),
        "sources": preview["profiles"],
        "errors": preview["errors"],
        "candidates": candidates,
        "graph": _relation_candidate_graph(candidates),
        "saved_graph": _relation_candidate_graph(_public_schema_relations()),
    }


@router.get("/schema-relations/graph")
def schema_relation_graph(request: Request):
    current_user(request)
    relations = _public_schema_relations()
    column_catalog = _public_column_catalog()
    return {
        "ok": True,
        "relations": relations,
        "column_catalog": column_catalog,
        "graph": _relation_candidate_graph(relations),
        "storage": "data/flow-data/schema_relations.json",
    }


@router.get("/resolve_term")
def resolve_term(request: Request, q: str = Query(..., min_length=1, max_length=200), limit: int = Query(5, ge=1, le=50)):
    current_user(request)
    return {"ok": True, "query": q, "candidates": resolve_term_to_columns(q, limit=limit)}


@router.post("/schema_doc/ai-draft", deprecated=True)
def schema_doc_ai_draft(req: SchemaDocAiDraftReq, request: Request):
    # M7: deprecated — ColumnDoc(`backend/core/flowi_units/`)와 중복. 새 정보는
    # ColumnDoc 또는 GET /api/agent/unit-ai/{key}/inspect로 확인/편집한다.
    _require_agent_wiki_admin(request)
    if not (req.body or "").strip():
        raise HTTPException(400, "body is required")
    known_relations = _schema_known_relation_ids(include_discovered=True)
    if req.hint_relation_id and req.hint_relation_id not in known_relations:
        known_relations.append(req.hint_relation_id)
    return kv.draft_schema_doc_metadata(
        req.body,
        hint_relation_id=req.hint_relation_id,
        hint_columns=req.hint_columns,
        known_relations=known_relations,
    )


@router.post("/schema_doc/ai-upsert", deprecated=True)
def schema_doc_ai_upsert(req: SchemaDocAiUpsertReq, request: Request):
    # M7: deprecated — schema_doc wiki(4개) 정리 후 ColumnDoc로 이관. 새 컬럼
    # 의미는 `backend/core/flowi_units/schema_columns.py`에 추가하거나 unit AI의
    # registry.py DataSourceRef.columns에 정의한다.
    me = _require_agent_wiki_admin(request)
    if req.wiki_doc:
        return kv.commit_schema_doc_draft(
            wiki_doc=req.wiki_doc,
            column_catalog_stubs=req.column_catalog_stubs,
            actor=me.get("username") or "admin",
        )
    if not (req.body or "").strip():
        raise HTTPException(400, "body or wiki_doc is required")
    known_relations = _schema_known_relation_ids(include_discovered=True)
    if req.hint_relation_id and req.hint_relation_id not in known_relations:
        known_relations.append(req.hint_relation_id)
    return kv.ai_upsert_schema_doc(
        body=req.body,
        hint_relation_id=req.hint_relation_id,
        hint_columns=req.hint_columns,
        actor=me.get("username") or "admin",
        known_relations=known_relations,
    )


@router.post("/schema_doc/scan_sources")
def schema_doc_scan_sources(req: SchemaDocScanSourcesReq, request: Request):
    me = _require_agent_wiki_admin(request)
    sources = _relation_discover_sources(max_sources=req.max_sources)
    if not sources:
        raise HTTPException(400, "no schema sources discovered")
    profiles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for src in sources:
        try:
            profiles.append(_relation_read_source(src, sample_rows=req.sample_rows))
        except HTTPException as exc:
            errors.append({
                "source": src.model_dump() if hasattr(src, "model_dump") else src.dict(),
                "status": exc.status_code,
                "detail": exc.detail,
            })
    stubs = _catalog_stubs_from_profiles(profiles)
    catalog = kv.merge_schema_column_catalog(stubs, actor=me.get("username") or "admin", sync_existing=True)
    return {
        "ok": True,
        "discovered_count": len(sources),
        "profile_count": len(profiles),
        "stub_count": len(stubs),
        "sources": profiles,
        "errors": errors,
        "catalog": catalog,
        "raw_sources_mutated": False,
    }


_SINGLE_FILE_PURPOSES = {"rulebook", "matching", "schema_doc", "lookup_table"}


def _single_file_source(src: SchemaRelationSource) -> SchemaRelationSource:
    source = src if isinstance(src, SchemaRelationSource) else SchemaRelationSource()
    source_type = str(source.source_type or "file").strip().lower()
    if source_type != "file":
        raise HTTPException(400, "single-file registration only accepts source_type=file")
    if not str(source.file or "").strip():
        raise HTTPException(400, "file is required")
    return SchemaRelationSource(
        source_type="file",
        root=source.root or "base_root",
        file=source.file,
        label=source.label or Path(str(source.file)).stem,
    )


def _registered_file_columns(req: SchemaSingleFileRegisterReq, profile: dict[str, Any]) -> list[str]:
    columns = [str(c or "").strip() for c in profile.get("columns") or [] if str(c or "").strip()]
    by_norm = {_relation_norm_col(c): c for c in columns}
    selected: list[str] = []
    for raw in [*(req.key_columns or []), *(req.output_columns or [])]:
        norm = _relation_norm_col(raw)
        col = by_norm.get(norm) or str(raw or "").strip()
        if col and col in columns and col not in selected:
            selected.append(col)
    if selected:
        return selected[:160]
    return columns[:80]


def _single_file_wiki_body(profile: dict[str, Any], purpose: str, key_columns: list[str], output_columns: list[str]) -> str:
    lines = [
        "## Source",
        f"- source_id: {profile.get('source_id') or ''}",
        f"- file: {profile.get('file') or Path(str(profile.get('resolved_path') or '')).name}",
        f"- purpose: {purpose}",
        f"- row_count: {profile.get('row_count') if profile.get('row_count') is not None else '-'}",
        f"- checksum: {profile.get('checksum') or '-'}",
        "",
        "## Approved Roles",
        "- key_columns: " + (", ".join(key_columns) if key_columns else "-"),
        "- output_columns: " + (", ".join(output_columns) if output_columns else "-"),
        "",
        "## Home Routing Contract",
        "- Home Flow-i may use this registered source for deterministic lookup only.",
        "- Raw file data is not modified by registration.",
        "- If runtime wiki/schema already has a matching doc_id, normal wiki save semantics preserve editability.",
    ]
    return "\n".join(lines) + "\n"


@router.post("/schema_doc/single-file/preview")
def schema_doc_single_file_preview(req: SchemaSingleFilePreviewReq, request: Request):
    current_user(request)
    source = _single_file_source(req.source)
    profile = _relation_read_source(source, sample_rows=req.sample_rows)
    return {
        "ok": True,
        "preview_only": True,
        "source": profile,
        "raw_sources_mutated": False,
    }


@router.post("/schema_doc/single-file/register")
def schema_doc_single_file_register(req: SchemaSingleFileRegisterReq, request: Request):
    me = _require_agent_wiki_admin(request)
    purpose = str(req.purpose or "lookup_table").strip().lower()
    if purpose not in _SINGLE_FILE_PURPOSES:
        raise HTTPException(400, f"purpose must be one of {', '.join(sorted(_SINGLE_FILE_PURPOSES))}")
    source = _single_file_source(req.source)
    profile = _relation_read_source(source, sample_rows=req.sample_rows)
    relation_id = _profile_relation_id(profile) or _safe_slug(Path(str(profile.get("file") or "")).stem, "single_file")
    selected_columns = _registered_file_columns(req, profile)
    key_columns = [c for c in selected_columns if _relation_norm_col(c) in {_relation_norm_col(x) for x in req.key_columns}]
    output_columns = [c for c in selected_columns if _relation_norm_col(c) in {_relation_norm_col(x) for x in req.output_columns}]
    if not key_columns and req.key_columns:
        raise HTTPException(400, "approved key_columns were not found in source")
    if not output_columns and req.output_columns:
        raise HTTPException(400, "approved output_columns were not found in source")
    dtypes = profile.get("dtypes") if isinstance(profile.get("dtypes"), dict) else {}
    samples = profile.get("sample_values") if isinstance(profile.get("sample_values"), dict) else {}
    role_by_norm = {_relation_norm_col(k): str(v or "").strip().lower() for k, v in (req.column_roles or {}).items()}
    key_norms = {_relation_norm_col(c) for c in key_columns}
    output_norms = {_relation_norm_col(c) for c in output_columns}
    source_id = str(profile.get("source_id") or "").strip()
    file_name = str(profile.get("file") or source.file or "").strip()
    now = _now_iso()
    stubs: list[dict[str, Any]] = []
    for col in selected_columns:
        norm = _relation_norm_col(col)
        role = role_by_norm.get(norm) or ("key" if norm in key_norms else "output" if norm in output_norms else "reference")
        canonical = _relation_canonical_col(col) or _schema_column_name(col)
        stubs.append({
            "relation_id": relation_id,
            "column": canonical,
            "raw_names": [col],
            "dtype": _relation_dtype_family(dtypes.get(col)),
            "canonical_alias": canonical,
            "unit": None,
            "fk": None,
            "sample_values": samples.get(col) or [],
            "source_id": source_id,
            "source_type": "file",
            "file_name": file_name,
            "source_file": file_name,
            "source_path": profile.get("resolved_path") or "",
            "purpose": purpose,
            "role": role,
            "column_role": role,
            "source_checksum": profile.get("checksum") or "",
            "source_row_count": profile.get("row_count"),
            "registered_at": now,
            "registered_by": me.get("username") or "admin",
            "approved_by": me.get("username") or "admin",
        })
    refs = [f"{relation_id}.{stub['column']}" for stub in stubs]
    title = req.title.strip() if req.title else f"{Path(file_name).name or relation_id} execution source"
    summary = req.summary.strip() if req.summary else f"{purpose} 단일 파일 {Path(file_name).name or relation_id}의 승인된 key/output column catalog"
    doc_id = req.doc_id.strip() if req.doc_id else f"single_file_{_safe_slug(relation_id)}_{purpose}"
    wiki_doc = {
        "doc_id": doc_id,
        "title": title,
        "summary": summary,
        "body": _single_file_wiki_body(profile, purpose, key_columns, output_columns),
        "tags": ["single_file", purpose, "execution_source", relation_id],
        "frontmatter": {
            "relation_id": relation_id,
            "column_refs": refs,
            "source_id": source_id,
            "source_ids": [source_id] if source_id else [],
            "source_file": file_name,
            "purpose": purpose,
            "row_count": profile.get("row_count"),
            "checksum": profile.get("checksum") or "",
            "key_columns": key_columns,
            "output_columns": output_columns,
        },
    }
    committed = kv.commit_schema_doc_draft(
        wiki_doc=wiki_doc,
        column_catalog_stubs=stubs,
        actor=me.get("username") or "admin",
    )
    return {
        "ok": True,
        "source": profile,
        "doc": committed.get("doc") or committed.get("wiki_doc"),
        "catalog": committed.get("catalog") or {},
        "graph_counts": committed.get("graph_counts") or {},
        "registered_columns": selected_columns,
        "raw_sources_mutated": False,
    }


@router.post("/schema-relations/save")
def schema_relation_save(req: SchemaRelationSaveReq, request: Request):
    me = _require_agent_schema_admin(request)
    candidates = [row for row in (req.candidates or []) if isinstance(row, dict)]
    if not candidates:
        raise HTTPException(400, "candidates are required")
    payload = _schema_relations_payload()
    existing = {
        str(row.get("relation_id") or _relation_candidate_id(row)): row
        for row in (payload.get("relations") or [])
        if isinstance(row, dict)
    }
    now = _now_iso()
    saved: list[dict[str, Any]] = []
    allowed_keys = {
        "relation_id", "left_source_id", "left_label", "left_source_type", "left_column", "left_dtype",
        "right_source_id", "right_label", "right_source_type", "right_column", "right_dtype",
        "canonical_key", "relation_type", "confidence", "evidence", "rationale", "left_sample", "right_sample", "status",
    }
    for candidate in candidates[:100]:
        row = {key: candidate.get(key) for key in allowed_keys if key in candidate}
        if not row.get("left_source_id") or not row.get("right_source_id") or not row.get("left_column") or not row.get("right_column"):
            continue
        row["left_column"] = str(row.get("left_column") or "").strip()
        row["right_column"] = str(row.get("right_column") or "").strip()
        row["canonical_key"] = str(row.get("canonical_key") or _relation_canonical_col(row["left_column"]) or _relation_norm_col(row["left_column"]))[:120]
        if not row["left_column"] or not row["right_column"]:
            continue
        row["relation_type"] = row.get("relation_type") or "join_key"
        row["relation_id"] = _relation_candidate_id(row)
        row["status"] = "confirmed"
        row["confirmed_by"] = me.get("username") or ""
        row["confirmed_at"] = now
        row["note"] = str(req.note or "")[:500]
        existing[str(row["relation_id"])] = row
        saved.append(row)
    if not saved:
        raise HTTPException(400, "no valid relation candidates")
    payload["relations"] = sorted(existing.values(), key=lambda r: str(r.get("confirmed_at") or ""), reverse=True)
    payload["updated_at"] = now
    payload["updated_by"] = me.get("username") or ""
    SCHEMA_RELATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_json(SCHEMA_RELATION_FILE, payload, indent=2)
    return {
        "ok": True,
        "saved_count": len(saved),
        "relations": payload["relations"],
        "graph": _relation_candidate_graph(payload["relations"]),
        "storage": "data/flow-data/schema_relations.json",
        "raw_sources_mutated": False,
    }


@router.post("/schema-relations/delete")
def schema_relation_delete(req: SchemaRelationDeleteReq, request: Request):
    me = _require_agent_schema_admin(request)
    relation_ids = {str(x or "").strip() for x in (req.relation_ids or []) if str(x or "").strip()}
    if not relation_ids:
        raise HTTPException(400, "relation_ids are required")
    payload = _schema_relations_payload()
    before = [row for row in (payload.get("relations") or []) if isinstance(row, dict)]
    kept = [row for row in before if str(row.get("relation_id") or "") not in relation_ids]
    removed = [row for row in before if str(row.get("relation_id") or "") in relation_ids]
    if not removed:
        raise HTTPException(404, "relation not found")
    now = _now_iso()
    payload["relations"] = kept
    payload["updated_at"] = now
    payload["updated_by"] = me.get("username") or ""
    deleted_log = payload.get("deleted_relations") if isinstance(payload.get("deleted_relations"), list) else []
    for row in removed:
        deleted_log.insert(0, {
            "relation_id": row.get("relation_id") or "",
            "left_source_id": row.get("left_source_id") or "",
            "left_column": row.get("left_column") or "",
            "right_source_id": row.get("right_source_id") or "",
            "right_column": row.get("right_column") or "",
            "deleted_by": me.get("username") or "",
            "deleted_at": now,
            "note": str(req.note or "")[:500],
        })
    payload["deleted_relations"] = deleted_log[:200]
    SCHEMA_RELATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_json(SCHEMA_RELATION_FILE, payload, indent=2)
    return {
        "ok": True,
        "deleted_count": len(removed),
        "relations": kept,
        "graph": _relation_candidate_graph(kept),
        "storage": "data/flow-data/schema_relations.json",
        "raw_sources_mutated": False,
    }


def _agent_feature_items() -> list[dict[str, Any]]:
    root = flowi_llm.FLOWI_AGENT_FEATURE_GUIDE_DIR
    rows: list[dict[str, Any]] = []
    try:
        files = sorted(root.glob("*.md")) if root.is_dir() else []
    except Exception:
        files = []
    for fp in files:
        try:
            text = fp.read_text(encoding="utf-8").strip()
        except Exception:
            text = ""
        title = fp.stem
        first = next((line.strip("# ").strip() for line in text.splitlines() if line.strip()), title)
        rows.append({
            "id": f"agent_feature:{fp.stem}",
            "kind": "agent_features",
            "title": first or title,
            "summary": _summary_text(text, limit=260),
            "content": text[:4000],
            "tags": [fp.stem],
            "source": str(fp.name),
            "related_functions": [fp.stem],
        })
    return rows


@router.get("/knowledge-inventory")
def knowledge_inventory(
    request: Request,
    q: str = Query("", max_length=200),
    tag: str = Query("", max_length=120),
    kind: str = Query("", max_length=80),
):
    me = current_user(request)
    needle = str(q or "").strip().lower()
    tag_needle = str(tag or "").strip().lower()
    kind_filter = str(kind or "").strip()
    view = semi.rag_knowledge_view(me.get("username") or "", me.get("role") or "user", q=q, limit=300)
    promoted = _promoted_ids()
    items: list[dict[str, Any]] = []

    def push(kind_name: str, row: dict[str, Any], *, title: str, summary: str, content: str = "", tags: list[Any] | None = None, related: list[str] | None = None):
        rid = str(row.get("id") or row.get("case_id") or f"{kind_name}:{len(items) + 1}")
        item = {
            "id": f"{kind_name}:{rid}" if ":" not in rid else rid,
            "source_id": rid,
            "kind": kind_name,
            "title": title or rid,
            "summary": summary,
            "content": content or summary,
            "tags": [str(x) for x in (tags or []) if str(x).strip()],
            "source": row.get("source") or row.get("source_kind") or "",
            "related_functions": related or [],
            "promoted": (f"{kind_name}:{rid}" if ":" not in rid else rid) in promoted,
            "raw": row,
        }
        if (not kind_filter or kind_filter == "all" or item["kind"] == kind_filter) and _hit(item, needle, tag_needle):
            items.append(item)

    for row in view.get("knowledge_cards") or []:
        push(
            "knowledge_cards",
            row,
            title=row.get("title") or row.get("id") or "",
            summary=_summary_text(row.get("electrical_mechanism"), row.get("recommended_checks")),
            content=json.dumps(row, ensure_ascii=False, default=str),
            tags=_listify(row.get("symptom_items")) + _listify(row.get("module_tags")),
            related=["search_knowledge_cards", "run_semiconductor_diagnosis"],
        )
    for row in view.get("causal_edges") or []:
        push(
            "causal_edges",
            {**row, "id": f"{row.get('source')}->{row.get('target')}:{row.get('relation')}"},
            title=f"{row.get('source')} -> {row.get('target')}",
            summary=_summary_text(row.get("relation"), row.get("evidence")),
            content=json.dumps(row, ensure_ascii=False, default=str),
            tags=[row.get("source"), row.get("target"), row.get("module")],
            related=["traverse_causal_graph"],
        )
    for row in semi.all_historical_cases():
        push(
            "similar_cases",
            row,
            title=row.get("title") or row.get("case_id") or "",
            summary=_summary_text(row.get("evidence"), row.get("resolution"), row.get("outcome")),
            content=json.dumps(row, ensure_ascii=False, default=str),
            tags=_listify(row.get("tags")) + _listify(row.get("symptoms")),
            related=["find_similar_cases"],
        )
    for row in view.get("runtime_knowledge") or []:
        out_kind = "promoted_docs" if row.get("source") == "agent_admin_tools_knowledge_ingest" else "custom_knowledge"
        push(
            out_kind,
            row,
            title=row.get("display_title") or row.get("title") or row.get("id") or "",
            summary=_summary_text(row.get("display_content") or row.get("content"), row.get("rag_effect")),
            content=row.get("display_content") or row.get("content") or "",
            tags=_listify(row.get("tags")) + _listify(row.get("items")) + _listify(row.get("key_terms")),
            related=["runtime_custom_knowledge", "flowi_rag_update"],
        )
    items.extend([
        item for item in _agent_feature_items()
        if (not kind_filter or kind_filter == "all" or item["kind"] == kind_filter) and _hit(item, needle, tag_needle)
    ])
    for row in flowi_llm._flowi_promoted_knowledge_items(limit=200):
        item = {
            "id": row.get("id") or "",
            "source_id": row.get("id") or "",
            "kind": "promoted_docs",
            "title": row.get("title") or "",
            "summary": row.get("summary") or "",
            "content": row.get("summary") or "",
            "tags": row.get("tags") or [],
            "source": "promoted_knowledge",
            "related_functions": ["_flowi_system_prompt"],
            "promoted": True,
            "raw": row,
        }
        if (not kind_filter or kind_filter == "all" or item["kind"] == kind_filter) and _hit(item, needle, tag_needle):
            items.append(item)

    counts = Counter(item["kind"] for item in items)
    tags = sorted({str(tag) for item in items for tag in _listify(item.get("tags")) if str(tag).strip()})[:80]
    return {
        "ok": True,
        "items": items[:500],
        "counts": dict(counts),
        "kinds": ["knowledge_cards", "causal_edges", "similar_cases", "custom_knowledge", "agent_features", "promoted_docs"],
        "tags": tags,
        "query": q,
        "tag": tag,
    }


@router.post("/knowledge-inventory/promote")
def promote_knowledge(req: PromoteReq, request: Request):
    me = _require_agent_admin(request)
    payload = _promoted_payload()
    items = [dict(item) for item in payload.get("items") or [] if isinstance(item, dict)]
    rid = str(req.id or "").strip()
    if not rid:
        raise HTTPException(400, "id is required")
    items = [item for item in items if str(item.get("id") or "") != rid]
    if req.promoted:
        summary = _summary_text(req.summary, req.content, limit=220)
        items.insert(0, {
            "id": rid,
            "kind": req.kind or "custom_knowledge",
            "title": (req.title or rid)[:180],
            "summary": summary[:220],
            "tags": req.tags[:20],
            "source": req.source or "knowledge_inventory",
            "promoted": True,
            "updated_by": me.get("username") or "admin",
            "updated_at": _now_iso(),
        })
    payload["items"] = items[:200]
    save_json(flowi_llm.FLOWI_PROMOTED_KNOWLEDGE_FILE, payload, indent=2)
    return {"ok": True, "promoted": req.promoted, "items": flowi_llm._flowi_promoted_knowledge_items(limit=200)}


@router.get("/recent-rag")
def recent_rag(
    request: Request,
    limit: int = Query(50, ge=1, le=50),
    user: str = Query("", max_length=120),
):
    me = current_user(request)
    username = me.get("username") or "user"
    requested_user = str(user or "").strip()
    target_user = requested_user if (me.get("role") == "admin" and requested_user) else username
    rows = _activity_rows(max(300, limit * 8))
    traces: list[dict[str, Any]] = []
    for rec in sorted(rows, key=lambda r: str(r.get("timestamp") or ""), reverse=True):
        if rec.get("username") != target_user:
            continue
        fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        prompt = str(fields.get("prompt") or fields.get("prompt_excerpt") or "")
        selected = fields.get("selected_function") or fields.get("action") or fields.get("intent") or rec.get("event") or ""
        retrieved = fields.get("retrieved_ids") or rec.get("retrieved_ids") or []
        if not isinstance(retrieved, list):
            retrieved = [retrieved] if retrieved else []
        traces.append({
            "timestamp": rec.get("timestamp") or "",
            "user": rec.get("username") or "",
            "event": rec.get("event") or "",
            "prompt": prompt,
            "selected_function": selected,
            "retrieved_ids": [str(x) for x in retrieved if str(x).strip()],
            "system_knowledge_ids": fields.get("system_knowledge_ids") or rec.get("system_knowledge_ids") or [],
            "score": fields.get("retrieval_score") or rec.get("retrieval_score"),
            "elapsed_ms": fields.get("elapsed_ms") or rec.get("elapsed_ms"),
            "result_type": fields.get("result_status") or rec.get("result_status") or ("missing" if fields.get("arguments_choices") else "success"),
        })
        if len(traces) >= limit:
            break
    return {"ok": True, "limit": limit, "user": target_user, "traces": traces}


def _prompt_history_user_roles() -> dict[str, str]:
    try:
        from routers.auth import read_users

        return {
            str(row.get("username") or ""): str(row.get("role") or "user")
            for row in read_users()
            if str(row.get("username") or "").strip()
        }
    except Exception:
        return {}


@router.get("/prompt-history")
def prompt_history(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    user: str = Query("", max_length=120),
    scope: str = Query("mine", max_length=20),
):
    me = current_user(request)
    username = me.get("username") or "user"
    requested_user = str(user or "").strip()
    admin_scope = me.get("role") == "admin" and str(scope or "").strip().lower() in {"all", "team", "users"}
    target_user = requested_user if (me.get("role") == "admin" and requested_user) else ("" if admin_scope else username)
    role_by_user = _prompt_history_user_roles()
    rows = _activity_rows(max(300, limit * 8))
    history: list[dict[str, Any]] = []
    for rec in sorted(rows, key=lambda r: str(r.get("timestamp") or ""), reverse=True):
        rec_user = str(rec.get("username") or "")
        if target_user and rec_user != target_user:
            continue
        fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
        prompt = str(fields.get("prompt") or fields.get("prompt_excerpt") or "").strip()
        if not prompt:
            continue
        status = fields.get("result_status") or rec.get("result_status") or ""
        if not status:
            status = "blocked" if fields.get("blocked") else "done"
        missing = fields.get("missing") or fields.get("missing_fields") or []
        if not isinstance(missing, list):
            missing = [missing] if missing else []
        actor_role = str(fields.get("user_role") or fields.get("role") or rec.get("role") or role_by_user.get(rec_user) or "").strip()
        if not actor_role and rec_user == username:
            actor_role = str(me.get("role") or "")
        if not actor_role:
            actor_role = "user"
        history.append({
            "id": f"{rec.get('timestamp') or ''}:{len(history)}",
            "timestamp": rec.get("timestamp") or "",
            "ts": rec.get("timestamp") or "",
            "user": rec_user,
            "actor_role": actor_role,
            "actor_type": "admin" if actor_role == "admin" else "user",
            "event": rec.get("event") or "",
            "prompt": prompt,
            "feature": fields.get("feature") or "",
            "intent": fields.get("intent") or "",
            "action": fields.get("selected_function") or fields.get("action") or fields.get("intent") or rec.get("event") or "",
            "status": status,
            "missing": [str(x) for x in missing if str(x).strip()],
            "answer": fields.get("answer") or fields.get("answer_excerpt") or "",
            "answer_excerpt": str(fields.get("answer") or fields.get("answer_excerpt") or "")[:800],
            "elapsed_ms": fields.get("elapsed_ms") or rec.get("elapsed_ms"),
            "source_ai": fields.get("source_ai") or "",
            "client_run_id": fields.get("client_run_id") or "",
        })
        if len(history) >= limit:
            break
    return {"ok": True, "limit": limit, "user": target_user or "all", "scope": "all" if admin_scope and not requested_user else "mine", "rows": history}


_OVERVIEW_KIND_ALIASES = {
    "semantic": {"semantic_proposal", "semantic_change"},
    "semantic_proposal": {"semantic_proposal"},
    "proposal": {"semantic_proposal"},
    "semantic_change": {"semantic_change"},
    "wiki": {"wiki_page", "wiki_source"},
    "wiki_page": {"wiki_page"},
    "agent_wiki": {"wiki_page"},
    "schema_doc": {"wiki_page"},
    "wiki_source": {"wiki_source"},
    "source": {"wiki_source"},
    "prompt": {"prompt_history"},
    "prompt_history": {"prompt_history"},
    "trace": {"prompt_history"},
    "knowledge_event": {"knowledge_event"},
    "event": {"knowledge_event"},
}


def _overview_query_hit(row: dict[str, Any], q: str) -> bool:
    needle = str(q or "").strip().lower()
    if not needle:
        return True
    return needle in json.dumps(row, ensure_ascii=False, default=str).lower()


def _overview_kind_allowed(filter_kind: str, overview_kind: str, row_kind: str = "") -> bool:
    kind = str(filter_kind or "").strip()
    if not kind or kind == "all":
        return True
    allowed = _OVERVIEW_KIND_ALIASES.get(kind, {kind})
    return overview_kind in allowed or row_kind == kind


def _overview_timestamp(row: dict[str, Any]) -> str:
    for key in ("timestamp", "updated_at", "created_at", "ts", "changed_at"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    return str(payload.get("changed_at") or "").strip()


def _overview_item(
    *,
    overview_kind: str,
    row: dict[str, Any],
    title: str = "",
    summary: str = "",
    source: str = "",
    status: str = "",
    row_kind: str = "",
) -> dict[str, Any]:
    raw_kind = row_kind or str(row.get("kind") or row.get("event_type") or row.get("source_type") or "")
    return {
        "id": str(row.get("id") or row.get("doc_id") or row.get("source_id") or row.get("event_id") or row.get("key") or ""),
        "kind": overview_kind,
        "row_kind": raw_kind,
        "title": str(title or row.get("title") or row.get("term") or row.get("prompt") or row.get("action") or raw_kind or overview_kind),
        "summary": _summary_text(summary or row.get("summary") or row.get("content_preview") or row.get("answer_excerpt") or row.get("rationale") or "", limit=280),
        "timestamp": _overview_timestamp(row),
        "source": str(source or row.get("source") or row.get("source_type") or row.get("event") or ""),
        "status": str(status or row.get("status") or row.get("result_type") or ""),
        "tags": [str(x) for x in _listify(row.get("tags")) if str(x).strip()][:12],
        "raw": row,
    }


def _filter_overview_rows(rows: list[dict[str, Any]], q: str, kind: str, overview_kind: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        row_kind = str(row.get("kind") or row.get("event_type") or row.get("source_type") or "")
        if not _overview_kind_allowed(kind, overview_kind, row_kind):
            continue
        if not _overview_query_hit(row, q):
            continue
        out.append(row)
    return out


@router.get("/knowledge/overview")
def agent_knowledge_overview(
    request: Request,
    q: str = Query("", max_length=200),
    kind: str = Query("", max_length=80),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    current_user(request)
    q_text = str(q or "").strip()
    kind_filter = str(kind or "").strip()
    read_limit = max(limit * 4, 100)

    inventory_kind = "" if kind_filter in _OVERVIEW_KIND_ALIASES else kind_filter
    inventory = knowledge_inventory(request, q=q_text, tag="", kind=inventory_kind)
    inventory_items = [
        item for item in (inventory.get("items") or [])
        if isinstance(item, dict) and _overview_kind_allowed(kind_filter, "knowledge_inventory", str(item.get("kind") or ""))
    ]

    proposals = _filter_overview_rows(_learn_list_proposals(status="", limit=500), q_text, kind_filter, "semantic_proposal")
    pending_proposals = [row for row in proposals if str(row.get("status") or "") == "pending"]
    semantic_changes = _filter_overview_rows(_lex_list_changes(limit=read_limit), q_text, kind_filter, "semantic_change")
    wiki_pages = _filter_overview_rows(kv.list_agent_wiki_pages(q=q_text, limit=read_limit), q_text, kind_filter, "wiki_page")
    wiki_sources = _filter_overview_rows(kv.list_agent_wiki_sources(q=q_text, source_type="", limit=read_limit), q_text, kind_filter, "wiki_source")
    knowledge_events = _filter_overview_rows(kv.list_events(limit=read_limit, q=q_text), q_text, kind_filter, "knowledge_event")
    prompt_rows = prompt_history(request, limit=min(100, read_limit), user="").get("rows") or []
    recent_prompt_history = _filter_overview_rows(
        [row for row in prompt_rows if isinstance(row, dict)],
        q_text,
        kind_filter,
        "prompt_history",
    )

    recent_items: list[dict[str, Any]] = []
    recent_items.extend(
        _overview_item(
            overview_kind="knowledge_inventory",
            row=item,
            title=str(item.get("title") or ""),
            summary=str(item.get("summary") or ""),
            source=str(item.get("source") or ""),
            status="promoted" if item.get("promoted") else "",
            row_kind=str(item.get("kind") or ""),
        )
        for item in inventory_items[:limit]
    )
    recent_items.extend(
        _overview_item(
            overview_kind="semantic_proposal",
            row=row,
            title=str(row.get("term") or ""),
            summary=str(row.get("rationale") or ""),
            source=str((row.get("origin") or {}).get("kind") if isinstance(row.get("origin"), dict) else ""),
            row_kind=str(row.get("category") or ""),
        )
        for row in proposals[:limit]
    )
    recent_items.extend(
        _overview_item(overview_kind="wiki_page", row=row, row_kind=str(row.get("kind") or ""))
        for row in wiki_pages[:limit]
    )
    recent_items.extend(
        _overview_item(overview_kind="wiki_source", row=row, row_kind=str(row.get("source_type") or ""))
        for row in wiki_sources[:limit]
    )
    recent_items.extend(
        _overview_item(
            overview_kind="prompt_history",
            row=row,
            title=str(row.get("prompt") or ""),
            summary=str(row.get("answer_excerpt") or ""),
            source=str(row.get("action") or row.get("feature") or ""),
            status=str(row.get("status") or ""),
            row_kind=str(row.get("feature") or row.get("intent") or ""),
        )
        for row in recent_prompt_history[:limit]
    )
    recent_items.extend(
        _overview_item(overview_kind="knowledge_event", row=row, row_kind=str(row.get("event_type") or ""))
        for row in knowledge_events[:limit]
    )
    recent_items.extend(
        _overview_item(
            overview_kind="semantic_change",
            row=row,
            title=f"{row.get('scope') or ''}:{row.get('key') or ''}".strip(":"),
            summary=_summary_text(row.get("before"), row.get("after")),
            source=str(row.get("by") or ""),
            row_kind=str(row.get("scope") or ""),
        )
        for row in semantic_changes[:limit]
    )
    recent_items.sort(key=lambda row: str(row.get("timestamp") or ""), reverse=True)

    inventory_counts = inventory.get("counts") if isinstance(inventory.get("counts"), dict) else {}
    counts = {
        "knowledge_inventory": len(inventory_items),
        "semantic_proposals": len(proposals),
        "pending_semantic_proposals": len(pending_proposals),
        "semantic_changes": len(semantic_changes),
        "wiki_pages": len(wiki_pages),
        "wiki_sources": len(wiki_sources),
        "prompt_history": len(recent_prompt_history),
        "knowledge_events": len(knowledge_events),
        "recent_items": len(recent_items[:limit]),
        "inventory_by_kind": inventory_counts,
    }

    return {
        "ok": True,
        "query": q_text,
        "kind": kind_filter,
        "limit": limit,
        "counts": counts,
        "recent_items": recent_items[:limit],
        "pending_semantic_proposals": pending_proposals[:limit],
        "recent_wiki_pages": wiki_pages[:limit],
        "recent_wiki_sources": wiki_sources[:limit],
        "recent_prompt_history": recent_prompt_history[:limit],
        "recent_knowledge_events": knowledge_events[:limit],
        "recent_semantic_changes": semantic_changes[:limit],
    }


@router.get("/wiki/sources")
def agent_wiki_sources(
    request: Request,
    q: str = Query("", max_length=200),
    source_type: str = Query("", max_length=80),
    limit: int = Query(100, ge=1, le=1000),
):
    current_user(request)
    return {"ok": True, "sources": kv.list_agent_wiki_sources(q=q, source_type=source_type, limit=limit)}


@router.post("/wiki/sources")
def agent_wiki_create_source(req: AgentWikiSourceReq, request: Request):
    me = _require_agent_wiki_admin(request)
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    data["actor"] = me.get("username") or "admin"
    try:
        source = kv.register_agent_wiki_source(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "source": source}


@router.get("/wiki/source")
def agent_wiki_source(request: Request, source_id: str = Query(..., min_length=1)):
    current_user(request)
    source = kv.get_agent_wiki_source(source_id)
    if not source:
        raise HTTPException(404, "Agent Wiki source not found")
    return {"ok": True, "source": source}


@router.post("/wiki/ingest/preview")
def agent_wiki_ingest_preview(req: AgentWikiIngestReq, request: Request):
    current_user(request)
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    try:
        preview = kv.preview_agent_wiki_ingest(data)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "preview": preview}


@router.post("/wiki/ingest/commit")
def agent_wiki_ingest_commit(req: AgentWikiIngestReq, request: Request):
    me = _require_agent_wiki_admin(request)
    data = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    data["actor"] = me.get("username") or "admin"
    try:
        result = kv.commit_agent_wiki_ingest(data)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}


@router.get("/wiki/pages")
def agent_wiki_pages(
    request: Request,
    q: str = Query("", max_length=200),
    limit: int = Query(200, ge=1, le=1000),
):
    current_user(request)
    return {"ok": True, "pages": kv.list_agent_wiki_pages(q=q, limit=limit)}


@router.get("/wiki/page")
def agent_wiki_page(request: Request, doc_id: str = Query(..., min_length=1)):
    current_user(request)
    doc = kv.get_doc(doc_id)
    if not doc:
        raise HTTPException(404, "Knowledge Vault page not found")
    return {"ok": True, "page": doc}


@router.post("/wiki/page/save")
def agent_wiki_page_save(req: AgentWikiPageSaveReq, request: Request):
    me = _require_agent_wiki_admin(request)
    doc_id = _safe_slug(req.doc_id, "wiki_page")
    if not doc_id:
        raise HTTPException(400, "doc_id required")
    existing = kv.get_doc(doc_id) or {}
    kind = str(req.kind or existing.get("kind") or "agent_wiki").strip()
    if kind not in _AGENT_WIKI_PAGE_KINDS:
        kind = str(existing.get("kind") or "agent_wiki")
    title = str(req.title or existing.get("title") or doc_id).strip()
    if not title:
        raise HTTPException(400, "title required")
    entity_raw = existing.get("entity") if isinstance(existing.get("entity"), dict) else {}
    frontmatter = _custom_wiki_frontmatter(existing.get("frontmatter"), req.frontmatter)
    doc = KnowledgeDoc(
        doc_id=doc_id,
        kind=kind,
        title=title[:220],
        summary=str(req.summary or "").strip()[:800],
        body=_strip_generated_h1(req.body, title, str(existing.get("title") or "")),
        actor=me.get("username") or "admin",
        created_at=str(existing.get("created_at") or ""),
        entity=FlowEntityKey(
            product=str(entity_raw.get("product") or ""),
            root_lot_id=str(entity_raw.get("root_lot_id") or ""),
            wafer_id=str(entity_raw.get("wafer_id") or ""),
        ),
        tags=req.tags,
        source_event_ids=existing.get("source_event_ids") if isinstance(existing.get("source_event_ids"), list) else [],
        frontmatter=frontmatter,
    )
    saved = kv.upsert_doc(doc)
    kv.append_wiki_log({
        "action": "page_save",
        "actor": me.get("username") or "admin",
        "doc_id": saved.get("doc_id") or doc_id,
        "title": saved.get("title") or title,
        "message": f"Saved wiki page {saved.get('doc_id') or doc_id}",
        "meta": {"path": saved.get("path") or "", "kind": saved.get("kind") or kind},
    })
    graph = kv.rebuild_graph()
    return {"ok": True, "page": saved, "doc": saved, "graph_counts": graph.get("counts") or {}}


@router.post("/wiki/page/delete")
def agent_wiki_page_delete(
    request: Request,
    req: AgentWikiPageDeleteReq | None = None,
    doc_id: str = Query("", max_length=200),
):
    me = _require_agent_wiki_admin(request)
    query_doc_id = doc_id if isinstance(doc_id, str) else ""
    target = query_doc_id or (req.doc_id if req else "")
    if not str(target or "").strip():
        raise HTTPException(400, "doc_id required")
    result = kv.delete_doc(str(target), actor=me.get("username") or "admin")
    if not result.get("deleted"):
        raise HTTPException(404, result.get("error") or "Knowledge Vault page not found")
    graph = kv.rebuild_graph()
    return {"ok": True, **result, "graph_counts": graph.get("counts") or {}}


@router.get("/wiki/search")
def agent_wiki_search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(30, ge=1, le=100),
):
    current_user(request)
    return {"ok": True, "query": q, "results": kv.search_agent_wiki(q=q, limit=limit)}


@router.get("/wiki/log")
def agent_wiki_log(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    action: str = Query("", max_length=80),
):
    current_user(request)
    return {"ok": True, "logs": kv.list_wiki_log(limit=limit, action=action)}


@router.post("/wiki/lint")
def agent_wiki_lint(request: Request):
    me = _require_agent_wiki_admin(request)
    result = kv.lint_agent_wiki()
    kv.append_wiki_log({
        "action": "lint",
        "actor": me.get("username") or "admin",
        "message": "Ran Agent Wiki lint",
        "meta": result.get("counts") or {},
    })
    return result


@router.get("/item-rules")
def item_rules(
    request: Request,
    source_type: str = Query("", max_length=40),
    product: str = Query("", max_length=80),
):
    current_user(request)
    source_filter = str(source_type or "").strip().upper()
    product_filter = str(product or "").strip().upper()
    rows: list[dict[str, Any]] = []
    for item in semi.ITEM_MASTER:
        st = str(item.get("source_type") or "").upper()
        if source_filter and st != source_filter:
            continue
        module = str(item.get("module") or "")
        raw_names = [str(x) for x in _listify(item.get("raw_names"))]
        rows.append({
            "item": item.get("canonical_item_id") or "",
            "display_name": item.get("display_name") or "",
            "matching_step_id": item.get("step_id") or item.get("func_step") or module,
            "matching_knob": item.get("knob") or ("KNOB:" + module if module else ""),
            "matching_mask": item.get("mask") or ("MASK:" + item.get("layer") if item.get("layer") else ""),
            "source_type": item.get("source_type") or "",
            "product": product_filter or "common",
            "unit": item.get("unit") or "",
            "raw_names": raw_names[:8],
            "rule": item.get("meaning") or "",
            "source": "semiconductor_knowledge.ITEM_MASTER",
        })
    return {
        "ok": True,
        "source_type": source_type,
        "product": product,
        "rules": rows,
        "counts": {"rules": len(rows), "items": len(semi.ITEM_MASTER)},
    }


@router.get("/admin-tools/status")
def admin_tools_status(request: Request):
    _require_agent_admin(request)
    state = load_json(AGENT_ADMIN_STATE_FILE, {})
    backups = []
    try:
        backups = sorted((p.name for p in AGENT_BACKUP_DIR.glob("*.json")), reverse=True)[:20] if AGENT_BACKUP_DIR.is_dir() else []
    except Exception:
        backups = []
    return {
        "ok": True,
        "matching_applications": len((state or {}).get("matching_applications") or []),
        "rulebook_applications": len((state or {}).get("rulebook_applications") or []),
        "backups": backups,
    }


def _ml_table_paths(product: str = "") -> list[Path]:
    roots = [PATHS.db_root]
    seen: set[Path] = set()
    paths: list[Path] = []
    product_u = str(product or "").strip().upper()
    for root in roots:
        try:
            candidates = list(root.glob(f"ML_TABLE_{product_u}.parquet")) if product_u else list(root.glob("ML_TABLE_*.parquet"))
        except Exception:
            candidates = []
        for fp in candidates:
            if fp not in seen:
                seen.add(fp)
                paths.append(fp)
    return paths


def _candidate_reason(target: str, col: str) -> tuple[int, str]:
    col_l = col.lower()
    aliases = {
        "product": ["product", "prod"],
        "root_lot_id": ["root_lot", "root", "lot_id", "lot"],
        "fab_lot_id": ["fab_lot", "fab", "lot_id"],
        "wafer_id": ["wafer", "wf", "slot"],
        "step": ["step", "func_step", "operation"],
        "knob": ["knob", "ppid", "recipe"],
        "mask": ["mask", "reticle"],
    }.get(target, [target])
    for idx, alias in enumerate(aliases):
        if alias in col_l:
            return 100 - idx * 8, f"{alias} token matches {target}"
    return 0, ""


@router.post("/admin-tools/matching/suggest")
def matching_suggest(req: MatchingSuggestReq, request: Request):
    _require_agent_admin(request)
    paths = _ml_table_paths(req.product)
    columns: list[str] = []
    source_file = ""
    for fp in paths:
        try:
            columns = list(pl.scan_parquet(fp).collect_schema().names())
            source_file = fp.name
            if columns:
                break
        except Exception:
            continue
    targets = ["product", "root_lot_id", "fab_lot_id", "wafer_id", "step", "knob", "mask"]
    candidates: list[dict[str, Any]] = []
    for target in targets:
        ranked = []
        for col in columns:
            score, reason = _candidate_reason(target, col)
            if score:
                ranked.append((score, col, reason))
        ranked.sort(reverse=True)
        if ranked:
            score, col, reason = ranked[0]
            candidates.append({"target": target, "source_column": col, "score": score, "reason": reason})
        else:
            candidates.append({"target": target, "source_column": "", "score": 0, "reason": "matching column not found"})
    return {
        "ok": True,
        "product": req.product,
        "source_table": req.source_table,
        "source_file": source_file,
        "columns": columns[:120],
        "candidates": candidates,
    }


@router.post("/admin-tools/matching/apply")
def matching_apply(req: MatchingApplyReq, request: Request):
    me = _require_agent_admin(request)
    state = load_json(AGENT_ADMIN_STATE_FILE, {})
    backup = _backup_state("matching", state)
    state.setdefault("matching_applications", []).insert(0, {
        "id": "MATCH-" + uuid.uuid4().hex[:10].upper(),
        "created_at": _now_iso(),
        "created_by": me.get("username") or "admin",
        "product": req.product,
        "source_table": req.source_table,
        "candidates": req.candidates,
        "note": req.note,
    })
    save_json(AGENT_ADMIN_STATE_FILE, state, indent=2)
    return {"ok": True, "backup": backup, "applied": state["matching_applications"][0]}


@router.post("/admin-tools/rulebook/suggest")
def rulebook_suggest(req: RulebookSuggestReq, request: Request):
    _require_agent_admin(request)
    needle = " ".join([req.knob, req.mask, req.change_summary]).lower()
    candidates: list[dict[str, Any]] = []
    for item in semi.ITEM_MASTER:
        text = json.dumps(item, ensure_ascii=False, default=str).lower()
        if needle.strip() and not any(tok and tok in text for tok in re.split(r"\s+", needle)):
            continue
        module = item.get("module") or ""
        candidates.append({
            "affected_item": item.get("canonical_item_id") or "",
            "affected_step": item.get("step_id") or item.get("func_step") or module,
            "knob": req.knob,
            "mask": req.mask,
            "reason": _summary_text(item.get("meaning"), item.get("aliases"), limit=180),
        })
        if len(candidates) >= 30:
            break
    if not candidates:
        seed_items = semi.ITEM_MASTER[:10] or [{"canonical_item_id": "", "module": ""}]
        for item in seed_items:
            candidates.append({
                "affected_item": item.get("canonical_item_id") or "",
                "affected_step": item.get("module") or "",
                "knob": req.knob,
                "mask": req.mask,
                "reason": "item master match not found; review manually before apply",
            })
    return {"ok": True, "product": req.product, "candidates": candidates}


@router.post("/admin-tools/rulebook/apply")
def rulebook_apply(req: RulebookApplyReq, request: Request):
    me = _require_agent_admin(request)
    state = load_json(AGENT_ADMIN_STATE_FILE, {})
    backup = _backup_state("rulebook", state)
    state.setdefault("rulebook_applications", []).insert(0, {
        "id": "RULE-" + uuid.uuid4().hex[:10].upper(),
        "created_at": _now_iso(),
        "created_by": me.get("username") or "admin",
        "product": req.product,
        "knob": req.knob,
        "mask": req.mask,
        "change_summary": req.change_summary,
        "candidates": req.candidates,
    })
    save_json(AGENT_ADMIN_STATE_FILE, state, indent=2)
    return {"ok": True, "backup": backup, "applied": state["rulebook_applications"][0]}


def _chunk_text(text: str, target: int = 1500) -> list[dict[str, Any]]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    chunks: list[dict[str, Any]] = []
    pos = 0
    while pos < len(cleaned):
        end = min(len(cleaned), pos + target + 200)
        if end < len(cleaned):
            window = cleaned[pos + target - 200:end]
            split_at = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("。"), window.rfind("다."))
            if split_at > 0:
                end = pos + target - 200 + split_at + 1
        chunk = cleaned[pos:end].strip()
        if chunk:
            chunks.append({"idx": len(chunks) + 1, "text": chunk, "chars": len(chunk)})
        pos = max(end, pos + 1)
    return chunks


@router.post("/admin-tools/knowledge/ingest")
def knowledge_ingest(req: KnowledgeIngestReq, request: Request):
    me = _require_agent_admin(request)
    content = str(req.content or "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    title = (req.title or req.file_name or "Agent ingested knowledge").strip()
    chunks = _chunk_text(content)
    doc_id = "AGDOC-" + uuid.uuid4().hex[:10].upper()
    AGENT_KNOWLEDGE_RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_name = f"{doc_id}_{_safe_slug(req.file_name or title)}.txt"
    raw_path = AGENT_KNOWLEDGE_RAW_DIR / raw_name
    raw_path.write_text(content, encoding="utf-8")
    structured = {
        "schema_type": "agent_ingested_document",
        "chunk_target_chars": 1500,
        "chunk_tolerance_chars": 200,
        "chunk_count": len(chunks),
        "chunks": chunks,
        "raw_path": str(raw_path),
        "file_name": req.file_name,
        "review_status": "admin_added_public",
    }
    saved = semi.add_custom_knowledge({
        "kind": "document",
        "visibility": "public",
        "title": title,
        "display_title": title,
        "source": "agent_admin_tools_knowledge_ingest",
        "document_type": req.doc_type,
        "tags": req.tags,
        "content": content,
        "display_content": content,
        "structured_json": structured,
    }, username=me.get("username") or "admin", role="admin")
    return {"ok": True, "id": doc_id, "saved": saved.get("row"), "structured": structured}


@router.get("/admin-tools/knowledge/list")
def knowledge_list(request: Request):
    _require_agent_admin(request)
    rows = []
    for row in semi.custom_knowledge_rows("", "admin"):
        structured = row.get("structured_json") if isinstance(row.get("structured_json"), dict) else {}
        if row.get("source") != "agent_admin_tools_knowledge_ingest" and structured.get("schema_type") != "agent_ingested_document":
            continue
        rows.append({
            "id": row.get("id") or "",
            "created_at": row.get("created_at") or "",
            "title": row.get("display_title") or row.get("title") or "",
            "doc_type": row.get("document_type") or "",
            "tags": row.get("tags") or [],
            "chunk_count": structured.get("chunk_count") or 0,
            "file_name": structured.get("file_name") or "",
        })
    return {"ok": True, "rows": rows[:200]}


# ─── Unit AI catalog / inspect (M3) ───────────────────────────────────
# Read-only views of the 11 Feature-level Unit AIs registered in
# core.flowi_units. The Agent tab renders these in a left list + right
# detail layout. Editing endpoints arrive in M4.

def _column_doc_dict(c) -> dict[str, Any]:
    return {
        "name": c.name,
        "meaning": c.meaning,
        "unit": c.unit,
        "sample_values": list(c.sample_values),
        "wiki_doc_id": c.wiki_doc_id,
    }


def _data_source_dict(ds) -> dict[str, Any]:
    return {
        "kind": ds.kind,
        "path": ds.path,
        "description": ds.description,
        "columns": [_column_doc_dict(c) for c in ds.columns],
    }


def _semantic_bindings_dict(sb) -> dict[str, Any]:
    return {
        "relation_ids": list(sb.relation_ids),
        "column_catalog_keys": list(sb.column_catalog_keys),
        "graph_node_ids": list(sb.graph_node_ids),
        "wiki_doc_ids": list(sb.wiki_doc_ids),
    }


def _handler_entry_dict(h) -> dict[str, Any]:
    return {
        "module": h.module,
        "function": h.function,
        "lineno": h.lineno,
        "description": h.description,
        "file_path": h.file_path,
    }


def _unit_ai_summary(unit) -> dict[str, Any]:
    return {
        "key": unit.key(),
        "title": unit.title(),
        "llm_profile": unit.llm_profile(),
        "data_source_count": len(unit.data_sources()),
        "column_doc_count": sum(len(ds.columns) for ds in unit.data_sources()),
        "feature_md_exists": unit.feature_md_path().exists(),
        "prompt_template_present": bool(unit.prompt_template_path() and unit.prompt_template_path().exists()),
        "handler_entry": _handler_entry_dict(unit.handler_entry()),
    }


class UnitAIRuntimeImprovementReq(BaseModel):
    goal: str = ""
    run: dict[str, Any] = Field(default_factory=dict)
    semantic: dict[str, Any] = Field(default_factory=dict)
    plan: list[dict[str, Any]] = Field(default_factory=list)
    results: list[dict[str, Any]] = Field(default_factory=list)
    conclusion: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/unit-ai/catalog")
def unit_ai_catalog(request: Request) -> dict[str, Any]:
    """Return summary metadata for all 11 unit AIs (read-only)."""
    current_user(request)
    items = [_unit_ai_summary(UNIT_AIS[k]) for k in UNIT_AIS.keys()]
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/unit-ai/{key}/runtime/blueprint")
def unit_ai_runtime_blueprint(key: str, request: Request) -> dict[str, Any]:
    current_user(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")
    return build_runtime_blueprint(unit_ai_scope=unit.key())


@router.post("/unit-ai/{key}/runtime/run")
async def unit_ai_runtime_run(key: str, req: AgentRuntimeRequest, request: Request) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")
    context = dict(req.context or {})
    context["unit_ai_scope"] = unit.key()
    scoped_req = AgentRuntimeRequest(
        goal=req.goal,
        max_terms=req.max_terms,
        use_llm=req.use_llm,
        context=context,
        unit_ai_scope=unit.key(),
    )
    result = await run_agent_runtime_once(scoped_req, str(me.get("username") or "user"))
    return {"ok": True, "unit_ai_scope": unit.key(), "run": result.model_dump(mode="json")}


@router.get("/unit-ai/{key}/runtime/stream")
async def unit_ai_runtime_stream(
    key: str,
    request: Request,
    goal: str = Query(..., min_length=1, max_length=4000),
    use_llm: bool = Query(False),
    max_terms: int = Query(24, ge=1, le=80),
):
    me = current_user(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")
    scoped_key = unit.key()
    req = AgentRuntimeRequest(
        goal=goal,
        use_llm=use_llm,
        max_terms=max_terms,
        context={"unit_ai_scope": scoped_key},
        unit_ai_scope=scoped_key,
    )

    async def _gen():
        async for event in stream_agent_runtime(req, str(me.get("username") or "user")):
            yield encode_sse_event(event)
            if await request.is_disconnected():
                break

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/unit-ai/{key}/runtime/improvement-proposals")
def unit_ai_runtime_improvement_proposals(key: str, req: UnitAIRuntimeImprovementReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")
    can_apply = _can_manage_agent_knowledge(me)
    proposals = _build_unit_ai_runtime_improvement_proposals(unit, req, can_apply=can_apply)
    return {
        "ok": True,
        "unit_ai_scope": unit.key(),
        "can_apply": can_apply,
        "proposals": proposals,
        "total": len(proposals),
    }


@router.get("/unit-ai/{key}/inspect")
def unit_ai_inspect(key: str, request: Request) -> dict[str, Any]:
    """Return full self-description of one unit AI plus the actual contents
    of its feature md and prompt template files."""
    current_user(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")

    feature_md_path = unit.feature_md_path()
    feature_md_text = ""
    feature_md_error = ""
    try:
        if feature_md_path.exists():
            feature_md_text = feature_md_path.read_text(encoding="utf-8")
    except OSError as exc:
        feature_md_error = str(exc)

    prompt_template_path = unit.prompt_template_path()
    prompt_template_text = ""
    prompt_template_parsed: Any = None
    prompt_template_error = ""
    if prompt_template_path is not None:
        try:
            if prompt_template_path.exists():
                prompt_template_text = prompt_template_path.read_text(encoding="utf-8")
                try:
                    prompt_template_parsed = json.loads(prompt_template_text)
                except json.JSONDecodeError:
                    prompt_template_parsed = None
        except OSError as exc:
            prompt_template_error = str(exc)

    return {
        "ok": True,
        "key": unit.key(),
        "title": unit.title(),
        "llm_profile": unit.llm_profile(),
        "feature_md": {
            "path": str(feature_md_path),
            "exists": feature_md_path.exists(),
            "text": feature_md_text,
            "error": feature_md_error,
        },
        "prompt_template": {
            "path": str(prompt_template_path) if prompt_template_path else "",
            "exists": bool(prompt_template_path and prompt_template_path.exists()),
            "text": prompt_template_text,
            "parsed": prompt_template_parsed,
            "error": prompt_template_error,
        },
        "data_sources": [_data_source_dict(ds) for ds in unit.data_sources()],
        "semantic_bindings": _semantic_bindings_dict(unit.semantic_bindings()),
        "handler_entry": _handler_entry_dict(unit.handler_entry()),
    }


def _runtime_req_run(req: UnitAIRuntimeImprovementReq) -> dict[str, Any]:
    run = req.run if isinstance(req.run, dict) else {}
    return {
        "goal": str(run.get("goal") or req.goal or ""),
        "semantic": run.get("semantic") if isinstance(run.get("semantic"), dict) else dict(req.semantic or {}),
        "plan": run.get("plan") if isinstance(run.get("plan"), list) else list(req.plan or []),
        "results": run.get("results") if isinstance(run.get("results"), list) else list(req.results or []),
        "conclusion": run.get("conclusion") if isinstance(run.get("conclusion"), dict) else dict(req.conclusion or {}),
        "events": run.get("events") if isinstance(run.get("events"), list) else list(req.events or []),
    }


def _runtime_issue_tags(run: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    semantic = run.get("semantic") if isinstance(run.get("semantic"), dict) else {}
    try:
        coverage = float(semantic.get("coverage") or 0)
    except (TypeError, ValueError):
        coverage = 0.0
    if coverage and coverage < 0.35:
        tags.append("low_semantic_coverage")
    if semantic and not semantic.get("candidates"):
        tags.append("low_semantic_coverage")
    for result in run.get("results") or []:
        if not isinstance(result, dict):
            continue
        if result.get("status") == "failed":
            tags.append("failed")
        guardrail = result.get("guardrail") if isinstance(result.get("guardrail"), dict) else {}
        guardrail_status = str(guardrail.get("status") or "")
        if guardrail_status in {"missing_slots", "approval_required", "blocked", "no_handler", "error"}:
            tags.append(guardrail_status)
        for warning in result.get("warnings") or []:
            text = str(warning or "")
            if "missing" in text:
                tags.append("missing_slots")
            if "no_handler" in text:
                tags.append("no_handler")
    conclusion = run.get("conclusion") if isinstance(run.get("conclusion"), dict) else {}
    for warning in conclusion.get("warnings") or []:
        text = str(warning or "")
        if "missing" in text:
            tags.append("missing_slots")
        if "approval_required" in text:
            tags.append("approval_required")
        if "blocked" in text:
            tags.append("blocked")
    out: list[str] = []
    for tag in tags:
        if tag and tag not in out:
            out.append(tag)
    return out


def _proposal_id(unit_key: str, target: str, payload: dict[str, Any]) -> str:
    raw = json.dumps({"unit": unit_key, "target": target, "payload": payload}, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _append_feature_note(text: str, unit_key: str, tags: list[str], goal: str) -> str:
    note = "\n\n## Runtime improvement note\n"
    note += f"- unit_ai: {unit_key}\n"
    if tags:
        note += "- issue: " + ", ".join(tags) + "\n"
    if goal:
        note += "- sample_goal: " + goal[:240] + "\n"
    note += "- policy: 승인 후 feature md/prompt/template/semantic 사전 중 하나로만 반영\n"
    base = text or ""
    if "## Runtime improvement note" in base and goal and goal[:120] in base:
        return base
    return base.rstrip() + note + "\n"


def _prompt_template_payload(unit, tags: list[str], goal: str) -> dict[str, Any] | None:
    path = unit.prompt_template_path()
    if path is None or not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        parsed = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    notes = parsed.get("runtime_improvement_notes")
    if not isinstance(notes, list):
        notes = []
    note = {
        "issue": tags,
        "sample_goal": goal[:240],
        "policy": "approved_only",
    }
    if note not in notes:
        notes.append(note)
    parsed["runtime_improvement_notes"] = notes[-8:]
    return {"text": json.dumps(parsed, ensure_ascii=False, indent=2) + "\n"}


def _first_action_row(run: dict[str, Any], unit_key: str) -> dict[str, Any]:
    for row in run.get("plan") or []:
        if isinstance(row, dict) and row.get("unit_ai") == unit_key and row.get("action"):
            return row
    return {"unit_ai": unit_key, "action": "inspect", "missing_slots": []}


def _unknown_semantic_terms(semantic: dict[str, Any]) -> list[str]:
    normalized = semantic.get("normalized_terms") if isinstance(semantic.get("normalized_terms"), dict) else {}
    out: list[str] = []
    for token in semantic.get("tokens") or []:
        text = str(token or "").strip()
        if len(text) < 2:
            continue
        if normalized.get(text):
            continue
        if text not in out:
            out.append(text)
        if len(out) >= 6:
            break
    return out


def _build_unit_ai_runtime_improvement_proposals(unit, req: UnitAIRuntimeImprovementReq, *, can_apply: bool) -> list[dict[str, Any]]:
    run = _runtime_req_run(req)
    unit_key = unit.key()
    goal = str(run.get("goal") or "")
    semantic = run.get("semantic") if isinstance(run.get("semantic"), dict) else {}
    tags = _runtime_issue_tags(run)
    action = _first_action_row(run, unit_key)
    missing = [str(v) for v in (action.get("missing_slots") or []) if str(v).strip()]
    proposals: list[dict[str, Any]] = []

    def add(target: str, title: str, rationale: str, endpoint: str, method: str, payload: dict[str, Any]) -> None:
        proposals.append({
            "id": _proposal_id(unit_key, target, payload),
            "target": target,
            "title": title,
            "rationale": rationale,
            "issue_tags": tags,
            "method": method,
            "endpoint": endpoint,
            "payload": payload,
            "can_apply": bool(can_apply),
            "approval_required": True,
        })

    if not tags:
        return proposals

    unknown_terms = _unknown_semantic_terms(semantic)
    if unknown_terms and ("low_semantic_coverage" in tags or "no_handler" in tags):
        alias_key = f"{unit_key}_runtime_terms"
        add(
            "semantic_alias",
            "시멘틱 alias 후보",
            "선택 AI 질문에서 해석되지 않은 단어가 있어 alias group 후보를 만듭니다.",
            "/api/agent/semantic/alias-groups",
            "PUT",
            {"key": alias_key, "values": unknown_terms},
        )

    if "low_semantic_coverage" in tags or "no_handler" in tags:
        hint_values = [unit_key, unit.title(), *(unknown_terms[:4] or [goal[:80]])]
        add(
            "semantic_intent",
            "intent hint 후보",
            "질문이 일반 orchestration으로 흐르거나 선택 AI handler로 충분히 좁혀지지 않았습니다.",
            "/api/agent/semantic/intent-hints",
            "PUT",
            {"key": f"{unit_key}_runtime", "values": [v for v in hint_values if str(v).strip()]},
        )

    if any(tag in tags for tag in ("no_handler", "missing_slots", "approval_required", "blocked", "failed")):
        feature_path = unit.feature_md_path()
        try:
            current_md = feature_path.read_text(encoding="utf-8") if feature_path.exists() else ""
        except OSError:
            current_md = ""
        add(
            "feature_md",
            "Feature 규칙 md 보강 후보",
            "실행 중 발견된 missing/no-handler/approval 상태를 이 unit AI의 공개 규칙 문서에 남기는 후보입니다.",
            f"/api/agent/unit-ai/{unit_key}/feature-md",
            "PUT",
            {"text": _append_feature_note(current_md, unit_key, tags, goal)},
        )

    prompt_payload = _prompt_template_payload(unit, tags, goal)
    if prompt_payload is not None and any(tag in tags for tag in ("no_handler", "low_semantic_coverage", "failed")):
        add(
            "prompt_template",
            "Prompt template 보강 후보",
            "선택 AI가 처리하지 못한 예시를 승인형 template note로 남기는 후보입니다.",
            f"/api/agent/unit-ai/{unit_key}/prompt-template",
            "PUT",
            prompt_payload,
        )

    if any(tag in tags for tag in ("missing_slots", "approval_required", "no_handler")):
        action_name = str(action.get("action") or "inspect")
        prompt_terms = [unit_key, action_name]
        if goal:
            prompt_terms.append(goal[:80])
        workflow_payload = {
            "key": _safe_slug(f"{unit_key}_{action_name}_runtime_fix"),
            "title": f"{unit.title()} {action_name} 실행 템플릿",
            "trigger": {
                "intent_in": [semantic.get("intent") or "general_orchestration"],
                "prompt_contains": prompt_terms,
                "slots_required": missing,
            },
            "steps": [{
                "unit_ai": unit_key,
                "action": action_name,
                "bind_slots": missing,
            }],
            "shared": False,
        }
        add(
            "workflow_template",
            "Workflow template 후보",
            "반복되는 missing slot 또는 승인형 흐름을 명시적인 workflow로 고정하는 후보입니다.",
            "/api/agent/workflows",
            "POST",
            workflow_payload,
        )

    return proposals


# ─── Unit AI inline editing (M4, admin only) ─────────────────────────

class UnitAIFeatureMdReq(BaseModel):
    text: str


class UnitAIPromptTemplateReq(BaseModel):
    text: str


@router.put("/unit-ai/{key}/feature-md")
def unit_ai_save_feature_md(key: str, req: UnitAIFeatureMdReq, request: Request) -> dict[str, Any]:
    """Overwrite a unit AI's feature md file. Admin only."""
    me = _require_agent_wiki_admin(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")
    path = unit.feature_md_path()
    text = req.text if isinstance(req.text, str) else ""
    if not text.endswith("\n"):
        text += "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"failed to write feature md: {exc}")
    return {"ok": True, "key": key, "path": str(path), "bytes": len(text.encode("utf-8")), "by": me.get("username") or ""}


@router.put("/unit-ai/{key}/prompt-template")
def unit_ai_save_prompt_template(key: str, req: UnitAIPromptTemplateReq, request: Request) -> dict[str, Any]:
    """Overwrite a unit AI's prompt template JSON file. Admin only. The
    submitted text must parse as JSON to be accepted; the previous file
    content is preserved if parsing fails."""
    me = _require_agent_wiki_admin(request)
    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(404, f"unknown unit AI key: {key}")
    path = unit.prompt_template_path()
    if path is None:
        raise HTTPException(400, f"unit AI {key} has no prompt template path")
    text = req.text if isinstance(req.text, str) else ""
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"prompt template must be valid JSON: {exc}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(500, f"failed to write prompt template: {exc}")
    return {"ok": True, "key": key, "path": str(path), "bytes": len(text.encode("utf-8")), "by": me.get("username") or ""}


@router.get("/column-catalog")
def column_catalog(request: Request) -> dict[str, Any]:
    """11개 unit AI의 모든 ColumnDoc을 dedupe해서 하나의 카탈로그로 반환.

    M7: schema_doc kind wiki(4개)와 중복되는 정보를 ColumnDoc 기반으로
    통합한 single source of truth. AgentV2 시멘틱 레이어의 '컬럼 카탈로그'
    sub-view가 사용한다. 같은 컬럼 이름이 여러 unit AI에 나타나면 사용처
    목록을 합쳐 한 행으로 보여준다.
    """
    current_user(request)
    rows: dict[str, dict[str, Any]] = {}
    for unit in UNIT_AIS.values():
        unit_key = unit.key()
        for ds in unit.data_sources():
            for col in ds.columns:
                name = str(col.name or "").strip()
                if not name:
                    continue
                bucket = rows.setdefault(name, {
                    "name": name,
                    "meaning": col.meaning or "",
                    "unit": col.unit or "",
                    "sample_values": list(col.sample_values or []),
                    "wiki_doc_id": col.wiki_doc_id or "",
                    "used_by": [],
                    "sources": [],
                })
                # Keep longest meaning when multiple units describe same column
                if len(col.meaning or "") > len(bucket["meaning"] or ""):
                    bucket["meaning"] = col.meaning
                if col.wiki_doc_id and not bucket["wiki_doc_id"]:
                    bucket["wiki_doc_id"] = col.wiki_doc_id
                if unit_key not in bucket["used_by"]:
                    bucket["used_by"].append(unit_key)
                if ds.path and ds.path not in bucket["sources"]:
                    bucket["sources"].append(ds.path)
                # Merge sample values uniquely
                for sv in (col.sample_values or []):
                    if sv and sv not in bucket["sample_values"]:
                        bucket["sample_values"].append(sv)
    items = sorted(rows.values(), key=lambda r: r["name"].lower())
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/source-inventory")
def source_inventory(request: Request) -> dict[str, Any]:
    """schema_relations.json의 모든 source(DB/파일)와 source별 join 정보 dump.

    M7: SemanticLayerTab의 'DB / 파일 인벤토리' sub-view가 사용. 사용자가
    'DB가 어떤거고 어떻게 연결하고'를 한눈에 보기 위함. relation 행 중심의
    SchemaRelationsPanel과 보완 관계.
    """
    current_user(request)
    raw = load_json(SCHEMA_RELATION_FILE) or {}
    if not isinstance(raw, dict):
        return {"ok": True, "sources": [], "total": 0}
    relations = raw.get("relations") if isinstance(raw.get("relations"), list) else []

    sources: dict[str, dict[str, Any]] = {}

    def _key(source_id: str, source_type: str, label: str) -> str:
        return source_id or f"{source_type}:{label}"

    for r in relations:
        if not isinstance(r, dict):
            continue
        for side in ("left", "right"):
            sid = str(r.get(f"{side}_source_id") or "").strip()
            stype = str(r.get(f"{side}_source_type") or "").strip()
            label = str(r.get(f"{side}_label") or "").strip()
            if not (sid or label):
                continue
            key = _key(sid, stype, label)
            bucket = sources.setdefault(key, {
                "source_id": sid,
                "source_type": stype,
                "label": label,
                "relation_count": 0,
                "join_keys": [],
                "connects_to": [],
            })
            bucket["relation_count"] += 1
            ck = str(r.get("canonical_key") or "").strip()
            if ck and ck not in bucket["join_keys"]:
                bucket["join_keys"].append(ck)
            other_side = "right" if side == "left" else "left"
            other_label = str(r.get(f"{other_side}_label") or "").strip()
            if other_label and other_label != label:
                if other_label not in bucket["connects_to"]:
                    bucket["connects_to"].append(other_label)

    items = sorted(sources.values(), key=lambda s: (s["source_type"], s["label"]))
    return {"ok": True, "sources": items, "total": len(items), "relations_total": len(relations)}


@router.get("/llm-profiles")
def list_llm_profiles(request: Request) -> dict[str, Any]:
    """Return llm_profiles keys from admin_settings.json. Read-only for any
    logged-in user; the actual edit happens in the LLM tab elsewhere."""
    current_user(request)
    settings_path = PATHS.data_root / "admin_settings.json"
    profiles: list[str] = []
    active: str = ""
    try:
        if settings_path.exists():
            raw = load_json(settings_path) or {}
            llm = raw.get("llm") if isinstance(raw.get("llm"), dict) else {}
            active = str(llm.get("profile") or "")
            block = raw.get("llm_profiles") if isinstance(raw.get("llm_profiles"), dict) else {}
            profiles = sorted([k for k in block.keys() if isinstance(k, str)])
    except OSError:
        pass
    return {"ok": True, "active": active, "profiles": profiles}


# ─── Workflow templates (M5) ─────────────────────────────────────────


class WorkflowSaveReq(BaseModel):
    key: str
    title: str = ""
    trigger: dict[str, Any] = Field(default_factory=dict)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    shared: bool = False


class WorkflowTestReq(BaseModel):
    prompt: str = ""
    intent: str = ""


class WorkflowExecuteReq(BaseModel):
    key: str = ""
    prompt: str = ""
    intent: str = ""
    slots: dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = True


@router.get("/workflows")
def workflows_list(request: Request) -> dict[str, Any]:
    me = current_user(request)
    username = str(me.get("username") or "")
    items = wf_templates.list_templates(username, include_shared=True)
    return {"ok": True, "items": items, "total": len(items)}


@router.post("/workflows")
def workflows_save(req: WorkflowSaveReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    username = str(me.get("username") or "")
    is_admin = _can_manage_agent_knowledge(me)
    try:
        row = wf_templates.save_template(
            req.model_dump(),
            by=username,
            is_admin=is_admin,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "template": row}


@router.put("/workflows/{key}")
def workflows_update(key: str, req: WorkflowSaveReq, request: Request) -> dict[str, Any]:
    if req.key != key:
        raise HTTPException(400, "url key and payload key must match")
    return workflows_save(req, request)


@router.delete("/workflows/{key}")
def workflows_delete(key: str, request: Request) -> dict[str, Any]:
    me = current_user(request)
    username = str(me.get("username") or "")
    is_admin = _can_manage_agent_knowledge(me)
    try:
        removed = wf_templates.delete_template(key, by=username, is_admin=is_admin)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    if not removed:
        raise HTTPException(404, f"workflow template not found: {key}")
    return {"ok": True}


@router.post("/workflows/test")
def workflows_test(req: WorkflowTestReq, request: Request) -> dict[str, Any]:
    me = current_user(request)
    username = str(me.get("username") or "")
    frame = resolve_semantic_frame(req.prompt or "", max_terms=32)
    intent = req.intent or frame.intent
    matched = wf_templates.match_prompt(req.prompt, intent=intent, username=username)
    plans, meta = build_action_plans(goal=req.prompt or "", semantic=frame.model_dump(), username=username)
    return {
        "ok": True,
        "matched": matched,
        "semantic": frame.model_dump(mode="json"),
        "runtime_plan": compact_plan_rows(plans),
        "guardrail": meta.get("guardrail") or guardrail_summary_from_plans(plans),
    }


@router.post("/workflows/execute")
def workflows_execute(req: WorkflowExecuteReq, request: Request) -> dict[str, Any]:
    """Run a workflow template's steps (dry-run by default).

    The runner is conservative: write actions (`create`, `save`, `delete`,
    `send_mail`, ...) always return `confirm_required: True` so the UI can
    ask the user to perform them explicitly. Read-only actions are
    dispatched through `core.flowi_units.dispatcher.try_dispatch` against
    the named unit AI.
    """
    me = current_user(request)
    username = str(me.get("username") or "")
    key = str(req.key or "").strip()
    template: dict[str, Any] | None
    if key:
        template = wf_templates.get_template(key)
        if not template:
            raise HTTPException(404, f"workflow not found: {key}")
    elif req.prompt:
        template = wf_templates.match_prompt(req.prompt, intent=req.intent, username=username)
        if not template:
            return {"ok": True, "matched": None, "execution": None}
    else:
        raise HTTPException(400, "key or prompt is required")
    execution = wf_templates.execute_steps(template, slots=dict(req.slots or {}), dry_run=bool(req.dry_run))
    frame = resolve_semantic_frame(req.prompt or template.get("title") or "", max_terms=32)
    plans, meta = build_action_plans(goal=req.prompt or template.get("title") or "", semantic=frame.model_dump(), username=username)
    _record_workflow_execution(request, template, execution, dry_run=bool(req.dry_run))
    return {
        "ok": True,
        "matched": template,
        "execution": execution,
        "semantic": frame.model_dump(mode="json"),
        "runtime_plan": compact_plan_rows(plans),
        "guardrail": meta.get("guardrail") or guardrail_summary_from_plans(plans),
    }


def _record_workflow_execution(
    request: Request,
    template: dict[str, Any],
    execution: dict[str, Any],
    *,
    dry_run: bool,
) -> None:
    key = str(template.get("key") or execution.get("workflow") or "").strip()
    if not key:
        return
    steps = execution.get("steps") if isinstance(execution.get("steps"), list) else []
    statuses = Counter(str(step.get("status") or "unknown") for step in steps if isinstance(step, dict))
    payload = {
        "workflow": key,
        "title": str(template.get("title") or ""),
        "dry_run": bool(dry_run),
        "steps": len(steps),
        "confirm_required": bool(execution.get("confirm_required")),
        "statuses": dict(statuses),
    }
    audit.record(
        request,
        action=f"ai_hub_run:workflow:{key}",
        detail=json.dumps(payload, ensure_ascii=False, default=str),
        tab="ai_hub",
    )
