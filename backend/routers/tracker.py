"""routers/tracker.py v4.1.0 — Issue board + inline images in description + lot/wafer table"""
import datetime, uuid, base64, re
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from core.paths import PATHS
from core.utils import load_json, save_json

router = APIRouter(prefix="/api/tracker", tags=["tracker"])
TRACKER_DIR = PATHS.data_root / "tracker"
IMG_DIR = TRACKER_DIR / "images"
for d in (TRACKER_DIR, IMG_DIR):
    d.mkdir(parents=True, exist_ok=True)
ISSUES_FILE = TRACKER_DIR / "issues.json"
CATS_FILE = TRACKER_DIR / "categories.json"
# v8.1.5: categories can now be mixed list of str or {name, color}
DEFAULT_CATS = [
    {"name": "Analysis", "color": "#3b82f6"},
    {"name": "Monitor", "color": "#a855f7"},
    {"name": "Equipment", "color": "#f97316"},
    {"name": "Process", "color": "#10b981"},
    {"name": "Quality", "color": "#ef4444"},
    {"name": "Other", "color": "#64748b"},
]


def _load():
    return load_json(ISSUES_FILE, [])


def _hash_color(name: str) -> str:
    """Legacy HSL hash fallback for items without color (mirrors frontend catColor)."""
    if not name:
        return "#64748b"
    h = 0
    for ch in name:
        h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
        # mimic JS |0 signed 32-bit
        if h >= 0x80000000:
            h -= 0x100000000
    return f"hsl({abs(h) % 360}, 58%, 58%)"


def _normalize_cats(raw):
    """Accept list of str or {name,color}; return list of {name,color}."""
    out = []
    for item in raw or []:
        if isinstance(item, str):
            nm = item.strip()
            if nm:
                out.append({"name": nm, "color": _hash_color(nm)})
        elif isinstance(item, dict):
            nm = (item.get("name") or "").strip()
            if nm:
                out.append({"name": nm, "color": item.get("color") or _hash_color(nm)})
    return out


def _load_cats():
    """Returns list of {name, color} dicts (v8.1.5). Legacy str list auto-upgraded on read."""
    raw = load_json(CATS_FILE, DEFAULT_CATS)
    return _normalize_cats(raw)


def _cat_names():
    """Legacy shape (list of str) for code paths that still use it."""
    return [c["name"] for c in _load_cats()]


def _save(issues):
    save_json(ISSUES_FILE, issues, indent=2)


def _save_image(data_uri: str) -> str:
    """Save base64 data URI to file, return filename."""
    match = re.match(r"data:image/(\w+);base64,(.*)", data_uri, re.DOTALL)
    if not match:
        return ""
    ext = match.group(1)
    if ext == "jpeg":
        ext = "jpg"
    raw = base64.b64decode(match.group(2))
    name = f"{uuid.uuid4().hex[:12]}.{ext}"
    (IMG_DIR / name).write_bytes(raw)
    return name


def _process_description(desc: str) -> tuple:
    """Extract base64 images from description HTML, save to disk, replace with markers."""
    if not desc:
        return desc, []
    saved = []
    def replace_img(m):
        src = m.group(1)
        if src.startswith("data:image/"):
            name = _save_image(src)
            if name:
                saved.append(name)
                return f"[IMG:{name}]"
        return m.group(0)
    processed = re.sub(r'<img[^>]+src="([^"]+)"[^>]*/?>', replace_img, desc)
    return processed, saved


def _render_description(desc: str) -> str:
    """Replace [IMG:name] markers with img tags for display."""
    if not desc:
        return desc
    def replace_marker(m):
        name = m.group(1)
        return f'<img src="/api/tracker/image?name={name}" style="max-width:100%;border-radius:6px;margin:4px 0;" />'
    return re.sub(r'\[IMG:([^\]]+)\]', replace_marker, desc)


class IssueCreate(BaseModel):
    title: str
    description: str = ""
    username: str = ""
    status: str = "in_progress"
    priority: str = "normal"
    category: str = ""
    images: List[str] = []
    lots: List[dict] = []
    links: List[str] = []

class IssueUpdate(BaseModel):
    issue_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    category: Optional[str] = None
    username: str = ""

class CommentReq(BaseModel):
    issue_id: str
    username: str = ""
    text: str = ""
    lot_id: str = ""
    wafer_id: str = ""

class LotBulkReq(BaseModel):
    issue_id: str
    username: str = ""
    rows: List[dict] = []


@router.get("/categories")
def get_categories():
    """v8.1.5: Returns list of {name, color}. Legacy str list auto-upgraded."""
    return {"categories": _load_cats()}


@router.get("/categories/usage")
def category_usage():
    """Return per-category issue count + orphan (issues whose category is no longer in list)."""
    cats = _cat_names()
    issues = _load()
    counts = {c: 0 for c in cats}
    orphans = {}
    for iss in issues:
        c = iss.get("category") or ""
        if not c:
            continue
        if c in counts:
            counts[c] += 1
        else:
            orphans[c] = orphans.get(c, 0) + 1
    return {"counts": counts, "orphans": orphans, "total": len(issues)}


