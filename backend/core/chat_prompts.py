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
from uuid import uuid4

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


def _needs_input(response):
    from core.flowi_turn import _status
    return (response.get("routing_trace") or {}).get("status") == "needs_input" or _status(response) == "needs_input"


def completed_origin(messages, result):
    """Resolve the first user question in a completed clarification chain."""
    from core.flowi_turn import _status
    tool = result.get("tool") or {}
    if _status(result) != "completed" or not tool or result.get("error"):
        return None
    if result.get("batch") and result["batch"].get("completed") != result["batch"].get("total"):
        return None
    index = len(messages) - 1
    if index < 0 or messages[index].get("role") != "user":
        return None
    import re
    from core import data_chat_split, data_chat_report
    text = messages[index].get("content", "").strip()
    if (data_chat_split.CANCEL.fullmatch(text) or data_chat_report.CANCEL.fullmatch(text)
            or re.fullmatch(r"취소\s+[0-9a-f]{32}[.!\s]*|cancel", text, re.I)):
        return None
    answers = []
    while index >= 2 and messages[index - 1].get("role") == "assistant":
        previous = messages[index - 1].get("response") or {}
        if not _needs_input(previous):
            break
        # Deferred batch questions have not been executed by a single answer.
        if any(q.get("status") == "deferred" for q in previous.get("questions", [])):
            return None
        if messages[index - 2].get("role") != "user":
            break
        answers.append(messages[index].get("content", ""))
        index -= 2
    prompt = str(messages[index].get("content") or "").strip()
    return {"prompt": prompt, "answers": answers, "message_id": messages[index].get("id", "")} if prompt else None


def _repair_success_origins(data, owner):
    """Once per owner, repair old answer-fragment records using their saved chats."""
    if not owner or owner in data.get("origin_migrated_owners", []):
        return
    from core import chat_conversations
    replacements, independent, incomplete, origin_counts = {}, set(), set(), {}
    for conversation in chat_conversations.list_conversations(owner):
        try:
            messages = chat_conversations.read(owner, conversation["id"])["messages"]
        except (OSError, ValueError):
            continue
        for index, message in enumerate(messages):
            if message.get("role") != "assistant":
                continue
            if index and messages[index - 1].get("role") == "user" and _needs_input(message.get("response") or {}):
                incomplete.add(str(messages[index - 1].get("content") or "").strip().casefold())
            origin = completed_origin(messages[:index], message.get("response") or {})
            if not origin:
                continue
            key = origin["prompt"].casefold()
            independent.add(key)
            origin_counts[key] = origin_counts.get(key, 0) + 1
            for answer in origin["answers"]:
                replacements.setdefault(answer.strip().casefold(), set()).add(origin["prompt"])
    merged, retained, repaired = {}, [], {}
    for item in data.get("successful", []):
        if item.get("owner") != owner:
            retained.append(item)
            continue
        item = dict(item)
        text = str(item.get("prompt") or item.get("text") or "").strip()
        if text.casefold() in incomplete and text.casefold() not in independent and text.casefold() not in replacements:
            continue
        if text.casefold() in replacements and text.casefold() not in independent:
            for original in sorted(replacements[text.casefold()]):
                repaired[original.casefold()] = {**item, "id": f"succ-{uuid4()}", "prompt": original,
                                                "text": original, "count": origin_counts[original.casefold()]}
            continue
        key = text.casefold()
        if key in merged:
            merged[key]["count"] = int(merged[key].get("count", 1)) + int(item.get("count", 1))
        else:
            merged[key] = item
            retained.append(item)
    for key, item in repaired.items():
        if key not in merged:
            retained.append(item)
        else:
            merged[key]["count"] = origin_counts[key]
    data["successful"] = retained
    data.setdefault("origin_migrated_owners", []).append(owner)
    _write_data(data)


def get_sample_prompts(user: str = "") -> dict[str, Any]:
    """Return global pinned questions and only this user's successful questions."""
    owner = str(user or "").strip()
    with _LOCK:
        data = _read_data()
        _repair_success_origins(data, owner)
        pinned = [_enrich_item(x) for x in data.get("pinned", [])]
        successful = [
            _enrich_item(x) for x in data.get("successful", [])
            if owner and str(x.get("owner") or "").strip() == owner
        ]
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
    owner = str(user or "").strip()
    if len(actual_text) < 2:
        return {"ok": False, "reason": "prompt too short"}
    if not owner:
        return {"ok": False, "reason": "user required"}

    with _LOCK:
        data = _read_data()
        successful = data.get("successful", [])

        # A repeated prompt is deduplicated only within its owner. Legacy global
        # entries have no trustworthy owner and are intentionally not adopted.
        found = False
        for item in successful:
            item_txt = item.get("prompt") or item.get("text") or ""
            if (str(item.get("owner") or "").strip() == owner
                    and item_txt.strip().lower() == actual_text.lower()):
                item["count"] = int(item.get("count", 1)) + 1
                item["last_used"] = _now_iso()
                if category and not item.get("category"):
                    item["category"] = category
                found = True
                break

        if not found:
            # Add to front
            entry = {
                "id": f"succ-{uuid4()}",
                "prompt": actual_text,
                "text": actual_text,
                "count": 1,
                "created_at": _now_iso(),
                "last_used": _now_iso(),
                "owner": owner,
                "category": category or "",
            }
            successful.insert(0, entry)

        # Check for auto-skillification (20+ usages across users or prompt count)
        total_prompt_count = sum(
            int(x.get("count", 1)) for x in successful
            if (x.get("prompt") or x.get("text") or "").strip().lower() == actual_text.lower()
        )
        already_auto_skilled = any(
            x.get("auto_skilled") for x in successful
            if (x.get("prompt") or x.get("text") or "").strip().lower() == actual_text.lower()
        )
        if total_prompt_count >= 20 and not already_auto_skilled:
            try:
                from core import flowi_personalization
                created = flowi_personalization.auto_create_skill_from_prompt(actual_text, category or "")
                if created:
                    for x in successful:
                        if (x.get("prompt") or x.get("text") or "").strip().lower() == actual_text.lower():
                            x["auto_skilled"] = True
                            x["auto_skill_id"] = created.get("id")
            except Exception as exc:
                logger.warning("auto-skillification failed: %s", type(exc).__name__)

        # Retain at most 60 prompts for this owner without deleting another
        # owner's private history or rewriting unowned legacy records.
        owner_seen = 0
        retained = []
        for item in successful:
            if str(item.get("owner") or "").strip() == owner:
                owner_seen += 1
                if owner_seen > 60:
                    continue
            retained.append(item)
        data["successful"] = retained
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
