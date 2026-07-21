#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for p in (ROOT, BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.tracker_schema import migrate_tracker_issues_file  # noqa: E402


def main() -> int:
    result = migrate_tracker_issues_file(reason="script", actor="scripts/migrate_tracker_schema.py")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
