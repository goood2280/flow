"""core/paths.py - FabCanvas.ai path registry.

Brand note: env var names for APP/DATA roots (HOL_APP_ROOT / HOL_DATA_ROOT /
HOL_PROD) and the default deploy dir name 'holweb-*' are legacy HOL prefix —
kept unchanged so existing prod deployments + user shell configs keep working.

v8.3.0 change — data-root resolution was extracted to `core/roots.py`:
   - `db_root`        (Hive-flat source tree: FAB/VM/MASK/KNOB/INLINE/ET/YLD + wafer_maps/…)
   - `base_root`      (single-file rulebooks + wide parquet — matching_step.csv,
                       knob_ppid.csv, mask.csv, inline_*.csv, yld_shot_agg.csv,
                       dvc_rulebook.csv, features_*.parquet, _uniques.json)
   - `wafer_map_root` (JSON wafer-map library; defaults to db_root/wafer_maps)

New preferred env vars (see core/roots.py for full chain):
    FABCANVAS_DB_ROOT         — overrides db_root
    FABCANVAS_BASE_ROOT       — overrides base_root
    FABCANVAS_WAFER_MAP_ROOT  — overrides wafer_map_root
Legacy `HOL_DB_ROOT` is still honoured as a fallback for db_root so existing
prod shell profiles keep working; Base and wafer-map have no legacy equivalent
(new v8.3+ concepts).

Local dev:  env vars unset → paths resolve relative to project root

Production (사내 배포) — set (legacy names still work):
    HOL_APP_ROOT        = /config/work/holweb-fast-api
    HOL_DATA_ROOT       = /config/work/sharedworkspace/holweb-data
    FABCANVAS_DB_ROOT   = /config/work/sharedworkspace/DB
    FABCANVAS_BASE_ROOT = /config/work/sharedworkspace/Base   (new)

Auto-detection: if /config/work/sharedworkspace exists AND no env vars set, we
assume prod layout and bind there automatically. Local dev is unaffected.

Back-compat: `PATHS.db_root` / `PATHS.base_root` / `PATHS.wafer_map_root` are
properties that call `core.roots` on each access, so runtime-edited
admin_settings.json takes effect without a restart. All legacy call-sites
(`PATHS.db_root`) continue to work.
"""
import os
from pathlib import Path

from core.roots import (
    get_db_root as _get_db_root,
    get_base_root as _get_base_root,
    get_wafer_map_root as _get_wafer_map_root,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # → FabCanvas.ai/
_PROD_SHARED = Path("/config/work/sharedworkspace")
_PROD_APP    = Path("/config/work/holweb-fast-api")
# v8.8.19: 데이터/DB 자동 감지 완화 — `/config/work/sharedworkspace` 만 있어도
#   사용자 데이터(users/sessions/groups/informs/meetings/calendar/dashboards/…) 를
#   공유 경로에 저장한다. 기존엔 `/config/work/holweb-fast-api` 가 없으면 로컬
#   `./data/holweb-data` 로 떨어져 setup.py 재실행 때마다 DB 가 휘발되는 문제가 있었음.
#   앱 루트(`is_prod`) 판정은 여전히 둘 다 필요 — 배포 여부 플래그 semantic 유지.
_SHARED_EXISTS = _PROD_SHARED.exists()
_IS_PROD       = _SHARED_EXISTS and _PROD_APP.exists()


class _Paths:
    def __init__(self):
        default_app  = str(_PROD_APP)    if _IS_PROD else str(_PROJECT_ROOT)
        # v8.8.19: 사용자 데이터 공유 — sharedworkspace 만 있어도 holweb-data 바인딩.
        default_data = (
            str(_PROD_SHARED / "holweb-data")
            if _SHARED_EXISTS
            else str(_PROJECT_ROOT / "data" / "holweb-data")
        )
        self.app_root  = Path(os.environ.get("HOL_APP_ROOT",  default_app))
        self.data_root = Path(os.environ.get("HOL_DATA_ROOT", default_data))
        self.is_prod   = _IS_PROD or os.environ.get("HOL_PROD") == "1"

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
        """Single-file rulebooks + wide parquet (see core.roots.get_base_root)."""
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
        # Best-effort: ensure db_root exists if it's under our project
        # (local dev convenience). No-op for external/prod paths.
        try:
            db = _get_db_root()
            if str(db).startswith(str(_PROJECT_ROOT)):
                db.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
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
