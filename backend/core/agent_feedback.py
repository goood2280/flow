"""core/agent_feedback.py — 홈 에이전트 feedback + alias.

사용자가 홈 에이전트 trace 의 결과 카드에서 thumbs-up/down 또는 "이 도구 썼어야 함"
교정 입력을 누르면 본 모듈이 jsonl 에 append 한다. orchestrator 는 alias dict
를 먼저 매칭한 뒤 휴리스틱/LLM planner 로 떨어진다.

저장소:
  - feedback: `data_root/agent_feedback.jsonl` (append-only)
  - alias: `data_root/home_agent_aliases.json` (dict, atomic write)

설계:
  - feedback 적재는 분석 용. 자동 학습은 phase 7 후속에서 한다.
  - alias 는 사용자 prompt pattern → tool name 매핑. orchestrator 가
    `_pick_tools` 보다 먼저 알리아스 매치 → 단일 step plan 으로 변환.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS

logger = logging.getLogger("flow.agent_feedback")

FEEDBACK_FILE = PATHS.data_root / "agent_feedback.jsonl"
ALIAS_FILE = PATHS.data_root / "home_agent_aliases.json"


def _ensure_dirs() -> None:
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Feedback append/list ────────────────────────────────────────────────────
def append_feedback(
    *,
    prompt: str,
    rating: str,  # "up" | "down" | "correction"
    suggested_tool: str = "",
    note: str = "",
    trace_summary: list[dict[str, Any]] | None = None,
    actor: str = "",
) -> dict[str, Any]:
    _ensure_dirs()
    row = {
        "id": f"fb_{uuid.uuid4().hex[:10]}",
        "ts": _now_iso(),
        "actor": actor or "",
        "rating": str(rating or "down")[:20],
        "prompt": str(prompt or "")[:1000],
        "suggested_tool": str(suggested_tool or "")[:80],
        "note": str(note or "")[:1000],
        "trace_summary": [
            {"tool": tr.get("tool"), "ok": tr.get("ok"), "ms": tr.get("ms")}
            for tr in (trace_summary or [])[:10]
        ],
    }
    with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return row


def list_feedback(*, days: int = 30, limit: int = 200) -> list[dict[str, Any]]:
    if not FEEDBACK_FILE.is_file():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    out: list[dict[str, Any]] = []
    for line in reversed(FEEDBACK_FILE.read_text("utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        try:
            ts = datetime.fromisoformat(str(row.get("ts") or "").replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < cutoff:
                continue
        except Exception:
            pass
        out.append(row)
        if len(out) >= max(1, min(limit, 2000)):
            break
    return out


# ── Alias dict ──────────────────────────────────────────────────────────────
def _load_aliases() -> dict[str, Any]:
    if not ALIAS_FILE.is_file():
        return {"entries": []}
    try:
        data = json.loads(ALIAS_FILE.read_text("utf-8"))
        if isinstance(data, dict):
            entries = data.get("entries")
            if isinstance(entries, list):
                return data
        return {"entries": []}
    except Exception as exc:
        logger.warning("alias load failed: %s", exc)
        return {"entries": []}


def _save_aliases(data: dict[str, Any]) -> None:
    _ensure_dirs()
    tmp = ALIAS_FILE.with_suffix(ALIAS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(ALIAS_FILE)


def list_aliases() -> list[dict[str, Any]]:
    data = _load_aliases()
    entries = list(data.get("entries") or [])
    entries.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return entries


def set_alias(*, pattern: str, tool: str, actor: str = "", note: str = "") -> dict[str, Any]:
    pattern = (pattern or "").strip()
    tool = (tool or "").strip()
    if not pattern or not tool:
        raise ValueError("pattern and tool required")
    data = _load_aliases()
    entries = list(data.get("entries") or [])
    entry = {
        "id": f"alias_{uuid.uuid4().hex[:8]}",
        "pattern": pattern[:400],
        "tool": tool[:80],
        "actor": actor or "",
        "note": note[:200] if note else "",
        "created_at": _now_iso(),
    }
    # 동일 pattern 이 이미 있으면 교체.
    entries = [e for e in entries if str(e.get("pattern") or "").lower() != pattern.lower()]
    entries.append(entry)
    data["entries"] = entries
    _save_aliases(data)
    return entry


def delete_alias(alias_id: str) -> bool:
    data = _load_aliases()
    entries = list(data.get("entries") or [])
    before = len(entries)
    entries = [e for e in entries if str(e.get("id") or "") != str(alias_id)]
    if len(entries) == before:
        return False
    data["entries"] = entries
    _save_aliases(data)
    return True


def match_alias(prompt: str) -> dict[str, Any] | None:
    """prompt 와 alias pattern 매칭. case-insensitive substring 우선, 없으면 regex 시도."""
    text = (prompt or "").strip()
    if not text:
        return None
    text_l = text.lower()
    entries = list_aliases()
    for entry in entries:
        pat = str(entry.get("pattern") or "")
        if not pat:
            continue
        pat_l = pat.lower()
        if pat_l in text_l:
            return entry
        # regex fallback (alias 가 정규식처럼 보이면 시도)
        if any(ch in pat for ch in (".*", "\\b", "[", "(", "?", "+")):
            try:
                if re.search(pat, text, flags=re.IGNORECASE):
                    return entry
            except re.error:
                continue
    return None
