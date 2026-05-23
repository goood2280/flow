"""AI Hub operational readiness.

Read-only health scoring and improvement backlog derived from existing AI Hub
stores. This creates no new state and does not execute tools.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import ai_hub_board, ai_hub_workflow_map, flowi_workflow_templates as wf_templates


STARTER_WORKFLOWS: list[dict[str, Any]] = [
    {
        "key": "ops_lot_step_review",
        "title": "운영 LOT 현재 step 확인",
        "trigger": {
            "intent_in": ["filebrowser_ai_sql", "semantic_inspection", "general_orchestration"],
            "prompt_contains": ["lot", "step"],
            "slots_required": ["product", "root_lot_ids"],
        },
        "steps": [
            {"unit_ai": "filebrowser", "action": "current_step", "bind_slots": ["product", "root_lot_ids"]},
            {"unit_ai": "tracker", "action": "lookup", "bind_slots": ["product", "root_lot_ids"]},
        ],
        "shared": True,
    },
    {
        "key": "ops_knob_lotwf_review",
        "title": "KNOB 기반 lot_wf 영향 확인",
        "trigger": {
            "intent_in": ["knob_analysis", "filebrowser_ai_sql"],
            "prompt_contains": ["knob"],
            "slots_required": ["product", "knobs"],
        },
        "steps": [
            {"unit_ai": "splittable", "action": "knob_impact", "bind_slots": ["product", "knobs"]},
            {"unit_ai": "filebrowser", "action": "query", "bind_slots": ["product", "knobs"]},
        ],
        "shared": True,
    },
    {
        "key": "ops_inform_draft_review",
        "title": "Inform 초안 전 검토",
        "trigger": {
            "intent_in": ["inform_draft"],
            "prompt_contains": ["인폼"],
            "slots_required": ["product", "root_lot_ids", "module"],
        },
        "steps": [
            {"unit_ai": "filebrowser", "action": "current_step", "bind_slots": ["product", "root_lot_ids"]},
            {"unit_ai": "inform", "action": "create_draft", "bind_slots": ["product", "root_lot_ids", "module"]},
        ],
        "shared": True,
    },
]


def build_readiness(*, username: str = "", days: int = 30) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    board = ai_hub_board.build_board(username=username, days=days, limit=12)
    workflow = ai_hub_workflow_map.build_workflow_map(
        username=username,
        days=days,
        limit=120,
        reference_limit=400,
    )
    board_counts = board.get("counts") if isinstance(board.get("counts"), dict) else {}
    map_counts = workflow.get("counts") if isinstance(workflow.get("counts"), dict) else {}

    tools_total = int(board_counts.get("tools_total") or map_counts.get("tools_total") or 0)
    tools_enabled = int(board_counts.get("tools_enabled") or 0)
    tools_disabled = int(board_counts.get("tools_disabled") or map_counts.get("tools_disabled_visible") or 0)
    tools_without_refs = int(map_counts.get("tools_without_refs_visible") or 0)
    tools_visible = int(map_counts.get("tools_visible") or tools_total or 0)
    semantic_pending = int(board_counts.get("semantic_proposals_pending") or 0)
    skill_candidates = int(board_counts.get("skill_candidates") or 0)
    skills = int(board_counts.get("skills") or 0)
    workflows = int(board_counts.get("workflows") or 0)
    workflow_validation = _workflow_validation_summary(workflow, fallback_total=workflows)

    catalog_score = _pct(tools_enabled, tools_total)
    grounding_score = _pct(max(0, tools_visible - tools_without_refs), tools_visible)
    queue_penalty = min(55, semantic_pending * 6 + skill_candidates * 4)
    learning_score = max(45 if semantic_pending or skill_candidates else 100, 100 - queue_penalty)
    asset_score = min(100, (skills * 18) + (workflows * 12))
    if skills == 0 and workflows == 0:
        asset_score = 35 if tools_total else 0
    validation_score = _pct(
        max(0, workflow_validation["checked"] - workflow_validation["warnings"]),
        workflow_validation["total"],
    )
    score = round(
        (catalog_score * 0.24)
        + (grounding_score * 0.30)
        + (learning_score * 0.18)
        + (asset_score * 0.16)
        + (validation_score * 0.12)
    )

    backlog = _build_backlog(board=board, workflow=workflow, counts={
        "tools_total": tools_total,
        "tools_disabled": tools_disabled,
        "tools_without_refs": tools_without_refs,
        "semantic_pending": semantic_pending,
        "skill_candidates": skill_candidates,
        "skills": skills,
        "workflows": workflows,
        "workflow_validation_total": workflow_validation["total"],
        "workflow_validation_checked": workflow_validation["checked"],
        "workflow_validation_unverified": workflow_validation["unverified"],
        "workflow_validation_warnings": workflow_validation["warnings"],
    })
    checks = [
        _check("tool_catalog", "도구 카탈로그", catalog_score, f"{tools_enabled}/{tools_total} enabled"),
        _check("knowledge_grounding", "Wiki/schema grounding", grounding_score, f"{tools_without_refs} tools missing evidence"),
        _check("learning_queue", "학습/승인 큐", learning_score, f"semantic {semantic_pending}, skill {skill_candidates}"),
        _check("workflow_assets", "워크플로우/스킬 자산", asset_score, f"workflow {workflows}, skill {skills}"),
        _check(
            "workflow_validation",
            "워크플로우 검증",
            validation_score,
            f"{workflow_validation['checked']}/{workflow_validation['total']} checked, {workflow_validation['warnings']} warning",
        ),
    ]
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": days,
        "score": score,
        "level": _level(score),
        "checks": checks,
        "counts": {
            "tools_total": tools_total,
            "tools_enabled": tools_enabled,
            "tools_disabled": tools_disabled,
            "tools_without_refs": tools_without_refs,
            "semantic_proposals_pending": semantic_pending,
            "skill_candidates": skill_candidates,
            "skills": skills,
            "workflows": workflows,
            "workflow_validation_total": workflow_validation["total"],
            "workflow_validation_checked": workflow_validation["checked"],
            "workflow_validation_unverified": workflow_validation["unverified"],
            "workflow_validation_warnings": workflow_validation["warnings"],
            "backlog": len(backlog),
        },
        "backlog": backlog,
        "sources": {
            "board": "/api/ai-hub/board",
            "workflow_map": "/api/ai-hub/workflow-map",
        },
    }


def _build_backlog(*, board: dict[str, Any], workflow: dict[str, Any], counts: dict[str, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lanes = {str(lane.get("id") or ""): lane for lane in (board.get("lanes") or []) if isinstance(lane, dict)}
    for item in (lanes.get("disabled_tools", {}).get("items") or [])[:8]:
        out.append({
            "id": "disabled_tool:" + str(item.get("id") or ""),
            "severity": "high",
            "title": "비활성 도구 확인",
            "target": str(item.get("title") or item.get("id") or ""),
            "detail": str(item.get("detail") or "도구가 비활성 상태입니다."),
            "action": "AI Hub 도구 카드 또는 운영 보드에서 활성화 여부를 결정",
            "route": "/api/ai-hub/tools",
            "actions": _disabled_tool_actions(item),
        })
    missing_items: list[str] = []
    for warning in workflow.get("warnings") or []:
        if isinstance(warning, dict) and warning.get("key") == "missing_evidence":
            missing_items = [str(v) for v in (warning.get("items") or []) if str(v).strip()]
            break
    for name in missing_items[:12]:
        out.append({
            "id": "missing_evidence:" + name,
            "severity": "medium",
            "title": "Wiki/schema 근거 보강",
            "target": name,
            "detail": "도구는 등록되어 있지만 knowledge_refs가 비어 있습니다.",
            "action": "feature doc, schema relation, wiki_doc_id, required args 중 하나 이상 연결",
            "route": "/api/ai-hub/workflow-map",
        })
    workflow_warning_specs = {
        "workflow_missing_tools": ("medium", "워크플로우 도구 참조 확인", "template step이 ToolRegistry에 없는 unit_ai를 참조합니다."),
        "workflow_empty_templates": ("medium", "워크플로우 step 추가", "template은 있지만 실행 step이 비어 있습니다."),
        "workflow_incomplete_steps": ("medium", "워크플로우 step 정의 보강", "template step의 unit_ai 또는 action이 비어 있습니다."),
    }
    for warning in workflow.get("warnings") or []:
        if not isinstance(warning, dict):
            continue
        key = str(warning.get("key") or "")
        spec = workflow_warning_specs.get(key)
        if not spec:
            continue
        severity, title, detail = spec
        for target in [str(v) for v in (warning.get("items") or []) if str(v).strip()][:8]:
            out.append({
                "id": f"{key}:{target}",
                "severity": severity,
                "title": title,
                "target": target,
                "detail": detail,
                "action": "Agent workflow template에서 step/action/도구 키를 정리",
                "route": "/api/agent/workflows",
            })
    for item in (lanes.get("semantic_proposals", {}).get("items") or [])[:8]:
        out.append({
            "id": "semantic_proposal:" + str(item.get("id") or item.get("title") or ""),
            "severity": "medium",
            "title": "시멘틱 제안 승인 대기",
            "target": str(item.get("title") or ""),
            "detail": str(item.get("detail") or ""),
            "action": "운영 보드 또는 Agent 시멘틱 탭에서 승인/거부",
            "route": "/api/agent/semantic/proposals",
            "actions": _semantic_actions(item),
        })
    for item in (lanes.get("skill_candidates", {}).get("items") or [])[:8]:
        out.append({
            "id": "skill_candidate:" + str(item.get("id") or ""),
            "severity": "low",
            "title": "스킬 후보 검토",
            "target": str(item.get("title") or item.get("id") or ""),
            "detail": str(item.get("meta") or ""),
            "action": "반복성이 맞으면 정식 스킬로 승인",
            "route": "/api/skills/candidates",
            "actions": _skill_actions(item),
        })
    out.extend(_workflow_validation_backlog(workflow))
    if counts["workflows"] == 0:
        out.append({
            "id": "workflow_assets:none",
            "severity": "medium",
            "title": "공유 워크플로우 없음",
            "target": "workflow_templates",
            "detail": "반복 prompt를 운영 템플릿으로 고정한 항목이 없습니다.",
            "action": "Agent 질문 설계 탭에서 자주 쓰는 흐름을 shared workflow로 저장",
            "route": "/api/agent/workflows",
            "actions": [{
                "id": "bootstrap_starters",
                "label": "시작 템플릿 생성",
                "tone": "ok",
                "method": "POST",
                "endpoint": "/api/ai-hub/readiness/bootstrap-workflows",
                "body": {},
                "confirm": True,
            }],
        })
    if counts["skills"] == 0:
        out.append({
            "id": "skills:none",
            "severity": "low",
            "title": "정식 스킬 없음",
            "target": "skills",
            "detail": "마이닝된 반복 작업을 승인한 정식 스킬이 없습니다.",
            "action": "AI Hub 스킬 패널에서 후보를 검토하거나 SQL 작업대에서 저장",
            "route": "/api/skills/list",
        })
    severity_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda row: (severity_order.get(str(row.get("severity")), 9), str(row.get("title") or ""), str(row.get("target") or "")))[:40]


def _workflow_validation_summary(workflow: dict[str, Any], *, fallback_total: int = 0) -> dict[str, int]:
    nodes = [
        node for node in (workflow.get("nodes") or [])
        if isinstance(node, dict) and node.get("type") == "workflow"
    ]
    total = len(nodes) or max(0, int(fallback_total or 0))
    checked = 0
    warnings = 0
    for node in nodes:
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        run_count = int(metrics.get("run_count") or 0)
        warning_count = int(metrics.get("warning_count") or 0)
        if run_count > 0:
            checked += 1
        if warning_count > 0:
            warnings += 1
    return {
        "total": total,
        "checked": checked,
        "unverified": max(0, total - checked),
        "warnings": warnings,
    }


def _workflow_validation_backlog(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for node in (workflow.get("nodes") or []):
        if not isinstance(node, dict) or node.get("type") != "workflow":
            continue
        key = str(node.get("workflow_key") or node.get("id") or "").replace("workflow:", "")
        title = str(node.get("label") or key)
        metrics = node.get("metrics") if isinstance(node.get("metrics"), dict) else {}
        run_count = int(metrics.get("run_count") or 0)
        warning_count = int(metrics.get("warning_count") or 0)
        if run_count <= 0:
            out.append({
                "id": f"workflow_unverified:{key}",
                "severity": "medium",
                "title": "워크플로우 검증 필요",
                "target": title,
                "detail": "최근 기간에 dry-run 또는 execute 검증 이력이 없습니다.",
                "action": "AI Hub 워크플로우 지도에서 Dry-run을 실행해 guardrail/step 상태를 확인",
                "route": "/api/ai-hub/workflow-map",
            })
        elif warning_count > 0:
            out.append({
                "id": f"workflow_validation_warning:{key}",
                "severity": "medium",
                "title": "워크플로우 검증 경고",
                "target": title,
                "detail": f"최근 검증에서 warning 성격 step 상태가 {warning_count}회 기록되었습니다.",
                "action": "Agent workflow template과 step action을 확인한 뒤 다시 Dry-run",
                "route": "/api/ai-hub/workflow-map",
            })
    return out[:16]


def bootstrap_starter_workflows(*, by: str = "system") -> dict[str, Any]:
    created: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    for template in STARTER_WORKFLOWS:
        key = str(template.get("key") or "")
        existing = wf_templates.get_template(key)
        if existing:
            preserved.append({
                "key": key,
                "title": str(existing.get("title") or template.get("title") or key),
                "shared": bool(existing.get("shared")),
            })
            continue
        saved = wf_templates.save_template(template, by=by, is_admin=True)
        created.append({
            "key": str(saved.get("key") or key),
            "title": str(saved.get("title") or template.get("title") or key),
            "shared": bool(saved.get("shared")),
        })
    return {
        "ok": True,
        "created": created,
        "preserved": preserved,
        "created_count": len(created),
        "preserved_count": len(preserved),
    }


def _disabled_tool_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = _copy_actions(item)
    if actions:
        return actions
    name = str(item.get("id") or "").strip()
    if not name:
        return []
    return [{
        "id": "enable",
        "label": "활성화",
        "tone": "ok",
        "method": "POST",
        "endpoint": f"/api/ai-hub/tools/{name}/toggle",
        "body": {"enabled": True},
    }]


def _semantic_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = _copy_actions(item)
    if actions:
        return actions
    proposal_id = str(item.get("id") or "").strip()
    if not proposal_id:
        return []
    return [
        {
            "id": "approve",
            "label": "승인",
            "tone": "ok",
            "method": "POST",
            "endpoint": "/api/agent/semantic/proposals/decide",
            "body": {"id": proposal_id, "status": "approved"},
        },
        {
            "id": "reject",
            "label": "거부",
            "tone": "bad",
            "method": "POST",
            "endpoint": "/api/agent/semantic/proposals/decide",
            "body": {"id": proposal_id, "status": "rejected"},
            "confirm": True,
        },
    ]


def _skill_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = _copy_actions(item)
    if actions:
        return actions
    key = str(item.get("id") or "").strip()
    if not key:
        return []
    return [
        {
            "id": "approve",
            "label": "승인",
            "tone": "ok",
            "method": "POST",
            "endpoint": f"/api/skills/candidates/{key}/approve",
            "body": {"title": str(item.get("title") or key)},
        },
        {
            "id": "reject",
            "label": "거부",
            "tone": "bad",
            "method": "POST",
            "endpoint": f"/api/skills/candidates/{key}/reject",
            "body": {},
            "confirm": True,
        },
    ]


def _copy_actions(item: dict[str, Any]) -> list[dict[str, Any]]:
    actions = item.get("actions")
    if not isinstance(actions, list):
        return []
    out: list[dict[str, Any]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        endpoint = str(action.get("endpoint") or "")
        method = str(action.get("method") or "")
        if not endpoint or method.upper() != "POST":
            continue
        out.append({
            "id": str(action.get("id") or ""),
            "label": str(action.get("label") or action.get("id") or ""),
            "tone": str(action.get("tone") or "neutral"),
            "method": "POST",
            "endpoint": endpoint,
            "body": action.get("body") if isinstance(action.get("body"), dict) else {},
            "confirm": bool(action.get("confirm")),
        })
    return out


def _pct(numer: int, denom: int) -> int:
    if denom <= 0:
        return 0
    return max(0, min(100, round((numer / denom) * 100)))


def _check(key: str, label: str, score: int, detail: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "score": max(0, min(100, int(score))),
        "tone": _tone(int(score)),
        "detail": detail,
    }


def _tone(score: int) -> str:
    if score >= 85:
        return "ok"
    if score >= 65:
        return "warn"
    return "bad"


def _level(score: int) -> str:
    if score >= 90:
        return "operational"
    if score >= 75:
        return "managed"
    if score >= 55:
        return "needs_attention"
    return "bootstrap"
