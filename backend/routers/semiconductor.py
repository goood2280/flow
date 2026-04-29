from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.auth import current_user, is_page_admin, require_admin
from core import semiconductor_knowledge as semi


router = APIRouter(prefix="/api", tags=["semiconductor-diagnosis"])


class ResolveItemsReq(BaseModel):
    raw_items: list[Any] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class DataQueryReq(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 500


class TrendReq(BaseModel):
    canonical_item_ids: list[str] = Field(default_factory=list)
    lot_filter: Any = None
    date_range: Any = None


class CorrelationReq(BaseModel):
    x_items: list[str] = Field(default_factory=list)
    y_items: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)


class ChartSpecReq(BaseModel):
    data: Any = None
    chart_intent: str = ""
    x: str = ""
    y: str = ""
    metric: str = ""
    color: str = ""
    title: str = ""


class DiagnosisRunReq(BaseModel):
    prompt: str = ""
    product: str = ""
    raw_items: list[Any] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    save: bool = True


class FlowLlmChatReq(BaseModel):
    prompt: str = ""
    product: str = ""
    context: list[dict[str, Any]] = Field(default_factory=list)
    mode: str = "mock"
    max_rows: int = 12


class EngineerKnowledgeReq(BaseModel):
    visibility: str = "private"
    role: str = ""
    product: str = ""
    module: str = ""
    use_case: str = ""
    prior_knowledge: str = ""
    tags: list[str] = Field(default_factory=list)
    quality_note: str = ""


class CustomKnowledgeReq(BaseModel):
    kind: str = "research_note"
    visibility: str = "private"
    title: str = ""
    source: str = "manual"
    document_type: str = ""
    source_url: str = ""
    product: str = ""
    module: str = ""
    items: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    content: str = ""
    structured_json: dict[str, Any] = Field(default_factory=dict)
    engineer_role: str = ""
    use_case: str = ""
    quality_note: str = ""


class RagUpdatePromptReq(BaseModel):
    prompt: str = ""


class RagDocumentReq(BaseModel):
    title: str = ""
    document_type: str = "internal_note"
    source: str = "manual_document"
    source_url: str = ""
    product: str = ""
    module: str = ""
    tags: list[str] = Field(default_factory=list)
    content: str = ""


class RagTableReq(BaseModel):
    title: str = ""
    table_type: str = "process_plan_func_step"
    source: str = "manual_table"
    source_url: str = ""
    visibility: str = "private"
    product: str = ""
    module: str = ""
    tags: list[str] = Field(default_factory=list)
    content: str = ""
    reference_content: str = ""
    apply_instructions: str = ""
    target_file: str = ""
    apply_to_file: bool = False
    preview: dict[str, Any] = Field(default_factory=dict)


class ReformatterProposalReq(BaseModel):
    product: str = ""
    prompt: str = ""
    sample_columns: list[str] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    use_dataset: bool = False


class ReformatterApplyReq(BaseModel):
    product: str = ""
    rules: list[dict[str, Any]] = Field(default_factory=list)


class TegProposalReq(BaseModel):
    product: str = ""
    prompt: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    use_dataset: bool = False


class TegApplyReq(BaseModel):
    product: str = ""
    teg_definitions: list[dict[str, Any]] = Field(default_factory=list)


class DatasetSampleReq(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)
    limit: int = 200


class DatasetProfileReq(BaseModel):
    source: dict[str, Any] = Field(default_factory=dict)
    limit: int = 300


def _user_context(me: dict, req_context: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "username": me.get("username") or "",
        "role": me.get("role") or "user",
        "conversation_turns": len(req_context or []),
    }


def _require_file_write_delegate(request: Request) -> dict[str, Any]:
    me = current_user(request)
    username = me.get("username") or ""
    if (me.get("role") or "") == "admin" or is_page_admin(username, "filebrowser"):
        return me
    raise HTTPException(403, "Admin or delegated FileBrowser writer only")


@router.get("/items/search")
def search_items(request: Request, q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=200)):
    current_user(request)
    return semi.search_items(q=q, limit=limit)


@router.post("/items/resolve")
def resolve_items(req: ResolveItemsReq, request: Request):
    current_user(request)
    return semi.resolve_item_semantics(req.raw_items, req.context)


@router.post("/data/query-et")
def query_et(req: DataQueryReq, request: Request):
    current_user(request)
    return semi.query_measurements("ET", req.filters, limit=req.limit)


@router.post("/data/query-inline")
def query_inline(req: DataQueryReq, request: Request):
    current_user(request)
    return semi.query_measurements("INLINE", req.filters, limit=req.limit)


