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

    cached = _read_cached(cache_fp)
    if cached is not None and _unchanged(cached.get("source"), source):
        resp = cached.get("response")
        if isinstance(resp, dict):
            out = dict(resp)
            out["preview_cache_hit"] = True
            _log_event("hit", endpoint, key, source["path"])
            return out

    is_leader, follower_event = _acquire_or_wait(key)
    if not is_leader:
        if follower_event is not None:
            follower_event.wait(timeout=_INFLIGHT_TIMEOUT_SECONDS)
        cached = _read_cached(cache_fp)
        if cached is not None and _unchanged(cached.get("source"), source):
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
            _write_cached(cache_fp, {
                "version": CACHE_VERSION,
                "endpoint": endpoint,
                "key": key_payload,
                "source": source,
                "response": result,
                "cached_at": dt.datetime.now().isoformat(timespec="seconds"),
            })
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
