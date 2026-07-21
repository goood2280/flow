#!/usr/bin/env python3
"""Parse SCORE_LOG.md auto-verified entries + archived handoff JSON
and emit data/holweb-data/release_notes.json for Home card consumption.

Usage:
  python scripts/update_release_notes.py
  python scripts/update_release_notes.py --limit 15
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "collab" / "archive"
SCORE_LOG = ROOT / "collab" / "SCORE_LOG.md"
OUT_DIR = ROOT / "data" / "holweb-data"
OUT = OUT_DIR / "release_notes.json"


def short(s: str, n: int = 140) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def build(limit: int = 15) -> dict:
    items = []
    if ARCHIVE.exists():
        for p in sorted(ARCHIVE.glob("handoff_v9_*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            items.append({
                "id": d.get("id", p.stem),
                "title": d.get("title", ""),
                "summary": short(d.get("summary", ""), 160),
                "resolution": short(d.get("resolution", ""), 200),
                "archived_at": d.get("updated_at", ""),
                "priority": d.get("priority", ""),
                "track": (d.get("context", {}) or {}).get("track", ""),
                "backlog_id": (d.get("context", {}) or {}).get("backlog_id", ""),
            })

    items.sort(key=lambda x: x.get("archived_at", ""), reverse=True)
    items = items[:limit]

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "total_archived": sum(1 for _ in ARCHIVE.glob("handoff_v9_*.json")) if ARCHIVE.exists() else 0,
        "recent": items,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=15)
    args = p.parse_args()
    result = build(args.limit)
    print(f"release_notes.json written: {OUT}")
    print(f"  generated_at: {result['generated_at']}")
    print(f"  total_archived: {result['total_archived']}")
    print(f"  recent items: {len(result['recent'])}")


if __name__ == "__main__":
    main()
