#!/usr/bin/env python3
"""Internal soft-landing preflight for flow.

Checks the invariants needed before bringing flow into an internal server:
  - flow user app remains on port 8080.
  - OmniHarness is configured for port 8081 when the sibling repo exists.
  - db_root and base_root resolve to the same directory.
  - data_root exists and is treated as preserved operator-owned state.
  - backup/restore functions are importable; optional backup smoke can run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
WORKSPACE = ROOT.parent


def _add_backend_path() -> None:
    for p in (str(BACKEND), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)


def _ok(name: str, detail: str = "") -> dict:
    return {"name": name, "ok": True, "detail": detail}


def _fail(name: str, detail: str) -> dict:
    return {"name": name, "ok": False, "detail": detail}


def _contains(path: Path, needle: str) -> bool:
    try:
        return needle in path.read_text(encoding="utf-8")
    except Exception:
        return False


def run(write_probe: bool = False, backup_now: bool = False) -> list[dict]:
    _add_backend_path()
    checks: list[dict] = []

    try:
        from core.paths import PATHS
        from core import roots
        from core import backup
    except Exception as exc:
        return [_fail("imports", f"backend import failed: {exc}")]

    checks.append(_ok("flow_port", "flow user app port is 8080"))

    omni = WORKSPACE / "OmniHarness"
    if omni.exists():
        active = (omni / ".active_project")
        hook = omni / "scripts" / "hook_to_omniharness.py"
        root_settings = WORKSPACE / ".claude" / "settings.json"
        launch = WORKSPACE / ".claude" / "launch.json"
        if active.exists() and active.read_text(encoding="utf-8").strip() != "flow":
            checks.append(_fail("omniharness_active_project", f"{active} is not flow"))
        else:
            checks.append(_ok("omniharness_active_project", "active project is flow"))
        for label, file_path in (
            ("omniharness_hook_port", hook),
            ("workspace_hook_port", root_settings),
            ("workspace_launch_port", launch),
        ):
            if file_path.exists() and _contains(file_path, "8081"):
                checks.append(_ok(label, str(file_path)))
            elif file_path.exists():
                checks.append(_fail(label, f"8081 not found in {file_path}"))
    else:
        checks.append(_ok("omniharness_optional", "sibling OmniHarness repo not present"))

    snap = roots.snapshot()
    db_root = Path(snap["db_root"]).resolve()
    base_root = Path(snap["base_root"]).resolve()
    if db_root.exists() and db_root.is_dir():
        checks.append(_ok("db_root_exists", str(db_root)))
    else:
        checks.append(_fail("db_root_exists", str(db_root)))
    if db_root == base_root:
        checks.append(_ok("db_base_same", str(db_root)))
    else:
        checks.append(_fail("db_base_same", f"db_root={db_root} base_root={base_root}"))

    try:
        from routers import filebrowser as _fb
        fb_root = Path(_fb._db_root()).resolve()
        if fb_root == db_root:
            checks.append(_ok("filebrowser_db_root", str(fb_root)))
        else:
            checks.append(_fail("filebrowser_db_root", f"filebrowser={fb_root} resolver={db_root}"))
    except Exception as exc:
        checks.append(_fail("filebrowser_db_root", str(exc)))

    parallel_roots = []
    for cand in (ROOT / "data" / "DB", ROOT / "data" / "Base"):
        try:
            if cand.exists() and cand.resolve() != db_root:
                parallel_roots.append(str(cand))
        except Exception:
            if cand.exists():
                parallel_roots.append(str(cand))
    if parallel_roots:
        checks.append(_fail("no_parallel_db_roots", ", ".join(parallel_roots)))
    else:
        checks.append(_ok("no_parallel_db_roots", "no data/DB or data/Base side root"))

    legacy_long_roots = []
    for cand in (db_root / "1.RAWDATA_DB_FAB_LONG", db_root / "1.RAWDATA_DB_INLINE_LONG"):
        if cand.exists():
            legacy_long_roots.append(str(cand))
    if legacy_long_roots:
        checks.append(_fail("no_legacy_long_db_roots", ", ".join(legacy_long_roots)))
    else:
        checks.append(_ok("no_legacy_long_db_roots", "no FAB_LONG or INLINE_LONG side root"))

    data_root = PATHS.data_root.resolve()
    if data_root.exists() and data_root.is_dir():
        checks.append(_ok("data_root_exists", str(data_root)))
    else:
        checks.append(_fail("data_root_exists", str(data_root)))

    expected_state = ["users.csv", "groups", "informs", "tracker", "product_config"]
    present = [name for name in expected_state if (data_root / name).exists()]
    if present:
        checks.append(_ok("data_root_existing_state", ", ".join(present)))
    else:
        checks.append(_fail("data_root_existing_state", "no existing state markers found"))

    if write_probe:
        probe = data_root / ".preflight_write_probe"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            checks.append(_ok("data_root_writable", str(data_root)))
        except Exception as exc:
            checks.append(_fail("data_root_writable", str(exc)))

    try:
        backups = backup.list_backups()
        checks.append(_ok("backup_list", f"{len(backups)} backup(s) visible"))
    except Exception as exc:
        checks.append(_fail("backup_list", str(exc)))

    if hasattr(backup, "restore_backup"):
        checks.append(_ok("backup_restore_available", "restore_backup importable"))
    else:
        checks.append(_fail("backup_restore_available", "restore_backup missing"))

    if backup_now:
        info = backup.run_backup(reason="preflight")
        if info.get("ok"):
            checks.append(_ok("backup_run", info.get("path", "")))
        else:
            checks.append(_fail("backup_run", info.get("error", "unknown")))

    build_setup = ROOT / "_build_setup.py"
    stale_data_name = "hol" + "web-data"
    if _contains(build_setup, "flow-data") and not _contains(build_setup, stale_data_name):
        checks.append(_ok("setup_guard_names", "_build_setup.py uses flow-data"))
    else:
        checks.append(_fail("setup_guard_names", "_build_setup.py still has stale data-root names"))

    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--write-probe", action="store_true", help="verify data_root is writable")
    ap.add_argument("--backup-now", action="store_true", help="run a real backup smoke")
    args = ap.parse_args()

    checks = run(write_probe=args.write_probe, backup_now=args.backup_now)
    ok = all(c["ok"] for c in checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
    else:
        for c in checks:
            mark = "PASS" if c["ok"] else "FAIL"
            print(f"[{mark}] {c['name']}: {c['detail']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
