"""Authenticated PI collaboration, with server-side access and author attribution."""
from datetime import date
import mimetypes
from pathlib import Path
import re
from typing import Literal
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core import product_wiki as wiki
from core.auth import current_user, is_page_manager, user_tab_tokens
from core.paths import PATHS


def require_access(request: Request):
    user = current_user(request)
    tabs, _ = user_tab_tokens(user)
    if user.get("role") == "admin" or is_page_manager(user, "productwiki") or set(tabs) & {"productwiki", "splittable", "lotmanage"}:
        return user
    raise HTTPException(403, "제품 위키 접근 권한이 필요합니다.")


router = APIRouter(prefix="/api/product-wiki", tags=["product-wiki"], dependencies=[Depends(require_access)])

UPLOAD_DIR = PATHS.data_root / "product_wiki" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:160] or "image.png"


class Entry(BaseModel):
    id: str = Field("", max_length=64)
    kind: Literal["structure", "split", "issue", "fact", "opinion", "decision"] = "issue"
    title: str = Field(..., min_length=1, max_length=200)
    body: str = Field("", max_length=50000)
    structure: str = Field("", max_length=200)
    split: str = Field("", max_length=200)
    lot_ids: list[str] = Field(default_factory=list, max_length=100)
    purpose: str = Field("", max_length=3000)
    expected_effect: str = Field("", max_length=3000)
    observed_effect: str = Field("", max_length=3000)
    status: Literal["open", "investigating", "validated", "closed"] = "open"
    evidence: str = Field("", max_length=6000)
    occurred_on: str = Field("", max_length=10)
    related_ids: list[str] = Field(default_factory=list, max_length=100)
    source_text: str | None = Field(None, max_length=50000)


class SaveRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    entry: Entry


class ReportRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    use_ai: bool = False


class CompileRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    use_ai: bool = True


class IntakeRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    text: str = Field(..., min_length=1, max_length=40000)
    entry_id: str = Field("", max_length=64)
    title: str = Field("", max_length=200)


@router.get("/products")
def products():
    """스플릿테이블에서 잡히는 제품만 노출."""
    from core.product_order import clean_product_order, load_product_order, order_products
    names = []
    try:
        import routers.splittable as sp
        res = sp.list_products()
        for p in res.get("products") or []:
            raw = str(p.get("name") or "").strip()
            if raw.upper().startswith("ML_TABLE_"):
                raw = raw[9:].strip()
            if raw:
                names.append(raw)
    except Exception:
        pass
    if not names:
        names = ["PRODA", "PRODB"]
    order = load_product_order()
    return {"products": order_products(clean_product_order(names), product_order=order)}


@router.get("/product")
def product(product: str = Query(..., min_length=1, max_length=200)):
    try:
        return wiki.document(product)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/sources")
def sources(product: str = Query(..., min_length=1, max_length=200)):
    """Read saved LOT tables only; never infer experiments from matching text."""
    import json
    from core.paths import PATHS
    from core.product_order import canonical_product_name
    target = canonical_product_name(product).casefold()
    tables, failed = [], False
    for path in (PATHS.data_root / "lot_management" / "tables").glob("*.json"):
        try:
            row = json.loads(path.read_text("utf-8"))
            if canonical_product_name(row.get("product")).casefold() == target:
                tables.append({key: row.get(key) for key in (
                    "product", "version", "updated_at", "updated_by", "columns", "rows")})
        except (OSError, ValueError, AttributeError):
            failed = True
    return {"lot_tables": tables, "warning": "일부 랏 관리 자료를 읽지 못했습니다." if failed else ""}


@router.post("/upload")
async def upload_image(request: Request, user=Depends(require_access)):
    try:
        form = await request.form()
    except Exception as exc:
        raise HTTPException(500, f"이미지 업로드 실패: {exc}")
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "file 필드가 필요합니다.")
    filename = _safe_filename(getattr(upload, "filename", "") or "image.png")
    content_type = str(getattr(upload, "content_type", "") or "")
    data_or_coro = upload.read()
    data = await data_or_coro if hasattr(data_or_coro, "__await__") else data_or_coro
    data = bytes(data or b"")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_IMAGE_EXTS and content_type.startswith("image/"):
        ext = ".jpg" if content_type == "image/jpeg" else f".{content_type.split('/', 1)[1]}"
        filename = _safe_filename(Path(filename).stem + ext)
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(400, "PNG, JPG, GIF, WEBP 이미지만 지원합니다.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "이미지 크기는 최대 10MB입니다.")
    uid = uuid.uuid4().hex[:12]
    target_dir = UPLOAD_DIR / uid
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    target.write_bytes(data)
    url = f"/api/product-wiki/files/{uid}/{filename}"
    return {"ok": True, "filename": filename, "url": url, "size": len(data), "uploaded_by": user.get("username", "")}


@router.get("/files/{uid}/{name}")
def serve_image(uid: str, name: str, request: Request):
    if not re.fullmatch(r"[A-Za-z0-9]+", uid):
        raise HTTPException(400, "잘못된 이미지 경로입니다.")
    safe = _safe_filename(name)
    target = (UPLOAD_DIR / uid / safe).resolve()
    try:
        target.relative_to(UPLOAD_DIR.resolve())
    except Exception:
        raise HTTPException(403, "접근할 수 없는 이미지 경로입니다.")
    if not target.is_file():
        raise HTTPException(404, "이미지를 찾을 수 없습니다.")
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=mime)


