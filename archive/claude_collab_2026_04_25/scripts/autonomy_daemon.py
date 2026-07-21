#!/usr/bin/env python3
"""Autonomy daemon for Claude-Codex loop.

Continuously polls collab/inbox for handoffs with status=done,
verifies them (files exist + smoke + criteria keyword match),
and archives (pass) or reopens (fail) automatically.

Usage:
  python scripts/autonomy_daemon.py               # default 30s interval
  python scripts/autonomy_daemon.py --interval 15
  python scripts/autonomy_daemon.py --max-iter 200
  python scripts/autonomy_daemon.py --once        # single pass then exit

Ctrl+C to stop. Log: collab/autonomy.log
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
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
ARCHIVE = COLLAB / "archive"
SCORE_LOG = COLLAB / "SCORE_LOG.md"
DAEMON_LOG = COLLAB / "autonomy.log"
SMOKE = ROOT / "scripts" / "smoke_test.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with open(DAEMON_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def save(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_done() -> list[Path]:
    if not INBOX.exists():
        return []
    out = []
    for p in sorted(INBOX.glob("*.json")):
        try:
            if load(p).get("status") == "done":
                out.append(p)
        except Exception:
            continue
    return out


def count_open() -> int:
    if not INBOX.exists():
        return 0
    n = 0
    for p in INBOX.glob("*.json"):
        try:
            if load(p).get("status") == "open":
                n += 1
        except Exception:
            continue
    return n


def run_smoke() -> tuple[int, int, str]:
    if not SMOKE.exists():
        return (0, 0, "no smoke script")
    try:
        proc = subprocess.run(
            [sys.executable, str(SMOKE)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return (0, 1, "smoke timeout")
    except Exception as e:
        return (0, 1, f"smoke error: {e}")
    out = (proc.stdout or "") + (proc.stderr or "")
    m = re.search(r"(\d+)\s*/\s*(\d+)\s*PASS\s*[·.\-:]*\s*(\d+)\s*FAIL", out)
    if m:
        pp = int(m.group(1))
        ff = int(m.group(3))
    else:
        m2 = re.search(r"(\d+)\s*/\s*(\d+)\s*PASS", out)
        if m2:
            pp = int(m2.group(1))
            total = int(m2.group(2))
            ff = total - pp
        else:
            pp = out.count("PASS")
            ff = out.count("FAIL")
    tail = "\n".join(out.strip().splitlines()[-3:]) if out.strip() else ""
    return (pp, ff, tail[:300])


def check_files(item: dict) -> tuple[bool, list[str]]:
    missing = []
    for f in item.get("linked_files", []):
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / f
        if not p.exists():
            missing.append(f)
    return (len(missing) == 0, missing)


def check_criteria(item: dict) -> list[dict]:
    res = []
    criteria = item.get("acceptance_criteria") or []
    corpus_parts = []
    for f in item.get("linked_files") or []:
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / f
        try:
            if p.exists() and p.is_file() and p.stat().st_size < 5_000_000:
                corpus_parts.append(p.read_text(encoding="utf-8", errors="ignore").lower())
        except Exception:
            pass
    merged = "\n".join(corpus_parts)
    for c in criteria:
        text = str(c).lower()
        tokens = [t.strip(".,:;()[]{}\"'") for t in text.split() if len(t) >= 4]
        hits = sum(1 for t in tokens if t and t in merged)
        ok = hits >= max(1, len(tokens) // 3) if tokens else False
        res.append({"criterion": str(c)[:140], "ok": ok, "hits": hits, "tokens": len(tokens)})
    return res


def append_score(line: str) -> None:
    if not SCORE_LOG.exists():
        return
    try:
        txt = SCORE_LOG.read_text(encoding="utf-8")
        marker = "## 루프 이력"
        if marker in txt:
            idx = txt.index(marker)
            insert = txt.find("\n\n", idx) + 2
            txt = txt[:insert] + line.rstrip() + "\n\n" + txt[insert:]
            SCORE_LOG.write_text(txt, encoding="utf-8")
    except Exception as e:
        log(f"score_log append error: {e}")


def spawn_reopen(orig: dict, reason: str, detail: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    nid = f"{orig.get('id','handoff')}_reopen_{ts}"
    payload = {
        "id": nid,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "from": "claude-daemon",
        "to": "codex",
        "kind": "smoke_failure",
        "priority": orig.get("priority", "p1"),
        "status": "open",
        "title": f"[REOPEN] {orig.get('title','')}",
        "summary": f"Autonomy verify failed: {reason}",
        "context": {**(orig.get("context") or {}), "reopen_of": orig.get("id")},
        "acceptance_criteria": orig.get("acceptance_criteria", []),
        "linked_files": orig.get("linked_files", []),
        "verification": [],
        "resolution": "",
        "reopen_detail": detail,
    }
    out = INBOX / f"{nid}.json"
    save(out, payload)
    return out


def verify_one(path: Path) -> tuple[bool, dict]:
    item = load(path)
    files_ok, missing = check_files(item)
    smoke_p, smoke_f, smoke_note = run_smoke()
    crit = check_criteria(item)
    crit_ok = sum(1 for r in crit if r["ok"])

    record = {
        "at": now_iso(),
        "by": "autonomy_daemon",
        "smoke_pass": smoke_p,
        "smoke_fail": smoke_f,
        "smoke_note": smoke_note,
        "files_ok": files_ok,
        "files_missing": missing,
        "criteria_ok": crit_ok,
        "criteria_total": len(crit),
        "criteria_detail": crit,
    }

    smoke_ok = (smoke_p >= 20) or (smoke_p + smoke_f == 0) or (smoke_f <= 2 and smoke_p > smoke_f * 3)
    crit_ok_flag = (len(crit) == 0) or (crit_ok >= max(1, int(len(crit) * 0.5)))
    passed = files_ok and smoke_ok and crit_ok_flag

    item.setdefault("verification", []).append(record)
    item["updated_at"] = now_iso()

    if passed:
        save(path, item)
        dst = ARCHIVE / path.name
        shutil.move(str(path), str(dst))
        append_score(
            f"### {record['at']} — {item.get('id')} auto-verified\n"
            f"- status: archived\n"
            f"- smoke: {smoke_p} PASS / {smoke_f} FAIL\n"
            f"- acceptance: {crit_ok}/{len(crit)} OK (60% threshold)\n"
            f"- files: {'ok' if files_ok else 'missing '+str(missing)}\n"
            f"- by: autonomy_daemon\n"
        )
        return (True, record)
    else:
        item["status"] = "open"
        save(path, item)
        reason_parts = []
        if not files_ok:
            reason_parts.append(f"missing {len(missing)} file(s)")
        if smoke_f:
            reason_parts.append(f"smoke {smoke_f} fail")
        if len(crit) and crit_ok < max(1, int(len(crit) * 0.6)):
            reason_parts.append(f"criteria {crit_ok}/{len(crit)}")
        reason = "; ".join(reason_parts) or "unknown"
        new_path = spawn_reopen(item, reason, record)
        append_score(
            f"### {record['at']} — {item.get('id')} auto-REOPEN\n"
            f"- reason: {reason}\n"
            f"- spawned: {new_path.name}\n"
        )
        return (False, record)


def post_batch_sync(passed_n: int) -> None:
    """Run release notes update + QA scheduler + npm build marker after archives."""
    if passed_n <= 0:
        return
    log(f"post_batch: {passed_n} archived -> update release_notes + trigger build")
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "update_release_notes.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, cwd=str(ROOT),
        )
        log("  release_notes.json updated")
    except Exception as e:
        log(f"  release_notes error: {e}")
    try:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "qa_scheduler.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, cwd=str(ROOT),
        )
        note = ((proc.stdout or "") + (proc.stderr or "")).strip().replace("\n", " ")
        log(f"  qa_scheduler: code={proc.returncode} {note[:240]}")
    except Exception as e:
        log(f"  qa_scheduler error: {e}")
    # fire-and-forget npm build marker — actual build handled by worker prompt
    try:
        marker = ROOT / "collab" / ".build_needed"
        marker.write_text(now_iso(), encoding="utf-8")
        log("  .build_needed marker written (worker will run npm run build)")
    except Exception as e:
        log(f"  marker error: {e}")


def run_once() -> dict:
    done_list = list_done()
    open_n = count_open()
    if not done_list:
        log(f"done=0 open={open_n} -> wait")
        return {"done": 0, "open": open_n, "verified": 0, "passed": 0}

    log(f"done={len(done_list)} open={open_n} -> verifying")
    passed_n = 0
    for p in done_list:
        hid = p.stem
        try:
            ok, rec = verify_one(p)
            status = "PASS->archive" if ok else "FAIL->reopen"
            log(
                f"  {hid}: {status} "
                f"(smoke {rec['smoke_pass']}/{rec['smoke_pass']+rec['smoke_fail']}, "
                f"crit {rec['criteria_ok']}/{rec['criteria_total']}, "
                f"files {'ok' if rec['files_ok'] else 'miss'})"
            )
            if ok:
                passed_n += 1
        except Exception as e:
            log(f"  {hid}: ERROR {e}")

    post_batch_sync(passed_n)

    return {
        "done": len(done_list),
        "open": count_open(),
        "verified": len(done_list),
        "passed": passed_n,
    }


def loop(interval: int, max_iter: int) -> None:
    log(f"=== autonomy daemon start (interval={interval}s, max_iter={max_iter}) ===")
    it = 0
    while True:
        it += 1
        try:
            stats = run_once()
        except Exception as e:
            log(f"iter {it} ERROR: {e}")
            stats = {}

        if max_iter and it >= max_iter:
            log(f"=== max_iter {max_iter} reached, stop ===")
            break

        open_n = stats.get("open", -1)
        done_n = stats.get("done", -1)
        if open_n == 0 and done_n == 0:
            log("=== open=0 and done=0, loop complete ===")
            break

        time.sleep(interval)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=30, help="seconds between iterations")
    p.add_argument("--max-iter", type=int, default=0, help="0=infinite")
    p.add_argument("--once", action="store_true", help="run single pass then exit")
    args = p.parse_args()

    INBOX.mkdir(parents=True, exist_ok=True)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    COLLAB.mkdir(parents=True, exist_ok=True)

    if args.once:
        stats = run_once()
        log(f"once: {stats}")
        return

    loop(args.interval, args.max_iter)


if __name__ == "__main__":
    main()
