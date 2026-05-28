"""core/home_orchestrator.py — 홈 에이전트 오케스트레이터.

자연어 prompt 를 받아 ToolRegistry 의 도구 중 적합한 것을 1~N개 골라
순차 실행하고, 트레이스(어떤 도구를 왜 골랐는지)를 응답에 포함한다.

선택 전략 (우선순위):
  1. LLM planner — env `FLOW_LLM_TOOL_CALL=1` 이고 llm_adapter 가 enabled 면
     LLM 에 tool catalog 와 prompt 를 보내 step sequence(JSON) 를 받아 실행.
  2. 휴리스틱 dispatcher — prompt 의 한국어/영어 키워드를 태그/이름에 매칭
     해 도구 후보 가중치 산정 → 상위 top_k 선택.

실행:
  - filebrowser_ai_sql, inform_registration 은 전용 unit runtime 으로 실행.
  - function-call 단위는 trace stub (실제 실행은 기존 flowi/chat 경로).

트레이스 포맷:
  [{"tool": name, "kind": "unit_ai"|"function",
    "input": {...}, "output_preview": "...",
    "ok": bool, "ms": int, "matched_terms"|"reason": "..."}]

orchestrate_stream(prompt, user) → generator: SSE event chunk dict 시퀀스.
  {"type": "plan", "steps": [...]}
  {"type": "step_start", "step": {...}}
  {"type": "step_end", "step": {..., "ok": bool, "output_preview": "..."}}
  {"type": "reply", "text": "...", "trace": [...]}
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
import inspect
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterator

from core import home_memory
from core import tool_registry
from core.agent_tool_contract import ToolCall
from core.paths import PATHS

logger = logging.getLogger("flow.home_orchestrator")

_MAX_STEPS = 3
_LLM_ENV_FLAG = "FLOW_LLM_TOOL_CALL"
HOME_AGENT_RUNS_DIR = PATHS.data_root / "home_agent_runs"
_RUN_ID_PREFIX = "home_flowi"
_MAX_SNAPSHOT_RUNS = 120
_MAX_PREVIEW_ROWS = 10
_MAX_PREVIEW_COLS = 20


_RUNTIME_FIXED_NODES: tuple[dict[str, str], ...] = (
    {"id": "prompt_input", "label": "프롬프트 입력", "phase": "input"},
    {"id": "semantic_layer", "label": "용어해석", "phase": "semantic"},
    {"id": "orchestrator", "label": "오케스트레이터", "phase": "plan"},
    {"id": "result_renderer", "label": "결과 정리", "phase": "render"},
)

_RUNTIME_BASE_EDGES: tuple[dict[str, str], ...] = (
    {"source": "prompt_input", "target": "semantic_layer"},
    {"source": "semantic_layer", "target": "orchestrator"},
)


# 한국어/영어 키워드 → 태그 가중치. 매칭되면 해당 태그 보유 도구가 점수 획득.
KEYWORD_WEIGHTS: list[tuple[re.Pattern[str], dict[str, float]]] = [
    (re.compile(r"\b(sql|join|쿼리|조인)\b", re.I), {"sql_workspace": 3.0, "filebrowser": 1.0}),
    (re.compile(r"(차트|그래프|시각화|chart|plot|trend)", re.I), {"chart": 2.5, "dashboard": 2.0}),
    (re.compile(r"(평균|중앙값|avg|median|집계|aggregate)", re.I), {"chart": 1.2, "dashboard": 1.2}),
    (re.compile(r"(lot|wafer|fab|로트|웨이퍼)", re.I), {"lot": 2.0, "fab": 1.5}),
    (re.compile(r"(knob|mask|스플릿|split|splittable)", re.I), {"splittable": 2.5, "knob": 2.0}),
    (re.compile(r"(인폼|inform|메일|mail)", re.I), {"inform": 2.5}),
    (re.compile(r"(회의|meeting|아젠다)", re.I), {"meeting": 2.5}),
    (re.compile(r"(이슈|tracker|추적)", re.I), {"tracker": 2.5}),
    (re.compile(r"(파일|file|parquet|csv|디렉토리)", re.I), {"filebrowser": 2.0}),
    (re.compile(r"(스키마|schema|컬럼|column)", re.I), {"filebrowser": 1.5, "search": 1.0}),
    (re.compile(r"(step|단계)", re.I), {"step": 1.5}),
    (re.compile(r"(diagnosis|진단|rca|dibl|vth|ion|ioff)", re.I), {"diagnosis": 2.5}),
    (re.compile(r"(일정|캘린더|calendar|변경점)", re.I), {"calendar": 2.5}),
    (re.compile(r"(layout|tablemap|관계|join)", re.I), {"tablemap": 1.5}),
]


def _keyword_signals(prompt: str) -> tuple[dict[str, float], list[str]]:
    signals: dict[str, float] = {}
    matched: list[str] = []
    for rx, weights in KEYWORD_WEIGHTS:
        m = rx.search(prompt or "")
        if not m:
            continue
        matched.append(m.group(0))
        for tag, w in weights.items():
            signals[tag] = signals.get(tag, 0.0) + w
    return signals, matched


def _score_tool(tool: dict[str, Any], signals: dict[str, float], prompt_lower: str) -> float:
    if not tool.get("enabled"):
        return 0.0
    score = 0.0
    for tag in tool.get("tags") or []:
        if tag in signals:
            score += signals[tag]
    # 이름/제목 키워드 매칭 bonus
    nm = (tool.get("name") or "").lower()
    title = (tool.get("title") or "").lower()
    for token in re.findall(r"[A-Za-z가-힣]{3,}", prompt_lower):
        if token in nm:
            score += 0.8
        elif token in title:
            score += 0.5
    return score


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_run_id(prefix: str = _RUN_ID_PREFIX) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}"


def _safe_run_id(run_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(run_id or "")).strip("._-")
    if not clean:
        raise ValueError("run_id is required")
    return clean[:160]


def _short_text(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[: max(1, limit)]


def _safe_value(value: Any, limit: int = 240) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _short_text(value, limit)
    return _short_text(value, limit)


def _safe_rows(rows: Any, *, max_rows: int = _MAX_PREVIEW_ROWS, max_cols: int = _MAX_PREVIEW_COLS) -> list[dict[str, Any]]:
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
            clean[_short_text(key, 120)] = _safe_value(value, 180)
        out.append(clean)
    return out


def _safe_string_list(value: Any, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = _short_text(item, 120)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _safe_public_tool(tool: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(tool, dict):
        return {}
    allowed = {
        "type",
        "answer",
        "inline_summary",
        "action",
        "feature",
        "intent",
        "table",
        "rows",
        "split_view",
        "chart",
        "chart_result",
        "blocks",
        "sql_draft",
        "selected_columns",
        "sample_rows",
        "row_count",
        "warnings",
        "sources",
        "blocked",
        "reject_reason",
        "requires_confirmation",
        "missing",
        "arguments_choices",
        "missing_freetext",
        "pending_prompt",
        "last_partial_prompt",
    }
    out: dict[str, Any] = {}
    for key in allowed:
        if key not in tool:
            continue
        value = deepcopy(tool.get(key))
        if key == "rows":
            value = _safe_rows(value)
        elif key == "sample_rows":
            value = _safe_rows(value, max_rows=5)
        elif key == "table" and isinstance(value, dict):
            value["rows"] = _safe_rows(value.get("rows"))
        elif key == "split_view" and isinstance(value, dict):
            rows = value.get("rows")
            if isinstance(rows, list):
                value["rows"] = rows[:12]
        elif key == "blocks" and isinstance(value, list):
            value = _safe_blocks(value)
        out[key] = value
    return out


def _safe_blocks(blocks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in blocks[:8]:
        if not isinstance(block, dict):
            continue
        clean = {
            key: deepcopy(block.get(key))
            for key in ("id", "kind", "title", "highlight")
            if key in block
        }
        payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
        if payload:
            clean_payload: dict[str, Any] = {}
            for key, value in payload.items():
                if key == "rows":
                    clean_payload[key] = _safe_rows(value)
                elif key == "columns":
                    clean_payload[key] = _safe_string_list(value, _MAX_PREVIEW_COLS)
                elif isinstance(value, list):
                    clean_payload[key] = value[:20]
                elif isinstance(value, dict):
                    clean_payload[key] = {str(k): _safe_value(v) for k, v in list(value.items())[:20]}
                else:
                    clean_payload[key] = _safe_value(value)
            clean["payload"] = clean_payload
        out.append(clean)
    return out


def _unit_ai_tools() -> list[dict[str, Any]]:
    tools = [
        tool
        for tool in tool_registry.list_tools(include_stats=False)
        if tool.get("kind") == "unit_ai"
    ]
    return sorted(tools, key=lambda item: str(item.get("name") or ""))


def _runtime_unit_names() -> set[str]:
    return {str(tool.get("name") or "") for tool in _unit_ai_tools() if tool.get("name")}


def _normalize_unit_name(name: str, tool: dict[str, Any] | None = None) -> str:
    name = str(name or "").strip()
    unit_names = _runtime_unit_names()
    if name in unit_names:
        return name
    if name == "filebrowser" and "filebrowser_ai_sql" in unit_names:
        return "filebrowser_ai_sql"
    if tool and isinstance(tool.get("sql_draft"), dict) and "filebrowser_ai_sql" in unit_names:
        return "filebrowser_ai_sql"
    return ""


def build_home_runtime_graph(
    *,
    selected_units: list[str] | None = None,
    statuses: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the public Home Flow-i runtime graph shown in Agent.

    The graph is deliberately public-state only: node status, short labels, and
    MCP candidate availability. It does not expose model reasoning text.
    """
    statuses = statuses or {}
    selected = [name for name in (selected_units or []) if name]
    selected_set = set(selected)
    nodes: list[dict[str, Any]] = []
    for node in _RUNTIME_FIXED_NODES:
        default_status = "pending"
        nodes.append({**node, "status": statuses.get(node["id"], default_status)})
    unit_nodes: list[dict[str, Any]] = []
    for tool in _unit_ai_tools():
        name = str(tool.get("name") or "")
        node_id = f"unit_ai:{name}"
        default = "available" if tool.get("enabled") else "skipped"
        if name in selected_set:
            default = "planned"
        unit_nodes.append({
            "id": node_id,
            "label": tool.get("title") or name,
            "phase": "unit_ai_mcp",
            "kind": "unit_ai",
            "tool": name,
            "status": statuses.get(node_id, default),
        })
    nodes.extend(unit_nodes)
    edges = [dict(edge) for edge in _RUNTIME_BASE_EDGES]
    for node in unit_nodes:
        edges.append({"source": "orchestrator", "target": node["id"]})
    for name in selected:
        node_id = f"unit_ai:{name}"
        if any(node.get("id") == node_id for node in unit_nodes):
            edges.append({"source": node_id, "target": "result_renderer"})
    if not selected:
        edges.append({"source": "orchestrator", "target": "result_renderer"})
    return {"nodes": nodes, "edges": edges}