@router.post("/compile")
def compile_wiki(req: CompileRequest, user=Depends(require_access)):
    try:
        return wiki.compile_product_wiki(req.product, actor=user["username"], use_ai=req.use_ai)
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"위키 컴파일 실패: {exc}")


@router.delete("/entries/{entry_id}")
def delete_entry(entry_id: str, product: str = Query(...), expected_revision: int = Query(...), user=Depends(require_access)):
    try:
        return wiki.delete_entry(product, expected_revision, entry_id, user["username"], is_page_manager(user, "productwiki"))
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/entries")
def save(req: SaveRequest, user=Depends(require_access)):
    entry = req.entry.model_dump() if hasattr(req.entry, "model_dump") else req.entry.dict()
    entry["title"] = entry["title"].strip()
    try:
        if not entry["title"]:
            raise ValueError("제목을 입력하세요.")
        if entry.get("occurred_on"):
            try:
                date.fromisoformat(entry["occurred_on"])
            except ValueError:
                raise ValueError("발생일 형식이 올바르지 않습니다.")
        if entry.get("kind") == "fact" and not entry.get("evidence", "").strip():
            raise ValueError("사실 기록에는 측정·문서 등 근거를 입력하세요.")
        if any(not s.strip() or len(s) > 200 for s in (entry.get("lot_ids") or []) + (entry.get("related_ids") or [])):
            raise ValueError("연결 ID는 1~200자여야 합니다.")
        return wiki.save_entry(req.product, req.expected_revision, entry,
                               user["username"], is_page_manager(user, "productwiki"))
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/intake")
def intake(req: IntakeRequest, user=Depends(require_access)):
    try:
        return wiki.intake_entry(req.product, req.expected_revision, req.text,
                                 user["username"], req.entry_id,
                                 is_page_manager(user, "productwiki"), req.title)
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/history")
def history(product: str = Query(..., min_length=1, max_length=200), entry_id: str = "",
            before_revision: int | None = Query(None, ge=1)):
    rows = wiki.history(product, entry_id, before_revision)
    return {"history": rows, "next_before_revision": rows[-1]["revision"] if len(rows) == 100 else None}


@router.post("/report")
def report(req: ReportRequest, user=Depends(require_access)):
    try:
        return wiki.create_report(req.product, user["username"], req.use_ai)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class StructureRow(BaseModel):
    module: str = Field(..., min_length=1, max_length=100)
    path: str = Field(..., min_length=1, max_length=504)
    step_ids: list[str] = Field(default_factory=list, max_length=200)
    description: str = Field("", max_length=3000)


class StructureTransition(BaseModel):
    order: int | None = Field(None, ge=1, le=500)
    module: str = Field(..., min_length=1, max_length=100)
    from_path: str = Field("", max_length=504)
    to_path: str = Field("", max_length=504)
    relation: str = Field("", max_length=80)
    description: str = Field("", max_length=3000)
    step_ids: list[str] = Field(default_factory=list, max_length=200)


class StructureRequest(BaseModel):
    product: str = Field(..., min_length=1, max_length=200)
    expected_revision: int = Field(..., ge=0)
    rows: list[StructureRow] = Field(default_factory=list, max_length=500)
    # Optional so an older client that only knows about structure rows cannot
    # accidentally erase administrator-authored change history.
    transitions: list[StructureTransition] | None = Field(None, max_length=500)


@router.get("/structure")
def product_structure(product: str = Query(..., min_length=1, max_length=200)):
    from core import product_wiki_structure as structure
    try:
        return structure.document(product)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/structure")
def save_product_structure(req: StructureRequest, user=Depends(require_access)):
    from core import product_wiki_structure as structure
    try:
        rows = [row.model_dump() if hasattr(row, "model_dump") else row.dict() for row in req.rows]
        transitions = None if req.transitions is None else [item.model_dump() if hasattr(item, "model_dump") else item.dict() for item in req.transitions]
        return structure.save(req.product, req.expected_revision, rows, user["username"],
                              is_page_manager(user, "productwiki"), transitions)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except wiki.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/structure/history")
def product_structure_history(product: str = Query(..., min_length=1, max_length=200)):
    from core import product_wiki_structure as structure
    try:
        return {"history": structure.history(product)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
