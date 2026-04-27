"""core/roots.py — central resolver for flow data roots (v8.3.0).

Soft-landing root abstraction. New deployments should use FLOW_* names and the
resolver functions exported below.

Priority chain (first match wins):
  1. New env var:   FLOW_DB_ROOT
  2. admin_settings.json `data_roots.db` (runtime editable, optional)
  3. Shared default: /config/work/sharedworkspace/DB when running from the prod
     app root, when FLOW_PROD=1 is explicitly set, or on Linux when
     /config/work/sharedworkspace exists
  4. Repo default:  <PROJECT_ROOT>/data/Fab, then <PROJECT_ROOT>/data/DB

`base_root` is now a compatibility alias to `db_root`. Single-file rulebooks,
ML_TABLE parquet files, and generated CSV files live at the DB root level.

`wafer_map_root` has one extra nuance — if unset at every tier, it resolves to
`<db_root>/wafer_maps` (current hive-flat layout). Callers that want to keep
wafer_maps co-located with DB don't need to set anything.

NOTE: This module intentionally keeps a separate SETTINGS_FILE resolver that
does NOT depend on `core.paths`, so the import graph stays one-way
(paths → roots, never the reverse) and admin-settings lookup can't be
circular-blocked during boot.
"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path

from core import root_profile

logger = logging.getLogger("flow.roots")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # → flow/
_PROFILE = root_profile.read_profile()
_USE_SHARED_DEFAULTS = root_profile.use_shared_defaults(_PROFILE)
_IS_PROD = _USE_SHARED_DEFAULTS

# Where admin.py writes runtime overrides. core/roots.py read-only peeks.
#
# v8.7.0 bugfix: previously hardcoded to ``<project>/data/admin_settings.json``
# which DID NOT MATCH admin.py's write target (which uses PATHS.data_root,
# i.e. ``data/flow-data/admin_settings.json`` by default, or whatever
# FLOW_DATA_ROOT env is set to). That mismatch caused "데이터 루트 저장 → 적용
# 안 됨" — the file persisted fine but the resolver never saw it. We now mirror
# the same FLOW_DATA_ROOT → prod-default → local-default chain that
# core/paths.py uses.
def _admin_settings_path() -> Path:
    env = os.environ.get("FLOW_DATA_ROOT")
    if env:
        return Path(env) / "admin_settings.json"
    return root_profile.default_data_root(_PROFILE) / "admin_settings.json"


def _read_admin_setting(key: str) -> str | None:
    """Peek `admin_settings.json → data_roots[<key>]`. Returns None if missing."""
    try:
        p = _admin_settings_path()
        if not p.is_file():
            return None
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        v = (cfg.get("data_roots") or {}).get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception as e:
        logger.warning(f"admin_settings.json read failed ({key}): {e}")
    return None


def _default_db_root() -> Path:
    # Local/demo default must be the active app-shaped root. `data/DB` is the
    # source seed layout and does not contain the root-level ML_TABLE/rulebook
    # files the Flow app reads at runtime.
    return root_profile.default_db_root(_PROFILE)


def _is_admin_setting_value(key: str, value: str) -> bool:
    try:
        return (_read_admin_setting(key) or "") == (value or "")
    except Exception:
        return False


def _return_existing_or_default_admin_root(key: str, value: str | None, default_factory) -> Path:
    if value:
        if _is_admin_setting_value(key, value) and not Path(value).exists():
            logger.warning(f"admin_settings data_roots.{key} ignored because path does not exist: {value}")
        else:
            return Path(value)
    return default_factory()


def get_db_root() -> Path:
    """Resolve the DB (Hive-flat) root directory.

    Chain: FLOW_DB_ROOT → admin_settings.data_roots.db → prod/local
           auto-detect → repo default.
    """
    v = os.environ.get("FLOW_DB_ROOT")
    if v:
        return Path(v)
    profile_db = str(root_profile.read_profile().get("db_root") or "").strip()
    if profile_db:
        p = Path(profile_db).expanduser()
        if p.exists():
            return p
        logger.warning(f"runtime_roots db_root ignored because path does not exist: {profile_db}")
    return _return_existing_or_default_admin_root("db", _read_admin_setting("db"), _default_db_root)


def get_base_root() -> Path:
    """Compatibility alias.

    Older call-sites still use PATHS.base_root / source_type=base_file for
    rulebooks and single-file parquet. Operationally there is only one root:
    DB root. Root-level files under DB are treated as these "base_file" sources.
    """
    return get_db_root()


def get_wafer_map_root() -> Path:
    """Resolve the wafer_maps root directory.

    Chain: FLOW_WAFER_MAP_ROOT → admin_settings.data_roots.wafer_map →
           <db_root>/wafer_maps (current hive-flat layout).
    """
    v = os.environ.get("FLOW_WAFER_MAP_ROOT")
    if v:
        return Path(v)
    admin_wm = _read_admin_setting("wafer_map")
    if admin_wm:
        if Path(admin_wm).exists():
            return Path(admin_wm)
        logger.warning(f"admin_settings data_roots.wafer_map ignored because path does not exist: {admin_wm}")
    return get_db_root() / "wafer_maps"


def snapshot() -> dict:
    """Return a plain-dict snapshot of all roots (for /admin or logging)."""
    return {
        "db_root":        str(get_db_root()),
        "base_root":      str(get_base_root()),
        "wafer_map_root": str(get_wafer_map_root()),
        "is_prod":        _IS_PROD or os.environ.get("FLOW_PROD") == "1",
        "admin_settings": str(_admin_settings_path()),
        "shared_defaults": _USE_SHARED_DEFAULTS,
        "root_profile":   root_profile.snapshot(),
    }
