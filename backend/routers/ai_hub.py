"""routers/ai_hub.py — AI Hub 통합 카탈로그 + 관리 라우터.

flow 본진의 두 추상화(Unit AI 11개 + Function-call 16개)를 한 화면에서
보고·관리할 수 있게 노출한다. 기존 dispatch/handle 로직은 손대지 않고
core/tool_registry.py 의 read 함수만 호출한다.

엔드포인트:
  GET    /api/ai-hub/tools                  통합 카탈로그
  GET    /api/ai-hub/tools/{name}           단일 도구 상세
  GET    /api/ai-hub/tools/{name}/history   최근 호출 이력
  GET    /api/ai-hub/tags                   태그 목록 (필터용)
  GET    /api/ai-hub/ops-snapshot           운영 스냅샷 관리 홈
  GET    /api/ai-hub/timeline               AI Hub 운영 이벤트 타임라인
  GET    /api/ai-hub/wiki-health            Agent Wiki/Knowledge Vault 운영 상태
  GET    /api/ai-hub/workflow-runbook       Agent workflow 운영 runbook
  GET    /api/ai-hub/workflow-map           n8n/Obsidian식 운영 지도
  GET    /api/ai-hub/workflow-map/export    지도 export (n8n JSON / Obsidian Markdown)
  GET    /api/ai-hub/workflow-map/export/download  지도 export 다운로드 (Obsidian ZIP / JSON)
  GET    /api/ai-hub/ops-export/download    운영 스냅샷 Obsidian vault ZIP / n8n JSON
  GET    /api/ai-hub/readiness              운영 준비도 + 개선 백로그
  GET    /api/ai-hub/deep-eval-report       Agent deep-eval 최신 리포트
  POST   /api/ai-hub/deep-eval-report/run   Agent deep-eval 최신 리포트 재생성 (admin)
  POST   /api/ai-hub/readiness/bootstrap-workflows  시작 shared workflow 템플릿 생성 (admin)
  POST   /api/ai-hub/tools/{name}/toggle    enabled on/off (admin)

상태 저장: data_root/tool_registry_state.json (admin_settings.json 사용 X)
"""
from __future__ import annotations

import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import ai_hub_board
from core import ai_hub_deep_eval
from core import ai_hub_ops_export
from core import ai_hub_ops_snapshot
from core import ai_hub_readiness
from core import ai_hub_timeline
from core import ai_hub_wiki_health
from core import ai_hub_workflow_map
from core import ai_hub_workflow_runbook
from core import audit
from core import tool_registry
from core.auth import current_user

router = APIRouter(prefix="/api/ai-hub", tags=["ai-hub"])


class ToggleRequest(BaseModel):
    enabled: bool


class DeepEvalRunRequest(BaseModel):
    cleanup_knowledge: bool = False
    min_cases: int = 80


def _require_admin(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return me


@router.get("/tools")
def list_tools(
    request: Request,
    kind: str | None = Query(default=None, description="filter by kind: unit_ai|function"),
    tag: str | None = Query(default=None, description="filter by tag"),
    enabled_only: bool = Query(default=False),
    days: int = Query(default=30, ge=1, le=365),
):
    me = current_user(request)
    items = tool_registry.list_tools(include_stats=True, days=days)
    if kind:
        items = [it for it in items if it.get("kind") == kind]
    if tag:
        items = [it for it in items if tag in (it.get("tags") or [])]
    if enabled_only:
        items = [it for it in items if it.get("enabled")]
    counts = {
        "unit_ai": sum(1 for it in items if it.get("kind") == "unit_ai"),
        "function": sum(1 for it in items if it.get("kind") == "function"),
        "enabled": sum(1 for it in items if it.get("enabled")),
        "total": len(items),
    }
    return {
        "items": items,
        "counts": counts,
        "days": days,
        "is_admin": (me or {}).get("role") == "admin",
    }


@router.get("/board")
def operations_board(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=8, ge=1, le=50),
):
    me = current_user(request)
    username = str((me or {}).get("username") or "")
    board = ai_hub_board.build_board(username=username, days=days, limit=limit)
    board["is_admin"] = (me or {}).get("role") == "admin"
    return board


@router.get("/ops-snapshot")
def ops_snapshot(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=8, ge=1, le=30),
):
    me = current_user(request)
    out = ai_hub_ops_snapshot.build_snapshot(
        username=str((me or {}).get("username") or ""),
        days=days,
        limit=limit,
    )
    out["is_admin"] = (me or {}).get("role") == "admin"
    return out


@router.get("/readiness")
def readiness(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
):
    me = current_user(request)
    out = ai_hub_readiness.build_readiness(
        username=str((me or {}).get("username") or ""),
        days=days,
    )
    out["is_admin"] = (me or {}).get("role") == "admin"
    return out


@router.get("/deep-eval-report")
def deep_eval_report(request: Request):
    me = current_user(request)
    out = ai_hub_deep_eval.load_latest_report()
    out["is_admin"] = (me or {}).get("role") == "admin"
    return out


@router.post("/deep-eval-report/run")
def deep_eval_report_run(request: Request, body: DeepEvalRunRequest | None = None):
    me = _require_admin(request)
    body = body or DeepEvalRunRequest()
    out = ai_hub_deep_eval.run_latest_report(
        cleanup_knowledge=bool(body.cleanup_knowledge),
        min_cases=int(body.min_cases or 80),
    )
    summary = out.get("summary") if isinstance(out.get("summary"), dict) else {}
    audit.record(
        request,
        action="ai_hub_deep_eval_run",
        detail=f"status={out.get('status')} passed={summary.get('passed')} failed={summary.get('failed')}",
        tab="ai_hub",
    )
    out["is_admin"] = True
    if isinstance(out.get("report"), dict):
        out["report"]["is_admin"] = True
    return out


