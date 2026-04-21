"""core/roots.py — central resolver for FabCanvas data roots (v8.3.0).

Soft-landing env var abstraction. Existing deploys keep working because the
legacy `HOL_*` chain is preserved as a fallback. New code should prefer the
`FABCANVAS_*` names and the resolver functions exported below.

Priority chain (first match wins), per root:
  1. New env vars:  FABCANVAS_DB_ROOT   / FABCANVAS_BASE_ROOT / FABCANVAS_WAFER_MAP_ROOT
  2. Legacy env:    HOL_DB_ROOT         (DB only — no legacy equivalent for Base/wafer_map)
  3. admin_settings.json `data_roots` block (runtime editable, optional)
  4. Prod auto-detect: /config/work/sharedworkspace/DB   (+ sibling /Base)
  5. Repo default:  <PROJECT_ROOT>/data/DB   (+ /data/Base)

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

logger = logging.getLogger("fabcanvas.roots")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # → FabCanvas.ai/
_PROD_SHARED = Path("/config/work/sharedworkspace")
_PROD_APP    = Path("/config/work/holweb-fast-api")
_IS_PROD     = _PROD_SHARED.exists() and _PROD_APP.exists()

# Where admin.py writes runtime overrides. core/roots.py read-only peeks.
#
# v8.7.0 bugfix: previously hardcoded to ``<project>/data/admin_settings.json``
# which DID NOT MATCH admin.py's write target (which uses PATHS.data_root,
# i.e. ``data/holweb-data/admin_settings.json`` by default, or whatever
# HOL_DATA_ROOT env is set to). That mismatch caused "데이터 루트 저장 → 적용
# 안 됨" — the file persisted fine but the resolver never saw it. We now mirror
# the same HOL_DATA_ROOT → prod → default chain that core/paths.py uses.
def _admin_settings_path() -> Path:
    env = os.environ.get("HOL_DATA_ROOT")
    if env:
        return Path(env) / "admin_settings.json"
    if _IS_PROD:
        return _PROD_SHARED / "holweb-data" / "admin_settings.json"
    return _PROJECT_ROOT / "data" / "holweb-data" / "admin_settings.json"


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
    if _IS_PROD:
        return _PROD_SHARED / "DB"
    return _PROJECT_ROOT / "data" / "DB"


def _default_base_root() -> Path:
    if _IS_PROD:
        return _PROD_SHARED / "Base"
    return _PROJECT_ROOT / "data" / "Base"


def get_db_root() -> Path:
    """Resolve the DB (Hive-flat) root directory.

    Chain: FABCANVAS_DB_ROOT → HOL_DB_ROOT → admin_settings.data_roots.db →
           prod auto-detect → repo default.
    """
    v = os.environ.get("FABCANVAS_DB_ROOT") or os.environ.get("HOL_DB_ROOT")
    if not v:
        v = _read_admin_setting("db")
    if v:
        return Path(v)
    return _default_db_root()


def get_base_root() -> Path:
    """Resolve the Base (single-file rulebook + wide parquet) root directory.

    Chain: FABCANVAS_BASE_ROOT → admin_settings.data_roots.base →
           prod auto-detect → repo default.
    (No legacy HOL_BASE_ROOT — Base is a post-v8.3 split.)
    """
    v = os.environ.get("FABCANVAS_BASE_ROOT")
    if not v:
        v = _read_admin_setting("base")
    if v:
        return Path(v)
    return _default_base_root()


def get_wafer_map_root() -> Path:
    """Resolve the wafer_maps root directory.

    Chain: FABCANVAS_WAFER_MAP_ROOT → admin_settings.data_roots.wafer_map →
           <db_root>/wafer_maps (current hive-flat layout).
    """
    v = os.environ.get("FABCANVAS_WAFER_MAP_ROOT")
    if not v:
        v = _read_admin_setting("wafer_map")
    if v:
        return Path(v)
    return get_db_root() / "wafer_maps"


def snapshot() -> dict:
    """Return a plain-dict snapshot of all roots (for /admin or logging)."""
    return {
        "db_root":        str(get_db_root()),
        "base_root":      str(get_base_root()),
        "wafer_map_root": str(get_wafer_map_root()),
        "is_prod":        _IS_PROD or os.environ.get("HOL_PROD") == "1",
        "admin_settings": str(_admin_settings_path()),
    }
