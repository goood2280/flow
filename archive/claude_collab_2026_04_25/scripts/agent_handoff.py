#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


ROOT = Path(__file__).resolve().parents[1]
COLLAB = ROOT / "collab"
INBOX = COLLAB / "inbox"
OUTBOX = COLLAB / "outbox"
ARCHIVE = COLLAB / "archive"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for p in (INBOX, OUTBOX, ARCHIVE):
        p.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_id(prefix: str = "handoff") -> str:
    ensure_dirs()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    existing = sorted(p.stem for p in INBOX.glob(f"{prefix}_{ts}_*.json"))
    return f"{prefix}_{ts}_{len(existing)+1:03d}"


def create_handoff(args: argparse.Namespace) -> None:
    ensure_dirs()
    hid = args.id or next_id()
    payload = {
        "id": hid,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "from": args.from_agent,
        "to": args.to_agent,
        "kind": args.kind,
        "priority": args.priority,
        "status": "open",
        "title": args.title,
        "summary": args.summary or "",
        "context": {},
        "acceptance_criteria": [],
        "linked_files": [],
        "verification": [],
        "resolution": "",
    }
    out = INBOX / f"{hid}.json"
    save_json(out, payload)
    print(out)


def list_handoffs(args: argparse.Namespace) -> None:
    ensure_dirs()
    for path in sorted(INBOX.glob("*.json")):
        try:
            item = load_json(path)
        except Exception:
            continue
        if args.status and item.get("status") != args.status:
            continue
        if args.to_agent and item.get("to") != args.to_agent:
            continue
        print(f"{item.get('id')} | {item.get('status')} | {item.get('priority')} | {item.get('to')} | {item.get('title')}")


def update_handoff(args: argparse.Namespace) -> None:
    ensure_dirs()
    path = INBOX / f"{args.id}.json"
    if not path.exists():
        raise SystemExit(f"handoff not found: {args.id}")
    item = load_json(path)
    if args.status:
        item["status"] = args.status
    if args.resolution:
        item["resolution"] = args.resolution
    if args.verification:
        item.setdefault("verification", []).append(args.verification)
    item["updated_at"] = now_iso()
    save_json(path, item)
    print(path)


def archive_handoff(args: argparse.Namespace) -> None:
    ensure_dirs()
    src = INBOX / f"{args.id}.json"
    if not src.exists():
        raise SystemExit(f"handoff not found: {args.id}")
    item = load_json(src)
    item["status"] = "archived"
    item["updated_at"] = now_iso()
    save_json(src, item)
    dst = ARCHIVE / src.name
    shutil.move(str(src), str(dst))
    print(dst)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Shared Claude/Codex handoff helper")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create")
    c.add_argument("--id", default="")
    c.add_argument("--from", dest="from_agent", default="claude")
    c.add_argument("--to", dest="to_agent", default="codex")
    c.add_argument("--kind", default="implementation_request")
    c.add_argument("--priority", default="p2")
    c.add_argument("--title", required=True)
    c.add_argument("--summary", default="")
    c.set_defaults(func=create_handoff)

    l = sub.add_parser("list")
    l.add_argument("--status", default="")
    l.add_argument("--to", dest="to_agent", default="")
    l.set_defaults(func=list_handoffs)

    u = sub.add_parser("update")
    u.add_argument("id")
    u.add_argument("--status", default="")
    u.add_argument("--resolution", default="")
    u.add_argument("--verification", default="")
    u.set_defaults(func=update_handoff)

    a = sub.add_parser("archive")
    a.add_argument("id")
    a.set_defaults(func=archive_handoff)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
