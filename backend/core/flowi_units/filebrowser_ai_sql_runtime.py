"""Agent-visible FileBrowser AI SQL runtime graph.

The FileBrowser router remains the owner of data access, SQL validation, and
read-only preview behavior. This module wraps those helpers in a small public
trace that the Agent tab can render.
"""
from __future__ import annotations

import json
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException


UNIT_AI_KEY = "filebrowser_ai_sql"

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "context_sample", "label": "용어해석 준비", "phase": "context"},
    {"id": "semantic_layer", "label": "용어해석", "phase": "semantic"},
    {"id": "filter_draft", "label": "filter SQL 생성", "phase": "llm"},
    {"id": "column_draft", "label": "표시 컬럼 생성", "phase": "llm"},
    {"id": "merge", "label": "병합", "phase": "validate"},
    {"id": "preview_apply", "label": "preview 적용", "phase": "preview"},
)

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "context_sample", "target": "semantic_layer"},
    {"source": "semantic_layer", "target": "filter_draft"},
    {"source": "filter_draft", "target": "column_draft"},
    {"source": "column_draft", "target": "merge"},
    {"source": "merge", "target": "preview_apply"},
)


def filebrowser_ai_sql_graph(statuses: dict[str, str] | None = None) -> dict[str, Any]:
    statuses = statuses or {}
    return {
        "nodes": [
            {
                **node,
                "status": statuses.get(node["id"], "pending"),
            }
            for node in GRAPH_NODES
        ],
        "edges": [dict(edge) for edge in GRAPH_EDGES],
    }


def _fb():
    from routers import filebrowser as fb

    return fb


def _safe_text(value: Any, max_len: int = 2000) -> str:
    try:
        return _fb()._cache_safe_text(value, max_len)
    except Exception:
        text = str(value or "").strip().replace("\x00", " ")
        return text[:max(1, max_len)].strip()


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


def _target_summary(req: dict[str, Any]) -> dict[str, str]:
    return {
        "scope": _safe_text(req.get("scope") or "db_product", 80),
        "root": _safe_text(req.get("root"), 160),
        "product": _safe_text(req.get("product"), 160),
        "file": _safe_text(req.get("file"), 240),
    }


def _trace_output(node_id: str, state: dict[str, Any], result: dict[str, Any]) -> Any:
    if node_id == "context_sample":
        return {
            "source": (state.get("sample_profile") or {}).get("source") or "",
            "columns_count": len(state.get("columns") or []),
            "sample_rows": state.get("sample_rows") or [],
            "sample_profile": {
                key: (state.get("sample_profile") or {}).get(key)
                for key in ("rows_sampled", "columns_scanned", "columns_profiled", "sampling_policy")
            },
        }
    if node_id == "semantic_layer":
        return state.get("semantic_frame") or {}
    if node_id == "filter_draft":
        return state.get("filter") or {}
    if node_id == "column_draft":
        return state.get("columns_result") or {}
    if node_id == "merge":
        return state.get("merged") or {}
    if node_id == "preview_apply":
        return state.get("preview") or {}
    return result


def _append_trace(
    state: dict[str, Any],
    *,
    node_id: str,
    status: str,
    input_summary: dict[str, Any],
    output: Any,
    warnings: list[str],
    started: float,
) -> None:
    label = next((node["label"] for node in GRAPH_NODES if node["id"] == node_id), node_id)
    state.setdefault("trace", []).append({
        "node_id": node_id,
        "label": label,
        "status": status,
        "input_summary": input_summary,
        "output": output,
        "warnings": warnings,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    })


def _node_status(warnings: list[str], failed: bool = False) -> str:
    if failed:
        return "failed"
    return "warning" if warnings else "success"


