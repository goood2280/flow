"""routers/watchlist.py — 관심랏(Watchlist) API 엔드포인트."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.auth import current_user
from core import watchlist as wl

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistToggleReq(BaseModel):
    lot_id: str = Field(..., description="대상 LOT 번호")
    watched: bool | None = Field(None, description="설정할 관심 상태 (None이면 토글)")


@router.get("/lots")
def list_watchlist_lots(request: Request):
    """현재 사용자가 등록한 관심랏 목록을 반환합니다."""
    me = current_user(request)
    username = me.get("username", "")
    lots = wl.get_user_watchlist(username)
    return {
        "ok": True,
        "username": username,
        "lots": lots,
        "total": len(lots),
    }


@router.post("/lots/toggle")
def toggle_watchlist_lot(req: WatchlistToggleReq, request: Request):
    """관심랏 등록/해제를 토글하거나 지정된 상태로 설정합니다."""
    me = current_user(request)
    username = me.get("username", "")
    lot_id = str(req.lot_id or "").strip().upper()
    if not lot_id:
        raise HTTPException(400, "lot_id is required")

    new_state = wl.toggle_watchlist_lot(username, lot_id, req.watched)
    all_lots = wl.get_user_watchlist(username)
    return {
        "ok": True,
        "username": username,
        "lot_id": lot_id,
        "watched": new_state,
        "lots": all_lots,
    }
