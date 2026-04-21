"""routers/informs.py v8.7.0 — 모듈 인폼 시스템 (역할 뷰 + 체크 + flow 상태 + SplitTable 연동 + 이미지 첨부 + 설정형 모듈/사유 + SplitTable 자동기록).

스키마 ({data_root}/informs/informs.json):
  [{
    id, parent_id, wafer_id, lot_id, product,
    module, reason, text, author, created_at,
    checked, checked_by, checked_at,
    flow_status, status_history:[{status, actor, at, note}],
    splittable_change: {column, old_value, new_value, applied} | null
  }]

규약:
  - parent_id 가 null 이면 루트 인폼. 답글/재인폼은 parent_id 로 트리 구성.
  - 삭제는 **작성자 본인만** 가능 (다른 유저·admin 불가) — v8.7.0 정책 변경.
  - 체크·상태변경은 해당 인폼 module 을 담당하는 유저 또는 admin.
  - flow_status: received | reviewing | in_progress | completed (순서 강제는 안 함).
  - splittable_change 는 자유형 메타. FE 에서 plan 변경 요약 카드로 렌더.

엔드포인트:
  GET  /api/informs?wafer_id=...        — 특정 wafer 스레드
  GET  /api/informs/recent              — 최근 루트 (role 필터 적용)
  GET  /api/informs/wafers              — 인폼 있는 wafer 목록
  GET  /api/informs/by-lot?lot_id=...   — 해당 lot 의 모든 스레드 (root+전체뷰)
  GET  /api/informs/by-product?product= — 해당 product 인폼 목록
  GET  /api/informs/my                  — 내 모듈 범위 인폼 (담당자용)
  GET  /api/informs/products            — 인폼 기록된 product 목록
  GET  /api/informs/lots                — 인폼 기록된 lot 목록
  GET  /api/informs/modules             — 모듈 드롭다운 옵션 (constants)
  POST /api/informs                     — 생성
  POST /api/informs/delete?id=          — 삭제 (작성자 본인만)
  POST /api/informs/check?id=           — 체크 토글
  POST /api/informs/status?id=          — flow_status 변경
  POST /api/informs/splittable?id=      — SplitTable 변경요청 attach
"""
import datetime
import mimetypes
import re
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.paths import PATHS
from core.utils import load_json, save_json
from core.auth import current_user, require_admin
from core.audit import record as _audit
from routers.groups import user_modules

router = APIRouter(prefix="/api/informs", tags=["informs"])

INFORMS_DIR = PATHS.data_root / "informs"
INFORMS_DIR.mkdir(parents=True, exist_ok=True)
INFORMS_FILE = INFORMS_DIR / "informs.json"
CONFIG_FILE = INFORMS_DIR / "config.json"
UPLOADS_DIR = INFORMS_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

# Default 모듈·사유. config.json 에 저장된 값이 있으면 그것을 우선.
DEFAULT_MODULES = ["GATE", "STI", "PC", "MOL", "BEOL", "ET", "EDS", "S-D Epi", "Spacer", "Well", "기타"]
DEFAULT_REASONS = ["재측정", "장비 이상", "공정 OOS", "혐의 확인", "레시피 변경", "외관 결함", "기타"]
FLOW_STATUSES = ["received", "reviewing", "in_progress", "completed"]
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB/이미지


def _load_config() -> dict:
    data = load_json(CONFIG_FILE, {})
    if not isinstance(data, dict):
        data = {}
    mods = data.get("modules")
    reas = data.get("reasons")
    if not isinstance(mods, list) or not mods:
        mods = list(DEFAULT_MODULES)
    if not isinstance(reas, list) or not reas:
        reas = list(DEFAULT_REASONS)
    return {"modules": mods, "reasons": reas}


def _save_config(cfg: dict) -> None:
    save_json(CONFIG_FILE, cfg, indent=2)


# legacy 변수 — 다른 모듈에서 import 해도 기본값 세트로 동작.
MODULES = list(DEFAULT_MODULES)
REASONS = list(DEFAULT_REASONS)


# ── helpers ────────────────────────────────────────────────────────────
def _load() -> list:
    data = load_json(INFORMS_FILE, [])
    return data if isinstance(data, list) else []


