"""routers/informs.py v8.5.1 — Wafer-level inform log (thread w/ replies).

스키마 ({data_root}/informs/informs.json):
  [{id, parent_id, wafer_id, module, reason, text, author, created_at}]

- parent_id 가 null 이면 루트 인폼 (새 건).
- parent_id 가 있으면 해당 루트의 댓글/재인폼 — 스레드 depth cap 없음 (FE 에서 시각 cap).
- reason 은 FE 드롭다운 값이지만 백엔드는 자유문자열 허용.
- module 도 마찬가지. 예) GATE/STI/PC/MOL/BEOL/ET/EDS/...
- 삭제: author 또는 admin. 자식 인폼 없는 leaf 만 삭제 (데이터 무결성).

엔드포인트:
  GET /api/informs?wafer_id=...         — 해당 wafer 의 전체 스레드.
  GET /api/informs/recent?limit=50      — 최근 N 루트 인폼 (overview).
  POST /api/informs                     — 생성.
  POST /api/informs/delete?id=...       — 삭제.
"""
import datetime
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user

router = APIRouter(prefix="/api/informs", tags=["informs"])

INFORMS_DIR = PATHS.data_root / "informs"
INFORMS_DIR.mkdir(parents=True, exist_ok=True)
INFORMS_FILE = INFORMS_DIR / "informs.json"


def _load() -> list:
    data = load_json(INFORMS_FILE, [])
    return data if isinstance(data, list) else []


def _save(items: list) -> None:
    save_json(INFORMS_FILE, items, indent=2)


def _new_id() -> str:
    return f"inf_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


class InformCreate(BaseModel):
    wafer_id: str
    module: str = ""
    reason: str = ""
    text: str = ""
    parent_id: Optional[str] = None


@router.get("")
def list_by_wafer(wafer_id: str = Query(..., min_length=1)):
    """한 wafer 의 전체 스레드. 시간순 정렬."""
    items = [x for x in _load() if x.get("wafer_id") == wafer_id]
    items.sort(key=lambda x: x.get("created_at", ""))
    return {"informs": items}


@router.get("/recent")
def recent_roots(limit: int = Query(50, ge=1, le=500)):
    """최근 루트 인폼 (대시보드용)."""
    items = [x for x in _load() if not x.get("parent_id")]
    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": items[:limit]}


@router.get("/wafers")
def list_wafers(limit: int = Query(200, ge=1, le=2000)):
    """인폼 기록이 있는 wafer_id 목록 (최근 순)."""
    items = _load()
    seen = {}
    for x in items:
        w = x.get("wafer_id")
        if not w:
            continue
        cur = seen.get(w)
        ts = x.get("created_at", "")
        if cur is None or ts > cur["last"]:
            seen[w] = {"wafer_id": w, "last": ts}
        # 카운트
        seen[w]["count"] = seen[w].get("count", 0) + 1
    arr = sorted(seen.values(), key=lambda v: v["last"], reverse=True)
    return {"wafers": arr[:limit]}


@router.post("")
def create_inform(req: InformCreate, request: Request):
    me = current_user(request)
    wid = (req.wafer_id or "").strip()
    if not wid:
        raise HTTPException(400, "wafer_id required")
    items = _load()
    # parent 유효성 체크
    if req.parent_id:
        parent = next((x for x in items if x.get("id") == req.parent_id), None)
        if not parent:
            raise HTTPException(404, "parent not found")
        if parent.get("wafer_id") != wid:
            raise HTTPException(400, "parent wafer mismatch")
    entry = {
        "id": _new_id(),
        "parent_id": req.parent_id or None,
        "wafer_id": wid,
        "module": (req.module or "").strip(),
        "reason": (req.reason or "").strip(),
        "text": (req.text or "").strip(),
        "author": me["username"],
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    items.append(entry)
    _save(items)
    return {"ok": True, "inform": entry}


@router.post("/delete")
def delete_inform(request: Request, id: str = Query(...)):
    me = current_user(request)
    items = _load()
    target = next((x for x in items if x.get("id") == id), None)
    if not target:
        raise HTTPException(404)
    if me.get("role") != "admin" and target.get("author") != me["username"]:
        raise HTTPException(403, "Only author or admin can delete")
    # 자식 있으면 삭제 금지 (data integrity)
    has_child = any(x.get("parent_id") == id for x in items)
    if has_child:
        raise HTTPException(400, "Has replies — remove them first")
    items = [x for x in items if x.get("id") != id]
    _save(items)
    return {"ok": True}
