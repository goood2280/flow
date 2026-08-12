"""core/memory_trim.py — 해제한 메모리를 OS 로 실제로 돌려준다.

`gc.collect()` 는 파이썬 객체만 회수한다. 그 뒤 free 된 힙은 allocator 의
arena 에 남아 **RSS 가 그대로다.** 캐시를 축출해도 워치독이 재측정한 비율이
안 내려가고(`98.6% → 98.6%`), 다음 주기에 또 축출을 시도하면서 이번에는 비울
캐시가 없어 `freed 0.0MB [nothing]` 만 반복됐다 — 축출 로직이 아니라 회수
경로가 빠져 있던 것이다.

여기서 두 allocator 를 모두 다룬다:

* **glibc malloc** — 파이썬 자체 할당. `malloc_trim(0)` 으로 top of heap 과
  free chunk 를 OS 에 반환한다. musl/Windows 에는 심볼이 없어 no-op.
* **jemalloc** — Linux polars 휠이 번들해 쓴다. 큰 collect 뒤 dirty page 를
  arena 에 오래 들고 있으므로 `arena.<all>.purge` 를 mallctl 로 호출한다.
  심볼이 프로세스에 없으면(=polars 가 시스템 malloc 을 쓰는 빌드) no-op.

둘 다 없는 환경(개발 PC Windows)에서는 `gc.collect()` 만 하고 조용히 끝난다.
심볼 조회는 1회만 하고 결과를 캐시한다.
"""
from __future__ import annotations

import ctypes
import gc
import logging
import platform
import threading

logger = logging.getLogger("flow.memory_trim")

_RESOLVE_LOCK = threading.Lock()
_RESOLVED = False
_MALLOC_TRIM = None      # glibc malloc_trim(size_t) -> int
_MALLCTL = None          # jemalloc mallctl(name, oldp, oldlenp, newp, newlen) -> int
_JE_PURGE_NAME = b""     # "arena.<narenas>.purge" — 해석 시점에 확정

# jemalloc 의 MALLCTL_ARENAS_ALL. 모든 arena 를 한 번에 지시하는 매직 인덱스로,
# jemalloc 5.x 에서 4096 으로 고정돼 있다.
_MALLCTL_ARENAS_ALL = 4096


def _resolve() -> None:
    """malloc_trim / mallctl 심볼을 1회 조회한다. 실패는 no-op 으로 굳힌다."""
    global _RESOLVED, _MALLOC_TRIM, _MALLCTL, _JE_PURGE_NAME
    with _RESOLVE_LOCK:
        if _RESOLVED:
            return
        _RESOLVED = True
        if platform.system() == "Windows":
            return
        try:
            # None = 현재 프로세스에 이미 로드된 심볼 전체. polars 가 링크한
            # jemalloc 도 여기서 잡힌다(별도 .so 경로를 추측하지 않는다).
            handle = ctypes.CDLL(None)
        except Exception as exc:
            logger.debug("memory_trim: dlopen(self) failed: %s", exc)
            return
        try:
            fn = handle.malloc_trim
            fn.argtypes = [ctypes.c_size_t]
            fn.restype = ctypes.c_int
            _MALLOC_TRIM = fn
        except Exception:
            _MALLOC_TRIM = None
        for symbol in ("mallctl", "je_mallctl"):
            try:
                fn = getattr(handle, symbol)
            except Exception:
                continue
            fn.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_size_t]
            fn.restype = ctypes.c_int
            _MALLCTL = fn
            _JE_PURGE_NAME = f"arena.{_MALLCTL_ARENAS_ALL}.purge".encode("ascii")
            break
        logger.info("memory_trim: malloc_trim=%s jemalloc_mallctl=%s",
                    bool(_MALLOC_TRIM), bool(_MALLCTL))


def available() -> dict:
    """어떤 회수 경로가 이 프로세스에서 실제로 동작하는지."""
    _resolve()
    return {"malloc_trim": bool(_MALLOC_TRIM), "jemalloc_purge": bool(_MALLCTL)}


def _rss_bytes() -> int:
    try:
        from core.runtime_limits import process_memory_snapshot

        snap = process_memory_snapshot()
    except Exception:
        return 0
    for key in ("process_memory_effective_gb", "process_rss_gb"):
        try:
            gb = float(snap.get(key) or 0.0)
        except Exception:
            gb = 0.0
        if gb > 0:
            return int(gb * (1024 ** 3))
    return 0


def trim(reason: str = "", *, collect: bool = True) -> dict:
    """gc + allocator arena 반환. 반환: 실행 결과와 회수량(측정 가능할 때).

    절대 예외를 올리지 않는다 — 회수는 부가 동작이고, 여기서 실패한다고 해서
    호출측(축출 pass, 캐시 파이프라인 단계)이 중단되면 안 된다.
    """
    _resolve()
    before = _rss_bytes()
    out: dict = {
        "reason": str(reason or ""),
        "collected": 0,
        "malloc_trim": False,
        "jemalloc_purge": False,
        "rss_before": before,
        "rss_after": before,
        "released_bytes": 0,
    }
    if collect:
        try:
            out["collected"] = int(gc.collect())
        except Exception:
            pass
    if _MALLOC_TRIM is not None:
        try:
            # 반환값 1 = 실제로 반환한 메모리가 있음, 0 = 없음. 둘 다 정상.
            _MALLOC_TRIM(0)
            out["malloc_trim"] = True
        except Exception as exc:
            logger.debug("memory_trim: malloc_trim failed: %s", exc)
    if _MALLCTL is not None:
        try:
            if _MALLCTL(_JE_PURGE_NAME, None, None, None, 0) == 0:
                out["jemalloc_purge"] = True
        except Exception as exc:
            logger.debug("memory_trim: jemalloc purge failed: %s", exc)
    after = _rss_bytes()
    out["rss_after"] = after
    if before > 0 and after > 0:
        out["released_bytes"] = max(0, before - after)
    return out