def _save(items: list) -> None:
    save_json(INFORMS_FILE, items, indent=2)


def _new_id() -> str:
    return f"inf_{datetime.datetime.now().strftime('%y%m%d')}_{uuid.uuid4().hex[:6]}"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _find(items: list, iid: str) -> Optional[dict]:
    return next((x for x in items if x.get("id") == iid), None)


def _upgrade(entry: dict) -> dict:
    """Legacy v8.5.1 레코드에 v8.7.0 필드를 채워 넣는다 (in-place safe copy)."""
    entry.setdefault("lot_id", "")
    entry.setdefault("product", "")
    entry.setdefault("checked", False)
    entry.setdefault("checked_by", "")
    entry.setdefault("checked_at", "")
    entry.setdefault("flow_status", "received" if not entry.get("parent_id") else "")
    entry.setdefault("status_history", [])
    entry.setdefault("splittable_change", None)
    entry.setdefault("images", [])
    entry.setdefault("embed_table", None)
    entry.setdefault("auto_generated", False)
    entry.setdefault("deadline", "")  # v8.7.1: 이슈 마감일 (YYYY-MM-DD 또는 "")
    return entry


def _load_upgraded() -> list:
    items = _load()
    changed = False
    for x in items:
        before_keys = set(x.keys())
        _upgrade(x)
        if set(x.keys()) != before_keys:
            changed = True
    if changed:
        _save(items)
    return items


def _visible_to(entry: dict, username: str, role: str, my_mods: set) -> bool:
    """admin/all-rounder 전부 통과. 그 외에는 본인이 작성했거나 모듈 담당인 경우."""
    if role == "admin" or "__all__" in my_mods:
        return True
    if entry.get("author") == username:
        return True
    mod = entry.get("module") or ""
    if mod and mod in my_mods:
        return True
    return False


def _can_moderate(entry: dict, username: str, role: str, my_mods: set) -> bool:
    """체크·상태변경 권한: admin 또는 해당 module 담당자 또는 작성자."""
    if role == "admin":
        return True
    if entry.get("author") == username:
        return True
    mod = entry.get("module") or ""
    return bool(mod and mod in my_mods)


def _root_id(items: list, entry: dict) -> str:
    """entry 가 속한 루트 인폼의 id 반환."""
    cur = entry
    seen: set = set()
    while cur and cur.get("parent_id"):
        if cur["id"] in seen:
            break
        seen.add(cur["id"])
        parent = _find(items, cur.get("parent_id"))
        if not parent:
            break
        cur = parent
    return cur.get("id", "") if cur else ""


# ── Pydantic ───────────────────────────────────────────────────────────
class SplitChange(BaseModel):
    column: str = ""
    old_value: str = ""
    new_value: str = ""
    applied: bool = False


class ImageRef(BaseModel):
    filename: str
    url: str
    size: int = 0


class EmbedTable(BaseModel):
    source: str = ""          # 예: "SplitTable/PROD_A"
    columns: List[str] = []
    rows: List[List] = []
    note: str = ""


class InformCreate(BaseModel):
    wafer_id: str
    lot_id: str = ""
    product: str = ""
    module: str = ""
    reason: str = ""
    text: str = ""
    parent_id: Optional[str] = None
    splittable_change: Optional[SplitChange] = None
    images: List[ImageRef] = []
    embed_table: Optional[EmbedTable] = None
    deadline: str = ""  # v8.7.1: YYYY-MM-DD, 빈 문자열이면 없음


class ConfigReq(BaseModel):
    modules: Optional[List[str]] = None
    reasons: Optional[List[str]] = None


class StatusReq(BaseModel):
    status: str
    note: str = ""


class CheckReq(BaseModel):
    checked: bool


class DeadlineReq(BaseModel):
    deadline: str = ""  # YYYY-MM-DD 또는 "" (해제)


