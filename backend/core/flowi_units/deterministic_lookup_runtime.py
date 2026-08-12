"""Deterministic FAB lookup runtime for Agent Unit AI panels."""
from __future__ import annotations

import datetime as _dt
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from core import agent_feedback_penalties
from core import agent_semantic_service
from core.paths import PATHS
from core.utils import jsonl_append, jsonl_read, jsonl_trim


RUNTIME_UNIT_KEYS = {"step_lookup", "ppid_knob", "lot_wip"}

GRAPH_NODES: tuple[dict[str, str], ...] = (
    {"id": "prompt_input", "label": "프롬프트 입력", "phase": "input"},
    {"id": "semantic_parse", "label": "용어 해석", "phase": "semantic"},
    {"id": "lookup_execute", "label": "CSV 조회", "phase": "execute"},
    {"id": "answer_render", "label": "답변 정리", "phase": "render"},
)

# 유닛마다 실행 노드가 무엇을 읽는지 다르다 — 그래프 라벨이 "CSV 조회" 로 고정되면
# 캐시를 읽는 유닛에서 근거를 잘못 알려준다.
_EXECUTE_NODE_LABELS = {
    "lot_wip": "latest cache 조회",
}

GRAPH_EDGES: tuple[dict[str, str], ...] = (
    {"source": "prompt_input", "target": "semantic_parse"},
    {"source": "semantic_parse", "target": "lookup_execute"},
    {"source": "lookup_execute", "target": "answer_render"},
)

STATE_DESIGN: dict[str, dict[str, Any]] = {
    "run_id": {"description": "Runtime execution id.", "producer": "runtime", "public": True},
    "request": {"description": "Sanitized prompt and optional product scope.", "producer": "prompt_input", "public": True},
    "semantic_frame": {"description": "Shared semantic interpretation and product hint.", "producer": "semantic_parse", "public": True},
    "lookup_result": {"description": "Deterministic unit handle() result from the FAB CSV reference.", "producer": "lookup_execute", "public": True},
    "answer": {"description": "Rendered answer string.", "producer": "answer_render", "public": True},
    "table": {"description": "Small public result table, when lookup returned rows.", "producer": "answer_render", "public": True},
    "trace": {"description": "Append-only public node trace rows.", "producer": "runtime", "public": True},
}

NODE_METADATA: dict[str, dict[str, Any]] = {
    "prompt_input": {
        "persona": "Sanitizes the prompt and optional product selector before lookup.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["payload.prompt", "payload.product"],
        "writes": ["request"],
        "shared_state": ["request.prompt", "request.product"],
        "answer_attach_rule": "Attach only prompt length and product scope.",
    },
    "semantic_parse": {
        "persona": "Builds a public semantic frame before executing the deterministic lookup.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.prompt", "request.product"],
        "writes": ["semantic_frame"],
        "shared_state": ["semantic_frame.source_catalog_matches", "semantic_frame.product"],
        "answer_attach_rule": "Attach public semantic hints and product scope only.",
    },
    "lookup_execute": {
        "persona": "Runs the registered Unit AI handle() method behind dispatcher-style unit boundaries.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["request.prompt", "semantic_frame.product"],
        "writes": ["lookup_result"],
        "shared_state": ["lookup_result.handled", "lookup_result.intent", "lookup_result.table"],
        "answer_attach_rule": "Attach deterministic CSV match results and provenance table only.",
    },
    "answer_render": {
        "persona": "Normalizes deterministic lookup output for the Agent tab result panel.",
        "prompt": {"system": "", "mode": "deterministic"},
        "reads": ["lookup_result"],
        "writes": ["answer", "table"],
        "shared_state": ["answer", "table.total"],
        "answer_attach_rule": "Attach answer and compact table; never mutate source CSV data.",
    },
}


def _safe_text(value: Any, max_len: int = 2000) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return text[: max(1, max_len)].strip()


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _history_path(unit_key: str) -> Path:
    return PATHS.data_root / "agent_unit_ai_sessions" / unit_key / "history.jsonl"


def _validate_unit_key(unit_key: str) -> str:
    key = _safe_text(unit_key, 80)
    if key not in RUNTIME_UNIT_KEYS:
        raise HTTPException(status_code=404, detail=f"{key} deterministic lookup runtime is not available")
    return key


