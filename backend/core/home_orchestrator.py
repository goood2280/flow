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

import functools
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

from core import agent_feedback_penalties
from core import home_memory
from core import tool_registry
from core.agent_tool_contract import ToolCall
from core.paths import PATHS

logger = logging.getLogger("flow.home_orchestrator")

_MAX_STEPS = 3
_LLM_ENV_FLAG = "FLOW_LLM_TOOL_CALL"
_REACT_ENV_FLAG = "FLOW_LLM_REACT_LOOP"
_MAX_ITERATIONS = 4
_REACT_MAX_ITERS_ENV = "FLOW_LLM_REACT_MAX_ITERS"
_REACT_DEADLINE_ENV = "FLOW_LLM_REACT_DEADLINE_SECONDS"
_REACT_ADMIN_MAX_ITERS_ENV = "FLOW_LLM_REACT_ADMIN_MAX_ITERS"
_REACT_ADMIN_DEADLINE_ENV = "FLOW_LLM_REACT_ADMIN_DEADLINE_SECONDS"
_REACT_DECISION_TIMEOUT_S = 8
_REACT_DEFAULT_DEADLINE_S = 90
_REACT_ADMIN_DEFAULT_DEADLINE_S = 300
_REACT_ADMIN_DEFAULT_MAX_ITERS = 16
# 홈 요청 스코프 LLM 예산. 분당 window(25)보다 한 단계 낮게 둬 1질문이
# 전역 쿼터를 통째로 먹지 않게 한다. 정확도 우선이라 여유 있게 잡고,
# env 로만 조정한다 (config/는 seed-only).
_HOME_TURN_LLM_LIMIT_ENV = "FLOW_HOME_TURN_LLM_LIMIT"
_HOME_TURN_LLM_ADMIN_LIMIT_ENV = "FLOW_HOME_TURN_LLM_ADMIN_LIMIT"
HOME_TURN_LLM_LIMIT = 20
HOME_TURN_LLM_ADMIN_LIMIT = 24
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
    (re.compile(r"(대시보드|dashboard)", re.I), {"chart": 1.5, "dashboard": 2.5}),
    (re.compile(r"(어디|어딨|어디있|위치|현위치|현재위치)", re.I), {"lot": 2.0, "fab": 1.0}),
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
    mixed_source_chart = (
        (signals.get("chart") or signals.get("dashboard"))
        and any(signals.get(tag) for tag in ("sql_workspace", "filebrowser", "tablemap", "fab", "lot"))
    )
    if nm == "dashboard_agent" and mixed_source_chart:
        score += 1.0
    if score <= 0.0:
        return score
    adjustment = agent_feedback_penalties.tool_score_adjustment(tool)
    score += float(adjustment.get("score_adjustment") or 0.0)
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
        "context",
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
    if name == "home_sql_join_dashboard" and "dashboard_agent" in unit_names:
        return "dashboard_agent"
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
    iterations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the public Home Flow-i runtime graph shown in Agent.

    The graph is deliberately public-state only: node status, short labels, and
    MCP candidate availability. It does not expose model reasoning text.

    ``iterations`` is optional and additive: when provided (ReAct 실행), 반복 노드
    chain 을 result_renderer 로 연결한다. None 이면 기존 단일 패스 그래프와 동일하다.
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
            "feedback_penalty": agent_feedback_penalties.tool_score_adjustment(name),
            "status": statuses.get(node_id, default),
        })
    nodes.extend(unit_nodes)
    edges = [dict(edge) for edge in _RUNTIME_BASE_EDGES]
    for node in unit_nodes:
        edges.append({"source": "orchestrator", "target": node["id"]})
    iter_list = iterations if isinstance(iterations, list) else []
    if iter_list:
        # ReAct 반복 노드 chain: orchestrator → iter:0 → … → iter:last → result_renderer.
        prev = "orchestrator"
        for it in iter_list:
            idx = it.get("index")
            tool_name = str(it.get("tool") or "")
            node_id = f"iter:{idx}:{tool_name}"
            label_no = (int(idx) + 1) if isinstance(idx, int) else "?"
            nodes.append({
                "id": node_id,
                "label": f"#{label_no} {it.get('title') or tool_name}",
                "phase": "react_iteration",
                "kind": "unit_ai",
                "tool": tool_name,
                "status": it.get("status") or "success",
            })
            edges.append({"source": prev, "target": node_id})
            prev = node_id
        edges.append({"source": prev, "target": "result_renderer"})
        return {"nodes": nodes, "edges": edges}
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


def _iterations_from_trace(trace: Any) -> list[dict[str, Any]]:
    """ReAct trace(list of trace rows)를 공개 가능한 iteration 요약 list 로.

    snapshot graph 의 반복 노드와 node_details, 그리고 snapshot 의 additive
    `iterations` 필드가 공유한다.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(trace, list):
        return out
    for idx, row in enumerate(trace):
        if not isinstance(row, dict):
            continue
        out.append({
            "index": idx,
            "tool": str(row.get("tool") or ""),
            "title": _short_text(row.get("title") or row.get("tool") or f"#{idx + 1}", 80),
            "status": _status_from_trace_row(row),
            "ok": bool(row.get("ok")),
            "ms": row.get("ms", 0),
            "result_preview": _short_text(row.get("result_preview"), 400),
            "reason": _short_text(row.get("reason"), 200),
        })
    return out


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
    iterations: list[dict[str, Any]] | None = None,
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
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    if not semantic and isinstance(meta.get("semantic_summary"), dict):
        # ReAct(list-trace) 실행은 trace 가 dict 가 아니므로 meta 에 담긴 frame 을 노출.
        semantic = meta.get("semantic_summary")
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
    for it in (iterations or []):
        idx = it.get("index")
        row = trace_rows[idx] if isinstance(idx, int) and 0 <= idx < len(trace_rows) and isinstance(trace_rows[idx], dict) else {}
        node_id = f"iter:{idx}:{it.get('tool') or ''}"
        details[node_id] = {
            "node_id": node_id,
            "status": it.get("status") or "success",
            "input_summary": row.get("input") if isinstance(row.get("input"), dict) else {},
            "output_summary": {
                "tool": it.get("tool") or "",
                "title": it.get("title") or it.get("tool") or "",
                "result_preview": it.get("result_preview") or "",
                "reason": it.get("reason") or "",
            },
            "warnings": _safe_string_list(row.get("warnings"), 8),
            "preview": {},
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
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    is_react = meta.get("planner") == "react"
    iterations = _iterations_from_trace(result.get("trace")) if is_react else []
    graph = build_home_runtime_graph(
        selected_units=selected_units,
        statuses=statuses,
        iterations=iterations or None,
    )
    snapshot = {
        "ok": bool(result.get("ok", True)),
        "run_id": run_id,
        "created_at": _now_iso(),
        "source": source,
        "username": _short_text((user or {}).get("username") or result.get("user") or "", 80),
        "prompt": _short_text(prompt, 2000),
        "input_prompt": _short_text(result.get("input_prompt"), 2000),
        "resolved_prompt": _short_text(result.get("resolved_prompt") or result.get("prompt"), 2000),
        "status": statuses.get("result_renderer", "pending"),
        "reply": result.get("reply") or result.get("answer") or "",
        "graph": graph,
        "trace": deepcopy(result.get("trace") or []),
        "action_log": {},
        "tool": _safe_public_tool(result.get("tool") if isinstance(result.get("tool"), dict) else {}),
        "node_details": {},
        "warnings": _result_warnings(result),
    }
    if is_react:
        # additive 필드 — 프론트는 미지 필드를 무시한다.
        snapshot["iterations"] = iterations
        snapshot["stop_reason"] = _short_text(meta.get("stop_reason"), 60)
        snapshot["semantic_frame"] = meta.get("semantic_summary") if isinstance(meta.get("semantic_summary"), dict) else {}
    snapshot["action_log"] = _synth_action_log(result, graph)
    snapshot["node_details"] = _node_details_for_result(
        prompt=prompt,
        result=result,
        selected_units=selected_units,
        statuses=statuses,
        source=source,
        iterations=iterations,
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
            "input_prompt": _short_text(data.get("input_prompt"), 240),
            "resolved_prompt": _short_text(data.get("resolved_prompt") or data.get("prompt"), 240),
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
    penalty_meta = {
        str(t.get("name") or ""): adj
        for t, _score in scored
        for adj in [agent_feedback_penalties.tool_score_adjustment(t)]
        if adj.get("penalty") or adj.get("boost")
    }
    meta = {
        "signals": signals,
        "matched_terms": matched_terms,
        "candidate_count": len(scored),
        "feedback_penalties": penalty_meta,
    }
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


# 관찰 1건이 차지할 수 있는 최대 길이. 목록형 답변(step 별 분포 등)이 300자에서
# 단어 중간에 잘리면 모델이 그 잘린 문장을 그대로 최종 답변으로 되뱉는다.
_OBSERVATION_CHARS = 1200


def _clip_observation(text: str, limit: int = _OBSERVATION_CHARS) -> str:
    """관찰 텍스트를 줄 경계에서 자르고 잘렸다는 사실을 남긴다."""
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean
    head = clean[:limit]
    cut = head.rfind("\n")
    if cut < limit // 2:
        cut = len(head)
    return head[:cut].rstrip() + "\n…(관찰 일부 생략 — 전체는 화면의 표/출처 참고)"


def _summarize_result(res: dict[str, Any]) -> str:
    if not isinstance(res, dict):
        return str(res)[:200]
    for key in ("text", "answer", "message", "summary"):
        v = res.get(key)
        if isinstance(v, str) and v.strip():
            return _clip_observation(v)
    keys = [k for k in res.keys() if k != "handled"]
    return f"키: {', '.join(sorted(keys))[:200]}"


def _agentic_settings_flag(name: str) -> bool:
    """admin_settings.json flowi_defaults.agentic 의 토글 (env 미설정 시 사용)."""
    try:
        from core.utils import load_json
        adm = load_json(PATHS.data_root / "admin_settings.json", {}) or {}
        defaults = adm.get("flowi_defaults") if isinstance(adm.get("flowi_defaults"), dict) else {}
        agentic = defaults.get("agentic") if isinstance(defaults.get("agentic"), dict) else {}
        return bool(agentic.get(name))
    except Exception:
        return False


def _flag_enabled(env_name: str, settings_key: str) -> bool:
    """env 가 명시돼 있으면 env 우선, 아니면 admin 설정(flowi_defaults.agentic)."""
    raw = str(os.environ.get(env_name, "")).strip()
    if raw:
        return raw.lower() in ("1", "true", "yes", "on")
    return _agentic_settings_flag(settings_key)


def _llm_planner_enabled() -> bool:
    """LLM planner 사용 여부.

    env `FLOW_LLM_TOOL_CALL` 이 명시돼 있으면 그 값을, 아니면 admin 설정
    `flowi_defaults.agentic.tool_call_enabled` 를 따른다. llm_adapter 가
    enabled 이어야 활성. 어느 쪽이든 비활성이면 휴리스틱 fallback.
    """
    if not _flag_enabled(_LLM_ENV_FLAG, "tool_call_enabled"):
        return False
    try:
        from core import llm_adapter
        return bool(llm_adapter.is_available())
    except Exception:
        return False


def _react_loop_enabled() -> bool:
    """반복 ReAct 루프 사용 여부.

    env `FLOW_LLM_REACT_LOOP` 이 명시돼 있으면 그 값을, 아니면 admin 설정
    `flowi_defaults.agentic.react_loop_enabled` 를 따른다. LLM planner도
    활성일 때만 True. 어느 하나라도 꺼져 있으면 기존 단일 패스 경로로
    graceful degrade 한다. 기본 off — 회귀 위험 0 유지.
    """
    if not _flag_enabled(_REACT_ENV_FLAG, "react_loop_enabled"):
        return False
    return _llm_planner_enabled()


def _user_role(user: dict[str, Any] | None) -> str:
    return "admin" if str((user or {}).get("role") or "") == "admin" else "user"


def _react_max_iters(role: str = "user") -> int:
    """반복 루프 상한. 일반 유저는 env `FLOW_LLM_REACT_MAX_ITERS` override,
    [1, 8] clamp (기본 8 — 턴이 길게 늘어지지 않도록 끊는 운영 정책).

    admin 은 RCA 류 다단계 분석을 위해 더 긴 루프를 허용한다 —
    env `FLOW_LLM_REACT_ADMIN_MAX_ITERS` override, [1, 24] clamp, 기본 16."""
    if role == "admin":
        raw = str(os.environ.get(_REACT_ADMIN_MAX_ITERS_ENV, "")).strip()
        try:
            value = int(raw) if raw else _REACT_ADMIN_DEFAULT_MAX_ITERS
        except (TypeError, ValueError):
            value = _REACT_ADMIN_DEFAULT_MAX_ITERS
        return max(1, min(24, value))
    raw = str(os.environ.get(_REACT_MAX_ITERS_ENV, "")).strip()
    try:
        value = int(raw) if raw else 8
    except (TypeError, ValueError):
        value = 8
    return max(1, min(8, value))


def react_available() -> bool:
    """홈 챗 경로에서 ReAct 오케스트레이션을 쓸 수 있는지 (LLM + 플래그)."""
    return _react_loop_enabled()


def home_turn_limit(role: str = "user") -> int:
    """홈 요청 스코프 LLM 상한. 일반 20·admin 24, env override, [1, 25] clamp.

    분당 window(25)보다 낮게 둬 1질문이 전역 쿼터를 고갈시키지 않게 한다.
    """
    if role == "admin":
        raw = str(os.environ.get(_HOME_TURN_LLM_ADMIN_LIMIT_ENV, "")).strip()
        try:
            value = int(raw) if raw else HOME_TURN_LLM_ADMIN_LIMIT
        except (TypeError, ValueError):
            value = HOME_TURN_LLM_ADMIN_LIMIT
        return max(1, min(25, value))
    raw = str(os.environ.get(_HOME_TURN_LLM_LIMIT_ENV, "")).strip()
    try:
        value = int(raw) if raw else HOME_TURN_LLM_LIMIT
    except (TypeError, ValueError):
        value = HOME_TURN_LLM_LIMIT
    return max(1, min(25, value))


def _with_home_turn_budget(func):
    """홈 진입점을 turn_budget 으로 감싼다. sync 함수·제너레이터 모두 지원.

    planner 결정·런타임·최종 요약을 같은 요청 카운터로 합산하고, 상한 초과 시
    reserve 단계에서 차단해 무한 호출을 막는다. 기존 turn_budget 이 있으면
    공유한다 (llm_usage.turn_budget 의 재진입 규칙).
    """
    if inspect.isgeneratorfunction(func):
        @functools.wraps(func)
        def gen_wrapper(*args: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
            from core import llm_usage
            user = kwargs.get("user")
            if user is None and len(args) >= 2:
                user = args[1]
            with llm_usage.turn_budget(home_turn_limit(_user_role(user if isinstance(user, dict) else None))):
                yield from func(*args, **kwargs)
        return gen_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        from core import llm_usage
        user = kwargs.get("user")
        if user is None and len(args) >= 2:
            user = args[1]
        with llm_usage.turn_budget(home_turn_limit(_user_role(user if isinstance(user, dict) else None))):
            return func(*args, **kwargs)
    return sync_wrapper


def _react_deadline_seconds(role: str = "user") -> int:
    """End-to-end ReAct budget. 일반 유저는 75~120초 밴드(기본 90초)로 Home UI
    응답 목표를 지키고, admin 은 긴 분석 루프를 위해 최대 10분까지 허용한다
    (env `FLOW_LLM_REACT_ADMIN_DEADLINE_SECONDS`, 기본 300초)."""
    if role == "admin":
        raw = str(os.environ.get(_REACT_ADMIN_DEADLINE_ENV, "")).strip()
        try:
            value = int(raw) if raw else _REACT_ADMIN_DEFAULT_DEADLINE_S
        except (TypeError, ValueError):
            value = _REACT_ADMIN_DEFAULT_DEADLINE_S
        return max(15, min(600, value))
    raw = str(os.environ.get(_REACT_DEADLINE_ENV, "")).strip()
    try:
        value = int(raw) if raw else _REACT_DEFAULT_DEADLINE_S
    except (TypeError, ValueError):
        value = _REACT_DEFAULT_DEADLINE_S
    return max(15, min(120, value))


def _semantic_frame_for_prompt(prompt: str) -> dict[str, Any]:
    """공유 semantic resolver 를 호출해 frame 을 반환.

    resolve() 가 실패해도 오케스트레이터를 막지 않도록 예외는 삼키고 {} 를 반환한다.
    """
    try:
        from core import agent_semantic_service
        frame = agent_semantic_service.resolve(str(prompt or ""))
        return frame if isinstance(frame, dict) else {}
    except Exception:
        logger.info("semantic frame resolve failed", exc_info=True)
        return {}


def _semantic_frame_summary(frame: dict[str, Any]) -> dict[str, Any]:
    """decision prompt 와 snapshot 에 쓸 compact·공개 안전 projection.

    내부 추론 원문은 담지 않고, 해석된 용어/슬롯/미지어만 추린다.
    """
    if not isinstance(frame, dict):
        return {}
    canonical_hits: list[str] = []
    alias_hits = frame.get("alias_hits")
    if isinstance(alias_hits, list):
        for hit in alias_hits:
            if isinstance(hit, dict):
                canonical = _short_text(hit.get("canonical"), 80)
                if canonical and canonical not in canonical_hits:
                    canonical_hits.append(canonical)
            if len(canonical_hits) >= 20:
                break
    slot_hints: dict[str, Any] = {}
    slot_hints_raw = frame.get("slot_hints")
    if isinstance(slot_hints_raw, dict):
        for key, value in slot_hints_raw.items():
            slot_key = _short_text(key, 60)
            if not slot_key:
                continue
            if isinstance(value, list):
                slot_hints[slot_key] = _safe_string_list(value, 12)
            else:
                slot_hints[slot_key] = _safe_value(value, 160)
            if len(slot_hints) >= 20:
                break
    intent_matches_raw = frame.get("intent_matches")
    intent_matches = (
        _safe_string_list(list(intent_matches_raw.keys()), 12)
        if isinstance(intent_matches_raw, dict)
        else []
    )
    unknown_raw = frame.get("unknown_terms")
    if isinstance(unknown_raw, list) and unknown_raw and isinstance(unknown_raw[0], dict):
        unknown_terms = _safe_string_list([item.get("term") for item in unknown_raw if isinstance(item, dict)], 12)
    else:
        unknown_terms = _safe_string_list(unknown_raw, 12)
    return {
        "resolved_columns": _safe_string_list(frame.get("resolved_columns"), 20),
        "alias_hits": canonical_hits,
        "slot_hints": slot_hints,
        "unknown_terms": unknown_terms,
        "value_catalog_matches": [
            {
                "column": _short_text(item.get("column"), 80),
                "value": _short_text(item.get("value"), 120),
                "confidence": item.get("confidence"),
            }
            for item in (frame.get("value_catalog_matches") or [])[:12]
            if isinstance(item, dict)
        ],
        "value_terms": _safe_string_list(frame.get("value_terms"), 12),
        "intent_matches": intent_matches,
    }


def _format_tool_catalog(tools: list[dict[str, Any]]) -> str:
    """도구 카탈로그를 LLM 이 한 번에 보기 좋은 짧은 list 텍스트로.

    planner(`_plan_with_llm`)와 ReAct decision(`_decide_next_action`)이 같은
    카탈로그 포맷을 공유하도록 추출한 함수다.
    """
    tool_lines: list[str] = []
    ranked_tools = [
        (idx, t, agent_feedback_penalties.tool_score_adjustment(t))
        for idx, t in enumerate(tools)
    ]
    ranked_tools.sort(key=lambda item: (-(float(item[2].get("score_adjustment") or 0.0)), item[0]))
    for _idx, t, adjustment in ranked_tools[:40]:
        ex = ""
        if t.get("examples"):
            first = t["examples"][0]
            ex_prompt = first.get("prompt") if isinstance(first, dict) else None
            if ex_prompt:
                ex = f' | 예: "{str(ex_prompt)[:50]}"'
        feedback = ""
        penalty = float(adjustment.get("penalty") or 0.0)
        boost = float(adjustment.get("boost") or 0.0)
        if penalty >= 2.0:
            feedback = f" | feedback: avoid penalty={penalty:g}"
        elif penalty > 0.0:
            feedback = f" | feedback: low_priority penalty={penalty:g}"
        elif boost > 0.0:
            feedback = f" | feedback: preferred boost={boost:g}"
        schema = t.get("input_schema") if isinstance(t.get("input_schema"), dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        required_ordered = [str(key) for key in (schema.get("required") or t.get("required_args") or [])]
        required = set(required_ordered)
        field_bits: list[str] = []
        for key, spec in list(properties.items())[:12]:
            spec = spec if isinstance(spec, dict) else {}
            kind = spec.get("type") or "any"
            enum = spec.get("enum") if isinstance(spec.get("enum"), list) else []
            enum_text = f"={','.join(str(v) for v in enum[:6])}" if enum else ""
            field_bits.append(f"{key}:{kind}{enum_text}{'*' if key in required else ''}")
        contract = f" | input {{{', '.join(field_bits)}}}" if field_bits else ""
        if required_ordered and not properties:
            contract = f" | required [{', '.join(required_ordered[:12])}]"
        tool_lines.append(f"- {t['name']} ({t['kind']}): {t.get('description','')[:120]}{contract}{ex}{feedback}")
    return "\n".join(tool_lines) or "(empty)"


def _plan_with_llm(prompt: str, tools: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """LLM 에게 prompt + tool catalog 을 주고 step list(JSON) 를 받아 반환.

    실패하면 None — 호출자는 휴리스틱 fallback 으로 떨어진다.
    """
    try:
        from core import llm_adapter
    except Exception:
        return None
    enabled_tools = [t for t in tools if t.get("enabled")]
    tools_text = _format_tool_catalog(enabled_tools)
    # 단일 지식 레이어 — 도구 선택 자체를 지식 기반으로 (카드가 담당 유닛/근거 파일을 안다).
    try:
        from core import knowledge_cards as _knowledge_cards
        knowledge_text = _knowledge_cards.prompt_block(prompt)
    except Exception:
        knowledge_text = ""
    knowledge_section = f"# 도메인 지식 카드\n{knowledge_text}\n\n" if knowledge_text else ""
    try:
        from core import structure_model
        structure_context = structure_model.prompt_context("", prompt, max_chars=1500)
    except Exception:
        structure_context = {}
    structure_section = (f"# 공통 3D 구조 지식\n{json.dumps(structure_context, ensure_ascii=False)}\n\n"
                         if structure_context else "")

    system = (
        "You are Flow-i's home agent planner. Pick the minimum set of tools from "
        "the catalog to satisfy the user's request. Respond with strict JSON only. "
        "If a knowledge card names a responsible unit (담당 유닛), prefer that tool."
    )
    user_prompt = (
        f"# 사용자 요청\n{prompt}\n\n"
        f"{knowledge_section}"
        f"{structure_section}"
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
    out = llm_adapter.complete_json(
        user_prompt,
        system=system,
        schema={
            "keys": ["steps"],
            "required": ["steps"],
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "maxItems": _MAX_STEPS,
                    "items": {
                        "type": "object",
                        "required": ["tool", "input"],
                        "properties": {
                            "tool": {"type": "string"},
                            "kind": {"type": "string", "enum": ["unit_ai", "function"]},
                            "input": {"type": "object"},
                            "reason": {"type": "string"},
                        },
                    },
                },
            },
        },
        timeout=_REACT_DECISION_TIMEOUT_S,
    )
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


def _observation_summary(trace: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    """이전 step 결과를 decision LLM 에게 줄 compact observation list.

    trace row 에서 도구/상태/결과 요약/경고만 추리고 내부 추론 원문은 담지 않는다.
    """
    out: list[dict[str, Any]] = []
    if not isinstance(trace, list):
        return out
    for row in trace[-max(1, limit):]:
        if not isinstance(row, dict):
            continue
        status = row.get("status") or ("success" if row.get("ok") else "failed")
        observation = {
            "tool": _short_text(row.get("tool"), 80),
            "status": _short_text(status, 40),
            "result_preview": _short_text(row.get("result_preview"), 400),
            "warnings": _safe_string_list(row.get("warnings"), 3),
        }
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        if result:
            table = result.get("table") if isinstance(result.get("table"), dict) else {}
            evidence = {
                "intent": _short_text(result.get("intent"), 80),
                "action": _short_text(result.get("action"), 80),
                "source": _short_text(result.get("source") or result.get("source_type"), 80),
                "filters": deepcopy(result.get("filters") or {}),
                "row_count": table.get("total", result.get("row_count")),
                "columns": _safe_string_list(table.get("columns") or result.get("columns"), 12),
                "missing": _safe_string_list(result.get("missing"), 8),
                "semantic_learning": deepcopy(result.get("semantic_learning") or {}),
            }
            observation["evidence"] = {key: value for key, value in evidence.items() if value not in (None, "", [], {})}
        out.append(observation)
    return out


def _native_decision_tools(
    tools: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Build strict native-tool definitions with safe OpenAI function names."""
    try:
        from core import llm_adapter
    except Exception:
        return [], {}
    definitions: list[dict[str, Any]] = []
    by_native_name: dict[str, dict[str, Any]] = {}
    for index, tool in enumerate([item for item in tools if item.get("enabled")], start=1):
        original_name = str(tool.get("name") or "").strip()
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", original_name).strip("_")[:48] or f"tool_{index}"
        safe_name = f"flow_{index}_{safe_name}"[:64]
        raw_schema = tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {}
        if not isinstance(raw_schema.get("properties"), dict):
            raw_schema = {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "product": {"type": "string"},
                    "max_rows": {"type": "integer", "minimum": 1, "maximum": 120},
                },
                "required": ["prompt"],
            }
        strict_input = llm_adapter._strict_response_schema(raw_schema)
        parameters = {
            "type": "object",
            "properties": {
                "input": strict_input,
                "reason": {"type": "string"},
            },
            "required": ["input", "reason"],
            "additionalProperties": False,
        }
        definitions.append({
            "type": "function",
            "function": {
                "name": safe_name,
                "description": str(tool.get("description") or tool.get("title") or original_name)[:800],
                "strict": True,
                "parameters": parameters,
            },
        })
        by_native_name[safe_name] = tool
    definitions.extend([
        {
            "type": "function",
            "function": {
                "name": "flowi_ask_user",
                "description": "Ask the user only when a required value is missing or ambiguous and no read-only tool can resolve it.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string"},
                        "choices": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                        "reason": {"type": "string"},
                    },
                    "required": ["question", "choices", "reason"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "flowi_final",
                "description": "Finish when observations contain enough evidence to answer the user's goal.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {"answer": {"type": "string"}, "reason": {"type": "string"}},
                    "required": ["answer", "reason"],
                    "additionalProperties": False,
                },
            },
        },
    ])
    return definitions, by_native_name


