#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COLLAB = ROOT / "collab"
INBOX = COLLAB / "inbox"
ARCHIVE = COLLAB / "archive"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for p in (INBOX, ARCHIVE):
        p.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_id(prefix: str = "loop") -> str:
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    seq = len(list(INBOX.glob(f"{prefix}_{ts}_*.json"))) + 1
    return f"{prefix}_{ts}_{seq:03d}"


def load_item(item_id: str) -> tuple[Path, dict]:
    path = INBOX / f"{item_id}.json"
    if not path.exists():
        raise SystemExit(f"not found: {item_id}")
    return path, load_json(path)


def submit(args: argparse.Namespace) -> None:
    ensure_dirs()
    item_id = args.id or next_id()
    data = {
        "id": item_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "from": args.from_agent,
        "to": args.to_agent,
        "kind": args.kind,
        "priority": args.priority,
        "status": "open",
        "title": args.title,
        "summary": args.summary or "",
        "request": {
            "acceptance_criteria": [],
            "linked_files": [],
        },
        "claim": {},
        "response": {
            "by": "",
            "summary": "",
            "verification": [],
        },
    }
    path = INBOX / f"{item_id}.json"
    save_json(path, data)
    print(path)


def claim(args: argparse.Namespace) -> None:
    path, data = load_item(args.id)
    data["status"] = "claimed"
    data["updated_at"] = now_iso()
    data["claim"] = {
        "by": args.by,
        "claimed_at": now_iso(),
        "note": args.note or "",
    }
    save_json(path, data)
    print(path)


def reply(args: argparse.Namespace) -> None:
    path, data = load_item(args.id)
    data["status"] = "responded"
    data["updated_at"] = now_iso()
    resp = data.setdefault("response", {})
    resp["by"] = args.by
    resp["summary"] = args.summary or ""
    if args.verification:
        resp.setdefault("verification", []).append(args.verification)
    save_json(path, data)
    print(path)


def close_item(args: argparse.Namespace) -> None:
    path, data = load_item(args.id)
    data["status"] = "closed"
    data["updated_at"] = now_iso()
    save_json(path, data)
    print(path)


def archive_item(args: argparse.Namespace) -> None:
    path, data = load_item(args.id)
    data["status"] = "archived"
    data["updated_at"] = now_iso()
    save_json(path, data)
    dst = ARCHIVE / path.name
    shutil.move(str(path), str(dst))
    print(dst)


def list_items(args: argparse.Namespace) -> None:
    ensure_dirs()
    for path in sorted(INBOX.glob("*.json")):
        try:
            item = load_json(path)
        except Exception:
            continue
        if args.agent and item.get("to") != args.agent and item.get("from") != args.agent:
            continue
        if args.status and item.get("status") != args.status:
            continue
        print(f"{item.get('id')} | {item.get('status')} | {item.get('from')} -> {item.get('to')} | {item.get('title')}")


def watch(args: argparse.Namespace) -> None:
    ensure_dirs()
    seen: dict[str, str] = {}
    while True:
        current = {}
        for path in sorted(INBOX.glob("*.json")):
            try:
                item = load_json(path)
            except Exception:
                continue
            if args.agent and item.get("to") != args.agent and item.get("from") != args.agent:
                continue
            key = item.get("id") or path.stem
            stamp = f"{item.get('status')}|{item.get('updated_at')}"
            current[key] = stamp
            if seen.get(key) != stamp:
                print(f"[{now_iso()}] {key} | {item.get('status')} | {item.get('from')} -> {item.get('to')} | {item.get('title')}")
        seen = current
        time.sleep(max(1, args.interval))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Near-real-time collaboration loop helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit")
    s.add_argument("--id", default="")
    s.add_argument("--from", dest="from_agent", default="claude")
    s.add_argument("--to", dest="to_agent", default="codex")
    s.add_argument("--kind", default="implementation_request")
    s.add_argument("--priority", default="p2")
    s.add_argument("--title", required=True)
    s.add_argument("--summary", default="")
    s.set_defaults(func=submit)

    c = sub.add_parser("claim")
    c.add_argument("id")
    c.add_argument("--by", required=True)
    c.add_argument("--note", default="")
    c.set_defaults(func=claim)

    r = sub.add_parser("reply")
    r.add_argument("id")
    r.add_argument("--by", required=True)
    r.add_argument("--summary", default="")
    r.add_argument("--verification", default="")
    r.set_defaults(func=reply)

    cl = sub.add_parser("close")
    cl.add_argument("id")
    cl.set_defaults(func=close_item)

    a = sub.add_parser("archive")
    a.add_argument("id")
    a.set_defaults(func=archive_item)

    l = sub.add_parser("list")
    l.add_argument("--agent", default="")
    l.add_argument("--status", default="")
    l.set_defaults(func=list_items)

    w = sub.add_parser("watch")
    w.add_argument("--agent", default="")
    w.add_argument("--interval", type=int, default=3)
    w.set_defaults(func=watch)
    return p


def main() -> None:
    ensure_dirs()
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
