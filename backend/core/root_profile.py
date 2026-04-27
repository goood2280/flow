"""Boot-time root profile for flow.

This module is intentionally independent from core.paths. It lets the Admin UI
persist local/shared/custom root preferences in a project-local file that is
read before PATHS is built.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROFILE_FILE = PROJECT_ROOT / "data" / "runtime_roots.json"
PROD_SHARED = Path("/config/work/sharedworkspace")
DEFAULT_PROD_APP_CANDIDATES = [
    Path("/config/work/flow-fast-api"),
]
VALID_MODES = {"auto", "local", "shared", "custom"}


def _same_path(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a.absolute() == b.absolute()


def _clean_mode(value: Any) -> str:
    mode = str(value or "auto").strip().lower()
    return mode if mode in VALID_MODES else "auto"


def read_profile() -> dict:
    try:
        if not PROFILE_FILE.is_file():
            return {"mode": "auto"}
        with open(PROFILE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"mode": "auto"}
    except Exception:
        return {"mode": "auto"}
    data["mode"] = _clean_mode(data.get("mode"))
    roots = []
    for raw in data.get("prod_app_roots") or []:
        s = str(raw or "").strip()
        if s:
            roots.append(s)
    data["prod_app_roots"] = roots
    return data


def write_profile(update: dict) -> dict:
    current = read_profile()
    next_profile = dict(current)
    for key in ("data_root", "db_root"):
        if key in update:
            val = update.get(key)
            if isinstance(val, str) and val.strip():
                next_profile[key] = val.strip()
            else:
                next_profile.pop(key, None)
    if "mode" in update:
        next_profile["mode"] = _clean_mode(update.get("mode"))
    if "prod_app_roots" in update:
        roots = []
        for raw in update.get("prod_app_roots") or []:
            s = str(raw or "").strip()
            if s and s not in roots:
                roots.append(s)
        if roots:
            next_profile["prod_app_roots"] = roots
        else:
            next_profile.pop("prod_app_roots", None)
    next_profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE_FILE.with_suffix(PROFILE_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(next_profile, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PROFILE_FILE)
    return next_profile


def prod_app_candidates(profile: dict | None = None) -> list[Path]:
    profile = profile or read_profile()
    out = list(DEFAULT_PROD_APP_CANDIDATES)
    for raw in profile.get("prod_app_roots") or []:
        p = Path(str(raw)).expanduser()
        if p not in out:
            out.append(p)
    return out


def project_is_prod_app(profile: dict | None = None) -> bool:
    profile = profile or read_profile()
    return any(_same_path(PROJECT_ROOT, p) for p in prod_app_candidates(profile))


def _is_linux_host() -> bool:
    return sys.platform.startswith("linux")


def _shared_default_root(profile: dict | None, child: str) -> Path | None:
    profile = profile or read_profile()
    mode = _clean_mode(profile.get("mode"))
    if mode == "local":
        return None
    if mode == "shared":
        return PROD_SHARED / child if PROD_SHARED.exists() else None
    if mode == "custom":
        return None
    if not PROD_SHARED.exists():
        return None
    shared_child = PROD_SHARED / child
    if project_is_prod_app(profile) or os.environ.get("FLOW_PROD") == "1":
        return shared_child
    if _is_linux_host() and shared_child.exists():
        return shared_child
    return None


def use_shared_defaults(profile: dict | None = None) -> bool:
    profile = profile or read_profile()
    return (
        _shared_default_root(profile, "DB") is not None
        or _shared_default_root(profile, "flow-data") is not None
    )


def local_data_root() -> Path:
    for cand in (PROJECT_ROOT / "flow-data", PROJECT_ROOT / "data" / "flow-data"):
        if cand.exists():
            return cand
    return PROJECT_ROOT / "data" / "flow-data"


def default_data_root(profile: dict | None = None) -> Path:
    profile = profile or read_profile()
    custom = str(profile.get("data_root") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        if p.exists():
            return p
    shared = _shared_default_root(profile, "flow-data")
    if shared is not None:
        return shared
    return local_data_root()


def default_db_root(profile: dict | None = None) -> Path:
    profile = profile or read_profile()
    custom = str(profile.get("db_root") or "").strip()
    if custom:
        p = Path(custom).expanduser()
        if p.exists():
            return p
    shared = _shared_default_root(profile, "DB")
    if shared is not None:
        return shared
    for cand in (PROJECT_ROOT / "data" / "Fab", PROJECT_ROOT / "Fab", PROJECT_ROOT / "data" / "DB", PROJECT_ROOT / "DB"):
        if cand.exists():
            return cand
    return PROJECT_ROOT / "data" / "Fab"


def snapshot() -> dict:
    profile = read_profile()
    return {
        "file": str(PROFILE_FILE),
        "mode": _clean_mode(profile.get("mode")),
        "data_root": str(profile.get("data_root") or ""),
        "db_root": str(profile.get("db_root") or ""),
        "prod_app_roots": list(profile.get("prod_app_roots") or []),
        "shared_exists": PROD_SHARED.exists(),
        "project_is_prod_app": project_is_prod_app(profile),
        "shared_defaults": use_shared_defaults(profile),
    }