def _decide_next_action(
    *,
    prompt: str,
    tools: list[dict[str, Any]],
    semantic_summary: dict[str, Any],
    observations: list[dict[str, Any]],
    step_index: int,
    max_steps: int,
    timeout_s: int = _REACT_DECISION_TIMEOUT_S,
) -> dict[str, Any] | None:
    """ReAct 한 턴 결정: 도구 1개를 호출하거나 finalize.

    반환:
      - `{"action": "call_tool", "tool": <tool dict>, "tool_name": str, "input": dict, "reason": str}`
      - `{"action": "final", "answer": str, "reason": str}`
      - LLM 실패 시 `None` (호출자는 fallback 으로 degrade).

    내부 `thought` 는 프롬프트 schema 에만 두고 반환/공개에는 싣지 않는다.
    """
    try:
        from core import llm_adapter
    except Exception:
        return None
    enabled_tools = [t for t in tools if t.get("enabled")]
    tool_by_name = {t["name"]: t for t in enabled_tools}
    tools_text = _format_tool_catalog(enabled_tools)
    remaining = max(0, int(max_steps) - int(step_index))
    obs_text = json.dumps(observations, ensure_ascii=False)[:6000] if observations else "(없음)"
    try:
        from core import structure_model
        structure_context = structure_model.prompt_context("", prompt, max_chars=1500)
    except Exception:
        structure_context = {}
    structure_text = json.dumps(structure_context, ensure_ascii=False) if structure_context else "(관련 없음)"
    system = (
        "You are Flow-i's home agent controller running a ReAct loop. "
        "Each turn you either call exactly ONE tool from the catalog, ask the USER "
        "one clarifying question (action=ask_user), or finalize with an answer. "
        "Use only tools that appear in the catalog. Base decisions on the user goal, "
        "the resolved semantic terms, and prior observations. Use ask_user ONLY when "
        "a required input is missing or ambiguous and no tool can resolve it — "
        "human-in-the-loop is for decisions only the user can make. "
        "Prefer to finalize as soon as the observations answer the goal. "
        "Never invent data that is not present in observations. "
        "Respond with strict JSON only."
    )
    user_prompt = (
        f"# 사용자 목표\n{prompt}\n\n"
        f"# 의미 분석(semantic frame)\n{json.dumps(semantic_summary, ensure_ascii=False)[:1500]}\n\n"
        f"# 공통 3D 구조 지식\n{structure_text}\n\n"
        f"# 사용 가능한 도구 카탈로그\n{tools_text}\n\n"
        f"# 지금까지의 관찰(observations)\n{obs_text}\n\n"
        f"# 남은 단계: {remaining}\n\n"
        "# 출력 형식 (JSON, 다른 텍스트 금지)\n"
        "{\n"
        '  "thought": "<한 줄 추론>",\n'
        '  "action": "call_tool | ask_user | final",\n'
        '  "tool": "<카탈로그 name, action=call_tool 일 때만>",\n'
        '  "input": {"prompt": "<단일 도구용 자연어>", "product": "<선택>", "max_rows": 12},\n'
        '  "question": "<action=ask_user 일 때 사용자에게 물을 한 문장>",\n'
        '  "choices": ["<선택지1>", "<선택지2>"],\n'
        '  "reason": "<왜 이 선택인지 한 줄>",\n'
        '  "answer": "<action=final 일 때 최종 답변>"\n'
        "}"
    )
    native_tools, native_name_map = _native_decision_tools(enabled_tools)
    if native_tools:
        native_system = (
            "You are Flow-i's home agent controller. Select exactly one native function. "
            "Use flowi_ask_user only when a required value is missing and no read-only tool can resolve it. "
            "Use flowi_final only when prior observations support the answer. Never invent evidence."
        )
        native_prompt = (
            f"# User goal\n{prompt}\n\n"
            f"# Resolved semantics\n{json.dumps(semantic_summary, ensure_ascii=False)[:2000]}\n\n"
            f"# Common 3D structure knowledge\n{structure_text}\n\n"
            f"# Prior observations\n{obs_text}\n\n"
            f"# Remaining steps\n{remaining}"
        )
        native = llm_adapter.complete_tool_call(
            native_prompt,
            tools=native_tools,
            system=native_system,
            timeout=max(1, min(int(timeout_s or _REACT_DECISION_TIMEOUT_S), _REACT_DECISION_TIMEOUT_S)),
        )
        if native.get("ok"):
            call = native.get("call") if isinstance(native.get("call"), dict) else {}
            call_name = str(call.get("name") or "")
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            reason = _short_text(arguments.get("reason"), 200)
            if call_name == "flowi_ask_user":
                question = _short_text(arguments.get("question"), 300)
                choices = _safe_string_list(arguments.get("choices"), 3)
                if question:
                    return {"action": "ask_user", "question": question, "choices": choices,
                            "reason": reason, "native_mode": "tools"}
            elif call_name == "flowi_final":
                return {"action": "final", "answer": _short_text(arguments.get("answer"), 4000),
                        "reason": reason, "native_mode": "tools"}
            elif call_name in native_name_map:
                tool = native_name_map[call_name]
                step_input = arguments.get("input") if isinstance(arguments.get("input"), dict) else {}
                step_input = llm_adapter._strip_optional_nulls(
                    step_input,
                    tool.get("input_schema") if isinstance(tool.get("input_schema"), dict) else {},
                )
                return {
                    "action": "call_tool",
                    "tool": tool,
                    "tool_name": _normalize_unit_name(str(tool.get("name") or ""), tool) or str(tool.get("name") or ""),
                    "input": step_input,
                    "reason": reason,
                    "native_mode": "tools",
                }
    out = llm_adapter.complete_json(
        user_prompt,
        system=system,
        schema={
            "keys": ["thought", "action", "tool", "input", "question", "choices", "reason", "answer"],
            "required": ["action"],
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "action": {"type": "string", "enum": ["call_tool", "ask_user", "final"]},
                "tool": {"type": "string"},
                "input": {"type": "object"},
                "question": {"type": "string"},
                "choices": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                "reason": {"type": "string"},
                "answer": {"type": "string"},
            },
        },
        timeout=max(1, min(int(timeout_s or _REACT_DECISION_TIMEOUT_S), _REACT_DECISION_TIMEOUT_S)),
    )
    if not out.get("ok"):
        logger.info("react decision failed: %s", out.get("error"))
        return None
    obj = out.get("obj") or {}
    action = str(obj.get("action") or "").strip().lower()
    reason = _short_text(obj.get("reason"), 200)
    if action == "ask_user":
        # Human-in-the-loop: 필요한 입력이 없거나 모호할 때 사용자에게 한 번 질문.
        question = _short_text(obj.get("question"), 300)
        if not question:
            return {"action": "final", "answer": _short_text(obj.get("answer"), 4000),
                    "reason": reason or "empty ask_user question"}
        choices = [
            _short_text(c, 120) for c in (obj.get("choices") or [])
            if isinstance(c, (str, int, float)) and _short_text(c, 120)
        ][:3]
        return {"action": "ask_user", "question": question, "choices": choices, "reason": reason}
    if action == "call_tool":
        name = str(obj.get("tool") or "").strip()
        tool = tool_by_name.get(name)
        if not tool:
            # 도구가 없거나 카탈로그 밖이면 finalize 로 강등.
            return {
                "action": "final",
                "answer": _short_text(obj.get("answer"), 4000),
                "reason": reason or "no usable tool in catalog",
            }
        step_input = obj.get("input")
        if not isinstance(step_input, dict):
            step_input = {"prompt": prompt}
        return {
            "action": "call_tool",
            "tool": tool,
            "tool_name": _normalize_unit_name(name, tool) or name,
            "input": step_input,
            "reason": reason,
        }
    # action 미지정/그 외는 final 로 처리.
    return {
        "action": "final",
        "answer": _short_text(obj.get("answer"), 4000),
        "reason": reason,
    }


def _has_dashboard_rows_or_columns(payload: dict[str, Any]) -> bool:
    return bool(
        isinstance(payload.get("columns"), list) and payload.get("columns")
        or isinstance(payload.get("sample_rows"), list) and payload.get("sample_rows")
    )


def dashboard_agent_should_use_source_runtime(
    payload: dict[str, Any],
    *,
    home_context: bool = False,
) -> bool:
    if not isinstance(payload, dict) or _has_dashboard_rows_or_columns(payload):
        return False
    if any(_short_text(payload.get(key), 240) for key in ("root", "product", "file", "scope")):
        return True
    if isinstance(payload.get("preferred_selected_columns"), list) and payload.get("preferred_selected_columns"):
        return True
    prompt = _short_text(payload.get("natural_language") or payload.get("prompt"), 2000)
    if not prompt:
        return False
    signals, _matched = _keyword_signals(prompt)
    has_chart = bool(signals.get("chart") or signals.get("dashboard"))
    has_source = bool(any(signals.get(tag) for tag in ("sql_workspace", "filebrowser", "tablemap", "fab", "lot")))
    if has_chart and has_source:
        return True
    return bool(home_context and has_chart)


def dashboard_agent_source_payload(step_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "natural_language": str(step_input.get("natural_language") or step_input.get("prompt") or ""),
        "root": str(step_input.get("root") or ""),
        "product": str(step_input.get("product") or ""),
        "file": str(step_input.get("file") or ""),
        "scope": str(step_input.get("scope") or ""),
        "max_rows": step_input.get("max_rows") or 12,
        "preferred_selected_columns": step_input.get("preferred_selected_columns") if isinstance(step_input.get("preferred_selected_columns"), list) else [],
    }


def dashboard_agent_result_from_source_runtime_result(result: dict[str, Any]) -> dict[str, Any]:
    trimmed = _trim_home_sql_join_dashboard_runtime_result(result)
    dashboard = trimmed.get("dashboard") if isinstance(trimmed.get("dashboard"), dict) else {}
    chart_result = dashboard.get("chart_result") if isinstance(dashboard.get("chart_result"), dict) else {}
    source_resolution = trimmed.get("source_resolution") if isinstance(trimmed.get("source_resolution"), dict) else {}
    question = (
        _short_text(source_resolution.get("question"), 240)
        or _short_text(dashboard.get("question"), 240)
        or _short_text(trimmed.get("question"), 240)
    )
    needs_input = bool(trimmed.get("blocked") or source_resolution.get("needs_input") or dashboard.get("needs_input"))
    out = {
        **trimmed,
        "unit_ai": "dashboard_agent",
        "source_orchestration": True,
        "needs_input": needs_input,
        "question": question,
        "chart_type": dashboard.get("chart_type") or chart_result.get("chart_type") or "",
        "config": deepcopy(dashboard.get("config") or chart_result.get("config") or {}),
        "chart_result": deepcopy(chart_result),
    }
    if needs_input and question:
        out["question"] = question
    return out