def _validate_deadline(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        datetime.date.fromisoformat(s[:10])
        return s[:10]
    except Exception:
        raise HTTPException(400, "deadline 포맷: YYYY-MM-DD")


# ── Endpoints ──────────────────────────────────────────────────────────
@router.get("/modules")
def list_modules():
    cfg = _load_config()
    return {
        "modules": cfg["modules"],
        "reasons": cfg["reasons"],
        "flow_statuses": FLOW_STATUSES,
    }


@router.get("/config")
def get_config():
    return _load_config()


@router.post("/config")
def save_config_endpoint(req: ConfigReq, _admin=Depends(require_admin)):
    """Admin 전용 — 모듈/사유 옵션 목록 편집."""
    cfg = _load_config()
    if req.modules is not None:
        cfg["modules"] = [m.strip() for m in req.modules if m and m.strip()]
    if req.reasons is not None:
        cfg["reasons"] = [r.strip() for r in req.reasons if r and r.strip()]
    # de-dup 유지 순서
    cfg["modules"] = list(dict.fromkeys(cfg["modules"]))
    cfg["reasons"] = list(dict.fromkeys(cfg["reasons"]))
    if not cfg["modules"]:
        cfg["modules"] = list(DEFAULT_MODULES)
    if not cfg["reasons"]:
        cfg["reasons"] = list(DEFAULT_REASONS)
    _save_config(cfg)
    return {"ok": True, "config": cfg}


# ── Image upload / serving ────────────────────────────────────────────
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename(name: str) -> str:
    name = Path(name).name  # strip dirs
    name = _SAFE_NAME_RE.sub("_", name)
    return name[-120:] or "file"


@router.post("/upload")
async def upload_image(request: Request, file: UploadFile = File(...)):
    """인폼용 이미지 업로드. 유저당 세션으로만 가능 (current_user 검증)."""
    me = current_user(request)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, f"이미지 형식만 업로드 가능합니다 ({', '.join(sorted(ALLOWED_IMAGE_EXTS))}).")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "파일이 너무 큽니다 (최대 8MB).")
    if not data:
        raise HTTPException(400, "빈 파일입니다.")

    uid = uuid.uuid4().hex[:12]
    safe = _safe_filename(file.filename or ("image" + ext))
    if not safe.lower().endswith(ext):
        safe += ext
    subdir = UPLOADS_DIR / uid
    subdir.mkdir(parents=True, exist_ok=True)
    dst = subdir / safe
    dst.write_bytes(data)

    url = f"/api/informs/files/{uid}/{safe}"
    return {"ok": True, "filename": safe, "url": url, "size": len(data),
            "uploaded_by": me["username"]}


@router.get("/files/{uid}/{name}")
def serve_image(request: Request, uid: str, name: str):
    """업로드 이미지 서빙. path traversal 차단."""
    # 인증은 전역 미들웨어가 처리하지만 방어적 검증.
    _ = current_user(request)
    if not re.fullmatch(r"[A-Za-z0-9]+", uid):
        raise HTTPException(400, "bad uid")
    safe = _safe_filename(name)
    dst = (UPLOADS_DIR / uid / safe).resolve()
    try:
        dst.relative_to(UPLOADS_DIR.resolve())
    except Exception:
        raise HTTPException(403, "path traversal")
    if not dst.is_file():
        raise HTTPException(404)
    mime, _ = mimetypes.guess_type(str(dst))
    return FileResponse(str(dst), media_type=mime or "application/octet-stream")


@router.get("")
def list_by_wafer(wafer_id: str = Query(..., min_length=1)):
    items = [x for x in _load_upgraded() if x.get("wafer_id") == wafer_id]
    items.sort(key=lambda x: x.get("created_at", ""))
    return {"informs": items}


@router.get("/recent")
def recent_roots(request: Request, limit: int = Query(50, ge=1, le=500)):
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    roots = [x for x in items if not x.get("parent_id")]
    roots = [x for x in roots if _visible_to(x, me["username"], me.get("role", "user"), my_mods)]
    roots.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": roots[:limit]}


