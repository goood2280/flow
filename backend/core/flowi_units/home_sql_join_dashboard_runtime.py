"""Agent-visible Home SQL → JOIN → Dashboard runtime graph.

Reuses FileBrowser AI SQL for the base source SQL draft, schema_relations
based JOIN planning from ``flowi_multisource``, and chart spec drafting via
``dashboard_join``. The actual SQL/JOIN execution is read-only and uses
Polars LazyFrame helpers from ``flowi_multisource``.
"""
from __future__ import annotations

import json
import operator
import time
import uuid
from copy import deepcopy
from typing import Annotated, Any, Callable, TypedDict

from fastapi import HTTPException

from app_v2.modules.agent_runtime.executor import (
    NodeExecutor,
    StateReducer,
    TraceRecorder,
    run_sequential as run_nodes_sequential,
)


UNIT_AI_KEY = "home_sql_join_dashboard"

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "base_source_resolve", "label": "기준 소스 확인", "phase": "context"},
    {"id": "ai_sql_draft", "label": "AI SQL 초안", "phase": "llm"},
    {"id": "join_candidate_select", "label": "JOIN 후보 선택", "phase": "plan"},
    {"id": "join_plan_validate", "label": "JOIN 계획 검증", "phase": "validate"},
    {"id": "join_execute", "label": "JOIN 실행", "phase": "execute"},
    {"id": "output_route", "label": "출력 모드 결정", "phase": "llm"},
    {"id": "dashboard_draft", "label": "Dashboard 차트 초안", "phase": "llm"},
)

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "base_source_resolve", "target": "ai_sql_draft"},
    {"source": "ai_sql_draft", "target": "join_candidate_select"},
    {"source": "join_candidate_select", "target": "join_plan_validate"},
    {"source": "join_plan_validate", "target": "join_execute"},
    {"source": "join_execute", "target": "output_route"},
    {"source": "output_route", "target": "dashboard_draft"},
)

OUTPUT_ROUTE_SYSTEM_PROMPT = (
    "You decide whether the user's natural-language request wants raw rows or a chart. "
    'Return JSON {"mode": "raw"|"chart", "reason": "short reason"}. '
    "Use 'chart' when the request mentions plotting, trends, comparisons, distributions. "
    "Use 'raw' when the request asks for a table, list, counts, or exact values. "
    "Do not return SELECT/FROM/DDL/markdown/reasoning."
)

DASHBOARD_DRAFT_SYSTEM_PROMPT = (
    "You design a dashboard chart spec from joined rows. "
    "Return JSON with chart_type from the provided whitelist only. "
    "Pick x and y from the provided columns. Use group/color only if the column exists. "
    "Keep title under 80 chars. Do not include markdown/comments."
)

STATE_DESIGN: dict[str, dict[str, Any]] = {
    "run_id": {"description": "Runtime execution id.", "producer": "runtime", "public": True},
    "request": {"description": "Sanitized prompt and base source slots.", "producer": "runtime", "public": True},
    "base_source": {"description": "Resolved base source scope/root/product/file.", "producer": "base_source_resolve", "public": True},
    "ai_sql": {"description": "FileBrowser AI SQL draft (WHERE/SELECT/SORT) + preview snapshot + sub-trace.", "producer": "ai_sql_draft", "public": True},
    "join_candidates": {"description": "Scored second-source candidates from schema_relations registry.", "producer": "join_candidate_select", "public": True},
    "join_plan": {"description": "Confirmed relation chain, JOIN steps, missing_evidence.", "producer": "join_plan_validate", "public": True},
    "joined": {"description": "Polars JOIN result summary (columns, sample_rows, row_count).", "producer": "join_execute", "public": True},
    "output_route": {"description": "raw vs chart routing decision plus LLM status.", "producer": "output_route", "public": True},
    "dashboard": {"description": "Chart spec + chart_result for Plotly renderer (empty when raw).", "producer": "dashboard_draft", "public": True},
    "node_errors": {"description": "Public node failure messages keyed by node id.", "producer": "runtime", "public": True},
    "trace": {"description": "Append-only public node trace rows.", "producer": "runtime", "public": True},
    "runtime_warnings": {"description": "Runner-level warnings.", "producer": "runtime", "public": True},
}

