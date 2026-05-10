"""Knowledge Vault API.

Thin FastAPI layer over core.knowledge_vault.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app_v2.shared.contracts import FlowEntityKey, KnowledgeDoc, KnowledgeEvent
from core import knowledge_vault as kv
from core.auth import current_user, is_page_admin

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"], dependencies=[Depends(current_user)])
ALLOWED_DOC_KINDS = {"product", "lot", "wafer", "knob", "issue", "meeting", "report", "decision", "agent_wiki", "ontology", "manual"}


def _dump_model(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(obj or {})


def _require_knowledge_admin(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if me.get("role") == "admin":
        return me
    username = me.get("username") or ""
    if is_page_admin(username, "diagnosis") or is_page_admin(username, "knowledge"):
        return me
    raise HTTPException(403, "admin or diagnosis page_admin only")


class KnowledgeDocSaveReq(BaseModel):
    doc_id: str = ""
    kind: str = "manual"
    title: str = ""
    summary: str = ""
    body: str = ""
    entity: FlowEntityKey = Field(default_factory=FlowEntityKey)
    tags: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    actor: str = ""


@router.get("/status")
def status():
    return kv.status()


@router.post("/bootstrap", dependencies=[Depends(_require_knowledge_admin)])
def bootstrap(request: Request):
    me = current_user(request)
    return kv.bootstrap(actor=me.get("username") or "system")


@router.get("/events")
def events(
    limit: int = Query(100, ge=1, le=1000),
    q: str = "",
    source_type: str = "",
    product: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
):
    return {"events": kv.list_events(limit=limit, q=q, source_type=source_type, product=product, root_lot_id=root_lot_id, wafer_id=wafer_id)}


@router.post("/events", dependencies=[Depends(_require_knowledge_admin)])
def append_event(req: KnowledgeEvent, request: Request):
    me = current_user(request)
    data = _dump_model(req)
    data["actor"] = data.get("actor") or me.get("username") or ""
    return {"ok": True, "event": kv.append_event(data)}


@router.get("/wiki")
def wiki(kind: str = "", q: str = "", limit: int = Query(200, ge=1, le=1000)):
    return {"docs": kv.list_docs(kind=kind, q=q, limit=limit)}


@router.get("/wiki/doc")
def wiki_doc(doc_id: str = Query(...)):
    row = kv.get_doc(doc_id)
    if not row:
        raise HTTPException(404, "knowledge doc not found")
    return row


@router.post("/wiki/upsert", dependencies=[Depends(_require_knowledge_admin)])
def wiki_upsert(req: KnowledgeDocSaveReq, request: Request):
    me = current_user(request)
    if not (req.title or req.doc_id):
        raise HTTPException(400, "title or doc_id required")
    doc = KnowledgeDoc(
        doc_id=req.doc_id,
        kind=req.kind if req.kind in ALLOWED_DOC_KINDS else "manual",
        title=req.title,
        summary=req.summary,
        body=req.body,
        actor=req.actor or me.get("username") or "",
        entity=req.entity,
        tags=req.tags,
        source_event_ids=req.source_event_ids,
        frontmatter=req.frontmatter,
    )
    return {"ok": True, "doc": kv.upsert_doc(doc)}


@router.get("/search")
def search(q: str = Query(..., min_length=1), scope: str = "all", limit: int = Query(30, ge=1, le=100)):
    if scope not in {"all", "wiki", "event"}:
        scope = "all"
    return {"query": q, "scope": scope, "results": kv.search(q=q, scope=scope, limit=limit)}


@router.get("/graph")
def graph():
    return kv.get_graph()


@router.post("/graph/rebuild", dependencies=[Depends(_require_knowledge_admin)])
def graph_rebuild():
    return {"ok": True, "graph": kv.rebuild_graph()}


class OntologyPayload(BaseModel):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


@router.get("/ontology")
def ontology_get():
    """Return the active ontology (AI-generated if saved, else default)."""
    saved = kv.load_ai_ontology()
    if saved and saved.get("nodes"):
        return {"source": "ai_ontology", "nodes": saved.get("nodes", []), "edges": saved.get("edges", [])}
    return {"source": "default_ontology", "nodes": kv.DEFAULT_ONTOLOGY_NODES, "edges": kv.DEFAULT_ONTOLOGY_EDGES}


@router.post("/ontology/ai-generate", dependencies=[Depends(_require_knowledge_admin)])
def ontology_ai_generate():
    """Ask the connected LLM to classify a fresh ontology from current wiki docs.

    Returns a preview only — the admin must call /ontology/save to persist.
    """
    return kv.generate_ai_ontology()


@router.post("/ontology/save", dependencies=[Depends(_require_knowledge_admin)])
def ontology_save(req: OntologyPayload, request: Request):
    me = current_user(request)
    record = kv.save_ai_ontology(req.model_dump(), actor=me.get("username") or "system")
    return {"ok": True, "saved": record, "graph": kv.rebuild_graph()}


@router.post("/ontology/clear", dependencies=[Depends(_require_knowledge_admin)])
def ontology_clear():
    removed = kv.clear_ai_ontology()
    return {"ok": True, "cleared": removed, "graph": kv.rebuild_graph()}
