"""core/skills_repo.py — Skill 단순 저장/조회.

Skill 정의 (JSON 파일 1개 = Skill 1개):
  {
    "key":          "monthly_lot_avg_et",
    "title":        "월별 Lot 평균 ET",
    "description":  "...",
    "kind":         "sql_workspace" | "chain",
    "cells":        [...],         # sql_workspace 일 때
    "steps":        [...],         # chain 일 때 (tool_name + args)
    "owner":        "username",
    "shared":       true,
    "created_at":   "...",
    "updated_at":   "...",
    "run_count":    0
  }

저장 위치:
  data_root/skills/<key>.json                정식 skill
  data_root/skills/_candidates/<hash>.json    Skill Miner 가 만든 후보
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import PATHS

logger = logging.getLogger("flow.skills_repo")

SKILLS_DIR = PATHS.data_root / "skills"
CANDIDATES_DIR = SKILLS_DIR / "_candidates"

_KEY_RE = re.compile(r"^[a-zA-Z0-9_]{1,80}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dirs() -> None:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)


def slugify_key(raw: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw or "")).strip("_")
    return (cleaned[:80] or "untitled").lower()


def _read_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("skill read failed %s: %s", p, e)
        return None


def _write_json(p: Path, payload: dict[str, Any]) -> None:
    _ensure_dirs()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_skills() -> list[dict[str, Any]]:
    _ensure_dirs()
    out: list[dict[str, Any]] = []
    for p in sorted(SKILLS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        d = _read_json(p)
        if d:
            out.append(d)
    return out


def get_skill(key: str) -> dict[str, Any] | None:
    if not _KEY_RE.match(key or ""):
        return None
    p = SKILLS_DIR / f"{key}.json"
    if not p.exists():
        return None
    return _read_json(p)


def save_skill(payload: dict[str, Any]) -> dict[str, Any]:
    key = slugify_key(payload.get("key") or "")
    if not _KEY_RE.match(key):
        raise ValueError(f"잘못된 skill key: {key}")
    p = SKILLS_DIR / f"{key}.json"
    payload = dict(payload)
    payload["key"] = key
    payload.setdefault("created_at", _now())
    payload["updated_at"] = _now()
    payload.setdefault("run_count", 0)
    _write_json(p, payload)
    return payload


def delete_skill(key: str) -> bool:
    if not _KEY_RE.match(key or ""):
        return False
    p = SKILLS_DIR / f"{key}.json"
    if not p.exists():
        return False
    p.unlink()
    return True


# ── Candidates ────────────────────────────────────────────────────────────────
def list_candidates() -> list[dict[str, Any]]:
    _ensure_dirs()
    out: list[dict[str, Any]] = []
    for p in sorted(CANDIDATES_DIR.glob("*.json")):
        d = _read_json(p)
        if d:
            out.append(d)
    return out


def save_candidate(candidate: dict[str, Any]) -> Path:
    _ensure_dirs()
    key = slugify_key(candidate.get("key") or candidate.get("hash") or "")
    if not _KEY_RE.match(key):
        raise ValueError(f"잘못된 candidate key: {key}")
    p = CANDIDATES_DIR / f"{key}.json"
    candidate = dict(candidate)
    candidate["key"] = key
    candidate.setdefault("created_at", _now())
    _write_json(p, candidate)
    return p


def approve_candidate(key: str, by: str, override_title: str | None = None) -> dict[str, Any]:
    if not _KEY_RE.match(key or ""):
        raise ValueError(f"잘못된 candidate key: {key}")
    src = CANDIDATES_DIR / f"{key}.json"
    if not src.exists():
        raise FileNotFoundError(f"candidate not found: {key}")
    cand = _read_json(src) or {}
    payload = {
        "key": key,
        "title": override_title or cand.get("title") or key,
        "description": cand.get("description") or "",
        "kind": cand.get("kind") or "chain",
        "steps": cand.get("steps") or [],
        "cells": cand.get("cells") or [],
        "owner": by,
        "shared": True,
        "approved_at": _now(),
        "source": "skill_miner",
        "miner_meta": {
            "freq": cand.get("freq"),
            "users": cand.get("users"),
            "first_seen": cand.get("first_seen"),
            "last_seen": cand.get("last_seen"),
        },
    }
    saved = save_skill(payload)
    src.unlink()
    return saved


def reject_candidate(key: str) -> bool:
    if not _KEY_RE.match(key or ""):
        return False
    p = CANDIDATES_DIR / f"{key}.json"
    if not p.exists():
        return False
    p.unlink()
    return True
