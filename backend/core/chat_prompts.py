"""core/chat_prompts.py — 관리자 고정 추천 질문 및 실제 성공 질의 저장소.

홈 데이터 챗 빈 화면의 추천 칩과 관리자 탭의 추천 질문 관리를 지원한다.
- pinned: 관리자가 의도적으로 고정(Pin)해둔 추천 질문 (홈 챗에 우선 노출)
- successful: 사용자들이 실제로 입력하여 성공적으로 결과를 얻은 질문 이력
"""
from __future__ import annotations

import datetime
import logging
import threading
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json

logger = logging.getLogger("flow.chat_prompts")

PROMPTS_FILE = PATHS.data_root / "chat_sample_prompts.json"
_LOCK = threading.Lock()

SEED_PINNED = [
    {
        "id": "pin-1",
        "prompt": "PRODA A1001 위치 조회",
        "category": "location",
        "pinned_by": "system",
        "pinned_at": "2026-09-12T00:00:00",
    },
    {
        "id": "pin-2",
        "prompt": "PRODA TEG_GATE 위치 보여줘",
        "category": "teg",
        "pinned_by": "system",
        "pinned_at": "2026-09-12T00:00:00",
    },
    {
        "id": "pin-3",
        "prompt": "prodA A1005.1 5.0 PC 스플릿 wafer 1~6 ABC 넣고 나머지는 ABB로 깔아줘",
        "category": "splittable",
        "pinned_by": "system",
        "pinned_at": "2026-09-12T00:00:00",
    },
    {
        "id": "pin-4",
        "prompt": "PRODA A1001 수율 맵 보여줘",
        "category": "yield_map",
        "pinned_by": "system",
        "pinned_at": "2026-09-12T00:00:00",
    },
    {
        "id": "pin-5",
        "prompt": "ET 트래커 이슈 목록 보여줘",
        "category": "tracker",
        "pinned_by": "system",
        "pinned_at": "2026-09-12T00:00:00",
    },
    {
        "id": "pin-6",
        "prompt": "prodA A1005.1 스플릿테이블 보여줘",
        "category": "splittable",
        "pinned_by": "system",
        "pinned_at": "2026-09-12T00:00:00",
    },
]


def _now_iso() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _read_data() -> dict[str, list[dict[str, Any]]]:
    raw = load_json(PROMPTS_FILE, None)
    if raw is None or not isinstance(raw, dict):
        data = {
            "pinned": list(SEED_PINNED),
            "successful": [],
        }
        try:
            save_json(PROMPTS_FILE, data)
        except Exception:
            logger.debug("initial chat_sample_prompts.json save failed", exc_info=True)
        return data

    pinned = raw.get("pinned")
    if not isinstance(pinned, list) or len(pinned) == 0:
        raw["pinned"] = list(SEED_PINNED)
    if not isinstance(raw.get("successful"), list):
        raw["successful"] = []
    return raw


def _write_data(data: dict[str, Any]) -> None:
    try:
        save_json(PROMPTS_FILE, data)
    except Exception as exc:
        logger.error("failed to save chat_sample_prompts.json: %s", exc)


def _enrich_item(item: dict[str, Any]) -> dict[str, Any]:
    d = dict(item)
    t = d.get("prompt") or d.get("text") or ""
    d["prompt"] = t
    d["text"] = t
    return d


def get_sample_prompts() -> dict[str, Any]:
    """Return both pinned questions and successful questions for the home chat and admin UI."""
    with _LOCK:
        data = _read_data()
        pinned = [_enrich_item(x) for x in data.get("pinned", [])]
        successful = [_enrich_item(x) for x in data.get("successful", [])]
        return {
            "ok": True,
            "pinned": pinned,
            "successful": successful,
            "all_pinned": pinned,
            "recent_success": successful,
        }


def record_success(prompt: str = "", user: str = "", category: str = "", text: str = "") -> dict[str, Any]:
    """Record a user query that succeeded, updating frequency count and timestamp."""
    actual_text = (prompt or text or "").strip()
    if len(actual_text) < 2:
        return {"ok": False, "reason": "prompt too short"}

    with _LOCK:
        data = _read_data()
        successful = data.get("successful", [])

        # Check if already present in successful list
        found = False
        for item in successful:
            item_txt = item.get("prompt") or item.get("text") or ""
            if item_txt.strip().lower() == actual_text.lower():
                item["count"] = int(item.get("count", 1)) + 1
                item["last_used"] = _now_iso()
                if user:
                    item["last_user"] = user
                if category and not item.get("category"):
                    item["category"] = category
                found = True
                break

        if not found:
            # Add to front
            entry = {
                "id": f"succ-{len(successful) + 1}-{int(datetime.datetime.now().timestamp())}",
                "prompt": actual_text,
                "text": actual_text,
                "count": 1,
                "created_at": _now_iso(),
                "last_used": _now_iso(),
                "last_user": user or "user",
                "category": category or "",
            }
            successful.insert(0, entry)

        # Retain at most 60 successful items
        data["successful"] = successful[:60]
        _write_data(data)
        return {"ok": True}


def pin_prompt(prompt: str = "", pinned: bool = True, user: str = "admin", category: str = "", text: str = "", pinned_by: str = "") -> dict[str, Any]:
    """Pin or unpin a prompt for admin curation."""
    actual_text = (prompt or text or "").strip()
    if not actual_text:
        return {"ok": False, "reason": "empty prompt"}

    with _LOCK:
        data = _read_data()
        pinned_list = data.get("pinned", [])

        if pinned:
            # Ensure not already pinned
            exists = any((p.get("prompt") or p.get("text") or "").strip().lower() == actual_text.lower() for p in pinned_list)
            if not exists:
                pinned_list.append({
                    "id": f"pin-{len(pinned_list) + 1}-{int(datetime.datetime.now().timestamp())}",
                    "prompt": actual_text,
                    "text": actual_text,
                    "category": category or "",
                    "pinned_by": user or pinned_by or "admin",
                    "pinned_at": _now_iso(),
                })
        else:
            pinned_list = [p for p in pinned_list if (p.get("prompt") or p.get("text") or "").strip().lower() != actual_text.lower()]

        data["pinned"] = pinned_list
        _write_data(data)
        return {"ok": True, "pinned": [_enrich_item(x) for x in pinned_list]}


def delete_prompt(prompt: str = "", text: str = "", item_id: str = "", kind: str = "all") -> dict[str, Any]:
    """Delete a prompt from pinned, successful, or both by text or id."""
    query_text = (prompt or text or "").strip().lower()
    qid = (item_id or "").strip()
    if not query_text and not qid:
        return {"ok": False, "reason": "empty query"}

    def _matches(p: dict[str, Any]) -> bool:
        if qid and p.get("id") == qid:
            return True
        if query_text and (p.get("prompt") or p.get("text") or "").strip().lower() == query_text:
            return True
        return False

    with _LOCK:
        data = _read_data()
        if kind in ("all", "pinned"):
            data["pinned"] = [p for p in data.get("pinned", []) if not _matches(p)]
        if kind in ("all", "successful"):
            data["successful"] = [p for p in data.get("successful", []) if not _matches(p)]

        _write_data(data)
        return {"ok": True}