@router.post("/categories/save")
def save_categories(cats: list):
    """v8.1.5: accepts list of str OR list of {name, color}. Always stored as normalized {name, color}."""
    normalized = _normalize_cats(cats)
    save_json(CATS_FILE, normalized)
    return {"ok": True, "categories": normalized}


@router.get("/issues")
def list_issues(status: str = Query(""), limit: int = Query(200)):
    issues = _load()
    if status:
        issues = [i for i in issues if i.get("status") == status]
    out = []
    for iss in issues[-limit:]:
        out.append({
            "id": iss["id"], "title": iss.get("title", ""),
            "status": iss.get("status", ""), "priority": iss.get("priority", "normal"),
            "category": iss.get("category", ""),
            "username": iss.get("username", ""),
            "created": iss.get("created", iss.get("timestamp", "")),
            "closed_at": iss.get("closed_at"),
            "lot_count": len(iss.get("lots", [])),
            "comment_count": len(iss.get("comments", [])),
        })
    return {"issues": list(reversed(out))}


@router.get("/issue")
def get_issue(issue_id: str = Query(...)):
    issues = _load()
    iss = next((i for i in issues if i["id"] == issue_id), None)
    if not iss:
        raise HTTPException(404)
    result = dict(iss)
    result["description_html"] = _render_description(result.get("description", ""))
    return {"issue": result}


@router.post("/create")
def create_issue(req: IssueCreate):
    issues = _load()
    now = datetime.datetime.now()
    iid = f"ISS-{now.strftime('%y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    desc, desc_images = _process_description(req.description)
    img_names = list(desc_images)
    for img in req.images:
        if img.startswith("data:"):
            name = _save_image(img)
            if name:
                img_names.append(name)
    lots = [{**lot, "username": req.username, "added": now.isoformat()} for lot in req.lots]
    issue = {
        "id": iid, "title": req.title, "description": desc,
        "username": req.username, "status": req.status, "priority": req.priority,
        "category": req.category or "", "links": req.links or [],
        "created": now.isoformat(), "closed_at": None,
        "images": img_names, "lots": lots, "comments": [],
    }
    issues.append(issue)
    _save(issues)
    return {"ok": True, "id": iid}


@router.post("/update")
def update_issue(req: IssueUpdate):
    issues = _load()
    iss = next((i for i in issues if i["id"] == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    if req.title is not None:
        iss["title"] = req.title
    if req.description is not None:
        desc, desc_images = _process_description(req.description)
        iss["description"] = desc
        if desc_images:
            iss.setdefault("images", []).extend(desc_images)
    if req.status is not None:
        iss["status"] = req.status
        if req.status == "closed" and not iss.get("closed_at"):
            iss["closed_at"] = datetime.datetime.now().isoformat()
        elif req.status != "closed":
            iss["closed_at"] = None
    if req.priority is not None:
        iss["priority"] = req.priority
    if req.category is not None:
        iss["category"] = req.category
    _save(issues)
    return {"ok": True}


@router.post("/comment")
def add_comment(req: CommentReq):
    issues = _load()
    iss = next((i for i in issues if i["id"] == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    iss.setdefault("comments", []).append({
        "username": req.username, "text": req.text,
        "lot_id": req.lot_id, "wafer_id": req.wafer_id,
        "timestamp": datetime.datetime.now().isoformat(),
    })
    _save(issues)
    return {"ok": True}


@router.post("/lots/bulk")
def bulk_lots(req: LotBulkReq):
    issues = _load()
    iss = next((i for i in issues if i["id"] == req.issue_id), None)
    if not iss:
        raise HTTPException(404)
    now = datetime.datetime.now().isoformat()
    for row in req.rows:
        iss.setdefault("lots", []).append({**row, "username": req.username, "added": now})
    _save(issues)
    return {"ok": True, "added": len(req.rows)}


@router.get("/image")
def get_image(name: str = Query(...)):
    # v8.4.6: traversal 방어 — IMG_DIR 밖 파일 다운로드 차단, 디렉터리 컴포넌트 제거
    from pathlib import Path as _P
    safe_name = _P(name).name  # 슬래시·백슬래시·.. 제거
    fp = (IMG_DIR / safe_name).resolve()
    try:
        fp.relative_to(IMG_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid path")
    if not fp.is_file():
        raise HTTPException(404)
    # MIME 명시로 sniffing 기반 XSS 방어 (L3)
    ext = fp.suffix.lower()
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")
    return FileResponse(str(fp), media_type=media,
                        headers={"X-Content-Type-Options": "nosniff"})


@router.post("/delete")
def delete_issue(issue_id: str = Query(...)):
    issues = [i for i in _load() if i["id"] != issue_id]
    _save(issues)
    return {"ok": True}
