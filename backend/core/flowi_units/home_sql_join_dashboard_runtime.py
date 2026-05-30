"""Internal Dashboard Agent source SQL → JOIN orchestration graph.

Reuses FileBrowser AI SQL for the base source SQL draft, schema_relations
based JOIN planning from ``flowi_multisource``, and chart spec drafting via
``dashboard_agent``. The actual SQL/JOIN execution is read-only and uses
Polars LazyFrame helpers from ``flowi_multisource``.
"""
from __future__ import annotations

import json
import operator
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from fastapi import HTTPException

from app_v2.modules.agent_runtime.executor import (
    NodeExecutor,
    StateReducer,
    TraceRecorder,
    run_sequential as run_nodes_sequential,
)
from core import agent_feedback_penalties
from core import agent_semantic_service


UNIT_AI_KEY = "home_sql_join_dashboard"

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "semantic_layer", "label": "용어해석", "phase": "semantic"},
    {"id": "source_resolve", "label": "소스 선택", "phase": "context"},
    {"id": "filebrowser_sql_draft", "label": "FileBrowser SQL 초안", "phase": "llm"},
    {"id": "data_need_decision", "label": "데이터 필요 판단", "phase": "plan"},
    {"id": "join_candidate_select", "label": "JOIN 후보 선택", "phase": "plan"},
    {"id": "join_plan_validate", "label": "JOIN 계획 검증", "phase": "validate"},
    {"id": "data_execute", "label": "데이터 실행", "phase": "execute"},
    {"id": "output_route", "label": "출력 모드 결정", "phase": "llm"},
    {"id": "dashboard_draft", "label": "Dashboard 차트 초안", "phase": "llm"},
)

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "semantic_layer", "target": "source_resolve"},
    {"source": "source_resolve", "target": "filebrowser_sql_draft"},
    {"source": "filebrowser_sql_draft", "target": "data_need_decision"},
    {"source": "data_need_decision", "target": "join_candidate_select"},
    {"source": "join_candidate_select", "target": "join_plan_validate"},
    {"source": "join_plan_validate", "target": "data_execute"},
    {"source": "data_execute", "target": "output_route"},
    {"source": "output_route", "target": "dashboard_draft"},
)

OUTPUT_ROUTE_SYSTEM_PROMPT = (
    "You decide whether the user's natural-language request wants raw rows or a chart. "
    'Return JSON {"mode": "raw"|"chart", "reason": "short reason"}. '
    "Use 'chart' when the request mentions plotting, trends, comparisons, distributions. "
    "Use 'raw' when the request asks for a table, list, counts, or exact values. "
    "Do not return SELECT/FROM/DDL/markdown/reasoning."
)

STATE_DESIGN: dict[str, dict[str, Any]] = {
    "run_id": {"description": "Runtime execution id.", "producer": "runtime", "public": True},
    "request": {"description": "Sanitized prompt and base source slots.", "producer": "runtime", "public": True},
    "semantic_frame": {"description": "Shared semantic frame used for source and chart orchestration.", "producer": "semantic_layer", "public": True},
    "source_resolution": {"description": "Selected FileBrowser source or blocked candidate list when ambiguous.", "producer": "source_resolve", "public": True},
    "base_source": {"description": "Compatibility alias for source_resolution.selected.", "producer": "source_resolve", "public": True},
    "ai_sql": {"description": "FileBrowser AI SQL draft (WHERE/SELECT/SORT) + preview snapshot + sub-trace.", "producer": "filebrowser_sql_draft", "public": True},
    "data_need": {"description": "Single-source vs confirmed-relation JOIN decision.", "producer": "data_need_decision", "public": True},
    "join_candidates": {"description": "Scored second-source candidates from schema_relations registry.", "producer": "join_candidate_select", "public": True},
    "join_plan": {"description": "Confirmed relation chain, JOIN steps, missing_evidence.", "producer": "join_plan_validate", "public": True},
    "joined": {"description": "Single-source preview or Polars JOIN result summary (columns, sample_rows, row_count).", "producer": "data_execute", "public": True},
    "output_route": {"description": "raw vs chart routing decision plus LLM status.", "producer": "output_route", "public": True},
    "dashboard": {"description": "Chart spec + chart_result for Plotly renderer (empty when raw).", "producer": "dashboard_draft", "public": True},
    "node_errors": {"description": "Public node failure messages keyed by node id.", "producer": "runtime", "public": True},
    "trace": {"description": "Append-only public node trace rows.", "producer": "runtime", "public": True},
    "runtime_warnings": {"description": "Runner-level warnings.", "producer": "runtime", "public": True},
}

