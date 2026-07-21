#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLAB = ROOT / "collab"
ARCHIVE = COLLAB / "archive"
STATE_FILE = COLLAB / "qa_state.json"
QA_SCRIPT = ROOT / "scripts" / "e2e_qa.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def archived_count() -> int:
    return sum(1 for _ in ARCHIVE.glob("handoff_v9_*.json"))


def run_qa() -> dict:
    proc = subprocess.run(
        [sys.executable, str(QA_SCRIPT)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
    )
    return {
        "code": proc.returncode,
        "stdout": (proc.stdout or "").strip()[:2000],
        "stderr": (proc.stderr or "").strip()[:2000],
    }


def main() -> int:
    state = load_json(STATE_FILE, {"last_archive_count": 0, "last_ux_archive_count": 0, "history": []})
    current = archived_count()
    delta = current - int(state.get("last_archive_count", 0) or 0)
    ux_delta = current - int(state.get("last_ux_archive_count", 0) or 0)
    result = {
        "checked_at": now_iso(),
        "archive_count": current,
        "delta_since_last_qa": delta,
        "delta_since_last_ux": ux_delta,
        "ran_qa": False,
        "ran_ux_review": ux_delta >= 5,
    }
    if delta >= 3:
        result["ran_qa"] = True
        result["qa_result"] = run_qa()
        state["last_archive_count"] = current
    if ux_delta >= 5:
        state["last_ux_archive_count"] = current
    state.setdefault("history", [])
    state["history"].insert(0, result)
    state["history"] = state["history"][:20]
    save_json(STATE_FILE, state)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
