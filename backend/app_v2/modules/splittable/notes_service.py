import datetime
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional

from .notes_repository import NotesRepository

class NoteSaveReq(BaseModel):
    scope: str                 # "wafer" | "param" | "lot" | "param_global"
    product: str = ""
    root_lot_id: str = ""
    wafer_id: str = ""
    param: str = ""            # scope == "param" / "param_global"
    text: str
    username: str = ""
    images: list[dict] = Field(default_factory=list)

class NoteDeleteReq(BaseModel):
    id: str
    username: str = ""

class NoteCommentReq(BaseModel):
    note_id: str
    text: str
    username: str = ""
    images: list[dict] = Field(default_factory=list)


def _clean_note_text(text: str) -> str:
    return (text or "").replace("\u200b", "").strip()

def _normalize_note_image(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    url = (
        raw.get("url")
        or raw.get("downloadUrl")
        or raw.get("fileUrl")
        or ((raw.get("attachment") or {}).get("downloadUrl") if isinstance(raw.get("attachment"), dict) else "")
        or ((raw.get("file") or {}).get("fileUrl") if isinstance(raw.get("file"), dict) else "")
    )
    url = str(url or "").strip().split("?", 1)[0]
    if url.startswith("api/informs/files/"):
        url = "/" + url
    elif url.startswith("files/"):
        url = "/api/informs/" + url
    if not url.startswith("/api/informs/files/"):
        return None
    filename = (
        raw.get("filename")
        or raw.get("name")
        or raw.get("displayName")
        or Path(url).name
        or "image"
    )
    try:
        size = int(raw.get("size") or raw.get("bytes") or 0)
    except Exception:
        size = 0
    return {"filename": Path(str(filename)).name or "image", "url": url, "size": max(0, size)}

def _normalize_note_images(images) -> list[dict]:
    out = []
    seen = set()
    for raw in images or []:
        item = _normalize_note_image(raw)
        if not item:
            continue
        key = item["url"]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:12]

def _normalize_note_entry(entry: dict) -> dict:
    e = dict(entry or {})
    e["text"] = _clean_note_text(str(e.get("text") or ""))
    e["images"] = _normalize_note_images(e.get("images") or [])
    comments = []
    for raw in e.get("comments") or []:
        if not isinstance(raw, dict):
            continue
        c = dict(raw)
        c["text"] = _clean_note_text(str(c.get("text") or ""))
        c["images"] = _normalize_note_images(c.get("images") or [])
        comments.append(c)
    e["comments"] = comments
    return e

def _notes_key_wafer(product: str, root_lot_id: str, wafer_id) -> str:
    return f"{product}__{root_lot_id}__W{wafer_id}"

def _notes_key_param(product: str, root_lot_id: str, wafer_id, param: str) -> str:
    return f"{product}__{root_lot_id}__W{wafer_id}__{param}"

def _notes_key_lot(product: str, root_lot_id: str) -> str:
    return f"{product}__LOT__{root_lot_id}"

def _notes_key_param_global(product: str, param: str) -> str:
    return f"{product}__PARAM__{param}"

def _notes_lot_prefix(product: str, root_lot_id: str) -> str:
    return f"{product}__{root_lot_id}__"

def _notes_product_param_prefix(product: str) -> str:
    return f"{product}__PARAM__"

def _notes_product_lot_prefix(product: str) -> str:
    return f"{product}__LOT__"


class NotesService:
    def __init__(self, repo: NotesRepository):
        self.repo = repo

    def list_notes(self, product: str, root_lot_id: str) -> list[dict]:
        entries = self.repo.load_notes()
        if product and root_lot_id:
            lot_pfx = _notes_lot_prefix(product, root_lot_id)
            lot_key = _notes_key_lot(product, root_lot_id)
            pg_pfx = _notes_product_param_prefix(product)
            def _match(e):
                k = str(e.get("key", ""))
                sc = e.get("scope")
                if sc == "wafer" and k.startswith(lot_pfx):
                    return True
                if sc == "param" and k.startswith(lot_pfx):
                    return True
                if sc == "lot" and k == lot_key:
                    return True
                if sc == "param_global" and k.startswith(pg_pfx):
                    return True
                return False
            entries = [e for e in entries if _match(e)]
        elif product:
            pg_pfx = _notes_product_param_prefix(product)
            lot_pfx = _notes_product_lot_prefix(product)
            entries = [e for e in entries
                       if str(e.get("key", "")).startswith(pg_pfx) or str(e.get("key", "")).startswith(lot_pfx)]
        
        entries = [_normalize_note_entry(e) for e in entries]
        entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
        return entries
