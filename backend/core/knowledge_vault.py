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
WIKI_DIR = KNOWLEDGE_ROOT / "wiki"
GRAPH_DIR = KNOWLEDGE_ROOT / "graph"
INDEX_DIR = KNOWLEDGE_ROOT / "index"
EVENTS_JSONL = EVENT_DIR / "events.jsonl"
WIKI_INDEX_FILE = INDEX_DIR / "wiki_index.json"
GRAPH_FILE = GRAPH_DIR / "graph.json"

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


def ensure_dirs() -> None:
    for d in (KNOWLEDGE_ROOT, RAW_DIR, EVENT_DIR, WIKI_DIR, GRAPH_DIR, INDEX_DIR):
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


def _node(nodes: dict[str, dict[str, Any]], node_id: str, label: str, kind: str, **extra: Any) -> None:
    if not node_id:
        return
    nodes.setdefault(node_id, {"id": node_id, "label": label or node_id, "kind": kind, **extra})


def _edge(edges: dict[str, dict[str, Any]], source: str, target: str, relation: str, evidence: str = "") -> None:
    if not source or not target or source == target:
        return
    edge_id = f"edge_{_hash_text(source + '|' + target + '|' + relation, 12)}"
    edges.setdefault(edge_id, _dump_model(KnowledgeEdge(edge_id=edge_id, source=source, target=target, relation=relation, evidence=evidence)))


def rebuild_graph() -> dict[str, Any]:
    ensure_dirs()
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    for item in DEFAULT_ONTOLOGY_NODES:
        _node(nodes, "concept:" + item["id"], item["label"], item["kind"])
    for item in DEFAULT_ONTOLOGY_EDGES:
        _edge(edges, "concept:" + item["source"], "concept:" + item["target"], item["relation"], "default_ontology")

    docs = list_docs(limit=1000)
    events = list_events(limit=1000)
    for row in docs:
        doc_id = "doc:" + str(row.get("doc_id") or "")
        _node(nodes, doc_id, row.get("title") or row.get("doc_id") or "", "wiki_doc", doc_kind=row.get("kind"), path=row.get("path"))
        ent = row.get("entity") or {}
        _attach_entity(nodes, edges, doc_id, ent, "documents")
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
        "ontology": {"nodes": DEFAULT_ONTOLOGY_NODES, "edges": DEFAULT_ONTOLOGY_EDGES},
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
    graph = get_graph(rebuild_if_missing=False)
    return {
        "ok": True,
        "root": str(KNOWLEDGE_ROOT),
        "raw_dir": str(RAW_DIR),
        "wiki_dir": str(WIKI_DIR),
        "graph_file": str(GRAPH_FILE),
        "index_file": str(WIKI_INDEX_FILE),
        "counts": {
            "docs": len(docs),
            "events": len(events),
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
