"""Knowledge Vault core.

Local-first knowledge layer for Flow:
- raw/events: immutable event records and markdown mirrors
- wiki: human-readable markdown pages
- graph: deterministic product/lot/wafer/document relationships
- index: lightweight derived metadata

This module intentionally has no FastAPI dependency so routers, schedulers, and
agents can reuse it without coupling.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app_v2.shared.contracts import FlowEntityKey, KnowledgeDoc, KnowledgeEdge, KnowledgeEvent
from core.paths import PATHS

KNOWLEDGE_ROOT = PATHS.data_root / "knowledge"
RAW_DIR = KNOWLEDGE_ROOT / "raw"
EVENT_DIR = RAW_DIR / "events"
SOURCE_DIR = RAW_DIR / "sources"
WIKI_DIR = KNOWLEDGE_ROOT / "wiki"
GRAPH_DIR = KNOWLEDGE_ROOT / "graph"
INDEX_DIR = KNOWLEDGE_ROOT / "index"
ONTOLOGY_DIR = KNOWLEDGE_ROOT / "ontology"
EVENTS_JSONL = EVENT_DIR / "events.jsonl"
SOURCES_JSONL = SOURCE_DIR / "sources.jsonl"
WIKI_INDEX_FILE = INDEX_DIR / "wiki_index.json"
WIKI_LOG_JSONL = INDEX_DIR / "wiki_log.jsonl"
GRAPH_FILE = GRAPH_DIR / "graph.json"
AI_ONTOLOGY_FILE = ONTOLOGY_DIR / "ai_ontology.json"

_ALLOWED_ONTOLOGY_KINDS = {
    "identity", "process", "module", "material", "metric", "split",
    "work", "output", "event", "concept", "actor", "tool", "other",
}

DEFAULT_ONTOLOGY_NODES = [
    {"id": "product", "label": "product", "kind": "identity"},
    {"id": "root_lot_id", "label": "root_lot_id", "kind": "identity"},
    {"id": "wafer_id", "label": "wafer_id", "kind": "identity"},
    {"id": "LOT_WF", "label": "LOT_WF", "kind": "identity"},
    {"id": "step_id", "label": "step_id", "kind": "process"},
    {"id": "function_step", "label": "function_step", "kind": "process"},
    {"id": "knob", "label": "knob", "kind": "split"},
    {"id": "issue", "label": "issue", "kind": "work"},
    {"id": "meeting", "label": "meeting", "kind": "work"},
    {"id": "report", "label": "report", "kind": "output"},
]

DEFAULT_ONTOLOGY_EDGES = [
    {"source": "product", "target": "root_lot_id", "relation": "has_lot"},
    {"source": "root_lot_id", "target": "wafer_id", "relation": "has_wafer"},
    {"source": "root_lot_id", "target": "LOT_WF", "relation": "forms_key"},
    {"source": "wafer_id", "target": "LOT_WF", "relation": "forms_key"},
    {"source": "step_id", "target": "function_step", "relation": "maps_to"},
    {"source": "step_id", "target": "knob", "relation": "split_by"},
    {"source": "issue", "target": "root_lot_id", "relation": "tracks"},
    {"source": "meeting", "target": "issue", "relation": "discusses"},
    {"source": "report", "target": "LOT_WF", "relation": "summarizes"},
]


def _safe_concept_id(value: Any) -> str:
    raw = str(value or "").strip()
    return re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_")[:64]


def _normalize_ontology_payload(payload: Any) -> dict[str, Any]:
    """Coerce arbitrary input (e.g., LLM JSON) into a strict ontology shape.

    Drops nodes/edges with missing/invalid fields. Edges referencing unknown
    nodes are discarded. Kinds outside `_ALLOWED_ONTOLOGY_KINDS` collapse to
    "concept".
    """
    if not isinstance(payload, dict):
        return {"nodes": [], "edges": []}
    raw_nodes = payload.get("nodes") if isinstance(payload.get("nodes"), list) else []
    raw_edges = payload.get("edges") if isinstance(payload.get("edges"), list) else []
    nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node_id = _safe_concept_id(item.get("id") or item.get("label"))
        if not node_id or node_id in seen_ids:
            continue
        label = str(item.get("label") or node_id).strip()[:120]
        kind = str(item.get("kind") or "concept").strip().lower()
        if kind not in _ALLOWED_ONTOLOGY_KINDS:
            kind = "concept"
        nodes.append({"id": node_id, "label": label, "kind": kind})
        seen_ids.add(node_id)
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for item in raw_edges:
        if not isinstance(item, dict):
            continue
        src = _safe_concept_id(item.get("source"))
        tgt = _safe_concept_id(item.get("target"))
        if not src or not tgt or src == tgt or src not in seen_ids or tgt not in seen_ids:
            continue
        relation = str(item.get("relation") or "relates_to").strip()[:60] or "relates_to"
        key = (src, tgt, relation)
        if key in seen_edges:
            continue
        seen_edges.add(key)
        edges.append({"source": src, "target": tgt, "relation": relation})
    return {"nodes": nodes[:120], "edges": edges[:320]}


def load_ai_ontology() -> dict[str, Any] | None:
    """Return stored AI ontology payload (raw on-disk dict) or None if not saved."""
    if not AI_ONTOLOGY_FILE.is_file():
        return None
    try:
        text = AI_ONTOLOGY_FILE.read_text(encoding="utf-8")
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    payload = data.get("ontology") if isinstance(data.get("ontology"), dict) else data
    return _normalize_ontology_payload(payload)


def save_ai_ontology(payload: dict[str, Any], *, actor: str = "system", source: str = "ai_llm") -> dict[str, Any]:
    ensure_dirs()
    normalized = _normalize_ontology_payload(payload)
    record = {
        "saved_at": now_iso(),
        "actor": actor,
        "source": source,
        "ontology": normalized,
    }
    _atomic_json(AI_ONTOLOGY_FILE, record)
    return record


def clear_ai_ontology() -> bool:
    if AI_ONTOLOGY_FILE.is_file():
        try:
            AI_ONTOLOGY_FILE.unlink()
            return True
        except Exception:
            return False
    return False


def _extract_json_block(text: str) -> str:
    """Pull the first {...} JSON object substring from an LLM response."""
    if not text:
        return ""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
    start = cleaned.find("{")
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1]
    return ""


def _build_ontology_prompt(docs: list[dict[str, Any]]) -> str:
    lines = []
    for d in docs[:80]:
        doc_id = str(d.get("doc_id") or "").strip()
        if not doc_id:
            continue
        title = str(d.get("title") or doc_id).strip()
        summary = str(d.get("summary") or "").strip().replace("\n", " ")[:240]
        tags = d.get("tags") if isinstance(d.get("tags"), list) else []
        tag_text = ", ".join(str(t) for t in tags if t)[:120]
        kind = str(d.get("kind") or "").strip()
        lines.append(f"- {doc_id} ({kind}) — {title} :: {summary} :: tags=[{tag_text}]")
    body = "\n".join(lines) or "(empty wiki)"
    allowed_kinds = ", ".join(sorted(_ALLOWED_ONTOLOGY_KINDS))
    return (
        "당신은 사내 반도체 데이터 ontology editor 입니다. "
        "아래 wiki 문서 목록을 보고 concept-level ontology graph 를 JSON 한 덩어리로만 출력하세요. "
        "코드 블록·해설·여백 없이 단일 JSON object 만 출력하세요.\n\n"
        "각 문서를 하나의 concept node 후보로 보고, 의미에 따라 kind 를 다음 중 하나로 분류하세요: "
        f"{allowed_kinds}.\n"
        "edges 는 의미 관계를 가진 source/target/relation 만 남기세요 (자체 루프 금지). "
        "node id 는 영문/숫자/언더스코어만, 60자 이내. relation 은 영문 소문자 + 언더스코어 1~3 단어 (예: contains, follows, replaces, contacted_by).\n\n"
        "wiki 문서 목록:\n" + body + "\n\n"
        "출력 형식:\n"
        "{\"nodes\": [{\"id\": \"...\", \"label\": \"...\", \"kind\": \"...\"}, ...],\n"
        " \"edges\": [{\"source\": \"...\", \"target\": \"...\", \"relation\": \"...\"}, ...]}\n"
    )


def generate_ai_ontology(*, max_docs: int = 80) -> dict[str, Any]:
    """Ask the configured LLM for a fresh ontology classification.

    Returns dict with keys:
        ok (bool), ontology (normalized), prompt_chars, raw_text, error,
        sample_docs (count fed to the LLM).
    Caller decides whether to persist via save_ai_ontology(...).
    """
    from core import llm_adapter
    docs = list_docs(limit=max_docs)
    if not docs:
        return {"ok": False, "error": "no wiki docs to classify", "ontology": {"nodes": [], "edges": []}, "sample_docs": 0}
    if not llm_adapter.is_available():
        return {"ok": False, "error": "llm not configured or disabled", "ontology": {"nodes": [], "edges": []}, "sample_docs": len(docs)}
    prompt = _build_ontology_prompt(docs)
    sys_prompt = (
        "You output a single JSON object. No code fences, no commentary. "
        "Keys: nodes (list of {id,label,kind}), edges (list of {source,target,relation})."
    )
    result = llm_adapter.complete(prompt, system=sys_prompt, timeout=40)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error") or "llm error", "ontology": {"nodes": [], "edges": []}, "sample_docs": len(docs), "prompt_chars": len(prompt)}
    text = str(result.get("text") or "")
    block = _extract_json_block(text) or text
    try:
        parsed = json.loads(block)
    except Exception as e:
        return {
            "ok": False,
            "error": f"llm output not parseable JSON: {e}",
            "ontology": {"nodes": [], "edges": []},
            "sample_docs": len(docs),
            "prompt_chars": len(prompt),
            "raw_text": text[:2000],
        }
    normalized = _normalize_ontology_payload(parsed)
    return {
        "ok": True,
        "ontology": normalized,
        "sample_docs": len(docs),
        "prompt_chars": len(prompt),
        "raw_text": text[:2000],
    }


def ensure_dirs() -> None:
    for d in (KNOWLEDGE_ROOT, RAW_DIR, EVENT_DIR, SOURCE_DIR, WIKI_DIR, GRAPH_DIR, INDEX_DIR, ONTOLOGY_DIR):
        d.mkdir(parents=True, exist_ok=True)


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def safe_id(value: Any, fallback: str = "item") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^\w.\-]+", "_", text, flags=re.UNICODE).strip("._-")
    return text[:160] or fallback


def _hash_text(text: str, n: int = 10) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:n]


def _dump_model(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(obj or {})


def _event_id(seed: dict[str, Any]) -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    basis = json.dumps(seed, ensure_ascii=False, sort_keys=True, default=str)
    return f"evt_{stamp}_{_hash_text(basis, 8)}"


def _doc_id(kind: str, title: str, entity: FlowEntityKey | dict[str, Any] | None = None) -> str:
    ent = entity if isinstance(entity, FlowEntityKey) else FlowEntityKey(**(entity or {}))
    parts = [kind]
    if ent.product:
        parts.append(ent.product)
    if ent.root_lot_id:
        parts.append(ent.root_lot_id)
    if ent.wafer_id:
        parts.append(ent.wafer_id)
    if title:
        parts.append(title)
    return safe_id("_".join(parts), fallback=f"{kind}_{_hash_text(title or kind)}")


def _atomic_json(path: Path, data: Any) -> None:
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_dirs()
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _read_jsonl(path: Path, limit: int = 1000) -> list[dict[str, Any]]:
    ensure_dirs()
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text("utf-8").splitlines()
    except Exception:
        return []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
        if len(rows) >= max(1, min(limit, 5000)):
            break
    return rows


def _clean_tags(tags: Any) -> list[str]:
    values = tags if isinstance(tags, list) else ([tags] if tags not in (None, "") else [])
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text[:80])
        if len(out) >= 30:
            break
    return out


def _frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in meta.items():
        if value in ("", None, [], {}):
            continue
        if isinstance(value, (list, dict)):
            encoded = json.dumps(value, ensure_ascii=False)
        else:
            encoded = str(value).replace("\n", " ")
        lines.append(f"{key}: {encoded}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def _read_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    raw = text[4:end].strip().splitlines()
    meta: dict[str, Any] = {}
    for line in raw:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        value = v.strip()
        if value.startswith("[") or value.startswith("{"):
            try:
                meta[k.strip()] = json.loads(value)
                continue
            except Exception:
                pass
        meta[k.strip()] = value
    body = text[end + 5 :].lstrip("\n")
    return meta, body


def _event_markdown(row: dict[str, Any]) -> str:
    ent = row.get("entity") or {}
    meta = {
        "event_id": row.get("event_id"),
        "source_type": row.get("source_type"),
        "source_id": row.get("source_id"),
        "actor": row.get("actor"),
        "created_at": row.get("created_at"),
        "product": ent.get("product"),
        "root_lot_id": ent.get("root_lot_id"),
        "wafer_id": ent.get("wafer_id"),
        "tags": row.get("tags") or [],
    }
    payload = row.get("payload") or {}
    body = [
        f"# {row.get('title') or row.get('event_id')}",
        "",
        row.get("summary") or "",
        "",
        "## Payload",
        "",
        "```json",
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
    ]
    return _frontmatter(meta) + "\n".join(body)


def _source_markdown(row: dict[str, Any], content: str) -> str:
    meta = {
        "source_id": row.get("source_id"),
        "source_type": row.get("source_type"),
        "title": row.get("title"),
        "actor": row.get("actor"),
        "created_at": row.get("created_at"),
        "tags": row.get("tags") or [],
        "checksum": row.get("checksum"),
        "content_chars": row.get("content_chars"),
    }
    return _frontmatter(meta) + (content or "").rstrip() + "\n"


def register_agent_wiki_source(source: dict[str, Any]) -> dict[str, Any]:
    """Append an immutable Agent Wiki source under raw/sources."""
    ensure_dirs()
    content = str(source.get("content") or "").strip()
    if not content:
        raise ValueError("content is required")
    now = source.get("created_at") or now_iso()
    source_type = safe_id(source.get("source_type") or "markdown", fallback="markdown")
    title = str(source.get("title") or source.get("file_name") or "Agent Wiki Source").strip()[:220]
    checksum = hashlib.sha256(content.encode("utf-8", "ignore")).hexdigest()
    stamp = _dt.datetime.now().strftime("%Y%m%d%H%M%S")
    requested_id = safe_id(source.get("source_id") or "", fallback="")
    source_id = requested_id or f"src_{stamp}_{_hash_text(title + checksum, 8)}"
    source_id = safe_id(source_id, fallback=f"src_{_hash_text(checksum)}")
    day = str(now)[:10] or _dt.datetime.now().strftime("%Y-%m-%d")
    base_source_id = source_id
    raw_path = SOURCE_DIR / day / f"{source_id}.md"
    counter = 1
    while raw_path.exists():
        source_id = safe_id(f"{base_source_id}_{_hash_text(checksum + str(counter), 6)}", fallback=f"src_{_hash_text(checksum)}")
        raw_path = SOURCE_DIR / day / f"{source_id}.md"
        counter += 1
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "source_id": source_id,
        "source_type": source_type,
        "title": title,
        "actor": str(source.get("actor") or ""),
        "created_at": now,
        "tags": _clean_tags(source.get("tags") or []),
        "checksum": checksum,
        "content_chars": len(content),
        "content_preview": _snippet(content, "", 260),
    }
    raw_path.write_text(_source_markdown(row, content), encoding="utf-8")
    row["raw_path"] = str(raw_path.relative_to(KNOWLEDGE_ROOT))
    _append_jsonl(SOURCES_JSONL, row)
    append_wiki_log({
        "action": "source_register",
        "actor": row.get("actor") or "",
        "source_ids": [source_id],
        "title": title,
        "message": f"Registered Agent Wiki source {source_id}",
    })
    return row


def list_agent_wiki_sources(q: str = "", source_type: str = "", limit: int = 100) -> list[dict[str, Any]]:
    q_l = str(q or "").strip().lower()
    type_filter = str(source_type or "").strip()
    out: list[dict[str, Any]] = []
    for row in _read_jsonl(SOURCES_JSONL, limit=5000):
        if type_filter and str(row.get("source_type") or "") != type_filter:
            continue
        if q_l:
            hay = " ".join([
                str(row.get("source_id") or ""),
                str(row.get("title") or ""),
                str(row.get("content_preview") or ""),
                " ".join(map(str, row.get("tags") or [])),
            ]).lower()
            if q_l not in hay:
                continue
        out.append(row)
        if len(out) >= max(1, min(limit, 1000)):
            break
    return out


def get_agent_wiki_source(source_id: str) -> dict[str, Any] | None:
    target = safe_id(source_id, fallback="")
    if not target:
        return None
    for row in _read_jsonl(SOURCES_JSONL, limit=5000):
        if str(row.get("source_id") or "") != target:
            continue
        raw_path = row.get("raw_path") or ""
        fp = KNOWLEDGE_ROOT / raw_path
        content = ""
        if fp.is_file():
            try:
                meta, content = _read_frontmatter(fp.read_text("utf-8"))
                row = {**row, **{k: v for k, v in meta.items() if k not in row or not row.get(k)}}
            except Exception:
                content = ""
        return {**row, "content": content}
    return None


def append_event(event: KnowledgeEvent | dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    ev = event if isinstance(event, KnowledgeEvent) else KnowledgeEvent(**(event or {}))
    row = _dump_model(ev)
    if not row.get("created_at"):
        row["created_at"] = now_iso()
    if not row.get("event_id"):
        row["event_id"] = _event_id(row)
    row["event_id"] = safe_id(row["event_id"], fallback=_event_id(row))
    day = str(row["created_at"])[:10] or _dt.datetime.now().strftime("%Y-%m-%d")
    raw_path = EVENT_DIR / day / f"{row['event_id']}.md"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(_event_markdown(row), encoding="utf-8")
    row["raw_path"] = str(raw_path.relative_to(KNOWLEDGE_ROOT))
    _append_jsonl(EVENTS_JSONL, row)
    return row


def list_events(
    limit: int = 100,
    q: str = "",
    source_type: str = "",
    product: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
) -> list[dict[str, Any]]:
    ensure_dirs()
    if not EVENTS_JSONL.is_file():
        return []
    q_l = q.strip().lower()
    out: list[dict[str, Any]] = []
    lines = EVENTS_JSONL.read_text("utf-8").splitlines()
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ent = row.get("entity") or {}
        if source_type and row.get("source_type") != source_type:
            continue
        if product and str(ent.get("product") or "") != product:
            continue
        if root_lot_id and str(ent.get("root_lot_id") or "") != root_lot_id:
            continue
        if wafer_id and str(ent.get("wafer_id") or "") != wafer_id:
            continue
        if q_l:
            hay = " ".join([
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                str(row.get("source_id") or ""),
                " ".join(map(str, row.get("tags") or [])),
                json.dumps(row.get("payload") or {}, ensure_ascii=False, default=str),
            ]).lower()
            if q_l not in hay:
                continue
        out.append(row)
        if len(out) >= max(1, min(limit, 1000)):
            break
    return out


def _doc_path(doc: KnowledgeDoc) -> Path:
    kind = safe_id(doc.kind or "manual")
    doc_id = safe_id(doc.doc_id or _doc_id(kind, doc.title, doc.entity), fallback="doc")
    return WIKI_DIR / kind / f"{doc_id}.md"


def _doc_markdown(doc: KnowledgeDoc) -> str:
    meta = {
        "doc_id": doc.doc_id,
        "kind": doc.kind,
        "title": doc.title,
        "summary": doc.summary,
        "actor": doc.actor,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "product": doc.entity.product,
        "root_lot_id": doc.entity.root_lot_id,
        "wafer_id": doc.entity.wafer_id,
        "tags": doc.tags,
        "source_event_ids": doc.source_event_ids,
        **(doc.frontmatter or {}),
    }
    title = doc.title or doc.doc_id
    body = doc.body or doc.summary or ""
    return _frontmatter(meta) + f"# {title}\n\n{body.rstrip()}\n"


def upsert_doc(doc: KnowledgeDoc | dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    kd = doc if isinstance(doc, KnowledgeDoc) else KnowledgeDoc(**(doc or {}))
    now = now_iso()
    if not kd.doc_id:
        kd.doc_id = _doc_id(kd.kind, kd.title, kd.entity)
    kd.doc_id = safe_id(kd.doc_id, fallback=_doc_id(kd.kind, kd.title, kd.entity))
    if not kd.created_at:
        kd.created_at = now
    kd.updated_at = now
    fp = _doc_path(kd)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(_doc_markdown(kd), encoding="utf-8")
    out = _dump_model(kd)
    out["path"] = str(fp.relative_to(KNOWLEDGE_ROOT))
    _refresh_wiki_index()
    return out


def _doc_from_path(fp: Path) -> dict[str, Any] | None:
    try:
        text = fp.read_text("utf-8")
    except Exception:
        return None
    meta, body = _read_frontmatter(text)
    rel = str(fp.relative_to(KNOWLEDGE_ROOT))
    doc_id = str(meta.get("doc_id") or fp.stem)
    kind = str(meta.get("kind") or fp.parent.name or "manual")
    ent = {
        "product": meta.get("product") or "",
        "root_lot_id": meta.get("root_lot_id") or "",
        "wafer_id": meta.get("wafer_id") or "",
    }
    tags = meta.get("tags") if isinstance(meta.get("tags"), list) else []
    return {
        "doc_id": doc_id,
        "kind": kind,
        "title": meta.get("title") or doc_id,
        "summary": meta.get("summary") or "",
        "body": body,
        "actor": meta.get("actor") or "",
        "created_at": meta.get("created_at") or "",
        "updated_at": meta.get("updated_at") or "",
        "entity": ent,
        "tags": tags,
        "source_event_ids": meta.get("source_event_ids") if isinstance(meta.get("source_event_ids"), list) else [],
        "frontmatter": meta,
        "path": rel,
    }


def _refresh_wiki_index() -> list[dict[str, Any]]:
    ensure_dirs()
    docs = []
    for fp in sorted(WIKI_DIR.rglob("*.md")):
        row = _doc_from_path(fp)
        if row:
            brief = {k: row.get(k) for k in ("doc_id", "kind", "title", "summary", "updated_at", "entity", "tags", "path")}
            fm = row.get("frontmatter") if isinstance(row.get("frontmatter"), dict) else {}
            source_ids = fm.get("source_ids")
            if isinstance(source_ids, list):
                brief["source_ids"] = source_ids
            related = fm.get("related_doc_ids")
            if isinstance(related, list) and related:
                brief["related_doc_ids"] = [str(x) for x in related if x]
            relations = fm.get("relations")
            if isinstance(relations, dict) and relations:
                brief["relations"] = {str(k): str(v) for k, v in relations.items() if k}
            if row.get("source_event_ids"):
                brief["source_event_ids"] = row.get("source_event_ids")
            docs.append(brief)
    _atomic_json(WIKI_INDEX_FILE, {"updated_at": now_iso(), "docs": docs})
    return docs


def list_docs(kind: str = "", q: str = "", limit: int = 200) -> list[dict[str, Any]]:
    docs = _refresh_wiki_index()
    q_l = q.strip().lower()
    out = []
    for row in sorted(docs, key=lambda x: str(x.get("updated_at") or ""), reverse=True):
        if kind and row.get("kind") != kind:
            continue
        if q_l:
            hay = " ".join([
                str(row.get("doc_id") or ""),
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                " ".join(map(str, row.get("tags") or [])),
                " ".join(map(str, row.get("source_ids") or [])),
            ]).lower()
            if q_l not in hay:
                continue
        out.append(row)
        if len(out) >= max(1, min(limit, 1000)):
            break
    return out


def get_doc(doc_id: str) -> dict[str, Any] | None:
    ensure_dirs()
    target = safe_id(doc_id)
    if not target:
        return None
    for fp in WIKI_DIR.rglob("*.md"):
        if fp.stem != target:
            continue
        return _doc_from_path(fp)
    return None


def _snippet(text: str, q: str, n: int = 180) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    q_l = q.lower()
    idx = clean.lower().find(q_l) if q_l else 0
    if idx < 0:
        idx = 0
    start = max(0, idx - 40)
    return clean[start : start + n]


def search(q: str, scope: str = "all", limit: int = 30) -> list[dict[str, Any]]:
    ensure_dirs()
    q = q.strip()
    if not q:
        return []
    q_l = q.lower()
    results: list[dict[str, Any]] = []
    if scope in ("all", "wiki"):
        for fp in WIKI_DIR.rglob("*.md"):
            row = _doc_from_path(fp)
            if not row:
                continue
            text = " ".join([row.get("title") or "", row.get("summary") or "", row.get("body") or ""])
            hay = text.lower()
            if q_l not in hay:
                continue
            score = hay.count(q_l) + (3 if q_l in str(row.get("title") or "").lower() else 0)
            results.append({
                "result_type": "wiki",
                "id": row["doc_id"],
                "title": row.get("title") or row["doc_id"],
                "snippet": _snippet(text, q),
                "score": float(score),
                "path": row.get("path") or "",
                "entity": row.get("entity") or {},
                "tags": row.get("tags") or [],
            })
    if scope in ("all", "event"):
        for row in list_events(limit=1000):
            text = " ".join([
                str(row.get("title") or ""),
                str(row.get("summary") or ""),
                json.dumps(row.get("payload") or {}, ensure_ascii=False, default=str),
            ])
            hay = text.lower()
            if q_l not in hay:
                continue
            score = hay.count(q_l) + (2 if q_l in str(row.get("title") or "").lower() else 0)
            results.append({
                "result_type": "event",
                "id": row.get("event_id") or "",
                "title": row.get("title") or row.get("event_id") or "",
                "snippet": _snippet(text, q),
                "score": float(score),
                "path": row.get("raw_path") or "",
                "entity": row.get("entity") or {},
                "tags": row.get("tags") or [],
            })
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[: max(1, min(limit, 100))]


def append_wiki_log(entry: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    seed = {
        "created_at": entry.get("created_at") or now_iso(),
        "action": entry.get("action") or "wiki_event",
        "actor": entry.get("actor") or "",
        "doc_id": entry.get("doc_id") or "",
        "source_ids": entry.get("source_ids") or [],
        "title": entry.get("title") or "",
        "message": entry.get("message") or "",
        "meta": entry.get("meta") or {},
    }
    seed["log_id"] = entry.get("log_id") or f"log_{_dt.datetime.now().strftime('%Y%m%d%H%M%S')}_{_hash_text(json.dumps(seed, ensure_ascii=False, sort_keys=True, default=str), 8)}"
    _append_jsonl(WIKI_LOG_JSONL, seed)
    return seed


def list_wiki_log(limit: int = 100, action: str = "") -> list[dict[str, Any]]:
    rows = _read_jsonl(WIKI_LOG_JSONL, limit=max(limit, 100))
    action_filter = str(action or "").strip()
    out = [row for row in rows if not action_filter or str(row.get("action") or "") == action_filter]
    return out[: max(1, min(limit, 1000))]


def _summary_from_text(text: str, limit: int = 260) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    chunks = re.split(r"(?<=[.!?。])\s+|\n{2,}", cleaned)
    selected = next((chunk.strip(" #-\t") for chunk in chunks if len(chunk.strip()) >= 20), cleaned)
    return selected[:limit].rstrip()


def _key_points(text: str, limit: int = 6) -> list[str]:
    points: list[str] = []
    for line in str(text or "").splitlines():
        item = line.strip()
        if not item or item.startswith("#") or item in {"---"}:
            continue
        item = re.sub(r"^[-*+\d.)\s]+", "", item).strip()
        if len(item) < 12:
            continue
        points.append(item[:220])
        if len(points) >= limit:
            break
    if points:
        return points
    for part in re.split(r"(?<=[.!?。])\s+", str(text or "")):
        item = part.strip()
        if len(item) >= 12:
            points.append(item[:220])
        if len(points) >= limit:
            break
    return points


def _agent_wiki_doc_id(title: str, source_ids: list[str], content: str) -> str:
    basis = "|".join(source_ids) or content[:500]
    return safe_id(f"agent_wiki_{title}_{_hash_text(basis, 8)}", fallback=f"agent_wiki_{_hash_text(basis or title)}")


def _agent_wiki_related(title: str, tags: list[str], exclude_doc_id: str = "", limit: int = 8) -> list[dict[str, Any]]:
    tokens = {t.lower() for t in tags if t}
    tokens.update(x.lower() for x in re.findall(r"[A-Za-z0-9_가-힣]{3,}", title or "")[:8])
    rows: list[dict[str, Any]] = []
    for row in list_docs(kind="agent_wiki", limit=1000):
        if exclude_doc_id and row.get("doc_id") == exclude_doc_id:
            continue
        hay = " ".join([
            str(row.get("title") or ""),
            str(row.get("summary") or ""),
            " ".join(map(str, row.get("tags") or [])),
        ]).lower()
        score = sum(1 for token in tokens if token and token in hay)
        if score:
            rows.append({**row, "score": score})
    rows.sort(key=lambda r: (r.get("score") or 0, str(r.get("updated_at") or "")), reverse=True)
    return rows[:limit]


def preview_agent_wiki_ingest(req: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic Agent Wiki page preview without writing raw/wiki files."""
    source_ids = [safe_id(x, fallback="") for x in (req.get("source_ids") or []) if safe_id(x, fallback="")]
    sources: list[dict[str, Any]] = []
    chunks: list[str] = []
    for source_id in source_ids:
        source = get_agent_wiki_source(source_id)
        if not source:
            raise FileNotFoundError(f"source not found: {source_id}")
        sources.append(source)
        if source.get("content"):
            chunks.append(str(source.get("content") or ""))
    direct_content = str(req.get("content") or "").strip()
    if direct_content:
        chunks.append(direct_content)
    if not chunks:
        raise ValueError("source_ids or content is required")
    content = "\n\n".join(chunks).strip()
    tags = _clean_tags([*(req.get("tags") or []), *(tag for source in sources for tag in (source.get("tags") or []))])
    title = str(req.get("title") or "").strip()
    if not title:
        title = str((sources[0].get("title") if sources else "") or "").strip()
    if not title:
        title = _summary_from_text(content, limit=80) or "Agent Wiki Page"
    doc_id = safe_id(req.get("doc_id") or "", fallback="") or _agent_wiki_doc_id(title, source_ids, content)
    summary = str(req.get("summary") or "").strip() or _summary_from_text(content)
    points = _key_points(content)
    source_lines = [
        f"- `{source.get('source_id')}` - {source.get('title') or source.get('raw_path') or ''}".rstrip()
        for source in sources
    ]
    if direct_content and not sources:
        source_lines.append("- direct preview content")
    related = _agent_wiki_related(title, tags, exclude_doc_id=doc_id)
    related_lines = [f"- [[{row.get('doc_id')}]] {row.get('title') or row.get('doc_id')}" for row in related]
    body_parts = [
        "## Summary",
        "",
        summary or "-",
        "",
        "## Maintained Notes",
        "",
        *(f"- {point}" for point in points),
        "",
        "## Sources",
        "",
        *(source_lines or ["- no source registered"]),
    ]
    if related_lines:
        body_parts.extend(["", "## Related Pages", "", *related_lines])
    return {
        "doc_id": doc_id,
        "kind": "agent_wiki",
        "title": title[:220],
        "summary": summary,
        "body": "\n".join(body_parts).rstrip() + "\n",
        "tags": tags,
        "source_ids": source_ids,
        "source_count": len(source_ids),
        "content_chars": len(content),
        "related_pages": related,
    }