def _execute_node(
    state: dict[str, Any],
    node_id: str,
    body: Callable[[dict[str, Any], list[str]], dict[str, Any] | None],
    input_summary: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    warnings: list[str] = []
    failed = False
    result: dict[str, Any] = {}
    try:
        result = body(state, warnings) or {}
        state.update(result)
    except Exception as exc:
        failed = True
        warnings.append(f"{type(exc).__name__}: {exc}")
        state.setdefault("node_errors", {})[node_id] = warnings[-1]
    status = _node_status(warnings, failed)
    _append_trace(
        state,
        node_id=node_id,
        status=status,
        input_summary=input_summary(state),
        output=_trace_output(node_id, state, result),
        warnings=warnings,
        started=started,
    )
    return state


def _context_input(state: dict[str, Any]) -> dict[str, Any]:
    return _target_summary(state.get("request") or {})


def _prompt_input(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "natural_language": _safe_text((state.get("request") or {}).get("natural_language"), 240),
        "columns_count": len(state.get("columns") or []),
        "sample_rows": len(state.get("sample_rows") or []),
    }


def _preview_input(state: dict[str, Any]) -> dict[str, Any]:
    return {
        **_target_summary(state.get("request") or {}),
        "sql": (state.get("merged") or {}).get("sql") or "",
        "selected_columns": (state.get("merged") or {}).get("selected_columns") or [],
    }


def _context_sample(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fb = _fb()
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    preferred = _string_list(req.get("preferred_selected_columns"))
    columns, dtypes, sample_rows, sample_profile, context_warnings = fb._ai_sql_context_from_source(
        scope=_safe_text(req.get("scope"), 80),
        root=_safe_text(req.get("root"), 160),
        product=_safe_text(req.get("product"), 160),
        file=_safe_text(req.get("file"), 240),
        columns=req.get("columns") or [],
        dtypes=req.get("dtypes") or {},
        sample_rows=req.get("sample_rows") or [],
        prompt=prompt,
        preferred_selected_columns=preferred,
    )
    warnings.extend(str(item) for item in (context_warnings or []))
    sample_rows = fb._safe_sample_rows(sample_rows or [], max_rows=10, max_cols=80, max_value_len=120)
    sample_profile = fb._build_ai_sql_sample_profile(
        columns,
        dtypes,
        sample_rows,
        source=(sample_profile or {}).get("source") or "request",
        source_sampled=bool((sample_profile or {}).get("source_sampled")),
        warnings=warnings,
        prompt=prompt,
        preferred_selected_columns=preferred,
        max_rows=10,
    )
    if not columns:
        warnings.append("No schema columns were resolved for this target.")
    return {
        "columns": list(columns or []),
        "dtypes": dict(dtypes or {}),
        "sample_rows": sample_rows,
        "sample_profile": sample_profile,
    }


def _semantic_aliases(columns: list[str], resolved_columns: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for col in resolved_columns[:20]:
        aliases = [col]
        spaced = col.replace("_", " ")
        if spaced != col:
            aliases.append(spaced)
        compact = col.replace("_", "")
        if compact and compact not in aliases:
            aliases.append(compact)
        out[col] = aliases
    if not out:
        for col in columns[:8]:
            out[col] = [col, col.replace("_", " ")]
    return out


def _semantic_layer(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fb = _fb()
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    columns = list(state.get("columns") or [])
    resolved_columns, unknown_terms = fb._resolve_ai_sql_prompt_columns(prompt, columns)
    if unknown_terms:
        warnings.append("Unknown column-like terms: " + ", ".join(unknown_terms[:8]))
    try:
        value_terms = fb._ai_sql_prompt_priority_values(prompt, columns)[:20]
    except Exception:
        value_terms = []
    try:
        step_mapping = fb._public_ai_sql_step_mapping_context(
            fb._ai_sql_step_mapping_context(prompt, columns, _safe_text(req.get("product"), 160))
        )
    except Exception:
        step_mapping = {}
    semantic_frame = {
        "natural_language": prompt,
        "resolved_columns": resolved_columns,
        "unknown_column_terms": unknown_terms,
        "value_terms": value_terms,
        "synonyms": _semantic_aliases(columns, resolved_columns),
        "step_mapping": step_mapping,
        "source": _target_summary(req),
    }
    return {"semantic_frame": semantic_frame}


def _llm_json(
    *,
    node_id: str,
    system: str,
    payload: dict[str, Any],
    schema: dict[str, Any],
    timeout: int = 20,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    llm_info: dict[str, Any] = {"available": False, "used": False, "error": "", "node_id": node_id}
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


def _filter_draft(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fb = _fb()
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    columns = list(state.get("columns") or [])
    context = _target_summary(req)
    step_mapping_context = fb._ai_sql_step_mapping_context(prompt, columns, context.get("product") or "")
    plan, llm_info, llm_warnings = _llm_json(
        node_id="filter_draft",
        system=(
            "You create only a Flow FileBrowser read-only WHERE/filter expression. "
            "Return JSON with sql, resolved_columns, resolved_values, and notes. "
            "Do not return SELECT, FROM, JOIN, ORDER BY, LIMIT, DDL, DML, comments, semicolons, markdown, or reasoning."
        ),
        payload={
            "natural_language": prompt,
            "columns": columns[:200],
            "dtypes": state.get("dtypes") or {},
            "sample_rows": state.get("sample_rows") or [],
            "sample_profile": state.get("sample_profile") or {},
            "semantic_frame": state.get("semantic_frame") or {},
            "step_mapping_context": fb._public_ai_sql_step_mapping_context(step_mapping_context),
            "context": context,
            "response_schema": {
                "sql": "column = 'value' AND other_col > 0",
                "resolved_columns": ["column"],
                "resolved_values": ["value"],
                "notes": "short public note",
            },
        },
        schema={
            "keys": ["sql", "resolved_columns", "resolved_values", "notes"],
            "required": [],
            "properties": {"sql": {}, "resolved_columns": {}, "resolved_values": {}, "notes": {}},
        },
    )
    warnings.extend(llm_warnings)
    raw_sql = _safe_text(plan.get("sql"), 1000)
    fallback = False
    try:
        raw_sql = fb._merge_ai_sql_step_mapping_filter(raw_sql, step_mapping_context, warnings)
        if fb._llm_misread_hash_wafer(prompt, raw_sql, columns):
            raise ValueError("Prompt #N token must be interpreted as wafer_id, not lot_id text")
        sql, validate_warnings = fb._validate_ai_sql_filter(raw_sql, columns)
        warnings.extend(validate_warnings)
    except Exception as exc:
        fallback = True
        warnings.append(f"filter_draft SQL rejected: {exc}")
        try:
            sql, validate_warnings = fb._validate_ai_sql_filter(
                fb._fallback_ai_sql(prompt, columns, product=context.get("product") or "", step_mapping_context=step_mapping_context),
                columns,
            )
            warnings.extend(validate_warnings)
            if sql:
                warnings.append("deterministic fallback used")
        except Exception as fallback_exc:
            sql = ""
            warnings.append(f"deterministic fallback failed: {fallback_exc}")
    resolved_values = [str(v) for v in (plan.get("resolved_values") or []) if str(v or "").strip()][:50]
    return {
        "filter": {
            "sql": sql,
            "warnings": list(warnings),
            "llm": llm_info,
            "fallback": fallback or not bool(llm_info.get("used")),
            "resolved_columns": [str(v) for v in (plan.get("resolved_columns") or []) if str(v or "").strip()][:50],
            "resolved_values": resolved_values,
            "notes": _safe_text(plan.get("notes"), 400),
        }
    }


def _column_draft(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fb = _fb()
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    columns = list(state.get("columns") or [])
    preferred = _string_list(req.get("preferred_selected_columns"))
    plan, llm_info, llm_warnings = _llm_json(
        node_id="column_draft",
        system=(
            "You choose display columns for Flow FileBrowser preview. "
            "Return JSON with selected_columns only. Use only provided columns. "
            "Return an empty list when the user did not ask for a projection."
        ),
        payload={
            "natural_language": prompt,
            "columns": columns[:300],
            "dtypes": state.get("dtypes") or {},
            "sample_rows": state.get("sample_rows") or [],
            "sample_profile": state.get("sample_profile") or {},
            "semantic_frame": state.get("semantic_frame") or {},
            "preferred_selected_columns": preferred,
            "response_schema": {"selected_columns": ["column", "other_col"], "notes": "short public note"},
        },
        schema={
            "keys": ["selected_columns", "notes"],
            "required": [],
            "properties": {"selected_columns": {}, "notes": {}},
        },
    )
    warnings.extend(llm_warnings)
    selected = fb._filter_ai_sql_selected_columns(
        _string_list(plan.get("selected_columns"), limit=300),
        columns,
        warnings,
        "column_draft",
    )
    if not selected:
        selected = fb._filter_ai_sql_selected_columns(
            preferred or fb._fallback_selected_columns_from_prompt(prompt, columns),
            columns,
            warnings,
            "column_draft_fallback",
        )
    return {
        "columns_result": {
            "selected_columns": selected,
            "warnings": list(warnings),
            "llm": llm_info,
            "fallback": not bool(llm_info.get("used")),
            "notes": _safe_text(plan.get("notes"), 400),
        }
    }


def _merge(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fb = _fb()
    columns = list(state.get("columns") or [])
    filter_result = state.get("filter") or {}
    column_result = state.get("columns_result") or {}
    sql = _safe_text(filter_result.get("sql"), 1000)
    try:
        sql, validate_warnings = fb._validate_ai_sql_filter(sql, columns)
        warnings.extend(validate_warnings)
    except Exception as exc:
        warnings.append(f"merged SQL rejected: {exc}")
        sql = ""
    selected_columns = fb._filter_ai_sql_selected_columns(
        column_result.get("selected_columns") or [],
        columns,
        warnings,
        "merge_selected_columns",
    )
    return {
        "merged": {
            "sql": sql,
            "selected_columns": selected_columns,
            "warnings": list(warnings),
        }
    }


def _source_lazy_frame(state: dict[str, Any]):
    fb = _fb()
    req = state.get("request") or {}
    scope = _safe_text(req.get("scope"), 80).casefold()
    root = _safe_text(req.get("root"), 160)
    product = _safe_text(req.get("product"), 160)
    file = _safe_text(req.get("file"), 240)
    from core.utils import lazy_read_source, scan_one_file, source_data_files

    if root and product:
        lf = lazy_read_source(root=root, product=product, recent_days=None, max_files=None, latest_only=False)
        if lf is None:
            raise ValueError(f"Cannot read DB product source: {root}/{product}")
        source_size = 0
        for fp in source_data_files(root=root, product=product):
            try:
                source_size += fp.stat().st_size
            except OSError:
                pass
        return lf, source_size
    fp = fb._resolve_ai_sql_profile_file(scope, file)
    if fp is None:
        fp = fb._resolve_data_file_for_schema(file, fb._load_filebrowser_settings())
    if fp is None:
        raise ValueError(f"Cannot resolve FileBrowser source file: {file}")
    lf = scan_one_file(Path(fp))
    if lf is None:
        raise ValueError(f"Cannot scan FileBrowser source file: {file}")
    try:
        source_size = Path(fp).stat().st_size
    except OSError:
        source_size = 0
    return lf, source_size


def _preview_apply(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    fb = _fb()
    merged = state.get("merged") or {}
    sql = _safe_text(merged.get("sql"), 1000)
    selected_columns = _string_list(merged.get("selected_columns"), limit=300)
    select_cols = ",".join(selected_columns)
    settings = fb._load_filebrowser_settings()
    lf, source_size = _source_lazy_frame(state)
    resp = fb._run_view_lazy(
        lf,
        sql,
        select_cols,
        100,
        meta_only=False,
        page=0,
        page_size=100,
        preview_cols=100,
        source_size=source_size,
        settings=settings,
    )
    resp = fb._finalize_preview_response(resp, settings)
    preview_warnings = list(warnings)
    if resp.get("truncated_cols"):
        preview_warnings.append("Preview columns were capped.")
    if resp.get("preview_capped"):
        preview_warnings.append("Preview rows were capped.")
    return {
        "preview": {
            "columns": resp.get("columns") or [],
            "rows": resp.get("data") or [],
            "total_rows": int(resp.get("total_rows") or 0),
            "preview_capped": bool(resp.get("preview_capped")),
            "warnings": preview_warnings,
            "selected_cols": resp.get("selected_cols"),
            "total_cols": int(resp.get("total_cols") or 0),
            "row_count_unknown": bool(resp.get("row_count_unknown")),
        }
    }


_NODE_RUNNERS: tuple[tuple[str, Callable[[dict[str, Any], list[str]], dict[str, Any] | None], Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("context_sample", _context_sample, _context_input),
    ("semantic_layer", _semantic_layer, _prompt_input),
    ("filter_draft", _filter_draft, _prompt_input),
    ("column_draft", _column_draft, _prompt_input),
    ("merge", _merge, _preview_input),
    ("preview_apply", _preview_apply, _preview_input),
)


def _run_with_langgraph(state: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from langgraph.graph import END, StateGraph
    except Exception:
        return None

    try:
        run_state = deepcopy(state)
        graph = StateGraph(dict)
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
        graph.set_entry_point("context_sample")
        graph.add_edge("context_sample", "semantic_layer")
        graph.add_edge("semantic_layer", "filter_draft")
        graph.add_edge("filter_draft", "column_draft")
        graph.add_edge("column_draft", "merge")
        graph.add_edge("merge", "preview_apply")
        graph.add_edge("preview_apply", END)
        app = graph.compile()
        return app.invoke(run_state)
    except Exception as exc:
        state.setdefault("runtime_warnings", []).append(
            f"LangGraph runner failed: {type(exc).__name__}: {exc}; sequential fallback runner used."
        )
        return None


def _run_sequential(state: dict[str, Any]) -> dict[str, Any]:
    state.setdefault("runtime_warnings", []).append("LangGraph is not installed; sequential fallback runner used.")
    for node_id, body, input_summary in _NODE_RUNNERS:
        state = _execute_node(state, node_id, body, input_summary)
    return state


def run_filebrowser_ai_sql_runtime(payload: dict[str, Any], *, username: str = "") -> dict[str, Any]:
    req = deepcopy(payload or {})
    prompt = _safe_text(req.get("natural_language"), 2000)
    if not prompt:
        raise HTTPException(status_code=400, detail="natural_language is required")
    req["natural_language"] = prompt
    run_id = "agent_fb_sql_" + uuid.uuid4().hex[:12]
    state: dict[str, Any] = {
        "run_id": run_id,
        "request": req,
        "username": _safe_text(username, 80),
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
            if row.get("node_id") == "context_sample":
                row.setdefault("warnings", []).extend(runtime_warnings)
                if row.get("status") == "success":
                    row["status"] = "warning"
                statuses["context_sample"] = row["status"]
                break
    preview = final_state.get("preview") or {}
    ok = bool(preview.get("columns") or preview.get("rows") or preview.get("total_rows") is not None)
    if any(row.get("node_id") == "preview_apply" and row.get("status") == "failed" for row in trace):
        ok = False
    return {
        "ok": ok,
        "run_id": run_id,
        "unit_ai": UNIT_AI_KEY,
        "graph": filebrowser_ai_sql_graph(statuses),
        "trace": trace,
        "semantic_frame": final_state.get("semantic_frame") or {},
        "filter": final_state.get("filter") or {"sql": "", "warnings": [], "llm": {}},
        "columns": final_state.get("columns_result") or {"selected_columns": [], "warnings": [], "llm": {}},
        "merged": final_state.get("merged") or {"sql": "", "selected_columns": []},
        "preview": preview or {"columns": [], "rows": [], "total_rows": 0, "preview_capped": False, "warnings": []},
    }
