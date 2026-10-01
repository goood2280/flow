"""Cross-process transaction lock for file-backed read/modify/write stores."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import threading
import time
from typing import Iterator


_LOCKS_GUARD = threading.Lock()
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_LOCAL = threading.local()


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.absolute()))


def _process_lock(key: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


def _acquire_os_lock(handle, timeout: float | None) -> None:
    if os.name == "nt":
        import msvcrt

        if handle.seek(0, 2) == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while True:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for file transaction lock")
                time.sleep(0.02)

    import fcntl

    if timeout is None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for file transaction lock")
            time.sleep(0.02)


def _release_os_lock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def file_transaction(path: str | os.PathLike[str], *, timeout: float | None = None) -> Iterator[None]:
    """Serialize a file transaction across threads and processes.

    The stable ``<filename>.lock`` sidecar remains lockable when the data file
    is atomically replaced. Calls for the same path are reentrant in one thread;
    the outermost call owns the OS lock.
    """
    data_path = Path(path)
    key = _path_key(data_path)
    lock = _process_lock(key)

    with lock:
        depths = getattr(_LOCAL, "depths", None)
        if depths is None:
            depths = {}
            _LOCAL.depths = depths
        depth = depths.get(key, 0)
        if depth:
            depths[key] = depth + 1
            try:
                yield
            finally:
                depths[key] -= 1
            return

        lock_path = data_path.with_name(data_path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+b") as handle:
            _acquire_os_lock(handle, timeout)
            depths[key] = 1
            try:
                yield
            finally:
                depths.pop(key, None)
                with contextlib.suppress(OSError):
                    _release_os_lock(handle)


_IS_WINDOWS = os.name == "nt"


def _replace_retry_seconds() -> float:
    try:
        value = float(os.environ.get("FLOW_FILE_REPLACE_RETRY_SEC", "") or 2.0)
    except ValueError:
        value = 2.0
    return max(0.0, min(30.0, value))


def replace_file(src: str | os.PathLike[str], dst: str | os.PathLike[str], *,
                 timeout: float | None = None) -> None:
    """``os.replace`` that waits out readers on Windows.

    Linux renames over an open file. Windows refuses (PermissionError,
    WinError 5/32) while any reader - a request thread, Polars, DuckDB - still
    holds ``dst`` or, for directories, a file inside it. Cache refreshes and
    JSON saves then failed at random under load on the Windows server. Readers
    hold files for milliseconds, so a short bounded retry makes the swap land;
    after ``timeout`` (``FLOW_FILE_REPLACE_RETRY_SEC``, default 2 s) the last
    error is raised as before.
    """
    if not _IS_WINDOWS:
        os.replace(src, dst)
        return
    deadline = time.monotonic() + (_replace_retry_seconds() if timeout is None else max(0.0, timeout))
    delay = 0.005
    while True:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.1)
