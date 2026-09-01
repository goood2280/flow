"""FileBrowser preview response cache.

Read-through disk cache keyed on (endpoint, source path, query payload).
Invalidated when the underlying file or product directory's mtime/size changes.
JSON-encoded one file per cache key; 100-row preview payloads stay tiny
(~50-500KB), so the parquet/columnar overhead would dominate.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from core.paths import PATHS as _DEFAULT_PATHS

logger = logging.getLogger("flow.filebrowser_cache")


def _paths():
    """Resolve the active PATHS, honoring monkey-patches applied to the router.

    Existing tests rebind ``routers.filebrowser.PATHS`` to a ``DummyPaths``
    instance. Reading through that binding when available keeps cache writes
    inside the test sandbox; otherwise we use the real singleton.
    """
    try:
        from routers import filebrowser as _fb
    except Exception:  # noqa: BLE001
        return _DEFAULT_PATHS
    return getattr(_fb, "PATHS", _DEFAULT_PATHS)

CACHE_VERSION = 1
_INFLIGHT_TIMEOUT_SECONDS = 60.0
_ACCESS_LOG_NAME = "filebrowser_preview_access.jsonl"

_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}
_MEMORY_LOCK = threading.Lock()
_MEMORY_CACHE: OrderedDict[str, tuple[int, dict]] = OrderedDict()
_MEMORY_CACHE_BYTES = 0


def cache_dir() -> Path:
    path = Path(_paths().cache_dir) / "filebrowser"
    path.mkdir(parents=True, exist_ok=True)
    return path


def access_log_file() -> Path:
    fp = Path(_paths().data_root) / "logs" / _ACCESS_LOG_NAME
    fp.parent.mkdir(parents=True, exist_ok=True)
    return fp


def _cache_available() -> bool:
    paths = _paths()
    return hasattr(paths, "cache_dir") and hasattr(paths, "data_root")


def is_enabled(settings: dict | None = None) -> bool:
    if not _cache_available():
        return False
    if settings is None:
        return True
    value = settings.get("preview_cache_enabled", True)
    return bool(value)


_BUDGET_MEMO_LOCK = threading.Lock()
_BUDGET_MEMO: tuple[float, float] | None = None  # (monotonic ts, default_gb)
_BUDGET_MEMO_TTL = 300.0


def _default_memory_cache_gb() -> float:
    """호스트 적응 기본 예산: 총메모리×10% 를 [0.5, 2]GB 로 클램프.

    기존 고정 6GB 기본값은 10GB급 개발 서버에서 root RAM 캐시(2~6GB)와
    합쳐 캐시만으로 OOM 을 낼 수 있었다. 총량을 못 읽으면 1GB.
    """
    global _BUDGET_MEMO
    import time as _time
    now = _time.monotonic()
    with _BUDGET_MEMO_LOCK:
        memo = _BUDGET_MEMO
        if memo is not None and now - memo[0] < _BUDGET_MEMO_TTL:
            return memo[1]
    try:
        from core.runtime_limits import system_memory_snapshot
        total_gb = float(system_memory_snapshot().get("system_memory_total_gb") or 0.0)
    except Exception:
        total_gb = 0.0
    gb = max(0.5, min(2.0, total_gb * 0.10)) if total_gb > 0 else 1.0
    with _BUDGET_MEMO_LOCK:
        _BUDGET_MEMO = (now, gb)
    return gb


def memory_cache_budget_bytes() -> int:
    """Process-local hot preview cache budget.

    The disk cache remains the durable cross-process fallback.  Default is
    host-adaptive (total*10%, clamped 0.5-2GB); operators can override with
    FLOW_PREVIEW_MEMORY_CACHE_GB (0 disables the memory layer).
    """
    raw = os.environ.get("FLOW_PREVIEW_MEMORY_CACHE_GB", "")
    if raw not in (None, ""):
        try:
            gb = float(raw)
        except Exception:
            gb = _default_memory_cache_gb()
        gb = max(0.0, min(64.0, gb))
        budget = int(gb * 1024 * 1024 * 1024)
        try:
            from core import cache_budget

            # 명시 핀도 전체 안전 풀은 우회하지 않는다. 개발 역할의 자동 축소만
            # 빼서 운영자가 지정한 값과 화면 표시가 어긋나지 않게 한다.
            budget = cache_budget.capped(
                "filebrowser_preview", budget, explicit=True
            )
        except Exception:
            pass
        return budget
    gb = max(0.0, min(64.0, _default_memory_cache_gb()))
    budget = int(gb * 1024 * 1024 * 1024)
    try:
        from core import cache_budget

        budget = cache_budget.capped("filebrowser_preview", budget)
    except Exception:
        pass
    return budget


def memory_cache_stats() -> dict:
    """메모리 캐시 현황 (관리자 메모리 종합 현황용) — 메타데이터만."""
    with _MEMORY_LOCK:
        entries = len(_MEMORY_CACHE)
        used = int(_MEMORY_CACHE_BYTES)
    return {
        "entries": entries,
        "bytes": used,
        "budget_bytes": memory_cache_budget_bytes(),
    }


def emergency_evict(max_bytes: int) -> int:
    """메모리 워치독 긴급 축출 — 오래된 항목부터 최대 max_bytes 제거.

    디스크 캐시 파일은 남으므로 다음 조회는 디스크 폴백으로 여전히 빠르다.
    반환: 회수한 추정 바이트."""
    global _MEMORY_CACHE_BYTES
    if max_bytes <= 0:
        return 0
    freed = 0
    with _MEMORY_LOCK:
        while _MEMORY_CACHE and freed < max_bytes:
            _key, (size, _payload) = _MEMORY_CACHE.popitem(last=False)
            size = int(size or 0)
            freed += size
            _MEMORY_CACHE_BYTES = max(0, _MEMORY_CACHE_BYTES - size)
    return freed


def settings_signature(settings: dict | None) -> str:
    """Return a short hash of settings fields that affect preview output.

    Any change here invalidates entries so a settings edit (e.g. lowering
    `csv_full_read_max_bytes`) cannot serve stale rows.
    """
    if not isinstance(settings, dict):
        return ""
    relevant = {
        "csv_full_read_max_bytes": settings.get("csv_full_read_max_bytes"),
        "csv_rules": settings.get("csv_rules"),
        "preview_max_rows": settings.get("preview_max_rows"),
        "preview_max_columns": settings.get("preview_max_columns"),
        "schema_column_page_size": settings.get("schema_column_page_size"),
    }
    blob = json.dumps(relevant, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def stat_for_file(fp: Path | None) -> dict | None:
    if fp is None:
        return None
    try:
        st = fp.stat()
    except OSError:
        return None
    return {
        "kind": "file",
        "path": str(fp),
        "mtime_ns": int(st.st_mtime_ns),
        "size_bytes": int(st.st_size),
    }


def stat_for_db_product(prod_dir: Path | None) -> dict | None:
    if prod_dir is None:
        return None
    try:
        st = prod_dir.stat()
    except OSError:
        return None
    if not prod_dir.is_dir():
        return None
    latest_mtime_ns = 0
    latest_size = 0
    try:
        for fp in prod_dir.rglob("*.parquet"):
            try:
                child = fp.stat()
            except OSError:
                continue
            if child.st_mtime_ns > latest_mtime_ns:
                latest_mtime_ns = int(child.st_mtime_ns)
                latest_size = int(child.st_size)
    except OSError:
        return None
    return {
        "kind": "db",
        "path": str(prod_dir),
        "mtime_ns": int(st.st_mtime_ns),
        "latest_child_mtime_ns": latest_mtime_ns,
        "latest_child_size": latest_size,
    }


def _hash_key(endpoint: str, source_path: str, key_payload: dict) -> str:
    payload = {
        "endpoint": endpoint,
        "source_path": source_path,
        "params": key_payload,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def _cache_file(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def _unchanged(stored: dict | None, current: dict | None) -> bool:
    if not stored or not current:
        return False
    if stored.get("kind") != current.get("kind"):
        return False
    if stored.get("path") != current.get("path"):
        return False
    if int(stored.get("mtime_ns") or 0) != int(current.get("mtime_ns") or 0):
        return False
    if current.get("kind") == "file":
        return int(stored.get("size_bytes") or 0) == int(current.get("size_bytes") or 0)
    return (
        int(stored.get("latest_child_mtime_ns") or 0)
        == int(current.get("latest_child_mtime_ns") or 0)
        and int(stored.get("latest_child_size") or 0)
        == int(current.get("latest_child_size") or 0)
    )


def _log_event(event: str, endpoint: str, key: str, path: str) -> None:
    try:
        fp = access_log_file()
        row = {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "endpoint": endpoint,
            "key": key,
            "path": path,
        }
        with fp.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        logger.debug("filebrowser cache access log write failed: %s", exc)


def _drop_memory_key(key: str) -> None:
    global _MEMORY_CACHE_BYTES
    existing = _MEMORY_CACHE.pop(key, None)
    if existing is not None:
        _MEMORY_CACHE_BYTES = max(0, _MEMORY_CACHE_BYTES - int(existing[0] or 0))


def _read_memory_cached(key: str, source: dict) -> dict | None:
    if memory_cache_budget_bytes() <= 0:
        return None
    with _MEMORY_LOCK:
        cached = _MEMORY_CACHE.get(key)
        if cached is None:
            return None
        _size, data = cached
        if not isinstance(data, dict) or int(data.get("version") or 0) != CACHE_VERSION:
            _drop_memory_key(key)
            return None
        if not _unchanged(data.get("source"), source):
            _drop_memory_key(key)
            return None
        _MEMORY_CACHE.move_to_end(key)
        return data


def _write_memory_cached(key: str, payload: dict, size_bytes: int | None = None) -> None:
    global _MEMORY_CACHE_BYTES
    budget = memory_cache_budget_bytes()
    if budget <= 0:
        with _MEMORY_LOCK:
            _MEMORY_CACHE.clear()
            _MEMORY_CACHE_BYTES = 0
        return
    if size_bytes is None:
        try:
            size_bytes = len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
        except Exception:
            size_bytes = 0
    size_bytes = max(0, int(size_bytes or 0))
    if size_bytes <= 0 or size_bytes > budget:
        return
    with _MEMORY_LOCK:
        _drop_memory_key(key)
        _MEMORY_CACHE[key] = (size_bytes, payload)
        _MEMORY_CACHE_BYTES += size_bytes
        while _MEMORY_CACHE_BYTES > budget and _MEMORY_CACHE:
            _old_key, (old_size, _old_payload) = _MEMORY_CACHE.popitem(last=False)
            _MEMORY_CACHE_BYTES = max(0, _MEMORY_CACHE_BYTES - int(old_size or 0))


def _read_cached(cache_fp: Path) -> dict | None:
    try:
        with cache_fp.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if int(data.get("version") or 0) != CACHE_VERSION:
        return None
    return data


def _write_cached(cache_fp: Path, payload: dict) -> None:
    cache_fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_fp.with_suffix(cache_fp.suffix + ".tmp")
    blob = json.dumps(payload, ensure_ascii=False, default=str)
    _write_memory_cached(cache_fp.stem, payload, len(blob.encode("utf-8")))
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(blob)
        tmp.replace(cache_fp)
    except OSError as exc:
        logger.warning("filebrowser cache write failed key=%s: %s", cache_fp.name, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_or_wait(key: str) -> tuple[bool, threading.Event | None]:
    """Singleflight: return (is_leader, follower_event)."""
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.get(key)
        if ev is None:
            ev = threading.Event()
            _INFLIGHT[key] = ev
            return True, None
        return False, ev


def _release(key: str) -> None:
    with _INFLIGHT_LOCK:
        ev = _INFLIGHT.pop(key, None)
    if ev is not None:
        ev.set()


def get_cached(
    *,
    endpoint: str,
    source: dict,
    key_payload: dict,
) -> dict | None:
    """Return a fresh cached response without starting a computation."""
    key = _hash_key(endpoint, source["path"], key_payload)
    cached = _read_memory_cached(key, source)
    if cached is None:
        cached = _read_cached(_cache_file(key))
    if cached is None or not _unchanged(cached.get("source"), source):
        return None
    _write_memory_cached(key, cached)
    response = cached.get("response")
    if not isinstance(response, dict):
        return None
    out = dict(response)
    out["preview_cache_hit"] = True
    _log_event("hit", endpoint, key, source["path"])
    return out


def put_cached(
    *, endpoint: str, source: dict, key_payload: dict, response: dict,
) -> None:
    """Publish a response computed by another server under this server's source key."""
    key = _hash_key(endpoint, source["path"], key_payload)
    _write_cached(_cache_file(key), {
        "version": CACHE_VERSION,
        "endpoint": endpoint,
        "key": key_payload,
        "source": source,
        "response": response,
        "cached_at": dt.datetime.now().isoformat(timespec="seconds"),
    })


