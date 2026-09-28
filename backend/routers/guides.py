"""업무 탭 사용법 영상(가이드) API.

영상은 코드 배포(setup.py)·공개 저장소에 넣지 않고 운영 폴더에 둔다. 폴더 하나 = 가이드 하나:

    {FLOW_DB_ROOT}/_guides/<helpId>/guide.json        ← 제목·챕터·파일 목록
                                    /<helpId>_720p.mp4 등

`_` 로 시작하는 폴더라 파일탐색기 DB 트리에는 보이지 않는다. 찾는 순서는
``FLOW_GUIDES_DIR`` → ``{db_root}/_guides`` → ``{data_root}/guides`` 이고 처음 있는 폴더 하나만 쓴다.
<helpId> 는 frontend pageManifest 의 helpId 이며, 가이드가 있는 탭에만 상단 도움말 버튼이 뜬다.

``<video src>`` 는 X-Session-Token 헤더를 못 실으므로 media 경로는 app_v2/runtime/security.py 의
``QUERY_TOKEN_PREFIXES`` 에 등록해 ``?t=`` 토큰을 받는다. FileResponse 가 Range 를 처리해 구간 이동이 된다.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from core.auth import current_user
from core.paths import PATHS

router = APIRouter(prefix="/api/guides", tags=["guides"])

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".vtt": "text/vtt; charset=utf-8",
}
_MAX_MANIFEST_BYTES = 256 * 1024


def _candidate_roots() -> list[Path]:
    out: list[Path] = []
    env = str(os.environ.get("FLOW_GUIDES_DIR") or "").strip()
    if env:
        out.append(Path(env))
    try:
        out.append(Path(PATHS.db_root) / "_guides")
    except Exception:
        pass
    out.append(Path(PATHS.data_root) / "guides")
    return out


def guides_root() -> Path | None:
    for root in _candidate_roots():
        try:
            if root.is_dir():
                return root
        except OSError:
            continue
    return None


def _media_url(guide_id: str, name: str) -> str:
    return f"/api/guides/media/{guide_id}/{name}"


def _clean_text(value, limit: int = 400) -> str:
    return str(value or "").strip()[:limit]


def _load_guide(folder: Path) -> dict | None:
    guide_id = folder.name
    if not _ID_RE.match(guide_id):
        return None
    manifest = folder / "guide.json"
    try:
        if not manifest.is_file() or manifest.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None

    def existing(name) -> str:
        name = str(name or "").strip()
        if not name or not _FILE_RE.match(name) or Path(name).suffix.lower() not in _MEDIA_TYPES:
            return ""
        return name if (folder / name).is_file() else ""

    sources = []
    for src in raw.get("sources") or []:
        if not isinstance(src, dict):
            continue
        name = existing(src.get("file"))
        if name:
            sources.append({
                "url": _media_url(guide_id, name),
                "label": _clean_text(src.get("label") or name, 40),
                "height": int(src.get("height") or 0),
                "bytes": (folder / name).stat().st_size,
            })
    if not sources:
        return None
    chapters = []
    for ch in raw.get("chapters") or []:
        if isinstance(ch, dict):
            try:
                t = max(0.0, float(ch.get("t") or 0))
            except (TypeError, ValueError):
                continue
            chapters.append({"t": t, "title": _clean_text(ch.get("title"), 80), "desc": _clean_text(ch.get("desc"))})
    notes = []
    for note in raw.get("notes") or []:
        if isinstance(note, dict):
            notes.append({
                "title": _clean_text(note.get("title"), 80),
                "lines": [_clean_text(x) for x in (note.get("lines") or []) if str(x or "").strip()][:20],
                "code": [_clean_text(x, 300) for x in (note.get("code") or []) if str(x or "").strip()][:20],
            })
    poster = existing(raw.get("poster"))
    captions = existing(raw.get("captions"))
    try:
        duration = float(raw.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "id": guide_id,
        "title": _clean_text(raw.get("title") or guide_id, 80),
        "summary": _clean_text(raw.get("summary")),
        "duration": duration,
        "updated": _clean_text(raw.get("updated"), 40),
        "sources": sources,
        "poster": _media_url(guide_id, poster) if poster else "",
        "captions": _media_url(guide_id, captions) if captions else "",
        "chapters": chapters,
        "notes": notes,
    }


@router.get("")
def list_guides(request: Request):
    current_user(request)
    root = guides_root()
    guides: dict[str, dict] = {}
    if root is not None:
        try:
            folders = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            folders = []
        for folder in folders:
            guide = _load_guide(folder)
            if guide:
                guides[guide["id"]] = guide
    return {"guides": guides}


@router.get("/media/{guide_id}/{name}")
def guide_media(guide_id: str, name: str, request: Request):
    current_user(request)
    suffix = Path(name).suffix.lower()
    if not _ID_RE.match(guide_id) or not _FILE_RE.match(name) or suffix not in _MEDIA_TYPES:
        raise HTTPException(404, "guide media not found")
    root = guides_root()
    if root is None:
        raise HTTPException(404, "guide media not found")
    try:
        base = root.resolve()
        path = (root / guide_id / name).resolve()
        path.relative_to(base)
    except (OSError, ValueError):
        raise HTTPException(404, "guide media not found")
    if not path.is_file():
        raise HTTPException(404, "guide media not found")
    return FileResponse(
        str(path),
        media_type=_MEDIA_TYPES[suffix],
        headers={"Cache-Control": "private, max-age=3600"},
    )