@router.get("/wafers")
def list_wafers(request: Request, limit: int = Query(500, ge=1, le=5000)):
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    seen: dict = {}
    for x in items:
        w = x.get("wafer_id")
        if not w:
            continue
        if not _visible_to(x, me["username"], me.get("role", "user"), my_mods):
            continue
        cur = seen.get(w)
        ts = x.get("created_at", "")
        if cur is None or ts > cur.get("last", ""):
            if cur is None:
                seen[w] = {"wafer_id": w, "last": ts, "count": 0, "lot_id": x.get("lot_id", ""),
                           "product": x.get("product", "")}
            else:
                cur["last"] = ts
                if x.get("lot_id"):
                    cur["lot_id"] = x.get("lot_id")
                if x.get("product"):
                    cur["product"] = x.get("product")
        seen[w]["count"] = seen[w].get("count", 0) + 1
    arr = sorted(seen.values(), key=lambda v: v.get("last", ""), reverse=True)
    return {"wafers": arr[:limit]}


@router.get("/by-lot")
def by_lot(request: Request, lot_id: str = Query(..., min_length=1)):
    """해당 lot 과 연결된 모든 인폼(모든 wafer 걸쳐). admin/all-rounder 전체, 그 외 모듈 필터."""
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    hits = [x for x in items if x.get("lot_id") == lot_id]
    hits = [x for x in hits if _visible_to(x, me["username"], me.get("role", "user"), my_mods)]
    hits.sort(key=lambda x: x.get("created_at", ""))
    wafers = sorted({x.get("wafer_id") for x in hits if x.get("wafer_id")})
    return {"informs": hits, "wafers": wafers, "count": len(hits)}


@router.get("/by-product")
def by_product(request: Request, product: str = Query(..., min_length=1),
               limit: int = Query(500, ge=1, le=5000)):
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    hits = [x for x in items if x.get("product") == product]
    hits = [x for x in hits if _visible_to(x, me["username"], me.get("role", "user"), my_mods)]
    # 루트 우선 최근순
    roots = [x for x in hits if not x.get("parent_id")]
    roots.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": roots[:limit], "count": len(roots)}


@router.get("/my")
def my_informs(request: Request, limit: int = Query(200, ge=1, le=2000)):
    """현재 유저 모듈 범위의 인폼 루트 (담당자 대시보드)."""
    me = current_user(request)
    role = me.get("role", "user")
    my_mods = user_modules(me["username"], role)
    items = _load_upgraded()
    roots = [x for x in items if not x.get("parent_id")]
    if role == "admin" or "__all__" in my_mods:
        vis = roots
    else:
        vis = [
            x for x in roots
            if (x.get("module") in my_mods) or x.get("author") == me["username"]
        ]
    vis.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return {"informs": vis[:limit], "all_rounder": role == "admin" or "__all__" in my_mods,
            "my_modules": [] if "__all__" in my_mods else sorted(my_mods)}


@router.get("/products")
def list_products(request: Request):
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    seen: dict = {}
    for x in items:
        p = x.get("product")
        if not p:
            continue
        if not _visible_to(x, me["username"], me.get("role", "user"), my_mods):
            continue
        s = seen.setdefault(p, {"product": p, "count": 0, "last": ""})
        s["count"] += 1
        ts = x.get("created_at", "")
        if ts > s["last"]:
            s["last"] = ts
    arr = sorted(seen.values(), key=lambda v: v["last"], reverse=True)
    return {"products": arr}


@router.get("/lots")
def list_lots(request: Request):
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    seen: dict = {}
    for x in items:
        l = x.get("lot_id")
        if not l:
            continue
        if not _visible_to(x, me["username"], me.get("role", "user"), my_mods):
            continue
        s = seen.setdefault(l, {"lot_id": l, "count": 0, "last": "", "product": x.get("product", "")})
        s["count"] += 1
        ts = x.get("created_at", "")
        if ts > s["last"]:
            s["last"] = ts
        if x.get("product"):
            s["product"] = x.get("product")
    arr = sorted(seen.values(), key=lambda v: v["last"], reverse=True)
    return {"lots": arr}


