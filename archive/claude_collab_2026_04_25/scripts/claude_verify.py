#!/usr/bin/env python3
"""Claude-side verifier for Codex-finished handoffs.

Scans flow/collab/inbox for handoffs with status=done, runs smoke test +
acceptance_criteria checks, appends verification records, and either
archives (pass) or reopens with a smoke_failure handoff (fail).

Usage:
  python3 scripts/claude_verify.py check
  python3 scripts/claude_verify.py verify <handoff_id>
  python3 scripts/claude_verify.py loop

Pure stdlib. Windows-safe paths.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
SCORE_LOG = COLLAB / "SCORE_LOG.md"
SMOKE_SCRIPT = ROOT / "scripts" / "smoke_test.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def list_done() -> list[Path]:
    if not INBOX.exists():
        return []
    out = []
    for p in sorted(INBOX.glob("*.json")):
        try:
            item = load(p)
        except Exception:
            continue
        if item.get("status") == "done":
            out.append(p)
    return out


def list_open_count() -> int:
    if not INBOX.exists():
        return 0
    n = 0
    for p in INBOX.glob("*.json"):
        try:
            item = load(p)
        except Exception:
            continue
        if item.get("status") == "open":
            n += 1
    return n


def run_smoke() -> tuple[int, int, str]:
    if not SMOKE_SCRIPT.exists():
        return (0, 1, f"smoke script missing: {SMOKE_SCRIPT}")
    try:
        proc = subprocess.run(
            [sys.executable, str(SMOKE_SCRIPT)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return (0, 1, "smoke timeout (>120s)")
    except Exception as e:
        return (0, 1, f"smoke error: {e}")

    out = (proc.stdout or "") + (proc.stderr or "")
    pass_n = out.count("PASS")
    fail_n = out.count("FAIL")
    tail = out.strip().splitlines()[-3:] if out.strip() else []
    note = " / ".join(tail) if tail else "no output"
    return (pass_n, fail_n, note[:300])


def check_linked_files(item: dict) -> tuple[bool, list[str]]:
    missing = []
    for f in item.get("linked_files", []):
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / f
        if not p.exists():
            missing.append(str(f))
    return (len(missing) == 0, missing)


def check_criteria(item: dict) -> list[dict]:
    """Keyword-level check: each criterion is OK if any linked_file contains
    at least one 4+ char word from the criterion text. Heuristic only —
    Claude (human) does the deep check in transcript."""
    results = []
    criteria = item.get("acceptance_criteria") or []
    linked = item.get("linked_files") or []

    corpus = []
    for f in linked:
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / f
        try:
            if p.exists() and p.is_file() and p.stat().st_size < 5_000_000:
                corpus.append(p.read_text(encoding="utf-8", errors="ignore").lower())
        except Exception:
            pass
    merged = "\n".join(corpus)

    for c in criteria:
        text = str(c).lower()
        tokens = [t.strip(".,:;()[]{}") for t in text.split() if len(t) >= 4]
        hits = sum(1 for t in tokens if t and t in merged)
        ok = hits >= max(1, len(tokens) // 3) if tokens else False
        results.append({
            "criterion": str(c)[:160],
            "ok": ok,
            "hits": hits,
            "tokens": len(tokens),
        })
    return results


def append_verification(item: dict, record: dict) -> None:
    item.setdefault("verification", []).append(record)
    item["updated_at"] = now_iso()


def spawn_failure_handoff(original: dict, reason: str, extra: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    new_id = f"{original.get('id','handoff')}_reopen_{ts}"
    payload = {
        "id": new_id,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "from": "claude",
        "to": "codex",
        "kind": "smoke_failure",
        "priority": original.get("priority", "p1"),
        "status": "open",
        "title": f"[REOPEN] {original.get('title','')}",
        "summary": f"Verify failed: {reason}",
        "context": {
            **(original.get("context") or {}),
            "reopen_of": original.get("id"),
        },
        "acceptance_criteria": original.get("acceptance_criteria", []),
        "linked_files": original.get("linked_files", []),
        "verification": [],
        "resolution": "",
        "reopen_detail": extra,
    }
    out = INBOX / f"{new_id}.json"
    save(out, payload)
    return out


def append_score_log(line: str) -> None:
    if not SCORE_LOG.exists():
        return
    text = SCORE_LOG.read_text(encoding="utf-8")
    marker = "_(아직 완료된 handoff 없음)_"
    if marker in text:
        text = text.replace(marker, line.rstrip() + "\n")
    else:
        anchor = "## 루프 이력"
        if anchor in text:
            idx = text.index(anchor)
            insert_at = text.find("\n\n", idx) + 2
            text = text[:insert_at] + line.rstrip() + "\n\n" + text[insert_at:]
        else:
            text += "\n" + line
    SCORE_LOG.write_text(text, encoding="utf-8")


def cmd_check(_args: argparse.Namespace) -> None:
    done = list_done()
    open_n = list_open_count()
    print(f"{len(done)} done items, {open_n} open items")
    for p in done:
        try:
            item = load(p)
        except Exception:
            continue
        print(f"  {item.get('id')} | {item.get('priority')} | {item.get('title')}")


def cmd_verify(args: argparse.Namespace) -> None:
    path = INBOX / f"{args.id}.json"
    if not path.exists():
        raise SystemExit(f"handoff not found: {args.id}")
    item = load(path)
    if item.get("status") != "done":
        raise SystemExit(f"handoff status is '{item.get('status')}', expected 'done'")

    files_ok, missing = check_linked_files(item)
    smoke_pass, smoke_fail, smoke_note = run_smoke()
    crit_results = check_criteria(item)
    crit_ok_n = sum(1 for r in crit_results if r["ok"])

    record = {
        "at": now_iso(),
        "smoke_pass": smoke_pass,
        "smoke_fail": smoke_fail,
        "smoke_note": smoke_note,
        "files_ok": files_ok,
        "files_missing": missing,
        "criteria_checks": crit_results,
        "criteria_ok": crit_ok_n,
        "criteria_total": len(crit_results),
    }

    passed = (
        files_ok
        and smoke_fail == 0
        and smoke_pass > 0
        and (len(crit_results) == 0 or crit_ok_n == len(crit_results))
    )
    append_verification(item, record)

    if passed:
        save(path, item)
        dst = ARCHIVE / path.name
        shutil.move(str(path), str(dst))
        log_line = (
            f"### {record['at']} — {item.get('id')} verified\n"
            f"- status: archived\n"
            f"- smoke: {smoke_pass}/{smoke_pass+smoke_fail} PASS\n"
            f"- acceptance: {crit_ok_n}/{len(crit_results)} OK\n"
            f"- files: {'ok' if files_ok else 'missing '+str(missing)}\n"
        )
        append_score_log(log_line)
        print(f"PASS -> {dst}")
    else:
        item["status"] = "open"
        save(path, item)
        reason_parts = []
        if not files_ok:
            reason_parts.append(f"missing files: {missing}")
        if smoke_fail:
            reason_parts.append(f"smoke fail x{smoke_fail}")
        if len(crit_results) and crit_ok_n < len(crit_results):
            reason_parts.append(f"criteria {crit_ok_n}/{len(crit_results)}")
        reason = "; ".join(reason_parts) or "unknown"
        new_path = spawn_failure_handoff(item, reason, record)
        print(f"FAIL -> reopened as {new_path}")


def cmd_loop(_args: argparse.Namespace) -> None:
    done = list_done()
    if not done:
        print("no done items")
        return
    for p in done:
        sub = argparse.Namespace(id=p.stem)
        try:
            cmd_verify(sub)
        except SystemExit as e:
            print(f"  skip {p.stem}: {e}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Claude-side handoff verifier")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.set_defaults(func=cmd_check)

    v = sub.add_parser("verify")
    v.add_argument("id")
    v.set_defaults(func=cmd_verify)

    l = sub.add_parser("loop")
    l.set_defaults(func=cmd_loop)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
