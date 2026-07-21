#!/usr/bin/env python3
"""Pick the next handoff for Codex to work on.

Priority order:
  1. priority p1 > p2 > p3
  2. within same priority: H > F > P > L (by track)
  3. dependency-satisfied first (deps all archived)
  4. id ascending

Usage:
  python scripts/next_handoff.py          # prints "handoff_v9_<ID>" or "NONE"
  python scripts/next_handoff.py --json   # full JSON line
  python scripts/next_handoff.py --path   # absolute path to the JSON file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "collab" / "inbox"
ARCHIVE = ROOT / "collab" / "archive"


TRACK_ORDER = {"H": 0, "F": 1, "P": 2, "L": 3}
PRIORITY_ORDER = {"p0": 0, "p1": 1, "p2": 2, "p3": 3, "p4": 4}


def load(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def archived_ids() -> set:
    out = set()
    if ARCHIVE.exists():
        for p in ARCHIVE.glob("*.json"):
            d = load(p)
            if d:
                out.add(d.get("id", p.stem))
    return out


def track_letter(item: dict) -> str:
    bid = str(item.get("context", {}).get("backlog_id") or "")
    for c in bid:
        if c.isalpha():
            return c.upper()
    title = str(item.get("title", ""))
    for c in title:
        if c in "HFPL":
            return c
    return "Z"


def open_candidates() -> list[dict]:
    if not INBOX.exists():
        return []
    out = []
    for p in sorted(INBOX.glob("*.json")):
        d = load(p)
        if not d:
            continue
        if d.get("status") != "open":
            continue
        # skip reopen handoffs — let daemon handle
        if d.get("kind") == "smoke_failure":
            continue
        d["_path"] = str(p)
        out.append(d)
    return out


def natural_num(iid: str) -> int:
    # extract trailing numeric part, e.g. handoff_v9_H10 -> 10
    import re
    m = re.search(r"(\d+)$", iid)
    if m:
        return int(m.group(1))
    m2 = re.search(r"[HFPL](\d+)", iid)
    if m2:
        return int(m2.group(1))
    return 9999


def sort_key(item: dict, done_ids: set) -> tuple:
    prio = PRIORITY_ORDER.get(item.get("priority", "p9"), 9)
    track = TRACK_ORDER.get(track_letter(item), 9)
    deps = item.get("context", {}).get("dependencies") or []
    deps_unmet = sum(1 for d in deps if f"handoff_v9_{d}" not in done_ids and d not in done_ids)
    iid = item.get("id", "")
    num = natural_num(iid)
    return (deps_unmet, prio, track, num, iid)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--json", action="store_true")
    p.add_argument("--path", action="store_true")
    args = p.parse_args()

    done = archived_ids()
    cands = open_candidates()
    if not cands:
        print("NONE")
        return

    cands.sort(key=lambda x: sort_key(x, done))
    top = cands[0]

    if args.json:
        out = {k: v for k, v in top.items() if k != "_path"}
        print(json.dumps(out, ensure_ascii=False))
    elif args.path:
        print(top["_path"])
    else:
        # default: print id + priority + track + title on one line
        print(f"{top.get('id')} | {top.get('priority')} | {track_letter(top)} | {top.get('title','')}")


if __name__ == "__main__":
    main()
