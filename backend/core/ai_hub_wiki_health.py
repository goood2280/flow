"""AI Hub Agent Wiki health summary.

This module builds a compact read-only operator view over the existing
Knowledge Vault/Agent Wiki stores. It does not own Wiki writes; Agent Wiki
source/page APIs remain under routers.agent.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import knowledge_vault as kv


def build_wiki_health(*, limit: int = 12) -> dict[str, Any]:
    limit = max(1, min(50, int(limit or 12)))

    docs = _safe_call(lambda: kv.list_docs(limit=1000), [])
    pages = _safe_call(lambda: kv.list_agent_wiki_pages(limit=max(100, limit)), [])
    sources = _safe_call(lambda: kv.list_agent_wiki_sources(limit=1000), [])
    logs = _safe_call(lambda: kv.list_wiki_log(limit=max(100, limit)), [])
    lint = _safe_call(kv.lint_agent_wiki, {"ok": False, "counts": {}, "error": "lint failed"})
    graph = _safe_call(lambda: kv.get_graph(rebuild_if_missing=False, rebuild_if_stale=False), {})

    kind_counts: dict[str, int] = {}
    for row in docs:
        if isinstance(row, dict):
            kind = str(row.get("kind") or "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1

    lint_counts = lint.get("counts") if isinstance(lint.get("counts"), dict) else {}
    graph_counts = graph.get("counts") if isinstance(graph.get("counts"), dict) else {}
    issue_count = sum(
        _int(lint_counts.get(key))
        for key in ("broken_links", "missing_sources", "stale_summaries", "contradiction_candidates")
    )
    status = _status(len(docs), len(sources), issue_count, bool(lint.get("ok", True)))

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "summary": _summary(len(docs), len(sources), graph_counts, issue_count),
        "counts": {
            "docs": len(docs),
            "agent_wiki_pages": kind_counts.get("agent_wiki", 0),
            "schema_docs": kind_counts.get("schema_doc", 0),
            "sources": len(sources),
            "wiki_log": len(logs),
            "graph_nodes": _int(graph_counts.get("nodes")),
            "graph_edges": _int(graph_counts.get("edges")),
            "lint_issues": issue_count,
            **{f"lint_{key}": _int(value) for key, value in lint_counts.items()},
        },
        "kind_counts": kind_counts,
        "lint": {
            "ok": bool(lint.get("ok")),
            "checked_at": str(lint.get("checked_at") or ""),
            "counts": lint_counts,
            "broken_links": _sample(lint.get("broken_links"), limit=limit),
            "missing_sources": _sample(lint.get("missing_sources"), limit=limit),
            "stale_summaries": _sample(lint.get("stale_summaries"), limit=limit),
            "contradiction_candidates": _sample(lint.get("contradiction_candidates"), limit=limit),
            "orphan_pages": _sample(lint.get("orphan_pages"), limit=min(limit, 8)),
        },
        "graph": {
            "exists": bool(graph.get("updated_at") or graph.get("nodes") or graph.get("edges")),
            "updated_at": str(graph.get("updated_at") or ""),
            "counts": {
                "nodes": _int(graph_counts.get("nodes")),
                "edges": _int(graph_counts.get("edges")),
                "docs": _int(graph_counts.get("docs")),
                "events": _int(graph_counts.get("events")),
            },
        },
        "recent_pages": [_page_row(row) for row in pages[:limit] if isinstance(row, dict)],
        "recent_sources": [_source_row(row) for row in sources[:limit] if isinstance(row, dict)],
        "recent_log": [_log_row(row) for row in logs[:limit] if isinstance(row, dict)],
        "sources": {
            "overview": "/api/agent/knowledge/overview",
            "wiki_pages": "/api/agent/wiki/pages",
            "wiki_sources": "/api/agent/wiki/sources",
            "wiki_lint": "/api/agent/wiki/lint",
            "workflow_map": "/api/ai-hub/workflow-map",
        },
    }


def _status(doc_count: int, source_count: int, issue_count: int, lint_ok: bool) -> str:
    if doc_count <= 0 and source_count <= 0:
        return "missing"
    if issue_count > 0 or not lint_ok:
        return "warn"
    return "pass"


def _summary(doc_count: int, source_count: int, graph_counts: dict[str, Any], issue_count: int) -> str:
    return (
        f"docs={doc_count} sources={source_count} "
        f"graph={_int(graph_counts.get('nodes'))}/{_int(graph_counts.get('edges'))} "
        f"lint_issues={issue_count}"
    )


def _safe_call(fn, fallback):
    try:
        out = fn()
        return out if out is not None else fallback
    except Exception as exc:
        if isinstance(fallback, dict):
            return {**fallback, "error": str(exc)[:240]}
        return fallback


def _sample(value: Any, *, limit: int) -> list[Any]:
    return list(value if isinstance(value, list) else [])[:limit]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _page_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": str(row.get("doc_id") or row.get("id") or ""),
        "kind": str(row.get("kind") or ""),
        "title": str(row.get("title") or row.get("doc_id") or ""),
        "summary": str(row.get("summary") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "tags": [str(x) for x in (row.get("tags") if isinstance(row.get("tags"), list) else [])][:8],
        "source_ids": [str(x) for x in (row.get("source_ids") if isinstance(row.get("source_ids"), list) else [])][:8],
        "path": str(row.get("path") or ""),
    }


def _source_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(row.get("source_id") or ""),
        "source_type": str(row.get("source_type") or ""),
        "title": str(row.get("title") or row.get("source_id") or ""),
        "actor": str(row.get("actor") or ""),
        "created_at": str(row.get("created_at") or ""),
        "tags": [str(x) for x in (row.get("tags") if isinstance(row.get("tags"), list) else [])][:8],
        "content_chars": _int(row.get("content_chars")),
    }


def _log_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "log_id": str(row.get("log_id") or ""),
        "created_at": str(row.get("created_at") or ""),
        "action": str(row.get("action") or ""),
        "actor": str(row.get("actor") or ""),
        "doc_id": str(row.get("doc_id") or ""),
        "title": str(row.get("title") or ""),
        "message": str(row.get("message") or ""),
    }