def _dashboard_tool_from_result_output(output: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    if not isinstance(output, dict):
        return {}
    intent = str(output.get("intent") or "")
    action = str(output.get("action") or "")
    chart_result = output.get("chart_result") if isinstance(output.get("chart_result"), dict) else {}
    is_dashboard_chart = str(chart_result.get("kind") or "").startswith("dashboard_")
    if "dashboard" not in intent and "dashboard" not in action and not is_dashboard_chart:
        return {}
    dashboard = output.get("dashboard") if isinstance(output.get("dashboard"), dict) else {}
    chart_result = output.get("chart_result") if isinstance(output.get("chart_result"), dict) else {}
    if not chart_result and isinstance(dashboard.get("chart_result"), dict):
        chart_result = dashboard.get("chart_result") or {}
    if not chart_result and not output.get("blocked") and not output.get("needs_input"):
        return {}
    title = (
        _short_text(chart_result.get("title"), 120)
        or _short_text(dashboard.get("title"), 120)
        or "Dashboard Agent"
    )
    tool: dict[str, Any] = {
        "type": "chart" if chart_result else "message",
        "feature": "dashboard",
        "intent": "dashboard_agent",
        "inline_summary": title,
        "warnings": _safe_string_list(warnings or output.get("warnings"), 12),
    }
    if chart_result:
        tool["chart_result"] = deepcopy(chart_result)
        tool["chart_type"] = chart_result.get("chart_type") or output.get("chart_type") or ""
        tool["config"] = deepcopy(output.get("config") or chart_result.get("config") or {})
    table = output.get("table") if isinstance(output.get("table"), dict) else {}
    if isinstance(table.get("rows"), list):
        tool["table"] = deepcopy(table)
        context = output.get("context")
        tool["context"] = deepcopy(context) if isinstance(context, dict) else {}
    for extra in ("chart_panels", "download_job", "slots", "filters", "sources"):
        if output.get(extra) is not None:
            tool[extra] = deepcopy(output.get(extra))
    if output.get("blocked") or output.get("needs_input"):
        tool["blocked"] = True
        question = _short_text(output.get("question"), 240)
        if question:
            tool["missing_freetext"] = [{"key": "dashboard_agent_input", "label": question}]
    return tool


def _tool_from_tool_calls(tool_calls: Any) -> dict[str, Any]:
    if not isinstance(tool_calls, list):
        return {}
    for call in reversed(tool_calls):
        if not isinstance(call, dict):
            continue
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        tool = _dashboard_tool_from_result_output(output, call.get("warnings") if isinstance(call.get("warnings"), list) else [])
        if tool:
            return tool
        if isinstance(output, dict):
            table = output.get("table") if isinstance(output.get("table"), dict) else {}
            has_table = isinstance(table.get("rows"), list)
            clarification = output.get("clarification") if isinstance(output.get("clarification"), dict) else {}
            missing = output.get("missing") if isinstance(output.get("missing"), list) else []
            # 표·선택지·추가입력 carrying unit 결과도 오른쪽·왼쪽 패널에 그대로 노출.
            if has_table or clarification or missing or output.get("needs_input"):
                passthrough: dict[str, Any] = {
                    "type": "table" if has_table else "message",
                    "feature": _short_text(output.get("feature") or call.get("tool") or "", 80),
                    "action": _short_text(output.get("action") or "", 80),
                    "intent": _short_text(output.get("intent") or "", 80),
                    "answer": _short_text(output.get("answer"), 2000),
                    "warnings": _safe_string_list(call.get("warnings") if isinstance(call.get("warnings"), list) else output.get("warnings"), 12),
                }
                if has_table:
                    passthrough["table"] = deepcopy(table)
                    context = output.get("context")
                    passthrough["context"] = deepcopy(context) if isinstance(context, dict) else {}
                if clarification:
                    passthrough["clarification"] = deepcopy(clarification)
                if missing:
                    passthrough["missing"] = _safe_string_list(missing, 20)
                if output.get("needs_input"):
                    passthrough["needs_input"] = True
                for extra in ("teg_candidates", "split_candidates", "approval", "sources",
                              "chart_result", "chart_panels", "download_job", "slots",
                              "filters", "context"):
                    if output.get(extra) is not None:
                        passthrough[extra] = deepcopy(output.get(extra))
                return passthrough
    for call in reversed(tool_calls):
        if not isinstance(call, dict):
            continue
        output = call.get("output") if isinstance(call.get("output"), dict) else {}
        # 표 없는 위치 답변(low_confidence 안내 등)도 Location 뷰 매핑 유지.
        if isinstance(output, dict) and output.get("feature") == "location":
            return {
                "type": "message",
                "feature": "location",
                "action": _short_text(output.get("action") or "", 80),
                "intent": _short_text(output.get("intent") or "", 80),
                "answer": _short_text(output.get("answer"), 2000),
                "warnings": _safe_string_list(output.get("warnings"), 12),
            }
    return {}


# ── Flow-i 기능 권한 (유저 tabs → feature key) ───────────────────────────────
# unit_ai 실행에 필요한 feature key (any-of). llm.py 단일 패스의 unit_only
# 게이트와 같은 기준 — 여기 없는 unit 은 게이트하지 않는다.
_UNIT_FEATURE_KEYS: dict[str, tuple[str, ...]] = {
    "filebrowser_ai_sql": ("filebrowser",),
    "inform_registration": ("inform",),
    "change_management": ("calendar", "meeting"),
    "dashboard_agent": ("dashboard",),
    "home_sql_join_dashboard": ("dashboard",),
    "split_nav": ("splittable",),
    "step_lookup": ("filebrowser", "splittable", "dashboard"),
    "ppid_knob": ("filebrowser", "splittable"),
    "dashboard_wip": ("dashboard",),
    "lotmanage_table": ("lotmanage", "lot_management"),
    "knob_lead_lots": ("filebrowser",),
    "need_product": (),
    "inline_values": ("filebrowser",),
    "inline_radius_plot": ("filebrowser",),
    "eta_forecast": ("lottracker", "tracker"),
    "et_download": ("reformatize", "filebrowser"),
    "et_time": ("ettime", "filebrowser"),
}


def _allowed_feature_keys_for_user(user: dict[str, Any] | None) -> set[str] | None:
    """유저의 flow-i feature 허용 키 집합. None = 필터 생략.

    admin 은 전체 허용(None). username 이 없는 내부 호출/hermetic 테스트도
    None — 권한 판단이 불가능한 곳에서 기능을 오차단하지 않는다."""
    if not user or not str(user.get("username") or "").strip():
        return None
    if str(user.get("role") or "") == "admin":
        return None
    try:
        from routers.llm import _allowed_flowi_feature_keys
        keys = _allowed_flowi_feature_keys(user)
    except Exception:
        return None
    return set(keys) if isinstance(keys, (set, list, tuple)) else None


def _unit_allowed(name: str, allowed: set[str] | None) -> bool:
    if allowed is None:
        return True
    required = _UNIT_FEATURE_KEYS.get(str(name or ""))
    if not required:
        return True
    return bool(set(required) & allowed)


def _filter_tools_for_user(tools: list[dict[str, Any]], user: dict[str, Any] | None) -> list[dict[str, Any]]:
    """tabs 권한이 없는 기능의 도구를 카탈로그에서 제외 — planner 가 보지 못하게.

    실행 시점 가드(_execute_step)와 이중 방어. function 도구는 feature 매핑이
    있는 것만 게이트하고, 범용(라우터/안내) 도구는 남긴다."""
    allowed = _allowed_feature_keys_for_user(user)
    if allowed is None:
        return tools
    feature_for_function = None
    try:
        from routers.llm import _flowi_feature_for_function as feature_for_function
    except Exception:
        pass
    out: list[dict[str, Any]] = []
    for tool in tools:
        kind = str(tool.get("kind") or "")
        name = str(tool.get("name") or "")
        if kind == "unit_ai":
            if _unit_allowed(name, allowed):
                out.append(tool)
            continue
        if kind == "function" and feature_for_function is not None:
            try:
                feature = str(feature_for_function(name) or "")
            except Exception:
                feature = ""
            if feature and feature not in allowed:
                continue
        out.append(tool)
    return out


# 단일 패스 엔진(_handle_flowi_query)이 의도 재라우팅에 실패했을 때 떨어지는
# 범용 안내(route/open) 액션들 — 실데이터가 아니므로 ReAct 진전으로 치지 않는다.
_GENERIC_GUIDANCE_ACTIONS = {
    "route_flowi_feature",
    "open_dashboard",
    "open_filebrowser",
    "open_splittable",
    "open_inform",
    "open_meeting",
}


def _function_result_is_guidance(res: dict[str, Any], requested_name: str = "") -> bool:
    """function 도구 결과가 실데이터가 아닌 기능 안내(폴백)인지 판정.

    ReAct 가 라우터 자체(route_flowi_feature 등)를 고른 경우에는 안내가 곧
    정답이므로 폴백으로 치지 않는다."""
    if str(requested_name or "") in _GENERIC_GUIDANCE_ACTIONS:
        return False
    action = str(res.get("action") or "")
    intent = str(res.get("intent") or "")
    return action in _GENERIC_GUIDANCE_ACTIONS or intent.endswith("_guidance")


# ── 홈 fast-path: 결정적 단축 경로 (LLM 없이) ─────────────────────────────
# "대시보드 보여줘 / 선행랏 / 어디에 있어 / 랏관리" 류는 휴리스틱 점수나
# planner 없이도 의도가 명확하므로 전용 러너로 바로 실행한다.
_LOT_TOKEN_RE = re.compile(
    r"\b(?:[A-Z][A-Z0-9]*\d[A-Z0-9]*(?:\.[A-Z0-9]+)?|[A-Z]{3,}\.\d+)\b"
)
_LOCATION_TERMS = ("어디", "어딨", "어딨어", "위치", "현위치", "현재위치", "where", "location")
_MAPPING_QUESTION_RE = re.compile(
    r"(step[_\s-]?id|function[_\s-]?step|step[_\s-]?desc)\s*(가|이|는|은|을|를)?\s*(뭐|무엇|알려|찾)",
    re.IGNORECASE,
)
_DASHBOARD_SHOW_TERMS = ("보여", "띄워", "열어", "확인", "차트", "show", "display", "view", "open")
_DASHBOARD_EXCLUDE_TERMS = ("scatter", "boxplot", "wafer map", "correlation", "heatmap", "trend")
_LOTMANAGE_TERMS = ("랏관리", "lot관리", "lot manage", "lotmanage", "랏 관리")
_LEAD_LOT_TERMS = ("선행랏", "선행", "가장 앞", "제일 앞", "맨 앞", "earliest", "first lot")
_INLINE_LOT_RE = re.compile(r"(?<![\w.])([A-Za-z][A-Za-z0-9]{3,}\.[A-Za-z0-9]+)(?![\w.])")
_INLINE_WAFER_RE = re.compile(r"(?:#|웨이퍼\s*|wafer\s*|wf\s*|슬롯\s*)0*(\d{1,2})(?![0-9])", re.I)
_RADIUS_TERMS = ("radius", "반경")
_PLOT_TERMS = ("plot", "그려", "차트", "scatter", "산점도")
_ETA_STRONG_TERMS = ("도착", "예정", "예측", "eta", "완료시각", "완료 시각", "언제쯤")
_ETA_TERMS = _ETA_STRONG_TERMS + ("언제",)
_ETA_AFFIRM_RE = re.compile(r"^(맞아|맞어|맞습니다|맞음|응|어|예|네|그래|그렇지|오케이|ok|yes|정답|좋아)\b[.!?\s]*$", re.I)
_ETDL_VERBS = ("뽑아", "뽑고", "추출", "다운로드", "download", "csv", "받아", "받어")
_ETTIME_TERMS = ("측정시간", "측정 시간", "et time")
_ET_DAYS_RE = re.compile(r"(\d{1,4})\s*일(?:치|간)?")
_ET_SUFFIX_RE = re.compile(r"__ET_(PRODUCT|DAYS|ITEM)=([^\s]+(?: [^\s]+)*?)(?=\s+__ET_|\s*$)")
_ET_STOPWORDS = {"ET", "DATA", "ETDATA", "FLOW", "FLOWI", "CSV", "INDEX", "PRODUCT", "DOWNLOAD"}


def _et_prompt_parts(prompt: str) -> tuple[str, dict[str, str]]:
    """__ET_PRODUCT/__ET_DAYS/__ET_ITEM suffix 분리 (값 공백 허용)."""
    found: dict[str, str] = {}
    kept: list[str] = []
    current: str | None = None
    for token in str(prompt or "").split():
        if token.startswith("__ET_PRODUCT="):
            current, found["product"] = "product", token[len("__ET_PRODUCT="):]
        elif token.startswith("__ET_DAYS="):
            current, found["days"] = "days", token[len("__ET_DAYS="):]
        elif token.startswith("__ET_ITEM="):
            current, found["item"] = "item", token[len("__ET_ITEM="):]
        elif token.startswith("__") and "=" in token:
            current = None
            kept.append(token)
        elif current in found:
            found[current] += " " + token
        else:
            kept.append(token)
    found = {key: value.strip() for key, value in found.items()}
    return " ".join(kept).strip(), found


def _et_days(prompt: str) -> int:
    match = _ET_DAYS_RE.search(str(prompt or ""))
    return max(0, min(int(match.group(1)), 3660)) if match else 0


def _et_bare_lot(prompt: str, product: str = "") -> str:
    """Dotted lot 우선, 없으면 제품·키워드가 아닌 단일 영문 토큰."""
    dotted = _LOT_TOKEN_RE.search(str(prompt or ""))
    if dotted:
        return str(dotted.group(0) or "").upper()
    try:
        from core import semantic_hitl
        norm = semantic_hitl.normalize_term
    except Exception:
        norm = lambda value: re.sub(r"[\s_-]+", "", str(value or "")).upper()  # noqa: E731
    stop = {norm(w) for w in _ET_STOPWORDS} | {norm(product)}
    cands = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]{3,}", str(prompt or "")):
        if norm(token) and norm(token) not in stop and token not in cands:
            cands.append(token)
    return cands[0].upper() if len(cands) == 1 else ""

_FASTPATH_TITLES = {
    "dashboard_wip": "WIP 대시보드",
    "lotmanage_table": "랏관리 표",
    "knob_lead_lots": "Knob 선행랏",
    "need_product": "제품 선택",
}


def _fastpath_product(prompt: str) -> tuple[str, list[str]]:
    """prompt 속 제품 후보. 정확히 1개면 (제품, 전체), 아니면 ("", 전체)."""
    from core import data_chat
    try:
        products = data_chat.available_product_names()
    except Exception:
        return "", []
    try:
        matches = data_chat.product_candidates(prompt, products)
    except Exception:
        matches = []
    if len(matches) == 1:
        return str(matches[0]), products
    return "", products


def _fastpath_tool(name: str) -> dict[str, Any]:
    try:
        tool = tool_registry.get_tool(name)
    except Exception:
        tool = None
    if isinstance(tool, dict) and tool.get("enabled"):
        return tool
    return {"name": name, "kind": "unit_ai", "title": _FASTPATH_TITLES.get(name, name),
            "description": "홈 fast-path 전용 결정적 실행"}


def _need_product_step(prompt: str, products: list[str]) -> dict[str, Any]:
    options = [{"label": name, "value": name} for name in (products or [])[:20]]
    return {
        "tool": _fastpath_tool("need_product"),
        "input": {"prompt": prompt, "need_products": options},
        "reason": "제품 미확정 — 선택지 제시",
        "source": "fastpath",
    }


def _plan_from_fastpath(
    prompt: str,
    user: dict[str, Any] | None = None,
    request: Any | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """fast-path plan 또는 None. (plan, meta) — meta.planner 는 fastpath:<kind>."""
    text = str(prompt or "")
    low = text.lower()

    # Q1: WIP 대시보드 ("{제품} 대시보드 보여줘" — 바차트 + split 기준열 변경).
    if ("대시보드" in text or "dashboard" in low):
        show = any(term in low or term in text for term in _DASHBOARD_SHOW_TERMS)
        bare = text.strip() in ("대시보드", "dashboard", "wip dashboard")
        excluded = any(term in low for term in _DASHBOARD_EXCLUDE_TERMS)
        if (show or bare) and not excluded:
            product, products = _fastpath_product(text)
            if not product:
                if products:
                    return [_need_product_step(text, products)], {"planner": "fastpath:need_product", "step_count": 1}
                return None, None
            return [{
                "tool": _fastpath_tool("dashboard_wip"),
                "input": {"prompt": text, "product": product, "max_rows": 12},
                "reason": f"WIP 대시보드 fast-path (product={product})",
                "source": "fastpath",
            }], {"planner": "fastpath:dashboard_wip", "step_count": 1}

    # Q2: Knob 선행랏 ("{제품} 23.0 RELIABILITY PPID23_2 … 선행랏").
    if any(term in text for term in _LEAD_LOT_TERMS):
        product, products = _fastpath_product(text)
        if not product:
            if products:
                return [_need_product_step(text, products)], {"planner": "fastpath:need_product", "step_count": 1}
            return None, None
        return [{
            "tool": _fastpath_tool("knob_lead_lots"),
            "input": {"prompt": text, "product": product, "max_rows": 12},
            "reason": f"Knob 선행랏 fast-path (product={product})",
            "source": "fastpath",
        }], {"planner": "fastpath:knob_lead_lots", "step_count": 1}

    # ETA 도착예정 ("proda AZVBBA.1 BV200000에 언제도착해?" — Lot tracker + 그래프).
    eta_plan, eta_meta = _plan_eta_fastpath(text, user=user, request=request, context=context)
    if eta_plan:
        return eta_plan, eta_meta

    # ET DATA 추출 ("ET DATA 제품명 ~~~ 뽑아줘" — days/lot/alias HITL + 다운로드).
    et_plan, et_meta = _plan_et_download_fastpath(text, user=user, context=context)
    if et_plan:
        return et_plan, et_meta

    # ET 측정시간 ("제품명 AZBBV.1 ET측정시간 보여줘").
    ett_plan, ett_meta = _plan_et_time_fastpath(text, user=user)
    if ett_plan:
        return ett_plan, ett_meta

    # Inline 값/Radius ("{제품} {lot} PC BCD ..." — 별칭 해석 + HITL).
    inline_plan, inline_meta = _plan_inline_fastpath(text, user)
    if inline_plan:
        return inline_plan, inline_meta

    # Q3: 현재위치 ("AZAAA.1 어디에 있어" — step_desc 포함).
    if any(term in low for term in _LOCATION_TERMS):
        if _LOT_TOKEN_RE.search(text) and not _MAPPING_QUESTION_RE.search(text):
            try:
                from core import lot_wip
                wip_question = lot_wip.is_wip_prompt(text)
            except Exception:
                wip_question = True
            if wip_question:
                product, _ = _fastpath_product(text)
                return [{
                    "tool": _fastpath_tool("lot_wip"),
                    "input": {"prompt": text, "product": product, "max_rows": 30},
                    "reason": "현재위치 fast-path (lot_wip)",
                    "source": "fastpath",
                }], {"planner": "fastpath:lot_wip", "step_count": 1}

    # Q4: 랏관리 표 ("{제품} 랏관리 테이블").
    if any(term in low or term in text for term in _LOTMANAGE_TERMS):
        product, products = _fastpath_product(text)
        if not product:
            if products:
                return [_need_product_step(text, products)], {"planner": "fastpath:need_product", "step_count": 1}
            return None, None
        return [{
            "tool": _fastpath_tool("lotmanage_table"),
            "input": {"prompt": text, "product": product, "max_rows": 50},
            "reason": f"랏관리 fast-path (product={product})",
            "source": "fastpath",
        }], {"planner": "fastpath:lotmanage_table", "step_count": 1}

    return None, None


def _synthetic_request(user: dict[str, Any] | None) -> Any:
    """라우터 가드(current_user)가 읽을 수 있는 최소 Request. request=None 대체용."""
    from starlette.requests import Request
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/", "raw_path": b"/", "query_string": b"", "headers": [],
        "client": ("home_fastpath", 0), "server": ("home_fastpath", 80),
    }
    request = Request(scope)
    request.state.user = dict(user or {})
    return request


