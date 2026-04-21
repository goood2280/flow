#!/usr/bin/env python3
"""FabCanvas (flow) — single setup entrypoint.

Replaces per-version `setup_vXXX.py`. Keep this file; bump the VERSION
constant on every release and append a one-line summary to CHANGELOG.md.

Usage:
    python setup.py install-deps     # pip install backend deps
    python setup.py build-frontend   # cd frontend && npm install && npm run build
    python setup.py all              # deps + frontend build + run server hint
    python setup.py version          # print VERSION

Run the server with:
    uvicorn app:app --host 0.0.0.0 --port 8080

Seed admin: hol / hol12345!  (override via FABCANVAS_ADMIN_PW env var)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# ── Single source of truth for the current release ──────────────────
VERSION = "8.7.1"
CODENAME = "flow"
# Keep in sync with CHANGELOG.md + VERSION.json (top entry).

ROOT = Path(__file__).resolve().parent


def _run(cmd: str, cwd: Path | None = None) -> int:
    print(f"$ {cmd}")
    return subprocess.call(cmd, shell=True, cwd=str(cwd or ROOT))


def install_deps() -> int:
    req = ROOT / "backend" / "requirements.txt"
    if req.exists():
        return _run(f"{sys.executable} -m pip install -r {req}")
    # Fallback — explicit list matches historic installer.
    pkgs = "fastapi uvicorn[standard] pandas pyarrow polars numpy python-multipart boto3 scikit-learn scipy openpyxl"
    return _run(f"{sys.executable} -m pip install {pkgs}")


def build_frontend() -> int:
    fe = ROOT / "frontend"
    if not (fe / "package.json").exists():
        print("frontend/package.json not found — skipping", file=sys.stderr)
        return 1
    rc = _run("npm install", cwd=fe)
    if rc != 0:
        return rc
    return _run("npm run build", cwd=fe)


def print_version() -> int:
    vj = ROOT / "VERSION.json"
    data = {}
    if vj.exists():
        try:
            data = json.loads(vj.read_text("utf-8"))
        except Exception:
            pass
    print(f"flow (FabCanvas) v{VERSION} — codename {CODENAME}")
    if data.get("version") and data["version"] != VERSION:
        print(f"  ⚠ VERSION.json mismatch: {data['version']}")
    return 0


def sync_version_json() -> int:
    """Stamp VERSION constant onto VERSION.json top-level (changelog unchanged)."""
    vj = ROOT / "VERSION.json"
    if not vj.exists():
        print("VERSION.json not found", file=sys.stderr)
        return 1
    data = json.loads(vj.read_text("utf-8"))
    if data.get("version") == VERSION:
        print(f"VERSION.json already at {VERSION}")
        return 0
    data["version"] = VERSION
    data["codename"] = CODENAME
    vj.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    print(f"VERSION.json → {VERSION}")
    return 0


COMMANDS = {
    "install-deps": install_deps,
    "build-frontend": build_frontend,
    "version": print_version,
    "sync-version": sync_version_json,
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        print("\nCommands:")
        for k in list(COMMANDS) + ["all"]:
            print(f"  {k}")
        return 0
    cmd = argv[0]
    if cmd == "all":
        rc = install_deps() or build_frontend() or print_version()
        return rc
    fn = COMMANDS.get(cmd)
    if not fn:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 2
    return fn()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