def get_or_compute(
    *,
    endpoint: str,
    source: dict,
    key_payload: dict,
    compute: Callable[[], Any],
) -> Any:
    """Return cached preview response if fresh, otherwise compute and cache.

    The cached response receives a `preview_cache_hit=true` field so callers
    can observe hits; on miss the original compute() return value is stored
    verbatim under the `response` key.
    """
    key = _hash_key(endpoint, source["path"], key_payload)
    cache_fp = _cache_file(key)

    cached_response = get_cached(endpoint=endpoint, source=source, key_payload=key_payload)
    if cached_response is not None:
        return cached_response

    is_leader, follower_event = _acquire_or_wait(key)
    if not is_leader:
        if follower_event is not None:
            follower_event.wait(timeout=_INFLIGHT_TIMEOUT_SECONDS)
        cached = _read_memory_cached(key, source)
        if cached is None:
            cached = _read_cached(cache_fp)
        if cached is not None and _unchanged(cached.get("source"), source):
            _write_memory_cached(key, cached)
            resp = cached.get("response")
            if isinstance(resp, dict):
                out = dict(resp)
                out["preview_cache_hit"] = True
                _log_event("hit", endpoint, key, source["path"])
                return out
        # Cache still missing after wait — fall through to compute ourselves.
        is_leader, _ = _acquire_or_wait(key)
        if not is_leader:
            return compute()

    try:
        _log_event("miss", endpoint, key, source["path"])
        result = compute()
        if isinstance(result, dict):
            put_cached(endpoint=endpoint, source=source, key_payload=key_payload, response=result)
        return result
    finally:
        _release(key)


def invalidate_for_path(path: str | os.PathLike[str]) -> int:
    """Remove cache files referencing a given source path. Returns count removed.

    Intended for explicit refresh actions; the routine TTL is mtime/size-based.
    """
    target = str(path)
    removed = 0
    try:
        for fp in cache_dir().glob("*.json"):
            data = _read_cached(fp)
            if not data:
                continue
            stored_path = (data.get("source") or {}).get("path")
            if stored_path == target:
                try:
                    fp.unlink()
                    removed += 1
                except OSError:
                    continue
    except OSError:
        pass
    return removed