def _run_dashboard_wip_view(
    prompt: str,
    product: str,
    request: Any | None,
    user: dict[str, Any] | None,
) -> dict[str, Any]:
    """대시보드 앱과 같은 WIP × Split 바차트(chart_result kind=dashboard_wip_split).

    split 기준열 변경은 오른쪽 WorkspaceDashboard 의 changeSplit 이 같은
    /api/dashboard/wip-split 으로 갱신한다.
    """
    from routers import dashboard as dashboard_router
    prompt_lower = str(prompt or "").lower()
    axis = "step_id" if "step_id" in prompt_lower else "step_desc"
    split_match = re.search(r"\b((?:KNOB|MASK|FAB)_[A-Za-z0-9_.-]+)\b", str(prompt or ""), flags=re.I)
    split_col = split_match.group(1) if split_match else ""
    req = request if request is not None else _synthetic_request(user)
    payload = dashboard_router.wip_split_summary(
        request=req,
        product=product,
        bin_size=30000,
        split_col=split_col,
        axis=axis,
        exclude_root_prefix="Z",
        lot_type="",
    )
    bins = payload.get("bins") if isinstance(payload.get("bins"), list) else []
    split_values = payload.get("split_values") if isinstance(payload.get("split_values"), list) else []
    selected = str(payload.get("product") or product or "ALL")
    total = int(payload.get("total_wafers") or 0)
    chart_result = {
        "ok": True,
        "kind": "dashboard_wip_split",
        "chart_type": "wip_stacked",
        "title": f"{selected} WIP × Split Dashboard",
        "product": selected,
        "bins": bins,
        "split_values": split_values,
        "unassigned_label": payload.get("unassigned_label") or "(unassigned)",
        "total_wafers": total,
        "matched_wafers": int(payload.get("matched_wafers") or 0),
        "axis": payload.get("axis") or axis,
        "bin_size": int(payload.get("bin_size") or 30000),
        "split_col": payload.get("split_col") or "",
        "split_cols": payload.get("split_cols") if isinstance(payload.get("split_cols"), list) else [],
        "split_options": payload.get("split_options") if isinstance(payload.get("split_options"), list) else [],
        "generated_at": payload.get("generated_at") or "",
    }
    return {
        "handled": True,
        "intent": "dashboard_wip_view",
        "action": "dashboard.wip_split.read",
        "feature": "dashboard",
        "answer": f"{selected} WIP 대시보드를 바로 표시합니다. 총 {total:,} wafer입니다.",
        "chart_result": chart_result,
        "slots": {"product": selected, "axis": chart_result["axis"],
                  "bin_size": chart_result["bin_size"], "split_col": chart_result["split_col"]},
        "source_ids": ["/api/dashboard/wip-split"],
        "sources": ["wip-split latest cache", "ML_TABLE split"],
    }


def _step_order_key(step_id: Any) -> tuple:
    """WIP 선행(가장 앞) 판정용 정렬 키 — 영문 prefix + 숫자 청크 순."""
    parts = re.findall(r"[A-Za-z]+|\d+", str(step_id or "").upper())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def _run_knob_lead_lots(
    prompt: str,
    product: str,
    max_rows: int = 12,
) -> dict[str, Any]:
    """ML_TABLE_{product} 에서 prompt 의 knob 값을 가진 wafer 중 WIP step 이
    가장 앞선 demolish 순으로 반환한다. step_desc 는 Vehicle_matching 에서."""
    import polars as pl
    from core import lot_progress_cache, lot_wip

    clean = re.sub(r"(?i)^ML_TABLE_", "", str(product or "").strip())
    ml_path = PATHS.db_root / f"ML_TABLE_{clean}.parquet"
    if ml_path is None or not ml_path.is_file():
        for candidate in sorted(PATHS.db_root.glob("ML_TABLE_*.parquet")):
            if candidate.stem[len("ML_TABLE_"):].upper() == clean.upper():
                ml_path = candidate
                break
    if ml_path is None or not ml_path.is_file():
        return {"handled": False, "error": f"ML_TABLE_{clean} 없음"}
    norm = lambda value: re.sub(r"[\s_]+", "", str(value or "")).upper()
    flat = lambda value: re.sub(r"_", "", norm(value))
    prompt_norm, prompt_flat = norm(prompt), flat(prompt)
    schema = pl.scan_parquet(ml_path).collect_schema()
    knob_cols = [name for name in schema.names() if name.upper().startswith("KNOB")]
    matched_col = ""
    for name in knob_cols:
        suffix = re.sub(r"(?i)^KNOB_", "", name)
        if norm(suffix) and norm(suffix) in prompt_norm:
            matched_col = name
            break
    if not matched_col:
        return {"handled": False, "error": "knob 컬럼 특정 실패"}
    values = (
        pl.scan_parquet(ml_path)
        .select(matched_col)
        .collect()[matched_col]
        .drop_nulls().unique().to_list()
    )
    matched_value = ""
    for value in values:
        text = str(value or "")
        if not text:
            continue
        if norm(text) in prompt_norm or flat(text) in prompt_flat or prompt_flat in flat(text):
            matched_value = text
            break
    if not matched_value:
        return {"handled": False, "error": f"{matched_col} 값 특정 실패"}
    frame = (
        pl.scan_parquet(ml_path)
        .filter(pl.col(matched_col).cast(pl.String, strict=False) == matched_value)
        .select(["ROOT_LOT_ID", "LOT_ID", "WAFER_ID"])
        .collect()
    )
    if frame.is_empty():
        return {"handled": True, "intent": "knob_lead_lots", "action": "knob_lead_lots.read",
                "feature": "filebrowser",
                "answer": f"{clean} ML_TABLE 에서 {matched_col}={matched_value} 인 wafer 가 없습니다.",
                "table": {"kind": "knob_lead_lots", "title": "Knob 선행랏", "columns": [], "rows": [], "total": 0},
                "context": {"product": clean}}
    state = lot_progress_cache.read_lot_progress_cache(allow_stale=True)
    wip_index: dict[tuple[str, str], dict] = {}
    for row in state.get("items") or []:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("root_lot_id") or "").upper(), str(row.get("wafer_id") or "").upper())
        if key[0] and key not in wip_index:
            wip_index[key] = row
    joined = []
    for row in frame.to_dicts():
        key = (str(row.get("ROOT_LOT_ID") or "").upper(), str(row.get("WAFER_ID") or "").upper())
        wip = wip_index.get(key)
        if not wip or not str(wip.get("step_id") or "").strip():
            continue
        joined.append({"root": str(row.get("ROOT_LOT_ID") or ""), "wafer": str(row.get("WAFER_ID") or ""),
                       "lot": str(row.get("LOT_ID") or ""), "step_id": str(wip.get("step_id") or ""),
                       "func_step": str(wip.get("function_step") or wip.get("func_step") or ""),
                       "product": str(wip.get("product") or clean)})
    joined.sort(key=lambda item: _step_order_key(item["step_id"]))
    top = joined[:max(1, min(int(max_rows or 12), 50))]
    out_rows = []
    for item in top:
        desc = lot_wip.describe_step(item["step_id"], item["product"])
        out_rows.append({
            "root_lot_id": item["root"], "wafer_id": item["wafer"], "lot_id": item["lot"],
            "step_id": item["step_id"],
            "step_desc": desc.get("step_desc") or item["func_step"],
            "function_step": item["func_step"],
            "knob": f"{matched_col}={matched_value}", "product": item["product"],
        })
    columns = ["root_lot_id", "wafer_id", "lot_id", "step_id", "step_desc", "function_step", "knob", "product"]
    columns = [col for col in columns if any(row.get(col) for row in out_rows)] or columns
    head = top[0] if top else {}
    head_desc = ((lot_wip.describe_step(head.get("step_id", ""), head.get("product", "")) or {}).get("step_desc")
                 or head.get("func_step", ""))
    answer = (
        f"{clean} ML_TABLE 에서 {matched_col}={matched_value} 인 wafer {len(joined):,}건 중 "
        f"WIP 기준 가장 앞선 step 은 {head.get('step_id', '-')} "
        f"({head_desc}) 입니다. "
        f"선행 {len(top):,}건을 표로 표시합니다." if top else
        f"{clean} ML_TABLE 에서 {matched_col}={matched_value} 조건은 {frame.height:,}건이나, "
        f"현재 WIP 에 남은 wafer 가 없습니다."
    )
    return {
        "handled": True,
        "intent": "knob_lead_lots",
        "action": "knob_lead_lots.read",
        "feature": "filebrowser",
        "answer": answer,
        "table": {"kind": "knob_lead_lots", "title": f"Knob 선행랏 ({matched_col}={matched_value})",
                  "columns": columns,
                  "rows": [{col: row.get(col, "") for col in columns} for row in out_rows],
                  "total": len(joined)},
        "context": {"product": clean},
        "sources": [ml_path.name, "lot_progress_latest_cache", "Vehicle_matching.csv"],
    }


def _run_fastpath_tool(
    name: str,
    step_input: dict[str, Any],
    request: Any | None,
    user: dict[str, Any] | None,
) -> dict[str, Any]:
    """fast-path 전용 tool 실행. handled dict, 실패면 handled=False."""
    prompt = str(step_input.get("prompt") or "")
    product = str(step_input.get("product") or "")
    if name == "dashboard_wip":
        return _run_dashboard_wip_view(prompt, product, request, user)
    if name == "lotmanage_table":
        from core import data_chat_features
        req = request if request is not None else _synthetic_request(user)
        res = data_chat_features.execute_feature(
            "lot_management.table", {"product": product}, req)
        # execute_feature 는 성공 시 예외 없이 tool 반환 (handled 키 없음).
        res = dict(res)
        res["handled"] = True
        table = res.get("table") if isinstance(res.get("table"), dict) else {}
        total = table.get("total", len(table.get("rows") or [])) if isinstance(table, dict) else 0
        res["answer"] = f"{product} 랏관리에 {total:,}건이 등록되어 있습니다."
        return res
    if name == "knob_lead_lots":
        try:
            max_rows = int(step_input.get("max_rows") or 12)
        except (TypeError, ValueError):
            max_rows = 12
        return _run_knob_lead_lots(prompt, product, max_rows=max_rows)
    if name in ("inline_values", "inline_radius_plot"):
        ask = str(step_input.get("inline_ask") or "")
        if ask in ("candidates", "map", "no_map"):
            return _inline_ask_result(ask, step_input)
        if name == "inline_values":
            return _run_inline_values(step_input)
        return _run_inline_radius_plot(step_input)
    if name == "eta_forecast":
        return _run_eta_forecast(step_input, request=request, user=user)
    if name == "et_download":
        return _run_et_download(step_input, request=request, user=user)
    if name == "et_time":
        return _run_et_time(step_input, request=request, user=user)
    if name == "need_product":
        options = step_input.get("need_products") or step_input.get("options") or []
        if not options:
            from core import data_chat
            try:
                names = data_chat.available_product_names()
            except Exception:
                names = []
            options = [{"label": item, "value": item} for item in (names or [])[:20]]
        return {
            "handled": True,
            "intent": "need_product",
            "action": "ask_product",
            "feature": "clarify",
            "answer": "어느 제품으로 조회할까요? 제품을 선택해 주세요.",
            "missing": ["product"],
            "needs_input": True,
            "clarification": {"kind": "product", "title": "제품 선택", "options": options,
                              "allow_other": True, "placeholder": "제품명 직접 입력"},
        }
    return {"handled": False, "error": f"unknown fastpath tool: {name}"}


def _inline_prompt_parts(prompt: str) -> tuple[str, str, str, str]:
    """후속턴 기계 판독 suffix(__INLINE_STEP/__INLINE_ITEM/__MAP) 분리.

    값에 공백이 있을 수 있어(예: item "5.0 PC") 다음 __KEY= 토큰 전까지
    누적한다.
    """
    step_id, item_id, map_name = "", "", ""
    kept: list[str] = []
    current: list[str] | None = None
    for token in str(prompt or "").split():
        if token.startswith("__INLINE_STEP="):
            current = None
            step_id = token[len("__INLINE_STEP="):]
            current = "step"
        elif token.startswith("__INLINE_ITEM="):
            current = None
            item_id = token[len("__INLINE_ITEM="):]
            current = "item"
        elif token.startswith("__MAP="):
            current = None
            map_name = token[len("__MAP="):]
            current = "map"
        elif token.startswith("__") and "=" in token:
            current = None
            kept.append(token)
        elif current == "step":
            step_id += " " + token
        elif current == "item":
            item_id += " " + token
        elif current == "map":
            map_name += " " + token
        else:
            kept.append(token)
    clean = " ".join(kept).strip()
    return clean, step_id.strip(), item_id.strip(), map_name.strip()


def _inline_candidate_label(candidate: dict[str, Any]) -> str:
    desc = str(candidate.get("desc") or candidate.get("item_desc") or "").strip()
    base = f"{candidate.get('step_id', '')} / {candidate.get('item_id', '')}"
    rows = candidate.get("lot_rows")
    suffix = f" · {rows}건" if isinstance(rows, int) and rows > 0 else ""
    return f"{base}{f' ({desc})' if desc else ''}{suffix}"