NODE_METADATA: dict[str, dict[str, Any]] = {
    "base_source_resolve": {
        "persona": "Deterministic resolver that normalizes the base FileBrowser source slot (root/product or file).",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.root", "request.product", "request.file"],
        "writes": ["base_source"],
        "shared_state": ["base_source.scope", "base_source.root", "base_source.product", "base_source.file"],
        "answer_attach_rule": "Attach scope/root/product/file only; never raw rows.",
    },
    "ai_sql_draft": {
        "persona": "Delegator that runs FileBrowser AI SQL runtime to obtain WHERE/SELECT/SORT for the base source.",
        "prompt": {"system": "(delegates to filebrowser_ai_sql graph)", "mode": "delegate"},
        "reads": ["request.natural_language", "base_source"],
        "writes": ["ai_sql"],
        "shared_state": ["ai_sql.where_sql", "ai_sql.selected_columns", "ai_sql.sort", "ai_sql.preview_rows"],
        "answer_attach_rule": "Attach SQL strings, selected columns, sort, preview metadata; carry sub-trace.",
    },
    "join_candidate_select": {
        "persona": "Deterministic candidate finder using schema_relations registry and prompt term hits.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.natural_language", "base_source.product"],
        "writes": ["join_candidates"],
        "shared_state": ["join_candidates"],
        "answer_attach_rule": "Attach candidate source_ids, labels, scores, and matched terms.",
    },
    "join_plan_validate": {
        "persona": "Confirmed-relation validator that builds the JOIN chain and lists missing evidence.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["join_candidates"],
        "writes": ["join_plan"],
        "shared_state": ["join_plan.sources", "join_plan.relations", "join_plan.steps", "join_plan.missing_evidence"],
        "answer_attach_rule": "Attach JOIN steps, relation ids, and missing-evidence reasons.",
    },
    "join_execute": {
        "persona": "Read-only JOIN executor (Polars LazyFrame) with identity filters from base source.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["base_source", "ai_sql.semantic_frame", "join_plan"],
        "writes": ["joined"],
        "shared_state": ["joined.row_count", "joined.sample_rows", "joined.columns"],
        "answer_attach_rule": "Attach JOIN result summary; rows are capped to the request max_rows.",
    },
    "output_route": {
        "persona": "Output-mode router (LLM JSON, heuristic fallback).",
        "prompt": {"system": OUTPUT_ROUTE_SYSTEM_PROMPT, "mode": "llm_json"},
        "reads": ["request.natural_language", "joined.row_count", "joined.columns"],
        "writes": ["output_route"],
        "shared_state": ["output_route.mode", "output_route.reason", "output_route.llm"],
        "answer_attach_rule": "Attach decided mode, public reason, LLM status.",
    },
    "dashboard_draft": {
        "persona": "Chart spec drafter (LLM JSON) restricted to dashboard_join whitelist.",
        "prompt": {"system": DASHBOARD_DRAFT_SYSTEM_PROMPT, "mode": "llm_json"},
        "reads": ["request.natural_language", "joined.columns", "joined.sample_rows"],
        "writes": ["dashboard"],
        "shared_state": ["dashboard.chart_type", "dashboard.config", "dashboard.chart_result"],
        "answer_attach_rule": "Attach chart_type, config, chart_result (Plotly-friendly); skip when mode!=chart.",
    },
}


class _RuntimeState(TypedDict, total=False):
    run_id: str
    request: dict[str, Any]
    username: str
    base_source: dict[str, Any]
    ai_sql: dict[str, Any]
    join_candidates: list[dict[str, Any]]
    join_registry: dict[str, Any]
    join_plan: dict[str, Any]
    joined: dict[str, Any]
    output_route: dict[str, Any]
    dashboard: dict[str, Any]
    node_errors: dict[str, str]
    trace: Annotated[list[dict[str, Any]], operator.add]
    runtime_warnings: Annotated[list[str], operator.add]
    agent_context: dict[str, Any]


def home_sql_join_dashboard_graph(statuses: dict[str, str] | None = None) -> dict[str, Any]:
    statuses = statuses or {}
    return {
        "nodes": [
            {
                **node,
                **deepcopy(NODE_METADATA.get(node["id"], {})),
                "state_io": {
                    "reads": list(NODE_METADATA.get(node["id"], {}).get("reads") or []),
                    "writes": list(NODE_METADATA.get(node["id"], {}).get("writes") or []),
                },
                "status": statuses.get(node["id"], "pending"),
            }
            for node in GRAPH_NODES
        ],
        "edges": [dict(edge) for edge in GRAPH_EDGES],
        "state_design": deepcopy(STATE_DESIGN),
    }


