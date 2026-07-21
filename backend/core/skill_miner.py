"""core/skill_miner.py — activity.jsonl 시퀀스 마이닝 → Skill 후보.

전제: tool 호출은 audit.record() 를 거쳐 activity.jsonl 에 누적된다.
일반 prefix:
  - "filebrowser_sql:workspace_run"   (SQL Workspace)
  - "home_agent:orchestrate"           (홈 에이전트)
  - "ai_hub_run:<name>"                (AI Hub 직접 실행)
  - "ai_hub_run:workflow:<key>"        (AI Hub workflow dry-run/execute)
  - "tool:<name>", "unit_ai:<key>"     (기타)

마이닝 룰:
  - 같은 user 의 연속 N 이벤트를 시간 윈도우(default 5분)로 묶는다
  - 도구명 시퀀스(action prefix 추출) 의 hash 를 키로 빈도 카운트
  - freq ≥ min_freq (default 3) AND distinct users ≥ min_users (default 2)
    조건을 만족하면 후보 등록

사용자 룰: admin_settings.json 절대 사용 X — 후보는 data_root/skills/_candidates/
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import skills_repo
from core.paths import PATHS

logger = logging.getLogger("flow.skill_miner")

# 도구 호출로 간주되는 prefix.
_TOOL_PREFIXES = (
    "filebrowser_sql:",
    "home_agent:",
    "ai_hub_run:",
    "tool:",
    "unit_ai:",
    "flowi_function:",
)


def _is_tool_action(action: str) -> bool:
    return any(action.startswith(p) for p in _TOOL_PREFIXES)


def _normalize_action(action: str) -> str:
    """`foo:bar/123 extra` → `foo:bar` 식으로 시퀀스 시그너처용 정규화."""
    if not action:
        return ""
    head = action.split()[0]
    # Workflow 실행은 template key 까지 보존해야 반복 검증이 특정 workflow skill 후보로 모인다.
    parts = head.split(":")
    if len(parts) >= 3 and parts[0] == "ai_hub_run" and parts[1] == "workflow":
        return ":".join(parts[:3])
    if len(parts) >= 2:
        return parts[0] + ":" + parts[1]
    return head


def _parse_ts(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _read_events(days: int) -> list[dict[str, Any]]:
    log = PATHS.activity_log
    if not log.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - max(1, days) * 86400
    out: list[dict[str, Any]] = []
    try:
        with log.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                ts = _parse_ts(row.get("timestamp"))
                if not ts:
                    continue
                if ts.timestamp() < cutoff:
                    continue
                action = str(row.get("action") or "")
                if not _is_tool_action(action):
                    continue
                row["_ts"] = ts
                row["_norm"] = _normalize_action(action)
                out.append(row)
    except Exception as e:
        logger.warning("activity read failed: %s", e)
    return out


def _group_into_sequences(events: list[dict[str, Any]], window_sec: int) -> list[list[dict[str, Any]]]:
    """user 별, 시간 window 안에 연속한 이벤트끼리 묶는다."""
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        by_user[str(e.get("username") or "")].append(e)
    seqs: list[list[dict[str, Any]]] = []
    for user, items in by_user.items():
        items.sort(key=lambda r: r["_ts"])
        current: list[dict[str, Any]] = []
        last_ts: float | None = None
        for ev in items:
            ts = ev["_ts"].timestamp()
            if last_ts is None or ts - last_ts <= window_sec:
                current.append(ev)
            else:
                if len(current) >= 2:
                    seqs.append(current)
                current = [ev]
            last_ts = ts
        if len(current) >= 2:
            seqs.append(current)
    return seqs


def _signature(seq: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(ev["_norm"] for ev in seq)


def _sig_hash(sig: tuple[str, ...]) -> str:
    raw = "→".join(sig)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _sig_to_steps(sig: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{"action": s, "args_hint": None} for s in sig]


def mine(days: int = 30, window_sec: int = 300, min_freq: int = 3, min_users: int = 2) -> dict[str, Any]:
    events = _read_events(days)
    seqs = _group_into_sequences(events, window_sec=window_sec)
    counter: Counter[tuple[str, ...]] = Counter()
    user_index: dict[tuple[str, ...], set[str]] = defaultdict(set)
    first_seen: dict[tuple[str, ...], datetime] = {}
    last_seen: dict[tuple[str, ...], datetime] = {}

    for seq in seqs:
        sig = _signature(seq)
        if len(sig) < 2:
            continue
        counter[sig] += 1
        u = str(seq[0].get("username") or "")
        if u:
            user_index[sig].add(u)
        ts0 = seq[0]["_ts"]
        ts1 = seq[-1]["_ts"]
        if sig not in first_seen or ts0 < first_seen[sig]:
            first_seen[sig] = ts0
        if sig not in last_seen or ts1 > last_seen[sig]:
            last_seen[sig] = ts1

    candidates: list[dict[str, Any]] = []
    for sig, freq in counter.most_common():
        users = sorted(user_index.get(sig) or [])
        if freq < min_freq or len(users) < min_users:
            continue
        key = "sk_" + _sig_hash(sig)
        candidate = {
            "key": key,
            "title": "·".join(s.split(":")[-1] for s in sig)[:120],
            "description": " → ".join(sig),
            "kind": "chain",
            "steps": _sig_to_steps(sig),
            "freq": freq,
            "users": users,
            "first_seen": first_seen[sig].isoformat(),
            "last_seen": last_seen[sig].isoformat(),
            "signature": list(sig),
            "mined_at": datetime.now(timezone.utc).isoformat(),
        }
        candidates.append(candidate)

    # 저장 — 기존 후보 동일 key 면 덮어씀.
    for c in candidates:
        try:
            skills_repo.save_candidate(c)
        except Exception as e:
            logger.warning("save_candidate failed for %s: %s", c["key"], e)

    return {
        "scanned_events": len(events),
        "sequence_count": len(seqs),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "window_sec": window_sec,
        "min_freq": min_freq,
        "min_users": min_users,
    }
