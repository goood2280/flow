"""AI Hub Agent workflow runbook.

Builds a table-oriented operator view from the existing workflow map. This is
derived, read-only data: no workflow templates or runtime state are modified.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import ai_hub_workflow_map


def build_runbook(
    *,
    username: str = "",
    days: int = 30,
    limit: int = 40,
    focus_tag: str = "",
    status: str = "",
    issue: str = "",
) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    limit = max(1, min(120, int(limit or 40)))
    focus_tag = str(focus_tag or "").strip()
    status = str(status or "").strip()
    issue = str(issue or "").strip()
    graph = ai_hub_workflow_map.build_workflow_map(
        username=str(username or ""),
        days=days,
        limit=max(40, limit),
        reference_limit=160,
        focus_tag=focus_tag,
    )

    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    graph_counts = graph.get("counts") if isinstance(graph.get("counts"), dict) else {}
    by_id = {str(node.get("id") or ""): node for node in nodes if isinstance(node, dict)}
    all_rows = [
        _runbook_row(node, by_id=by_id, edges=edges)
        for node in nodes
        if isinstance(node, dict) and node.get("type") == "workflow"
    ]
    rows = _filter_rows(all_rows, status=status, issue=issue)[:limit]
    next_action_queue = _next_action_queue(rows)
    counts = {
        "workflows": len(rows),
        "workflows_total": len(all_rows),
        "ready": sum(1 for row in rows if row.get("status") == "ready"),
        "attention": sum(1 for row in rows if row.get("status") == "attention"),
        "blocked": sum(1 for row in rows if row.get("status") == "blocked"),
        "checked": sum(1 for row in rows if int(row.get("run_count") or 0) > 0),
        "unchecked": sum(1 for row in rows if int(row.get("run_count") or 0) <= 0),
        "shared": sum(1 for row in rows if row.get("shared")),
        "personal": sum(1 for row in rows if not row.get("shared")),
        "next_actions": sum(len(row.get("next_actions") or []) for row in rows),
        "workflow_templates_total": _int(graph_counts.get("workflow_templates_total")) or len(rows),
    }
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "limit": limit,
        "focus_tag": focus_tag,
        "status": status,
        "issue": issue,
        "counts": counts,
        "status_options": _status_options(all_rows),
        "issue_options": _issue_options(all_rows),
        "next_action_queue": next_action_queue,
        "top_tags": graph.get("top_tags") if isinstance(graph.get("top_tags"), list) else [],
        "warnings": graph.get("warnings") if isinstance(graph.get("warnings"), list) else [],
        "actions": _runbook_actions(counts),
        "items": rows,
        "sources": {
            "workflow_map": "/api/ai-hub/workflow-map",
            "workflow_execute": "/api/agent/workflows/execute",
            "workflow_templates": "/api/agent/workflows",
        },
    }


def _filter_rows(rows: list[dict[str, Any]], *, status: str, issue: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if status and str(row.get("status") or "") != status:
            continue
        if issue and not any(isinstance(item, dict) and item.get("key") == issue for item in row.get("issues") or []):
            continue
        out.append(row)
    return out


def _status_options(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = {
        "ready": "ready",
        "attention": "attention",
        "blocked": "blocked",
    }
    return [
        {"key": key, "label": label, "count": sum(1 for row in rows if row.get("status") == key)}
        for key, label in labels.items()
    ]


def _issue_options(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        for issue in row.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            key = str(issue.get("key") or "").strip()
            if not key:
                continue
            bucket = counts.setdefault(key, {"key": key, "label": str(issue.get("label") or key), "count": 0})
            bucket["count"] = int(bucket.get("count") or 0) + 1
    return sorted(counts.values(), key=lambda row: (-int(row.get("count") or 0), str(row.get("key") or "")))


def _runbook_row(node: dict[str, Any], *, by_id: dict[str, dict[str, Any]], edges: list[Any]) -> dict[str, Any]:
    key = str(node.get("workflow_key") or "").strip()
    steps = node.get("steps") if isinstance(node.get("steps"), list) else []
    metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
    tool_names = _unique(str(step.get("unit_ai") or "").strip() for step in steps if isinstance(step, dict))
    tool_nodes = [by_id.get(f"tool:{name}") or {} for name in tool_names]
    missing_tools = [name for name, tool in zip(tool_names, tool_nodes) if "missing_tool" in (tool.get("tags") or [])]
    disabled_tools = [
        name for name, tool in zip(tool_names, tool_nodes)
        if tool and not tool.get("enabled") and name not in missing_tools
    ]
    incomplete_steps = [
        f"{int(step.get('index') or 0) + 1}"
        for step in steps
        if isinstance(step, dict) and (not str(step.get("unit_ai") or "").strip() or not str(step.get("action") or "").strip())
    ]
    evidence_node_ids = _evidence_node_ids(tool_names, edges=edges)
    run_count = _int(metrics.get("run_count"))
    warning_count = _int(metrics.get("warning_count"))
    issues = _issues(
        steps=steps,
        incomplete_steps=incomplete_steps,
        missing_tools=missing_tools,
        disabled_tools=disabled_tools,
        evidence_node_ids=evidence_node_ids,
        run_count=run_count,
        warning_count=warning_count,
    )
    next_actions = _next_actions(issues)
    status, tone = _status(issues)
    return {
        "key": key,
        "title": str(node.get("label") or key),
        "owner": str(node.get("owner") or ""),
        "shared": bool(node.get("shared")),
        "status": status,
        "tone": tone,
        "trigger_summary": _trigger_summary(str(node.get("detail") or "")),
        "step_count": len(steps),
        "steps": [_step_row(step) for step in steps],
        "tool_names": tool_names,
        "missing_tools": missing_tools,
        "disabled_tools": disabled_tools,
        "evidence_count": len(evidence_node_ids),
        "evidence_node_ids": evidence_node_ids[:20],
        "run_count": run_count,
        "warning_count": warning_count,
        "last_run": str(metrics.get("last_run") or ""),
        "last_status": str(metrics.get("last_status") or ""),
        "issues": issues,
        "next_actions": next_actions,
        "actions": _actions(key),
    }


def _step_row(step: dict[str, Any]) -> dict[str, Any]:
    fixed = step.get("fixed_slots") if isinstance(step.get("fixed_slots"), dict) else {}
    return {
        "index": _int(step.get("index")) + 1,
        "unit_ai": str(step.get("unit_ai") or ""),
        "action": str(step.get("action") or ""),
        "bind_slots": [str(v) for v in (step.get("bind_slots") if isinstance(step.get("bind_slots"), list) else [])],
        "fixed_slots": {str(k): v for k, v in fixed.items()},
    }


def _evidence_node_ids(tool_names: list[str], *, edges: list[Any]) -> list[str]:
    tool_ids = {f"tool:{name}" for name in tool_names}
    out: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("kind") != "evidence":
            continue
        if str(edge.get("from") or "") not in tool_ids:
            continue
        target = str(edge.get("to") or "")
        if target and target not in out:
            out.append(target)
    return out


def _issues(
    *,
    steps: list[Any],
    incomplete_steps: list[str],
    missing_tools: list[str],
    disabled_tools: list[str],
    evidence_node_ids: list[str],
    run_count: int,
    warning_count: int,
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not steps:
        out.append({"key": "no_steps", "label": "step 없음", "tone": "bad"})
    if incomplete_steps:
        out.append({"key": "incomplete_steps", "label": f"step 정의 누락 {len(incomplete_steps)}", "tone": "bad"})
    if missing_tools:
        out.append({"key": "missing_tools", "label": f"미등록 도구 {len(missing_tools)}", "tone": "bad"})
    if disabled_tools:
        out.append({"key": "disabled_tools", "label": f"비활성 도구 {len(disabled_tools)}", "tone": "bad"})
    if warning_count:
        out.append({"key": "run_warnings", "label": f"검증 warning {warning_count}", "tone": "warn"})
    if run_count <= 0:
        out.append({"key": "not_checked", "label": "최근 검증 없음", "tone": "warn"})
    if steps and not evidence_node_ids:
        out.append({"key": "no_evidence", "label": "Wiki/schema 근거 없음", "tone": "warn"})
    return out


_NEXT_ACTIONS = {
    "missing_tools": {
        "title": "미등록 도구 연결",
        "detail": "ToolRegistry 등록 또는 workflow step unit_ai 값을 수정",
        "route": "/api/ai-hub/workflow-map",
        "tone": "bad",
    },
    "disabled_tools": {
        "title": "비활성 도구 확인",
        "detail": "AI Hub 도구 카드에서 enabled 상태와 사용 여부 판단",
        "route": "/api/ai-hub/tools",
        "tone": "bad",
    },
    "incomplete_steps": {
        "title": "step 정의 보강",
        "detail": "unit_ai/action/bind_slots를 workflow template에 입력",
        "route": "/api/agent/workflows",
        "tone": "bad",
    },
    "no_steps": {
        "title": "workflow step 추가",
        "detail": "반복 업무를 unit_ai/action step으로 작성",
        "route": "/api/agent/workflows",
        "tone": "bad",
    },
    "run_warnings": {
        "title": "검증 warning 재확인",
        "detail": "Dry-run 결과의 missing/blocked/no_handler를 확인",
        "route": "/api/agent/workflows/execute",
        "tone": "warn",
    },
    "not_checked": {
        "title": "Dry-run 재검증",
        "detail": "Runbook row의 Dry-run을 실행해 최근 검증 상태 갱신",
        "route": "/api/agent/workflows/execute",
        "tone": "warn",
    },
    "no_evidence": {
        "title": "Wiki/schema 근거 연결",
        "detail": "도구 knowledge_refs 또는 Agent Wiki/source를 보강",
        "route": "/api/ai-hub/workflow-map",
        "tone": "warn",
    },
}


def _next_actions(issues: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for issue in issues:
        key = str(issue.get("key") or "").strip()
        if not key or key in seen:
            continue
        action = _NEXT_ACTIONS.get(key)
        if not action:
            continue
        out.append({"key": key, **action})
        seen.add(key)
    return out


def _next_action_queue(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        workflow = {
            "key": str(row.get("key") or ""),
            "title": str(row.get("title") or row.get("key") or ""),
            "status": str(row.get("status") or ""),
            "tone": str(row.get("tone") or ""),
        }
        for action in row.get("next_actions") or []:
            if not isinstance(action, dict):
                continue
            key = str(action.get("key") or "").strip()
            if not key:
                continue
            bucket = buckets.setdefault(key, {
                "key": key,
                "title": str(action.get("title") or key),
                "detail": str(action.get("detail") or ""),
                "route": str(action.get("route") or ""),
                "tone": str(action.get("tone") or "neutral"),
                "count": 0,
                "workflow_keys": [],
                "workflows": [],
                "status_counts": {},
            })
            bucket["count"] = int(bucket.get("count") or 0) + 1
            status_counts = bucket["status_counts"] if isinstance(bucket.get("status_counts"), dict) else {}
            status = workflow["status"]
            status_counts[status] = int(status_counts.get(status) or 0) + 1
            bucket["status_counts"] = status_counts
            if workflow["key"] not in bucket["workflow_keys"]:
                bucket["workflow_keys"].append(workflow["key"])
            if len(bucket["workflows"]) < 8:
                bucket["workflows"].append(workflow)
    return sorted(
        buckets.values(),
        key=lambda row: (
            _tone_rank(str(row.get("tone") or "")),
            _action_rank(str(row.get("key") or "")),
            -int(row.get("count") or 0),
            str(row.get("key") or ""),
        ),
    )


def _status(issues: list[dict[str, str]]) -> tuple[str, str]:
    tones = {str(issue.get("tone") or "") for issue in issues}
    if "bad" in tones:
        return "blocked", "bad"
    if "warn" in tones:
        return "attention", "warn"
    return "ready", "ok"


def _tone_rank(tone: str) -> int:
    return {"bad": 0, "warn": 1, "ok": 2, "neutral": 3}.get(str(tone or ""), 4)


def _action_rank(key: str) -> int:
    keys = list(_NEXT_ACTIONS)
    try:
        return keys.index(str(key or ""))
    except ValueError:
        return len(keys)


def _trigger_summary(detail: str) -> dict[str, str]:
    out = {"intent": "", "contains": "", "slots": ""}
    for line in str(detail or "").splitlines():
        if line.startswith("trigger intent="):
            out["intent"] = line.split("trigger intent=", 1)[-1].strip()
        elif line.startswith("contains="):
            out["contains"] = line.split("contains=", 1)[-1].strip()
        elif line.startswith("slots="):
            out["slots"] = line.split("slots=", 1)[-1].strip()
    return out


def _actions(key: str) -> list[dict[str, Any]]:
    if not key:
        return []
    return [{
        "id": "dry_run",
        "label": "Dry-run",
        "method": "POST",
        "endpoint": "/api/agent/workflows/execute",
        "body": {"key": key, "slots": {}, "dry_run": True},
    }]


def _runbook_actions(counts: dict[str, int]) -> list[dict[str, Any]]:
    if _int(counts.get("workflow_templates_total")) > 0:
        return []
    return [{
        "id": "bootstrap_workflows",
        "label": "시작 템플릿 생성",
        "method": "POST",
        "endpoint": "/api/ai-hub/readiness/bootstrap-workflows",
        "body": {},
        "tone": "ok",
    }]


def _unique(values) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0
