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

from core import knowledge_vault as kv
from core import semiconductor_knowledge as semi
from core.auth import current_user, is_page_admin, require_admin
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


class SchemaRelationSaveReq(BaseModel):
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class SchemaRelationDeleteReq(BaseModel):
    relation_ids: list[str] = Field(default_factory=list)
    note: str = ""


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


def _relation_canonical_col(name: str) -> str:
    norm = _relation_norm_col(name)
    for canonical, aliases in _RELATION_ALIASES.items():
        if norm in aliases:
            return canonical
    if norm.endswith("id") and len(norm) > 3:
        return norm
    return ""


def _relation_read_source(src: SchemaRelationSource, *, sample_rows: int = 20) -> dict[str, Any]:
    target, files = _relation_resolve_source_files(src)
    first = files[0]
    lf = _relation_scan_file(first)
    schema = lf.collect_schema()
    columns = list(schema.names())
    dtypes = {name: str(schema[name]) for name in columns}
    key_columns = [c for c in columns if _relation_canonical_col(c)][:20]
    sample_values: dict[str, list[str]] = {}
    if key_columns and sample_rows > 0:
        try:
            df = lf.select(key_columns).head(min(max(int(sample_rows), 1), 50)).collect()
            for col in key_columns:
                vals = []
                for value in df.get_column(col).drop_nulls().unique().head(6).to_list():
                    text = str(value).strip()
                    if text:
                        vals.append(text[:80])
                sample_values[col] = vals
        except Exception:
            sample_values = {}
    source_id = _relation_source_id(src, target)
    return {
        "source_id": source_id,
        "source_type": str(src.source_type or "file").strip().lower(),
        "label": src.label or src.product or src.file or target.name,
        "root": src.root,
        "product": src.product,
        "file": src.file,
        "resolved_path": str(target),
        "files_scanned": len(files),
        "columns": columns,
        "dtypes": dtypes,
        "key_columns": key_columns,
        "sample_values": sample_values,
    }


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
                    row["relation_id"] = _relation_candidate_id(row)
                    candidates.append(row)
    candidates.sort(key=lambda r: (float(r.get("confidence") or 0), bool(r.get("left_sample") and r.get("right_sample"))), reverse=True)
    dedup: dict[str, dict[str, Any]] = {}
    for row in candidates:
        dedup.setdefault(str(row.get("relation_id")), row)
    return list(dedup.values())[: max(1, min(int(limit or 30), 100))]


def _schema_relations_payload() -> dict[str, Any]:
    data = load_json(SCHEMA_RELATION_FILE, {"relations": []})
    if not isinstance(data, dict):
        data = {"relations": []}
    if not isinstance(data.get("relations"), list):
        data["relations"] = []
    return data


def _public_schema_relations() -> list[dict[str, Any]]:
    payload = _schema_relations_payload()
    return [row for row in payload.get("relations") or [] if isinstance(row, dict)]


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


def _require_agent_wiki_admin(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if me.get("role") == "admin":
        return me
    username = me.get("username") or ""
    if is_page_admin(username, "diagnosis") or is_page_admin(username, "knowledge"):
        return me
    raise HTTPException(403, "admin or diagnosis/knowledge page admin only")


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
    profiles: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for src in sources[:6]:
        try:
            profiles.append(_relation_read_source(src, sample_rows=req.sample_rows))
        except HTTPException as exc:
            errors.append({
                "source": src.model_dump() if hasattr(src, "model_dump") else src.dict(),
                "status": exc.status_code,
                "detail": exc.detail,
            })
    if len(profiles) < 2:
        raise HTTPException(400, {"message": "not enough readable schema sources", "errors": errors})
    candidates = _relation_candidates(profiles, limit=req.max_candidates)
    return {
        "ok": True,
        "preview_only": True,
        "saved": False,
        "sources": profiles,
        "errors": errors,
        "candidates": candidates,
        "graph": _relation_candidate_graph(candidates),
        "saved_graph": _relation_candidate_graph(_public_schema_relations()),
    }


@router.get("/schema-relations/graph")
def schema_relation_graph(request: Request):
    current_user(request)
    relations = _public_schema_relations()
    return {
        "ok": True,
        "relations": relations,
        "graph": _relation_candidate_graph(relations),
        "storage": "data/flow-data/schema_relations.json",
    }


@router.post("/schema-relations/save")
def schema_relation_save(req: SchemaRelationSaveReq, request: Request):
    me = require_admin(request)
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
        "canonical_key", "relation_type", "confidence", "evidence", "left_sample", "right_sample",
    }
    for candidate in candidates[:100]:
        row = {key: candidate.get(key) for key in allowed_keys if key in candidate}
        if not row.get("left_source_id") or not row.get("right_source_id") or not row.get("left_column") or not row.get("right_column"):
            continue
        row["relation_type"] = row.get("relation_type") or "join_key"
        row["relation_id"] = row.get("relation_id") or _relation_candidate_id(row)
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
    me = require_admin(request)
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
    if not doc or doc.get("kind") != "agent_wiki":
        raise HTTPException(404, "Agent Wiki page not found")
    return {"ok": True, "page": doc}


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
