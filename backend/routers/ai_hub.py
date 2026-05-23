"""routers/ai_hub.py — AI Hub 통합 카탈로그 + 관리 라우터.

flow 본진의 두 추상화(Unit AI 11개 + Function-call 16개)를 한 화면에서
보고·관리할 수 있게 노출한다. 기존 dispatch/handle 로직은 손대지 않고
core/tool_registry.py 의 read 함수만 호출한다.

엔드포인트:
  GET    /api/ai-hub/tools                  통합 카탈로그
  GET    /api/ai-hub/tools/{name}           단일 도구 상세
  GET    /api/ai-hub/tools/{name}/history   최근 호출 이력
  GET    /api/ai-hub/tags                   태그 목록 (필터용)
  GET    /api/ai-hub/workflow-map           n8n/Obsidian식 운영 지도
  GET    /api/ai-hub/workflow-map/export    지도 export (n8n JSON / Obsidian Markdown)
  GET    /api/ai-hub/readiness              운영 준비도 + 개선 백로그
  POST   /api/ai-hub/tools/{name}/toggle    enabled on/off (admin)

상태 저장: data_root/tool_registry_state.json (admin_settings.json 사용 X)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core import ai_hub_board
from core import ai_hub_readiness
from core import ai_hub_workflow_map
from core import audit
from core import tool_registry
from core.auth import current_user

router = APIRouter(prefix="/api/ai-hub", tags=["ai-hub"])


class ToggleRequest(BaseModel):
    enabled: bool


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


@router.get("/workflow-map")
def workflow_map(
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    reference_limit: int = Query(default=160, ge=20, le=400),
    focus_tag: str = Query(default=""),
):
    return ai_hub_workflow_map.build_workflow_map(
        days=days,
        limit=limit,
        reference_limit=reference_limit,
        focus_tag=focus_tag,
    )


@router.get("/workflow-map/export")
def workflow_map_export(
    format: str = Query(default="n8n", pattern="^(n8n|obsidian|markdown|md|json)$"),
    days: int = Query(default=30, ge=1, le=365),
    limit: int = Query(default=40, ge=1, le=120),
    reference_limit: int = Query(default=160, ge=20, le=400),
    focus_tag: str = Query(default=""),
):
    try:
        return ai_hub_workflow_map.export_workflow_map(
            export_format=format,
            days=days,
            limit=limit,
            reference_limit=reference_limit,
            focus_tag=focus_tag,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