def _plan_inline_fastpath(
    prompt: str,
    user: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """Inline 별칭 값 조회 / Radius plot. (plan, meta) 또는 (None, None).

    lot 토큰 + 제품 확정 + 별칭 후보가 있어야 한다. 후보·map 이 모호하면
    clarification HITL 로 되묻고, 선택값에 기계 판독 suffix 를 심어 다음 턴에
    실행한다.
    """
    from core import inline_alias
    text = str(prompt or "")
    low = text.lower()
    lot_match = _INLINE_LOT_RE.search(text)
    inline_kw = ("inline" in low or "인라인" in text)
    location_kw = any(term in low for term in _LOCATION_TERMS)
    product, products = _fastpath_product(text)
    if not product:
        # 제품 없이 lot만 있으면 위치 조회일 수 있다 — Inline 신호가 없으면 양보.
        if location_kw and not inline_kw:
            return None, None
        if (lot_match or inline_kw) and products:
            return [_need_product_step(text, products)], {"planner": "fastpath:need_product", "step_count": 1}
        return None, None
    clean, pinned_step, pinned_item, map_name = _inline_prompt_parts(text)
    candidates = inline_alias.resolve_candidates(product, clean)
    if not candidates:
        # 측정 별칭 신호가 없으면 다른 경로(위치 등)에 양보한다.
        return None, None
    lot_match = _INLINE_LOT_RE.search(text)
    root_lot = str(lot_match.group(1) or "").upper() if lot_match else ""
    if not root_lot:
        # 점 없는 bare root(A1021 등)는 DB 대조로만 확정한다.
        root_lot = inline_alias.find_root_in_text(product, clean)
        if not root_lot:
            return None, None
    is_radius = ("radius" in low or "반경" in text) and any(t in low or t in text for t in _PLOT_TERMS)
    wafer_match = _INLINE_WAFER_RE.search(text)
    wafer = wafer_match.group(1) if wafer_match else ""
    tool_name = "inline_radius_plot" if is_radius else "inline_values"

    chosen: dict[str, Any] | None = None
    if pinned_step and pinned_item:
        chosen = {"step_id": pinned_step, "item_id": pinned_item}
    else:
        narrowed = inline_alias.disambiguate_by_lot(product, root_lot, candidates)
        with_rows = [c for c in narrowed if int(c.get("lot_rows") or 0) > 0]
        pool = with_rows or narrowed
        if len(pool) == 1:
            chosen = {"step_id": str(pool[0].get("step_id") or ""),
                      "item_id": str(pool[0].get("item_id") or "")}
    if chosen is None:
        narrowed = inline_alias.disambiguate_by_lot(product, root_lot, candidates)
        options = [{"label": _inline_candidate_label(c),
                    "value": f"{clean} __INLINE_STEP={c.get('step_id', '')} __INLINE_ITEM={c.get('item_id', '')}"}
                   for c in narrowed]
        return [{
            "tool": _fastpath_tool(tool_name),
            "input": {"prompt": text, "product": product, "root_lot": root_lot, "wafer": wafer,
                      "inline_ask": "candidates",
                      "candidates": [{"step_id": c.get("step_id"), "item_id": c.get("item_id"),
                                      "label": _inline_candidate_label(c)} for c in narrowed]},
            "reason": "Inline 후보 선택 필요",
            "source": "fastpath",
        }], {"planner": f"fastpath:{tool_name}_ask", "step_count": 1}

    if is_radius and not map_name:
        maps = inline_alias.available_maps(product, chosen["step_id"], chosen["item_id"])
        suggested = list(maps.get("suggested") or [])
        others = [t["table_name"] for t in (maps.get("tables") or [])
                  if isinstance(t, dict) and t.get("table_name") and t["table_name"] not in suggested]
        ordered = suggested + others
        if not ordered:
            return [{
                "tool": _fastpath_tool(tool_name),
                "input": {"prompt": text, "product": product, "root_lot": root_lot, "wafer": wafer,
                          "step_id": chosen["step_id"], "item_id": chosen["item_id"],
                          "inline_ask": "no_map"},
                "reason": "등록된 Inline map 없음 안내",
                "source": "fastpath",
            }], {"planner": f"fastpath:{tool_name}_ask", "step_count": 1}
        base = f"{clean} __INLINE_STEP={chosen['step_id']} __INLINE_ITEM={chosen['item_id']}"
        options = [{"label": name, "value": f"{base} __MAP={name}"} for name in ordered[:20]]
        return [{
            "tool": _fastpath_tool(tool_name),
            "input": {"prompt": text, "product": product, "root_lot": root_lot, "wafer": wafer,
                      "step_id": chosen["step_id"], "item_id": chosen["item_id"],
                      "inline_ask": "map", "map_options": ordered[:20]},
            "reason": "Inline map 선택 필요",
            "source": "fastpath",
        }], {"planner": f"fastpath:{tool_name}_ask", "step_count": 1}

    return [{
        "tool": _fastpath_tool(tool_name),
        "input": {"prompt": text, "product": product, "root_lot": root_lot, "wafer": wafer,
                  "step_id": chosen["step_id"], "item_id": chosen["item_id"], "map_name": map_name},
        "reason": f"Inline fast-path ({chosen['step_id']}/{chosen['item_id']})",
        "source": "fastpath",
    }], {"planner": f"fastpath:{tool_name}", "step_count": 1}


def _inline_ask_result(kind: str, step_input: dict[str, Any]) -> dict[str, Any]:
    """후보/map 선택 HITL 결과 (clarification)."""
    if kind == "candidates":
        options = []
        for cand in step_input.get("candidates") or []:
            if not isinstance(cand, dict):
                continue
            label = str(cand.get("label") or f"{cand.get('step_id')} / {cand.get('item_id')}")
            value = f"{_inline_prompt_parts(str(step_input.get('prompt') or ''))[0]} __INLINE_STEP={cand.get('step_id', '')} __INLINE_ITEM={cand.get('item_id', '')}"
            options.append({"label": label, "value": value})
        return {
            "handled": True, "intent": "inline_ask_candidates", "action": "inline.ask",
            "feature": "inline",
            "answer": "같은 별칭의 측정 후보가 여러 개입니다. 어떤 측정으로 조회할까요?",
            "missing": ["inline_candidate"], "needs_input": True,
            "clarification": {"kind": "inline_candidate", "title": "측정 후보를 선택하세요",
                              "options": options, "allow_other": False},
        }
    if kind == "map":
        clean, step_id, item_id, _map = _inline_prompt_parts(str(step_input.get("prompt") or ""))
        step_id = step_id or str(step_input.get("step_id") or "")
        item_id = item_id or str(step_input.get("item_id") or "")
        options = [{"label": name, "value": f"{clean} __INLINE_STEP={step_id} __INLINE_ITEM={item_id} __MAP={name}"}
                   for name in (step_input.get("map_options") or [])]
        return {
            "handled": True, "intent": "inline_ask_map", "action": "inline.ask",
            "feature": "inline",
            "answer": "어떤 map으로 매칭할까요? Inline map setting에서 선택하세요.",
            "missing": ["inline_map"], "needs_input": True,
            "clarification": {"kind": "inline_map", "title": "어떤 map으로 매칭할까요?",
                              "options": options, "allow_other": False},
        }
    return {
        "handled": True, "intent": "inline_ask_nomap", "action": "inline.ask",
        "feature": "inline",
        "answer": "해당 (step_id, item_id)에 등록된 Inline map이 없습니다. TEG 위치조회의 Inline map setting에 먼저 등록해 주세요.",
        "missing": ["inline_map"], "needs_input": True,
    }


def _run_inline_values(step_input: dict[str, Any]) -> dict[str, Any]:
    from core import inline_alias
    product = str(step_input.get("product") or "")
    root_lot = str(step_input.get("root_lot") or "")
    wafer = str(step_input.get("wafer") or "")
    step_id = str(step_input.get("step_id") or "")
    item_id = str(step_input.get("item_id") or "")
    if not (product and root_lot and step_id and item_id):
        return {"handled": False, "error": "Inline 조회 인자 부족"}
    result = inline_alias.query_values(product, root_lot, step_id, item_id, wafer_id=wafer)
    if not result.get("ok"):
        return {"handled": False, "error": str(result.get("error") or "INLINE 조회 실패")}
    avg_rows = result.get("avg_rows") or []
    lines = [f"- wafer {row['wafer_id']}: avg {row['avg']} (n={row['n']})" for row in avg_rows[:30]]
    if len(avg_rows) > 30:
        lines.append(f"… 외 {len(avg_rows) - 30} wafer")
    site_note = "SITE 필터 적용" if result.get("site_filtered") else "SITE 열 없음 — 전체 subitem"
    answer = (f"{product} {root_lot} {step_id}/{item_id} wafer별 avg "
              f"({len(avg_rows)} wafer, {result.get('total', 0)} shots, {site_note}).\n" + "\n".join(lines))
    return {
        "handled": True, "intent": "inline_values", "action": "inline.values",
        "feature": "inline",
        "answer": answer,
        "table": {"kind": "inline_wafer_avg", "title": f"wafer별 avg ({step_id}/{item_id})",
                  "columns": ["wafer_id", "n", "avg"],
                  "rows": [{"wafer_id": r["wafer_id"], "n": r["n"], "avg": r["avg"]} for r in avg_rows],
                  "total": len(avg_rows)},
        "context": {"product": product, "root_lot_id": root_lot},
        "sources": ["1.RAWDATA_DB_INLINE", "Inline_matching.csv"],
        "filters": result.get("filters", {}),
        "warnings": result.get("warnings", []),
    }


def _run_inline_radius_plot(step_input: dict[str, Any]) -> dict[str, Any]:
    from core import inline_alias
    product = str(step_input.get("product") or "")
    root_lot = str(step_input.get("root_lot") or "")
    wafer = str(step_input.get("wafer") or "")
    step_id = str(step_input.get("step_id") or "")
    item_id = str(step_input.get("item_id") or "")
    map_name = str(step_input.get("map_name") or "")
    if not (product and root_lot and step_id and item_id and map_name):
        return {"handled": False, "error": "Radius plot 인자 부족 (map 선택 필요)"}
    result = inline_alias.radius_panels(product, root_lot, step_id, item_id, map_name, wafer_id=wafer)
    if not result.get("ok"):
        return {"handled": False, "error": str(result.get("error") or "Radius plot 실패")}
    panels = result.get("panels") or []
    table = {"kind": "inline_radius_summary", "title": f"Radius plot 요약 ({map_name})",
             "columns": ["wafer_id", "n"],
             "rows": [{"wafer_id": p["wafer_id"], "n": p["n"]} for p in panels],
             "total": len(panels)}
    answer = (f"{product} {root_lot} {step_id}/{item_id} — map '{map_name}' 매칭, "
              f"{len(panels)} wafer, {result.get('total_points', 0)} points. "
              f"x=Radius, y=fab_value scatter + 3차 fitting.")
    tool: dict[str, Any] = {
        "handled": True, "intent": "inline_radius_plot", "action": "inline.radius_plot",
        "feature": "inline",
        "answer": answer,
        "table": table,
        "chart_panels": [{"title": p["title"], "chart": p["chart"]} for p in panels],
        "context": {"product": product, "root_lot_id": root_lot},
        "sources": ["1.RAWDATA_DB_INLINE", "Inline_matching.csv", "Inline map setting", "Chip_Radius.csv"],
        "filters": result.get("filters", {}),
        "warnings": result.get("warnings", []),
    }
    return tool


def _norm_step_text(value: Any) -> str:
    """step_desc 유사 비교용 정규화 (_·공백·대소문자 waive)."""
    return re.sub(r"[\s_\-]+", "", str(value or "")).upper()


def _leading_number(value: Any) -> str:
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(value or ""))
    return match.group(1) if match else ""


