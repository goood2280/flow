"""Admin-only domain knowledge editing and read-only AI drafts."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from core.auth import require_admin
from core import domain_knowledge as knowledge, llm_adapter
from core.paths import PATHS
import re
import uuid

router = APIRouter(prefix="/api/admin/domain-knowledge", tags=["admin-domain-knowledge"])


class DocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(max_length=knowledge.MAX_BODY)
    editing_guidelines: str = Field(min_length=1, max_length=knowledge.MAX_GUIDELINES)
    base_version: int = Field(ge=0)


class PreviewRequest(DocumentRequest):
    instruction: str = Field(min_length=1, max_length=20000)
    section_heading: str = Field(default="", max_length=300)


def _response(doc):
    return {**doc, "llm_available": llm_adapter.is_available()}


@router.get("")
def read(user=Depends(require_admin)):
    return _response(knowledge.read_document())


@router.put("")
def save(req: DocumentRequest, user=Depends(require_admin)):
    try:
        return _response(knowledge.save_document(**req.model_dump(), actor=user["username"]))
    except knowledge.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/preview")
def preview(req: PreviewRequest, user=Depends(require_admin)):
    try:
        return knowledge.preview(**req.model_dump())
    except knowledge.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except knowledge.Unavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/history")
def history(user=Depends(require_admin)):
    return {"items": knowledge.history()}


@router.get("/history/{version}")
def revision(version: int, user=Depends(require_admin)):
    try:
        return _response(knowledge.read_document(version))
    except KeyError as exc:
        raise HTTPException(404, "해당 이력이 없습니다.") from exc


@router.get("/references")
def references(user=Depends(require_admin)):
    return knowledge.references()


@router.post("/assets")
async def upload_asset(request: Request, user=Depends(require_admin)):
    from io import BytesIO
    from PIL import Image, UnidentifiedImageError
    async with request.form(max_files=1, max_fields=2, max_part_size=8_000_000) as form:
        upload = form.get("file")
        if not hasattr(upload, "read"):
            raise HTTPException(400, "이미지 파일을 선택해 주세요.")
        data = await upload.read(8_000_001)
        if len(data) > 8_000_000:
            raise HTTPException(413, "이미지는 최대 8MB입니다.")
        try:
            with Image.open(BytesIO(data)) as img:
                if img.format not in {"PNG", "JPEG", "WEBP", "GIF"} or img.width * img.height > 25_000_000:
                    raise ValueError()
                img.verify()
            output = BytesIO()
            with Image.open(BytesIO(data)) as img:
                img.convert("RGBA").save(output, format="PNG")
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise HTTPException(400, "유효한 PNG/JPG/WEBP/GIF 이미지(2,500만 화소 이하)가 필요합니다.") from exc
        name = uuid.uuid4().hex + ".png"
        folder = PATHS.data_root / "knowledge" / "assets"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(output.getvalue())
        return {"name": str(upload.filename or "구조 이미지"), "url": f"/api/admin/domain-knowledge/assets/{name}"}


@router.get("/assets/{name}")
def asset(name: str, user=Depends(require_admin)):
    if not re.fullmatch(r"[a-f0-9]{32}\.png", name):
        raise HTTPException(404, "이미지가 없습니다.")
    path = PATHS.data_root / "knowledge" / "assets" / name
    if not path.is_file():
        raise HTTPException(404, "이미지가 없습니다.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})