@router.get("/timeline")
def timeline(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=30, ge=1, le=120),
    category: str = Query(default=""),
):
    me = current_user(request)
    out = ai_hub_timeline.build_timeline(days=days, limit=limit, category=category)
    out["is_admin"] = (me or {}).get("role") == "admin"
    return out


@router.get("/wiki-health")
def wiki_health(
    request: Request,
    limit: int = Query(default=12, ge=1, le=50),
):
    me = current_user(request)
    out = ai_hub_wiki_health.build_wiki_health(limit=limit)
    out["is_admin"] = (me or {}).get("role") == "admin"
    return out


@router.post("/readiness/bootstrap-workflows")
def readiness_bootstrap_workflows(request: Request):
    me = _require_admin(request)
    out = ai_hub_readiness.bootstrap_starter_workflows(by=me.get("username") or "admin")
    audit.record(
        request,
        action="ai_hub_readiness_bootstrap_workflows",
        detail=f"created={out.get('created_count')} preserved={out.get('preserved_count')}",
        tab="ai_hub",
    )
    return out


@router.get("/workflow-map")
def workflow_map(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    reference_limit: int = Query(default=160, ge=20, le=400),
    focus_tag: str = Query(default=""),
):
    me = current_user(request)
    return ai_hub_workflow_map.build_workflow_map(
        username=str((me or {}).get("username") or ""),
        days=days,
        limit=limit,
        reference_limit=reference_limit,
        focus_tag=focus_tag,
    )


@router.get("/workflow-runbook")
def workflow_runbook(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    focus_tag: str = Query(default=""),
):
    me = current_user(request)
    out = ai_hub_workflow_runbook.build_runbook(
        username=str((me or {}).get("username") or ""),
        days=days,
        limit=limit,
        focus_tag=focus_tag,
    )
    out["is_admin"] = (me or {}).get("role") == "admin"
    return out


@router.get("/workflow-map/export")
def workflow_map_export(
    request: Request,
    format: str = Query(default="n8n", pattern="^(n8n|obsidian|markdown|md|json)$"),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    reference_limit: int = Query(default=160, ge=20, le=400),
    focus_tag: str = Query(default=""),
):
    me = current_user(request)
    try:
        return ai_hub_workflow_map.export_workflow_map(
            export_format=format,
            username=str((me or {}).get("username") or ""),
            days=days,
            limit=limit,
            reference_limit=reference_limit,
            focus_tag=focus_tag,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflow-map/export/download")
def workflow_map_export_download(
    request: Request,
    format: str = Query(default="obsidian", pattern="^(obsidian|markdown|md|n8n|json)$"),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    reference_limit: int = Query(default=160, ge=20, le=400),
    focus_tag: str = Query(default=""),
):
    me = current_user(request)
    try:
        payload = ai_hub_workflow_map.export_workflow_map(
            export_format=format,
            username=str((me or {}).get("username") or ""),
            days=days,
            limit=limit,
            reference_limit=reference_limit,
            focus_tag=focus_tag,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    fmt = str(format or "").lower()
    if fmt in {"obsidian", "markdown", "md"}:
        data = ai_hub_workflow_map.export_obsidian_zip(payload)
        filename = "flow-ai-hub-workflow-map.obsidian.zip"
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    filename = str(payload.get("filename") or "flow-ai-hub-workflow-map.json")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/ops-export/download")
def ops_export_download(
    request: Request,
    format: str = Query(default="obsidian", pattern="^(obsidian|n8n|json)$"),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    reference_limit: int = Query(default=160, ge=20, le=400),
    focus_tag: str = Query(default=""),
):
    me = current_user(request)
    fmt = str(format or "obsidian").lower()
    if fmt in {"n8n", "json"}:
        payload = ai_hub_ops_export.build_n8n_export(
            username=str((me or {}).get("username") or ""),
            days=days,
            limit=limit,
            focus_tag=focus_tag,
        )
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        filename = str(payload.get("filename") or "flow-ai-hub-operations.n8n.json")
        return StreamingResponse(
            io.BytesIO(data),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    payload = ai_hub_ops_export.build_obsidian_export(
        username=str((me or {}).get("username") or ""),
        days=days,
        limit=limit,
        reference_limit=reference_limit,
        focus_tag=focus_tag,
    )
    data = ai_hub_ops_export.export_obsidian_zip(payload)
    filename = "flow-ai-hub-operations.obsidian.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/tools/{name}")
def get_tool(request: Request, name: str, days: int = Query(default=30, ge=1, le=365)):
    me = current_user(request)
    tool = tool_registry.get_tool(name, days=days)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {name}")
    return {
        "tool": tool,
        "is_admin": (me or {}).get("role") == "admin",
    }


@router.get("/tools/{name}/history")
def get_tool_history(
    name: str,
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=20, ge=1, le=200),
):
    return {
        "name": name,
        "days": days,
        "limit": limit,
        "items": tool_registry.get_history(name, days=days, limit=limit),
    }


@router.get("/tags")
def list_tags():
    return {"tags": tool_registry.all_tags()}


@router.post("/tools/{name}/toggle")
def toggle_tool(request: Request, name: str, body: ToggleRequest):
    me = _require_admin(request)
    tool = tool_registry.get_tool(name)
    if not tool:
        raise HTTPException(status_code=404, detail=f"tool not found: {name}")
    new_state = tool_registry.set_enabled(name, body.enabled, by=me.get("username") or "admin")
    audit.record(
        request,
        action=f"ai_hub_toggle:{name}",
        detail=f"enabled={bool(body.enabled)}",
        tab="ai_hub",
    )
    return {"name": name, "state": new_state, "enabled": bool(body.enabled)}
