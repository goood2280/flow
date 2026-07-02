"""core/flowi_fewshots.py — human-in-the-loop 용어/매핑 학습 저장소.

Flow-i가 결정적 조회(step_lookup 등)로 답을 못 찾으면 사용자에게 방법을 묻고,
사용자가 "기억해: <용어>는 <답>" 형태로 가르쳐준 매핑을 여기 저장한다.
이후 같은 용어가 질문에 등장하면 조회보다 먼저 학습된 답을 쓴다.
피드백(싫어요 + 교정 코멘트)도 같은 저장소로 들어와 방법을 교정한다.

저장: data_root/flowi_fewshots.json — 모든 유저 공유 (가르친 사람 기록).
"""
from __future__ import annotations

import re
import threading
from datetime import datetime, timezone
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

FEWSHOT_FILE = PATHS.data_root / "flowi_fewshots.json"
_LOCK = threading.Lock()
_MAX_ENTRIES = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    data = load_json(FEWSHOT_FILE, {"entries": []}) or {"entries": []}
    if not isinstance(data.get("entries"), list):
        data["entries"] = []
    return data


def _save(data: dict[str, Any]) -> None:
    entries = data.get("entries") or []
    if len(entries) > _MAX_ENTRIES:
        entries.sort(key=lambda e: (int(e.get("uses") or 0), e.get("updated_at") or ""))
        data["entries"] = entries[len(entries) - _MAX_ENTRIES:]
    save_json(FEWSHOT_FILE, data, indent=2)


def _norm_term(term: str) -> str:
    return str(term or "").strip().upper()


def teach(term: str, answer: str, *, by: str = "", source: str = "teach") -> dict[str, Any] | None:
    """용어→답 매핑을 저장/갱신한다. source: teach(직접 지시) | feedback(교정 코멘트)."""
    term_n = _norm_term(term)
    answer_c = str(answer or "").strip()[:1000]
    if not term_n or not answer_c or len(term_n) > 120:
        return None
    with _LOCK:
        data = _load()
        for entry in data["entries"]:
            if _norm_term(entry.get("term")) == term_n:
                entry["answer"] = answer_c
                entry["taught_by"] = by or entry.get("taught_by") or ""
                entry["source"] = source
                entry["updated_at"] = _now()
                _save(data)
                return dict(entry)
        entry = {
            "term": term_n,
            "answer": answer_c,
            "taught_by": by or "",
            "source": source,
            "created_at": _now(),
            "updated_at": _now(),
            "uses": 0,
        }
        data["entries"].append(entry)
        _save(data)
        return dict(entry)


def forget(term: str) -> bool:
    term_n = _norm_term(term)
    if not term_n:
        return False
    with _LOCK:
        data = _load()
        before = len(data["entries"])
        data["entries"] = [e for e in data["entries"] if _norm_term(e.get("term")) != term_n]
        if len(data["entries"]) == before:
            return False
        _save(data)
        return True


def lookup(term: str) -> dict[str, Any] | None:
    term_n = _norm_term(term)
    if not term_n:
        return None
    with _LOCK:
        data = _load()
        for entry in data["entries"]:
            if _norm_term(entry.get("term")) == term_n:
                entry["uses"] = int(entry.get("uses") or 0) + 1
                entry["last_used_at"] = _now()
                _save(data)
                return dict(entry)
    return None


def match_in_text(text: str) -> dict[str, Any] | None:
    """text 안에 학습된 용어가 경계 기준으로 등장하면 그 매핑을 반환 (긴 용어 우선)."""
    up = str(text or "").upper()
    if not up.strip():
        return None
    with _LOCK:
        data = _load()
        entries = sorted(data["entries"], key=lambda e: -len(_norm_term(e.get("term"))))
        for entry in entries:
            term_n = _norm_term(entry.get("term"))
            if not term_n:
                continue
            if re.search(r"(?<![A-Z0-9_])" + re.escape(term_n) + r"(?![A-Z0-9_])", up):
                entry["uses"] = int(entry.get("uses") or 0) + 1
                entry["last_used_at"] = _now()
                _save(data)
                return dict(entry)
    return None


def list_entries() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(e) for e in _load()["entries"]]