@router.post("")
def create_inform(req: InformCreate, request: Request):
    me = current_user(request)
    wid = (req.wafer_id or "").strip()
    if not wid:
        raise HTTPException(400, "wafer_id required")
    items = _load_upgraded()

    # parent 검증 + 상속 (lot_id / product).
    inherit_lot = (req.lot_id or "").strip()
    inherit_product = (req.product or "").strip()
    if req.parent_id:
        parent = _find(items, req.parent_id)
        if not parent:
            raise HTTPException(404, "parent not found")
        if parent.get("wafer_id") != wid:
            raise HTTPException(400, "parent wafer mismatch")
        # 자식은 부모 lot/product 상속 (입력 없을 때)
        inherit_lot = inherit_lot or parent.get("lot_id", "")
        inherit_product = inherit_product or parent.get("product", "")

    sc = None
    if req.splittable_change and (req.splittable_change.column or req.splittable_change.new_value):
        sc = {
            "column": (req.splittable_change.column or "").strip(),
            "old_value": (req.splittable_change.old_value or "").strip(),
            "new_value": (req.splittable_change.new_value or "").strip(),
            "applied": bool(req.splittable_change.applied),
        }

    # 이미지 화이트리스트: 서버에 저장된 업로드 경로만 허용 (URL 필터링).
    imgs = []
    for im in (req.images or []):
        if not im.url or not im.url.startswith("/api/informs/files/"):
            continue
        imgs.append({
            "filename": _safe_filename(im.filename or "image"),
            "url": im.url,
            "size": max(0, int(im.size or 0)),
        })

    embed = None
    if req.embed_table and (req.embed_table.columns or req.embed_table.rows):
        cols = [str(c) for c in (req.embed_table.columns or [])][:40]
        rows = []
        for r in (req.embed_table.rows or [])[:200]:
            if isinstance(r, list):
                rows.append([("" if v is None else str(v)) for v in r[:len(cols) if cols else 40]])
        embed = {
            "source": (req.embed_table.source or "").strip()[:160],
            "columns": cols,
            "rows": rows,
            "note": (req.embed_table.note or "").strip()[:500],
        }

    now = _now()
    is_root = not req.parent_id
    entry = {
        "id": _new_id(),
        "parent_id": req.parent_id or None,
        "wafer_id": wid,
        "lot_id": inherit_lot,
        "product": inherit_product,
        "module": (req.module or "").strip(),
        "reason": (req.reason or "").strip(),
        "text": (req.text or "").strip(),
        "author": me["username"],
        "created_at": now,
        "checked": False,
        "checked_by": "",
        "checked_at": "",
        "flow_status": "received" if is_root else "",
        "status_history": (
            [{"status": "received", "actor": me["username"], "at": now, "note": "created"}]
            if is_root else []
        ),
        "splittable_change": sc,
        "images": imgs,
        "embed_table": embed,
        "auto_generated": False,
        "deadline": _validate_deadline(req.deadline) if is_root else "",
    }
    items.append(entry)
    _save(items)
    _audit(request, "inform:reply" if req.parent_id else "inform:create",
           detail=f"wafer={wid} module={entry['module']} lot={inherit_lot}", tab="inform")
    return {"ok": True, "inform": entry}


# ── Auto-log helper (다른 라우터가 import) ─────────────────────────────
def auto_log_splittable_change(author: str, product: str, lot_id: str,
                               cell_key: str, old_value, new_value, action: str = "set") -> None:
    """SplitTable plan 변경이 일어나면 해당 lot 에 자동 인폼 루트를 남긴다.

    - wafer_id 가 없으면 lot_id 를 placeholder 로 사용 (스레드는 lot 뷰에서 묶여 보임).
    - module 은 cell_key prefix 로 추정 (KNOB/MASK/FAB → 기타). 추후 룰 확장.
    - auto_generated=True 로 표시 → FE 에서 시스템 발행 카드로 렌더.
    """
    try:
        items = _load_upgraded()
        col = cell_key.split("|")[-1] if "|" in cell_key else cell_key
        upper = col.upper()
        if upper.startswith("MASK_"):
            mod = "MASK"
        elif upper.startswith("FAB_"):
            mod = "FAB"
        elif upper.startswith("KNOB_") or "_" not in upper:
            mod = "KNOB"
        else:
            mod = ""
        now = _now()
        text = f"[SplitTable 자동기록] {action} · {col} · {old_value!r} → {new_value!r}"
        entry = {
            "id": _new_id(),
            "parent_id": None,
            "wafer_id": lot_id or product or "auto",
            "lot_id": lot_id or "",
            "product": product or "",
            "module": mod,
            "reason": "레시피 변경",
            "text": text,
            "author": author or "system",
            "created_at": now,
            "checked": False, "checked_by": "", "checked_at": "",
            "flow_status": "received",
            "status_history": [{"status": "received", "actor": author or "system",
                                "at": now, "note": "auto from SplitTable"}],
            "splittable_change": {
                "column": col,
                "old_value": ("" if old_value is None else str(old_value)),
                "new_value": ("" if new_value is None else str(new_value)),
                "applied": (action == "set"),
            },
            "images": [],
            "embed_table": None,
            "auto_generated": True,
        }
        items.append(entry)
        _save(items)
    except Exception:
        # 자동기록 실패로 인해 plan 저장까지 실패시키면 안 됨.
        pass