def commit_agent_wiki_ingest(req: dict[str, Any]) -> dict[str, Any]:
    """Commit raw source if needed, wiki page, index refresh, and append-only log."""
    source_ids = [safe_id(x, fallback="") for x in (req.get("source_ids") or []) if safe_id(x, fallback="")]
    direct_content = str(req.get("content") or "").strip()
    if direct_content and not source_ids:
        source = register_agent_wiki_source({
            "source_type": req.get("source_type") or "markdown",
            "title": req.get("title") or "Agent Wiki Source",
            "content": direct_content,
            "tags": req.get("tags") or [],
            "actor": req.get("actor") or "",
        })
        source_ids = [source["source_id"]]
        req = {**req, "source_ids": source_ids, "content": ""}
    preview = preview_agent_wiki_ingest(req)
    if str(req.get("summary") or "").strip():
        preview["summary"] = str(req.get("summary") or "").strip()
    if str(req.get("body") or "").strip():
        preview["body"] = str(req.get("body") or "").strip() + "\n"
    doc = KnowledgeDoc(
        doc_id=preview["doc_id"],
        kind="agent_wiki",
        title=preview["title"],
        summary=preview["summary"],
        body=preview["body"],
        actor=str(req.get("actor") or ""),
        tags=preview["tags"],
        source_event_ids=[],
        frontmatter={
            "schema_type": "agent_llm_wiki_page_v1",
            "source_ids": source_ids,
            "content_chars": preview.get("content_chars") or 0,
        },
    )
    saved = upsert_doc(doc)
    log = append_wiki_log({
        "action": "ingest_commit",
        "actor": req.get("actor") or "",
        "doc_id": saved.get("doc_id") or preview["doc_id"],
        "source_ids": source_ids,
        "title": saved.get("title") or preview["title"],
        "message": f"Committed Agent Wiki page {saved.get('doc_id') or preview['doc_id']}",
        "meta": {"path": saved.get("path") or "", "source_count": len(source_ids)},
    })
    return {"doc": saved, "preview": preview, "log": log}


