"""Standalone Dashboard Agent runtime graph."""
from __future__ import annotations

import json
import operator
import re
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
from core import agent_feedback_penalties
from core import agent_prompt_overrides
from core import agent_semantic_service


UNIT_AI_KEY = "dashboard_agent"

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "semantic_layer", "label": "용어해석", "phase": "semantic"},
    {"id": "chart_intent", "label": "차트 의도", "phase": "intent"},
    {"id": "chart_type_select", "label": "차트 타입 선택", "phase": "llm"},
    {"id": "params_fill", "label": "파라미터 채우기", "phase": "llm"},
    {"id": "spec_validate", "label": "스펙 검증", "phase": "validate"},
    {"id": "render_spec", "label": "Plotly spec", "phase": "render"},
)

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "semantic_layer", "target": "chart_intent"},
    {"source": "chart_intent", "target": "chart_type_select"},
    {"source": "chart_type_select", "target": "params_fill"},
    {"source": "params_fill", "target": "spec_validate"},
    {"source": "spec_validate", "target": "render_spec"},
)

CHART_TYPE_SELECT_SYSTEM_PROMPT = (
    "You choose a Flow Dashboard chart type. Return strict JSON only with chart_type and reason. "
    "chart_type must be one of available_chart_types. Do not include markdown or hidden reasoning."
)

PARAMS_FILL_SYSTEM_PROMPT = (
    "You fill a Flow Dashboard chart spec. Return strict JSON only with x, y, group, color, agg, and title. "
    "Use only provided columns. agg must be raw, count, sum, avg, median, min, or max."
)

STATE_DESIGN: dict[str, dict[str, Any]] = {
    "run_id": {"description": "Runtime execution id.", "producer": "runtime", "public": True},
    "request": {"description": "natural_language, columns, sample_rows, optional product/dtypes.", "producer": "runtime", "public": True},
    "semantic_frame": {"description": "Shared semantic frame for the chart prompt.", "producer": "semantic_layer", "public": True},
    "chart_intent": {"description": "Chart intent confidence and fallback signals.", "producer": "chart_intent", "public": True},
    "chart_type": {"description": "Selected Dashboard chart type and LLM/fallback status.", "producer": "chart_type_select", "public": True},
    "params": {"description": "Selected x/y/group/color/agg/title parameters.", "producer": "params_fill", "public": True},
    "spec": {"description": "Validated chart config for Dashboard/Plotly.", "producer": "spec_validate", "public": True},
    "chart_result": {"description": "Existing PlotlyChart-compatible chart_result shape.", "producer": "render_spec", "public": True},
    "trace": {"description": "Append-only public node trace rows.", "producer": "runtime", "public": True},
    "runtime_warnings": {"description": "Runner-level warnings.", "producer": "runtime", "public": True},
}

