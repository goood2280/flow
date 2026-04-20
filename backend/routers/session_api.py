"""routers/session_api.py - 유저별 세션 저장/복원 API.

v8.4.6 보안 패치:
  - save/load 는 본인 세션만 접근 가능 (verify_owner).
"""
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from core.session import save_session, load_session
from core.auth import verify_owner

router = APIRouter(prefix="/api/session", tags=["session"])

class SaveReq(BaseModel):
    username: str
    last_tab: str = ""
    form_data: dict = {}

@router.post("/save")
def save(req: SaveReq, request: Request):
    verify_owner(request, req.username)
    save_session(req.username, req.dict())
    return {"ok": True}

@router.get("/load")
def load(request: Request, username: str = Query(...)):
    verify_owner(request, username)
    return load_session(username)