def list_agent_wiki_pages(q: str = "", limit: int = 200) -> list[dict[str, Any]]:
    return list_docs(kind="agent_wiki", q=q, limit=limit)


def search_agent_wiki(q: str, limit: int = 30) -> list[dict[str, Any]]:
    q_l = str(q or "").strip().lower()
    if not q_l:
        return []
    results: list[dict[str, Any]] = []
    for row in list_docs(kind="agent_wiki", limit=1000):
        hay = " ".join([
            str(row.get("doc_id") or ""),
            str(row.get("title") or ""),
            str(row.get("summary") or ""),
            " ".join(map(str, row.get("tags") or [])),
            " ".join(map(str, row.get("source_ids") or [])),
        ]).lower()
        if q_l not in hay:
            continue
        score = hay.count(q_l) + (3 if q_l in str(row.get("title") or "").lower() else 0)
        doc = get_doc(str(row.get("doc_id") or ""))
        text = " ".join([row.get("title") or "", row.get("summary") or "", (doc or {}).get("body") or ""])
        results.append({
            "result_type": "wiki",
            "id": row.get("doc_id") or "",
            "doc_id": row.get("doc_id") or "",
            "title": row.get("title") or row.get("doc_id") or "",
            "summary": row.get("summary") or "",
            "snippet": _snippet(text, q_l),
            "score": float(score),
            "path": row.get("path") or "",
            "tags": row.get("tags") or [],
            "source_ids": row.get("source_ids") or [],
        })
    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results[: max(1, min(limit, 100))]