NODE_METADATA: dict[str, dict[str, Any]] = {
    "semantic_layer": {
        "persona": "Shared semantic resolver for dashboard prompts and available columns.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.natural_language", "request.columns", "request.product", "request.dtypes"],
        "writes": ["semantic_frame"],
        "shared_state": ["semantic_frame.resolved_columns", "semantic_frame.value_terms", "semantic_frame.step_mapping"],
        "answer_attach_rule": "Attach the shared public semantic frame only.",
    },
    "chart_intent": {
        "persona": "Deterministic chart intent detector with keyword fallback.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.natural_language", "semantic_frame"],
        "writes": ["chart_intent"],
        "shared_state": ["chart_intent.has_chart_intent", "chart_intent.confidence"],
        "answer_attach_rule": "Attach intent signal and matched terms only.",
    },
    "chart_type_select": {
        "persona": "LLM JSON selector constrained to dashboard_join chart types.",
        "prompt": {"system": CHART_TYPE_SELECT_SYSTEM_PROMPT, "mode": "llm_json"},
        "reads": ["request.natural_language", "request.columns", "chart_intent"],
        "writes": ["chart_type"],
        "shared_state": ["chart_type.chart_type", "chart_type.llm", "chart_type.fallback"],
        "answer_attach_rule": "Attach selected chart type, reason, LLM status, and fallback warning.",
    },
    "params_fill": {
        "persona": "LLM JSON parameter filler using only provided columns.",
        "prompt": {"system": PARAMS_FILL_SYSTEM_PROMPT, "mode": "llm_json"},
        "reads": ["request.natural_language", "request.columns", "sample_rows", "chart_type"],
        "writes": ["params"],
        "shared_state": ["params.x", "params.y", "params.group", "params.color", "params.agg"],
        "answer_attach_rule": "Attach validated parameter candidates and public LLM status only.",
    },
    "spec_validate": {
        "persona": "Deterministic spec validator that applies Dashboard defaults and drops invalid columns.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["chart_type", "params", "request.columns"],
        "writes": ["spec"],
        "shared_state": ["spec.config", "spec.chart_type", "spec.title"],
        "answer_attach_rule": "Attach final chart config and warnings.",
    },
    "render_spec": {
        "persona": "Plotly-compatible chart_result renderer from sample rows and validated config.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["spec", "request.sample_rows"],
        "writes": ["chart_result"],
        "shared_state": ["chart_result.kind", "chart_result.points", "chart_result.config"],
        "answer_attach_rule": "Attach chart_result without changing the existing Plotly shape.",
    },
}


class _RuntimeState(TypedDict, total=False):
    run_id: str
    request: dict[str, Any]
    username: str
    semantic_frame: dict[str, Any]
    chart_intent: dict[str, Any]
    chart_type: dict[str, Any]
    params: dict[str, Any]
    spec: dict[str, Any]
    chart_result: dict[str, Any]
    node_errors: dict[str, str]
    trace: Annotated[list[dict[str, Any]], operator.add]
    runtime_warnings: Annotated[list[str], operator.add]


def _safe_text(value: Any, max_len: int = 2000) -> str:
    text = str(value or "").strip().replace("\x00", " ")
    return text[: max(1, max_len)].strip()


def _string_list(value: Any, limit: int = 100) -> list[str]:
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    for item in raw:
        text = _safe_text(item, 160)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _safe_rows(rows: Any, *, max_rows: int = 500, max_cols: int = 80) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        clean: dict[str, Any] = {}
        for idx, (key, value) in enumerate(row.items()):
            if idx >= max_cols:
                break
            clean[_safe_text(key, 120)] = value if value is None or isinstance(value, (bool, int, float, str)) else _safe_text(value, 240)
        out.append(clean)
    return out


def _node_metadata(node_id: str) -> dict[str, Any]:
    meta = deepcopy(NODE_METADATA.get(node_id, {}))
    override = agent_prompt_overrides.node_override(UNIT_AI_KEY, node_id)
    if override.get("persona"):
        meta["persona"] = override.get("persona")
    prompt = meta.get("prompt") if isinstance(meta.get("prompt"), dict) else {}
    if override.get("prompt_system"):
        meta["prompt"] = {**prompt, "system": override.get("prompt_system")}
    if override.get("cache"):
        meta["cache"] = override.get("cache")
    return meta


def dashboard_agent_graph(statuses: dict[str, str] | None = None) -> dict[str, Any]:
    statuses = statuses or {}
    return {
        "nodes": [
            {
                **node,
                **_node_metadata(node["id"]),
                "state_io": {
                    "reads": list(_node_metadata(node["id"]).get("reads") or []),
                    "writes": list(_node_metadata(node["id"]).get("writes") or []),
                },
                "status": statuses.get(node["id"], "pending"),
            }
            for node in GRAPH_NODES
        ],
        "edges": [dict(edge) for edge in GRAPH_EDGES],
        "state_design": deepcopy(STATE_DESIGN),
    }


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


def _node_status(warnings: list[str], failed: bool = False) -> str:
    if failed:
        return "failed"
    return "warning" if warnings else "success"