def _eta_step_chunk(prompt: str, product: str = "", lot: str = "") -> str:
    text = str(prompt or "")
    for token in (product, lot):
        if token:
            text = re.sub(r"(?<![\w])" + re.escape(token) + r"(?![\w])", " ", text, flags=re.I)
    text = re.sub(r"도착|언제쯤|언제|예정|예측|\bETA\b|완료\s*시각|해줘|해\s*줘|보여\s*줘|알려\s*줘|주세요|에$", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip(" ?.!-")


def _eta_preset_steps(product: str) -> list[dict[str, str]]:
    """Lot tracker 톱니바퀴 preset (flow-data) — 제품별 [{step_id, step_desc}]."""
    try:
        from routers import lot_tracker
        doc = lot_tracker._load_preset_doc()
    except Exception:
        return []
    if not isinstance(doc, dict):
        return []
    for key in (product, str(product or "").upper(), str(product or "").strip()):
        rows = doc.get(key)
        if isinstance(rows, list) and rows:
            return [r for r in rows if isinstance(r, dict) and r.get("step_id")]
    return []


def _eta_vehicle_steps(product: str) -> list[dict[str, str]]:
    """Vehicle_matching.csv 제품 행 — [{step_id, step_desc}]."""
    try:
        from core import matching_store
        rows, _path = matching_store.read_csv_rows("Vehicle_matching.csv")
    except Exception:
        return []
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        prod = str(row.get("product") or row.get("vehicle") or "")
        if product and prod and prod.upper() != str(product).upper():
            # vehicle 표기는 제품과 다를 수 있어 product 열이 있을 때만 필터.
            if str(row.get("product") or "") and prod.upper() != str(product).upper():
                continue
        step_id = str(row.get("step_id") or "").strip()
        if step_id:
            out.append({"step_id": step_id, "step_desc": str(row.get("step_desc") or "").strip()})
    return out


def _eta_step_candidates(prompt: str, product: str, lot: str = "") -> list[dict[str, Any]]:
    """step_desc 유사 후보. Inline 별칭 매칭이 item_desc 포함보다 우선한다."""
    chunk = _eta_step_chunk(prompt, product, lot)
    if not chunk or len(chunk) < 2:
        return []
    chunk_norm = _norm_step_text(chunk)
    chunk_num = _leading_number(chunk)
    words = [w for w in re.split(r"\s+", chunk) if len(w) >= 2]
    word_norms = [_norm_step_text(w) for w in words]

    alias_steps: set[str] = set()
    try:
        from core import inline_alias
        for cand in inline_alias.resolve_candidates(product, chunk):
            if cand.get("step_id"):
                alias_steps.add(str(cand["step_id"]).upper())
    except Exception:
        pass

    seen: dict[str, dict[str, Any]] = {}
    for source, rows in (("tracker_preset", _eta_preset_steps(product)),
                         ("vehicle_matching", _eta_vehicle_steps(product))):
        for row in rows:
            step_id = str(row.get("step_id") or "").strip()
            step_desc = str(row.get("step_desc") or "").strip()
            if not step_id or step_id.upper() in seen:
                continue
            desc_norm = _norm_step_text(step_desc)
            score = 0
            if chunk_norm and chunk_norm == desc_norm:
                score = 100
            elif word_norms and all(w and w in desc_norm for w in word_norms):
                score = 60
                if chunk_num and chunk_num == _leading_number(step_desc):
                    score = 85
            elif chunk_num and chunk_num == _leading_number(step_desc):
                score = 45
            if step_id.upper() in alias_steps:
                score += 60
            if score >= 50:
                seen[step_id.upper()] = {"step_id": step_id, "step_desc": step_desc,
                                         "source": source, "score": score}
    return sorted(seen.values(), key=lambda r: (-r["score"], r["step_id"]))[:5]


def _plan_eta_fastpath(
    prompt: str,
    user: dict[str, Any] | None = None,
    request: Any | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """ETA 도착예정 fastpath. (plan, meta) 또는 (None, None).

    step_id 직접 지정이면 data_chat_eta 로 곧장, step_desc면 유사 후보 확인
    HITL → 확정 후 Lot tracker 참고 lot 질문 → 결과 순으로 진행한다.
    """
    from core import data_chat_eta
    text = str(prompt or "")
    low = text.lower()
    base = dict(context) if isinstance(context, dict) else {}
    prior = base.get("eta_query") if isinstance(base.get("eta_query"), dict) else {}
    proposal = base.get("eta_proposal") if isinstance(base.get("eta_proposal"), dict) else {}

    # 0. step 확정 응답 (옵션 선택은 self-contained 문장으로 바로 파싱됨).
    if proposal and proposal.get("step_id") and proposal.get("lot_id"):
        picked = ""
        marker = re.search(r"__ETA_STEP=([^\s]+)", text)
        if marker:
            picked = marker.group(1)
        elif _ETA_AFFIRM_RE.match(text.strip()):
            picked = str(proposal.get("step_id") or "")
        step_tok = re.search(r"\b[A-Z]{1,3}\d{5,6}[A-Z0-9]{0,4}\b", text)
        if step_tok and not picked:
            picked = step_tok.group(0).upper()
        if picked:
            product = str(proposal.get("product") or "")
            full = f"{product} {proposal['lot_id']} {picked}에 언제도착해?"
            return [{
                "tool": _fastpath_tool("eta_forecast"),
                "input": {"prompt": full, "product": product, "context": base, "max_rows": 12},
                "reason": f"ETA step 확정 ({picked})",
                "source": "fastpath",
            }], {"planner": "fastpath:eta_forecast", "step_count": 1}

    # 1. 의도 감지.
    strong = any(term in low or term in text for term in _ETA_STRONG_TERMS)
    lot_like = bool(_LOT_TOKEN_RE.search(text))
    follow = bool(prior.get("lot_id")) and bool(
        base.get("pending_eta") or any(term in low or term in text for term in _ETA_TERMS) or lot_like)
    if not (strong or follow):
        return None, None

    # 2. 제품 확정.
    product, products = _fastpath_product(text)
    if not product:
        ctx_product = str(base.get("confirmed_product") or base.get("product") or prior.get("product") or "")
        if ctx_product:
            product = ctx_product.upper()
        elif products:
            return [_need_product_step(text, products)], {"planner": "fastpath:need_product", "step_count": 1}
        else:
            return None, None

    # 3. step_id 직접 지정이면 곧장 dispatch.
    if re.search(r"\b[A-Z]{1,3}\d{5,6}[A-Z0-9]{0,4}\b", text):
        return [{
            "tool": _fastpath_tool("eta_forecast"),
            "input": {"prompt": text, "product": product, "context": base, "max_rows": 12},
            "reason": "ETA fast-path (Lot tracker)",
            "source": "fastpath",
        }], {"planner": "fastpath:eta_forecast", "step_count": 1}

    # 4. step_desc 유사 후보 → 확인 HITL (1건이면 확정 제안, 복수면 선택).
    lot_match = _LOT_TOKEN_RE.search(text) or data_chat_eta._LOT.search(text)
    lot = str(lot_match.group(0) if lot_match else "").upper()
    candidates = _eta_step_candidates(text, product, lot)
    if candidates:
        return [{
            "tool": _fastpath_tool("eta_forecast"),
            "input": {"prompt": text, "product": product, "lot": lot, "context": base,
                      "inline_ask": "eta_step", "candidates": candidates, "max_rows": 12},
            "reason": "ETA 목표공정 확인 필요",
            "source": "fastpath",
        }], {"planner": "fastpath:eta_forecast_ask", "step_count": 1}

    # 5. 나머지는 data_chat_eta 상태머신에 위임 (lot/target/reference 순차 질문).
    return [{
        "tool": _fastpath_tool("eta_forecast"),
        "input": {"prompt": text, "product": product, "context": base, "max_rows": 12},
        "reason": "ETA fast-path (Lot tracker)",
        "source": "fastpath",
    }], {"planner": "fastpath:eta_forecast", "step_count": 1}


def _eta_forecast_chart(tool: dict[str, Any]) -> dict[str, Any] | None:
    """forecast points → 경과일 line chart."""
    forecast = tool.get("forecast") if isinstance(tool.get("forecast"), dict) else {}
    points = [p for p in (forecast.get("points") or []) if isinstance(p, dict)]
    if len(points) < 2:
        return None
    chart_points = []
    for index, point in enumerate(points):
        try:
            y = float(point.get("elapsed_days"))
        except (TypeError, ValueError):
            continue
        chart_points.append({
            "x": index,
            "x_label": str(point.get("step_label") or point.get("step_id") or ""),
            "y": y,
            "step_id": str(point.get("step_id") or ""),
            "eta": str(point.get("eta") or ""),
        })
    if len(chart_points) < 2:
        return None
    lot_id = str(tool.get("lot_id") or "")
    target = str(tool.get("target_step_id") or "")
    return {"kind": "line", "chart_type": "line",
            "title": f"{lot_id} {target} 예상 진행",
            "x_label": "공정", "y_label": "경과일", "points": chart_points}


def _eta_ask_step_result(step_input: dict[str, Any]) -> dict[str, Any]:
    product = str(step_input.get("product") or "")
    lot = str(step_input.get("lot") or "")
    candidates = [c for c in (step_input.get("candidates") or []) if isinstance(c, dict)][:5]
    options = []
    for cand in candidates:
        label = f"{cand.get('step_id', '')} ({cand.get('step_desc', '') or '설명없음'}) — 이 공정 맞나요?"
        value = f"{product} {lot} {cand.get('step_id', '')}에 언제도착해?"
        options.append({"label": label, "value": value})
    first = candidates[0] if candidates else {}
    return {
        "handled": True, "intent": "eta_ask_step", "action": "eta.ask_step",
        "feature": "eta",
        "answer": f"{product} {lot}의 목표 공정을 확인해 주세요. '{_eta_step_chunk(str(step_input.get('prompt') or ''), product, lot)}'와(과) 비슷한 공정을 찾았습니다.",
        "missing": ["target_step_id"], "needs_input": True,
        "clarification": {"kind": "eta_step", "title": "이 공정이 맞나요?",
                          "options": options, "allow_other": True,
                          "placeholder": "Step ID 직접 입력 (예: BV200000)"},
        "_context": {**(step_input.get("context") if isinstance(step_input.get("context"), dict) else {}),
                     "eta_proposal": {"product": product, "lot_id": lot,
                                      "step_id": str(first.get("step_id") or ""),
                                      "step_desc": str(first.get("step_desc") or "")}},
    }


def _run_eta_forecast(step_input: dict[str, Any], request: Any | None,
                      user: dict[str, Any] | None) -> dict[str, Any]:
    from core import data_chat_eta
    if str(step_input.get("inline_ask") or "") == "eta_step":
        return _eta_ask_step_result(step_input)
    prompt = str(step_input.get("prompt") or "")
    product = str(step_input.get("product") or "")
    base = step_input.get("context") if isinstance(step_input.get("context"), dict) else {}
    ctx = dict(base)
    if product:
        ctx["confirmed_product"] = product
    req = request if request is not None else _synthetic_request(user)
    try:
        res = data_chat_eta.dispatch(prompt, ctx, req)
    except Exception as exc:
        return {"handled": False, "error": f"ETA 조회 실패: {exc}"}
    if res is None:
        return {"handled": False, "error": "ETA 판단 불가"}
    tool = dict(res.get("tool") or {})
    # 참고 lot 선택지는 "참고 {lot}" 형태로 — data_chat_eta 가 lots[0]을
    # target이 아닌 reference 로 잡도록 (전체문 재구성 시 lots[0]이 꼬임).
    clar = tool.get("clarification")
    if isinstance(clar, dict) and isinstance(clar.get("options"), list):
        fixed = []
        for opt in clar["options"]:
            if not isinstance(opt, dict):
                continue
            value = str(opt.get("value") or opt.get("label") or "")
            if value and "__" not in value and not value.startswith("참고"):
                value = f"참고 {value}"
            fixed.append({**opt, "value": value})
        tool["clarification"] = {**clar, "options": fixed}
    out = {"handled": True, "feature": "eta",
           "answer": str(res.get("reply") or ""),
           "ok": bool(res.get("ok", True)),
           **{k: v for k, v in tool.items() if k != "feature"},
           "_context": res.get("context") if isinstance(res.get("context"), dict) else base}
    chart = _eta_forecast_chart(tool)
    if chart:
        out["chart_result"] = chart
    return out


def _et_products(me: dict[str, Any] | None) -> list[str]:
    """reformatize ET 제품 목록."""
    from routers import reformatize as rf
    try:
        rows = (rf.products(me or {}).get("products") or [])
    except Exception:
        return []
    return [str(r.get("product") or "").strip() for r in rows
            if isinstance(r, dict) and str(r.get("product") or "").strip()]


def _match_et_product(prompt: str, products: list[str]) -> str:
    try:
        from core import semantic_hitl
        norm = semantic_hitl.normalize_term
    except Exception:
        norm = lambda value: re.sub(r"[\s_-]+", "", str(value or "")).upper()  # noqa: E731
    text = norm(prompt)
    for name in products:
        if name and norm(name) and norm(name) in text:
            return name
    return ""


def _plan_et_download_fastpath(
    prompt: str,
    user: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """ET DATA 추출 fastpath. 제품 → 기간/랏 → alias → 다운로드 순 HITL."""
    text = str(prompt or "")
    low = text.lower()
    has_et = ("et data" in low or "etdata" in low
              or (re.search(r"\bet\b", low) and any(v in low or v in text for v in _ETDL_VERBS)))
    if not has_et or "측정시간" in text or "측정 시간" in text or "et time" in low:
        return None, None
    if not any(v in low or v in text for v in _ETDL_VERBS):
        return None, None
    me = user if isinstance(user, dict) else {}
    products = _et_products(me)
    clean, suffix = _et_prompt_parts(text)
    base = context if isinstance(context, dict) else {}
    draft = base.get("et_draft") if isinstance(base.get("et_draft"), dict) else {}
    if draft.get("product"):
        # 자유 입력 후속(랏/일수/항목): 초안과 병합해 이어간다.
        product = str(draft.get("product") or "")
        try:
            draft_days = int(draft.get("days") or 0)
        except (TypeError, ValueError):
            draft_days = 0
        days = _et_days(clean)
        if suffix.get("days", "").isdigit():
            days = max(0, min(int(suffix["days"]), 3660))
        if not days:
            days = draft_days
        lot = _et_bare_lot(clean, product) or str(draft.get("lot") or "")
        return [{
            "tool": _fastpath_tool("et_download"),
            "input": {"prompt": text, "product": product, "days": days, "lot": lot, "max_rows": 12},
            "reason": f"ET 다운로드 fast-path ({product}, 초안 이어감)",
            "source": "fastpath",
        }], {"planner": "fastpath:et_download", "step_count": 1}
    product = str(suffix.get("product") or "") or _match_et_product(clean, products)
    if not product:
        if products:
            options = [{"label": name, "value": f"{clean} __ET_PRODUCT={name}"} for name in products[:12]]
            return [{
                "tool": _fastpath_tool("et_download"),
                "input": {"prompt": text, "product": "", "inline_ask": "et_product",
                          "product_options": [o["label"] for o in options]},
                "reason": "ET 제품 선택 필요",
                "source": "fastpath",
            }], {"planner": "fastpath:et_download_ask", "step_count": 1}
        return None, None
    days = _et_days(clean)
    if suffix.get("days", "").isdigit():
        days = max(0, min(int(suffix["days"]), 3660))
    lot = _et_bare_lot(clean, product)
    if not days and not lot:
        return [{
            "tool": _fastpath_tool("et_download"),
            "input": {"prompt": text, "product": product, "inline_ask": "et_scope"},
            "reason": "ET 기간/랏 선택 필요",
            "source": "fastpath",
        }], {"planner": "fastpath:et_download_ask", "step_count": 1}
    return [{
        "tool": _fastpath_tool("et_download"),
        "input": {"prompt": text, "product": product, "days": days, "lot": lot, "max_rows": 12},
        "reason": f"ET 다운로드 fast-path ({product})",
        "source": "fastpath",
    }], {"planner": "fastpath:et_download", "step_count": 1}


def _et_draft(step_input: dict[str, Any]) -> dict[str, Any]:
    """ET 멀티턴 초안 (다음 턴 입력 병합용)."""
    try:
        days = int(step_input.get("days") or 0)
    except (TypeError, ValueError):
        days = 0
    return {"product": str(step_input.get("product") or ""),
            "days": days, "lot": str(step_input.get("lot") or "")}


def _et_ask_result(kind: str, step_input: dict[str, Any]) -> dict[str, Any]:
    prompt = str(step_input.get("prompt") or "")
    clean, _suffix = _et_prompt_parts(prompt)
    draft = _et_draft(step_input)
    if kind == "et_product":
        options = [{"label": name, "value": f"{clean} __ET_PRODUCT={name}"}
                   for name in (step_input.get("product_options") or [])]
        return {
            "handled": True, "intent": "et_download_ask", "action": "et_download.ask",
            "feature": "reformatize",
            "answer": "ET DATA를 뽑을 제품을 선택해 주세요.",
            "missing": ["product"], "needs_input": True,
            "clarification": {"kind": "product", "title": "ET 제품 선택",
                              "options": options, "allow_other": True,
                              "placeholder": "제품명 직접 입력"},
            "_context": {"et_draft": draft},
        }
    if kind == "et_scope":
        options = [{"label": f"최근 {days}일치", "value": f"{clean} __ET_DAYS={days}"}
                   for days in (3, 5, 7, 30)]
        return {
            "handled": True, "intent": "et_download_ask", "action": "et_download.ask",
            "feature": "reformatize",
            "answer": "며칠치 뽑아드릴까요? 아님 어떤 랏 뽑아드릴까요? 랏 ID를 직접 입력해도 됩니다.",
            "missing": ["days_lot"], "needs_input": True,
            "clarification": {"kind": "et_scope", "title": "며칠치? 아님 어떤 랏?",
                              "options": options, "allow_other": True,
                              "placeholder": "롯 ID 입력 (예: A1021)"},
            "_context": {"et_draft": draft},
        }
    if kind == "et_alias":
        carry = ""
        try:
            days = int(step_input.get("days") or 0)
        except (TypeError, ValueError):
            days = 0
        if days:
            carry += f" __ET_DAYS={days}"
        if str(step_input.get("lot") or ""):
            carry += f" {step_input.get('lot')}"
        options = [{"label": name, "value": f"{clean} __ET_ITEM={name}{carry}"}
                   for name in (step_input.get("alias_options") or [])]
        return {
            "handled": True, "intent": "et_download_ask", "action": "et_download.ask",
            "feature": "reformatize",
            "answer": "어떤 ET 항목으로 뽑을까요?",
            "missing": ["et_alias"], "needs_input": True,
            "clarification": {"kind": "et_alias", "title": "ET 항목 선택",
                              "options": options, "allow_other": True,
                              "placeholder": "항목 alias 직접 입력"},
            "_context": {"et_draft": draft},
        }
    return {"handled": False, "error": f"unknown et ask: {kind}"}


def _run_et_download(step_input: dict[str, Any], request: Any | None,
                     user: dict[str, Any] | None) -> dict[str, Any]:
    from routers import reformatize as rf
    ask = str(step_input.get("inline_ask") or "")
    if ask in ("et_product", "et_scope", "et_alias"):
        return _et_ask_result(ask, step_input)
    prompt = str(step_input.get("prompt") or "")
    clean, suffix = _et_prompt_parts(prompt)
    me = user if isinstance(user, dict) else {}
    product = str(step_input.get("product") or suffix.get("product") or "")
    if not product:
        product = _match_et_product(clean, _et_products(me))
    if not product:
        return {"handled": False, "error": "ET 제품 특정 실패"}
    days = int(step_input.get("days") or 0)
    if suffix.get("days", "").isdigit():
        days = max(0, min(int(suffix["days"]), 3660))
    if not days:
        days = _et_days(clean)
    lot = str(step_input.get("lot") or "") or _et_bare_lot(clean, product)
    if not days and not lot:
        step_input = {**step_input, "prompt": prompt, "product": product, "inline_ask": "et_scope"}
        return _et_ask_result("et_scope", step_input)
    try:
        items_payload = rf.list_items(product, me)
    except Exception as exc:
        return {"handled": False, "error": f"ET 항목 조회 실패: {exc}"}
    aliases = [str(r.get("alias") or "").strip() for r in (items_payload.get("items") or [])
               if isinstance(r, dict) and str(r.get("alias") or "").strip()]
    item = str(suffix.get("item") or "")
    if not item:
        try:
            from core import semantic_hitl
            norm = semantic_hitl.normalize_term
        except Exception:
            norm = lambda value: re.sub(r"[\s_-]+", "", str(value or "")).upper()  # noqa: E731
        text_norm = norm(clean)
        exact = next((a for a in sorted(aliases, key=len, reverse=True)
                      if a and norm(a) and norm(a) in text_norm), "")
        if exact:
            item = exact
        elif len(aliases) == 1:
            item = aliases[0]
    if not item:
        step_input = {**step_input, "prompt": prompt, "product": product,
                      "inline_ask": "et_alias", "alias_options": aliases[:12]}
        return _et_ask_result("et_alias", step_input)
    try:
        job = rf.download_start(
            rf.DownloadJobReq(product=product, items=[item], days=days, lot_filter=lot), me)
    except Exception as exc:
        return {"handled": False, "error": f"ET 다운로드 등록 실패: {exc}"}
    job_id = str(job.get("job_id") or "")
    scope = f"최근 {days}일" if days else f"랏 {lot}"
    table = {"kind": "et_download_job", "title": "ET 다운로드 작업",
             "columns": ["job_id", "상태", "파일"],
             "rows": [{"job_id": job_id, "상태": str(job.get("status") or "대기열 등록"),
                       "파일": f"{product}_reformatize.csv"}],
             "total": 1}
    return {
        "handled": True, "intent": "et_download", "action": "reformatize.download.start",
        "feature": "reformatize",
        "answer": f"{product} {item} {scope} ET 다운로드를 대기열에 등록했습니다. (job {job_id})",
        "table": table,
        "download_job": {"job_id": job_id, "filename": f"{product}_reformatize.csv",
                         "status_url": f"/api/reformatize/download/status?job_id={job_id}",
                         "file_url": f"/api/reformatize/download/file?job_id={job_id}"},
        "context": {},
        "slots": {"product": product, "item": item, "days": days, "lot": lot},
        "sources": ["/api/reformatize/download/start"],
    }


def _plan_et_time_fastpath(
    prompt: str,
    user: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """ET 측정시간 fastpath. 제품 → (추이 | 랏 측정) 분기."""
    text = str(prompt or "")
    low = text.lower()
    if not any(term in low or term in text for term in _ETTIME_TERMS):
        return None, None
    if any(v in low or v in text for v in ("다운로드", "download", "뽑아", "추출")):
        return None, None
    me = user if isinstance(user, dict) else {}
    from routers import et_time as et
    req = _synthetic_request(me if me.get("username") else None)
    try:
        products = [str(v or "").strip() for v in
                    (et.et_time_products(req, prefix="", limit=500).get("products") or []) if str(v or "").strip()]
    except Exception:
        products = []
    product = _match_et_product(text, products)
    if not product:
        if products:
            clean, _suffix = _et_prompt_parts(text)
            options = [{"label": name, "value": f"{clean} __ET_PRODUCT={name}"} for name in products[:12]]
            return [{
                "tool": _fastpath_tool("et_time"),
                "input": {"prompt": text, "product": "", "inline_ask": "et_product",
                          "product_options": [o["label"] for o in options]},
                "reason": "ET 측정시간 제품 선택 필요",
                "source": "fastpath",
            }], {"planner": "fastpath:et_time_ask", "step_count": 1}
        return None, None
    lot = _et_bare_lot(text, product)
    trend = (any(term in low or term in text for term in ("추이", "trend", "개월", "장기")) and not lot)
    months_match = re.search(r"(\d{1,3})\s*개월", text)
    months = max(1, min(int(months_match.group(1)), 120)) if months_match else 12
    if not trend and not lot:
        return [{
            "tool": _fastpath_tool("et_time"),
            "input": {"prompt": text, "product": product, "inline_ask": "et_lot"},
            "reason": "ET 측정시간 랏 선택 필요",
            "source": "fastpath",
        }], {"planner": "fastpath:et_time_ask", "step_count": 1}
    return [{
        "tool": _fastpath_tool("et_time"),
        "input": {"prompt": text, "product": product, "lot": lot, "trend": trend,
                  "months": months, "max_rows": 12},
        "reason": f"ET 측정시간 fast-path ({product})",
        "source": "fastpath",
    }], {"planner": "fastpath:et_time", "step_count": 1}


def _et_time_trend_chart(title: str, series: list[dict[str, Any]]) -> dict[str, Any] | None:
    points = []
    for entry in series or []:
        name = str(entry.get("name") or "")
        for point in entry.get("points") or []:
            if not isinstance(point, dict):
                continue
            try:
                y = float(point.get("y"))
            except (TypeError, ValueError):
                continue
            points.append({"x": point.get("x"), "x_label": str(point.get("x_label") or point.get("bucket") or ""),
                           "y": y, "series": name, "n": point.get("n")})
    if not points:
        return None
    return {"kind": "scatter", "chart_type": "scatter", "title": title,
            "x_label": "월", "y_label": "초", "color_by": "series", "points": points}


def _run_et_time(step_input: dict[str, Any], request: Any | None,
                 user: dict[str, Any] | None) -> dict[str, Any]:
    ask = str(step_input.get("inline_ask") or "")
    prompt = str(step_input.get("prompt") or "")
    if ask == "et_product":
        clean, _suffix = _et_prompt_parts(prompt)
        options = [{"label": name, "value": f"{clean} __ET_PRODUCT={name}"}
                   for name in (step_input.get("product_options") or [])]
        return {"handled": True, "intent": "et_time_ask", "action": "et_time.ask",
                "feature": "ettime",
                "answer": "ET 측정시간을 조회할 제품을 선택해 주세요.",
                "missing": ["product"], "needs_input": True,
                "clarification": {"kind": "product", "title": "ET 제품 선택",
                                  "options": options, "allow_other": True,
                                  "placeholder": "제품명 직접 입력"}}
    if ask == "et_lot":
        return {"handled": True, "intent": "et_time_ask", "action": "et_time.ask",
                "feature": "ettime",
                "answer": "개별 측정시간을 조회할 Root Lot ID를 알려주세요.",
                "missing": ["root_lot_id"], "needs_input": True}
    from routers import et_time as et
    me = user if isinstance(user, dict) else {}
    req = request if request is not None else _synthetic_request(me if me.get("username") else None)
    product = str(step_input.get("product") or "")
    lot = str(step_input.get("lot") or "")
    clean, suffix = _et_prompt_parts(prompt)
    if suffix.get("product"):
        product = suffix["product"]
    if not product:
        return {"handled": False, "error": "ET 제품 특정 실패"}
    if not lot:
        lot = _et_bare_lot(clean or prompt, product)
    trend = bool(step_input.get("trend"))
    months = max(1, min(int(step_input.get("months") or 12), 120))
    try:
        if trend or (not lot and any(t in prompt.lower() or t in prompt for t in ("추이", "trend", "개월", "장기"))):
            payload = et.et_time_trend(req, product=product, months=months)
            trend_map = payload.get("trend") if isinstance(payload.get("trend"), dict) else {}
            ranked = []
            for step_id, points in trend_map.items():
                valid = [p for p in (points or []) if isinstance(p, dict)]
                wafers = sum(int(p.get("wafers") or 0) for p in valid)
                weighted = sum(float(p.get("avg_duration_sec") or 0) * int(p.get("wafers") or 0) for p in valid)
                avg = weighted / wafers if wafers else 0.0
                ranked.append((avg, wafers, str(step_id), valid))
            ranked.sort(key=lambda row: (-row[0], -row[1], row[2]))
            series = []
            table_rows = []
            for avg, wafers, step_id, points in ranked[:12]:
                latest = points[-1] if points else {}
                series.append({
                    "name": step_id,
                    "points": [{"x": idx, "x_label": str(p.get("month") or ""),
                                "y": p.get("avg_duration_sec"), "n": p.get("wafers")} for idx, p in enumerate(points)],
                })
                table_rows.append({"step_id": step_id, "months": len(points), "wafers": wafers,
                                   "period_avg_sec": round(avg, 1),
                                   "latest_month": latest.get("month") or "",
                                   "latest_avg": latest.get("avg_duration_text") or ""})
            chart = _et_time_trend_chart(f"{product} 최근 {months}개월 ET 측정시간 추이", series)
            tool: dict[str, Any] = {
                "handled": True, "intent": "et_time_trend", "action": "et_time.trend",
                "feature": "ettime",
                "answer": f"{product} 최근 {months}개월 ET 측정시간 추이와 평균 시간이 큰 주요 step을 표시했습니다.",
                "table": {"kind": "et_time_trend", "title": "ET 측정시간 추이",
                          "columns": ["step_id", "months", "wafers", "period_avg_sec",
                                      "latest_month", "latest_avg"],
                          "rows": table_rows, "total": len(table_rows)},
                "slots": {"product": product, "months": months},
                "sources": [f"/api/et-time/trend?product={product}&months={months}"],
            }
            if chart:
                tool["chart_result"] = chart
            return tool
        if not lot:
            return {"handled": True, "intent": "et_time_ask", "action": "et_time.ask",
                    "feature": "ettime",
                    "answer": "개별 측정시간을 조회할 Root Lot ID를 알려주세요.",
                    "missing": ["root_lot_id"], "needs_input": True}
        payload = et.et_time_measure(req, product=product, root_lot_id=lot, lot_id="")
    except Exception as exc:
        return {"handled": False, "error": f"ET 측정시간 조회 실패: {exc}"}
    rows = [r for r in (payload.get("rows") or []) if isinstance(r, dict)]
    table = {"kind": "et_time_measure", "title": f"{lot} ET 측정시간",
             "columns": sorted({k for r in rows for k in r}) if rows else [],
             "rows": rows, "total": len(rows)}
    total = payload.get("total_duration_text") or payload.get("total_sec") or ""
    return {
        "handled": True, "intent": "et_time_measure", "action": "et_time.measure",
        "feature": "ettime",
        "answer": f"{product} {lot}의 ET 측정시간입니다. {('합계 ' + str(total)) if total else ''}".strip(),
        "table": table,
        "slots": {"product": product, "root_lot_id": lot},
        "sources": [f"/api/et-time/measure?product={product}&root_lot_id={lot}"],
    }


def _lot_wip_home_context(result: dict[str, Any]) -> dict[str, Any]:
    """lot_wip 결과를 오른쪽 Location 뷰가 읽는 context 로 변환."""
    context: dict[str, Any] = {}
    target = result.get("target") if isinstance(result.get("target"), dict) else {}
    filters = result.get("filters") if isinstance(result.get("filters"), dict) else {}
    product = str(target.get("product") or filters.get("product") or "")
    roots = filters.get("root_lot_ids") if isinstance(filters.get("root_lot_ids"), list) else []
    lots = filters.get("lot_ids") if isinstance(filters.get("lot_ids"), list) else []
    if product:
        context["product"] = product
    if roots and str(roots[0] or "").strip():
        context["root_lot_id"] = str(roots[0])
    elif str(target.get("root_lot_id") or "").strip():
        context["root_lot_id"] = str(target.get("root_lot_id"))
    if lots and str(lots[0] or "").strip():
        context["lot_id"] = str(lots[0])
    elif str(target.get("lot_id") or "").strip():
        context["lot_id"] = str(target.get("lot_id"))
    return context


def _execute_step(
    tool: dict[str, Any],
    step_input: dict[str, Any],
    *,
    request: Any | None = None,
    user: dict[str, Any] | None = None,
    agent_context: dict[str, Any] | None = None,
    allow_function_exec: bool = False,
) -> dict[str, Any]:
    """`_execute_step_impl` + 홈 챗 진행 표시.

    오케스트레이터가 도구를 하나 띄울 때마다 공개 이벤트(이름·상태·소요시간)를
    남긴다. 내부 추론이나 입력 원문은 담지 않는다 — `flowi_progress` 가 공개 키
    화이트리스트로 자른다. 실행 자체는 아래 impl 이 그대로 한다.
    """
    from core import flowi_progress

    name = str(tool.get("name") or "")
    title = str(tool.get("title") or name)
    token = flowi_progress.step_start("오케스트레이터", title, group=name)
    try:
        out = _execute_step_impl(
            tool, step_input,
            request=request, user=user,
            agent_context=agent_context,
            allow_function_exec=allow_function_exec,
        )
    except BaseException:
        flowi_progress.step_end(token, status="failed")
        raise
    flowi_progress.step_end(token, status=_status_from_trace_row(out))
    return out


def _execute_step_impl(
    tool: dict[str, Any],
    step_input: dict[str, Any],
    *,
    request: Any | None = None,
    user: dict[str, Any] | None = None,
    agent_context: dict[str, Any] | None = None,
    allow_function_exec: bool = False,
) -> dict[str, Any]:
    """단일 step 실행. step_input 은 input_schema 기준 dict.

    일부 unit_ai 는 전용 runtime 으로 실행한다. function-call 도구는
    ReAct 루프(allow_function_exec=True)에서만 실제 실행하고, 휴리스틱
    단일 패스 경로에서는 기존대로 stub 으로 남긴다 (LLM 없는 환경/테스트에서
    무거운 실행·전역 상태 오염 방지).
    """
    name = tool.get("name") or ""
    kind = tool.get("kind") or ""
    t0 = time.perf_counter()
    out: dict[str, Any] = {
        "ok": False,
        "kind": kind,
        "name": name,
        "input": step_input,
        "agent_context": deepcopy(agent_context or {}),
        "feedback_penalty": agent_feedback_penalties.tool_score_adjustment(name),
    }
    try:
        if kind == "unit_ai":
            # 실행 시점 권한 가드 — 카탈로그 필터를 우회한 경로(휴리스틱/alias/직접
            # 호출)에서도 tabs 권한 없는 기능은 실행하지 않는다.
            if not _unit_allowed(name, _allowed_feature_keys_for_user(user)):
                out.update({
                    "ok": False,
                    "blocked": True,
                    "status": "blocked",
                    "warnings": [f"'{name}' 기능 권한 없음"],
                    "result_preview": f"권한 차단: '{name}' 은 현재 계정 탭 권한에 없는 기능입니다.",
                    "result": {
                        "handled": True,
                        "blocked": True,
                        "intent": "permission_denied",
                        "answer": "현재 계정에는 이 기능 권한이 없어 실행할 수 없습니다. 관리자에게 해당 탭 권한을 요청하세요.",
                    },
                })
                return _finish_exec_out(out, t0)
            if name == "filebrowser_ai_sql":
                step_input = _prefill_filebrowser_source(
                    step_input, str(step_input.get("prompt") or ""))
                out["input"] = step_input
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

                if dashboard_agent_should_use_source_runtime(step_input, home_context=True):
                    from core.flowi_units.home_sql_join_dashboard_runtime import run_home_sql_join_dashboard_runtime

                    payload = dashboard_agent_source_payload(step_input)
                    res = _call_runtime_with_context(
                        run_home_sql_join_dashboard_runtime,
                        payload,
                        username=str((user or {}).get("username") or step_input.get("username") or ""),
                        agent_context=agent_context,
                    )
                    coerced = dashboard_agent_result_from_source_runtime_result(res)
                    out["ok"] = bool(coerced.get("ok"))
                    out["status"] = str(coerced.get("status") or ("blocked" if coerced.get("blocked") else ("success" if coerced.get("ok") else "failed")))
                    out["blocked"] = bool(coerced.get("blocked") or coerced.get("needs_input"))
                    out["warnings"] = _warnings_from_home_sql_join_dashboard_runtime(res)
                    out["result"] = coerced
                    out["public_result"] = out["result"]
                    out["sub_trace"] = deepcopy(res.get("trace") or [])
                    out["result_preview"] = _summarize_dashboard_agent_runtime_result(coerced)
                    return _finish_exec_out(out, t0)

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
            if name in ("dashboard_wip", "lotmanage_table", "knob_lead_lots", "need_product",
                            "inline_values", "inline_radius_plot",
                            "eta_forecast", "et_download", "et_time"):
                if not _unit_allowed(name, _allowed_feature_keys_for_user(user)):
                    out.update({
                        "ok": False,
                        "blocked": True,
                        "status": "blocked",
                        "warnings": [f"'{name}' 기능 권한 없음"],
                        "result_preview": f"권한 차단: '{name}' 은 현재 계정 탭 권한에 없는 기능입니다.",
                        "result": {
                            "handled": True,
                            "blocked": True,
                            "intent": "permission_denied",
                            "answer": "현재 계정에는 이 기능 권한이 없어 실행할 수 없습니다. 관리자에게 해당 탭 권한을 요청하세요.",
                        },
                    })
                    return _finish_exec_out(out, t0)
                res = _run_fastpath_tool(name, step_input, request=request, user=user)
                if res and res.get("handled"):
                    out["ok"] = True
                    out["result"] = res
                    out["public_result"] = res
                    out["result_preview"] = _summarize_result(res)
                    out["raw_keys"] = sorted(res.keys())
                else:
                    out["ok"] = False
                    out["result_preview"] = str((res or {}).get("error") or "fast-path 실행 실패")
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
                res = dict(res)
                if str(res.get("unit_ai") or name) == "lot_wip":
                    # 위치 답변은 오른쪽 Location 뷰로 렌더한다.
                    res["feature"] = "location"
                    res["context"] = _lot_wip_home_context(res)
                out["ok"] = True
                out["result"] = res
                out["public_result"] = res
                out["result_preview"] = _summarize_result(res)
                out["raw_keys"] = sorted(res.keys())
            else:
                out["ok"] = False
                out["result_preview"] = "unit_ai 가 prompt 를 처리하지 않음 (handled=False)"
        elif kind == "function":
            if not allow_function_exec:
                out["result_preview"] = "function-call 은 ReAct 루프에서만 실제 실행됩니다 (단일 패스는 stub)."
                return _finish_exec_out(out, t0)
            # function-call 도구 실제 실행 — Flow-i 단일 패스 엔진에 위임한다.
            # 오케스트레이터(LLM)가 만든 '단일 도구용 자연어' prompt 를 그대로 넘겨
            # 함수 추론 + 인자 추출 + 실행 로직을 재사용한다 (중복 구현 없음).
            from routers.llm import _allowed_flowi_feature_keys, _handle_flowi_query
            q = str(step_input.get("prompt") or step_input.get("natural_language") or "").strip()
            if not q:
                out["result_preview"] = "빈 함수 입력 prompt"
            else:
                q = f"{q}\n__FLOWI_FORCE_FUNCTION__={name}"
                res = _handle_flowi_query(
                    q,
                    str(step_input.get("product") or ""),
                    max_rows=max(4, min(24, int(step_input.get("max_rows") or 12))),
                    allowed_keys=(_allowed_flowi_feature_keys(user) if user else None),
                    username=str((user or {}).get("username") or "flowi"),
                    role=str((user or {}).get("role") or "user"),
                    agent_context=agent_context,
                )
                res = res if isinstance(res, dict) else {}
                handled = bool(res.get("handled"))
                blocked = bool(res.get("blocked"))
                guidance = handled and _function_result_is_guidance(res, requested_name=name)
                out["ok"] = handled and not blocked and not guidance
                out["blocked"] = blocked
                out["status"] = "blocked" if blocked else (
                    "guidance_fallback" if guidance else ("success" if out["ok"] else "failed"))
                out["result"] = res
                out["public_result"] = res
                if guidance:
                    # 함수가 실제 실행되지 못하고 기능 안내로 폴백 — 실데이터 아님.
                    # 진전으로 치지 않고, decision LLM 이 인자를 보강해 재시도하도록
                    # 무엇이 빠졌는지 preview 로 알린다.
                    out["warnings"] = [f"함수 '{name}' 실행이 기능 안내(route)로 폴백됨 — 실데이터 아님"]
                    out["result_preview"] = (
                        f"[guidance_fallback] '{name}' 가 실행되지 못하고 안내 문구만 반환됨. "
                        "lot/wafer/제품 식별자를 원문 그대로 포함한 단일 문장으로 입력을 바꿔 재시도 필요. "
                        "안내 원문: " + _short_text(res.get("answer") or "", 200)
                    )
                else:
                    out["result_preview"] = _short_text(
                        res.get("answer") or res.get("intent") or "", 400)
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


def _prefill_filebrowser_source(step_input: dict[str, Any], prompt: str) -> dict[str, Any]:
    """root/product 가 비어 있으면 prompt 토큰으로 FileBrowser DB 대상을 추정한다.

    planner(LLM)가 source slot 을 채우지 못해도 'PRODA에서 IOFF 높은 wafer' 같은
    질문이 즉시 실행되도록: product 는 roots 의 product 목록과 대소문자 무시 매칭,
    root 는 도메인 키워드(INLINE/VM/ET) 우선, 기본 FAB 계열. 추정 실패 시 원본
    유지(기존 needs_input 흐름으로 되묻기)."""
    if str(step_input.get("file") or "").strip():
        return step_input
    root = str(step_input.get("root") or "").strip()
    product = str(step_input.get("product") or "").strip()
    if root and product:
        return step_input
    try:
        from routers import filebrowser as fb
        roots = [str(r.get("name") or "") for r in (fb.list_roots().get("roots") or []) if r.get("name")]
    except Exception:
        return step_input
    if not roots:
        return step_input
    text = f"{prompt} {step_input.get('natural_language') or ''}"
    toks = {t.upper() for t in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{1,29}", text)}
    if not product:
        try:
            for rname in roots:
                for p in (fb.list_products(root=rname).get("products") or []):
                    pname = str((p.get("name") if isinstance(p, dict) else p) or "")
                    if pname and pname.upper() in toks:
                        product = pname
                        break
                if product:
                    break
        except Exception:
            return step_input
    if not product:
        return step_input
    if not root:
        def _pick(suffix: str) -> str:
            return next((r for r in roots if r.upper().endswith(suffix)), "")
        if "INLINE" in toks:
            root = _pick("INLINE")
        elif "VM" in toks:
            root = _pick("VM")
        elif "ET" in toks:
            root = _pick("ET")
        if not root:
            root = _pick("FAB") or roots[0]
    out = dict(step_input)
    out["root"] = root
    out["product"] = product
    out.setdefault("scope", "db_product")
    return out


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
    source_resolution = result.get("source_resolution") if isinstance(result.get("source_resolution"), dict) else {}
    data_need = result.get("data_need") if isinstance(result.get("data_need"), dict) else {}
    chart_result = dashboard.get("chart_result") if isinstance(dashboard.get("chart_result"), dict) else {}
    question = (
        _short_text(source_resolution.get("question"), 240)
        or _short_text(dashboard.get("question"), 240)
        or _short_text(result.get("question"), 240)
    )
    return {
        "ok": bool(result.get("ok")),
        "status": result.get("status") or "",
        "blocked": bool(result.get("blocked")),
        "needs_input": bool(result.get("blocked") or source_resolution.get("needs_input") or dashboard.get("needs_input")),
        "question": question,
        "run_id": result.get("run_id") or "",
        "unit_ai": result.get("unit_ai") or "home_sql_join_dashboard",
        "graph": deepcopy(result.get("graph") or {}),
        "trace": deepcopy(result.get("trace") or []),
        "semantic_frame": deepcopy(result.get("semantic_frame") or {}),
        "source_resolution": deepcopy(source_resolution),
        "base_source": deepcopy(result.get("base_source") or {}),
        "ai_sql": {
            "where_sql": _short_text(ai_sql.get("where_sql"), 1000),
            "display_sql": _short_text(ai_sql.get("display_sql"), 1000),
            "selected_columns": _safe_string_list(ai_sql.get("selected_columns"), _MAX_PREVIEW_COLS),
            "sort": deepcopy(ai_sql.get("sort") or {}),
            "preview_total_rows": ai_sql.get("preview_total_rows"),
            "ok": bool(ai_sql.get("ok")),
        },
        "data_need": deepcopy(data_need),
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
        "chart_type": dashboard.get("chart_type") or chart_result.get("chart_type") or "",
        "config": deepcopy(dashboard.get("config") or chart_result.get("config") or {}),
        "chart_result": deepcopy(chart_result),
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
    # 지식 카드가 담당 유닛을 지목하면 해당 도구 점수를 올려 지식 기반 선택을 반영.
    try:
        from core import knowledge_cards as _knowledge_cards
        hinted = set(_knowledge_cards.unit_hints(prompt))
    except Exception:
        hinted = set()
    if hinted:
        for p in picks:
            if str((p.get("tool") or {}).get("name") or "") in hinted:
                p["score"] = float(p.get("score") or 0.0) + 2.0
        picks.sort(key=lambda p: -float(p.get("score") or 0.0))
    plan: list[dict[str, Any]] = []
    for p in picks:
        plan.append({
            "tool": p["tool"],
            "input": {"prompt": prompt, "product": "", "max_rows": 12},
            "reason": f"휴리스틱 score={round(p['score'], 2)}",
            "source": "heuristic",
            "score": p["score"],
            "feedback_penalty": agent_feedback_penalties.tool_score_adjustment(p["tool"]),
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
        "feedback_penalty": step.get("feedback_penalty") or exec_out.get("feedback_penalty") or agent_feedback_penalties.tool_score_adjustment(tool["name"]),
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


def home_usage_block() -> dict[str, Any] | None:
    """현재 홈 요청의 차감 블록. flowi_turn usage 와 동일 shape + turn_exhausted.

    turn_budget 밖(예산 미적용 경로)이면 None. 이번 요청 합산(llm_calls_used)과
    분당 window 스냅샷을 함께 담아 프론트가 "N회 차감 · 분당 잔여"를 표시한다.
    """
    try:
        from core import llm_usage
        turn = llm_usage.current_turn()
        if turn is None:
            return None
        used = max(0, int(turn.get("llm_calls_used") or 0))
        limit = max(0, int(turn.get("llm_call_limit") or 0))
        return {
            "llm_calls_used": used,
            "llm_call_limit": limit,
            "llm_calls_remaining": max(0, limit - used),
            "turn_exhausted": bool(limit and used >= limit),
            **llm_usage.snapshot(),
        }
    except Exception:
        logger.debug("home usage block build failed", exc_info=True)
        return None


def _extract_step_query(result: dict[str, Any]) -> dict[str, Any]:
    """공개 결과에서 쿼리 증거만 추린다. 없으면 {} (섹션 숨김용)."""
    if not isinstance(result, dict):
        return {}
    query: dict[str, Any] = {}
    for container_key in ("merged", "filter", "ai_sql", "sql_draft"):
        container = result.get(container_key)
        if not isinstance(container, dict):
            continue
        for sql_key in ("display_sql", "sql", "where_sql", "applied_where_sql"):
            text = _short_text(container.get(sql_key), 1000)
            if text and not query.get("sql"):
                query["sql"] = text
        cols = _safe_string_list(container.get("selected_columns"), _MAX_PREVIEW_COLS)
        if cols and not query.get("selected_columns"):
            query["selected_columns"] = cols
    for sql_key in ("display_sql", "sql", "where_sql", "applied_where_sql"):
        text = _short_text(result.get(sql_key), 1000)
        if text and not query.get("sql"):
            query["sql"] = text
    return query


def build_response_evidence(
    *,
    trace: list[dict[str, Any]] | None,
    semantic_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """왼쪽 해석 패널용 공개 증거 블록. 내부 추론 원문은 담지 않는다."""
    steps: list[dict[str, Any]] = []
    for row in trace or []:
        if not isinstance(row, dict):
            continue
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        step_input = row.get("input") if isinstance(row.get("input"), dict) else {}
        targets = {
            key: _short_text(step_input.get(key), 120)
            for key in ("product", "root_lot_id", "lot_id", "file")
            if _short_text(step_input.get(key), 120)
        }
        steps.append({
            "tool": _short_text(row.get("tool"), 80),
            "title": _short_text(row.get("title"), 120),
            "kind": _short_text(row.get("kind"), 20),
            "status": _short_text(row.get("status"), 20),
            "ok": bool(row.get("ok")),
            "reason": _short_text(row.get("reason"), 200),
            "source": _short_text(row.get("source"), 60),
            "ms": row.get("ms", 0),
            "targets": targets,
            "sources": _safe_string_list(result.get("sources"), 12),
            "query": _extract_step_query(result),
            "warnings": _safe_string_list(row.get("warnings"), 8),
            "result_preview": _short_text(row.get("result_preview"), 400),
        })
    return {"semantic": semantic_summary if isinstance(semantic_summary, dict) else {}, "steps": steps}


def _attach_runtime_result(
    out: dict[str, Any],
    *,
    prompt: str,
    user: dict[str, Any] | None,
    source: str = "home_agent",
    run_id: str | None = None,
) -> dict[str, Any]:
    trace = out.get("trace") if isinstance(out.get("trace"), list) else []
    if not out.get("answer") and out.get("reply"):
        out["answer"] = out.get("reply")
    if not out.get("reply") and out.get("answer"):
        out["reply"] = out.get("answer")
    if "tool" not in out:
        out["tool"] = _tool_from_tool_calls(out.get("tool_calls")) or _tool_summary_from_trace(trace)
    # 멀티턴 fastpath(ETA 등)가 다음 턴에 이어쓸 대화 context 회수.
    tool_calls = out.get("tool_calls") if isinstance(out.get("tool_calls"), list) else []
    for call in tool_calls:
        output = call.get("output") if isinstance(call, dict) and isinstance(call.get("output"), dict) else {}
        carried = output.pop("_context", None) if isinstance(output, dict) else None
        if isinstance(carried, dict) and "context" not in out:
            out["context"] = carried
    usage = home_usage_block()
    if usage is not None:
        out["usage"] = usage
        if usage.get("turn_exhausted"):
            meta = out.get("meta")
            if isinstance(meta, dict):
                meta["budget_exhausted"] = True
    meta = out.get("meta") if isinstance(out.get("meta"), dict) else {}
    out["evidence"] = build_response_evidence(
        trace=trace,
        semantic_summary=meta.get("semantic_summary") if isinstance(meta.get("semantic_summary"), dict) else None,
    )
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


def _action_signature(tool_name: str, merged_input: dict[str, Any]) -> str:
    """반복-액션 가드용 서명. 의미 있는 입력 키만 정규화해 해시한다."""
    payload: dict[str, Any] = {}
    for key in ("prompt", "product", "root", "file", "columns", "max_rows"):
        if isinstance(merged_input, dict) and key in merged_input:
            payload[key] = merged_input.get(key)
    try:
        sig = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        sig = str(payload)
    return f"{tool_name}|{sig}"


def _compose_final_reply(
    prompt: str,
    trace: list[dict[str, Any]],
    model_answer: str,
    semantic_summary: dict[str, Any],
    *,
    timeout_s: int = _REACT_DECISION_TIMEOUT_S,
) -> str:
    """최종 답변 생성.

    1) model 이 final.answer 를 줬으면 그대로 사용 (공통 경로 — LLM 재호출 없음).
    2) 아니면 루프 활성 시 관찰 기반으로 1회 LLM 요약.
    3) 실패/비활성/관찰없음 시 항상 `_synthesize_reply(trace)` fallback.
    """
    answer = _short_text(model_answer, 4000) if model_answer else ""
    if answer:
        return answer
    if not _react_loop_enabled():
        return _synthesize_reply(trace)
    observations = _observation_summary(trace)
    if not observations:
        return _synthesize_reply(trace)
    try:
        from core import llm_adapter
        system = (
            "You are Flow-i. Summarize the tool observations into a concise Korean "
            "answer to the user goal. Do not invent data not present in observations."
        )
        user_prompt = (
            f"# 사용자 목표\n{prompt}\n\n"
            # 관찰 1건이 _OBSERVATION_CHARS 까지 올 수 있으므로 여기서 다시 반토막
            # 내면 최종 답변이 또 잘린다.
            f"# 관찰(observations)\n{json.dumps(observations, ensure_ascii=False)[:8000]}\n\n"
            "위 관찰만 근거로 한국어로 간결히 답하라."
        )
        out = llm_adapter.complete(
            user_prompt,
            system=system,
            timeout=max(1, min(int(timeout_s or _REACT_DECISION_TIMEOUT_S), _REACT_DECISION_TIMEOUT_S)),
        )
        if out.get("ok") and out.get("text"):
            return _short_text(out.get("text"), 4000)
    except Exception:
        logger.info("react final compose failed", exc_info=True)
    return _synthesize_reply(trace)


def _run_react_loop(
    *,
    prompt: str,
    tools: list[dict[str, Any]],
    semantic_summary: dict[str, Any],
    user: dict[str, Any] | None,
    request: Any | None,
    max_steps: int,
) -> Iterator[dict[str, Any]]:
    """관찰→결정→실행을 반복하는 단일 control-flow 제너레이터.

    orchestrate() 는 이 제너레이터를 drain 해 최종 `final` 이벤트만 쓰고,
    orchestrate_stream() 는 중간 이벤트를 SSE 로 매핑한다. 두 진입점이 같은
    루프 구현을 공유하므로 로직 드리프트가 없다.

    내부 이벤트 union:
      {"kind": "loop_start", "max_steps": int}
      {"kind": "decision", "index": int, "action": str, "tool": str, "reason": str}
      {"kind": "step_start", "index": int, "tool": str, "tool_def": dict, "input": dict}
      {"kind": "step_end", "index": int, "tool": str, "exec_out": dict,
       "trace_row": dict, "tool_call": ToolCall}
      {"kind": "final", "trace": list, "tool_calls": list, "reply": str, "stop_reason": str}

    종료 가드 3중 + model_final/blocked/llm_error 로 무한 루프를 막는다.
    """
    trace: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []
    accumulated: dict[str, Any] = {}
    seen_signatures: set[str] = set()
    model_answer = ""
    ask_user: dict[str, Any] | None = None
    stop_reason = "max_steps"
    no_progress_streak = 0
    started = time.monotonic()
    deadline = started + _react_deadline_seconds(_user_role(user))

    yield {"kind": "loop_start", "max_steps": max_steps}

    for index in range(max_steps):
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 2.0:
            stop_reason = "deadline"
            break
        parent_context = _parent_context_from_tool_calls(tool_calls)
        observations = _observation_summary(trace)
        decision = _decide_next_action(
            prompt=prompt,
            tools=tools,
            semantic_summary=semantic_summary,
            observations=observations,
            step_index=index,
            max_steps=max_steps,
            timeout_s=max(1, min(_REACT_DECISION_TIMEOUT_S, int(remaining_s))),
        )
        if decision is None:
            stop_reason = "llm_error"
            break
        if decision.get("action") == "final":
            model_answer = _short_text(decision.get("answer"), 4000)
            stop_reason = "model_final"
            yield {"kind": "decision", "index": index, "action": "final",
                   "tool": "", "reason": decision.get("reason", ""),
                   "decision_mode": decision.get("native_mode") or "json"}
            break
        if decision.get("action") == "ask_user":
            # Human-in-the-loop — 사용자 답변이 있어야 진행 가능. 루프를 끊고
            # 질문을 그대로 반환한다 (홈 챗 UI 가 선택지 버튼으로 렌더).
            ask_user = {"question": decision.get("question") or "",
                        "choices": list(decision.get("choices") or [])}
            stop_reason = "ask_user"
            yield {"kind": "decision", "index": index, "action": "ask_user",
                   "tool": "", "reason": decision.get("reason", ""),
                   "decision_mode": decision.get("native_mode") or "json"}
            break

        tool_def = decision.get("tool") or {}
        tool_name = decision.get("tool_name") or str(tool_def.get("name") or "")
        yield {"kind": "decision", "index": index, "action": "call_tool",
               "tool": tool_name, "reason": decision.get("reason", ""),
               "decision_mode": decision.get("native_mode") or "json"}

        # 입력 병합: 기존 단일 패스 로직과 동일.
        decision_input = decision.get("input") if isinstance(decision.get("input"), dict) else {}
        merged_input = {**decision_input, **{k: v for k, v in accumulated.items() if k not in decision_input}}
        merged_input = _merge_step_input_with_parent(merged_input, parent_context)
        if "prompt" not in merged_input:
            merged_input["prompt"] = prompt

        # 반복-액션 가드: 동일 tool + 의미입력 재호출 차단.
        signature = _action_signature(tool_name, merged_input)
        if signature in seen_signatures:
            stop_reason = "repeated_action"
            break
        seen_signatures.add(signature)

        step = {"tool": tool_def, "input": merged_input,
                "reason": decision.get("reason", ""),
                "source": "react_native_tools" if decision.get("native_mode") == "tools" else "react"}
        yield {"kind": "step_start", "index": index, "tool": tool_name,
               "tool_def": tool_def, "input": merged_input}
        exec_out = _execute_step(tool_def, merged_input, request=request, user=user,
                                 agent_context=parent_context, allow_function_exec=True)
        trace_row = _make_trace_row(step, exec_out)
        tool_call = _make_tool_call(step, exec_out)
        trace.append(trace_row)
        tool_calls.append(tool_call)
        yield {"kind": "step_end", "index": index, "tool": tool_name,
               "exec_out": exec_out, "trace_row": trace_row, "tool_call": tool_call}

        # 스칼라 누적(기존 :1786-1791 로직과 동일) + 진전 판정.
        progressed = bool(exec_out.get("ok"))
        if exec_out.get("ok") and isinstance(exec_out.get("result"), dict):
            res = exec_out["result"]
            for key in ("product", "lot_id", "root_lot_id", "wafer_id"):
                v = res.get(key)
                if isinstance(v, str) and v and key not in accumulated:
                    accumulated[key] = v

        if exec_out.get("blocked"):
            stop_reason = "blocked"
            break

        if progressed:
            no_progress_streak = 0
        else:
            no_progress_streak += 1
            if no_progress_streak >= 2:
                stop_reason = "no_progress"
                break

    remaining_s = max(1, int(deadline - time.monotonic()))
    if ask_user:
        # 질문 자체가 응답 — LLM compose 생략.
        reply = ask_user.get("question") or ""
    else:
        reply = _compose_final_reply(
            prompt,
            trace,
            model_answer,
            semantic_summary,
            timeout_s=max(1, min(_REACT_DECISION_TIMEOUT_S, remaining_s)),
        )
    yield {
        "kind": "final",
        "trace": trace,
        "tool_calls": tool_calls,
        "reply": reply,
        "stop_reason": stop_reason,
        "ask_user": ask_user,
    }


@_with_home_turn_budget
def orchestrate(
    prompt: str,
    user: dict[str, Any] | None = None,
    top_k: int = 2,
    *,
    request: Any | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """홈 에이전트 메인 엔트리포인트.

    prompt → alias 매칭 → LLM planner (선택) → 휴리스틱 → step 실행 → trace + 응답.

    요청 스코프 turn_budget 안에서 실행된다 (planner·런타임·최종 요약 합산,
    상한은 home_turn_limit). 응답에는 usage·evidence 가 포함된다.
    context 가 있으면 멀티턴 fastpath(ETA 등)가 이전 턴 상태를 이어받으며,
    갱신된 context 를 응답의 "context" 로 돌려준다.
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

    tools = _filter_tools_for_user(tool_registry.list_tools(include_stats=False), user)

    if _react_loop_enabled():
        semantic_summary = _semantic_frame_summary(_semantic_frame_for_prompt(prompt))
        react_out: dict[str, Any] | None = None
        for event in _run_react_loop(
            prompt=prompt,
            tools=tools,
            semantic_summary=semantic_summary,
            user=user,
            request=request,
            max_steps=_react_max_iters(_user_role(user)),
        ):
            if event.get("kind") == "final":
                react_out = event
        # llm_error 로 아무 step 도 못 돌면 기존 alias/heuristic 경로로 graceful degrade.
        if react_out is not None and (react_out.get("trace") or react_out.get("stop_reason") != "llm_error"):
            react_trace = react_out.get("trace") or []
            return _attach_runtime_result({
                "ok": True,
                "prompt": prompt,
                "trace": react_trace,
                "tool_calls": react_out.get("tool_calls") or [],
                "meta": {
                    "planner": "react",
                    "step_count": len(react_trace),
                    "stop_reason": react_out.get("stop_reason"),
                    "semantic_summary": semantic_summary,
                },
                "reply": react_out.get("reply") or "",
                "ask_user": react_out.get("ask_user"),
                "picked_count": len(react_trace),
            }, prompt=prompt, user=user)

    plan: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {"planner": "heuristic"}
    # 단일 패스도 왼쪽 해석 패널용 semantic frame 을 항상 포함한다.
    single_pass_semantic = _semantic_frame_summary(_semantic_frame_for_prompt(prompt))

    plan = _plan_from_alias(prompt, tools)
    if plan:
        meta = {"planner": "alias", "step_count": len(plan)}
    if not plan:
        fast_plan, fast_meta = _plan_from_fastpath(prompt, user=user, request=request,
                                                   context=context)
        if fast_plan:
            plan, meta = fast_plan, dict(fast_meta or {})
    if not plan and _llm_planner_enabled():
        plan = _plan_with_llm(prompt, tools)
        if plan:
            meta = {"planner": "llm", "step_count": len(plan)}
    if not plan:
        plan, hmeta = _plan_from_heuristic(prompt, top_k=top_k)
        meta.update(hmeta)
        meta["planner"] = "heuristic"
    meta["semantic_summary"] = single_pass_semantic

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


def _orchestrate_stream_react(
    prompt: str,
    tools: list[dict[str, Any]],
    user: dict[str, Any] | None,
    request: Any | None,
    run_id: str,
) -> Iterator[dict[str, Any]]:
    """orchestrate_stream 의 ReAct 분기.

    `_run_react_loop` 내부 이벤트를 기존 SSE shape(status/plan/step_start/step_end/reply)
    로 매핑한다. 사전 plan 이 없으므로 `plan` 이벤트의 steps 는 빈 배열이고, 실행된
    도구로 selected_units 와 graph 를 incremental 하게 키운다.
    """
    semantic_summary = _semantic_frame_summary(_semantic_frame_for_prompt(prompt))
    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "semantic_layer",
        "status": "용어해석중",
        "graph": build_home_runtime_graph(statuses={"prompt_input": "success", "semantic_layer": "running"}),
        "semantic": semantic_summary,
    }
    plan_statuses = {"prompt_input": "success", "semantic_layer": "success", "orchestrator": "running"}
    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "orchestrator",
        "status": "실행 계획 정리중",
        "graph": build_home_runtime_graph(statuses=plan_statuses),
    }
    yield {
        "type": "plan",
        "run_id": run_id,
        "meta": {"planner": "react"},
        "graph": build_home_runtime_graph(statuses=plan_statuses),
        "steps": [],
    }

    selected_units: list[str] = []
    trace: list[dict[str, Any]] = []
    tool_calls: list[ToolCall] = []
    stop_reason = "max_steps"
    reply = ""
    for event in _run_react_loop(
        prompt=prompt,
        tools=tools,
        semantic_summary=semantic_summary,
        user=user,
        request=request,
        max_steps=_react_max_iters(_user_role(user)),
    ):
        kind = event.get("kind")
        if kind == "step_start":
            tool_def = event.get("tool_def") or {}
            unit = _normalize_unit_name(str(tool_def.get("name") or "")) if tool_def.get("kind") == "unit_ai" else ""
            if unit and unit not in selected_units:
                selected_units.append(unit)
            running_statuses = {"prompt_input": "success", "semantic_layer": "success", "orchestrator": "success"}
            for done_unit in selected_units:
                running_statuses[f"unit_ai:{done_unit}"] = "success"
            if unit:
                running_statuses[f"unit_ai:{unit}"] = "running"
            yield {
                "type": "step_start",
                "run_id": run_id,
                "status": "단위AI 실행중" if tool_def.get("kind") == "unit_ai" else "실행 계획 정리중",
                "graph": build_home_runtime_graph(selected_units=selected_units, statuses=running_statuses),
                "index": event.get("index"),
                "tool": str(tool_def.get("name") or ""),
                "kind": tool_def.get("kind"),
                "input": event.get("input") or {},
            }
        elif kind == "step_end":
            row = event.get("trace_row") or {}
            trace.append(row)
            tool_call = event.get("tool_call")
            if tool_call is not None:
                tool_calls.append(tool_call)
            step_statuses = _runtime_statuses({"ok": True, "trace": trace, "tool": _tool_summary_from_trace(trace)}, selected_units)
            yield {
                "type": "step_end",
                "run_id": run_id,
                "status": "단위AI 실행중",
                "graph": build_home_runtime_graph(selected_units=selected_units, statuses=step_statuses),
                "index": event.get("index"),
                "tool": event.get("tool"),
                "ok": row.get("ok"),
                "node_status": row.get("status"),
                "ms": row.get("ms", 0),
                "result_preview": row.get("result_preview", ""),
            }
        elif kind == "final":
            trace = event.get("trace") or trace
            tool_calls = event.get("tool_calls") or tool_calls
            reply = event.get("reply") or ""
            stop_reason = event.get("stop_reason") or stop_reason

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
        "reply": reply,
        "picked_count": len(trace),
        "meta": {
            "planner": "react",
            "step_count": len(trace),
            "stop_reason": stop_reason,
            "semantic_summary": semantic_summary,
        },
    }, prompt=prompt, user=user, run_id=run_id)
    yield final


@_with_home_turn_budget
def orchestrate_stream(
    prompt: str,
    user: dict[str, Any] | None = None,
    top_k: int = 2,
    *,
    request: Any | None = None,
) -> Iterator[dict[str, Any]]:
    """SSE 용 generator. orchestrate 와 동일하지만 step 별로 event 를 yield 한다.

    소비 측 (FastAPI StreamingResponse)은 각 dict 를 `event: <type>\\ndata: <json>\\n\\n`
    으로 직렬화한다. 최종 reply 에는 usage·evidence 가 포함된다.
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
    tools = _filter_tools_for_user(tool_registry.list_tools(include_stats=False), user)
    if _react_loop_enabled():
        yield from _orchestrate_stream_react(prompt, tools, user, request, run_id)
        return
    plan: list[dict[str, Any]] | None = None
    meta: dict[str, Any] = {"planner": "heuristic"}
    stream_semantic = _semantic_frame_summary(_semantic_frame_for_prompt(prompt))
    yield {
        "type": "status",
        "run_id": run_id,
        "stage": "semantic_layer",
        "status": "용어해석중",
        "graph": build_home_runtime_graph(statuses={"prompt_input": "success", "semantic_layer": "running"}),
        "semantic": stream_semantic,
    }
    plan = _plan_from_alias(prompt, tools)
    if plan:
        meta = {"planner": "alias", "step_count": len(plan)}
    if not plan:
        fast_plan, fast_meta = _plan_from_fastpath(prompt, user=user)
        if fast_plan:
            plan, meta = fast_plan, dict(fast_meta or {})
    if not plan and _llm_planner_enabled():
        plan = _plan_with_llm(prompt, tools)
        if plan:
            meta = {"planner": "llm", "step_count": len(plan)}
    if not plan:
        plan, hmeta = _plan_from_heuristic(prompt, top_k=top_k)
        meta.update(hmeta)
        meta["planner"] = "heuristic"
    meta["semantic_summary"] = stream_semantic
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