def lint_agent_wiki() -> dict[str, Any]:
    docs = [get_doc(row.get("doc_id") or "") for row in list_agent_wiki_pages(limit=1000)]
    docs = [doc for doc in docs if doc]
    ids = {str(doc.get("doc_id") or "") for doc in docs}
    inbound: dict[str, int] = {doc_id: 0 for doc_id in ids}
    broken_links: list[dict[str, Any]] = []
    missing_sources: list[dict[str, Any]] = []
    stale_summaries: list[dict[str, Any]] = []
    title_groups: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        doc_id = str(doc.get("doc_id") or "")
        body = str(doc.get("body") or "")
        for target in re.findall(r"\[\[([^\]|#]+)", body):
            target_id = safe_id(target, fallback="")
            if not target_id:
                continue
            if target_id in inbound:
                inbound[target_id] += 1
            elif not get_doc(target_id):
                broken_links.append({"doc_id": doc_id, "target": target_id})
        frontmatter = doc.get("frontmatter") if isinstance(doc.get("frontmatter"), dict) else {}
        source_ids = frontmatter.get("source_ids") if isinstance(frontmatter.get("source_ids"), list) else []
        latest_source_at = ""
        for source_id in source_ids:
            source = get_agent_wiki_source(str(source_id))
            if not source:
                missing_sources.append({"doc_id": doc_id, "source_id": source_id})
                continue
            latest_source_at = max(latest_source_at, str(source.get("created_at") or ""))
        summary = str(doc.get("summary") or "").strip()
        updated_at = str(doc.get("updated_at") or "")
        if not summary:
            stale_summaries.append({"doc_id": doc_id, "reason": "summary is empty"})
        elif latest_source_at and updated_at and latest_source_at > updated_at:
            stale_summaries.append({"doc_id": doc_id, "reason": "source is newer than wiki page", "source_created_at": latest_source_at, "updated_at": updated_at})
        key = re.sub(r"[^a-z0-9가-힣]+", "", str(doc.get("title") or "").lower())
        if key:
            title_groups.setdefault(key, []).append(doc)
    orphan_pages = [
        {"doc_id": doc_id, "inbound_links": count}
        for doc_id, count in inbound.items()
        if count == 0
    ]
    contradiction_candidates: list[dict[str, Any]] = []
    for group in title_groups.values():
        summaries = {str(doc.get("summary") or "").strip().lower() for doc in group if str(doc.get("summary") or "").strip()}
        if len(group) > 1 and len(summaries) > 1:
            contradiction_candidates.append({
                "title": group[0].get("title") or "",
                "doc_ids": [doc.get("doc_id") for doc in group],
                "reason": "same normalized title with different summaries",
            })
    return {
        "ok": True,
        "checked_at": now_iso(),
        "broken_links": broken_links,
        "missing_sources": missing_sources,
        "orphan_pages": orphan_pages,
        "stale_summaries": stale_summaries,
        "contradiction_candidates": contradiction_candidates,
        "counts": {
            "pages": len(docs),
            "broken_links": len(broken_links),
            "missing_sources": len(missing_sources),
            "orphan_pages": len(orphan_pages),
            "stale_summaries": len(stale_summaries),
            "contradiction_candidates": len(contradiction_candidates),
        },
    }


