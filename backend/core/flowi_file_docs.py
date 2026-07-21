"""core/flowi_file_docs.py — Files 단일 파일 설명문 카탈로그.

사용자/관리자가 파일별 설명("step_matching.csv는 step_id와 function_step 매핑")을
등록하면, Flow-i가 질문과 설명을 대조해 어느 파일을 검색해야 할지 고르고
그 파일 안에서 용어를 찾아 답한다. 설명이 없거나 매칭이 약하면 human-in-the-loop
로 설명 등록이나 few-shot 티칭을 요청한다.

저장: data_root/flowi_file_docs.json — 모든 유저 공유, Admin 페이지에서 관리.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

FILE_DOCS_FILE = PATHS.data_root / "flowi_file_docs.json"
_LOCK = threading.Lock()
_MAX_ENTRIES = 500
_FILENAME_RE = re.compile(r"^[\w\-. ()\[\]가-힣]{1,120}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    data = load_json(FILE_DOCS_FILE, {"files": {}}) or {"files": {}}
    if not isinstance(data.get("files"), dict):
        data["files"] = {}
    return data


def _save(data: dict[str, Any]) -> None:
    files = data.get("files") or {}
    if len(files) > _MAX_ENTRIES:
        ordered = sorted(files.items(), key=lambda kv: kv[1].get("updated_at") or "")
        data["files"] = dict(ordered[len(ordered) - _MAX_ENTRIES:])
    save_json(FILE_DOCS_FILE, data, indent=2)


def _norm_file(name: str) -> str:
    return str(name or "").strip().lower()


def set_doc(filename: str, description: str, *, by: str = "") -> dict[str, Any] | None:
    fname = str(filename or "").strip()
    desc = str(description or "").strip()[:800]
    if not fname or not desc or not _FILENAME_RE.match(fname):
        return None
    with _LOCK:
        data = _load()
        entry = data["files"].get(_norm_file(fname)) or {}
        entry.update({
            "file": fname,
            "description": desc,
            "updated_by": by or entry.get("updated_by") or "",
            "updated_at": _now(),
        })
        entry.setdefault("created_at", _now())
        data["files"][_norm_file(fname)] = entry
        _save(data)
        return dict(entry)


def delete_doc(filename: str) -> bool:
    key = _norm_file(filename)
    if not key:
        return False
    with _LOCK:
        data = _load()
        if key not in data["files"]:
            return False
        data["files"].pop(key, None)
        _save(data)
        return True


def get_doc(filename: str) -> dict[str, Any] | None:
    with _LOCK:
        entry = _load()["files"].get(_norm_file(filename))
        return dict(entry) if entry else None


def list_docs() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(v) for v in _load()["files"].values()]


def match_files(question: str, *, limit: int = 4) -> list[dict[str, Any]]:
    """질문 토큰과 파일명/설명의 겹침으로 검색 대상 파일을 고른다 (점수 내림차순)."""
    text = str(question or "").strip().lower()
    if not text:
        return []
    q_tokens = {t for t in re.split(r"[\s,/()\[\]{}:;'\"?.!]+", text) if len(t) >= 2}
    # PPID_08_0 같은 복합 토큰은 하위 토큰(ppid, 08...)으로도 설명과 대조한다.
    for tok in list(q_tokens):
        q_tokens |= {t for t in re.split(r"[_\-.]+", tok) if len(t) >= 2}
    if not q_tokens:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in list_docs():
        fname = str(entry.get("file") or "").lower()
        desc = str(entry.get("description") or "").lower()
        d_tokens = {t for t in re.split(r"[\s,/()\[\]{}:;'\"?.!_\-]+", f"{fname} {desc}") if len(t) >= 2}
        if not d_tokens:
            continue
        overlap = len(d_tokens & q_tokens)
        if fname and fname in text:
            overlap += 3  # 파일명 직접 언급은 강한 신호
        if overlap <= 0:
            continue
        scored.append((float(overlap), entry))
    scored.sort(key=lambda x: -x[0])
    return [{**e, "score": s} for s, e in scored[:limit]]
