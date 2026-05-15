"""Background prewarmer for the FileBrowser preview cache.

Reads the recent access log, picks the most-clicked sources, and re-runs the
matching endpoint in-process so the response is materialized in the cache.
Self-learning: only sources users actually click end up prewarmed.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from typing import Iterable

from core import filebrowser_cache as fbcache

logger = logging.getLogger("flow.filebrowser_cache_prewarm")

PREWARM_INTERVAL_SECONDS = 30 * 60
PREWARM_TOP_N = 20
ACCESS_LOG_TAIL = 2000

_STARTED = False
_STARTED_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def start_prewarmer() -> None:
    global _STARTED, _THREAD
    with _STARTED_LOCK:
        if _STARTED:
            return
        _STARTED = True
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, name="filebrowser-prewarm", daemon=True)
    _THREAD.start()
    logger.info("filebrowser preview prewarmer started")


def stop_prewarmer() -> None:
    _STOP.set()


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _prewarm_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("filebrowser preview prewarm cycle failed: %s", exc)
        if _STOP.wait(PREWARM_INTERVAL_SECONDS):
            break


def _read_recent_misses(limit: int) -> list[dict]:
    fp = fbcache.access_log_file()
    if not fp.is_file():
        return []
    try:
        lines = fp.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]
    except OSError:
        return []
    import json
    out: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _top_sources(rows: Iterable[dict], n: int) -> list[tuple[str, str, int]]:
    counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        endpoint = str(row.get("endpoint") or "")
        path = str(row.get("path") or "")
        if endpoint and path:
            counter[(endpoint, path)] += 1
    return [(ep, path, cnt) for (ep, path), cnt in counter.most_common(n)]


def _prewarm_once() -> None:
    rows = _read_recent_misses(ACCESS_LOG_TAIL)
    if not rows:
        return
    targets = _top_sources(rows, PREWARM_TOP_N)
    if not targets:
        return
    # Import here to avoid circular import at module load.
    from routers import filebrowser as fb

    db_root = fb._db_root().resolve()
    base_root = fb._base_root().resolve()
    warmed = 0
    for endpoint, path, _count in targets:
        try:
            if endpoint == "view":
                root, product = _split_product_path(path, db_root)
                if root and product:
                    fb.view_product(
                        root=root, product=product, sql="", rows=fb.LATEST_PREVIEW_ROWS,
                        cols=20, select_cols="", meta_only=True, all_partitions=False,
                        engine="auto", page=0, page_size=fb.LATEST_PREVIEW_ROWS,
                    )
                    warmed += 1
            elif endpoint == "root-parquet-view":
                rel = _relative_to(path, db_root)
                if rel is not None:
                    fb.view_root_parquet(
                        file=rel, sql="", rows=fb.LATEST_PREVIEW_ROWS, cols=10,
                        select_cols="", meta_only=True, engine="auto",
                        page=0, page_size=fb.LATEST_PREVIEW_ROWS,
                    )
                    warmed += 1
            elif endpoint == "base-file-view":
                rel = _relative_to(path, base_root) or _relative_to(path, db_root)
                if rel is not None:
                    fb.base_file_view(
                        file=rel, sql="", rows=fb.LATEST_PREVIEW_ROWS, cols=10,
                        select_cols="", engine="auto", meta_only=True,
                        page=0, page_size=fb.LATEST_PREVIEW_ROWS, request=None,
                    )
                    warmed += 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("prewarm skip endpoint=%s path=%s: %s", endpoint, path, exc)
    if warmed:
        logger.info("filebrowser preview prewarm: warmed %d source(s)", warmed)


def _split_product_path(path: str, db_root) -> tuple[str, str]:
    """Split an absolute product directory under db_root into (root, product)."""
    from pathlib import Path

    try:
        rel = Path(path).resolve().relative_to(db_root)
    except (ValueError, OSError):
        return "", ""
    parts = rel.parts
    if len(parts) < 2:
        return "", ""
    return parts[0], parts[1]


def _relative_to(path: str, base) -> str | None:
    from pathlib import Path

    try:
        rel = Path(path).resolve().relative_to(base)
    except (ValueError, OSError):
        return None
    return str(rel).replace("\\", "/")