def _node(nodes: dict[str, dict[str, Any]], node_id: str, label: str, kind: str, **extra: Any) -> None:
    if not node_id:
        return
    nodes.setdefault(node_id, {"id": node_id, "label": label or node_id, "kind": kind, **extra})


def _edge(edges: dict[str, dict[str, Any]], source: str, target: str, relation: str, evidence: str = "") -> None:
    if not source or not target or source == target:
        return
    edge_id = f"edge_{_hash_text(source + '|' + target + '|' + relation, 12)}"
    edges.setdefault(edge_id, _dump_model(KnowledgeEdge(edge_id=edge_id, source=source, target=target, relation=relation, evidence=evidence)))


def _active_ontology() -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Return (nodes, edges, source) — AI ontology if saved, else built-in default."""
    ai = load_ai_ontology()
    if ai and isinstance(ai.get("nodes"), list) and ai["nodes"]:
        return ai["nodes"], list(ai.get("edges") or []), "ai_ontology"
    return DEFAULT_ONTOLOGY_NODES, DEFAULT_ONTOLOGY_EDGES, "default_ontology"


def rebuild_graph() -> dict[str, Any]:
    ensure_dirs()
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    ont_nodes, ont_edges, ont_source = _active_ontology()
    for item in ont_nodes:
        _node(nodes, "concept:" + str(item.get("id") or ""), item.get("label") or item.get("id") or "", item.get("kind") or "concept")
    for item in ont_edges:
        _edge(edges, "concept:" + str(item.get("source") or ""), "concept:" + str(item.get("target") or ""), item.get("relation") or "relates_to", ont_source)

    docs = list_docs(limit=1000)
    events = list_events(limit=1000)
    doc_ids_present = {str(row.get("doc_id") or "") for row in docs}
    for row in docs:
        doc_id = "doc:" + str(row.get("doc_id") or "")
        _node(nodes, doc_id, row.get("title") or row.get("doc_id") or "", "wiki_doc", doc_kind=row.get("kind"), path=row.get("path"))
        ent = row.get("entity") or {}
        _attach_entity(nodes, edges, doc_id, ent, "documents")
        related = row.get("related_doc_ids") if isinstance(row.get("related_doc_ids"), list) else []
        relations_map = row.get("relations") if isinstance(row.get("relations"), dict) else {}
        for ref in related:
            ref_id = str(ref or "").strip()
            if not ref_id or ref_id not in doc_ids_present:
                continue
            relation = str(relations_map.get(ref_id) or "relates_to").strip() or "relates_to"
            _edge(edges, doc_id, "doc:" + ref_id, relation, "frontmatter:related_doc_ids")
    for row in events:
        event_id = "event:" + str(row.get("event_id") or "")
        _node(nodes, event_id, row.get("title") or row.get("event_id") or "", "event", source_type=row.get("source_type"), path=row.get("raw_path"))
        ent = row.get("entity") or {}
        _attach_entity(nodes, edges, event_id, ent, "records")

    graph = {
        "updated_at": now_iso(),
        "nodes": list(nodes.values()),
        "edges": list(edges.values()),
        "counts": {"nodes": len(nodes), "edges": len(edges), "docs": len(docs), "events": len(events)},
        "ontology": {"nodes": list(ont_nodes), "edges": list(ont_edges), "source": ont_source},
    }
    _atomic_json(GRAPH_FILE, graph)
    return graph


def _attach_entity(nodes: dict[str, dict[str, Any]], edges: dict[str, dict[str, Any]], source_id: str, ent: dict[str, Any], relation: str) -> None:
    product = str(ent.get("product") or "").strip()
    lot = str(ent.get("root_lot_id") or "").strip()
    wafer = str(ent.get("wafer_id") or "").strip()
    product_id = f"product:{product}" if product else ""
    lot_id = f"lot:{product}:{lot}" if product and lot else (f"lot:{lot}" if lot else "")
    wafer_id = f"wafer:{product}:{lot}:{wafer}" if product and lot and wafer else (f"wafer:{wafer}" if wafer else "")
    if product_id:
        _node(nodes, product_id, product, "product")
        _edge(edges, source_id, product_id, relation)
    if lot_id:
        _node(nodes, lot_id, lot, "lot", product=product)
        if product_id:
            _edge(edges, product_id, lot_id, "has_lot")
        _edge(edges, source_id, lot_id, relation)
    if wafer_id:
        _node(nodes, wafer_id, wafer, "wafer", product=product, root_lot_id=lot)
        if lot_id:
            _edge(edges, lot_id, wafer_id, "has_wafer")
        _edge(edges, source_id, wafer_id, relation)


def get_graph(rebuild_if_missing: bool = True) -> dict[str, Any]:
    ensure_dirs()
    if GRAPH_FILE.is_file():
        try:
            return json.loads(GRAPH_FILE.read_text("utf-8")) or {}
        except Exception:
            pass
    if rebuild_if_missing:
        return rebuild_graph()
    return {"updated_at": "", "nodes": [], "edges": [], "counts": {"nodes": 0, "edges": 0, "docs": 0, "events": 0}}


def status() -> dict[str, Any]:
    ensure_dirs()
    docs = list_docs(limit=1000)
    events = list_events(limit=1000)
    sources = list_agent_wiki_sources(limit=1000)
    graph = get_graph(rebuild_if_missing=False)
    return {
        "ok": True,
        "root": str(KNOWLEDGE_ROOT),
        "raw_dir": str(RAW_DIR),
        "source_dir": str(SOURCE_DIR),
        "wiki_dir": str(WIKI_DIR),
        "graph_file": str(GRAPH_FILE),
        "index_file": str(WIKI_INDEX_FILE),
        "wiki_log_file": str(WIKI_LOG_JSONL),
        "counts": {
            "docs": len(docs),
            "events": len(events),
            "sources": len(sources),
            "agent_wiki_pages": len([row for row in docs if row.get("kind") == "agent_wiki"]),
            "graph_nodes": len(graph.get("nodes") or []),
            "graph_edges": len(graph.get("edges") or []),
        },
        "ontology": {"nodes": DEFAULT_ONTOLOGY_NODES, "edges": DEFAULT_ONTOLOGY_EDGES},
    }


def bootstrap(actor: str = "system") -> dict[str, Any]:
    ensure_dirs()
    doc = KnowledgeDoc(
        doc_id="knowledge_vault_overview",
        kind="ontology",
        title="Knowledge Vault Overview",
        summary="Flow Knowledge Vault skeleton: raw events, wiki pages, graph, and search index.",
        actor=actor,
        tags=["knowledge", "ontology", "system"],
        body=(
            "## Purpose\n\n"
            "Knowledge Vault keeps immutable operational events under raw/, human-readable pages under wiki/, "
            "and deterministic relationships under graph/.\n\n"
            "## Canonical identity\n\n"
            "- product\n"
            "- root_lot_id\n"
            "- wafer_id\n"
            "- LOT_WF = root_lot_id + '_' + wafer_id\n\n"
            "## Extension points\n\n"
            "Company-specific rules should live in domain packs, matching tables, and templates rather than core code.\n"
        ),
    )
    saved = upsert_doc(doc)
    event = append_event(KnowledgeEvent(
        source_type="system",
        source_id="knowledge_bootstrap",
        title="Knowledge Vault bootstrap",
        summary="Created default Knowledge Vault overview page.",
        actor=actor,
        tags=["knowledge", "bootstrap"],
        wiki_targets=[saved["doc_id"]],
    ))
    graph = rebuild_graph()
    return {"ok": True, "doc": saved, "event": event, "graph_counts": graph.get("counts") or {}}