@router.post("/delete")
def delete_inform(request: Request, id: str = Query(...)):
    """작성자 본인만 삭제. 자식 있으면 무결성 차단."""
    me = current_user(request)
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if target.get("author") != me["username"]:
        raise HTTPException(403, "작성자 본인만 삭제 가능합니다.")
    has_child = any(x.get("parent_id") == id for x in items)
    if has_child:
        raise HTTPException(400, "답글이 달린 글은 삭제할 수 없습니다.")
    items = [x for x in items if x.get("id") != id]
    _save(items)
    _audit(request, "inform:delete", detail=f"id={id}", tab="inform")
    return {"ok": True}


@router.post("/check")
def check_inform(req: CheckReq, request: Request, id: str = Query(...)):
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403, "모듈 담당자만 체크할 수 있습니다.")
    target["checked"] = bool(req.checked)
    target["checked_by"] = me["username"] if req.checked else ""
    target["checked_at"] = _now() if req.checked else ""
    _save(items)
    _audit(request, "inform:check", detail=f"id={id} checked={target['checked']}", tab="inform")
    return {"ok": True, "inform": target}


@router.post("/status")
def set_status(req: StatusReq, request: Request, id: str = Query(...)):
    st = (req.status or "").strip()
    if st not in FLOW_STATUSES:
        raise HTTPException(400, f"invalid status; must be one of {FLOW_STATUSES}")
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if target.get("parent_id"):
        raise HTTPException(400, "status 는 루트 인폼에만 적용됩니다.")
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403, "모듈 담당자만 상태를 변경할 수 있습니다.")
    if target.get("flow_status") == st:
        return {"ok": True, "inform": target}
    target["flow_status"] = st
    hist = target.get("status_history") or []
    hist.append({"status": st, "actor": me["username"], "at": _now(),
                 "note": (req.note or "").strip()})
    target["status_history"] = hist
    _save(items)
    _audit(request, "inform:status", detail=f"id={id} status={st}", tab="inform")
    return {"ok": True, "inform": target}


@router.post("/deadline")
def set_deadline(req: DeadlineReq, request: Request, id: str = Query(...)):
    """루트 인폼의 마감일을 설정/해제. 작성자/모듈 담당자/admin 만 가능."""
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if target.get("parent_id"):
        raise HTTPException(400, "deadline 은 루트 인폼에만 설정 가능합니다.")
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403, "작성자/모듈 담당자/관리자만 변경 가능합니다.")
    dl = _validate_deadline(req.deadline)
    target["deadline"] = dl
    _save(items)
    _audit(request, "inform:deadline", detail=f"id={id} deadline={dl or '(clear)'}", tab="inform")
    return {"ok": True, "inform": target}


@router.post("/splittable")
def attach_splittable(req: SplitChange, request: Request, id: str = Query(...)):
    """해당 인폼에 SplitTable 변경요청 메타 attach (작성자/담당자/admin)."""
    me = current_user(request)
    my_mods = user_modules(me["username"], me.get("role", "user"))
    items = _load_upgraded()
    target = _find(items, id)
    if not target:
        raise HTTPException(404)
    if not _can_moderate(target, me["username"], me.get("role", "user"), my_mods):
        raise HTTPException(403)
    target["splittable_change"] = {
        "column": (req.column or "").strip(),
        "old_value": (req.old_value or "").strip(),
        "new_value": (req.new_value or "").strip(),
        "applied": bool(req.applied),
    }
    _save(items)
    return {"ok": True, "inform": target}