@router.post("/analytics/trend")
def trend(req: TrendReq, request: Request):
    current_user(request)
    return semi.get_metric_trend(req.canonical_item_ids, req.lot_filter, req.date_range)


@router.post("/analytics/correlation")
def correlation(req: CorrelationReq, request: Request):
    current_user(request)
    return semi.run_correlation_analysis(req.x_items, req.y_items, req.filters)


@router.post("/charts/spec")
def chart_spec(req: ChartSpecReq, request: Request):
    current_user(request)
    return semi.create_chart_spec(
        data=req.data,
        chart_intent=req.chart_intent,
        x=req.x,
        y=req.y,
        metric=req.metric,
        color=req.color,
        title=req.title,
    )


@router.post("/diagnosis/run")
def diagnosis_run(req: DiagnosisRunReq, request: Request):
    me = current_user(request)
    return semi.run_diagnosis(
        req.prompt,
        product=req.product,
        raw_items=req.raw_items,
        filters=req.filters,
        user_context=_user_context(me),
        save=req.save,
    )


@router.get("/diagnosis/{run_id}")
def diagnosis_get(run_id: str, request: Request):
    me = current_user(request)
    if str(run_id or "").strip().lower() == "knowledge":
        return _knowledge_manifest_response(me)
    row = semi.get_diagnosis_run(run_id)
    if not row:
        raise HTTPException(404, "Diagnosis run not found")
    return row


@router.post("/llm/chat")
def llm_chat(req: FlowLlmChatReq, request: Request):
    """Semiconductor assistant chat adapter.

    This route intentionally stays deterministic by default.  It exposes the
    same backend whitelist used by /api/diagnosis/run and does not accept SQL
    text from the model/client.  Real LLM drafting can be layered behind this
    route later when OPENAI_API_KEY is present, but tool execution remains here.
    """
    me = current_user(request)
    report = semi.run_diagnosis(
        req.prompt,
        product=req.product,
        filters={"chat_mode": req.mode, "max_rows": req.max_rows},
        user_context=_user_context(me, req.context),
        save=True,
    )
    top = report.get("ranked_hypotheses") or []
    if top:
        lines = [f"{h.get('rank')}. {h.get('hypothesis')} (confidence {h.get('confidence')})" for h in top[:3]]
        answer = "진단/RCA가 구조화된 RCA 초안을 만들었습니다.\n" + "\n".join(lines)
    else:
        answer = "인식된 반도체 지표가 부족합니다. item_master에 등록된 ET/Inline/VM item 또는 측정 구조를 더 알려주세요."
    return {
        "ok": True,
        "mode": "mock_llm_deterministic",
        "answer": answer,
        "diagnosis": report,
        "tool_calls": [
            "resolve_item_semantics",
            "search_knowledge_cards",
            "traverse_causal_graph",
            "find_similar_cases",
            "create_chart_spec",
            "save_diagnosis_report",
        ],
    }


@router.get("/semiconductor/knowledge")
def knowledge_manifest(request: Request):
    me = current_user(request)
    return _knowledge_manifest_response(me)


def _knowledge_manifest_response(me: dict[str, Any]):
    out = semi.storage_manifest()
    out.update({
        "counts": {
            "item_master": len(semi.ITEM_MASTER),
            "knowledge_cards": len(semi.all_knowledge_cards()),
            "default_seed_cards": len(semi.seed_knowledge_cards()),
            "custom_cards": len(semi.custom_knowledge_cards()),
            "causal_edges": len(semi.all_causal_edges()),
            "historical_cases": len(semi.all_historical_cases()),
            "engineer_use_cases": len(semi.ENGINEER_USE_CASE_SEEDS),
        },
        "tools": semi.llm_tool_catalog(),
        "custom_knowledge": semi.custom_knowledge_rows(me.get("username") or "", me.get("role") or "user"),
    })
    return out


@router.get("/semiconductor/knowledge/rag-view")
def knowledge_rag_view(
    request: Request,
    q: str = Query("", max_length=200),
    limit: int = Query(120, ge=20, le=300),
):
    me = current_user(request)
    return _knowledge_rag_view_response(me, q, limit)


def _knowledge_rag_view_response(me: dict[str, Any], q: str = "", limit: int = 120):
    return semi.rag_knowledge_view(
        username=me.get("username") or "",
        role=me.get("role") or "user",
        q=q,
        limit=limit,
    )


@router.get("/diagnosis/knowledge")
@router.get("/rca/knowledge")
@router.get("/knowledge/rca")
def knowledge_manifest_alias(request: Request):
    """Compatibility aliases for cached/older RCA knowledge screens."""
    me = current_user(request)
    return _knowledge_manifest_response(me)


