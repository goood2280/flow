from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core import semiconductor_knowledge as semi
from core.auth import current_user, require_admin
from core.paths import PATHS
from core.utils import load_json, save_json
from routers import llm as flowi_llm


router = APIRouter(prefix="/api/agent", tags=["agent"])

AGENT_BACKUP_DIR = PATHS.data_root / "agent_backups"
AGENT_ADMIN_STATE_FILE = PATHS.data_root / "agent_admin_tools.json"
AGENT_KNOWLEDGE_RAW_DIR = PATHS.data_root / "knowledge" / "raw"


class PromptPreviewReq(BaseModel):
    prompt: str = ""
    product: str = ""
    max_rows: int = 20


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
    roots = [PATHS.db_root, PATHS.data_root / "splittable" / "match_cache"]
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
