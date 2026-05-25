#!/usr/bin/env python3
"""Dry-run or apply runtime Knowledge Wiki clearing/cleanup."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import knowledge_vault as kv  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear all runtime Knowledge Wiki pages, or run the legacy selected cleanup.")
    parser.add_argument("--apply", action="store_true", help="delete selected docs after creating a backup")
    parser.add_argument(
        "--mode",
        choices=("all", "selected"),
        default="all",
        help="all deletes every runtime Wiki page; selected removes only legacy demo/internal docs",
    )
    parser.add_argument("--actor", default="codex_wiki_cleanup", help="actor name written to wiki log when applying")
    args = parser.parse_args()

    if args.mode == "selected":
        result = kv.cleanup_runtime_wiki(apply=bool(args.apply), actor=args.actor)
    else:
        result = kv.clear_runtime_wiki(apply=bool(args.apply), actor=args.actor)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
