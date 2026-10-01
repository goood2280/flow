"""LOT_WF current FAB progress cache.

The cache is intentionally file-backed so SplitTable, Inform, Tracker, and
agents can read the same current-lot position without rescanning FAB parquet
for every UI request.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from core.paths import PATHS

logger = logging.getLogger("flow.lot_progress_cache")

LOT_PROGRESS_DEFAULT_SOURCE_ROOTS = ("1.RAWDATA_DB", "FAB", "1.RAWDATA_DB_FAB")
CACHE_VERSION = 1
# 진행 문구 최소 간격(초) — 파일마다 던지면 이벤트 로그가 진행률로 뒤덮인다.
_PROGRESS_MIN_INTERVAL_SEC = 20.0
CACHE_REFRESH_MINUTES_DEFAULT = 30
CACHE_REFRESH_MINUTES_MIN = 1
CACHE_REFRESH_MINUTES_MAX = 1440
SOURCE_ROOT_SETTING_KEY = "lot_progress_source_root"
COLUMN_MAPPING_SETTING_KEY = "lot_progress_column_mapping"
LOT_PROGRESS_CANONICAL_COLUMNS = (
    "root_lot_id", "lot_id", "wafer_id", "step_id", "process_id",
    "tkin_time", "tkout_time", "time", "update_time", "eqp_id", "chamber_id", "ppid", "lot_type",
)
DEFAULT_LOT_PROGRESS_COLUMN_MAPPING = {col: col for col in LOT_PROGRESS_CANONICAL_COLUMNS}
FUNCTION_STEP_SOURCE_COLUMNS = (
    "function_step",
    "func_step",
    "func step",
    "canonical_step",
    "step_function",
    "step_desc",
    "step description",
    "step_description",
)
STEP_MAPPING_FILENAMES = (
    "Vehicle_matching.csv",
    "vehicle_matching.csv",
    "step_matching.csv",
    "matching_step.csv",
    "step_function.csv",
)

# Keep the published cache snapshot independent from the long-running source
# refresh.  A refresh can scan FAB parquet for minutes; readers must keep using
# the last complete snapshot during that work instead of waiting for it.
_CACHE_LOCK = threading.RLock()
_CACHE_REFRESH_LOCK = threading.Lock()
_CACHE_STATE: dict | None = None
_CACHE_INDEX: dict | None = None
_CACHE_INDEX_KEY: tuple[int, str, int] | None = None
_CACHE_STARTED = False
_CACHE_STOP = threading.Event()
_CACHE_THREAD: threading.Thread | None = None
_CACHE_RUNNING = False
_CACHE_LAST_SKIPPED_BY_LOCK = False
# _CACHE_STATE 가 담고 있는 캐시 파일 세대 (mtime_ns, size). 파일이 이 세대 그대로면
# 신선도 확인 때 수십 MB JSON 을 다시 읽고 인덱스를 다시 만들지 않는다.
_CACHE_FILE_SIG: tuple[int, int] | None = None


def _cache_dir() -> Path:
    path = PATHS.cache_dir / "lot_progress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_file() -> Path:
    return _cache_dir() / "lot_wf_current.json"


def cache_parquet_file() -> Path:
    return _cache_dir() / "lot_wf_current.parquet"


def filebrowser_cache_parquet_file() -> Path:
    fp = PATHS.db_cache_dir / "lot_progress_latest_lot_by_root_wafer.parquet"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def metadata() -> dict:
    """Static operational metadata for the FileBrowser LOT progress cache."""
    column_mapping = lot_progress_column_mapping()
    return {
        "product_binding": {
            "rule": "product는 FAB DB root 바로 아래 제품 폴더명으로 고정합니다.",
            "example_path_shape": "<db_root>/<effective_db_root>/<product>/.../*.parquet",
            "source_column": "product_dir.name",
            "code_location": "backend/core/lot_progress_cache.py product folder rule",
        },
        "latest_key_columns": ["product", "LOT_WF(root_lot_id + wafer_id)"],
        "latest_order_columns": ["update_time", "tkout_time", "tkin_time", "time"],
        "lot_id_source_column": column_mapping.get("lot_id", "lot_id"),
        "root_lot_id_source_column": column_mapping.get("root_lot_id", "root_lot_id"),
        "wafer_id_source_column": f"{column_mapping.get('wafer_id', 'wafer_id')} (normalized, e.g. W01/#01 -> 1)",
        "column_mapping_setting": f"settings.json.{COLUMN_MAPPING_SETTING_KEY}",
        "column_mapping": column_mapping,
        "column_mapping_defaults": dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING),
        "step_mapping_sources": list(STEP_MAPPING_FILENAMES),
        "function_step_source_columns": [
            "step matching CSV",
            *FUNCTION_STEP_SOURCE_COLUMNS,
        ],
        "manual_change_points": {
            "db_root": "settings.json.lot_progress_source_root",
            "column_mapping": f"settings.json.{COLUMN_MAPPING_SETTING_KEY}",
            "product_binding": "backend/core/lot_progress_cache.py product folder rule",
            "latest_rule": "backend/core/lot_progress_cache.py _sort_time and latest key creation",
            "step_mapping": "root-level matching CSV files",
        },
    }


def lot_status_cache_file() -> Path:
    fp = PATHS.data_root / "tracker" / "lot_status_cache.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def refresh_lock_file() -> Path:
    fp = PATHS.data_root / "locks" / "lot_progress_cache.lock"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def refresh_log_file() -> Path:
    fp = PATHS.data_root / "logs" / "lot_progress_cache_refresh.jsonl"
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _append_refresh_log(entry: dict) -> None:
    try:
        fp = refresh_log_file()
        row = {
            "ts": _now_iso(),
            "pid": os.getpid(),
            "host": socket.gethostname(),
            **(entry or {}),
        }
        with fp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("LOT progress refresh log write failed: %s", exc)


def _read_refresh_log(limit: int = 500) -> list[dict]:
    fp = refresh_log_file()
    if not fp.is_file():
        return []
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    except Exception:
        return []
    out: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _latest_refresh_log(status: str | None = None) -> dict | None:
    for row in reversed(_read_refresh_log()):
        if status is None or str(row.get("status") or "") == status:
            return row
    return None


def _parse_iso_seconds(value: str) -> dt.datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


# 크로스 서버/프로세스 refresh 단일 실행 보장은 shared_lease 로 한다.
# 이전 fcntl.flock 방식은 Windows 에서 no-op(이중 실행 허용)이었고, 죽은
# 소유자의 stale lock 을 회수하는 TTL 도 없었다. lease 는 TTL 만료 시 탈취된다.
_REFRESH_LEASE_NAME = "lot_progress_cache_refresh"
_REFRESH_LEASE_TTL_SEC = 1800.0


def _try_acquire_refresh_lock():
    """Return (handle, owner). handle 이 None 이면 다른 프로세스/서버가 보유 중."""
    try:
        from core import shared_lease
    except Exception:  # noqa: BLE001 — lease 불가 시 단일 프로세스 가정으로 진행
        return _REFRESH_LEASE_NAME, ""
    if shared_lease.try_acquire(_REFRESH_LEASE_NAME, ttl_sec=_REFRESH_LEASE_TTL_SEC):
        return _REFRESH_LEASE_NAME, ""
    return None, shared_lease.holder(_REFRESH_LEASE_NAME)


def _renew_refresh_lock() -> None:
    try:
        from core import shared_lease
        shared_lease.renew(_REFRESH_LEASE_NAME, ttl_sec=_REFRESH_LEASE_TTL_SEC)
    except Exception:  # noqa: BLE001
        pass


def _release_refresh_lock(handle) -> None:
    if handle is None:
        return
    try:
        from core import shared_lease
        shared_lease.release(_REFRESH_LEASE_NAME)
    except Exception:  # noqa: BLE001
        pass


def _lock_state() -> dict:
    fp = refresh_lock_file()
    if _CACHE_RUNNING:
        return {"locked": True, "running": True, "path": str(fp), "owner": ""}
    owner = ""
    try:
        from core import shared_lease
        owner = shared_lease.holder(_REFRESH_LEASE_NAME)
    except Exception:  # noqa: BLE001
        owner = ""
    locked = bool(owner)
    return {"locked": locked, "running": locked, "path": str(fp), "owner": owner}


_SCAN_SLICE_TLS = threading.local()


def _scan_yield_setting(name: str, default: float, upper: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(0.0, min(upper, value))


def _yield_scan_slice() -> None:
    """FAB 풀 스캔이 사용자 요청/메모리와 경합하지 않도록 일정 시간마다 양보한다.

    예전에는 파일마다 양보했다(대형 서버 1회 최대 3초). 사용자가 끊이지 않는 운영 서버에서는
    수십 ms 짜리 파일마다 3초를 쉬어 FAB 파일이 수만 개면 WIP 갱신이 몇 시간 걸렸고, 그동안
    조회 레인 슬롯과 제품 순환을 붙들었다. 이제 1초 훑을 때마다 최대 0.5초 양보한다
    (`FLOW_LOT_PROGRESS_YIELD_EVERY_SEC`·`FLOW_LOT_PROGRESS_YIELD_MAX_WAIT_SEC`). 사용자 활동이
    없으면 즉시 반환한다. 메모리가 실제로 부족하면 잠시 쉬며 사용자 요청이 먼저 처리될 시간을 준다.
    """
    now = time.monotonic()
    last = getattr(_SCAN_SLICE_TLS, "last", None)
    if last is None:
        _SCAN_SLICE_TLS.last = now
    elif now - last >= _scan_yield_setting("FLOW_LOT_PROGRESS_YIELD_EVERY_SEC", 1.0, 60.0):
        try:
            from core import request_priority
            request_priority.yield_to_users(
                max_wait_sec=_scan_yield_setting("FLOW_LOT_PROGRESS_YIELD_MAX_WAIT_SEC", 0.5, 10.0))
        except Exception:  # noqa: BLE001
            pass
        _SCAN_SLICE_TLS.last = time.monotonic()
    try:
        from core.runtime_limits import process_memory_high
        if process_memory_high():
            time.sleep(1.0)
    except Exception:  # noqa: BLE001
        pass


def _freshness_state(last_success_at: str, *, running: bool = False, error: bool = False) -> str:
    if running:
        return "running"
    parsed = _parse_iso_seconds(last_success_at)
    if parsed is None:
        return "error" if error else "never"
    age = (dt.datetime.now() - parsed).total_seconds()
    return "ok" if age <= 6 * 3600 else "stale"


def _state_with_runtime(state: dict | None, *, skipped_by_lock: bool = False, error: bool = False) -> dict:
    out = dict(state or {})
    latest_attempt = _latest_refresh_log()
    latest_success = _latest_refresh_log("success")
    last_attempt_at = _safe_text((latest_attempt or {}).get("ts") or (latest_attempt or {}).get("started_at"))
    last_success_at = _safe_text((latest_success or {}).get("ts") or (latest_success or {}).get("generated_at"))
    if not last_success_at:
        last_success_at = _safe_text(out.get("generated_at"))
    lock_state = _lock_state()
    running = bool(_CACHE_RUNNING or lock_state.get("running"))
    out.update({
        "last_success_at": last_success_at,
        "last_attempt_at": last_attempt_at,
        "freshness_state": _freshness_state(last_success_at, running=running, error=error),
        "refresh_log_path": str(refresh_log_file()),
        "lock_state": lock_state,
        "running": running,
        "skipped_by_lock": bool(skipped_by_lock or _CACHE_LAST_SKIPPED_BY_LOCK),
        "row_count": int(out.get("count") or len(out.get("items") or []) or 0),
    })
    return out


def lot_progress_cache_refresh_minutes() -> int:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    if isinstance(data, dict):
        raw = data.get(
            "lot_progress_refresh_minutes",
            data.get("splittable_match_refresh_minutes", CACHE_REFRESH_MINUTES_DEFAULT),
        )
    else:
        raw = CACHE_REFRESH_MINUTES_DEFAULT
    try:
        value = int(raw)
    except Exception:
        value = CACHE_REFRESH_MINUTES_DEFAULT
    return max(CACHE_REFRESH_MINUTES_MIN, min(CACHE_REFRESH_MINUTES_MAX, value))


def lot_progress_cache_refresh_seconds() -> int:
    return max(60, lot_progress_cache_refresh_minutes() * 60)


def lot_progress_cache_source_root() -> str:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return ""
    raw = data.get(SOURCE_ROOT_SETTING_KEY, "")
    return _clean_source_root_hint(raw)


def _clean_column_mapping_name(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"[\x00\r\n\t]+", " ", text)
    return text[:120].strip()


def normalize_lot_progress_column_mapping(value=None) -> dict[str, str]:
    mapping = dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING)
    if not isinstance(value, dict):
        return mapping
    for canonical in LOT_PROGRESS_CANONICAL_COLUMNS:
        raw = value.get(canonical)
        clean = _clean_column_mapping_name(raw)
        if clean:
            mapping[canonical] = clean
    return mapping


def lot_progress_column_mapping() -> dict[str, str]:
    settings_path = PATHS.data_root / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.is_file() else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        return dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING)
    return normalize_lot_progress_column_mapping(data.get(COLUMN_MAPPING_SETTING_KEY))


def _safe_text(value) -> str:
    if value is None:
        return ""
    try:
        text = str(value)
    except Exception:
        return ""
    if text.lower() in {"nan", "nat", "none", "null"}:
        return ""
    return text.strip()


def _norm_key(value) -> str:
    return _safe_text(value).upper()


def _product_cell_keys(value) -> list[str]:
    text = _norm_key(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[,，、]", text) if part.strip()]


def _norm_wafer(value) -> str:
    text = _safe_text(value).upper()
    if not text:
        return ""
    core = re.sub(r"^(?:#|WAFER|WF|W)\s*", "", text, flags=re.I).strip()
    try:
        number = float(core)
    except Exception:
        return text
    if number.is_integer():
        return str(int(number))
    return text


def _sort_time(row: dict) -> str:
    return _safe_text(row.get("update_time") or row.get("tkout_time") or row.get("tkin_time") or row.get("time"))


def _cache_index_key(state: dict) -> tuple[int, str, int]:
    items = state.get("items") or []
    return (id(items), _safe_text(state.get("generated_at")), int(state.get("count") or len(items) or 0))


def _add_index_value(target: dict[str, list[dict]], key: str, item: dict) -> None:
    if key:
        target.setdefault(key, []).append(item)


def _build_cache_index(state: dict | None) -> dict:
    items = [item for item in (state or {}).get("items") or [] if isinstance(item, dict)]
    rows = sorted(items, key=_sort_time, reverse=True)
    index = {
        "all": rows,
        "by_product": {},
        "by_lot_id": {},
        "by_root_lot_id": {},
        "by_wafer_id": {},
        "by_lot_wf": {},
        "products": [],
    }
    products: dict[str, str] = {}
    for item in rows:
        product = _norm_key(item.get("product"))
        process_id = _norm_key(item.get("process_id"))
        lot_id = _norm_key(item.get("lot_id"))
        root_lot_id = _norm_key(item.get("root_lot_id"))
        wafer_id = _norm_wafer(item.get("wafer_id"))
        lot_wf = _norm_key(item.get("lot_wf"))
        for key in {product, process_id}:
            _add_index_value(index["by_product"], key, item)
        for key in {lot_id, root_lot_id}:
            _add_index_value(index["by_lot_id"], key, item)
        _add_index_value(index["by_root_lot_id"], root_lot_id, item)
        _add_index_value(index["by_wafer_id"], wafer_id, item)
        _add_index_value(index["by_lot_wf"], lot_wf, item)
        if product and product not in products:
            products[product] = _safe_text(item.get("product"))
    index["products"] = sorted((v for v in products.values() if v), key=lambda value: value.lower())
    return index


def _set_cache_state(state: dict) -> None:
    global _CACHE_STATE, _CACHE_INDEX, _CACHE_INDEX_KEY
    index = _build_cache_index(state)
    index_key = _cache_index_key(state)
    with _CACHE_LOCK:
        _CACHE_STATE = state
        _CACHE_INDEX = index
        _CACHE_INDEX_KEY = index_key


def _cache_index_for(state: dict) -> dict:
    global _CACHE_INDEX, _CACHE_INDEX_KEY
    key = _cache_index_key(state)
    with _CACHE_LOCK:
        if _CACHE_INDEX is not None and _CACHE_INDEX_KEY == key:
            return _CACHE_INDEX
    index = _build_cache_index(state)
    with _CACHE_LOCK:
        if _CACHE_STATE is not None and _cache_index_key(_CACHE_STATE) == key:
            _CACHE_INDEX = index
            _CACHE_INDEX_KEY = key
    return index


def _cache_state_fresh(state: dict, max_age_seconds: int) -> bool:
    # verified_at = 원천 지문이 같아서 다시 만들 필요가 없다고 확인한 시각(메모리 전용).
    # 확인 뒤에는 새로 만든 것과 같으므로 나이를 그 시각부터 센다.
    ages = []
    for key in ("generated_at", "verified_at"):
        stamp = _safe_text((state or {}).get(key))
        if not stamp:
            continue
        try:
            ages.append((dt.datetime.now() - dt.datetime.fromisoformat(stamp)).total_seconds())
        except Exception:
            continue
    return bool(ages) and min(ages) <= max_age_seconds


def _state_db_root_matches(state: dict | None) -> bool:
    if not isinstance(state, dict):
        return False
    stored = _safe_text(state.get("db_root"))
    if not stored:
        return True
    try:
        return str(Path(stored).resolve()).casefold() == str(PATHS.db_root.resolve()).casefold()
    except Exception:
        return stored == str(PATHS.db_root)


def _fresh_existing_cache_state(
    cache_path: Path,
    source_root_hint: str,
    column_mapping: dict,
    max_age_seconds: int,
) -> dict | None:
    def _usable(state: dict) -> bool:
        return (
            not state.get("errors")
            and cache_path.is_file()
            and cache_parquet_file().is_file()
            and not _source_recheck_due(state)
            and _cache_state_fresh(state, max_age_seconds)
            and _state_db_root_matches(state)
            and _state_source_root_matches(state, source_root_hint)
            and _state_column_mapping_matches(state, column_mapping)
        )

    # 메모리 상태가 이미 쓸 만하면 파일을 다시 읽거나 인덱스를 다시 만들지 않는다.
    # 예전에는 제품 순환이 제품마다 이 함수를 불러, 매번 JSON 전체 파싱 + 전 항목 정렬·
    # 색인을 GIL 을 쥔 채 반복했다(대형 서버에서 사용자 요청이 그동안 멈춘다).
    current = _CACHE_STATE
    if isinstance(current, dict) and _usable(current):
        return dict(current)
    loaded, sig = _load_cache_file_state(cache_path)
    if isinstance(loaded, dict) and loaded is not current and _usable(loaded):
        _adopt_file_state(loaded, sig)
        return dict(loaded)
    return None


_STATE_JSON_CHUNK_ITEMS = 500


def _write_state_json(state: dict, path: Path) -> None:
    """json.dumps(state) 와 같은 바이트를 쓰되 항목을 묶음별로 직렬화한다.

    compact json.dumps 는 C 인코더가 끝날 때까지 GIL 을 놓지 않는다 — 4.5만 항목에서 다른
    요청이 0.2초 멈췄고, 항목이 수십만인 대형 서버에서는 수 초 동안 서버 전체가 멈춘다.
    묶음 사이마다 다른 스레드가 GIL 을 받는다. items 가 상태의 마지막 키일 때 출력이 같다."""
    items = state.get("items")
    if not isinstance(items, list) or list(state)[-1:] != ["items"]:
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return
    head = {key: value for key, value in state.items() if key != "items"}
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(head, ensure_ascii=False)[:-1])
        fh.write(', "items": [' if head else '"items": [')
        for start in range(0, len(items), _STATE_JSON_CHUNK_ITEMS):
            if start:
                fh.write(", ")
            fh.write(json.dumps(items[start:start + _STATE_JSON_CHUNK_ITEMS], ensure_ascii=False)[1:-1])
        fh.write("]}")


def _cache_file_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (int(st.st_mtime_ns), int(st.st_size))


def _load_cache_file_state(cache_path: Path) -> tuple[dict | None, tuple[int, int] | None]:
    """캐시 파일을 읽는다. 메모리 상태와 같은 세대면 다시 읽지 않고 메모리 상태를 돌려준다."""
    sig = _cache_file_sig(cache_path)
    if sig is None:
        return None, None
    if _CACHE_STATE is not None and sig == _CACHE_FILE_SIG:
        return _CACHE_STATE, sig
    try:
        loaded = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None, sig
    return (loaded if isinstance(loaded, dict) else None), sig


def _adopt_file_state(state: dict, sig: tuple[int, int] | None) -> None:
    global _CACHE_FILE_SIG
    _set_cache_state(state)
    _CACHE_FILE_SIG = sig


def _lot_progress_source_digest(source_root_hint: str, column_mapping: dict | None) -> str:
    """WIP 결과를 정하는 입력 전체의 지문 — FAB 원천 파일, ML_TABLE(root 화이트리스트),
    step matching, 열 매핑, 캐시 형식, 앱 버전. 같으면 다시 훑어도 같은 결과다."""
    from core import source_digest as sd

    db_root = PATHS.db_root
    roots = [str(info["path"]) for info in _fab_source_roots(db_root, source_root_hint)]
    try:
        from core import ml_table_lookup as _mlt
        ml_files = [str(p) for p in (_mlt._discover_ml_table_files() or [])]
    except Exception:
        ml_files = []
    return sd.combine({
        "version": CACHE_VERSION,
        "db_root": str(db_root),
        "source_root": source_root_hint,
        "mapping": json.dumps(normalize_lot_progress_column_mapping(column_mapping), sort_keys=True),
        "fab": sd.tree_digest(roots, suffixes=(".parquet",))["digest"],
        "inputs": sd.files_digest([*(str(p) for p in _step_matching_paths()), *ml_files]),
        "app": sd.files_digest([sd.app_version_path()]),
    })


def _sources_changed_since(state: dict, source_root_hint: str, column_mapping: dict) -> bool:
    """state 를 만든 뒤 원천 지문이 바뀌었는가. 지문이 없는 옛 캐시·꺼짐·실패는 False(예전 동작)."""
    try:
        from core import source_digest as sd
        if not sd.change_driven_enabled():
            return False
    except Exception:
        return False
    stored = _safe_text((state or {}).get("source_digest"))
    if not stored:
        return False
    try:
        return _lot_progress_source_digest(source_root_hint, column_mapping) != stored
    except Exception:
        logger.debug("lot_progress source digest failed", exc_info=True)
        return False


def _source_recheck_due(state: dict) -> bool:
    """Limit digest reuse by the actual build time, never by verified_at."""
    from core import source_digest as sd
    if not sd.change_driven_enabled():
        return False
    try:
        built = dt.datetime.fromisoformat(str(state.get("generated_at") or ""))
        return (dt.datetime.now() - built).total_seconds() >= sd.full_recheck_sec()
    except (TypeError, ValueError):
        return True


def _verified_unchanged_state(cache_path: Path, source_root_hint: str,
                              column_mapping: dict) -> dict | None:
    """나이로는 낡았지만 원천 지문이 같은 캐시를 '확인됨'으로 되살린다.

    지문이 같으면 FAB 전체를 다시 훑어도 같은 결과이므로 재스캔하지 않는다. 필요한
    제품이 캐시에 없더라도 마찬가지다 — 예전에는 FAB 행이 없는 제품(ML_TABLE 만 있는
    데모 제품 등)이 순환에 걸릴 때마다 FAB 전체 재스캔을 반복했다.
    """
    try:
        from core import source_digest as sd
        if not sd.change_driven_enabled():
            return None
    except Exception:
        return None
    current = _CACHE_STATE
    candidate, sig = (current, _CACHE_FILE_SIG) if isinstance(current, dict) else (None, None)
    if not isinstance(candidate, dict) or not candidate.get("source_digest"):
        candidate, sig = _load_cache_file_state(cache_path)
    if not isinstance(candidate, dict) or not candidate.get("source_digest"):
        return None
    if (candidate.get("errors") or _source_recheck_due(candidate)
            or not cache_path.is_file() or not cache_parquet_file().is_file()):
        return None
    if not (
        _state_db_root_matches(candidate)
        and _state_source_root_matches(candidate, source_root_hint)
        and _state_column_mapping_matches(candidate, column_mapping)
    ):
        return None
    try:
        live = _lot_progress_source_digest(source_root_hint, column_mapping)
    except Exception:
        logger.debug("lot_progress source digest failed", exc_info=True)
        return None
    if live != candidate.get("source_digest"):
        return None
    if candidate is not _CACHE_STATE:
        _adopt_file_state(candidate, sig)
    candidate["verified_at"] = _now_iso()
    _append_refresh_log({
        "status": "success",
        "verified_unchanged": True,
        "generated_at": candidate.get("generated_at"),
        "row_count": int(candidate.get("count") or len(candidate.get("items") or []) or 0),
        "source_roots": list(candidate.get("source_roots") or []),
    })
    return dict(candidate)


def _state_has_products(state: dict | None, required_products: Iterable[str] | None) -> bool:
    # 제품 순환은 ML_TABLE_PRODA 로 묻고 캐시 행은 FAB 이름 PRODA 를 담는다. 예전엔 이름이
    # 안 맞아 방금 만든 캐시도 늘 "제품 없음" 이었고, 제품마다 FAB 전체 재스캔을 반복하며
    # 공용 캐시 슬롯을 붙들었다.
    from core.latest_lot_cache_format import normalize_product

    required = {normalize_product(value) for value in (required_products or []) if normalize_product(value)}
    if not required:
        return True
    present = {
        normalize_product(row.get("product"))
        for row in ((state or {}).get("items") or [])
        if isinstance(row, dict) and normalize_product(row.get("product"))
    }
    return required.issubset(present)


def _empty_cache_state() -> dict:
    return {
        "version": CACHE_VERSION,
        "generated_at": "",
        "count": 0,
        "items": [],
        "cache_file": str(cache_file()),
    }


def _lot_status_time(row: dict) -> str:
    return _safe_text(row.get("update_time") or row.get("time") or row.get("last_checked_at") or row.get("last_move_at") or row.get("tkout_time") or row.get("tkin_time"))


def _lot_status_key(row: dict) -> tuple[str, str, str]:
    return (
        _norm_key(row.get("root_lot_id")),
        _norm_key(row.get("lot_id")),
        _norm_key(row.get("wafer_id")),
    )


def _tracker_status_row(row: dict, *, source: str = "tracker") -> dict | None:
    if not isinstance(row, dict):
        return None
    lot_id = _safe_text(row.get("lot_id") or row.get("fab_lot_id") or row.get("root_lot_id"))
    if not lot_id:
        return None
    wafer_id = _norm_wafer(row.get("wafer_id"))
    if not wafer_id:
        return None
    step_id = _safe_text(
        row.get("step_id")
        or row.get("current_step")
        or row.get("current_step_id")
    )
    function_step = _safe_text(
        row.get("function_step")
        or row.get("current_function_step")
        or row.get("func_step")
        or row.get("et_last_function_step")
        or ""
    )
    time_value = _lot_status_time(row)
    return {
        "root_lot_id": _safe_text(row.get("root_lot_id")),
        "wafer_id": wafer_id,
        "lot_id": lot_id,
        "step_id": step_id,
        "func_step": function_step,
        "update_time": time_value,
    }


def _load_tracker_lot_status_state() -> dict:
    fp = lot_status_cache_file()
    if not fp.is_file():
        return {"version": CACHE_VERSION, "generated_at": "", "items": [], "count": 0}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return {"version": CACHE_VERSION, "generated_at": "", "items": [], "count": 0}


def upsert_tracker_lot_status_rows(rows: list[dict], source: str = "tracker") -> dict:
    fp = lot_status_cache_file()
    state = _load_tracker_lot_status_state()
    merged: dict[tuple[str, str, str], dict] = {}
    for row in state.get("items") or []:
        row_norm = _tracker_status_row(dict(row), source="tracker")
        if row_norm is None:
            continue
        merged[_lot_status_key(row_norm)] = row_norm
    for row in rows or []:
        row_norm = _tracker_status_row(dict(row), source=source)
        if row_norm is None:
            continue
        key = _lot_status_key(row_norm)
        current = merged.get(key)
        if current is None or _lot_status_time(row_norm) >= _lot_status_time(current):
            merged[key] = row_norm
    out = sorted(
        merged.values(),
        key=lambda row: (_lot_status_time(row), _norm_key(row.get("root_lot_id")), _norm_key(row.get("lot_id")), _wafer_sort_value(row.get("wafer_id"))),
        reverse=True,
    )
    state = {
        "version": CACHE_VERSION,
        "generated_at": _now_iso(),
        "count": len(out),
        "items": out,
    }
    tmp = fp.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    from core.file_transaction import replace_file
    replace_file(tmp, fp)
    return state


def _save_tracker_lot_status_cache(rows: list[dict], source: str = "tracker") -> dict:
    return upsert_tracker_lot_status_rows(rows, source=source)


def _wafer_sort_value(value) -> int:
    try:
        return int(_norm_wafer(value) or 0)
    except Exception:
        return 999999


def compress_wafer_ids(values) -> str:
    numeric: list[int] = []
    labels: list[str] = []
    seen_labels: set[str] = set()
    for value in values or []:
        wafer = _norm_wafer(value)
        if not wafer:
            continue
        if re.fullmatch(r"\d+", wafer):
            numeric.append(int(wafer))
            continue
        key = wafer.upper()
        if key not in seen_labels:
            seen_labels.add(key)
            labels.append(wafer)
    numbers = sorted(set(numeric))
    parts: list[str] = []
    idx = 0
    while idx < len(numbers):
        start = numbers[idx]
        end = start
        while idx + 1 < len(numbers) and numbers[idx + 1] == end + 1:
            idx += 1
            end = numbers[idx]
        parts.append(str(start) if start == end else f"{start}~{end}")
        idx += 1
    parts.extend(labels)
    return f"#{','.join(parts)}" if parts else ""


def _lot_progress_parquet_rows(state: dict) -> list[dict]:
    generated_at = _safe_text((state or {}).get("generated_at"))
    rows: list[dict] = []
    for item in (state or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        lot_id = _safe_text(item.get("lot_id"))
        function_step = _safe_text(item.get("function_step") or item.get("func_step"))
        rows.append({
            "product": _safe_text(item.get("product")),
            "root_lot_id": _safe_text(item.get("root_lot_id")),
            "wafer_id": _norm_wafer(item.get("wafer_id")),
            "lot_id": lot_id,
            "step_id": _safe_text(item.get("step_id")),
            "function_step": function_step,
            "tkout_time": _safe_text(item.get("tkout_time")),
            "update_time": generated_at,
            "lot_type": _safe_text(item.get("lot_type")),
        })
    rows.sort(key=lambda row: (_norm_key(row.get("product")), _norm_key(row.get("root_lot_id")), _wafer_sort_value(row.get("wafer_id"))))
    return rows


def _lot_progress_parquet_frame(rows: list[dict]):
    import polars as pl  # type: ignore

    columns = [
        "product", "root_lot_id", "wafer_id", "lot_id",
        "step_id", "function_step", "tkout_time", "update_time", "lot_type",
    ]
    if rows:
        return pl.DataFrame(rows).select(columns)
    return pl.DataFrame({col: [] for col in columns})


def _write_lot_progress_parquet(target: Path, df) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    df.write_parquet(tmp)
    from core.file_transaction import replace_file
    replace_file(tmp, target)


def export_lot_progress_parquet(state: dict | None = None) -> dict:
    """Export the scanner-owned compatibility parquet.

    The DB/cache canonical latest-lot file is owned exclusively by the
    SplitTable FAB matching exporter. Keeping this scanner output internal
    prevents two schedulers from overwriting one cache with different product
    naming. Existing legacy files are deliberately not deleted.
    """
    if state is None:
        fp = cache_file()
        if fp.is_file():
            try:
                state = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                state = None
        if not isinstance(state, dict):
            state = load_lot_progress_cache()
    rows = _lot_progress_parquet_rows(state or {})
    df = _lot_progress_parquet_frame(rows)
    # 대시보드 wip-split 이 읽는 lot_wf_current.parquet 도 같은 내용으로 export 한다.
    paths = [cache_parquet_file()]
    written: list[str] = []
    for target in paths:
        _write_lot_progress_parquet(target, df)
        written.append(str(target))
    # SplitTable root 검색이 읽는 per-root 파티션을 같은 쓰기 시점에 동기화한다.
    # 내용이 같은 재-export 는 meta 갱신만으로 끝나고, 실패해도 monolithic 폴백이
    # 있으므로 export 자체는 성공으로 처리한다.
    return {
        "ok": True,
        "rows": len(rows),
        "paths": written,
        "canonical_owned_by": "splittable_match_cache",
        "canonical_path": str(filebrowser_cache_parquet_file()),
    }


def _step_matching_paths() -> list[Path]:
    roots = []
    for root in (PATHS.db_root, PATHS.base_root, PATHS.data_root / "Fab"):
        try:
            p = Path(root)
        except Exception:
            continue
        if p not in roots:
            roots.append(p)
    out: list[Path] = []
    for root in roots:
        for name in STEP_MAPPING_FILENAMES:
            path = root / name
            if path not in out:
                out.append(path)
    return out


def _row_ci(row: dict, *names: str):
    lookup = {str(k or "").strip().lower(): v for k, v in (row or {}).items()}
    for name in names:
        key = str(name or "").strip().lower()
        if key in lookup:
            value = lookup.get(key)
            if _safe_text(value):
                return value
    return ""


def load_step_matching(*, errors: list[str] | None = None) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    by_product: dict[tuple[str, str], str] = {}
    by_step: dict[str, str] = {}
    for path in _step_matching_paths():
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    products = _product_cell_keys(_row_ci(row, "product", "process_id", "prod"))
                    step_id = _norm_key(_row_ci(row, "step_id", "raw_step_id", "step"))
                    function_step = _safe_text(_row_ci(row, *FUNCTION_STEP_SOURCE_COLUMNS))
                    if not step_id or not function_step:
                        continue
                    for product in products:
                        by_product[(product, step_id)] = function_step
                    by_step.setdefault(step_id, function_step)
        except Exception as exc:
            logger.warning("step matching load failed: %s (%s)", path, exc)
            if errors is not None:
                errors.append(f"step matching load failed: {path}: {exc}")
    return by_product, by_step


_FAB_PROGRESS_COLUMNS = list(LOT_PROGRESS_CANONICAL_COLUMNS)


def _mapped_fab_progress_columns(column_mapping: dict | None = None) -> list[str]:
    mapping = normalize_lot_progress_column_mapping(column_mapping)
    out: list[str] = []
    seen: set[str] = set()
    for canonical in _FAB_PROGRESS_COLUMNS:
        source = _clean_column_mapping_name(mapping.get(canonical) or canonical)
        if source and source not in seen:
            seen.add(source)
            out.append(source)
    for source in FUNCTION_STEP_SOURCE_COLUMNS:
        if source and source not in seen:
            seen.add(source)
            out.append(source)
    return out


def _resolve_available_fab_progress_columns(names: Iterable[str], requested: Iterable[str]) -> list[str]:
    exact = {str(name): str(name) for name in names}
    folded = {str(name).casefold(): str(name) for name in names}
    out: list[str] = []
    seen: set[str] = set()
    for column in requested:
        text = str(column or "")
        actual = exact.get(text) or folded.get(text.casefold())
        if actual and actual not in seen:
            seen.add(actual)
            out.append(actual)
    return out


def _available_fab_progress_columns(path: Path, column_mapping: dict | None = None) -> list[str]:
    mapped_columns = _mapped_fab_progress_columns(column_mapping)
    try:
        import polars as pl  # type: ignore
        schema = pl.read_parquet_schema(str(path))
        names = schema.keys() if hasattr(schema, "keys") else schema
        return _resolve_available_fab_progress_columns(names, mapped_columns)
    except Exception:
        pass
    try:
        import pyarrow.parquet as pq  # type: ignore
        names = pq.ParquetFile(str(path)).schema.names
        return _resolve_available_fab_progress_columns(names, mapped_columns)
    except Exception:
        return mapped_columns


def _fill_missing_progress_columns(row: dict, column_mapping: dict | None = None, *,
                                   normalized: bool = False) -> dict:
    # 행마다 매핑을 다시 정규화하면(컬럼명 13개 정리) FAB 행 수 × 13 번 regex 가 돈다.
    mapping = column_mapping if normalized and column_mapping else normalize_lot_progress_column_mapping(column_mapping)
    raw = dict(row or {})
    lower_lookup = {str(key).casefold(): value for key, value in raw.items()}
    out: dict = {}
    for canonical in _FAB_PROGRESS_COLUMNS:
        source = mapping.get(canonical) or canonical
        if source in raw:
            out[canonical] = raw.get(source)
        else:
            out[canonical] = lower_lookup.get(str(source).casefold())
    for source in FUNCTION_STEP_SOURCE_COLUMNS:
        if source in raw:
            out[source] = raw.get(source)
        else:
            value = lower_lookup.get(str(source).casefold())
            if value is not None:
                out[source] = value
    return out


# FAB parquet 스트리밍 배치 크기 (row). 너무 크면 메모리 스파이크, 너무 작으면
# 오버헤드 — row-group 하나 정도인 6.4만 행이 무난. env 로 조절 가능.
def _fab_read_batch_rows() -> int:
    try:
        v = int(os.environ.get("FLOW_LOT_PROGRESS_READ_BATCH_ROWS", "") or 64000)
    except Exception:
        v = 64000
    return max(1000, min(1_000_000, v))


_FAB_READ_BATCH_ROWS = _fab_read_batch_rows()


def _ml_table_root_lot_ids(*, errors: list[str] | None = None) -> set[str]:
    """ML_TABLE_*.parquet 들의 root_lot_id(정규화) 합집합.

    lot_progress(LOT_WF 현재위치) 캐시를 **ML_TABLE 에 실제 존재하는 root** 로만
    한정하기 위한 화이트리스트. FAB DB 전체(ML_TABLE 밖 root 포함)를 메모리에
    올리던 것을 이 집합에 드는 root 로만 좁혀 refresh 피크 메모리를 줄인다.

    root_lot_id 컬럼만 lazy scan → unique 로 읽으므로 값 컬럼은 만지지 않는다.
    ML_TABLE 을 하나도 못 찾거나 실패하면 빈 set → 호출측이 필터를 생략(안전한
    전량 스캔 폴백)한다.
    """
    roots: set[str] = set()
    try:
        import polars as pl  # type: ignore
    except Exception:
        return roots
    try:
        from core import ml_table_lookup as _mlt
        files = _mlt._discover_ml_table_files()
    except Exception as exc:
        logger.warning("ML_TABLE 파일 탐색 실패 (lot_progress root 화이트리스트): %s", exc)
        if errors is not None:
            errors.append(f"ML_TABLE discovery failed: {exc}")
        return roots
    for fp in files:
        try:
            schema = pl.read_parquet_schema(str(fp))
            names = list(schema.keys()) if hasattr(schema, "keys") else list(schema)
            col = next((c for c in names if str(c).strip().lower() == "root_lot_id"), None)
            if not col:
                continue
            values = (
                pl.scan_parquet(str(fp))
                .select(pl.col(col).cast(pl.Utf8))
                .unique()
                .collect()
                .to_series()
                .to_list()
            )
            for v in values:
                key = _norm_key(v)
                if key:
                    roots.add(key)
        except Exception as exc:
            logger.warning("ML_TABLE root_lot_id 수집 실패 %s: %s", fp, exc)
            if errors is not None:
                errors.append(f"ML_TABLE root read failed: {fp}: {exc}")
    return roots


def _read_parquet_rows(path: Path, column_mapping: dict | None = None) -> Iterable[dict]:
    mapping = normalize_lot_progress_column_mapping(column_mapping)
    columns = _available_fab_progress_columns(path, mapping)
    if not columns:
        return
    # row-group 단위 스트리밍 — FAB parquet 전체를 한 번에 메모리에 올리지 않는다
    # (대용량 FAB 파일에서 refresh 가 OOM 되던 주원인). pyarrow iter_batches 로
    # batch 씩만 메모리에 두고 소비 후 즉시 해제.
    try:
        import pyarrow.parquet as pq  # type: ignore
        pf = pq.ParquetFile(str(path))
        for batch in pf.iter_batches(batch_size=_FAB_READ_BATCH_ROWS, columns=columns):
            for row in batch.to_pylist():
                yield _fill_missing_progress_columns(row, mapping, normalized=True)
            del batch
        return
    except Exception:
        pass
    try:
        import polars as pl  # type: ignore
        df = pl.read_parquet(str(path), columns=columns)
        for row in df.iter_rows(named=True):
            yield _fill_missing_progress_columns(row, mapping, normalized=True)
        return
    except Exception:
        pass
    try:
        import pandas as pd  # type: ignore
        df = pd.read_parquet(str(path), columns=columns)
        for row in df.to_dict(orient="records"):
            yield _fill_missing_progress_columns(row, mapping, normalized=True)
    except Exception as exc:
        logger.warning("FAB parquet read failed: %s (%s)", path, exc)


# ── 최신 행 후보를 Polars 로 먼저 고른다 ───────────────────────────────────────
# refresh 는 FAB 모든 행을 파이썬 dict 로 꺼내 (제품, root_웨이퍼) 마다 가장 늦은 행만
# 남겼다. 시간의 90% 가 파이썬(GIL, 1코어)이었다. 여기서는 배치마다 그 "남을 행" 만
# Polars(다중 코어)로 골라 내고, 항목을 만드는 파이썬 코드는 그 행들에만 그대로 돈다.
# 선택 규칙은 파이썬 루프와 똑같다: 통과 조건(root·wafer·step 이 비지 않음), 키
# upper(root_wafer), 정렬 문자열 _sort_time(item) 최대, 같으면 먼저 나온 행. 파이썬
# str() 과 문자열 표현이 달라질 수 있는 타입(날짜·실수·불리언)이나 비ASCII 랏/웨이퍼가
# 섞인 배치는 줄이지 않고 전 행을 예전 경로로 넘긴다 — 결과가 달라질 여지를 두지 않는다.
# FLOW_LOT_PROGRESS_VECTOR=0 이면 항상 예전 행 단위 경로.
_PY_WHITESPACE = "".join(ch for ch in map(chr, range(0x3001)) if ch.isspace())
_SAFE_TEXT_BLANKS = ["nan", "nat", "none", "null"]


class _NotReducible(Exception):
    pass


def _vector_reduce_enabled() -> bool:
    return str(os.environ.get("FLOW_LOT_PROGRESS_VECTOR", "1") or "1").strip().lower() not in {
        "0", "false", "no", "off"}


def _canonical_sources(columns: list[str], mapping: dict) -> dict[str, str | None]:
    """_fill_missing_progress_columns 와 같은 규칙으로 canonical → 실제 컬럼명."""
    exact = set(columns)
    folded = {str(name).casefold(): name for name in columns}
    out: dict[str, str | None] = {}
    for canonical in _FAB_PROGRESS_COLUMNS:
        source = mapping.get(canonical) or canonical
        out[canonical] = source if source in exact else folded.get(str(source).casefold())
    return out


def _batch_latest_candidates(batch, sources: dict[str, str | None]):
    """배치에서 파이썬 루프가 끝내 남길 행 (번호, 키, 정렬문자열) 목록 — 키가 처음 나온 순서.

    키는 _norm_key(item["lot_wf"]), 정렬문자열은 _sort_time(item) 과 같다. 못 줄이면 None."""
    import polars as pl  # type: ignore

    df = pl.from_arrow(batch)
    schema = df.schema

    def text(canonical: str, *, allow_int: bool):
        source = sources.get(canonical)
        if source is None or source not in schema:
            return pl.lit(None, dtype=pl.Utf8)
        dtype = schema[source]
        if dtype in (pl.String, pl.Null) or isinstance(dtype, (pl.Categorical, pl.Enum)):
            return pl.col(source).cast(pl.Utf8)
        if allow_int and dtype.is_integer():
            return pl.col(source).cast(pl.Utf8)
        raise _NotReducible(f"{source}:{dtype}")

    def safe(expr):
        return (pl.when(expr.is_null() | expr.str.to_lowercase().is_in(_SAFE_TEXT_BLANKS))
                .then(pl.lit(""))
                .otherwise(expr.str.strip_chars(_PY_WHITESPACE)))

    def first_truthy(*exprs):
        # 파이썬 `a or b or c` — 빈 문자열·None 은 거짓, 모두 거짓이면 마지막 값.
        out = exprs[-1]
        for expr in reversed(exprs[:-1]):
            out = pl.when(expr.is_not_null() & (expr.str.len_chars() > 0)).then(expr).otherwise(out)
        return out

    try:
        root = safe(text("root_lot_id", allow_int=True))
        step = safe(text("step_id", allow_int=True))
        tkin_raw = text("tkin_time", allow_int=False)
        tkout_raw = text("tkout_time", allow_int=False)
        time_raw = text("time", allow_int=False)
        update_raw = text("update_time", allow_int=False)
        wafer_source = sources.get("wafer_id")
        if wafer_source is None or wafer_source not in schema:
            return [], [], []  # 웨이퍼가 없으면 어떤 행도 통과하지 못한다
        wafer_dtype = schema[wafer_source]
        wafer_col = pl.col(wafer_source)
        if isinstance(wafer_dtype, (pl.Categorical, pl.Enum)):
            wafer_col = wafer_col.cast(pl.Utf8)
        elif not (wafer_dtype in (pl.String, pl.Null) or wafer_dtype.is_integer()):
            raise _NotReducible(f"{wafer_source}:{wafer_dtype}")
    except _NotReducible:
        return None
    # 웨이퍼 표기는 종류가 적다 — 고유값에만 파이썬 _norm_wafer 를 그대로 적용한다.
    raw_wafers = [v for v in df.select(wafer_col.unique()).to_series().to_list() if v is not None]
    if raw_wafers:
        wafer = wafer_col.replace_strict(raw_wafers, [_norm_wafer(v) for v in raw_wafers],
                                         default=None, return_dtype=pl.Utf8).fill_null("")
    else:
        wafer = pl.lit("", dtype=pl.Utf8)
    tkin = safe(tkin_raw)
    tkout = safe(tkout_raw)
    time_item = safe(first_truthy(time_raw, tkout_raw, tkin_raw))
    update_item = safe(first_truthy(update_raw, tkout_raw, tkin_raw, time_raw))
    sort_time = safe(first_truthy(update_item, tkout, tkin, time_item))
    cand = (
        df.with_row_index("__idx")
        .select(
            pl.col("__idx"),
            root.alias("__root"),
            wafer.alias("__wafer"),
            step.alias("__step"),
            sort_time.alias("__sort"),
        )
        .filter((pl.col("__root") != "") & (pl.col("__wafer") != "") & (pl.col("__step") != ""))
    )
    if cand.height == 0:
        return [], [], []
    non_ascii = r"[^\x00-\x7F]"
    if cand.select((pl.col("__root").str.contains(non_ascii) | pl.col("__wafer").str.contains(non_ascii)).any()).item():
        return None  # upper() 규칙 차이가 날 수 있는 값 — 파이썬 경로가 판단한다
    best = (
        cand.with_columns((pl.col("__root") + "_" + pl.col("__wafer")).str.to_uppercase().alias("__key"))
        .group_by("__key")
        .agg(
            pl.col("__idx").sort_by(["__sort", "__idx"], descending=[True, False]).first().alias("__win"),
            pl.col("__sort").max().alias("__max"),
            pl.col("__idx").min().alias("__first"),
        )
        .sort("__first")
    )
    return (best.get_column("__win").to_list(), best.get_column("__key").to_list(),
            best.get_column("__max").to_list())


def _build_progress_item(raw: dict, product: str, root_name: str,
                         step_by_product: dict, step_by_id: dict) -> dict | None:
    """FAB 행 하나 → 캐시 항목. root·wafer·step 중 하나라도 비면 None (예전 루프 본문 그대로)."""
    root_lot_id = _safe_text(raw.get("root_lot_id"))
    lot_id = _safe_text(raw.get("lot_id"))
    wafer_id = _norm_wafer(raw.get("wafer_id"))
    step_id = _safe_text(raw.get("step_id"))
    if not (root_lot_id and wafer_id and step_id):
        return None
    process_id = _safe_text(raw.get("process_id"))
    product_key = _norm_key(product)
    step_key = _norm_key(step_id)
    function_step = (
        step_by_product.get((product_key, step_key))
        or step_by_product.get((_norm_key(process_id), step_key))
        or step_by_id.get(step_key)
        or _safe_text(_row_ci(raw, *FUNCTION_STEP_SOURCE_COLUMNS))
        or ""
    )
    lot_wf = f"{root_lot_id}_{wafer_id}"
    return {
        "product": product,
        "process_id": process_id,
        "root_lot_id": root_lot_id,
        "lot_id": lot_id,
        "wafer_id": wafer_id,
        "LOT_WF": lot_wf,
        "lot_wf": lot_wf,
        "step_id": step_id,
        "function_step": function_step,
        "func_step": function_step,
        "tkin_time": _safe_text(raw.get("tkin_time")),
        "tkout_time": _safe_text(raw.get("tkout_time")),
        "time": _safe_text(raw.get("time") or raw.get("tkout_time") or raw.get("tkin_time")),
        "update_time": _safe_text(raw.get("update_time") or raw.get("tkout_time") or raw.get("tkin_time") or raw.get("time")),
        "eqp_id": _safe_text(raw.get("eqp_id")),
        "chamber_id": _safe_text(raw.get("chamber_id")),
        "ppid": _safe_text(raw.get("ppid")),
        "lot_type": _safe_text(raw.get("lot_type")),
        "source_root": root_name,
    }


class _LatestRowReducer:
    """한 제품 폴더의 FAB 행을 받아 (root_웨이퍼) 마다 파이썬 루프가 남길 행만 모은다.

    파일·배치를 넘어 끝까지 줄인 뒤에야 행을 파이썬 dict 로 꺼낸다. 받은 순서대로
    병합하므로(더 늦으면 교체, 같으면 먼저 온 행, 키 순서는 처음 나온 순서) 모든 행을
    파이썬 루프에 넣은 것과 결과가 같다. 줄일 수 없는 배치의 행은 add_row 로 들어와
    그 자리에서 항목으로 만들어 같은 규칙에 끼운다."""

    def __init__(self, build: Callable[[dict], dict | None]):
        self._build = build
        self._best: dict[str, list] = {}   # key -> [sort, ref]; ref = (table_no, pos) | item dict
        self._tables: list = []

    def _merge(self, key: str, sort: str, ref) -> None:
        cur = self._best.get(key)
        if cur is None:
            self._best[key] = [sort, ref]
        elif sort > cur[0]:
            cur[0] = sort
            cur[1] = ref

    def add_batch(self, winners_table, keys: list[str], sorts: list[str], mapping: dict) -> None:
        # 표의 행은 파일의 실제 컬럼명 그대로다 — 꺼낼 때 mapping 으로 canonical 로 맞춘다.
        table_no = len(self._tables)
        self._tables.append((winners_table, mapping))
        for pos, (key, sort) in enumerate(zip(keys, sorts)):
            self._merge(key, sort, (table_no, pos))

    def add_row(self, raw: dict) -> None:
        item = self._build(raw)
        if item is not None:
            self._merge(_norm_key(item["lot_wf"]), _sort_time(item), item)

    def items(self) -> list[dict]:
        wanted: dict[int, list[int]] = {}
        for _sort, ref in self._best.values():
            if isinstance(ref, tuple):
                wanted.setdefault(ref[0], []).append(ref[1])
        rows: dict[tuple[int, int], dict] = {}
        for table_no, positions in wanted.items():
            import pyarrow as pa  # type: ignore

            table, mapping = self._tables[table_no]
            picked = table.take(pa.array(positions, type=pa.int64())).to_pylist()
            for pos, raw in zip(positions, picked):
                rows[(table_no, pos)] = _fill_missing_progress_columns(raw, mapping, normalized=True)
        self._tables = []
        out: list[dict] = []
        for _sort, ref in self._best.values():
            item = self._build(rows[ref]) if isinstance(ref, tuple) else ref
            if item is not None:
                out.append(item)
        return out


def _feed_parquet(reducer: _LatestRowReducer, path: Path, column_mapping: dict | None, stats: dict) -> None:
    """FAB parquet 한 개를 reducer 에 넣는다. stats["rows"] 에 읽은 행 수를 더한다(rows_seen)."""
    mapping = normalize_lot_progress_column_mapping(column_mapping)
    if _vector_reduce_enabled():
        columns = _available_fab_progress_columns(path, mapping)
        if not columns:
            return
        try:
            import pyarrow as pa  # type: ignore
            import pyarrow.parquet as pq  # type: ignore

            sources = _canonical_sources(columns, mapping)
            pf = pq.ParquetFile(str(path))
            for batch in pf.iter_batches(batch_size=_FAB_READ_BATCH_ROWS, columns=columns):
                stats["rows"] = stats.get("rows", 0) + batch.num_rows
                found = _batch_latest_candidates(batch, sources)
                if found is None:
                    stats["unreduced_batches"] = stats.get("unreduced_batches", 0) + 1
                    for row in batch.to_pylist():
                        reducer.add_row(_fill_missing_progress_columns(row, mapping, normalized=True))
                elif found[0]:
                    winners, keys, sorts = found
                    reducer.add_batch(batch.take(pa.array(winners, type=pa.int64())), keys, sorts, mapping)
                del batch
            return
        except Exception as exc:
            # 예전 경로로 다시 읽는다. 이미 넣은 행이 겹쳐도 병합 규칙상 결과는 같다.
            logger.debug("lot_progress vector reduce fell back for %s: %s", path, exc)
    for row in _read_parquet_rows(path, mapping):
        stats["rows"] = stats.get("rows", 0) + 1
        reducer.add_row(row)


def _fab_product_dirs(fab_root: Path) -> Iterable[Path]:
    if not fab_root.is_dir():
        return []
    try:
        return [p for p in fab_root.iterdir() if p.is_dir()]
    except Exception:
        return []


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _clean_source_root_hint(value: str = "") -> str:
    text = _safe_text(value).strip().strip("/\\")
    if not text:
        return ""
    if text.casefold() in {"auto", "default", "자동"}:
        return ""
    path = Path(text)
    if path.is_absolute():
        return str(path)
    parts = [part for part in re.split(r"[\\/]+", text) if part and part not in {".", ".."}]
    if not parts:
        return ""
    first = parts[0]
    first_upper = first.upper()
    if first_upper.startswith("1.RAWDATA_DB") or first_upper == "FAB":
        return first
    return text


def normalize_lot_progress_source_root(value: str = "") -> str:
    return _clean_source_root_hint(value)


def _candidate_source_root_path(db_root: Path, root_name: str) -> Path | None:
    name = _clean_source_root_hint(root_name)
    if not name:
        return None
    path = Path(name)
    if path.is_absolute():
        return path
    try:
        from app_v2.shared.source_adapter import resolve_named_child
        if "/" not in name and "\\" not in name:
            resolved = resolve_named_child(db_root, name)
            if resolved is not None and _path_key(resolved) != _path_key(db_root):
                return resolved
    except Exception:
        pass
    return db_root / name


def _resolve_fab_root(db_root: Path, root_name: str) -> Path | None:
    path = _candidate_source_root_path(db_root, root_name)
    return path if path is not None and path.is_dir() else None


def _source_root_name(path: Path, db_root: Path) -> str:
    try:
        rel = path.relative_to(db_root)
        if rel.parts:
            return "/".join(rel.parts)
    except Exception:
        pass
    return path.name or str(path)


def _has_product_parquet_dirs(root: Path) -> bool:
    for product_dir in _fab_product_dirs(root):
        try:
            next(product_dir.rglob("*.parquet"))
            return True
        except StopIteration:
            continue
        except Exception:
            continue
    return False


def lot_progress_source_root_candidates(db_root: Path | None = None, source_root: str = "") -> list[dict]:
    db_root = db_root or PATHS.db_root
    candidates: list[dict] = []
    seen: set[str] = set()

    def _add(path: Path | None, name: str, origin: str) -> None:
        if path is None:
            return
        exists = path.is_dir()
        key = _path_key(path) if exists else str(path).casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append({
            "source_root": _source_root_name(path, db_root) if exists else name,
            "path": str(path),
            "exists": bool(exists),
            "origin": origin,
        })

    hint = _clean_source_root_hint(source_root)
    if hint:
        _add(_candidate_source_root_path(db_root, hint), hint, "configured")
    for name in LOT_PROGRESS_DEFAULT_SOURCE_ROOTS:
        _add(_candidate_source_root_path(db_root, name), name, "auto")
    db_name = db_root.name
    db_upper = db_name.upper()
    if db_upper.startswith("1.RAWDATA_DB"):
        _add(db_root, db_name, "db_root")
    if not any(candidate.get("exists") for candidate in candidates) and _has_product_parquet_dirs(db_root):
        _add(db_root, db_name or str(db_root), "db_root")
    return candidates


def _fab_source_roots(db_root: Path, source_root: str = "") -> list[dict]:
    roots: list[dict] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None or not path.is_dir():
            return
        key = _path_key(path)
        if key in seen:
            return
        seen.add(key)
        roots.append({"path": path, "source_root": _source_root_name(path, db_root)})

    hint = _clean_source_root_hint(source_root)
    if hint:
        _add(_resolve_fab_root(db_root, hint))
        return roots
    for candidate in lot_progress_source_root_candidates(db_root):
        if candidate.get("exists"):
            _add(Path(str(candidate.get("path") or "")))
    return roots


def _fab_root_names_for_error(source_root: str = "") -> list[str]:
    names: list[str] = []
    hint = _clean_source_root_hint(source_root)
    if hint:
        names.append(hint)
        return names
    for name in LOT_PROGRESS_DEFAULT_SOURCE_ROOTS:
        if name not in names:
            names.append(name)
    return names


def _source_ref_matches(left: str, right: str) -> bool:
    a = _clean_source_root_hint(left)
    b = _clean_source_root_hint(right)
    if not a or not b:
        return False
    if a.casefold() == b.casefold():
        return True
    try:
        return str(Path(a).resolve()).casefold() == str(Path(b).resolve()).casefold()
    except Exception:
        return False


def _state_source_root_matches(state: dict | None, source_root: str = "") -> bool:
    hint = _clean_source_root_hint(source_root)
    if not hint:
        if not isinstance(state, dict):
            return True
        return not _clean_source_root_hint(state.get("configured_source_root", ""))
    if not isinstance(state, dict):
        return False
    candidates: list[str] = []
    for key in ("source_root", "configured_source_root"):
        value = state.get(key)
        if value:
            candidates.append(str(value))
    for key in ("source_roots", "fab_roots"):
        for value in state.get(key) or []:
            if value:
                candidates.append(str(value))
    return any(_source_ref_matches(candidate, hint) for candidate in candidates)


def _state_column_mapping_matches(state: dict | None, column_mapping: dict | None = None) -> bool:
    expected = normalize_lot_progress_column_mapping(column_mapping)
    if not isinstance(state, dict):
        return False
    stored = state.get("column_mapping")
    if not isinstance(stored, dict):
        return expected == dict(DEFAULT_LOT_PROGRESS_COLUMN_MAPPING)
    return normalize_lot_progress_column_mapping(stored) == expected


def refresh_lot_progress_cache(force: bool = False, source_root: str = "",
                               progress: Callable[[str], None] | None = None,
                               required_products: Iterable[str] | None = None) -> dict:
    """Rebuild the LOT_WF current-position cache from FAB parquet.

    progress: 진행 문구 콜백. 이 갱신은 FAB DB 전체를 훑어 수 분~수십 분 걸리는데
    예전에는 '갱신 중…' 한 줄만 남아 멈춘 것과 구분이 안 됐다. 제품 x/y 와 지금까지
    모은 랏 수를 주기적으로 흘려보낸다 (호출측이 heartbeat 로도 쓴다).
    """
    global _CACHE_STATE, _CACHE_RUNNING, _CACHE_LAST_SKIPPED_BY_LOCK
    source_root_hint = _clean_source_root_hint(source_root) or lot_progress_cache_source_root()
    column_mapping = lot_progress_column_mapping()
    # Serialize refresh workers without taking the snapshot lock.  Hot readers
    # continue serving _CACHE_STATE (or the last complete cache file) while the
    # new snapshot is constructed and atomically published at the end.
    with _CACHE_REFRESH_LOCK:
        cache_path = cache_file()
        max_age_seconds = lot_progress_cache_refresh_seconds()
        fresh_state = _fresh_existing_cache_state(cache_path, source_root_hint, column_mapping, max_age_seconds)
        # 제품 순환(force)은 원천이 바뀌어서 부른다 — 30분이 안 된 캐시라도 원천 지문이 다르면
        # 다시 만든다. 예전에는 나이만 보고 돌려줘서 FAB 가 바뀌어도 최대 30분 낡은 WIP 를 냈다.
        sources_changed = bool(
            force and fresh_state is not None
            and _sources_changed_since(fresh_state, source_root_hint, column_mapping)
        )
        if (fresh_state is not None and not sources_changed
                and _state_has_products(fresh_state, required_products)):
            _CACHE_LAST_SKIPPED_BY_LOCK = False
            fresh_state["skipped_recent_success"] = bool(force)
            return _state_with_runtime(fresh_state)
        verified_state = (None if sources_changed else
                          _verified_unchanged_state(cache_path, source_root_hint, column_mapping))
        if verified_state is not None:
            _CACHE_LAST_SKIPPED_BY_LOCK = False
            verified_state["skipped_recent_success"] = bool(force)
            verified_state["verified_unchanged"] = True
            return _state_with_runtime(verified_state)
        lock_fh, lock_owner = _try_acquire_refresh_lock()
        if lock_fh is None:
            _CACHE_LAST_SKIPPED_BY_LOCK = True
            _append_refresh_log({
                "status": "skipped_by_lock",
                "reason": "another refresh is running",
                "lock_owner": lock_owner,
            })
            state = _CACHE_STATE
            if state is None and cache_path.is_file():
                try:
                    loaded = json.loads(cache_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        state = loaded
                        _set_cache_state(loaded)
                except Exception:
                    state = None
            if state is None:
                state = {
                    "version": CACHE_VERSION,
                    "generated_at": "",
                    "cache_file": str(cache_path),
                    "count": 0,
                    "files_scanned": 0,
                    "rows_seen": 0,
                    "errors": ["refresh skipped because another process holds the cache lock"],
                    "items": [],
                }
            return _state_with_runtime(state, skipped_by_lock=True)

        _CACHE_RUNNING = True
        _CACHE_LAST_SKIPPED_BY_LOCK = False
        started_at = _now_iso()
        _append_refresh_log({
            "status": "started",
            "started_at": started_at,
            "force": bool(force),
            "source_root_hint": source_root_hint,
        })
        try:
            db_root = PATHS.db_root
            # 훑기 전에 입력 지문을 잡는다 — 훑는 동안 들어온 변경은 다음 확인에서 잡힌다.
            try:
                from core import source_digest as _sd
                source_digest = (_lot_progress_source_digest(source_root_hint, column_mapping)
                                 if _sd.change_driven_enabled() else "")
            except Exception:
                logger.debug("lot_progress source digest failed", exc_info=True)
                source_digest = ""
            fab_roots = _fab_source_roots(db_root, source_root_hint)
            errors: list[str] = []
            step_by_product, step_by_id = load_step_matching(errors=errors)
            latest: dict[tuple[str, str], dict] = {}
            files_scanned = 0
            rows_seen = 0
            rows_kept = 0
            # ML_TABLE 에 실제 존재하는 root_lot_id 만 유지 — FAB DB 전체를 메모리에
            # 올리던 것을 좁혀 refresh OOM 을 막는다. 집합이 비면(ML_TABLE 미발견)
            # 필터를 끄고 기존 전량 스캔으로 폴백한다.
            allowed_roots = _ml_table_root_lot_ids(errors=errors)
            if allowed_roots:
                logger.info("lot_progress refresh: ML_TABLE root %d 개로 스코프 한정", len(allowed_roots))

            if not fab_roots:
                tried = ", ".join(_fab_root_names_for_error(source_root_hint))
                errors.append(f"FAB rawdata root not found under {db_root}; tried {tried}")

            # 진행 표시 — 제품 폴더를 먼저 다 세어 분모를 만든다(폴더 목록은 얕은 조회).
            scan_plan: list[tuple[dict, list]] = []
            for root_info in fab_roots:
                product_dirs = list(_fab_product_dirs(root_info["path"]))
                if not product_dirs and len(errors) < 20:
                    errors.append(f"No product folders found under {root_info['path']}")
                scan_plan.append((root_info, product_dirs))
            product_total = sum(len(dirs) for _r, dirs in scan_plan)
            product_done = 0
            seen_roots: set[str] = set()
            lot_total = len(allowed_roots) if allowed_roots else 0
            last_progress_ts = 0.0

            def _emit_progress(force_emit: bool = False, state_override: str = "running") -> None:
                """진행 문구 — 최소 간격으로 던진다. 랏 분모는 ML_TABLE root 수."""
                nonlocal last_progress_ts
                now = time.monotonic()
                if not force_emit and now - last_progress_ts < _PROGRESS_MIN_INTERVAL_SEC:
                    return
                last_progress_ts = now
                lots = (f"랏 {len(seen_roots):,}/{lot_total:,}"
                        if lot_total and len(seen_roots) <= lot_total
                        else f"랏 {len(seen_roots):,}")
                try:
                    msg = f"제품 {product_done:,}/{product_total:,} · {lots} · 파일 {files_scanned:,}"
                    if progress is not None:
                        progress(msg)
                    from core.cache_event_log import record, progress_detail
                    record(
                        "cache_op",
                        f"WIP cache build: {msg}",
                        ok=True,
                        detail={
                            "progress": progress_detail("latest_lot", len(seen_roots), max(1, lot_total), state=state_override, unit="랏")
                        }
                    )
                except Exception:
                    logger.debug("lot_progress progress callback failed", exc_info=True)

            _emit_progress(force_emit=True)
            for root_info, product_dirs in scan_plan:
                fab_root = root_info["path"]
                root_name = root_info["source_root"]
                for product_dir in product_dirs:
                    # Product comes from the FAB DB product folder, not from a parquet column.
                    product = product_dir.name
                    _renew_refresh_lock()
                    reducer = _LatestRowReducer(
                        lambda raw, _product=product, _root_name=root_name: _build_progress_item(
                            raw, _product, _root_name, step_by_product, step_by_id))
                    for parquet in product_dir.rglob("*.parquet"):
                        files_scanned += 1
                        _yield_scan_slice()
                        _emit_progress()
                        read_stats: dict = {}
                        try:
                            _feed_parquet(reducer, parquet, column_mapping, read_stats)
                        except Exception as exc:
                            if len(errors) < 20:
                                errors.append(f"{parquet}: {exc}")
                        finally:
                            rows_seen += int(read_stats.get("rows") or 0)
                    # 제품 폴더 단위로 줄인 행만 예전과 같은 규칙으로 전역 병합한다.
                    for item in reducer.items():
                        seen_roots.add(item["root_lot_id"])
                        key = (_norm_key(product), _norm_key(item["lot_wf"]))
                        prev = latest.get(key)
                        if prev is None or _sort_time(item) > _sort_time(prev):
                            latest[key] = item
                    product_done += 1
                    _emit_progress(force_emit=True)

            if fab_roots and files_scanned == 0 and len(errors) < 20:
                roots_text = ", ".join(str(root["path"]) for root in fab_roots)
                errors.append(f"No FAB parquet files found under {roots_text}")

            items = sorted(
                latest.values(),
                key=lambda row: (_norm_key(row.get("product")), _norm_key(row.get("root_lot_id")), _wafer_sort_value(row.get("wafer_id"))),
            )
            source_roots = [root["source_root"] for root in fab_roots]
            fab_root_paths = [str(root["path"]) for root in fab_roots]
            source_root_candidates = lot_progress_source_root_candidates(db_root, source_root_hint)
            state = {
                "version": CACHE_VERSION,
                "generated_at": _now_iso(),
                "db_root": str(db_root),
                "fab_root": fab_root_paths[0] if fab_root_paths else "",
                "fab_roots": fab_root_paths,
                "configured_source_root": source_root_hint,
                "source_root": source_roots[0] if source_roots else "",
                "source_roots": source_roots,
                "effective_source_roots": source_roots,
                "source_root_candidates": source_root_candidates,
                "column_mapping": column_mapping,
                "cache_file": str(cache_path),
                "count": len(items),
                "files_scanned": files_scanned,
                "rows_seen": rows_seen,
                "errors": errors,
                # 읽기 오류가 있었던 세대는 지문을 남기지 않는다 — 같은 원천이라도 다시 훑게 한다.
                "source_digest": source_digest if not errors else "",
                "items": items,
            }
            _save_tracker_lot_status_cache(items, source="lot_progress_cache")
            tmp = cache_path.with_suffix(".tmp")
            # compact dump — 수만 item 상태에서 indent=2 는 직렬화 문자열 크기와
            # 피크 메모리를 2배 가까이 키운다. 사람이 읽는 파일이 아니므로 압축.
            _write_state_json(state, tmp)
            from core.file_transaction import replace_file
            replace_file(tmp, cache_path)
            try:
                export_lot_progress_parquet(state)
            except Exception as exc:
                logger.warning("LOT_WF parquet export failed: %s", exc)
            _adopt_file_state(state, _cache_file_sig(cache_path))
            _append_refresh_log({
                "status": "success",
                "started_at": started_at,
                "generated_at": state.get("generated_at"),
                "files_scanned": files_scanned,
                "rows_seen": rows_seen,
                "row_count": len(items),
                "errors": len(errors),
                "source_roots": source_roots,
            })
            _emit_progress(force_emit=True, state_override="done")
            _CACHE_RUNNING = False
            _release_refresh_lock(lock_fh)
            lock_fh = None
            return _state_with_runtime(state)
        except Exception as exc:
            _append_refresh_log({
                "status": "failure",
                "started_at": started_at,
                "error": f"{type(exc).__name__}: {exc}",
            })
            raise
        finally:
            _CACHE_RUNNING = False
            if lock_fh is not None:
                _release_refresh_lock(lock_fh)


def load_lot_progress_cache(max_age_seconds: int | None = None) -> dict:
    """Load cache from memory/file and refresh when stale."""
    global _CACHE_STATE
    if max_age_seconds is None:
        max_age_seconds = lot_progress_cache_refresh_seconds()
    source_root_hint = lot_progress_cache_source_root()
    column_mapping = lot_progress_column_mapping()
    should_refresh = False
    with _CACHE_LOCK:
        if (
            _CACHE_STATE
            and _state_source_root_matches(_CACHE_STATE, source_root_hint)
            and _state_column_mapping_matches(_CACHE_STATE, column_mapping)
            and _cache_state_fresh(_CACHE_STATE, max_age_seconds)
        ):
            return dict(_CACHE_STATE)
        path = cache_file()
        state, sig = _load_cache_file_state(path)
        if (
            isinstance(state, dict)
            and state is not _CACHE_STATE
            and _cache_state_fresh(state, max_age_seconds)
            and _state_source_root_matches(state, source_root_hint)
            and _state_column_mapping_matches(state, column_mapping)
        ):
            _adopt_file_state(state, sig)
            return dict(state)
        should_refresh = True
    if should_refresh:
        return refresh_lot_progress_cache(force=True, source_root=source_root_hint)
    return refresh_lot_progress_cache(force=True, source_root=source_root_hint)


def read_lot_progress_cache(max_age_seconds: int | None = None, *, allow_stale: bool = True) -> dict:
    """Read the existing cache without triggering a source scan.

    Hot UI paths use this when a stale or missing cache should return a fast
    readiness/empty result instead of doing a multi-second FAB parquet refresh
    inside the request.
    """
    if max_age_seconds is None:
        max_age_seconds = lot_progress_cache_refresh_seconds()
    source_root_hint = lot_progress_cache_source_root()
    column_mapping = lot_progress_column_mapping()
    with _CACHE_LOCK:
        if (
            _CACHE_STATE
            and _state_source_root_matches(_CACHE_STATE, source_root_hint)
            and _state_column_mapping_matches(_CACHE_STATE, column_mapping)
            and (allow_stale or _cache_state_fresh(_CACHE_STATE, max_age_seconds))
        ):
            return dict(_CACHE_STATE)
        path = cache_file()
        if path.is_file():
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                state = None
            if (
                isinstance(state, dict)
                and _state_source_root_matches(state, source_root_hint)
                and _state_column_mapping_matches(state, column_mapping)
                and (allow_stale or _cache_state_fresh(state, max_age_seconds))
            ):
                _set_cache_state(state)
                return dict(state)
    return _empty_cache_state()


def _matches(item: dict, *, product: str = "", lot_id: str = "", root_lot_id: str = "", wafer_id: str = "", lot_wf: str = "") -> bool:
    if product and _norm_key(item.get("product")) != _norm_key(product) and _norm_key(item.get("process_id")) != _norm_key(product):
        return False
    if lot_wf and _norm_key(item.get("lot_wf")) != _norm_key(lot_wf):
        return False
    if root_lot_id and _norm_key(item.get("root_lot_id")) != _norm_key(root_lot_id):
        return False
    if lot_id:
        needle = _norm_key(lot_id)
        if needle not in {_norm_key(item.get("lot_id")), _norm_key(item.get("root_lot_id"))}:
            return False
    if wafer_id and _norm_wafer(wafer_id) and _norm_wafer(item.get("wafer_id")) != _norm_wafer(wafer_id):
        return False
    return True


def lookup_lot_progress(
    *,
    product: str = "",
    lot_id: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
    lot_wf: str = "",
    limit: int = 50,
    max_age_seconds: int | None = None,
    refresh_if_missing: bool = True,
) -> list[dict]:
    state = (
        load_lot_progress_cache(max_age_seconds=max_age_seconds)
        if refresh_if_missing
        else read_lot_progress_cache(max_age_seconds=max_age_seconds, allow_stale=True)
    )
    return _lookup_lot_progress_in_state(
        state,
        product=product,
        lot_id=lot_id,
        root_lot_id=root_lot_id,
        wafer_id=wafer_id,
        lot_wf=lot_wf,
        limit=limit,
    )


def _lookup_lot_progress_in_state(
    state: dict,
    *,
    product: str = "",
    lot_id: str = "",
    root_lot_id: str = "",
    wafer_id: str = "",
    lot_wf: str = "",
    limit: int = 50,
) -> list[dict]:
    try:
        cap = max(1, min(int(limit), 500))
    except Exception:
        cap = 50
    index = _cache_index_for(state)
    prod = _norm_key(product)
    lot = _norm_key(lot_id)
    root = _norm_key(root_lot_id)
    wafer = _norm_wafer(wafer_id)
    lot_wf_key = _norm_key(lot_wf)
    if lot_wf_key:
        candidates = index["by_lot_wf"].get(lot_wf_key, [])
    elif root:
        candidates = index["by_root_lot_id"].get(root, [])
    elif lot:
        candidates = index["by_lot_id"].get(lot, [])
    elif prod:
        candidates = index["by_product"].get(prod, [])
    elif wafer:
        candidates = index["by_wafer_id"].get(wafer, [])
    else:
        candidates = index["all"]
    rows: list[dict] = []
    for item in candidates:
        if not _matches(item, product=product, lot_id=lot_id, root_lot_id=root_lot_id, wafer_id=wafer_id, lot_wf=lot_wf):
            continue
        rows.append(dict(item))
        if len(rows) >= cap:
            break
    return rows[:cap]


def list_products(max_age_seconds: int | None = None) -> list[str]:
    """Return products from the hot LOT progress cache without parquet scans."""
    state = read_lot_progress_cache(max_age_seconds=max_age_seconds, allow_stale=True)
    return list(_cache_index_for(state).get("products") or [])


def lot_progress_summary(
    *,
    lot_id: str = "",
    root_lot_id: str = "",
    product: str = "",
    limit: int = 500,
    max_age_seconds: int | None = None,
    refresh_if_missing: bool = True,
) -> dict:
    rows = lookup_lot_progress(
        product=product,
        lot_id=lot_id,
        root_lot_id=root_lot_id,
        limit=limit,
        max_age_seconds=max_age_seconds,
        refresh_if_missing=refresh_if_missing,
    )
    return _lot_progress_summary_from_rows(rows, lot_id=lot_id)


def _lot_progress_summary_from_rows(rows: list[dict], *, lot_id: str = "") -> dict:
    rows = sorted(rows, key=lambda row: _wafer_sort_value(row.get("wafer_id")))
    lean_rows = []
    for row in rows:
        lean_rows.append({
            "product": _safe_text(row.get("product") or row.get("process_id")),
            "root_lot_id": _safe_text(row.get("root_lot_id")),
            "wafer_id": _norm_wafer(row.get("wafer_id")),
            "lot_id": _safe_text(row.get("lot_id")),
            "step_id": _safe_text(row.get("step_id")),
            "func_step": _safe_text(row.get("func_step") or row.get("function_step")),
            "update_time": _lot_status_time(row),
        })
    wafer_ids: list[str] = []
    seen_wafers: set[str] = set()
    for row in lean_rows:
        wafer = row.get("wafer_id") or ""
        if not wafer or wafer in seen_wafers:
            continue
        seen_wafers.add(wafer)
        wafer_ids.append(wafer)
    latest = sorted(lean_rows, key=lambda row: _lot_status_time(row), reverse=True)[0] if lean_rows else {}
    root_values = [r.get("root_lot_id") for r in lean_rows if r.get("root_lot_id")]
    lot_values = [r.get("lot_id") for r in lean_rows if r.get("lot_id")]
    product_values = [r.get("product") for r in lean_rows if r.get("product")]
    return {
        "ok": True,
        "lot_id": lot_values[0] if lot_values else _safe_text(lot_id),
        "root_lot_id": root_values[0] if root_values and len(set(root_values)) == 1 else "",
        "product": product_values[0] if product_values and len(set(product_values)) == 1 else "",
        "wafer_count": len(wafer_ids),
        "wafer_ids": wafer_ids,
        "wafer_label": compress_wafer_ids(wafer_ids),
        "step_id": latest.get("step_id") or "",
        "func_step": latest.get("func_step") or "",
        "update_time": latest.get("update_time") or "",
        "rows": lean_rows,
    }


def lot_progress_summaries(
    lot_ids,
    *,
    product: str = "",
    limit: int = 500,
    max_age_seconds: int | None = None,
    refresh_if_missing: bool = False,
) -> dict[str, dict]:
    """Return latest-cache summaries for many LOT IDs with one cache read."""
    state = (
        load_lot_progress_cache(max_age_seconds=max_age_seconds)
        if refresh_if_missing
        else read_lot_progress_cache(max_age_seconds=max_age_seconds, allow_stale=True)
    )
    out: dict[str, dict] = {}
    for raw_lot_id in lot_ids or []:
        lot_id = _safe_text(raw_lot_id)
        key = _norm_key(lot_id)
        if not key or key in out:
            continue
        rows = _lookup_lot_progress_in_state(
            state,
            product=product,
            lot_id=lot_id,
            limit=limit,
        )
        out[key] = _lot_progress_summary_from_rows(rows, lot_id=lot_id)
    return out


def canonical_lot_progress_summaries(
    lot_ids,
    *,
    product: str = "",
    limit: int = 500,
    match_root: bool = False,
) -> dict[str, dict]:
    """Return LOT summaries from the canonical dashboard WIP parquet.

    The WIP dashboard reads ``filebrowser_cache_parquet_file()``.  UI callers
    that need to display the same current LOT/step must use this function
    instead of the scanner-internal ``lot_wf_current.json`` cache; the two
    caches have different owners and refresh schedules.
    ``match_root`` is explicit for root-lot requests; the default continues
    to match only FAB lot IDs so sibling lots never leak into FAB results.
    """
    from core.latest_lot_cache_format import FORMAT_COLUMN, FORMAT_VERSION, normalize_product

    requested: dict[str, str] = {}
    for raw_lot_id in lot_ids or []:
        lot_id = _safe_text(raw_lot_id)
        key = _norm_key(lot_id)
        if key and key not in requested:
            requested[key] = lot_id
    if not requested:
        return {}

    empty = {
        key: _lot_progress_summary_from_rows([], lot_id=lot_id)
        for key, lot_id in requested.items()
    }
    path = filebrowser_cache_parquet_file()
    if not path.is_file():
        return empty

    try:
        import polars as pl  # type: ignore

        schema = pl.read_parquet_schema(path)
        names = list(schema.keys()) if hasattr(schema, "keys") else list(schema)
        required = {FORMAT_COLUMN, "product", "root_lot_id", "lot_id", "wafer_id", "step_id"}
        if not required.issubset(names):
            return empty

        lf = pl.scan_parquet(path)
        version_rows = lf.select(
            pl.col(FORMAT_COLUMN).cast(pl.Int64, strict=False).alias(FORMAT_COLUMN)
        ).head(1).collect()
        if version_rows.is_empty() or version_rows.item(0, 0) != FORMAT_VERSION:
            return empty

        lot_key = (
            pl.col("root_lot_id" if match_root else "lot_id").cast(pl.Utf8, strict=False).fill_null("")
            .str.strip_chars().str.to_uppercase()
        )
        # Lot Management stores the current FAB lot_id, not root_lot_id.
        # Matching a requested lot against both columns can combine sibling
        # FAB lots under the same root and over-count Qty.
        predicate = lot_key.is_in(list(requested))
        product_key = normalize_product(product)
        if product_key:
            cache_product = (
                pl.col("product").cast(pl.Utf8, strict=False).fill_null("")
                .str.strip_chars().str.replace(r"(?i)^ML_TABLE_", "")
                .str.to_uppercase()
            )
            predicate &= cache_product == product_key

        selected = [
            name for name in (
                "product", "root_lot_id", "wafer_id", "lot_id", "step_id",
                "function_step", "tkout_time", "update_time",
            )
            if name in names
        ]
        rows = lf.filter(predicate).select(selected).collect().to_dicts()
    except Exception as exc:
        logger.warning("canonical WIP cache read failed: %s (%s)", path, exc)
        return empty

    rows_by_key: dict[str, list[dict]] = {key: [] for key in requested}
    for raw_row in rows:
        row = dict(raw_row)
        # The canonical writer stamps one cache update_time on every row.
        # tkout_time is the actual per-wafer move time and must decide the
        # displayed current step.
        row["update_time"] = _safe_text(row.get("tkout_time") or row.get("update_time"))
        lot_key_value = _norm_key(row.get("root_lot_id" if match_root else "lot_id"))
        if lot_key_value in rows_by_key and len(rows_by_key[lot_key_value]) < max(1, min(int(limit), 500)):
            rows_by_key[lot_key_value].append(row)

    return {
        key: _lot_progress_summary_from_rows(rows_by_key[key], lot_id=lot_id)
        for key, lot_id in requested.items()
    }


def canonical_wafer_inventory(*, product: str, lot_id: str = "", root_lot_id: str = "", limit: int = 200) -> dict:
    """Read current product membership, counting distinct wafers before display limits."""
    import polars as pl
    from core.latest_lot_cache_format import FORMAT_COLUMN, FORMAT_VERSION, normalize_product

    if not normalize_product(product):
        raise ValueError("조회할 제품을 선택해 주세요.")
    path = filebrowser_cache_parquet_file()
    if not path.is_file():
        raise ValueError("현재 WIP 캐시가 없습니다. 캐시 갱신 후 다시 조회해 주세요.")
    names = list(pl.read_parquet_schema(path))
    required = {FORMAT_COLUMN, "product", "root_lot_id", "lot_id", "wafer_id"}
    if not required.issubset(names):
        raise ValueError("현재 WIP 캐시의 식별 정보가 부족합니다. 캐시를 갱신해 주세요.")
    lf = pl.scan_parquet(path)
    version = lf.select(pl.col(FORMAT_COLUMN)).head(1).collect()
    if not version.is_empty() and version.item(0, 0) != FORMAT_VERSION:
        raise ValueError("현재 WIP 캐시 형식이 오래되었습니다. 캐시를 갱신해 주세요.")
    def key(column):
        return pl.col(column).cast(pl.Utf8, strict=False).fill_null("").str.strip_chars().str.to_uppercase()
    predicate = key("product").str.replace(r"^ML_TABLE_", "") == normalize_product(product)
    if lot_id:
        predicate &= key("lot_id") == _norm_key(lot_id)
    if root_lot_id:
        predicate &= key("root_lot_id") == _norm_key(root_lot_id)
    selected = [name for name in ("product", "root_lot_id", "lot_id", "wafer_id", "step_id", "function_step", "tkout_time", "update_time") if name in names]
    rows = lf.filter(predicate).select(selected).collect().to_dicts()
    unique = {}
    for raw in rows:
        row = dict(raw)
        row["wafer_id"] = _norm_wafer(row.get("wafer_id"))
        if not row["wafer_id"]:
            continue
        identity = (_norm_key(row.get("root_lot_id") or row.get("lot_id")), row["wafer_id"])
        previous = unique.get(identity)
        stamp = lambda r: _safe_text(r.get("tkout_time") or r.get("update_time"))
        if previous is None or stamp(row) > stamp(previous):
            unique[identity] = row
    rows = sorted(unique.values(), key=lambda row: (_norm_key(row.get("root_lot_id")), _wafer_sort_value(row["wafer_id"])))
    cap = max(1, min(int(limit), 200))
    return {"items": rows[:cap], "total": len(rows), "truncated": len(rows) > cap}


def lot_id_candidates(
    *,
    product: str = "",
    prefix: str = "",
    limit: int = 200,
    max_age_seconds: int | None = None,
) -> list[dict]:
    state = load_lot_progress_cache(max_age_seconds=max_age_seconds)
    index = _cache_index_for(state)
    prod = _norm_key(product)
    pref = _norm_key(prefix)
    out: list[dict] = []
    seen: set[str] = set()
    rows = index["by_product"].get(prod, []) if prod else index["all"]
    for item in rows:
        if prod and _norm_key(item.get("product")) != prod and _norm_key(item.get("process_id")) != prod:
            continue
        lot_id = _safe_text(item.get("lot_id"))
        if not lot_id:
            continue
        key = _norm_key(lot_id)
        if pref and not key.startswith(pref):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "value": lot_id,
            "type": "lot_id",
            "lot_id": lot_id,
            "fab_lot_id": lot_id,
            "source_root": item.get("source_root") or "",
            "product": item.get("product") or "",
            "process_id": item.get("process_id") or "",
            "root_lot_id": item.get("root_lot_id") or "",
            "step_id": item.get("step_id") or "",
            "function_step": item.get("function_step") or item.get("func_step") or "",
            "time": item.get("update_time") or item.get("time") or item.get("tkout_time") or item.get("tkin_time") or "",
            "cache": "lot_progress",
        })
        if len(out) >= limit:
            break
    return out


def lot_progress_snapshot(
    *,
    product: str = "",
    root_lot_id: str = "",
    lot_id: str = "",
    wafer_id: str = "",
    lot_wf: str = "",
    max_age_seconds: int | None = None,
    refresh_if_missing: bool = True,
) -> dict:
    state = (
        load_lot_progress_cache(max_age_seconds=max_age_seconds)
        if refresh_if_missing
        else read_lot_progress_cache(max_age_seconds=max_age_seconds, allow_stale=True)
    )
    rows = _lookup_lot_progress_in_state(
        state,
        product=product,
        root_lot_id=root_lot_id,
        lot_id=lot_id,
        wafer_id=wafer_id,
        lot_wf=lot_wf,
        limit=1,
    )
    if not rows:
        return {"fab": {}, "et": [], "cache": {"hit": False}}
    row = rows[0]
    fab = {
        **row,
        "time": row.get("update_time") or row.get("time") or row.get("tkout_time") or row.get("tkin_time") or "",
        "cache_source": "lot_progress_cache",
    }
    return {"fab": fab, "et": [], "cache": {"hit": True, "generated_at": state.get("generated_at")}}


def cache_status() -> dict:
    configured_source_root = lot_progress_cache_source_root()
    source_root_candidates = lot_progress_source_root_candidates(PATHS.db_root, configured_source_root)
    try:
        state = read_lot_progress_cache(max_age_seconds=lot_progress_cache_refresh_seconds(), allow_stale=True)
    except Exception as exc:
        runtime = _state_with_runtime({}, error=True)
        return {
            "ok": False,
            "error": str(exc),
            "cache_file": str(cache_file()),
            "configured_source_root": configured_source_root,
            "source_root": "",
            "source_roots": [],
            "effective_source_roots": [],
            "source_root_candidates": source_root_candidates,
            "last_success_at": runtime.get("last_success_at", ""),
            "last_attempt_at": runtime.get("last_attempt_at", ""),
            "freshness_state": runtime.get("freshness_state", "error"),
            "refresh_log_path": runtime.get("refresh_log_path", str(refresh_log_file())),
            "lock_state": runtime.get("lock_state") or {},
            "running": bool(runtime.get("running")),
            "skipped_by_lock": bool(runtime.get("skipped_by_lock")),
            **metadata(),
        }
    runtime = _state_with_runtime(state)
    source_roots = list(state.get("source_roots") or [])
    products = list(_cache_index_for(state).get("products") or [])[:500]
    return {
        "ok": True,
        "version": state.get("version"),
        "generated_at": state.get("generated_at"),
        "count": state.get("count", len(state.get("items") or [])),
        "row_count": runtime.get("row_count", state.get("count", len(state.get("items") or []))),
        "products": products,
        "product_count": len(products),
        "files_scanned": state.get("files_scanned", 0),
        "rows_seen": state.get("rows_seen", 0),
        "errors": state.get("errors") or [],
        "cache_file": str(cache_file()),
        "scheduler_started": _CACHE_STARTED,
        "interval_minutes": lot_progress_cache_refresh_minutes(),
        "interval_seconds": lot_progress_cache_refresh_seconds(),
        "configured_source_root": configured_source_root,
        "source_root": state.get("source_root") or "",
        "source_roots": source_roots,
        "effective_source_roots": list(state.get("effective_source_roots") or source_roots),
        "fab_roots": list(state.get("fab_roots") or []),
        "source_root_candidates": list(state.get("source_root_candidates") or source_root_candidates),
        "last_success_at": runtime.get("last_success_at", ""),
        "last_attempt_at": runtime.get("last_attempt_at", ""),
        "freshness_state": runtime.get("freshness_state", "never"),
        "refresh_log_path": runtime.get("refresh_log_path", str(refresh_log_file())),
        "lock_state": runtime.get("lock_state") or {},
        "running": bool(runtime.get("running")),
        "skipped_by_lock": bool(runtime.get("skipped_by_lock")),
        **metadata(),
    }


def _refresh_with_offload() -> None:
    """주기 풀스캔 1회 — 낡았을 때만 FAB parquet 을 다시 훑는다.

    신선하면 스캔 자체를 건너뛴다. 낡았으면 사용자 요청이 조용해진 뒤
    (heavy_jobs idle lane, 조회 전제 캐시라 짧은 유예) 메모리 admission 을 거쳐
    이 서버에서 다시 만든다."""
    max_age = lot_progress_cache_refresh_seconds()
    fresh = read_lot_progress_cache(max_age, allow_stale=False)
    if _safe_text(fresh.get("generated_at")):
        return

    def _local_refresh() -> dict:
        state = load_lot_progress_cache(max_age_seconds=max_age)
        return {"ok": bool(state.get("generated_at")), "count": int(state.get("count") or 0)}

    from core import heavy_jobs
    heavy_jobs.run_heavy(
        "splittable_lot_progress_cache_refresh",
        _local_refresh,
        label="lot_progress_refresh",
        idle_only=True,
    )


def _scheduler_loop() -> None:
    while not _CACHE_STOP.is_set():
        try:
            _refresh_with_offload()
        except Exception as exc:
            logger.warning("LOT progress cache refresh failed: %s", exc)
        _CACHE_STOP.wait(lot_progress_cache_refresh_seconds())


def start_lot_progress_cache_scheduler() -> bool:
    global _CACHE_STARTED, _CACHE_THREAD
    if _CACHE_STARTED:
        return False
    # The SplitTable product-rotation scheduler owns WIP latest-lot refresh as
    # one of its four serial stages.  Do not keep a second per-cache timer that
    # can overlap another product/kind on the same server.  An explicit env
    # disable of product rotation restores the legacy standalone timer.
    #
    # 2026-08-04: 제품 순환의 기본값이 '꺼짐' 이 되면서 이 분기를 "순환이 꺼졌으면
    # legacy 타이머" 로 두면 안 된다 — 그러면 아무것도 설정하지 않은 서버에서
    # 자동 캐싱을 끈 게 아니라 **구형 WIP 타이머로 바뀌기만** 한다. legacy 타이머는
    # 운영자가 env 로 순환을 명시적으로 끈 경우에만 복원한다.
    rotation_raw = os.environ.get("FLOW_SPLITTABLE_AUTO_PRODUCT_CACHE_ENABLED")
    env_explicit = rotation_raw is not None and str(rotation_raw).strip() != ""
    env_disabled = env_explicit and str(rotation_raw).strip().lower() not in {
        "1", "true", "yes", "on", "enabled",
    }
    if not env_disabled:
        logger.info("LOT progress independent scheduler retired; "
                    "product rotation (or explicit off) owns refresh")
        return False
    # 개발/양산 2서버가 같은 data root 를 공유할 때, 주기 풀스캔을 한 서버에만
    # 두기 위한 서버 단위 스위치. lease 가 이중 실행은 막지만 스캔 시도 자체를
    # 끄고 싶은 서버(개발)는 이 env 를 설정한다.
    if os.environ.get("FLOW_DISABLE_LOT_PROGRESS_SCHEDULER", "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.info("LOT progress cache scheduler disabled by FLOW_DISABLE_LOT_PROGRESS_SCHEDULER")
        return False
    _CACHE_STARTED = True
    _CACHE_THREAD = threading.Thread(target=_scheduler_loop, name="lot-progress-cache", daemon=True)
    _CACHE_THREAD.start()
    return True