def _trace_output(node_id: str, state: dict[str, Any], result: dict[str, Any]) -> Any:
    if node_id == "render_spec":
        chart = state.get("chart_result") or {}
        return {
            "chart_type": chart.get("chart_type") or "",
            "title": chart.get("title") or "",
            "points": len(chart.get("points") or []),
            "config": chart.get("config") or {},
        }
    return state.get({
        "semantic_layer": "semantic_frame",
        "chart_intent": "chart_intent",
        "chart_type_select": "chart_type",
        "params_fill": "params",
        "spec_validate": "spec",
    }.get(node_id, ""), result)


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
    effective_system = agent_prompt_overrides.prompt_system(UNIT_AI_KEY, node_id, system)
    cache_text = agent_prompt_overrides.cache(UNIT_AI_KEY, node_id)
    if cache_text:
        effective_system = f"{effective_system}\n\n# Operator cache\n{cache_text}"
    llm_info: dict[str, Any] = {
        "available": False,
        "used": False,
        "error": "",
        "node_id": node_id,
        "system": _safe_text(effective_system, 4000),
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
            system=effective_system,
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


def _prompt_input(state: dict[str, Any]) -> dict[str, Any]:
    req = state.get("request") or {}
    return {
        "natural_language": _safe_text(req.get("natural_language"), 240),
        "columns_count": len(req.get("columns") or []),
        "sample_rows": len(req.get("sample_rows") or []),
    }


def _semantic_layer(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    req = state.get("request") or {}
    frame = agent_semantic_service.resolve(
        _safe_text(req.get("natural_language"), 2000),
        columns=req.get("columns") if isinstance(req.get("columns"), list) else [],
        product=_safe_text(req.get("product"), 160),
        dtypes=req.get("dtypes") if isinstance(req.get("dtypes"), dict) else {},
    )
    unknown = list(frame.get("unknown_column_terms") or [])
    if unknown:
        warnings.append("Unknown column-like terms: " + ", ".join(unknown[:8]))
    return {
        "semantic_frame": {
            "natural_language": frame.get("natural_language") or "",
            "resolved_columns": list(frame.get("resolved_columns") or []),
            "unknown_column_terms": unknown,
            "value_terms": list(frame.get("value_terms") or []),
            "synonyms": dict(frame.get("synonyms") or {}),
            "step_mapping": dict(frame.get("step_mapping") or {}),
        }
    }


def _chart_intent(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    prompt = _safe_text((state.get("request") or {}).get("natural_language"), 2000)
    matched = [term for term in ("차트", "그래프", "시각화", "chart", "plot", "trend", "분포", "scatter", "box") if term.casefold() in prompt.casefold()]
    has_intent = bool(matched)
    if not has_intent:
        warnings.append("chart intent not explicit; dashboard_agent will still draft a chart spec")
    return {
        "chart_intent": {
            "has_chart_intent": has_intent,
            "matched_terms": matched,
            "confidence": 0.85 if has_intent else 0.35,
        }
    }


def _chart_type_select(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import dashboard_join

    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    columns = list(req.get("columns") or [])
    fallback_type = dashboard_join.infer_chart_type(prompt, columns)
    plan, llm_info, llm_warnings = _llm_json(
        node_id="chart_type_select",
        system=CHART_TYPE_SELECT_SYSTEM_PROMPT,
        payload={
            "natural_language": prompt,
            "available_chart_types": list(dashboard_join.DASHBOARD_CHART_TYPES),
            "columns": columns[:80],
            "semantic_frame": state.get("semantic_frame") or {},
            "chart_intent": state.get("chart_intent") or {},
            "response_schema": {"chart_type": "one of available_chart_types", "reason": "short reason"},
        },
        schema={"keys": ["chart_type", "reason"], "required": [], "properties": {"chart_type": {}, "reason": {}}},
    )
    warnings.extend(w for w in llm_warnings if w not in warnings)
    chart_type = _safe_text(plan.get("chart_type"), 40)
    fallback = False
    if chart_type not in dashboard_join.DASHBOARD_CHART_TYPES:
        if chart_type:
            warnings.append(f"chart_type '{chart_type}' not whitelisted; fallback {fallback_type}")
        chart_type = fallback_type
        fallback = True
    return {
        "chart_type": {
            "chart_type": chart_type,
            "reason": _safe_text(plan.get("reason"), 240),
            "llm": llm_info,
            "fallback": fallback or not bool(llm_info.get("used")),
        }
    }


def _column_kind(columns: list[str], rows: list[dict[str, Any]]) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for col in columns:
        samples = [row.get(col) for row in rows[:30] if isinstance(row, dict) and row.get(col) is not None]
        if samples and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in samples):
            kinds[col] = "numeric"
        elif re.search(r"(time|date|dt|일시|시간)", col, re.IGNORECASE):
            kinds[col] = "time"
        else:
            kinds[col] = "category"
    return kinds


def _fallback_params(prompt: str, columns: list[str], rows: list[dict[str, Any]]) -> dict[str, str]:
    kinds = _column_kind(columns, rows)
    resolved = [col for col in columns if col in agent_semantic_service.resolve(prompt, columns=columns).get("resolved_columns", [])]
    numeric = [col for col in columns if kinds.get(col) == "numeric"]
    time_cols = [col for col in columns if kinds.get(col) == "time"]
    category = [col for col in columns if kinds.get(col) == "category"]
    x_col = (time_cols or category or columns or [""])[0]
    y_col = (numeric or resolved or columns[1:2] or columns[:1] or [""])[0]
    group_col = next((col for col in category if col != x_col), "")
    return {
        "x": x_col,
        "y": y_col,
        "group": group_col,
        "color": group_col,
        "agg": "raw" if numeric else "count",
    }


def _norm_axis_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").casefold())


def _axis_marker_re(axis: str) -> re.Pattern[str]:
    if axis == "x":
        return re.compile(r"(?:\bx\s*[-_ ]?axis\b|\baxis\s*x\b|x\s*축|x축|\bx\s*[=:])", re.I)
    return re.compile(r"(?:\by\s*[-_ ]?axis\b|\baxis\s*y\b|y\s*축|y축|\by\s*[=:])", re.I)


def _both_axes_requested(prompt: str) -> bool:
    text = str(prompt or "")
    return bool(re.search(r"(?:x\s*[,/&]\s*y\s*축|x\s*[,/&]\s*y\s*axis|x\s*축\s*(?:과|와|및|/|,)\s*y\s*축|x\s*axis\s*(?:and|/|,)\s*y\s*axis)", text, re.I))


def _explicit_axis_column(prompt: str, axis: str, columns: list[str]) -> tuple[bool, str]:
    text = str(prompt or "")
    marker = _axis_marker_re(axis)
    other_marker = _axis_marker_re("y" if axis == "x" else "x")
    matches = list(marker.finditer(text))
    if not matches:
        return False, ""
    norm_columns = [(col, _norm_axis_text(col)) for col in columns if _norm_axis_text(col)]
    for match in matches:
        window = text[match.end():match.end() + 120]
        other = other_marker.search(window)
        if other:
            window = window[:other.start()]
        window_norm = _norm_axis_text(window)
        found: list[tuple[int, int, str]] = []
        for col, norm_col in norm_columns:
            idx = window_norm.find(norm_col)
            if idx >= 0:
                found.append((idx, -len(norm_col), col))
        if found:
            found.sort()
            return True, found[0][2]
    return True, ""


def _axis_has_values(rows: list[dict[str, Any]], col: str) -> bool:
    if not rows or not col:
        return True
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(col)
        if value is not None and str(value).strip() != "":
            return True
    return False


def _axis_requirements(
    *,
    prompt: str,
    columns: list[str],
    rows: list[dict[str, Any]],
    params: dict[str, Any],
    chart_type: str,
) -> dict[str, Any]:
    both_requested = _both_axes_requested(prompt)
    x_marker, x_explicit = _explicit_axis_column(prompt, "x", columns)
    y_marker, y_explicit = _explicit_axis_column(prompt, "y", columns)
    required = set()
    if both_requested:
        required.update({"x", "y"})
    if x_marker:
        required.add("x")
    if y_marker:
        required.add("y")
    if chart_type in {"scatter"}:
        required.update({"x", "y"})
    elif chart_type in {"line"} and (x_marker or y_marker or both_requested):
        required.update({"x", "y"})

    explicit = {"x": x_explicit, "y": y_explicit}
    selected = {"x": _safe_text(params.get("x"), 120), "y": _safe_text(params.get("y"), 120)}
    missing: list[dict[str, str]] = []
    for axis in ("x", "y"):
        marker_required = both_requested or (axis == "x" and x_marker) or (axis == "y" and y_marker)
        if axis in required and marker_required and not explicit[axis]:
            missing.append({"axis": axis, "reason": "axis_value_empty"})
        if explicit[axis]:
            selected[axis] = explicit[axis]
        if axis in required and not selected[axis]:
            missing.append({"axis": axis, "reason": "axis_column_missing"})
        if selected[axis] and selected[axis] not in columns:
            missing.append({"axis": axis, "reason": "axis_column_not_in_table", "column": selected[axis]})
        if selected[axis] in columns and not _axis_has_values(rows, selected[axis]):
            missing.append({"axis": axis, "reason": "axis_column_has_no_values", "column": selected[axis]})
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in missing:
        key = (item.get("axis", ""), item.get("reason", ""), item.get("column", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    needs_input = bool(deduped)
    return {
        "required_axes": sorted(required),
        "explicit_axes": explicit,
        "selected_axes": selected,
        "missing": deduped,
        "needs_input": needs_input,
        "question": "차트에 필요한 x/y축 컬럼 값을 채워 주세요." if needs_input else "",
    }


def _params_fill(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    req = state.get("request") or {}
    prompt = _safe_text(req.get("natural_language"), 2000)
    columns = list(req.get("columns") or [])
    rows = _safe_rows(req.get("sample_rows"), max_rows=80)
    fallback = _fallback_params(prompt, columns, rows)
    plan, llm_info, llm_warnings = _llm_json(
        node_id="params_fill",
        system=PARAMS_FILL_SYSTEM_PROMPT,
        payload={
            "natural_language": prompt,
            "chart_type": (state.get("chart_type") or {}).get("chart_type") or "",
            "columns": columns[:80],
            "column_kinds": _column_kind(columns, rows),
            "sample_rows": rows[:5],
            "fallback_params": fallback,
            "response_schema": {"x": "column", "y": "column", "group": "optional column", "color": "optional column", "agg": "raw|count|sum|avg|median|min|max", "title": "short title"},
        },
        schema={"keys": ["x", "y", "group", "color", "agg", "title"], "required": [], "properties": {k: {} for k in ("x", "y", "group", "color", "agg", "title")}},
    )
    warnings.extend(w for w in llm_warnings if w not in warnings)

    def valid_col(value: Any) -> str:
        text = _safe_text(value, 120)
        return text if text in columns else ""

    agg = _safe_text(plan.get("agg"), 24).lower()
    if agg not in {"raw", "count", "sum", "avg", "median", "min", "max"}:
        agg = fallback.get("agg") or "raw"
    params = {
        "x": valid_col(plan.get("x")) or fallback.get("x") or "",
        "y": valid_col(plan.get("y")) or fallback.get("y") or "",
        "group": valid_col(plan.get("group")) or fallback.get("group") or "",
        "color": valid_col(plan.get("color")) or fallback.get("color") or "",
        "agg": agg,
        "title": _safe_text(plan.get("title"), 80),
        "llm": llm_info,
        "fallback": not bool(llm_info.get("used")),
    }
    axis_requirements = _axis_requirements(
        prompt=prompt,
        columns=columns,
        rows=rows,
        params=params,
        chart_type=_safe_text((state.get("chart_type") or {}).get("chart_type"), 40),
    )
    if axis_requirements.get("explicit_axes", {}).get("x"):
        params["x"] = axis_requirements["explicit_axes"]["x"]
    if axis_requirements.get("explicit_axes", {}).get("y"):
        params["y"] = axis_requirements["explicit_axes"]["y"]
    axis_requirements["selected_axes"] = {"x": params.get("x") or "", "y": params.get("y") or ""}
    params["axis_requirements"] = axis_requirements
    if axis_requirements.get("needs_input"):
        warnings.append(axis_requirements.get("question") or "chart axis input is required")
    return {"params": params}


def _spec_validate(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    from core import dashboard_join

    req = state.get("request") or {}
    columns = list(req.get("columns") or [])
    chart_type = _safe_text((state.get("chart_type") or {}).get("chart_type"), 40) or dashboard_join.infer_chart_type(
        _safe_text(req.get("natural_language"), 2000),
        columns,
    )
    params = state.get("params") or {}
    axis_requirements = params.get("axis_requirements") if isinstance(params.get("axis_requirements"), dict) else {}
    if axis_requirements.get("needs_input"):
        return {
            "spec": {
                "blocked": True,
                "needs_input": True,
                "question": axis_requirements.get("question") or "차트 축 입력이 필요합니다.",
                "axis_requirements": axis_requirements,
                "columns": columns,
            }
        }
    selected_items = [col for col in (params.get("x"), params.get("y")) if col] or columns[:2]
    overrides = {
        key: value
        for key, value in (
            ("x", params.get("x")),
            ("y", params.get("y")),
            ("group", params.get("group")),
            ("color", params.get("color")),
            ("agg", params.get("agg")),
        )
        if value
    }
    try:
        config = dashboard_join.apply_chart_defaults(chart_type, selected_items=selected_items, overrides=overrides)
    except Exception as exc:
        warnings.append(f"apply_chart_defaults failed: {exc}")
        config = {"chart_type": chart_type, **overrides}
    title = _safe_text(params.get("title"), 80) or f"Home Flow-i {chart_type}"
    return {
        "spec": {
            "chart_type": chart_type,
            "title": title,
            "config": config,
            "columns": columns,
            "axis_requirements": axis_requirements,
        }
    }


def _render_spec(state: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    req = state.get("request") or {}
    spec = state.get("spec") or {}
    if spec.get("blocked"):
        warnings.append(spec.get("question") or "chart spec requires user input")
        return {
            "chart_result": {},
            "needs_input": True,
            "question": spec.get("question") or "",
            "axis_requirements": spec.get("axis_requirements") or {},
        }
    config = spec.get("config") if isinstance(spec.get("config"), dict) else {}
    columns = list(spec.get("columns") or req.get("columns") or [])
    rows = _safe_rows(req.get("sample_rows"), max_rows=500)
    x_resolved = str(config.get("x") or "")
    y_resolved = str(config.get("y") or "")
    points: list[dict[str, Any]] = []
    for row in rows:
        point: dict[str, Any] = {
            "x": row.get(x_resolved) if x_resolved else None,
            "y": row.get(y_resolved) if y_resolved else None,
        }
        for key in columns[:8]:
            if key in {x_resolved, y_resolved}:
                continue
            point[key] = row.get(key)
        points.append(point)
    if not rows:
        warnings.append("render_spec has no sample_rows; chart_result contains an empty points list")
    chart_type = _safe_text(spec.get("chart_type"), 40) or str(config.get("chart_type") or "scatter")
    chart_result = {
        "ok": True,
        "kind": f"dashboard_{chart_type}",
        "chart_type": chart_type,
        "title": _safe_text(spec.get("title"), 80) or f"Home Flow-i {chart_type}",
        "points": points,
        "total": len(points),
        "config": config,
        "chart_config": config,
        "status": "draft",
    }
    return {"chart_result": chart_result}


_NODE_RUNNERS: tuple[tuple[str, Callable[[dict[str, Any], list[str]], dict[str, Any] | None], Callable[[dict[str, Any]], dict[str, Any]]], ...] = (
    ("semantic_layer", _semantic_layer, _prompt_input),
    ("chart_intent", _chart_intent, _prompt_input),
    ("chart_type_select", _chart_type_select, _prompt_input),
    ("params_fill", _params_fill, _prompt_input),
    ("spec_validate", _spec_validate, _prompt_input),
    ("render_spec", _render_spec, _prompt_input),
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
        graph.add_edge("render_spec", END)
        return graph.compile().invoke(run_state)
    except Exception:
        return None


def _run_sequential(state: dict[str, Any]) -> dict[str, Any]:
    return run_nodes_sequential(state, _NODE_RUNNERS, _NODE_EXECUTOR)


def run_dashboard_agent_runtime(
    payload: dict[str, Any],
    *,
    username: str = "",
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    req = deepcopy(payload or {})
    prompt = _safe_text(req.get("natural_language") or req.get("prompt"), 2000)
    if not prompt:
        raise HTTPException(status_code=400, detail="natural_language is required")
    columns = _string_list(req.get("columns"), limit=300)
    sample_rows = _safe_rows(req.get("sample_rows"), max_rows=500)
    if not columns and sample_rows:
        columns = list(sample_rows[0].keys())
    req.update({
        "natural_language": prompt,
        "columns": columns,
        "sample_rows": sample_rows,
        "product": _safe_text(req.get("product"), 160),
        "dtypes": req.get("dtypes") if isinstance(req.get("dtypes"), dict) else {},
    })
    run_id = "agent_dashboard_" + uuid.uuid4().hex[:12]
    state: dict[str, Any] = {
        "run_id": run_id,
        "request": req,
        "username": _safe_text(username, 80),
        "agent_context": deepcopy(agent_context or {}),
        "trace": [],
        "runtime_warnings": [],
        "node_errors": {},
    }
    final_state = _run_with_langgraph(state) or _run_sequential(state)
    statuses = {row.get("node_id"): row.get("status") for row in final_state.get("trace") or [] if isinstance(row, dict)}
    warnings: list[str] = []
    for row in final_state.get("trace") or []:
        for item in row.get("warnings") or []:
            text = _safe_text(item, 240)
            if text and text not in warnings:
                warnings.append(text)
    chart_result = final_state.get("chart_result") if isinstance(final_state.get("chart_result"), dict) else {}
    needs_input = bool(final_state.get("needs_input") or (final_state.get("spec") or {}).get("needs_input"))
    ok = not needs_input and not bool(final_state.get("node_errors")) and bool(chart_result)
    status = "blocked" if needs_input else ("success" if ok and not warnings else ("warning" if ok else "failed"))
    result = {
        "ok": ok,
        "status": status,
        "blocked": needs_input,
        "needs_input": needs_input,
        "question": _safe_text(final_state.get("question") or (final_state.get("spec") or {}).get("question"), 240),
        "run_id": run_id,
        "unit_ai": UNIT_AI_KEY,
        "graph": dashboard_agent_graph(statuses),
        "trace": final_state.get("trace") or [],
        "semantic_frame": final_state.get("semantic_frame") or {},
        "chart_intent": final_state.get("chart_intent") or {},
        "chart_type": (final_state.get("chart_type") or {}).get("chart_type") or chart_result.get("chart_type") or "",
        "params": final_state.get("params") or {},
        "axis_requirements": final_state.get("axis_requirements") or (final_state.get("params") or {}).get("axis_requirements") or {},
        "config": (final_state.get("spec") or {}).get("config") or chart_result.get("config") or {},
        "spec": final_state.get("spec") or {},
        "chart_result": chart_result,
        "warnings": warnings,
        "runtime_warnings": final_state.get("runtime_warnings") or [],
    }
    return agent_feedback_penalties.annotate_result(UNIT_AI_KEY, result)
