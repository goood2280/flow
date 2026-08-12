"""core/roots.py — central resolver for flow data roots (v8.3.0).

Soft-landing root abstraction. New deployments should use FLOW_* names and the
resolver functions exported below.

Priority chain (first match wins):
  1. New env var:   FLOW_DB_ROOT
  2. admin_settings.json `data_roots.db` (runtime editable, optional)
  3. Shared default: /config/work/sharedworkspace/DB when running from the prod
     app root, when FLOW_PROD=1 is explicitly set, or on Linux when that shared
     DB child exists
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
import threading
import time
from pathlib import Path

from core import root_profile

logger = logging.getLogger("flow.roots")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent   # → flow/
_PROFILE = root_profile.read_profile()
_USE_SHARED_DEFAULTS = root_profile.use_shared_defaults(_PROFILE)
_IS_PROD = _USE_SHARED_DEFAULTS
_PROJECT_DB_ROOT_SUFFIXES = (
    Path("data") / "Fab",
    Path("data") / "DB",
    Path("Fab"),
    Path("DB"),
)

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


def _windows_drive_to_wsl_path(value: str) -> Path | None:
    raw = str(value or "").strip()
    if len(raw) < 3 or raw[1] != ":" or raw[2] not in ("\\", "/"):
        return None
    drive = raw[0].lower()
    tail = raw[3:].replace("\\", "/").lstrip("/")
    return Path("/mnt") / drive / tail


def _path_has_suffix(value: str, suffix: Path) -> bool:
    parts = [p for p in str(value or "").replace("\\", "/").split("/") if p]
    suffix_parts = list(suffix.parts)
    if len(parts) < len(suffix_parts):
        return False
    return [p.casefold() for p in parts[-len(suffix_parts):]] == [
        p.casefold() for p in suffix_parts
    ]


def _candidate_config_paths(value: str) -> list[Path]:
    raw = str(value or "").strip()
    if not raw:
        return []
    p = Path(raw).expanduser()
    candidates: list[Path] = []

    wsl_path = _windows_drive_to_wsl_path(raw)
    if wsl_path is not None:
        candidates.append(wsl_path)

    if p.is_absolute():
        candidates.append(p)
    else:
        # Relative paths in admin_settings.json are project-root relative, not
        # process-CWD relative. Keep the raw relative candidate as a final
        # compatibility fallback for older launch scripts.
        candidates.extend([_PROJECT_ROOT / p, p])

    # The checked-in local seed used to carry an absolute path to this checkout.
    # If that seed is copied to another checkout, map known DB-root suffixes
    # back onto the active project instead of warning on every resolver call.
    for suffix in _PROJECT_DB_ROOT_SUFFIXES:
        if _path_has_suffix(raw, suffix):
            candidates.append(_PROJECT_ROOT / suffix)

    out: list[Path] = []
    seen: set[str] = set()
    for cand in candidates:
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def _resolve_existing_config_path(value: str) -> Path | None:
    for cand in _candidate_config_paths(value):
        if cand.exists():
            return cand
    return None


def _is_admin_setting_value(key: str, value: str) -> bool:
    try:
        return (_read_admin_setting(key) or "") == (value or "")
    except Exception:
        return False


def _return_existing_or_default_admin_root(key: str, value: str | None, default_factory) -> Path:
    if value:
        resolved = _resolve_existing_config_path(value)
        if resolved is not None:
            return resolved
        if _is_admin_setting_value(key, value):
            logger.warning(f"admin_settings data_roots.{key} ignored because path does not exist: {value}")
    return default_factory()


# get_db_root() 결과 메모이즈.
#
# 이 함수는 runtime_roots 프로필과 admin_settings.json 을 **매 호출마다 열어서
# 파싱**했다. `PATHS.db_root` 는 property 라 접근 한 번이 곧 이 함수 한 번이고,
# splittable 의 `_base_root()`/`_db_base()` 가 그 위에 있다 — 요청 경로에서 수십
# 번 불리는 값이 매번 JSON 두 개를 읽었다. 실측 1회 1.32ms.
#
# 무효화는 2단이다. 바깥은 짧은 TTL(_DB_ROOT_TTL), 안쪽은 **두 설정 파일의
# (mtime, size)**. 모듈 docstring 이 "admin_settings.json takes effect without a
# restart" 를 약속하므로 재시작 없이 반영되는 성질은 유지해야 하고, 실제로 유지
# 된다 — 다만 반영까지 최대 _DB_ROOT_TTL 만큼 늦는다(관리자 저장 직후 화면을
# 새로고침하는 간격보다 훨씬 짧다). 즉시 반영이 필요하면 clear_root_cache().
_DB_ROOT_CACHE: dict[str, object] = {}
_DB_ROOT_CACHE_LOCK = threading.Lock()
# stat 조차 건너뛰는 창. 이 값이 곧 "관리자가 루트를 바꾼 뒤 반영까지의 최대 지연"
# 이다. 화면 조작 → 다음 요청 사이 간격보다 훨씬 짧아야 하므로 1초로 둔다
# (splittable 의 _VIEW_GLOBAL_SIG_TTL 과 같은 값·같은 이유).
_DB_ROOT_TTL = 1.0


def _config_stat_sig(path: Path) -> tuple:
    try:
        st = path.stat()
        return (str(path), st.st_mtime, st.st_size)
    except Exception:
        return (str(path), 0.0, 0)


def _db_root_env_sig() -> tuple:
    """I/O 없이 읽을 수 있는 입력 전부 — TTL 로 가리지 않고 매번 확인한다.

    env 뿐 아니라 이 모듈/root_profile 의 전역까지 넣는다. 해석 결과는 이 값들에
    도 달려 있는데, 파일 stat 만 보고 있으면 전역이 바뀌어도 캐시가 눈치채지
    못한다 — 실제로 tests/test_roots.py 가 이 방식으로 샌드박스를 만들고, 캐시를
    처음 넣었을 때 그 두 테스트가 깨져서 발견했다. 전부 속성 읽기라 비용이 없다.
    """
    profile = _PROFILE if isinstance(_PROFILE, dict) else {}
    return (
        os.environ.get("FLOW_DB_ROOT") or "",
        os.environ.get("FLOW_DATA_ROOT") or "",
        os.environ.get("FLOW_PROD") or "",
        str(_PROJECT_ROOT),
        str(getattr(root_profile, "PROJECT_ROOT", "")),
        str(getattr(root_profile, "PROFILE_FILE", "")),
        str(getattr(root_profile, "PROD_SHARED", "")),
        str(profile.get("mode") or ""),
        str(profile.get("data_root") or ""),
        str(profile.get("db_root") or ""),
    )


def _db_root_file_sig() -> tuple:
    """설정 파일 부분 — stat 이 드는 쪽이라 TTL 안에서는 건너뛴다."""
    try:
        profile_file = root_profile.PROFILE_FILE
    except Exception:
        profile_file = Path("")
    return (
        _config_stat_sig(profile_file),
        _config_stat_sig(_admin_settings_path()),
    )


def clear_root_cache() -> None:
    """루트 설정을 코드에서 바꿨을 때(테스트·관리자 저장 직후) 즉시 비운다."""
    with _DB_ROOT_CACHE_LOCK:
        _DB_ROOT_CACHE.clear()


def get_db_root() -> Path:
    """Resolve the DB (Hive-flat) root directory.

    Chain: FLOW_DB_ROOT → admin_settings.data_roots.db → prod/local
           auto-detect → repo default.

    설정 파일이 그대로면 앞선 결과를 재사용한다 (위 _DB_ROOT_CACHE 주석 참고).
    """
    now = time.monotonic()
    env_sig = _db_root_env_sig()
    with _DB_ROOT_CACHE_LOCK:
        # 짧은 창 안에서는 파일 stat 을 건너뛴다. 이 함수는 한 요청에서 수십 번
        # 불리고 그 사이에 관리자가 루트를 바꿀 수는 없다. env 는 읽기가 공짜라
        # 이 창에서도 매번 확인한다 — 안 그러면 테스트/기동 스크립트가 env 를
        # 바꿔도 최대 TTL 동안 옛 루트가 나온다.
        fresh = (now - float(_DB_ROOT_CACHE.get("checked_at") or 0.0)) < _DB_ROOT_TTL
        if fresh and "value" in _DB_ROOT_CACHE and _DB_ROOT_CACHE.get("env") == env_sig:
            return _DB_ROOT_CACHE["value"]  # type: ignore[return-value]

    file_sig = _db_root_file_sig()
    with _DB_ROOT_CACHE_LOCK:
        if (
            "value" in _DB_ROOT_CACHE
            and _DB_ROOT_CACHE.get("env") == env_sig
            and _DB_ROOT_CACHE.get("files") == file_sig
        ):
            _DB_ROOT_CACHE["checked_at"] = now
            return _DB_ROOT_CACHE["value"]  # type: ignore[return-value]
    value = _get_db_root_uncached()
    with _DB_ROOT_CACHE_LOCK:
        _DB_ROOT_CACHE["env"] = env_sig
        _DB_ROOT_CACHE["files"] = file_sig
        _DB_ROOT_CACHE["value"] = value
        _DB_ROOT_CACHE["checked_at"] = now
    return value


def _get_db_root_uncached() -> Path:
    v = os.environ.get("FLOW_DB_ROOT")
    if v:
        resolved = _resolve_existing_config_path(v)
        return resolved if resolved is not None else Path(v).expanduser()
    profile_db = str(root_profile.read_profile().get("db_root") or "").strip()
    if profile_db:
        p = _resolve_existing_config_path(profile_db)
        if p is not None:
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
        resolved = _resolve_existing_config_path(admin_wm)
        if resolved is not None:
            return resolved
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
