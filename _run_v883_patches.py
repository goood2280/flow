"""v8.8.3 aggregated patch runner.

Run once from FabCanvas.ai root:
  python _run_v883_patches.py

Applies:
  1. _patch_meeting_v883.py    — FE meeting visibility group_ids picker
  2. _patch_version_v883.py    — VERSION.json + CHANGELOG.md bump

Both patchers are idempotent. After running, restart the dev server and
run `npm --prefix frontend run build` + `python -m compileall backend` to
verify no syntax errors, then `git add -A && git commit && git push`.
"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).parent

for name in ("_patch_meeting_v883.py", "_patch_version_v883.py"):
    p = HERE / name
    if not p.exists():
        print(f"! missing {name} — skipping")
        continue
    print(f"=== {name} ===")
    try:
        runpy.run_path(str(p), run_name="__main__")
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"  exit {e.code}")
    except Exception as e:
        print(f"  ERROR: {e!r}")
        sys.exit(1)

print("\nAll patches applied. Next:")
print("  python -m compileall backend")
print("  npm --prefix frontend run build")
print("  git add -A && git commit -m 'v8.8.3 — backup max 5, PageGear unify, Base delete, informs comments, meeting visibility FE' && git push")
print("  python _build_setup.py   # regenerate self-extracting setup.py")