@router.get("/diagnosis/knowledge/rag-view")
@router.get("/rca/knowledge/rag-view")
@router.get("/knowledge/rca/rag-view")
@router.get("/knowledge/rag-view")
def knowledge_rag_view_alias(
    request: Request,
    q: str = Query("", max_length=200),
    limit: int = Query(120, ge=1, le=500),
):
    """Compatibility aliases for cached/older RCA knowledge screens."""
    me = current_user(request)
    return _knowledge_rag_view_response(me, q, limit)


@router.get("/semiconductor/use-cases")
def use_cases(request: Request):
    current_user(request)
    return semi.list_engineer_use_cases()


@router.get("/semiconductor/source-profiles")
def source_profiles(request: Request):
    current_user(request)
    return {"ok": True, "profiles": semi.SOURCE_TYPE_PROFILES}


@router.get("/semiconductor/engineer-knowledge")
def engineer_knowledge(request: Request):
    me = current_user(request)
    return semi.list_engineer_knowledge(me.get("username") or "", me.get("role") or "user")


@router.post("/semiconductor/engineer-knowledge")
def engineer_knowledge_add(req: EngineerKnowledgeReq, request: Request):
    me = current_user(request)
    return semi.add_engineer_knowledge(req.dict(), me.get("username") or "", me.get("role") or "user")


@router.post("/semiconductor/knowledge/import")
def custom_knowledge_import(req: CustomKnowledgeReq, request: Request):
    me = require_admin(request)
    return semi.add_custom_knowledge(req.dict(), me.get("username") or "", me.get("role") or "admin")


@router.post("/semiconductor/knowledge/document")
def custom_knowledge_document(req: RagDocumentReq, request: Request):
    me = require_admin(request)
    try:
        return semi.add_document_knowledge(req.dict(), me.get("username") or "", me.get("role") or "admin")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/semiconductor/knowledge/table/preview")
def custom_knowledge_table_preview(req: RagTableReq, request: Request):
    me = current_user(request)
    return semi.preview_table_knowledge(req.dict(), me.get("username") or "", me.get("role") or "user")


@router.post("/semiconductor/knowledge/table/commit")
def custom_knowledge_table_commit(req: RagTableReq, request: Request):
    me = current_user(request)
    try:
        if req.apply_to_file or req.target_file:
            if me.get("role") != "admin" and not is_page_admin(me.get("username") or "", "filebrowser"):
                raise HTTPException(403, "admin or filebrowser page_admin only")
        return semi.commit_table_knowledge(req.dict(), me.get("username") or "", me.get("role") or "user")
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/semiconductor/knowledge/update-prompt")
def custom_knowledge_update_prompt(req: RagUpdatePromptReq, request: Request):
    me = current_user(request)
    role = me.get("role") or "user"
    try:
        return semi.structure_rag_update_from_prompt(
            req.prompt,
            username=me.get("username") or "",
            role=role,
            require_marker=(role != "admin"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/semiconductor/reformatter/propose")
def reformatter_propose(req: ReformatterProposalReq, request: Request):
    current_user(request)
    if req.use_dataset or req.source:
        return semi.reformatter_alias_proposal_from_dataset(req.product, source=req.source, prompt=req.prompt)
    return semi.reformatter_alias_proposal_from_prompt(req.prompt, product=req.product, sample_columns=req.sample_columns)


@router.post("/semiconductor/reformatter/apply")
def reformatter_apply(req: ReformatterApplyReq, request: Request):
    me = _require_file_write_delegate(request)
    try:
        return semi.apply_reformatter_alias_proposal(req.product, req.rules, username=me.get("username") or "filebrowser_delegate")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/semiconductor/teg/propose")
def teg_propose(req: TegProposalReq, request: Request):
    current_user(request)
    if req.use_dataset or req.source:
        return semi.teg_layout_proposal_from_dataset(req.product, source=req.source, prompt=req.prompt)
    return semi.teg_layout_proposal_from_rows(req.product, rows=req.rows, prompt=req.prompt)


@router.post("/semiconductor/teg/apply")
def teg_apply(req: TegApplyReq, request: Request):
    me = _require_file_write_delegate(request)
    try:
        return semi.apply_teg_layout_proposal(req.product, req.teg_definitions, username=me.get("username") or "filebrowser_delegate")
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/semiconductor/dataset/sample")
def semiconductor_dataset_sample(req: DatasetSampleReq, request: Request):
    current_user(request)
    return semi.dataset_sample(req.source, limit=req.limit)


@router.post("/semiconductor/dataset/profile")
def semiconductor_dataset_profile(req: DatasetProfileReq, request: Request):
    current_user(request)
    return semi.dataset_profile(req.source, limit=req.limit)