def _status_from_trace_row(row: dict[str, Any]) -> str:
    if row.get("blocked"):
        return "blocked"
    explicit = str(row.get("status") or "").strip()
    if explicit:
        return explicit
    if row.get("ok"):
        warnings = row.get("warnings") if isinstance(row.get("warnings"), list) else []
        return "warning" if warnings else "success"
    return "failed"


def _result_warnings(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    for source in (
        tool.get("warnings") if isinstance(tool.get("warnings"), list) else [],
        (tool.get("sql_draft") or {}).get("warnings") if isinstance(tool.get("sql_draft"), dict) else [],
    ):
        for item in source or []:
            text = _short_text(item, 240)
            if text and text not in warnings:
                warnings.append(text)
    trace = result.get("trace")
    if isinstance(trace, dict):
        validation = trace.get("validation") if isinstance(trace.get("validation"), dict) else {}
        for item in validation.get("warnings") or []:
            text = _short_text(item, 240)
            if text and text not in warnings:
                warnings.append(text)
    return warnings[:12]


def _selected_units_from_result(result: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    trace = result.get("trace")
    if isinstance(trace, list):
        for row in trace:
            if not isinstance(row, dict) or row.get("kind") != "unit_ai":
                continue
            name = _normalize_unit_name(str(row.get("tool") or ""), tool)
            if name and name not in selected:
                selected.append(name)
    elif isinstance(trace, dict):
        rows = trace.get("unit_ai_selection") if isinstance(trace.get("unit_ai_selection"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            if status in {"delegated", "running", "success", "warning", "failed", "blocked", "approval_required"}:
                name = _normalize_unit_name(str(row.get("key") or row.get("unit_ai") or ""), tool)
                if name and name not in selected:
                    selected.append(name)
        guard = trace.get("guardrail") if isinstance(trace.get("guardrail"), dict) else {}
        name = _normalize_unit_name(str(guard.get("selected_feature") or ""), tool)
        if name and name not in selected:
            selected.append(name)
    feature = _normalize_unit_name(str(tool.get("feature") or ""), tool)
    if feature and feature not in selected:
        selected.append(feature)
    return selected


def _runtime_statuses(result: dict[str, Any], selected_units: list[str]) -> dict[str, str]:
    trace = result.get("trace")
    warnings = _result_warnings(result)
    blocked = bool(result.get("blocked") or result.get("needs_input"))
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    if tool.get("blocked"):
        blocked = True
    if isinstance(trace, dict):
        guard = trace.get("guardrail") if isinstance(trace.get("guardrail"), dict) else {}
        if guard.get("status") == "blocked":
            blocked = True
        clarification = trace.get("clarification_loop") if isinstance(trace.get("clarification_loop"), dict) else {}
        if clarification.get("needs_input"):
            blocked = True
    if blocked:
        result_status = "blocked"
    elif result.get("ok", True) is False:
        result_status = "failed"
    else:
        result_status = "warning" if warnings else "success"
    statuses = {
        "prompt_input": "success",
        "semantic_layer": "success",
        "orchestrator": "blocked" if blocked else "success",
        "result_renderer": result_status,
    }
    if isinstance(trace, list):
        for row in trace:
            if not isinstance(row, dict) or row.get("kind") != "unit_ai":
                continue
            name = _normalize_unit_name(str(row.get("tool") or ""), tool)
            if name:
                statuses[f"unit_ai:{name}"] = _status_from_trace_row(row)
    for name in selected_units:
        node_id = f"unit_ai:{name}"
        statuses.setdefault(node_id, result_status)
    return statuses


def _preview_from_result(result: dict[str, Any]) -> dict[str, Any]:
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    table = tool.get("table") if isinstance(tool.get("table"), dict) else {}
    if table:
        return {
            "kind": table.get("kind") or "table",
            "title": table.get("title") or "",
            "columns": _safe_string_list(table.get("columns"), _MAX_PREVIEW_COLS),
            "rows": _safe_rows(table.get("rows")),
            "total": table.get("total", len(table.get("rows") or [])),
        }
    rows = tool.get("rows") if isinstance(tool.get("rows"), list) else []
    if rows:
        return {"kind": "rows", "rows": _safe_rows(rows), "total": len(rows)}
    sql_draft = tool.get("sql_draft") if isinstance(tool.get("sql_draft"), dict) else {}
    if sql_draft:
        return {
            "kind": "sql_draft",
            "sql": _short_text(sql_draft.get("sql"), 1000),
            "selected_columns": _safe_string_list(sql_draft.get("selected_columns"), _MAX_PREVIEW_COLS),
            "warnings": _safe_string_list(sql_draft.get("warnings"), 8),
        }
    return {}


def _node_details_for_result(
    *,
    prompt: str,
    result: dict[str, Any],
    selected_units: list[str],
    statuses: dict[str, str],
    source: str,
) -> dict[str, Any]:
    trace = result.get("trace")
    tool = result.get("tool") if isinstance(result.get("tool"), dict) else {}
    action_log = result.get("action_log") if isinstance(result.get("action_log"), dict) else {}
    warnings = _result_warnings(result)
    semantic = {}
    interpretation = {}
    plan: list[Any] = []
    unit_selection: list[Any] = []
    if isinstance(trace, dict):
        semantic = trace.get("semantic") if isinstance(trace.get("semantic"), dict) else {}
        interpretation = trace.get("interpretation") if isinstance(trace.get("interpretation"), dict) else {}
        plan = trace.get("plan") if isinstance(trace.get("plan"), list) else []
        unit_selection = trace.get("unit_ai_selection") if isinstance(trace.get("unit_ai_selection"), list) else []
    details: dict[str, Any] = {
        "prompt_input": {
            "node_id": "prompt_input",
            "status": statuses.get("prompt_input", "pending"),
            "input_summary": {"prompt": _short_text(prompt, 500), "source": source},
            "output_summary": {"chars": len(prompt or "")},
            "warnings": [],
        },
        "semantic_layer": {
            "node_id": "semantic_layer",
            "status": statuses.get("semantic_layer", "pending"),
            "input_summary": {"prompt": _short_text(prompt, 240)},
            "output_summary": {
                "semantic": semantic,
                "interpretation": interpretation,
                "signals": (result.get("meta") or {}).get("signals") if isinstance(result.get("meta"), dict) else {},
            },
            "warnings": _safe_string_list(semantic.get("warnings") if isinstance(semantic, dict) else [], 8),
        },
        "orchestrator": {
            "node_id": "orchestrator",
            "status": statuses.get("orchestrator", "pending"),
            "input_summary": {"candidate_unit_ai": selected_units},
            "output_summary": {
                "planner": (result.get("meta") or {}).get("planner") if isinstance(result.get("meta"), dict) else "",
                "plan": plan[:20],
                "unit_ai_selection": unit_selection[:20],
                "tool": {
                    "feature": tool.get("feature") or "",
                    "action": tool.get("action") or "",
                    "intent": tool.get("intent") or "",
                    "blocked": bool(tool.get("blocked")),
                },
            },
            "warnings": [],
        },
        "result_renderer": {
            "node_id": "result_renderer",
            "status": statuses.get("result_renderer", "pending"),
            "input_summary": {"selected_unit_ai": selected_units},
            "output_summary": {
                "answer": _short_text(result.get("answer") or result.get("reply"), 1200),
                "ok": bool(result.get("ok", True)),
                "needs_input": bool(result.get("needs_input")),
                "action_log_summary": action_log.get("summary", [])[:6] if isinstance(action_log.get("summary"), list) else [],
            },
            "warnings": warnings,
            "preview": _preview_from_result(result),
        },
    }
    trace_rows = trace if isinstance(trace, list) else []
    for name in selected_units:
        node_id = f"unit_ai:{name}"
        row = next((item for item in trace_rows if isinstance(item, dict) and _normalize_unit_name(str(item.get("tool") or ""), tool) == name), {})
        details[node_id] = {
            "node_id": node_id,
            "status": statuses.get(node_id, "planned"),
            "input_summary": row.get("input") if isinstance(row.get("input"), dict) else {"prompt": _short_text(prompt, 240)},
            "output_summary": {
                "tool": row.get("tool") or name,
                "title": row.get("title") or name,
                "result_preview": row.get("result_preview") or _short_text(result.get("answer") or result.get("reply"), 500),
                "feature": tool.get("feature") or "",
                "action": tool.get("action") or "",
            },
            "warnings": _safe_string_list(row.get("warnings"), 8) or warnings,
            "preview": _preview_from_result(result),
        }
    return details


def _synth_action_log(result: dict[str, Any], graph: dict[str, Any]) -> dict[str, Any]:
    existing = result.get("action_log") if isinstance(result.get("action_log"), dict) else {}
    if existing:
        return deepcopy(existing)
    statuses = {node.get("id"): node.get("status") for node in graph.get("nodes") or [] if isinstance(node, dict)}
    selected = [node for node in graph.get("nodes") or [] if str(node.get("id") or "").startswith("unit_ai:") and node.get("status") not in {"available", "skipped"}]
    summary = [
        "질문을 공개 가능한 runtime 단계로 정리했습니다.",
        f"오케스트레이터 상태: {statuses.get('orchestrator', 'pending')}.",
    ]
    if selected:
        summary.append("선택된 단위AI MCP: " + ", ".join(str(node.get("tool") or node.get("id")) for node in selected[:4]))
    if result.get("reply") or result.get("answer"):
        summary.append("최종 결과를 Home 응답으로 정리했습니다.")
    return {
        "summary": summary[:6],
        "timeline": [
            {"stage": node.get("id"), "title": node.get("label"), "status": node.get("status"), "detail": node.get("phase")}
            for node in (graph.get("nodes") or [])
            if isinstance(node, dict) and not str(node.get("id") or "").startswith("unit_ai:available")
        ][:12],
        "final_answer": result.get("answer") or result.get("reply") or "",
        "disclaimer": "내부 추론 원문이 아니라 검증 가능한 실행 요약입니다.",
    }


def build_home_runtime_snapshot(
    *,
    prompt: str,
    result: dict[str, Any],
    user: dict[str, Any] | None = None,
    source: str = "home_agent",
    run_id: str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    prompt = str(prompt or "").strip()
    result = result if isinstance(result, dict) else {}
    run_id = _safe_run_id(run_id or str(result.get("run_id") or "") or _new_run_id())
    selected_units = _selected_units_from_result(result)
    statuses = _runtime_statuses(result, selected_units)
    graph = build_home_runtime_graph(selected_units=selected_units, statuses=statuses)
    snapshot = {
        "ok": bool(result.get("ok", True)),
        "run_id": run_id,
        "created_at": _now_iso(),
        "source": source,
        "username": _short_text((user or {}).get("username") or result.get("user") or "", 80),
        "prompt": _short_text(prompt, 2000),
        "status": statuses.get("result_renderer", "pending"),
        "reply": result.get("reply") or result.get("answer") or "",
        "graph": graph,
        "trace": deepcopy(result.get("trace") or []),
        "action_log": {},
        "tool": _safe_public_tool(result.get("tool") if isinstance(result.get("tool"), dict) else {}),
        "node_details": {},
        "warnings": _result_warnings(result),
    }
    snapshot["action_log"] = _synth_action_log(result, graph)
    snapshot["node_details"] = _node_details_for_result(
        prompt=prompt,
        result=result,
        selected_units=selected_units,
        statuses=statuses,
        source=source,
    )
    if save:
        save_home_runtime_snapshot(snapshot)
    return snapshot


def _snapshot_path(run_id: str) -> Any:
    clean = _safe_run_id(run_id)
    return HOME_AGENT_RUNS_DIR / f"{clean}.json"


def save_home_runtime_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    run_id = _safe_run_id(str(snapshot.get("run_id") or ""))
    path = _snapshot_path(run_id)
    HOME_AGENT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    _prune_home_runtime_snapshots()
    return snapshot


def _prune_home_runtime_snapshots(limit: int = _MAX_SNAPSHOT_RUNS) -> None:
    try:
        files = sorted(HOME_AGENT_RUNS_DIR.glob("*.json"), key=lambda fp: fp.stat().st_mtime, reverse=True)
        for fp in files[max(1, limit):]:
            try:
                fp.unlink()
            except OSError:
                pass
    except Exception:
        logger.debug("home runtime snapshot prune failed", exc_info=True)


def load_home_runtime_run(run_id: str) -> dict[str, Any] | None:
    path = _snapshot_path(run_id)
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("home runtime snapshot load failed: %s", run_id, exc_info=True)
        return None


def list_home_runtime_runs(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(100, int(limit or 20)))
    try:
        files = sorted(HOME_AGENT_RUNS_DIR.glob("*.json"), key=lambda fp: fp.stat().st_mtime, reverse=True)
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for fp in files[:limit]:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        rows.append({
            "run_id": data.get("run_id") or fp.stem,
            "created_at": data.get("created_at") or "",
            "source": data.get("source") or "",
            "username": data.get("username") or "",
            "prompt": _short_text(data.get("prompt"), 240),
            "status": data.get("status") or "",
            "reply": _short_text(data.get("reply"), 240),
        })
    return rows


def record_flowi_runtime_run(
    *,
    prompt: str,
    result: dict[str, Any],
    user: dict[str, Any] | None = None,
    source: str = "llm_flowi_chat",
) -> dict[str, Any]:
    snapshot = build_home_runtime_snapshot(
        prompt=prompt,
        result=result,
        user=user,
        source=source,
        save=True,
    )
    return {
        "run_id": snapshot["run_id"],
        "graph": snapshot["graph"],
        "status": snapshot["status"],
    }


def _pick_tools(prompt: str, top_k: int = 2) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = tool_registry.list_tools(include_stats=False)
    signals, matched_terms = _keyword_signals(prompt)
    plower = (prompt or "").lower()
    scored = [(t, _score_tool(t, signals, plower)) for t in items]
    scored = [(t, s) for t, s in scored if s > 0.0]
    scored.sort(key=lambda x: -x[1])
    picked = scored[:top_k]
    meta = {"signals": signals, "matched_terms": matched_terms, "candidate_count": len(scored)}
    return [{"tool": t, "score": s} for t, s in picked], meta


# 실제 실행 가능한 도구 매핑 — 회귀 위험 최소화 위해 좁게 시작.
def _execute_tool(tool: dict[str, Any], prompt: str) -> dict[str, Any]:
    return _execute_step(tool, {"prompt": prompt or ""})


def _call_runtime_with_context(func, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    agent_context = kwargs.pop("agent_context", None)
    try:
        sig = inspect.signature(func)
        accepts_context = "agent_context" in sig.parameters or any(
            param.kind == inspect.Parameter.VAR_KEYWORD
            for param in sig.parameters.values()
        )
    except (TypeError, ValueError):
        accepts_context = True
    if accepts_context:
        kwargs["agent_context"] = agent_context
    return func(payload, **kwargs)


def _summarize_result(res: dict[str, Any]) -> str:
    if not isinstance(res, dict):
        return str(res)[:200]
    for key in ("text", "answer", "message", "summary"):
        v = res.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:300]
    keys = [k for k in res.keys() if k != "handled"]
    return f"키: {', '.join(sorted(keys))[:200]}"


def _llm_planner_enabled() -> bool:
    """LLM planner 사용 여부.

    환경변수 `FLOW_LLM_TOOL_CALL=1` 이고 llm_adapter 가 enabled 이어야 활성.
    어느 쪽이든 비활성이면 휴리스틱 fallback. 회귀 위험 0 유지.
    """
    if str(os.environ.get(_LLM_ENV_FLAG, "")).strip() not in ("1", "true", "yes", "on"):
        return False
    try:
        from core import llm_adapter
        return bool(llm_adapter.is_available())
    except Exception:
        return False


def _plan_with_llm(prompt: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """LLM 에게 prompt + tool catalog 을 주고 step list(JSON) 를 받아 반환.

    실패하면 None — 호출자는 휴리스틱 fallback 으로 떨어진다.
    """
    try:
        from core import llm_adapter
    except Exception:
        return None
    enabled_tools = [t for t in tools if t.get("enabled")]
    # 도구 카탈로그를 LLM 이 한 번에 보기 좋은 짧은 list 로.
    tool_lines = []
    for t in enabled_tools[:40]:
        ex = ""
        if t.get("examples"):
            first = t["examples"][0]
            ex_prompt = first.get("prompt") if isinstance(first, dict) else None
            if ex_prompt:
                ex = f' | 예: "{str(ex_prompt)[:50]}"'
        tool_lines.append(f"- {t['name']} ({t['kind']}): {t.get('description','')[:120]}{ex}")
    tools_text = "\n".join(tool_lines) or "(empty)"

    system = (
        "You are Flow-i's home agent planner. Pick the minimum set of tools from "
        "the catalog to satisfy the user's request. Respond with strict JSON only."
    )
    user_prompt = (
        f"# 사용자 요청\n{prompt}\n\n"
        f"# 사용 가능한 도구 카탈로그\n{tools_text}\n\n"
        "# 출력 형식 (JSON, 다른 텍스트 금지)\n"
        "{\n"
        '  "steps": [\n'
        '    {"tool": "<카탈로그 name>", "kind": "unit_ai|function", '
        '"input": {"prompt": "<단일 도구로 풀어쓴 자연어>", "product": "<선택>", "max_rows": 12}, '
        '"reason": "<왜 골랐는지 한 줄>"}\n'
        "  ]\n"
        "}\n"
        f"steps 는 최대 {_MAX_STEPS}개. 필요 없으면 빈 배열."
    )
    out = llm_adapter.complete_json(user_prompt, system=system, schema={"keys": ["steps"]})
    if not out.get("ok"):
        logger.info("llm planner failed: %s", out.get("error"))
        return None
    obj = out.get("obj") or {}
    steps = obj.get("steps")
    if not isinstance(steps, list):
        return None
    # 카탈로그에 실제 존재하는 도구만 남김.
    tool_by_name = {t["name"]: t for t in enabled_tools}
    plan: list[dict[str, Any]] = []
    seen_unit_ai: set[str] = set()
    for s in steps[:_MAX_STEPS]:
        if not isinstance(s, dict):
            continue
        name = str(s.get("tool") or "").strip()
        tool = tool_by_name.get(name)
        if not tool:
            continue
        if tool.get("kind") == "unit_ai":
            normalized = _normalize_unit_name(name, tool)
            if normalized in seen_unit_ai:
                continue
            if normalized:
                seen_unit_ai.add(normalized)
        plan.append({
            "tool": tool,
            "input": s.get("input") if isinstance(s.get("input"), dict) else {"prompt": prompt},
            "reason": str(s.get("reason") or "")[:200],
            "source": "llm",
        })
    return plan or None


def _execute_step(
    tool: dict[str, Any],
    step_input: dict[str, Any],
    *,
    request: Any | None = None,
    user: dict[str, Any] | None = None,
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """단일 step 실행. step_input 은 input_schema 기준 dict.

    일부 unit_ai 는 전용 runtime 으로 실행하고, function-call 은 stub 으로 남긴다.
    """
    name = tool.get("name") or ""
    kind = tool.get("kind") or ""
    t0 = time.perf_counter()
    out: dict[str, Any] = {"ok": False, "kind": kind, "name": name, "input": step_input, "agent_context": deepcopy(agent_context or {})}
    try:
        if kind == "unit_ai":
            if name == "filebrowser_ai_sql":
                missing = _missing_filebrowser_source_slots(step_input)
                if missing:
                    out.update({
                        "ok": False,
                        "blocked": True,
                        "status": "blocked",
                        "warnings": ["FileBrowser source slot is required before running AI SQL."],
                        "result_preview": "FileBrowser AI SQL 실행에는 DB root/product 또는 단일 file 대상이 필요합니다.",
                        "result": {
                            "handled": False,
                            "needs_input": True,
                            "missing": missing,
                            "question": "분석할 FileBrowser 대상(DB root/product 또는 단일 file)을 먼저 선택해 주세요.",
                        },
                    })
                    return _finish_exec_out(out, t0)
                from core.flowi_units.filebrowser_ai_sql_runtime import run_filebrowser_ai_sql_runtime
                payload = {
                    "natural_language": str(step_input.get("natural_language") or step_input.get("prompt") or ""),
                    "scope": str(step_input.get("scope") or ("db_product" if step_input.get("root") and step_input.get("product") else "base")),
                    "root": str(step_input.get("root") or ""),
                    "product": str(step_input.get("product") or ""),
                    "file": str(step_input.get("file") or ""),
                    "columns": step_input.get("columns") if isinstance(step_input.get("columns"), list) else [],
                    "dtypes": step_input.get("dtypes") if isinstance(step_input.get("dtypes"), dict) else {},
                    "sample_rows": step_input.get("sample_rows") if isinstance(step_input.get("sample_rows"), list) else [],
                    "preferred_selected_columns": step_input.get("preferred_selected_columns") if isinstance(step_input.get("preferred_selected_columns"), list) else [],
                }
                res = _call_runtime_with_context(
                    run_filebrowser_ai_sql_runtime,
                    payload,
                    username=str((step_input.get("username") or "")),
                    agent_context=agent_context,
                )
                try:
                    from routers import filebrowser as filebrowser_router
                    filebrowser_router._record_filebrowser_ai_sql_history(
                        str(step_input.get("username") or ""),
                        source="home_flowi_unit_ai",
                        request_payload=payload,
                        result_payload=res,
                    )
                except Exception:
                    logger.debug("home Flow-i filebrowser AI SQL history append failed", exc_info=True)
                out["ok"] = bool(res.get("ok"))
                out["status"] = _status_from_filebrowser_runtime(res)
                out["warnings"] = _warnings_from_filebrowser_runtime(res)
                out["result"] = _trim_filebrowser_runtime_result(res)
                out["sub_trace"] = deepcopy(res.get("trace") or [])
                out["result_preview"] = _summarize_filebrowser_runtime_result(res)
                return _finish_exec_out(out, t0)
            if name == "inform_registration":
                from core.flowi_units.inform_registration_runtime import run_inform_registration_runtime

                payload = {
                    "prompt": step_input.get("prompt") or step_input.get("natural_language") or "",
                    "session_id": step_input.get("session_id") or "",
                    "action": step_input.get("action") or "continue",
                    "slot_overrides": step_input.get("slot_overrides") if isinstance(step_input.get("slot_overrides"), dict) else {},
                }
                res = _call_runtime_with_context(
                    run_inform_registration_runtime,
                    payload,
                    username=str((user or {}).get("username") or step_input.get("username") or ""),
                    request=request,
                    agent_context=agent_context,
                )
                out["ok"] = bool(res.get("ok"))
                out["status"] = str(res.get("status") or ("success" if res.get("ok") else "failed"))
                out["warnings"] = _warnings_from_inform_registration_runtime(res)
                out["result"] = _trim_inform_registration_runtime_result(res)
                out["public_result"] = out["result"]
                out["sub_trace"] = deepcopy(res.get("trace") or [])
                out["result_preview"] = _summarize_inform_registration_runtime_result(res)
                return _finish_exec_out(out, t0)
            if name == "change_management":
                from core.flowi_units.change_management_runtime import run_change_management_runtime

                payload = {
                    "prompt": step_input.get("prompt") or step_input.get("natural_language") or "",
                    "meeting_id": step_input.get("meeting_id") or "",
                    "session_id": step_input.get("session_id") or "",
                }
                res = _call_runtime_with_context(
                    run_change_management_runtime,
                    payload,
                    username=str((user or {}).get("username") or step_input.get("username") or ""),
                    request=request,
                    agent_context=agent_context,
                )
                out["ok"] = bool(res.get("ok"))
                out["status"] = str(res.get("status") or ("success" if res.get("ok") else "failed"))
                out["warnings"] = _warnings_from_change_management_runtime(res)
                out["result"] = _trim_change_management_runtime_result(res)
                out["public_result"] = out["result"]
                out["sub_trace"] = deepcopy(res.get("trace") or [])
                out["result_preview"] = _summarize_change_management_runtime_result(res)
                return _finish_exec_out(out, t0)
            if name == "dashboard_agent":
                from core.flowi_units.dashboard_agent_runtime import run_dashboard_agent_runtime

                payload = {
                    "natural_language": str(step_input.get("natural_language") or step_input.get("prompt") or ""),
                    "columns": step_input.get("columns") if isinstance(step_input.get("columns"), list) else [],
                    "sample_rows": step_input.get("sample_rows") if isinstance(step_input.get("sample_rows"), list) else [],
                    "product": str(step_input.get("product") or ""),
                    "dtypes": step_input.get("dtypes") if isinstance(step_input.get("dtypes"), dict) else {},
                }
                res = _call_runtime_with_context(
                    run_dashboard_agent_runtime,
                    payload,
                    username=str((user or {}).get("username") or step_input.get("username") or ""),
                    agent_context=agent_context,
                )
                out["ok"] = bool(res.get("ok"))
                out["status"] = str(res.get("status") or ("success" if res.get("ok") else "failed"))
                out["warnings"] = _safe_string_list(res.get("warnings"), 12)
                out["result"] = _trim_dashboard_agent_runtime_result(res)
                out["public_result"] = out["result"]
                out["sub_trace"] = deepcopy(res.get("trace") or [])
                out["result_preview"] = _summarize_dashboard_agent_runtime_result(res)
                return _finish_exec_out(out, t0)
            if name == "home_sql_join_dashboard":
                from core.flowi_units.home_sql_join_dashboard_runtime import run_home_sql_join_dashboard_runtime

                payload = {
                    "natural_language": str(step_input.get("natural_language") or step_input.get("prompt") or ""),
                    "root": str(step_input.get("root") or ""),
                    "product": str(step_input.get("product") or ""),
                    "file": str(step_input.get("file") or ""),
                    "max_rows": step_input.get("max_rows") or 12,
                    "preferred_selected_columns": step_input.get("preferred_selected_columns") if isinstance(step_input.get("preferred_selected_columns"), list) else [],
                }
                res = _call_runtime_with_context(
                    run_home_sql_join_dashboard_runtime,
                    payload,
                    username=str((user or {}).get("username") or step_input.get("username") or ""),
                    agent_context=agent_context,
                )
                out["ok"] = bool(res.get("ok"))
                out["status"] = str(res.get("status") or ("blocked" if res.get("blocked") else ("success" if res.get("ok") else "failed")))
                out["warnings"] = _warnings_from_home_sql_join_dashboard_runtime(res)
                out["result"] = _trim_home_sql_join_dashboard_runtime_result(res)
                out["public_result"] = out["result"]
                out["sub_trace"] = deepcopy(res.get("trace") or [])
                out["result_preview"] = _summarize_home_sql_join_dashboard_runtime_result(res)
                return _finish_exec_out(out, t0)
            from core.flowi_units.dispatcher import try_dispatch
            prompt = str(step_input.get("prompt") or "").strip()
            product = str(step_input.get("product") or "")
            try:
                max_rows = int(step_input.get("max_rows") or 12)
            except (TypeError, ValueError):
                max_rows = 12
            res = try_dispatch(prompt, product=product, max_rows=max_rows, only=[name])
            if res and res.get("handled"):
                out["ok"] = True
                out["result"] = res
                out["result_preview"] = _summarize_result(res)
                out["raw_keys"] = sorted(res.keys())
            else:
                out["ok"] = False
                out["result_preview"] = "unit_ai 가 prompt 를 처리하지 않음 (handled=False)"
        elif kind == "function":
            out["ok"] = False
            out["result_preview"] = "function-call 은 /api/llm/flowi/chat 경로에서 호출됩니다 (현재는 trace stub)."
        else:
            out["ok"] = False
            out["result_preview"] = f"unknown kind: {kind}"
    except Exception as e:
        out["ok"] = False
        out["result_preview"] = f"{type(e).__name__}: {e}"
        logger.exception("step exec failed: %s", name)
    return _finish_exec_out(out, t0)


def _finish_exec_out(out: dict[str, Any], started: float) -> dict[str, Any]:
    out["ms"] = int((time.perf_counter() - started) * 1000)
    return out


def _missing_filebrowser_source_slots(step_input: dict[str, Any]) -> list[str]:
    has_db_target = bool(str(step_input.get("root") or "").strip() and str(step_input.get("product") or "").strip())
    has_file_target = bool(str(step_input.get("file") or "").strip())
    has_inline_context = bool(step_input.get("columns") and isinstance(step_input.get("columns"), list))
    missing: list[str] = []
    if not (has_db_target or has_file_target or has_inline_context):
        missing.extend(["root/product", "file"])
    return missing


def _warnings_from_filebrowser_runtime(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for source in (
        result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        (result.get("filter") or {}).get("warnings") if isinstance(result.get("filter"), dict) else [],
        (result.get("columns") or {}).get("warnings") if isinstance(result.get("columns"), dict) else [],
        (result.get("preview") or {}).get("warnings") if isinstance(result.get("preview"), dict) else [],
    ):
        for item in source or []:
            text = _short_text(item, 240)
            if text and text not in warnings:
                warnings.append(text)
    return warnings[:12]


def _status_from_filebrowser_runtime(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "failed"
    return "warning" if _warnings_from_filebrowser_runtime(result) else "success"


def _trim_filebrowser_runtime_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    out = {
        "ok": bool(result.get("ok")),
        "run_id": result.get("run_id") or "",
        "unit_ai": result.get("unit_ai") or "filebrowser_ai_sql",
        "graph": deepcopy(result.get("graph") or {}),
        "trace": deepcopy(result.get("trace") or []),
        "semantic_frame": deepcopy(result.get("semantic_frame") or {}),
        "filter": deepcopy(result.get("filter") or {}),
        "columns": deepcopy(result.get("columns") or {}),
        "merged": deepcopy(result.get("merged") or {}),
        "preview": deepcopy(result.get("preview") or {}),
    }
    preview = out.get("preview") if isinstance(out.get("preview"), dict) else {}
    if preview:
        preview["rows"] = _safe_rows(preview.get("rows"))
        preview["columns"] = _safe_string_list(preview.get("columns"), _MAX_PREVIEW_COLS)
    return out


def _summarize_filebrowser_runtime_result(result: dict[str, Any]) -> str:
    merged = result.get("merged") if isinstance(result.get("merged"), dict) else {}
    preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
    sql = merged.get("display_sql") or merged.get("sql") or preview.get("display_sql") or ""
    try:
        row_count = int(preview.get("rows_returned") if preview.get("rows_returned") is not None else len(preview.get("rows") or []))
    except Exception:
        row_count = len(preview.get("rows") or [])
    total = preview.get("total_rows")
    bits = []
    if sql:
        bits.append(_short_text(sql, 220))
    bits.append(f"preview {row_count} rows" + (f" / total {total}" if total is not None else ""))
    warnings = _warnings_from_filebrowser_runtime(result)
    if warnings:
        bits.append(f"warnings {len(warnings)}")
    return " · ".join(bits)


def _warnings_from_inform_registration_runtime(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for item in (result.get("warnings") if isinstance(result.get("warnings"), list) else []):
        text = _short_text(item, 240)
        if text and text not in warnings:
            warnings.append(text)
    return warnings[:12]


def _trim_inform_registration_runtime_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        "session_id": result.get("session_id") or "",
        "status": result.get("status") or "",
        "missing": _safe_string_list(result.get("missing"), 20),
        "requires_confirmation": bool(result.get("requires_confirmation")),
        "draft": deepcopy(result.get("draft") or {}),
        "created_inform": deepcopy(result.get("created_inform") or {}),
        "warnings": _warnings_from_inform_registration_runtime(result),
    }


def _summarize_inform_registration_runtime_result(result: dict[str, Any]) -> str:
    status = _short_text(result.get("status"), 80) or "unknown"
    answer = _short_text(result.get("answer") or result.get("question"), 220)
    missing = _safe_string_list(result.get("missing"), 8)
    created = result.get("created_inform") if isinstance(result.get("created_inform"), dict) else {}
    bits = [f"status {status}"]
    if created.get("id"):
        bits.append(f"inform {created.get('id')}")
    if missing:
        bits.append("missing " + ", ".join(missing[:5]))
    if answer:
        bits.append(answer)
    warnings = _warnings_from_inform_registration_runtime(result)
    if warnings:
        bits.append(f"warnings {len(warnings)}")
    return " · ".join(bits)


def _warnings_from_change_management_runtime(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for item in (result.get("warnings") if isinstance(result.get("warnings"), list) else []):
        text = _short_text(item, 240)
        if text and text not in warnings:
            warnings.append(text)
    return warnings[:12]


def _trim_change_management_runtime_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    return {
        "status": result.get("status") or "",
        "answer": _short_text(result.get("answer"), 1200),
        "needs_clarification": bool(result.get("needs_clarification")),
        "meeting_reference": deepcopy(result.get("meeting_reference") or {}),
        "meeting": deepcopy(result.get("meeting") or {}),
        "meetings": deepcopy((result.get("meetings") or [])[:20]) if isinstance(result.get("meetings"), list) else [],
        "sources": deepcopy((result.get("sources") or [])[:50]) if isinstance(result.get("sources"), list) else [],
        "calendar_events": deepcopy((result.get("calendar_events") or [])[:50]) if isinstance(result.get("calendar_events"), list) else [],
        "warnings": _warnings_from_change_management_runtime(result),
    }


def _summarize_change_management_runtime_result(result: dict[str, Any]) -> str:
    status = _short_text(result.get("status"), 80) or "unknown"
    answer = _short_text(result.get("answer"), 260)
    meetings = result.get("meetings") if isinstance(result.get("meetings"), list) else []
    events = result.get("calendar_events") if isinstance(result.get("calendar_events"), list) else []
    bits = [f"status {status}"]
    if meetings:
        bits.append(f"회의 {len(meetings)}건")
    if events:
        bits.append(f"변경점 이벤트 {len(events)}건")
    if answer:
        bits.append(answer)
    warnings = _warnings_from_change_management_runtime(result)
    if warnings:
        bits.append(f"warnings {len(warnings)}")
    return " · ".join(bits)


def _trim_dashboard_agent_runtime_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    chart_result = result.get("chart_result") if isinstance(result.get("chart_result"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status") or "",
        "run_id": result.get("run_id") or "",
        "unit_ai": result.get("unit_ai") or "dashboard_agent",
        "graph": deepcopy(result.get("graph") or {}),
        "trace": deepcopy(result.get("trace") or []),
        "semantic_frame": deepcopy(result.get("semantic_frame") or {}),
        "chart_type": result.get("chart_type") or chart_result.get("chart_type") or "",
        "params": deepcopy(result.get("params") or {}),
        "config": deepcopy(result.get("config") or chart_result.get("config") or {}),
        "chart_result": deepcopy(chart_result),
        "warnings": _safe_string_list(result.get("warnings"), 12),
    }


def _summarize_dashboard_agent_runtime_result(result: dict[str, Any]) -> str:
    status = _short_text(result.get("status"), 80) or "unknown"
    chart_result = result.get("chart_result") if isinstance(result.get("chart_result"), dict) else {}
    chart_type = _short_text(result.get("chart_type") or chart_result.get("chart_type"), 80)
    total = chart_result.get("total")
    bits = [f"status {status}"]
    if chart_type:
        bits.append(f"chart {chart_type}")
    if total is not None:
        bits.append(f"points {total}")
    warnings = _safe_string_list(result.get("warnings"), 12)
    if warnings:
        bits.append(f"warnings {len(warnings)}")
    return " · ".join(bits)


def _warnings_from_home_sql_join_dashboard_runtime(result: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    sources = [
        result.get("warnings") if isinstance(result.get("warnings"), list) else [],
        (result.get("joined") or {}).get("warnings") if isinstance(result.get("joined"), dict) else [],
        (result.get("join_plan") or {}).get("missing_evidence") if isinstance(result.get("join_plan"), dict) else [],
        (result.get("ai_sql") or {}).get("sub_warnings") if isinstance(result.get("ai_sql"), dict) else [],
    ]
    for source in sources:
        for item in source or []:
            text = _short_text(item, 240)
            if text and text not in warnings:
                warnings.append(text)
    return warnings[:12]


def _trim_home_sql_join_dashboard_runtime_result(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    joined = result.get("joined") if isinstance(result.get("joined"), dict) else {}
    ai_sql = result.get("ai_sql") if isinstance(result.get("ai_sql"), dict) else {}
    join_plan = result.get("join_plan") if isinstance(result.get("join_plan"), dict) else {}
    dashboard = result.get("dashboard") if isinstance(result.get("dashboard"), dict) else {}
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status") or "",
        "blocked": bool(result.get("blocked")),
        "run_id": result.get("run_id") or "",
        "unit_ai": result.get("unit_ai") or "home_sql_join_dashboard",
        "graph": deepcopy(result.get("graph") or {}),
        "trace": deepcopy(result.get("trace") or []),
        "base_source": deepcopy(result.get("base_source") or {}),
        "ai_sql": {
            "where_sql": _short_text(ai_sql.get("where_sql"), 1000),
            "display_sql": _short_text(ai_sql.get("display_sql"), 1000),
            "selected_columns": _safe_string_list(ai_sql.get("selected_columns"), _MAX_PREVIEW_COLS),
            "sort": deepcopy(ai_sql.get("sort") or {}),
            "preview_total_rows": ai_sql.get("preview_total_rows"),
            "ok": bool(ai_sql.get("ok")),
        },
        "join_candidates": deepcopy(result.get("join_candidates") or [])[:10],
        "join_plan": {
            "sources": deepcopy(join_plan.get("sources") or [])[:6],
            "relation_ids": _safe_string_list(join_plan.get("relation_ids"), 20),
            "join_keys": _safe_string_list(join_plan.get("join_keys"), 20),
            "steps": deepcopy(join_plan.get("steps") or [])[:6],
            "missing_evidence": _safe_string_list(join_plan.get("missing_evidence"), 12),
            "blocked": bool(join_plan.get("blocked")),
            "single_source": bool(join_plan.get("single_source")),
        },
        "joined": {
            "row_count": joined.get("row_count") or 0,
            "columns": _safe_string_list(joined.get("columns"), _MAX_PREVIEW_COLS),
            "sample_rows": _safe_rows(joined.get("sample_rows")),
            "blocked": bool(joined.get("blocked")),
            "reason": joined.get("reason") or "",
            "fallback": joined.get("fallback") or "",
            "filters": deepcopy(joined.get("filters") or {}),
        },
        "output_route": deepcopy(result.get("output_route") or {}),
        "dashboard": deepcopy(dashboard),
        "warnings": _warnings_from_home_sql_join_dashboard_runtime(result),
    }


def _summarize_home_sql_join_dashboard_runtime_result(result: dict[str, Any]) -> str:
    status = _short_text(result.get("status"), 80) or "unknown"
    joined = result.get("joined") if isinstance(result.get("joined"), dict) else {}
    route = result.get("output_route") if isinstance(result.get("output_route"), dict) else {}
    join_plan = result.get("join_plan") if isinstance(result.get("join_plan"), dict) else {}
    dashboard = result.get("dashboard") if isinstance(result.get("dashboard"), dict) else {}
    bits = [f"status {status}"]
    if join_plan.get("relation_ids"):
        bits.append("join " + ", ".join(_safe_string_list(join_plan.get("relation_ids"), 3)))
    if joined.get("row_count") is not None:
        bits.append(f"rows {joined.get('row_count')}")
    mode = _short_text(route.get("mode"), 16)
    if mode:
        bits.append(f"mode {mode}")
    if dashboard and not dashboard.get("skipped") and dashboard.get("chart_type"):
        bits.append(f"chart {dashboard.get('chart_type')}")
    warnings = _warnings_from_home_sql_join_dashboard_runtime(result)
    if warnings:
        bits.append(f"warnings {len(warnings)}")
    return " · ".join(bits)


def _plan_from_heuristic(prompt: str, top_k: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """휴리스틱 keyword 매칭 → step list. orchestrate fallback 용."""
    picks, meta = _pick_tools(prompt, top_k=top_k)
    plan: list[dict[str, Any]] = []
    for p in picks:
        plan.append({
            "tool": p["tool"],
            "input": {"prompt": prompt, "product": "", "max_rows": 12},
            "reason": f"휴리스틱 score={round(p['score'], 2)}",
            "source": "heuristic",
            "score": p["score"],
        })
    return plan, meta


def _make_trace_row(step: dict[str, Any], exec_out: dict[str, Any]) -> dict[str, Any]:
    tool = step["tool"]
    score = step.get("score")
    row = {
        "tool": tool["name"],
        "kind": tool["kind"],
        "title": tool["title"],
        "ok": bool(exec_out.get("ok")),
        "status": exec_out.get("status") or ("success" if exec_out.get("ok") else ("blocked" if exec_out.get("blocked") else "failed")),
        "blocked": bool(exec_out.get("blocked")),
        "ms": exec_out.get("ms", 0),
        "input": step.get("input") or {},
        "result_preview": exec_out.get("result_preview", ""),
        "warnings": list(exec_out.get("warnings") or []),
        "reason": step.get("reason", ""),
        "source": step.get("source", ""),
    }
    if score is not None:
        row["score"] = round(score, 2)
        row["confidence"] = round(min(1.0, score / 5.0), 2)
    if "public_result" in exec_out:
        row["result"] = deepcopy(exec_out.get("public_result") or {})
    if exec_out.get("sub_trace"):
        row["sub_trace"] = deepcopy(exec_out.get("sub_trace") or [])
    return row


def _synthesize_reply(trace: list[dict[str, Any]]) -> str:
    succ = [tr for tr in trace if tr.get("ok")]
    if succ:
        head = succ[0]
        return f"[{head['title']}] {head['result_preview']}"
    blocked = [tr for tr in trace if tr.get("blocked")]
    if blocked:
        head = blocked[0]
        return f"[{head['title']}] {head['result_preview']}"
    return "도구를 선택했지만 prompt 가 자세하지 않아 결과를 만들지 못했습니다. 트레이스를 참고해 인자를 보완해 주세요."


def _tool_summary_from_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    if not trace:
        return {}
    first = trace[0]
    return {
        "feature": first.get("tool") or "",
        "action": first.get("kind") or "",
        "intent": first.get("tool") or "",
        "blocked": bool(first.get("blocked")),
        "missing": ((first.get("result") or {}).get("missing") if isinstance(first.get("result"), dict) else []) or [],
        "warnings": first.get("warnings") or [],
        "inline_summary": first.get("result_preview") or "",
    }


def _make_tool_call(step: dict[str, Any], exec_out: dict[str, Any]) -> ToolCall:
    status = str(exec_out.get("status") or ("success" if exec_out.get("ok") else ("blocked" if exec_out.get("blocked") else "failed")))
    output = exec_out.get("result") if isinstance(exec_out.get("result"), dict) else {}
    return {
        "tool": str((step.get("tool") or {}).get("name") or exec_out.get("name") or ""),
        "input": deepcopy(step.get("input") or exec_out.get("input") or {}),
        "output": deepcopy(output),
        "status": status if status in {"success", "warning", "failed", "blocked"} else ("success" if exec_out.get("ok") else "failed"),
        "sub_trace": deepcopy(exec_out.get("sub_trace") or []),
        "warnings": list(exec_out.get("warnings") or []),
    }


def _parent_context_from_tool_calls(tool_calls: list[ToolCall]) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    last_output: dict[str, Any] = {}
    for call in tool_calls[-5:]:
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        last_output = output or last_output
        summary: dict[str, Any] = {
            "tool": call.get("tool") or "",
            "status": call.get("status") or "",
            "warnings": list(call.get("warnings") or [])[:5],
        }
        for key in (
            "product",
            "lot_id",
            "root_lot_id",
            "wafer_id",
            "semantic_frame",
            "merged",
            "preview",
            "joined",
            "chart_result",
            "columns",
        ):
            if key in output:
                summary[key] = deepcopy(output.get(key))
        summaries.append(summary)
    return {
        "tool_calls": summaries,
        "last_output": deepcopy(last_output),
    }


def _merge_step_input_with_parent(step_input: dict[str, Any], parent_context: dict[str, Any]) -> dict[str, Any]:
    merged = dict(step_input or {})
    last_output = parent_context.get("last_output") if isinstance(parent_context.get("last_output"), dict) else {}
    if not last_output:
        return merged
    if not merged.get("columns"):
        preview = last_output.get("preview") if isinstance(last_output.get("preview"), dict) else {}
        joined = last_output.get("joined") if isinstance(last_output.get("joined"), dict) else {}
        columns_result = last_output.get("columns") if isinstance(last_output.get("columns"), dict) else {}
        columns = preview.get("columns") or joined.get("columns") or columns_result.get("selected_columns") or []
        if isinstance(columns, list):
            merged["columns"] = columns
    if not merged.get("sample_rows"):
        preview = last_output.get("preview") if isinstance(last_output.get("preview"), dict) else {}
        joined = last_output.get("joined") if isinstance(last_output.get("joined"), dict) else {}
        rows = preview.get("rows") or joined.get("sample_rows") or []
        if isinstance(rows, list):
            merged["sample_rows"] = rows
    for key in ("product", "lot_id", "root_lot_id", "wafer_id"):
        if not merged.get(key) and isinstance(last_output.get(key), str):
            merged[key] = last_output.get(key)
    return merged


def _attach_runtime_result(
    out: dict[str, Any],
    *,
    prompt: str,
    user: dict[str, Any] | None,
    source: str = "home_agent",
    run_id: str | None = None,
) -> dict[str, Any]:
    trace = out.get("trace") if isinstance(out.get("trace"), list) else []
    if "tool" not in out:
        out["tool"] = _tool_summary_from_trace(trace)
    snapshot = build_home_runtime_snapshot(
        prompt=prompt,
        result=out,
        user=user,
        source=source,
        run_id=run_id,
        save=True,
    )
    out["run_id"] = snapshot["run_id"]
    out["graph"] = snapshot["graph"]
    out["action_log"] = snapshot["action_log"]
    out["runtime_status"] = snapshot["status"]
    try:
        row = home_memory.remember_turn(
            username=str((user or {}).get("username") or out.get("user") or ""),
            prompt=prompt,
            answer=str(out.get("reply") or out.get("answer") or ""),
            tool=out.get("tool") if isinstance(out.get("tool"), dict) else {},
            source=source,
            run_id=str(out.get("run_id") or ""),
        )
        out["home_memory"] = {"stored": bool(row), "memory_id": (row or {}).get("memory_id") or ""}
    except Exception:
        logger.debug("home orchestrator memory append failed", exc_info=True)
    return out


def _plan_from_alias(prompt: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """사용자가 등록한 alias 가 매칭되면 단일 step plan 으로 변환."""
    try:
        from core import agent_feedback
    except Exception:
        return None
    matched = agent_feedback.match_alias(prompt)
    if not matched:
        return None
    name = str(matched.get("tool") or "").strip()
    tool = next((t for t in tools if t.get("name") == name and t.get("enabled")), None)
    if not tool:
        return None
    return [{
        "tool": tool,
        "input": {"prompt": prompt, "product": "", "max_rows": 12},
        "reason": f"사용자 alias 매칭 (pattern='{matched.get('pattern')}')",
        "source": "alias",
    }]


def orchestrate(
    prompt: str,
    user: dict[str, Any] | None = None,
    top_k: int = 2,
    *,
    request: Any | None = None,
) -> dict[str, Any]:
    """홈 에이전트 메인 엔트리포인트.

    prompt → alias 매칭 → LLM planner (선택) → 휴리스틱 → step 실행 → trace + 응답.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        return {"ok": False, "error": "빈 prompt", "trace": []}
    username = str((user or {}).get("username") or "")
    if home_memory.is_memory_recall_prompt(prompt):
        tool = home_memory.recall_answer(prompt=prompt, username=username, agent_context=None)
        return _attach_runtime_result({
            "ok": True,
            "prompt": prompt,
            "trace": [],
            "meta": {"planner": "home_memory", "step_count": 0},
            "reply": tool.get("answer") or "",
            "tool": tool,
            "picked_count": 0,
        }, prompt=prompt, user=user)

    tools = tool_registry.list_tools(include_stats=False)
    plan: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {"planner": "heuristic"}

    plan = _plan_from_alias(prompt, tools)
    if plan:
        meta = {"planner": "alias", "step_count": len(plan)}
    if not plan and _llm_planner_enabled():
        plan = _plan_with_llm(prompt, tools)
        if plan:
            meta = {"planner": "llm", "step_count": len(plan)}
    if not plan:
        plan, hmeta = _plan_from_heuristic(prompt, top_k=top_k)
        meta.update(hmeta)
        meta["planner"] = "heuristic"

    if not plan:
        return _attach_runtime_result({
            "ok": False,
            "prompt": prompt,
            "trace": [],
            "meta": meta,
            "reply": "키워드 매칭으로 적합한 도구를 찾지 못했습니다. AI 허브에서 도구를 활성화하거나 더 구체적인 단어를 사용해 주세요.",
        }, prompt=prompt, user=user)

    trace: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []
    accumulated: dict[str, Any] = {}
    for step in plan[:_MAX_STEPS]:
        parent_context = _parent_context_from_tool_calls(tool_calls)
        merged_input = {**step.get("input", {}), **{k: v for k, v in accumulated.items() if k not in (step.get("input") or {})}}
        merged_input = _merge_step_input_with_parent(merged_input, parent_context)
        if "prompt" not in merged_input:
            merged_input["prompt"] = prompt
        step["input"] = merged_input
        exec_out = _execute_step(step["tool"], merged_input, request=request, user=user, agent_context=parent_context)
        trace.append(_make_trace_row(step, exec_out))
        tool_calls.append(_make_tool_call(step, exec_out))
        # 다음 step input 에 사용할 수 있도록 product 같은 단순 값 누적.
        if exec_out.get("ok") and isinstance(exec_out.get("result"), dict):
            res = exec_out["result"]
            for key in ("product", "lot_id", "root_lot_id", "wafer_id"):
                v = res.get(key)
                if isinstance(v, str) and v and key not in accumulated:
                    accumulated[key] = v

    return _attach_runtime_result({
        "ok": True,
        "prompt": prompt,
        "trace": trace,
        "tool_calls": tool_calls,
        "meta": meta,
        "reply": _synthesize_reply(trace),
        "picked_count": len(trace),
    }, prompt=prompt, user=user)


def orchestrate_stream(
    prompt: str,
    user: dict[str, Any] | None = None,
    top_k: int = 2,
    *,
    request: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """SSE 용 generator. orchestrate 와 동일하지만 step 별로 event 를 yield 한다.

    소비 측 (FastAPI StreamingResponse)은 각 dict 를 `event: <type>\\ndata: <json>\\n\\n`
    으로 직렬화한다.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        yield {"type": "reply", "ok": False, "error": "빈 prompt", "trace": []}
        return

    run_id = _new_run_id()
    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "catalog",
        "status": "단위AI 기능 탐색중",
        "graph": build_home_runtime_graph(statuses={"prompt_input": "running"}),
    }
    tools = tool_registry.list_tools(include_stats=False)
    plan: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {"planner": "heuristic"}
    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "semantic_layer",
        "status": "용어해석중",
        "graph": build_home_runtime_graph(statuses={"prompt_input": "success", "semantic_layer": "running"}),
    }
    plan = _plan_from_alias(prompt, tools)
    if plan:
        meta = {"planner": "alias", "step_count": len(plan)}
    if not plan and _llm_planner_enabled():
        plan = _plan_with_llm(prompt, tools)
        if plan:
            meta = {"planner": "llm", "step_count": len(plan)}
    if not plan:
        plan, hmeta = _plan_from_heuristic(prompt, top_k=top_k)
        meta.update(hmeta)
        meta["planner"] = "heuristic"
    selected_units = [
        name
        for name in (
            _normalize_unit_name(str((step.get("tool") or {}).get("name") or ""))
            for step in (plan or [])
            if (step.get("tool") or {}).get("kind") == "unit_ai"
        )
        if name
    ]
    plan_statuses = {"prompt_input": "success", "semantic_layer": "success", "orchestrator": "running"}
    for name in selected_units:
        plan_statuses[f"unit_ai:{name}"] = "planned"
    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "orchestrator",
        "status": "실행 계획 정리중",
        "graph": build_home_runtime_graph(selected_units=selected_units, statuses=plan_statuses),
    }

    yield {
        "type": "plan",
        "run_id": run_id,
        "meta": meta,
        "graph": build_home_runtime_graph(selected_units=selected_units, statuses=plan_statuses),
        "steps": [
            {
                "tool": s["tool"]["name"],
                "kind": s["tool"]["kind"],
                "title": s["tool"]["title"],
                "input": s.get("input") or {},
                "reason": s.get("reason", ""),
            }
            for s in (plan or [])
        ],
    }

    if not plan:
        final = _attach_runtime_result({
            "type": "reply",
            "ok": False,
            "trace": [],
            "reply": "키워드 매칭으로 적합한 도구를 찾지 못했습니다.",
            "meta": meta,
        }, prompt=prompt, user=user, run_id=run_id)
        yield final
        return

    trace: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []
    accumulated: dict[str, Any] = {}
    for idx, step in enumerate(plan[:_MAX_STEPS]):
        parent_context = _parent_context_from_tool_calls(tool_calls)
        merged_input = {**step.get("input", {}), **{k: v for k, v in accumulated.items() if k not in (step.get("input") or {})}}
        merged_input = _merge_step_input_with_parent(merged_input, parent_context)
        if "prompt" not in merged_input:
            merged_input["prompt"] = prompt
        step["input"] = merged_input
        running_unit = _normalize_unit_name(str(step["tool"].get("name") or "")) if step["tool"].get("kind") == "unit_ai" else ""
        running_statuses = dict(plan_statuses)
        running_statuses["orchestrator"] = "success"
        if running_unit:
            running_statuses[f"unit_ai:{running_unit}"] = "running"
        yield {
            "type": "step_start",
            "run_id": run_id,
            "status": "단위AI 실행중" if step["tool"].get("kind") == "unit_ai" else "실행 계획 정리중",
            "graph": build_home_runtime_graph(selected_units=selected_units, statuses=running_statuses),
            "index": idx,
            "tool": step["tool"]["name"],
            "kind": step["tool"]["kind"],
            "input": merged_input,
        }
        exec_out = _execute_step(step["tool"], merged_input, request=request, user=user, agent_context=parent_context)
        row = _make_trace_row(step, exec_out)
        trace.append(row)
        tool_calls.append(_make_tool_call(step, exec_out))
        step_statuses = _runtime_statuses({"ok": True, "trace": trace, "tool": _tool_summary_from_trace(trace)}, selected_units)
        yield {
            "type": "step_end",
            "run_id": run_id,
            "status": "단위AI 실행중" if step["tool"].get("kind") == "unit_ai" else "실행 계획 정리중",
            "graph": build_home_runtime_graph(selected_units=selected_units, statuses=step_statuses),
            "index": idx,
            "tool": step["tool"]["name"],
            "ok": row["ok"],
            "node_status": row.get("status"),
            "ms": row["ms"],
            "result_preview": row["result_preview"],
        }
        if exec_out.get("ok") and isinstance(exec_out.get("result"), dict):
            res = exec_out["result"]
            for key in ("product", "lot_id", "root_lot_id", "wafer_id"):
                v = res.get(key)
                if isinstance(v, str) and v and key not in accumulated:
                    accumulated[key] = v

    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "result_renderer",
        "status": "결과 정리중",
        "graph": build_home_runtime_graph(
            selected_units=selected_units,
            statuses=_runtime_statuses({"ok": True, "trace": trace, "tool": _tool_summary_from_trace(trace)}, selected_units),
        ),
    }
    final = _attach_runtime_result({
        "type": "reply",
        "ok": True,
        "trace": trace,
        "tool_calls": tool_calls,
        "reply": _synthesize_reply(trace),
        "picked_count": len(trace),
        "meta": meta,
    }, prompt=prompt, user=user, run_id=run_id)
    yield final