NODE_METADATA: dict[str, dict[str, Any]] = {
    "semantic_layer": {
        "persona": "Shared semantic resolver for prompt terms before choosing a source.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.natural_language", "request.product", "request.columns"],
        "writes": ["semantic_frame"],
        "shared_state": ["semantic_frame.resolved_columns", "semantic_frame.value_terms", "semantic_frame.unknown_terms"],
        "answer_attach_rule": "Attach public semantic frame only; never raw source rows.",
    },
    "source_resolve": {
        "persona": "Deterministic resolver that prefers explicit root/product/file and blocks ambiguous automatic source choices.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.root", "request.product", "request.file", "semantic_frame", "schema_relations.column_catalog"],
        "writes": ["source_resolution", "base_source"],
        "shared_state": ["source_resolution.selected", "source_resolution.candidates", "source_resolution.needs_input"],
        "answer_attach_rule": "Attach selected source or candidate list only; never scan broad DB rows.",
    },
    "filebrowser_sql_draft": {
        "persona": "Delegator that runs FileBrowser AI SQL runtime to obtain WHERE/SELECT/SORT for the base source.",
        "prompt": {"system": "(delegates to filebrowser_ai_sql graph)", "mode": "delegate"},
        "reads": ["request.natural_language", "source_resolution.selected"],
        "writes": ["ai_sql"],
        "shared_state": ["ai_sql.where_sql", "ai_sql.selected_columns", "ai_sql.sort", "ai_sql.preview_rows"],
        "answer_attach_rule": "Attach SQL strings, selected columns, sort, preview metadata; carry sub-trace.",
    },
    "data_need_decision": {
        "persona": "Deterministic planner that decides whether single-source preview is enough or a confirmed JOIN is required.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.natural_language", "source_resolution", "ai_sql"],
        "writes": ["data_need"],
        "shared_state": ["data_need.needs_join", "data_need.reason", "data_need.blocked"],
        "answer_attach_rule": "Attach single-source/JOIN decision and public reason.",
    },
    "join_candidate_select": {
        "persona": "Deterministic candidate finder using schema_relations registry and prompt term hits.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.natural_language", "source_resolution.selected", "data_need"],
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
    "data_execute": {
        "persona": "Read-only data executor: single-source FileBrowser preview or confirmed-relation Polars JOIN.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["source_resolution", "ai_sql", "data_need", "join_plan"],
        "writes": ["joined"],
        "shared_state": ["joined.row_count", "joined.sample_rows", "joined.columns"],
        "answer_attach_rule": "Attach result summary; rows are capped to the request max_rows.",
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
        "persona": "Delegator that calls Dashboard Agent with resolved rows/columns and preserves source evidence.",
        "prompt": {"system": "(delegates to dashboard_agent graph)", "mode": "delegate"},
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
    semantic_frame: dict[str, Any]
    source_resolution: dict[str, Any]
    base_source: dict[str, Any]
    ai_sql: dict[str, Any]
    data_need: dict[str, Any]
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
    if node_id == "semantic_layer":
        frame = state.get("semantic_frame") or {}
        return {
            "resolved_columns": list(frame.get("resolved_columns") or [])[:20],
            "value_terms": list(frame.get("value_terms") or [])[:12],
            "unknown_terms": list(frame.get("unknown_terms") or [])[:12],
        }
    if node_id == "source_resolve":
        sr = state.get("source_resolution") or {}
        selected = sr.get("selected") if isinstance(sr.get("selected"), dict) else {}
        return {
            "status": sr.get("status") or "",
            "needs_input": bool(sr.get("needs_input")),
            "selected": {k: selected.get(k) for k in ("scope", "root", "product", "file", "source_id") if selected.get(k)},
            "candidates": (sr.get("candidates") or [])[:8],
        }
    if node_id == "filebrowser_sql_draft":
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
    if node_id == "data_need_decision":
        data_need = state.get("data_need") or {}
        return {
            "needs_join": bool(data_need.get("needs_join")),
            "blocked": bool(data_need.get("blocked")),
            "reason": _safe_text(data_need.get("reason"), 240),
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
    if node_id == "data_execute":
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


def _semantic_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    return {
        "natural_language": _safe_text(req.get("natural_language"), 240),
        "product": _safe_text(req.get("product"), 160),
        "columns_count": len(req.get("columns") or []) if isinstance(req.get("columns"), list) else 0,
    }


def _source_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    return {
        "root": _safe_text(req.get("root"), 160),
        "product": _safe_text(req.get("product"), 160),
        "file": _safe_text(req.get("file"), 240),
        "semantic_terms": list((state.get("semantic_frame") or {}).get("value_terms") or [])[:8],
    }


def _prompt_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    return {"natural_language": _safe_text(req.get("natural_language"), 240)}


def _candidate_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    bs = (state.get("source_resolution") or {}).get("selected") or state.get("base_source") or {}
    return {
        "natural_language": _safe_text(req.get("natural_language"), 240),
        "product": _safe_text(bs.get("product"), 80),
        "needs_join": bool((state.get("data_need") or {}).get("needs_join")),
    }


def _plan_input(state: dict[str, Any]) -> dict[str, Any]:
    cands = state.get("join_candidates") or []
    return {"candidate_count": len(cands), "candidates": [c.get("label") or c.get("source_id") for c in cands[:6]]}


def _execute_input(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("join_plan") or {}
    return {
        "steps": len(plan.get("steps") or []),
        "blocked": bool(plan.get("blocked")),
        "single_source": bool(plan.get("single_source")),
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


def _semantic_layer(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    columns = _string_list(req.get("columns"), limit=200)
    dtypes = req.get("dtypes") if isinstance(req.get("dtypes"), dict) else {}
    resolved = agent_semantic_service.resolve(
        prompt,
        columns=columns,
        product=_safe_text(req.get("product"), 160),
        dtypes=dtypes,
        sample_profile=req.get("sample_profile") if isinstance(req.get("sample_profile"), dict) else {},
        source_ref={
            "root": _safe_text(req.get("root"), 160),
            "product": _safe_text(req.get("product"), 160),
            "file": _safe_text(req.get("file"), 240),
        },
    )
    unknown_terms = list(resolved.get("unknown_terms") or [])
    if unknown_terms:
        warnings.append("semantic unknown terms: " + ", ".join(str(x) for x in unknown_terms[:8]))
    return {
        "semantic_frame": {
            "natural_language": prompt,
            "resolved_columns": list(resolved.get("resolved_columns") or []),
            "unknown_column_terms": list(resolved.get("unknown_column_terms") or []),
            "value_terms": list(resolved.get("value_terms") or []),
            "value_catalog_matches": list(resolved.get("value_catalog_matches") or []),
            "synonyms": dict(resolved.get("synonyms") or {}),
            "step_mapping": dict(resolved.get("step_mapping") or {}),
            "unknown_terms": unknown_terms,
            "unknown_term_texts": list(resolved.get("unknown_term_texts") or []),
            "source_catalog_matches": list(resolved.get("source_catalog_matches") or []),
        }
    }


def _product_hint_from_prompt(prompt: str, explicit: str = "") -> str:
    if explicit:
        return _safe_text(explicit, 160)
    text = str(prompt or "")
    for pattern in (
        r"\bproduct\s*[:=]\s*([A-Za-z0-9_.-]{2,80})\b",
        r"\bprod\s*[:=]\s*([A-Za-z0-9_.-]{2,80})\b",
        r"\b(PROD[A-Za-z0-9_.-]{1,60})\b",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return _safe_text(match.group(1), 160)
    return ""


def _source_id_for_selected(selected: dict[str, Any]) -> str:
    source_id = _safe_text(selected.get("source_id"), 240)
    if source_id:
        return source_id
    root = _safe_text(selected.get("root"), 160)
    file = _safe_text(selected.get("file"), 240)
    if root:
        return f"db_{root}"
    if file:
        return f"file_base_root_{file}"
    return ""


def _selected_source_payload(
    *,
    scope: str,
    root: str = "",
    product: str = "",
    file: str = "",
    source_id: str = "",
    label: str = "",
    confidence: str = "explicit",
    score: int = 100,
    terms: list[str] | None = None,
) -> dict[str, Any]:
    clean = {
        "scope": _safe_text(scope, 80) or ("db_product" if root and product else "base"),
        "root": _safe_text(root, 160),
        "product": _safe_text(product, 160),
        "file": _safe_text(file, 240),
        "source_id": _safe_text(source_id, 240),
        "label": _safe_text(label, 240),
        "confidence": confidence,
        "score": int(score or 0),
        "terms": _string_list(terms or [], limit=20),
    }
    if not clean["source_id"]:
        clean["source_id"] = _source_id_for_selected(clean)
    if not clean["label"]:
        clean["label"] = clean["file"] or "/".join(x for x in (clean["root"], clean["product"]) if x) or clean["source_id"]
    return clean


def _candidate_from_source_profile(source: Any, product_hint: str) -> dict[str, Any]:
    source_id = _safe_text(getattr(source, "source_id", ""), 240)
    label = _safe_text(getattr(source, "label", ""), 240) or source_id
    source_type = _safe_text(getattr(source, "source_type", ""), 40)
    root = ""
    product = product_hint
    file = ""
    scope = "base"
    if source_type == "db" or source_id.startswith("db_"):
        root = source_id[3:] if source_id.startswith("db_") else label
        if product and root.upper().endswith("_" + product.upper()):
            root = root[: -(len(product) + 1)]
        scope = "db_product"
    else:
        for prefix in ("file_base_root_", "file_db_root_"):
            if source_id.startswith(prefix):
                file = source_id[len(prefix):]
                break
        if not file:
            file = label
        scope = "base"
    needs = []
    if scope == "db_product" and not product:
        needs.append("product")
    if scope != "db_product" and not file:
        needs.append("file")
    return {
        "source_id": source_id,
        "label": label,
        "source_type": source_type or ("db" if scope == "db_product" else "file"),
        "scope": scope,
        "root": root,
        "product": product,
        "file": file,
        "score": int(getattr(source, "score", 0) or 0),
        "terms": list(getattr(source, "terms", []) or [])[:20],
        "needs": needs,
        "viable": not needs,
        "evidence": "schema_relations",
    }


def _path_candidate(path: Path, *, root: Path, source_root: str, prompt_norm: str, product_hint: str) -> dict[str, Any] | None:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except Exception:
        return None
    label = str(rel)
    key = re.sub(r"[^a-z0-9]+", "", label.lower())
    stem = re.sub(r"[^a-z0-9]+", "", path.stem.lower())
    if not key or (key not in prompt_norm and stem not in prompt_norm):
        return None
    score = 4 + (2 if stem and stem in prompt_norm else 0)
    return {
        "source_id": f"file_{source_root}_{label}",
        "label": label,
        "source_type": "file",
        "scope": "base",
        "root": "",
        "product": product_hint,
        "file": label,
        "score": score,
        "terms": [path.stem],
        "needs": [],
        "viable": True,
        "evidence": "filebrowser_base_file",
    }


def _filebrowser_source_candidates(prompt: str, product_hint: str) -> list[dict[str, Any]]:
    from core.paths import PATHS

    prompt_norm = re.sub(r"[^a-z0-9]+", "", str(prompt or "").lower())
    candidates: list[dict[str, Any]] = []
    db_root = Path(PATHS.db_root)
    if db_root.is_dir():
        for child in sorted(db_root.iterdir(), key=lambda p: p.name.lower())[:200]:
            if child.name.startswith(".") or child.name.startswith("_"):
                continue
            name_norm = re.sub(r"[^a-z0-9]+", "", child.name.lower())
            if child.is_dir() and name_norm and name_norm in prompt_norm:
                candidates.append({
                    "source_id": f"db_{child.name}",
                    "label": child.name,
                    "source_type": "db",
                    "scope": "db_product",
                    "root": child.name,
                    "product": product_hint,
                    "file": "",
                    "score": 4,
                    "terms": [child.name],
                    "needs": [] if product_hint else ["product"],
                    "viable": bool(product_hint),
                    "evidence": "filebrowser_root",
                })
            elif child.is_file() and child.suffix.lower() in {".parquet", ".csv"}:
                cand = _path_candidate(child, root=db_root, source_root="db_root", prompt_norm=prompt_norm, product_hint=product_hint)
                if cand:
                    candidates.append(cand)
    base_root = Path(PATHS.base_root)
    if base_root.is_dir() and base_root != db_root:
        for child in sorted(base_root.iterdir(), key=lambda p: p.name.lower())[:200]:
            if child.is_file() and child.suffix.lower() in {".parquet", ".csv"}:
                cand = _path_candidate(child, root=base_root, source_root="base_root", prompt_norm=prompt_norm, product_hint=product_hint)
                if cand:
                    candidates.append(cand)
    return candidates


def _source_resolve_candidates(prompt: str, product_hint: str, warnings: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from core import flowi_multisource

    registry = flowi_multisource._load_schema_registry()
    relations = [r for r in registry.get("relations") or [] if isinstance(r, dict)]
    catalog = [r for r in registry.get("column_catalog") or [] if isinstance(r, dict)]
    candidates: list[dict[str, Any]] = []
    if relations or catalog:
        sources = flowi_multisource._relation_sources(relations)
        flowi_multisource._add_catalog_sources(sources, catalog)
        flowi_multisource._attach_catalog_rows(sources, catalog)
        try:
            _, relation_hits, _ = flowi_multisource._lookup_prompt_knowledge(prompt)
        except Exception as exc:
            warnings.append(f"knowledge lookup failed: {exc}")
            relation_hits = set()
        for source in flowi_multisource._score_sources(sources, prompt, relation_hits, product_hint):
            candidates.append(_candidate_from_source_profile(source, product_hint))
    candidates.extend(_filebrowser_source_candidates(prompt, product_hint))
    by_key: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        key = cand.get("source_id") or f"{cand.get('scope')}:{cand.get('root')}:{cand.get('product')}:{cand.get('file')}"
        if key not in by_key or int(cand.get("score") or 0) > int(by_key[key].get("score") or 0):
            by_key[key] = cand
    deduped = sorted(by_key.values(), key=lambda row: (int(row.get("score") or 0), str(row.get("label") or "")), reverse=True)
    return deduped[:8], {"relations": relations, "catalog": catalog}


def _source_resolve(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    root = _safe_text(req.get("root"), 160)
    product = _safe_text(req.get("product"), 160)
    file = _safe_text(req.get("file"), 240)
    scope = _safe_text(req.get("scope"), 80)
    if root and product:
        selected = _selected_source_payload(scope="db_product", root=root, product=product, source_id=f"db_{root}", confidence="explicit")
        resolution = {"status": "resolved", "needs_input": False, "selected": selected, "candidates": [selected], "reason": "explicit root/product"}
        return {"source_resolution": resolution, "base_source": selected}
    if file:
        file_scope = scope or ("rootpq" if file.lower().endswith(".parquet") else "base")
        selected = _selected_source_payload(scope=file_scope, file=file, source_id=f"file_base_root_{file}", confidence="explicit")
        resolution = {"status": "resolved", "needs_input": False, "selected": selected, "candidates": [selected], "reason": "explicit file"}
        return {"source_resolution": resolution, "base_source": selected}

    product_hint = _product_hint_from_prompt(prompt, product)
    candidates, registry = _source_resolve_candidates(prompt, product_hint, warnings)
    viable = [cand for cand in candidates if cand.get("viable")]
    selected: dict[str, Any] = {}
    reason = ""
    needs_input = True
    if len(viable) == 1:
        selected = _selected_source_payload(
            scope=str(viable[0].get("scope") or "base"),
            root=str(viable[0].get("root") or ""),
            product=str(viable[0].get("product") or ""),
            file=str(viable[0].get("file") or ""),
            source_id=str(viable[0].get("source_id") or ""),
            label=str(viable[0].get("label") or ""),
            confidence="auto_high",
            score=int(viable[0].get("score") or 0),
            terms=list(viable[0].get("terms") or []),
        )
        reason = "single high-confidence source candidate"
        needs_input = False
    elif viable:
        top_score = int(viable[0].get("score") or 0)
        second = int(viable[1].get("score") or 0) if len(viable) > 1 else 0
        if top_score >= 6 and top_score >= second + 3:
            selected = _selected_source_payload(
                scope=str(viable[0].get("scope") or "base"),
                root=str(viable[0].get("root") or ""),
                product=str(viable[0].get("product") or ""),
                file=str(viable[0].get("file") or ""),
                source_id=str(viable[0].get("source_id") or ""),
                label=str(viable[0].get("label") or ""),
                confidence="auto_high",
                score=top_score,
                terms=list(viable[0].get("terms") or []),
            )
            reason = "top candidate clearly outranks alternatives"
            needs_input = False
    if needs_input:
        reason = "source is ambiguous" if candidates else "source candidate not found"
        warnings.append("source_resolve needs input: " + reason)
    resolution = {
        "status": "needs_input" if needs_input else "resolved",
        "needs_input": needs_input,
        "selected": selected,
        "candidates": candidates,
        "reason": reason,
        "question": "분석할 FileBrowser source(root/product 또는 file)를 선택해 주세요." if needs_input else "",
        "join_registry_summary": {"relations": len(registry.get("relations") or []), "catalog": len(registry.get("catalog") or [])},
    }
    if selected:
        return {"source_resolution": resolution, "base_source": selected}
    return {"source_resolution": resolution, "base_source": {"blocked": True}}


def _filebrowser_sql_draft(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    source_resolution = state.get("source_resolution") or {}
    bs = source_resolution.get("selected") if isinstance(source_resolution.get("selected"), dict) else (state.get("base_source") or {})
    if source_resolution.get("needs_input") or bs.get("blocked"):
        warnings.append("filebrowser_sql_draft skipped: source not resolved")
        return {"ai_sql": {"skipped": True, "where_sql": "", "selected_columns": [], "sort": {}, "sub_trace": [], "preview_rows": [], "preview_columns": []}}
    from core.flowi_units.filebrowser_ai_sql_runtime import run_filebrowser_ai_sql_runtime

    req = state.get("request") or {}
    try:
        max_rows = max(1, min(int(req.get("max_rows") or 50), 100))
    except (TypeError, ValueError):
        max_rows = 50
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
        "include_preview_rows": True,
        "preview_row_limit": max_rows,
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
        warnings.append("filebrowser_sql_draft underlying runtime returned ok=false")
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
            "request": {k: payload.get(k) for k in ("scope", "root", "product", "file")},
        }
    }


def _data_need_decision(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    req = state.get("request") or {}
    source_resolution = state.get("source_resolution") or {}
    if source_resolution.get("needs_input"):
        return {"data_need": {"needs_join": False, "blocked": True, "reason": "source_needs_input"}}
    prompt = _safe_text(req.get("natural_language"), 2000)
    needs_join = bool(flowi_multisource._has_multisource_intent(prompt))
    if not needs_join:
        return {"data_need": {"needs_join": False, "blocked": False, "reason": "single source is sufficient"}}
    return {"data_need": {"needs_join": True, "blocked": False, "reason": "prompt requests multiple sources or JOIN"}}


def _join_candidate_select(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    selected_source = (state.get("source_resolution") or {}).get("selected") or state.get("base_source") or {}
    data_need = state.get("data_need") or {}
    if (state.get("source_resolution") or {}).get("needs_input"):
        warnings.append("join_candidate_select skipped: source needs input")
        return {"join_candidates": [], "join_registry": {"relations": [], "catalog": [], "scored": []}}
    registry = flowi_multisource._load_schema_registry()
    relations = [r for r in registry.get("relations") or [] if isinstance(r, dict)]
    catalog = [r for r in registry.get("column_catalog") or [] if isinstance(r, dict)]
    sources = flowi_multisource._relation_sources(relations)
    flowi_multisource._add_catalog_sources(sources, catalog)
    flowi_multisource._attach_catalog_rows(sources, catalog)
    selected_sid = _source_id_for_selected(selected_source)

    def ensure_selected_profile() -> Any:
        if selected_sid and selected_sid in sources:
            return sources[selected_sid]
        root = _safe_text(selected_source.get("root"), 160)
        file = _safe_text(selected_source.get("file"), 240)
        label = _safe_text(selected_source.get("label"), 240) or root or file or selected_sid
        for source in sources.values():
            source_key = flowi_multisource._norm(getattr(source, "label", "")) or flowi_multisource._norm(getattr(source, "source_id", ""))
            if root and source_key == flowi_multisource._norm(root):
                return source
            if file and source_key == flowi_multisource._norm(Path(file).stem):
                return source
        source_type = "db" if root else "file"
        sid = selected_sid or (f"db_{root}" if root else f"file_base_root_{file}")
        profile = flowi_multisource.SourceProfile(source_id=sid, source_type=source_type, label=label)
        sources[sid] = profile
        return profile

    base_profile = ensure_selected_profile() if selected_source else None
    if base_profile is not None:
        base_profile.score = max(int(getattr(base_profile, "score", 0) or 0), 100)
        base_profile.terms = list(dict.fromkeys([*(getattr(base_profile, "terms", []) or []), "selected_source"]))

    if not data_need.get("needs_join"):
        scored = [base_profile] if base_profile is not None else []
        candidates = [
            {
                "source_id": s.source_id,
                "label": s.label,
                "source_type": s.source_type,
                "score": s.score,
                "terms": list(s.terms),
                "selected_base": True,
            }
            for s in scored
        ]
        return {
            "join_candidates": candidates,
            "join_registry": {"relations": relations, "catalog": catalog, "scored": scored},
        }
    if not relations and not catalog and base_profile is None:
        warnings.append("schema_relations registry is empty")
        return {"join_candidates": [], "join_registry": {"relations": [], "catalog": [], "scored": []}}
    req = state.get("request") or {}
    bs = selected_source
    prompt = _safe_text(req.get("natural_language"), 2000)
    product = _safe_text(bs.get("product"), 80)
    try:
        _, relation_hits, _ = flowi_multisource._lookup_prompt_knowledge(prompt)
    except Exception as exc:
        warnings.append(f"knowledge lookup failed: {exc}")
        relation_hits = set()
    scored = flowi_multisource._score_sources(sources, prompt, relation_hits, product)
    if base_profile is not None and all(s.source_id != base_profile.source_id for s in scored):
        scored.insert(0, base_profile)
    if base_profile is not None:
        scored.sort(key=lambda s: (s.source_id != base_profile.source_id, -int(getattr(s, "score", 0) or 0), s.label))
    candidates = [
        {
            "source_id": s.source_id,
            "label": s.label,
            "source_type": s.source_type,
            "score": s.score,
            "terms": list(s.terms),
            "selected_base": base_profile is not None and s.source_id == base_profile.source_id,
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

    source_resolution = state.get("source_resolution") or {}
    if source_resolution.get("needs_input"):
        return {
            "join_plan": {
                "sources": [],
                "relations": [],
                "relation_ids": [],
                "join_keys": [],
                "steps": [],
                "missing_evidence": ["source_needs_input"],
                "blocked": True,
                "single_source": False,
            }
        }
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
    single_source = len(scored) == 1 or not bool((state.get("data_need") or {}).get("needs_join"))
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


def _data_execute(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import flowi_multisource

    join_plan = state.get("join_plan") or {}
    ai = state.get("ai_sql") or {}
    data_need = state.get("data_need") or {}
    req = state.get("request") or {}
    try:
        max_rows = max(1, min(int(req.get("max_rows") or 12), 100))
    except (TypeError, ValueError):
        max_rows = 12
    if join_plan.get("blocked") or data_need.get("blocked"):
        warnings.append("data_execute skipped: source or confirmed JOIN evidence is missing")
        return {
            "joined": {
                "row_count": 0,
                "sample_rows": [],
                "columns": [],
                "warnings": list(warnings),
                "blocked": True,
                "reason": "join_blocked" if join_plan.get("blocked") else "source_needs_input",
            }
        }
    if not data_need.get("needs_join") or join_plan.get("single_source"):
        rows = ai.get("preview_rows") or []
        cols = ai.get("selected_columns") or ai.get("preview_columns") or []
        total = ai.get("preview_total_rows")
        try:
            row_count = int(total) if total is not None else len(rows)
        except (TypeError, ValueError):
            row_count = len(rows)
        return {
            "joined": {
                "row_count": row_count,
                "sample_rows": rows[:max_rows],
                "columns": cols,
                "warnings": list(warnings),
                "blocked": False,
                "single_source": True,
                "fallback": "filebrowser_preview",
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


def _source_evidence(state: dict[str, Any], dashboard_trace: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    source_resolution = state.get("source_resolution") or {}
    selected = source_resolution.get("selected") if isinstance(source_resolution.get("selected"), dict) else {}
    join_plan = state.get("join_plan") or {}
    ai = state.get("ai_sql") or {}
    source_ids = [
        str(row.get("source_id") or "")
        for row in (join_plan.get("sources") or [])
        if isinstance(row, dict) and row.get("source_id")
    ]
    if not source_ids and selected.get("source_id"):
        source_ids = [str(selected.get("source_id"))]
    return {
        "source_ids": list(dict.fromkeys(source_ids)),
        "selected_source": {k: selected.get(k) for k in ("scope", "root", "product", "file", "source_id", "label") if selected.get(k)},
        "relation_ids": list(join_plan.get("relation_ids") or []),
        "join_keys": list(join_plan.get("join_keys") or []),
        "single_source": bool(join_plan.get("single_source")),
        "data_need": state.get("data_need") or {},
        "sql_summary": {
            "display_sql": _safe_text(ai.get("display_sql"), 1000),
            "where_sql": _safe_text(ai.get("where_sql"), 1000),
            "selected_columns": list(ai.get("selected_columns") or [])[:80],
            "sort": ai.get("sort") if isinstance(ai.get("sort"), dict) else {},
        },
        "sub_trace": {
            "filebrowser_ai_sql": deepcopy(ai.get("sub_trace") or []),
            "dashboard_agent": deepcopy(dashboard_trace or []),
        },
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
    if res.get("needs_input") or res.get("blocked"):
        question = _safe_text(res.get("question"), 240) or "차트 축 입력이 필요합니다."
        warnings.append(question)
        return {
            "dashboard": {
                "skipped": True,
                "blocked": True,
                "needs_input": True,
                "reason": "dashboard_agent_needs_input",
                "question": question,
                "axis_requirements": res.get("axis_requirements") if isinstance(res.get("axis_requirements"), dict) else {},
                "sub_trace": deepcopy(res.get("trace") or []),
                "sub_run_id": res.get("run_id") or "",
            }
        }
    if not res.get("ok"):
        warnings.append("dashboard_agent underlying runtime returned ok=false")
    sub_trace = deepcopy(res.get("trace") or [])
    source_evidence = _source_evidence(state, sub_trace)
    chart_result = deepcopy(res.get("chart_result")) if isinstance(res.get("chart_result"), dict) else {}
    config = deepcopy(res.get("config")) if isinstance(res.get("config"), dict) else {}
    result_config = deepcopy(chart_result.get("config")) if isinstance(chart_result.get("config"), dict) else {}
    chart_config = deepcopy(chart_result.get("chart_config")) if isinstance(chart_result.get("chart_config"), dict) else {}
    result_config["source_evidence"] = source_evidence
    chart_config["source_evidence"] = source_evidence
    chart_result["config"] = result_config
    chart_result["chart_config"] = chart_config or result_config
    config["source_evidence"] = source_evidence
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
            "source_evidence": source_evidence,
            "sub_trace": sub_trace,
            "sub_run_id": res.get("run_id") or "",
        }
    }


_NODE_RUNNERS: tuple[tuple[str, Callable[[dict[str, Any], list[str]], dict[str, Any] | None], Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("semantic_layer", _semantic_layer, _semantic_input),
    ("source_resolve", _source_resolve, _source_input),
    ("filebrowser_sql_draft", _filebrowser_sql_draft, _prompt_input),
    ("data_need_decision", _data_need_decision, _prompt_input),
    ("join_candidate_select", _join_candidate_select, _candidate_input),
    ("join_plan_validate", _join_plan_validate, _plan_input),
    ("data_execute", _data_execute, _execute_input),
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
        graph.set_entry_point("semantic_layer")
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
            if row.get("node_id") == "semantic_layer":
                row.setdefault("warnings", []).extend(runtime_warnings)
                if row.get("status") == "success":
                    row["status"] = "warning"
                statuses["semantic_layer"] = row["status"]
                break
    source_resolution = final_state.get("source_resolution") or {}
    base_source = final_state.get("base_source") or {}
    join_plan = final_state.get("join_plan") or {}
    joined = final_state.get("joined") or {}
    route = final_state.get("output_route") or {}
    dashboard = final_state.get("dashboard") or {}
    chart_result = dashboard.get("chart_result") if isinstance(dashboard.get("chart_result"), dict) else {}
    blocked = bool(source_resolution.get("needs_input") or base_source.get("blocked") or join_plan.get("blocked") or joined.get("blocked") or dashboard.get("blocked"))
    ok = bool(joined.get("row_count") or joined.get("sample_rows") or joined.get("columns")) and not blocked
    if any(row.get("status") == "failed" for row in trace):
        ok = False
    status = "blocked" if blocked else ("warning" if any(row.get("status") == "warning" for row in trace) else ("success" if ok else "failed"))
    result = {
        "ok": ok,
        "status": status,
        "blocked": blocked,
        "needs_input": blocked,
        "question": (
            _safe_text(source_resolution.get("question"), 240)
            or _safe_text(dashboard.get("question"), 240)
        ),
        "run_id": run_id,
        "unit_ai": UNIT_AI_KEY,
        "graph": home_sql_join_dashboard_graph(statuses),
        "trace": trace,
        "semantic_frame": final_state.get("semantic_frame") or {},
        "source_resolution": source_resolution,
        "base_source": base_source,
        "ai_sql": final_state.get("ai_sql") or {},
        "data_need": final_state.get("data_need") or {},
        "join_candidates": final_state.get("join_candidates") or [],
        "join_plan": join_plan,
        "joined": joined,
        "output_route": route,
        "dashboard": dashboard,
        "chart_type": dashboard.get("chart_type") or chart_result.get("chart_type") or "",
        "config": dashboard.get("config") or chart_result.get("config") or {},
        "chart_result": chart_result,
        "warnings": runtime_warnings,
    }
    return agent_feedback_penalties.annotate_result(UNIT_AI_KEY, result)
