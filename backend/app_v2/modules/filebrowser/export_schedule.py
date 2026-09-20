"""Schedule the legacy lot-progress export only when its source changed."""

import threading
from pathlib import Path
from typing import Callable


_lock = threading.Lock()
_inflight: set[str] = set()
_failed: set[str] = set()


def schedule_if_stale(
    source: Path, target: Path, export: Callable[[], object], *, settings: Path | None = None,
) -> bool:
    """Start one export when JSON or its source settings changed."""
    try:
        source_mtime = source.stat().st_mtime_ns
    except OSError:
        source_mtime = None
    try:
        target_mtime = target.stat().st_mtime_ns
    except OSError:
        target_mtime = None
    try:
        settings_mtime = settings.stat().st_mtime_ns if settings is not None else None
    except OSError:
        settings_mtime = None

    key = str(target)
    with _lock:
        if (
            key not in _failed
            and source_mtime is not None
            and target_mtime is not None
            and target_mtime >= max(source_mtime, settings_mtime or 0)
        ):
            return False
        if key in _inflight:
            return False
        _inflight.add(key)

    def run() -> None:
        try:
            result = export()
            with _lock:
                if isinstance(result, dict) and result.get("ok") is False:
                    _failed.add(key)
                else:
                    _failed.discard(key)
        except Exception:
            with _lock:
                _failed.add(key)
            raise
        finally:
            with _lock:
                _inflight.discard(key)

    try:
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        with _lock:
            _inflight.discard(key)
        raise
    return True
