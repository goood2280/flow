"""routers/sql_workspace.py — FileBrowser 멀티 SQL Workspace.

여러 SQL 셀을 순차 실행해 named view 를 만들고 final SELECT 로 결과를 반환한다.
core/sql_workspace.py 가 안전 검증 + DuckDB 실행을 담당하며, 이 라우터는
인증/감사/응답 형태만 책임진다.

엔드포인트:
  POST /api/sql-workspace/run         cells 실행 → result + 셀 trace
  POST /api/sql-workspace/save-skill  cells 시퀀스를 Skill 후보로 저장
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import audit
from core.auth import current_user
from core.paths import PATHS

logger = logging.getLogger("flow.sql_workspace")

router = APIRouter(prefix="/api/sql-workspace", tags=["sql-workspace"])


class WorkspaceCell(BaseModel):
    name: str | None = None
    sql: str


class WorkspaceRunRequest(BaseModel):
    cells: list[WorkspaceCell]
    row_limit: int | None = None


class SaveSkillRequest(BaseModel):
    key: str
    title: str
    description: str | None = ""
    cells: list[WorkspaceCell]
    placeholders: dict[str, Any] | None = None
    shared: bool = False


def _require_user(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if not me:
        raise HTTPException(status_code=401, detail="auth required")
    return me


@router.post("/run")
def run_workspace(request: Request, body: WorkspaceRunRequest):
    me = _require_user(request)
    from core import sql_workspace as engine

    if not body.cells:
        raise HTTPException(status_code=400, detail="cells 가 비어 있습니다")

    cells_dict = [c.model_dump() for c in body.cells]
    t0 = time.perf_counter()
    try:
        out = engine.run_workspace(
            cells_dict,
            row_limit=int(body.row_limit or engine.DEFAULT_ROW_LIMIT),
        )
    except ValueError as e:
        audit.record(
            request,
            action="filebrowser_sql:workspace_reject",
            detail=f"cells={len(cells_dict)} reason={e}",
            tab="filebrowser",
        )
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.exception("workspace run failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")

    audit.record(
        request,
        action="filebrowser_sql:workspace_run",
        detail=f"cells={len(cells_dict)} rows={out['result']['rowcount']} elapsed_ms={out['elapsed_ms']}",
        tab="filebrowser",
    )
    return out


SKILLS_DIR = PATHS.data_root / "skills"
SKILL_CANDIDATES_DIR = SKILLS_DIR / "_candidates"


def _slugify_key(k: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(k or "")).strip("_")
    return cleaned[:80] or "untitled"


@router.post("/save-skill")
def save_skill(request: Request, body: SaveSkillRequest):
    me = _require_user(request)
    if not body.cells:
        raise HTTPException(status_code=400, detail="cells 가 비어 있습니다")
    key = _slugify_key(body.key)
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    target = SKILLS_DIR / f"{key}.json"

    if target.exists():
        raise HTTPException(status_code=409, detail=f"이미 존재하는 skill key: {key}")

    payload = {
        "key": key,
        "title": (body.title or key)[:120],
        "description": (body.description or "")[:500],
        "kind": "sql_workspace",
        "cells": [c.model_dump() for c in body.cells],
        "placeholders": body.placeholders or {},
        "owner": me.get("username") or "anonymous",
        "shared": bool(body.shared),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "run_count": 0,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    audit.record(
        request,
        action=f"skill:save:{key}",
        detail=f"title={payload['title']} cells={len(body.cells)}",
        tab="ai_hub",
    )
    return {"ok": True, "skill": payload}
