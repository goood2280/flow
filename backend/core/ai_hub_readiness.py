"""AI Hub operational readiness.

Read-only health scoring and improvement backlog derived from existing AI Hub
stores. This creates no new state and does not execute tools.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core import ai_hub_board, ai_hub_workflow_map


def build_readiness(*, username: str = "", days: int = 30) -> dict[str, Any]:
    days = max(1, min(365, int(days or 30)))
    board = ai_hub_board.build_board(username=username, days=days, limit=12)
    workflow = ai_hub_workflow_map.build_workflow_map(days=days, limit=120, reference_limit=400)
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

    catalog_score = _pct(tools_enabled, tools_total)
    grounding_score = _pct(max(0, tools_visible - tools_without_refs), tools_visible)
    queue_penalty = min(55, semantic_pending * 6 + skill_candidates * 4)
    learning_score = max(45 if semantic_pending or skill_candidates else 100, 100 - queue_penalty)
    asset_score = min(100, (skills * 18) + (workflows * 12))
    if skills == 0 and workflows == 0:
        asset_score = 35 if tools_total else 0
    score = round((catalog_score * 0.28) + (grounding_score * 0.34) + (learning_score * 0.20) + (asset_score * 0.18))

    backlog = _build_backlog(board=board, workflow=workflow, counts={
        "tools_total": tools_total,
        "tools_disabled": tools_disabled,
        "tools_without_refs": tools_without_refs,
        "semantic_pending": semantic_pending,
        "skill_candidates": skill_candidates,
        "skills": skills,
        "workflows": workflows,
    })
    checks = [
        _check("tool_catalog", "도구 카탈로그", catalog_score, f"{tools_enabled}/{tools_total} enabled"),
        _check("knowledge_grounding", "Wiki/schema grounding", grounding_score, f"{tools_without_refs} tools missing evidence"),
        _check("learning_queue", "학습/승인 큐", learning_score, f"semantic {semantic_pending}, skill {skill_candidates}"),
        _check("workflow_assets", "워크플로우/스킬 자산", asset_score, f"workflow {workflows}, skill {skills}"),
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
    for item in (lanes.get("semantic_proposals", {}).get("items") or [])[:8]:
        out.append({
            "id": "semantic_proposal:" + str(item.get("id") or item.get("title") or ""),
            "severity": "medium",
            "title": "시멘틱 제안 승인 대기",
            "target": str(item.get("title") or ""),
            "detail": str(item.get("detail") or ""),
            "action": "운영 보드 또는 Agent 시멘틱 탭에서 승인/거부",
            "route": "/api/agent/semantic/proposals",
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
        })
    if counts["workflows"] == 0:
        out.append({
            "id": "workflow_assets:none",
            "severity": "medium",
            "title": "공유 워크플로우 없음",
            "target": "workflow_templates",
            "detail": "반복 prompt를 운영 템플릿으로 고정한 항목이 없습니다.",
            "action": "Agent 질문 설계 탭에서 자주 쓰는 흐름을 shared workflow로 저장",
            "route": "/api/agent/workflows",
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
