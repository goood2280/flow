"""core/paths.py - flow path registry.

Deployment note: use FLOW_* environment variables only. data_root is treated as
operator-owned state and must survive code updates.

v8.3.0 change — data-root resolution was extracted to `core/roots.py`.
Current operating model:
   - `db_root`        (the single source root)
   - `base_root`      (compatibility alias to db_root; root-level files such as
                       matching_step.csv, ML_TABLE_*.parquet, features_*.parquet)
   - `wafer_map_root` (compatibility helper; defaults to db_root/wafer_maps)

New preferred env vars (see core/roots.py for full chain):
    FLOW_DB_ROOT         — overrides db_root

Local dev:  env vars unset → DB root resolves to data/Fab under project root

Production (사내 배포):
    FLOW_APP_ROOT        = /config/work/flow-fast-api
    FLOW_DATA_ROOT       = /config/work/sharedworkspace/flow-data
    FLOW_DB_ROOT         = /config/work/sharedworkspace/DB

Auto-detection: sharedworkspace defaults are used when this checkout is the
production app root, FLOW_PROD=1 is explicitly set, or the app is running on a
Linux host where the relevant /config/work/sharedworkspace/{DB,flow-data}
directory already exists.

Compatibility: `PATHS.db_root` / `PATHS.base_root` / `PATHS.wafer_map_root` are
properties that call `core.roots` on each access, so runtime-edited
admin_settings.json takes effect without a restart. `base_root` intentionally
returns the same path as `db_root`.
"""
import os
from pathlib import Path

from core import root_profile
from core.roots import (
    get_db_root as _get_db_root,
    get_base_root as _get_base_root,
    get_wafer_map_root as _get_wafer_map_root,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # → flow/
_PROFILE = root_profile.read_profile()
_PROD_APP = next((p for p in root_profile.prod_app_candidates(_PROFILE) if p.exists()), root_profile.prod_app_candidates(_PROFILE)[0])
_USE_SHARED_DEFAULTS = root_profile.use_shared_defaults(_PROFILE)
_IS_PROD = _USE_SHARED_DEFAULTS


class _Paths:
    def __init__(self):
        default_app  = str(_PROD_APP)    if _IS_PROD else str(_PROJECT_ROOT)
        default_data = str(root_profile.default_data_root(_PROFILE))
        self.app_root  = Path(os.environ.get("FLOW_APP_ROOT",  default_app))
        self.data_root = Path(os.environ.get("FLOW_DATA_ROOT", default_data))
        self.is_prod   = _IS_PROD or os.environ.get("FLOW_PROD") == "1"

        # Sub-paths (data-root derived)
        self.log_dir        = self.data_root / "logs"
        self.users_csv      = self.data_root / "users.csv"
        self.shares_json    = self.data_root / "shares.json"
        self.activity_log   = self.data_root / "logs" / "activity.jsonl"
        self.download_log   = self.data_root / "logs" / "downloads.jsonl"
        self.resource_log   = self.data_root / "logs" / "resource.jsonl"
        self.upload_dir     = self.data_root / "uploads"
        self.cache_dir      = self.data_root / "cache"
        self._ensure_dirs()

    # ── Data-root properties (resolver-backed, runtime-editable) ──────────
    @property
    def db_root(self) -> Path:
        """Hive-flat DB tree (resolver-backed, see core.roots.get_db_root)."""
        return _get_db_root()

    @property
    def base_root(self) -> Path:
        """Compatibility alias for DB root (see core.roots.get_base_root)."""
        return _get_base_root()

    @property
    def wafer_map_root(self) -> Path:
        """Wafer map JSON library (see core.roots.get_wafer_map_root)."""
        return _get_wafer_map_root()

    def _ensure_dirs(self):
        # db_root / base_root intentionally excluded — they're read-mostly
        # sources maintained outside the app, and creating them empty would
        # mask a misconfiguration. Log dir / cache dir / upload dir are ours.
        # v8.8.19: data_root 자체도 명시 생성 — 공유 경로 첫 실행시 보장.
        self.data_root.mkdir(parents=True, exist_ok=True)
        for d in [self.log_dir, self.upload_dir, self.cache_dir]:
            d.mkdir(parents=True, exist_ok=True)
        # Do not create db_root here. The DB root is operator-owned source data;
        # creating an empty fallback directory can hide a misconfigured real DB.
        if not self.users_csv.exists():
            self.users_csv.write_text(
                "username,password_hash,role,status,created,tabs\n", encoding="utf-8")
        if not self.shares_json.exists():
            self.shares_json.write_text("{}", encoding="utf-8")

    def __repr__(self):
        return (f"Paths(\n  app_root        = {self.app_root}\n"
                f"  data_root       = {self.data_root}\n"
                f"  db_root         = {self.db_root}\n"
                f"  base_root       = {self.base_root}\n"
                f"  wafer_map_root  = {self.wafer_map_root}\n)")


PATHS = _Paths()