def _safe_text(value: Any, max_len: int = 2000) -> str:
    text = str(value or "").strip().replace("\x00", " ")
    return text[: max(1, max_len)].strip()


def _string_list(value: Any, limit: int = 100) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(part or "").strip() for part in value]
    else:
        raw = [str(value or "").strip()]
    out: list[str] = []
    for item in raw:
        if item and item not in out:
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _node_status(warnings: list[str], failed: bool = False) -> str:
    if failed:
        return "failed"
    return "warning" if warnings else "success"


def _trace_row(
    *,
    node_id: str,
    status: str,
    input_summary: dict[str, Any],
    output: Any,
    warnings: list[str],
    started: float,
) -> dict[str, Any]:
    label = next((node["label"] for node in GRAPH_NODES if node["id"] == node_id), node_id)
    return {
        "node_id": node_id,
        "label": label,
        "status": status,
        "input_summary": input_summary,
        "output": output,
        "warnings": warnings,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _trace_output(node_id: str, state: dict[str, Any], result: dict[str, Any]) -> Any:
    if node_id == "base_source_resolve":
        bs = state.get("base_source") or {}
        return {k: bs.get(k) for k in ("scope", "root", "product", "file")}
    if node_id == "ai_sql_draft":
        ai = state.get("ai_sql") or {}
        return {
            "where_sql": _safe_text(ai.get("where_sql"), 400),
            "display_sql": _safe_text(ai.get("display_sql"), 400),
            "selected_columns": list(ai.get("selected_columns") or [])[:50],
            "sort": ai.get("sort") or {},
            "preview_total_rows": ai.get("preview_total_rows"),
            "preview_rows_returned": len(ai.get("preview_rows") or []),
            "sub_trace_rows": len(ai.get("sub_trace") or []),
        }
    if node_id == "join_candidate_select":
        return {"candidates": (state.get("join_candidates") or [])[:8]}
    if node_id == "join_plan_validate":
        plan = state.get("join_plan") or {}
        return {
            "relation_ids": plan.get("relation_ids") or [],
            "join_keys": plan.get("join_keys") or [],
            "steps": (plan.get("steps") or [])[:6],
            "missing_evidence": (plan.get("missing_evidence") or [])[:10],
            "blocked": bool(plan.get("blocked")),
            "single_source": bool(plan.get("single_source")),
        }
    if node_id == "join_execute":
        joined = state.get("joined") or {}
        return {
            "row_count": joined.get("row_count") or 0,
            "columns_count": len(joined.get("columns") or []),
            "blocked": bool(joined.get("blocked")),
            "reason": joined.get("reason") or "",
        }
    if node_id == "output_route":
        route = state.get("output_route") or {}
        return {"mode": route.get("mode"), "reason": _safe_text(route.get("reason"), 240), "llm": route.get("llm") or {}}
    if node_id == "dashboard_draft":
        dash = state.get("dashboard") or {}
        return {
            "skipped": bool(dash.get("skipped")),
            "chart_type": dash.get("chart_type") or "",
            "title": _safe_text(dash.get("title"), 120),
            "points": len(((dash.get("chart_result") or {}).get("points") or [])),
            "llm": dash.get("llm") or {},
            "sub_trace": dash.get("sub_trace") or [],
            "sub_run_id": dash.get("sub_run_id") or "",
        }
    return result


def _node_label(node_id: str) -> str:
    return next((node["label"] for node in GRAPH_NODES if node["id"] == node_id), node_id)


_NODE_EXECUTOR = NodeExecutor(
    trace_output=lambda node_id, state, result: _trace_output(node_id, state, result),
    trace_recorder=TraceRecorder(label_for=_node_label),
)


def _execute_node(
    state: dict[str, Any],
    node_id: str,
    body: Callable[[dict[str, Any], list[str]], dict[str, Any] | None],
    input_summary: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    return _NODE_EXECUTOR.execute(state, node_id, body, input_summary)


def _llm_json(
    *,
    node_id: str,
    system: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    timeout: int = 20,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    llm_info: dict[str, Any] = {
        "available": False,
        "used": False,
        "error": "",
        "node_id": node_id,
        "system": _safe_text(system, 4000),
    }
    warnings: list[str] = []
    plan: dict[str, Any] = {}
    try:
        from core import llm_adapter

        llm_info["available"] = bool(llm_adapter.is_available())
        if not llm_info["available"]:
            warnings.append("LLM is not configured.")
            return plan, llm_info, warnings
        out = llm_adapter.complete_json(
            json.dumps(payload, ensure_ascii=False),
            system=system,
            timeout=timeout,
            max_retries=1,
            schema=schema,
        )
        llm_info["used"] = bool(out.get("ok") and isinstance(out.get("obj"), dict))
        if out.get("error"):
            llm_info["error"] = str(out.get("error") or "")
        if out.get("repaired"):
            llm_info["repaired_json"] = True
        plan = out.get("obj") if isinstance(out.get("obj"), dict) else {}
    except Exception as exc:
        llm_info["error"] = f"{type(exc).__name__}: {exc}"
    if llm_info.get("error"):
        warnings.append(f"LLM failed: {llm_info['error']}")
    return plan, llm_info, warnings


def _base_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    return {
        "natural_language": _safe_text(req.get("natural_language"), 240),
        "root": _safe_text(req.get("root"), 160),
        "product": _safe_text(req.get("product"), 160),
        "file": _safe_text(req.get("file"), 240),
    }


def _prompt_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    return {"natural_language": _safe_text(req.get("natural_language"), 240)}


def _candidate_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    bs = state.get("base_source") or {}
    return {
        "natural_language": _safe_text(req.get("natural_language"), 240),
        "product": _safe_text(bs.get("product"), 80),
    }


def _plan_input(state: dict[str, Any]) -> dict[str, Any]:
    cands = state.get("join_candidates") or []
    return {"candidate_count": len(cands), "candidates": [c.get("label") or c.get("source_id") for c in cands[:6]]}


def _execute_input(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("join_plan") or {}
    return {
        "steps": len(plan.get("steps") or []),
        "blocked": bool(plan.get("blocked")),
        "missing_evidence": (plan.get("missing_evidence") or [])[:4],
    }


def _route_input(state: dict[str, Any]) -> dict[str, Any]:
    joined = state.get("joined") or {}
    return {
        "row_count": joined.get("row_count") or 0,
        "columns_count": len(joined.get("columns") or []),
    }


def _dashboard_input(state: dict[str, Any]) -> dict[str, Any]:
    route = state.get("output_route") or {}
    joined = state.get("joined") or {}
    return {
        "mode": route.get("mode"),
        "row_count": joined.get("row_count") or 0,
        "columns_count": len(joined.get("columns") or []),
    }


def _base_source_resolve(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    req = state.get("request") or {}
    root = _safe_text(req.get("root"), 160)
    product = _safe_text(req.get("product"), 160)
    file = _safe_text(req.get("file"), 240)
    scope = ""
    if root and product:
        scope = "db_product"
    elif file:
        scope = "base"
    else:
        warnings.append("base source slot is required (root/product 또는 file)")
    return {
        "base_source": {
            "scope": scope,
            "root": root,
            "product": product,
            "file": file,
            "blocked": not scope,
        }
    }


def _ai_sql_draft(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    bs = state.get("base_source") or {}
    if bs.get("blocked"):
        warnings.append("ai_sql_draft skipped: base source not resolved")
        return {"ai_sql": {"skipped": True, "where_sql": "", "selected_columns": [], "sort": {}, "sub_trace": [], "preview_rows": [], "preview_columns": []}}
    from core.flowi_units.filebrowser_ai_sql_runtime import run_filebrowser_ai_sql_runtime

    req = state.get("request") or {}
    payload = {
        "natural_language": _safe_text(req.get("natural_language"), 2000),
        "scope": bs.get("scope") or "base",
        "root": bs.get("root") or "",
        "product": bs.get("product") or "",
        "file": bs.get("file") or "",
        "columns": req.get("columns") if isinstance(req.get("columns"), list) else [],
        "dtypes": req.get("dtypes") if isinstance(req.get("dtypes"), dict) else {},
        "sample_rows": [],
        "preferred_selected_columns": req.get("preferred_selected_columns") if isinstance(req.get("preferred_selected_columns"), list) else [],
    }
    res = run_filebrowser_ai_sql_runtime(
        payload,
        username=_safe_text(state.get("username"), 80),
        agent_context=state.get("agent_context") if isinstance(state.get("agent_context"), dict) else None,
    )
    merged = res.get("merged") if isinstance(res.get("merged"), dict) else {}
    preview = res.get("preview") if isinstance(res.get("preview"), dict) else {}
    semantic = res.get("semantic_frame") if isinstance(res.get("semantic_frame"), dict) else {}
    sub_trace = res.get("trace") if isinstance(res.get("trace"), list) else []
    sub_warnings: list[str] = []
    for row in sub_trace:
        if not isinstance(row, dict):
            continue
        for w in row.get("warnings") or []:
            text = _safe_text(w, 240)
            if text and text not in sub_warnings:
                sub_warnings.append(text)
    if not res.get("ok"):
        warnings.append("ai_sql_draft underlying runtime returned ok=false")
    return {
        "ai_sql": {
            "where_sql": _safe_text(merged.get("where_sql"), 1000),
            "display_sql": _safe_text(merged.get("display_sql"), 1000),
            "selected_columns": _string_list(merged.get("selected_columns"), limit=200),
            "sort": merged.get("sort") if isinstance(merged.get("sort"), dict) else {},
            "preview_rows": preview.get("rows") or [],
            "preview_columns": _string_list(preview.get("columns"), limit=200),
            "preview_total_rows": preview.get("total_rows"),
            "semantic_frame": semantic,
            "sub_trace": sub_trace,
            "sub_warnings": sub_warnings,
            "ok": bool(res.get("ok")),
        }
    }


def _join_candidate_select(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    registry = flowi_multisource._load_schema_registry()
    relations = [r for r in registry.get("relations") or [] if isinstance(r, dict)]
    catalog = [r for r in registry.get("column_catalog") or [] if isinstance(r, dict)]
    if not relations and not catalog:
        warnings.append("schema_relations registry is empty")
        return {"join_candidates": [], "join_registry": {"relations": [], "catalog": [], "scored": []}}
    sources = flowi_multisource._relation_sources(relations)
    flowi_multisource._add_catalog_sources(sources, catalog)
    flowi_multisource._attach_catalog_rows(sources, catalog)
    req = state.get("request") or {}
    bs = state.get("base_source") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    product = _safe_text(bs.get("product"), 80)
    try:
        _, relation_hits, _ = flowi_multisource._lookup_prompt_knowledge(prompt)
    except Exception as exc:
        warnings.append(f"knowledge lookup failed: {exc}")
        relation_hits = set()
    scored = flowi_multisource._score_sources(sources, prompt, relation_hits, product)
    candidates = [
        {
            "source_id": s.source_id,
            "label": s.label,
            "source_type": s.source_type,
            "score": s.score,
            "terms": list(s.terms),
        }
        for s in scored
    ]
    if not candidates:
        warnings.append("no scored candidate sources for prompt")
    return {
        "join_candidates": candidates,
        "join_registry": {
            "relations": relations,
            "catalog": catalog,
            "scored": scored,
        },
    }


def _join_plan_validate(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    registry = state.get("join_registry") or {}
    scored = list(registry.get("scored") or [])
    relations = list(registry.get("relations") or [])
    if not scored:
        return {
            "join_plan": {
                "sources": [],
                "relations": [],
                "relation_ids": [],
                "join_keys": [],
                "steps": [],
                "missing_evidence": ["no_candidates"],
                "blocked": True,
                "single_source": False,
            }
        }
    bs = state.get("base_source") or {}
    product_hint = _safe_text(bs.get("product"), 80)
    missing: list[str] = []
    for source in scored:
        source.files, file_warnings = flowi_multisource._resolve_source_files(source, product_hint=product_hint)
        for w in file_warnings:
            if w and w not in missing:
                missing.append(w)
        flowi_multisource._schema_for_source(source)
        for w in source.warnings:
            if w and w not in missing:
                missing.append(w)
    single_source = len(scored) == 1
    confirmed = flowi_multisource._confirmed_relations_between(relations, {s.source_id for s in scored}) if not single_source else []
    if not single_source and not confirmed:
        missing.append("관계 확인 필요: confirmed schema_relation 없음")
    missing.extend(flowi_multisource._attach_join_columns({s.source_id: s for s in scored}, confirmed))
    plan_steps, plan_warnings = flowi_multisource._connectivity_join_plan(scored, confirmed)
    for w in plan_warnings:
        if w and w not in missing:
            missing.append(w)
    if not single_source and len(plan_steps) < len(scored) - 1:
        missing.append("관계 확인 필요: confirmed relation chain 미완성")
    for w in missing:
        if w not in warnings:
            warnings.append(w)
    blocked = bool(missing) and not single_source
    return {
        "join_plan": {
            "sources": [
                {
                    "source_id": s.source_id,
                    "label": s.label,
                    "source_type": s.source_type,
                    "files": [str(fp) for fp in s.files[:3]],
                    "columns_count": len(s.columns),
                    "join_columns": dict(s.join_columns),
                }
                for s in scored
            ],
            "relations": confirmed,
            "relation_ids": [str(r.get("relation_id") or "") for r in confirmed if r.get("relation_id")],
            "join_keys": sorted({flowi_multisource._relation_key(r) for r in confirmed if flowi_multisource._relation_key(r)}),
            "steps": plan_steps,
            "missing_evidence": missing,
            "blocked": blocked,
            "single_source": single_source,
        }
    }


def _join_execute(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    join_plan = state.get("join_plan") or {}
    ai = state.get("ai_sql") or {}
    req = state.get("request") or {}
    try:
        max_rows = max(1, min(int(req.get("max_rows") or 12), 100))
    except (TypeError, ValueError):
        max_rows = 12
    if join_plan.get("blocked"):
        rows = ai.get("preview_rows") or []
        cols = ai.get("selected_columns") or ai.get("preview_columns") or []
        warnings.append("join blocked; falling back to FileBrowser AI SQL preview rows")
        return {
            "joined": {
                "row_count": len(rows),
                "sample_rows": rows[:max_rows],
                "columns": cols,
                "warnings": list(warnings),
                "blocked": True,
                "reason": "join_blocked",
                "fallback": "ai_sql_preview",
            }
        }
    registry = state.get("join_registry") or {}
    scored = list(registry.get("scored") or [])
    if not scored:
        rows = ai.get("preview_rows") or []
        cols = ai.get("selected_columns") or ai.get("preview_columns") or []
        return {
            "joined": {
                "row_count": len(rows),
                "sample_rows": rows[:max_rows],
                "columns": cols,
                "warnings": list(warnings),
                "blocked": False,
                "single_source": True,
                "fallback": "ai_sql_preview",
            }
        }
    plan_steps = join_plan.get("steps") or []
    bs = state.get("base_source") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    product = _safe_text(bs.get("product"), 80)
    try:
        lot_tokens = [lot for lot in flowi_multisource._extract_lot_tokens(prompt) if lot != product.upper()]
    except Exception:
        lot_tokens = []
    try:
        wafer_tokens = flowi_multisource._extract_wafer_tokens(prompt)
    except Exception:
        wafer_tokens = []
    filters = {
        "product": product,
        "root_lot_ids": lot_tokens,
        "wafer_ids": wafer_tokens,
    }
    chart_intent = flowi_multisource._has_chart_intent(prompt)
    column_hits: set[str] = set()
    for source in scored:
        source.selected_columns = flowi_multisource._source_selected_columns(source, prompt, column_hits, chart_intent)
    frames: dict[str, Any] = {}
    for source in scored:
        frame, frame_warnings = flowi_multisource._source_frame(source, filters)
        for w in (source.warnings or []) + (frame_warnings or []):
            text = _safe_text(w, 240)
            if text and text not in warnings:
                warnings.append(text)
        if frame is None:
            return {
                "joined": {
                    "row_count": 0,
                    "sample_rows": [],
                    "columns": [],
                    "warnings": list(warnings),
                    "blocked": True,
                    "reason": "source_readiness",
                }
            }
        frames[source.source_id] = frame
    if len(scored) == 1:
        df = frames[scored[0].source_id]
        df = df.drop([c for c in df.columns if str(c).startswith("__join_")])
        join_warnings: list[str] = []
    else:
        joined_df, join_warnings = flowi_multisource._join_frames(scored, frames, plan_steps)
        for w in join_warnings:
            text = _safe_text(w, 240)
            if text and text not in warnings:
                warnings.append(text)
        if joined_df is None:
            return {
                "joined": {
                    "row_count": 0,
                    "sample_rows": [],
                    "columns": [],
                    "warnings": list(warnings),
                    "blocked": True,
                    "reason": "join_failed",
                }
            }
        df = joined_df
    sample = flowi_multisource._rows_from_df(df, max_rows=max_rows)
    cols = [c for c in df.columns if not str(c).startswith("__join_")]
    return {
        "joined": {
            "row_count": int(df.height),
            "sample_rows": sample,
            "columns": cols,
            "warnings": list(warnings),
            "blocked": False,
            "filters": filters,
        }
    }


def _output_route(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    heuristic_chart = flowi_multisource._has_chart_intent(prompt)
    fallback_mode = "chart" if heuristic_chart else "raw"
    joined = state.get("joined") or {}
    payload = {
        "natural_language": prompt,
        "joined": {
            "row_count": joined.get("row_count") or 0,
            "columns": (joined.get("columns") or [])[:40],
        },
        "candidate_modes": ["raw", "chart"],
        "heuristic_chart_intent": heuristic_chart,
        "response_schema": {"mode": "raw|chart", "reason": "short reason"},
    }
    plan, llm_info, llm_warnings = _llm_json(
        node_id="output_route",
        system=OUTPUT_ROUTE_SYSTEM_PROMPT,
        payload=payload,
        schema={"keys": ["mode", "reason"], "required": [], "properties": {"mode": {}, "reason": {}}},
    )
    for w in llm_warnings:
        if w and w not in warnings:
            warnings.append(w)
    mode = str(plan.get("mode") or "").strip().lower()
    fallback = False
    if mode not in ("raw", "chart"):
        if mode:
            warnings.append(f"output_mode '{mode}' invalid; fallback to {fallback_mode}")
        mode = fallback_mode
        fallback = True
    return {
        "output_route": {
            "mode": mode,
            "reason": _safe_text(plan.get("reason"), 240),
            "llm": llm_info,
            "fallback_mode": fallback_mode,
            "fallback": fallback or not bool(llm_info.get("used")),
        }
    }


def _dashboard_draft(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    route = state.get("output_route") or {}
    if route.get("mode") != "chart":
        return {"dashboard": {"skipped": True, "reason": "mode_not_chart"}}
    from core.flowi_units.dashboard_agent_runtime import run_dashboard_agent_runtime

    joined = state.get("joined") or {}
    rows = joined.get("sample_rows") or []
    columns = list(joined.get("columns") or [])
    if not rows or not columns:
        warnings.append("dashboard_draft skipped: no joined rows/columns")
        return {"dashboard": {"skipped": True, "reason": "no_data"}}
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    res = run_dashboard_agent_runtime(
        {
            "natural_language": prompt,
            "columns": columns,
            "sample_rows": rows,
            "product": (state.get("base_source") or {}).get("product") or req.get("product") or "",
        },
        username=_safe_text(state.get("username"), 80),
        agent_context=state.get("agent_context") if isinstance(state.get("agent_context"), dict) else None,
    )
    for item in res.get("warnings") or []:
        text = _safe_text(item, 240)
        if text and text not in warnings:
            warnings.append(text)
    if not res.get("ok"):
        warnings.append("dashboard_agent underlying runtime returned ok=false")
    chart_result = res.get("chart_result") if isinstance(res.get("chart_result"), dict) else {}
    config = res.get("config") if isinstance(res.get("config"), dict) else {}
    chart_type = _safe_text(res.get("chart_type") or chart_result.get("chart_type"), 40)
    title = _safe_text(chart_result.get("title"), 80) or f"Home Flow-i {chart_type or 'chart'}"
    return {
        "dashboard": {
            "skipped": False,
            "chart_type": chart_type,
            "title": title,
            "config": config or chart_result.get("config") or {},
            "chart_result": chart_result,
            "llm": {
                "delegate": "dashboard_agent",
                "run_id": res.get("run_id") or "",
                "status": res.get("status") or "",
            },
            "fallback": any(bool((row.get("output") or {}).get("fallback")) for row in res.get("trace") or [] if isinstance(row, dict)),
            "sub_trace": deepcopy(res.get("trace") or []),
            "sub_run_id": res.get("run_id") or "",
        }
    }


_NODE_RUNNERS: tuple[tuple[str, Callable[[dict[str, Any], list[str]], dict[str, Any] | None], Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("base_source_resolve", _base_source_resolve, _base_input),
    ("ai_sql_draft", _ai_sql_draft, _prompt_input),
    ("join_candidate_select", _join_candidate_select, _candidate_input),
    ("join_plan_validate", _join_plan_validate, _plan_input),
    ("join_execute", _join_execute, _execute_input),
    ("output_route", _output_route, _route_input),
    ("dashboard_draft", _dashboard_draft, _dashboard_input),
)


def _merge_diff_into_state(state: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    return StateReducer.merge_diff(state, diff)


def _run_with_langgraph(state: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from langgraph.graph import END, StateGraph
    except Exception:
        return None

    try:
        run_state = deepcopy(state)
        graph = StateGraph(_RuntimeState)
        for node_id, body, input_summary in _NODE_RUNNERS:
            graph.add_node(
                node_id,
                lambda current, _node_id=node_id, _body=body, _input=input_summary: _execute_node(
                    current,
                    _node_id,
                    _body,
                    _input,
                ),
            )
        graph.set_entry_point("base_source_resolve")
        for edge in GRAPH_EDGES:
            graph.add_edge(edge["source"], edge["target"])
        graph.add_edge("dashboard_draft", END)
        app = graph.compile()
        return app.invoke(run_state)
    except Exception as exc:
        state.setdefault("runtime_warnings", []).append(
            f"LangGraph runner failed: {type(exc).__name__}: {exc}; sequential fallback runner used."
        )
        return None


def _run_sequential(state: dict[str, Any]) -> dict[str, Any]:
    return run_nodes_sequential(state, _NODE_RUNNERS, _NODE_EXECUTOR)


def run_home_sql_join_dashboard_runtime(
    payload: dict[str, Any],
    *,
    username: str = "",
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req = deepcopy(payload or {})
    prompt = _safe_text(req.get("natural_language") or req.get("prompt"), 2000)
    if not prompt:
        raise HTTPException(status_code=400, detail="natural_language is required")
    req["natural_language"] = prompt
    run_id = "agent_home_sql_join_dashboard_" + uuid.uuid4().hex[:12]
    state: dict[str, Any] = {
        "run_id": run_id,
        "request": req,
        "username": _safe_text(username, 80),
        "agent_context": deepcopy(agent_context or {}),
        "trace": [],
        "runtime_warnings": [],
    }
    final_state = _run_with_langgraph(state)
    if final_state is None:
        final_state = _run_sequential(state)
    trace = list(final_state.get("trace") or [])
    statuses = {str(row.get("node_id")): str(row.get("status") or "pending") for row in trace}
    runtime_warnings = list(final_state.get("runtime_warnings") or [])
    if runtime_warnings:
        for row in trace:
            if row.get("node_id") == "base_source_resolve":
                row.setdefault("warnings", []).extend(runtime_warnings)
                if row.get("status") == "success":
                    row["status"] = "warning"
                statuses["base_source_resolve"] = row["status"]
                break
    base_source = final_state.get("base_source") or {}
    join_plan = final_state.get("join_plan") or {}
    joined = final_state.get("joined") or {}
    route = final_state.get("output_route") or {}
    dashboard = final_state.get("dashboard") or {}
    blocked = bool(base_source.get("blocked") or join_plan.get("blocked") or joined.get("blocked"))
    ok = bool(joined.get("row_count") or joined.get("sample_rows") or joined.get("columns")) and not blocked
    if any(row.get("status") == "failed" for row in trace):
        ok = False
    status = "blocked" if blocked else ("warning" if any(row.get("status") == "warning" for row in trace) else ("success" if ok else "failed"))
    return {
        "ok": ok,
        "status": status,
        "blocked": blocked,
        "run_id": run_id,
        "unit_ai": UNIT_AI_KEY,
        "graph": home_sql_join_dashboard_graph(statuses),
        "trace": trace,
        "base_source": base_source,
        "ai_sql": final_state.get("ai_sql") or {},
        "join_candidates": final_state.get("join_candidates") or [],
        "join_plan": join_plan,
        "joined": joined,
        "output_route": route,
        "dashboard": dashboard,
        "warnings": runtime_warnings,
    }
