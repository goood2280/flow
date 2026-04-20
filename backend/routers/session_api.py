"""routers/session_api.py - 유저별 세션 저장/복원 API"""
from fastapi import APIRouter, Query
from pydantic import BaseModel
from core.session import save_session, load_session

router = APIRouter(prefix="/api/session", tags=["session"])

class SaveReq(BaseModel):
    username: str
    last_tab: str = ""
    form_data: dict = {}

@router.post("/save")
def save(req: SaveReq):
    save_session(req.username, req.dict())
    return {"ok": True}

@router.get("/load")
def load(username: str = Query(...)):
    return load_session(username)
