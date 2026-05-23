"""routers/skills.py — Skill 카탈로그 + Miner.

엔드포인트:
  GET    /api/skills/list                        정식 Skill 목록
  GET    /api/skills/candidates                  Skill 후보 목록
  POST   /api/skills/candidates/{key}/approve    후보 → 정식 승격 (admin)
  POST   /api/skills/candidates/{key}/reject     후보 거부 (admin)
  POST   /api/skills/mine                        즉시 마이닝 실행 (admin)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core import audit, skill_miner, skills_repo
from core.auth import current_user

router = APIRouter(prefix="/api/skills", tags=["skills"])


def _require_admin(request: Request) -> dict[str, Any]:
    me = current_user(request)
    if not me or me.get("role") != "admin":
        raise HTTPException(status_code=403, detail="admin only")
    return me


class ApproveRequest(BaseModel):
    title: str | None = None


class MineRequest(BaseModel):
    days: int = 30
    window_sec: int = 300
    min_freq: int = 3
    min_users: int = 2


@router.get("/list")
def list_skills(request: Request):
    me = current_user(request)
    return {"items": skills_repo.list_skills(), "is_admin": (me or {}).get("role") == "admin"}


@router.get("/candidates")
def list_candidates(request: Request):
    me = current_user(request)
    return {"items": skills_repo.list_candidates(), "is_admin": (me or {}).get("role") == "admin"}


@router.post("/candidates/{key}/approve")
def approve(request: Request, key: str, body: ApproveRequest | None = None):
    me = _require_admin(request)
    try:
        saved = skills_repo.approve_candidate(
            key,
            by=me.get("username") or "admin",
            override_title=(body.title if body else None),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"candidate not found: {key}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    audit.record(request, action=f"skill:approve:{key}", detail=f"title={saved.get('title')}", tab="ai_hub")
    return {"ok": True, "skill": saved}


@router.post("/candidates/{key}/reject")
def reject(request: Request, key: str):
    _require_admin(request)
    ok = skills_repo.reject_candidate(key)
    if not ok:
        raise HTTPException(status_code=404, detail=f"candidate not found: {key}")
    audit.record(request, action=f"skill:reject:{key}", tab="ai_hub")
    return {"ok": True}


@router.post("/mine")
def mine(request: Request, body: MineRequest):
    _require_admin(request)
    out = skill_miner.mine(
        days=max(1, min(365, int(body.days))),
        window_sec=max(30, min(86400, int(body.window_sec))),
        min_freq=max(2, int(body.min_freq)),
        min_users=max(1, int(body.min_users)),
    )
    audit.record(
        request,
        action="skill:mine",
        detail=f"events={out['scanned_events']} candidates={out['candidate_count']}",
        tab="ai_hub",
    )
    return out