def deterministic_lookup_graph(unit_key: str, statuses: dict[str, str] | None = None) -> dict[str, Any]:
    key = _validate_unit_key(unit_key)
    statuses = statuses or {}
    graph = {
        "layout": {"rankdir": "LR"},
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
    graph["state_design"]["lookup_result"]["description"] = f"{key} deterministic lookup output."
    execute_label = _EXECUTE_NODE_LABELS.get(key)
    if execute_label:
        for node in graph["nodes"]:
            if node.get("id") == "lookup_execute":
                node["label"] = execute_label
    return graph


def _trace_row(
    node_id: str,
    status: str,
    output: Any,
    warnings: list[str],
    started: float,
    input_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    label = next((node["label"] for node in GRAPH_NODES if node["id"] == node_id), node_id)
    return {
        "node_id": node_id,
        "label": label,
        "status": status,
        "input_summary": input_summary or {},
        "output": output,
        "warnings": list(warnings or []),
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }


def _product_from_prompt(prompt: str) -> str:
    for pattern in (
        r"\bproduct\s*[:=]?\s*([A-Za-z0-9_-]+)",
        r"\bprod\s*[:=]?\s*([A-Za-z0-9_-]+)",
        r"제품\s*[:=]?\s*([A-Za-z0-9_-]+)",
    ):
        match = re.search(pattern, prompt or "", re.IGNORECASE)
        if match:
            return _safe_text(match.group(1), 160)
    return ""


def _history_entry(
    *,
    run_id: str,
    unit_key: str,
    username: str,
    request_payload: dict[str, Any],
    result_payload: dict[str, Any],
) -> dict[str, Any]:
    table = result_payload.get("table") if isinstance(result_payload.get("table"), dict) else {}
    return {
        "history_id": run_id,
        "run_id": run_id,
        "timestamp": _now_iso(),
        "username": _safe_text(username, 80),
        "unit_ai": unit_key,
        "prompt": _safe_text(request_payload.get("prompt"), 2000),
        "natural_language": _safe_text(request_payload.get("prompt"), 2000),
        "product": _safe_text(request_payload.get("product"), 160),
        "status": _safe_text(result_payload.get("status"), 80),
        "ok": bool(result_payload.get("ok")),
        "answer": _safe_text(result_payload.get("answer"), 4000),
        "table_summary": {
            "kind": _safe_text(table.get("kind"), 80),
            "title": _safe_text(table.get("title"), 160),
            "columns": list(table.get("columns") or [])[:20] if isinstance(table.get("columns"), list) else [],
            "total": int(table.get("total") or 0) if str(table.get("total") or "0").isdigit() else 0,
        },
        "warnings": [_safe_text(item, 240) for item in (result_payload.get("warnings") or [])[:10]],
        "trace_summary": [
            {
                "node_id": _safe_text(row.get("node_id"), 80),
                "status": _safe_text(row.get("status"), 40),
                "duration_ms": row.get("duration_ms") if isinstance(row.get("duration_ms"), int) else 0,
            }
            for row in (result_payload.get("trace") or [])[:8]
            if isinstance(row, dict)
        ],
    }


def _append_history(unit_key: str, row: dict[str, Any]) -> None:
    path = _history_path(unit_key)
    jsonl_append(path, row, add_timestamp=False)
    jsonl_trim(path, 500)


def list_deterministic_lookup_history(unit_key: str, limit: int = 50, *, username: str = "") -> list[dict[str, Any]]:
    key = _validate_unit_key(unit_key)

    def _visible(row: dict[str, Any]) -> bool:
        return not username or not row.get("username") or row.get("username") == username

    rows = jsonl_read(
        _history_path(key),
        limit=max(1, min(int(limit or 50), 500)),
        filter_fn=_visible,
    )
    rows.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
    return rows[: max(1, min(int(limit or 50), 200))]


def run_deterministic_lookup_runtime(
    unit_key: str,
    payload: dict[str, Any],
    *,
    username: str = "",
    agent_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = _validate_unit_key(unit_key)
    body = deepcopy(payload or {})
    prompt = _safe_text(body.get("prompt") or body.get("natural_language"), 2000)
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")
    product = _safe_text(body.get("product"), 160) or _product_from_prompt(prompt)
    run_id = f"agent_{key}_" + uuid.uuid4().hex[:12]
    warnings: list[str] = []
    trace: list[dict[str, Any]] = []
    request_payload = {"prompt": prompt, "product": product}

    started = time.perf_counter()
    trace.append(_trace_row(
        "prompt_input",
        "success",
        request_payload,
        [],
        started,
        {"prompt_chars": len(prompt), "product": product},
    ))

    started = time.perf_counter()
    semantic_warnings: list[str] = []
    try:
        resolved = agent_semantic_service.resolve(prompt, product=product)
    except Exception as exc:
        resolved = {}
        semantic_warnings.append(f"semantic resolve failed: {type(exc).__name__}: {exc}")
    semantic_frame = {
        "natural_language": prompt,
        "product": product,
        "source_catalog_matches": list(resolved.get("source_catalog_matches") or []),
        "measurement_term_matches": list(resolved.get("measurement_term_matches") or []),
        "value_terms": list(resolved.get("value_terms") or []),
        "step_mapping": dict(resolved.get("step_mapping") or {}),
        "warnings": list(resolved.get("warnings") or []),
    }
    semantic_warnings.extend(_safe_text(item, 240) for item in semantic_frame["warnings"] if str(item or "").strip())
    trace.append(_trace_row(
        "semantic_parse",
        "warning" if semantic_warnings else "success",
        semantic_frame,
        semantic_warnings,
        started,
        {"prompt_chars": len(prompt), "product": product},
    ))
    warnings.extend(item for item in semantic_warnings if item not in warnings)

    started = time.perf_counter()
    lookup_warnings: list[str] = []
    from core.flowi_units import get_unit_ai

    unit = get_unit_ai(key)
    if unit is None:
        raise HTTPException(status_code=404, detail=f"{key} unit is not registered")
    lookup_result = unit.handle(
        prompt,
        {"product": product},
        {"agent_context": deepcopy(agent_context or {}), "username": _safe_text(username, 80)},
    )
    if lookup_result is None:
        lookup_result = {"handled": False, "answer": "", "table": {}, "unit_ai": key}
        lookup_warnings.append("No deterministic lookup match was found.")
    trace.append(_trace_row(
        "lookup_execute",
        "success" if lookup_result.get("handled") else "warning",
        lookup_result,
        lookup_warnings,
        started,
        {"unit_ai": key, "product": product},
    ))
    warnings.extend(item for item in lookup_warnings if item not in warnings)

    started = time.perf_counter()
    table = lookup_result.get("table") if isinstance(lookup_result.get("table"), dict) else {}
    answer = _safe_text(lookup_result.get("answer"), 4000)
    render_warnings: list[str] = []
    if not answer:
        answer = "매칭되는 FAB reference 결과가 없습니다."
        render_warnings.append("answer is empty")
    trace.append(_trace_row(
        "answer_render",
        "warning" if render_warnings else "success",
        {"answer": answer, "table": table},
        render_warnings,
        started,
        {"handled": bool(lookup_result.get("handled")), "rows": int(table.get("total") or 0) if table else 0},
    ))
    warnings.extend(item for item in render_warnings if item not in warnings)

    statuses = {str(row.get("node_id")): str(row.get("status") or "pending") for row in trace}
    ok = bool(lookup_result.get("handled")) and not render_warnings
    status = "success" if ok and not warnings else ("warning" if lookup_result.get("handled") else "no_match")
    result = {
        "ok": ok,
        "unit_ai": key,
        "run_id": run_id,
        "status": status,
        "prompt": prompt,
        "product": product,
        "semantic_frame": semantic_frame,
        "lookup_result": lookup_result,
        "answer": answer,
        "table": table,
        "graph": deterministic_lookup_graph(key, statuses),
        "trace": trace,
        "warnings": warnings,
    }
    try:
        _append_history(key, _history_entry(
            run_id=run_id,
            unit_key=key,
            username=username,
            request_payload=request_payload,
            result_payload=result,
        ))
    except Exception:
        pass
    return agent_feedback_penalties.annotate_result(key, result)
