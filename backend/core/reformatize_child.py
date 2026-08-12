# -*- coding: utf-8 -*-
"""core/reformatize_child.py — ET 다운로드 계산 프로세스의 진입 모듈.

ET 다운로드는 큰 프레임이 프로세스와 함께 사라지도록 `spawn` 자식에서 돈다
(`routers.reformatize._run_download_isolated`). 문제는 **폴라스 스레드풀 크기가
프로세스 최초 import 시점에 고정**된다는 것이다. 자식은 `app.py` 를 거치지
않으니 `core.runtime_limits.apply_runtime_limits()` 가 돌지 않고, 부모 환경에
운영자가 큰 값을 박아뒀거나 환경변수가 비어 있으면 자식이 **호스트 코어를 전부
잡는다** — 다운로드 한 건이 서버 CPU 를 독점하던 원인이다.

그래서 진입점을 이 모듈로 옮긴다. spawn 자식이 target 을 풀기 위해 이 모듈을
import 할 때 **polars 가 로드되기 전에** 스레드 상한을 환경변수로 심고, 그 다음
함수 본문에서 비로소 `routers.reformatize` 를 import 한다.

기본은 1 코어다 (`FLOW_REFORMATIZE_THREADS`). ET 다운로드는 사용자가 기다리는
배치성 작업이라 응답 속도보다 서버 여유가 중요하고, 실제 비용의 대부분은
parquet I/O 다.
"""
from __future__ import annotations

import os

_THREAD_ENV = (
    "POLARS_MAX_THREADS",
    "RAYON_NUM_THREADS",
    "PYARROW_NUM_THREADS",
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)


def download_threads() -> int:
    """ET 다운로드 계산에 허용할 스레드 수 (기본 1, 최소 1)."""
    raw = str(os.environ.get("FLOW_REFORMATIZE_THREADS", "") or "1").strip()
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 1


def pin_threads() -> int:
    """폴라스/BLAS 스레드 상한을 환경변수로 고정한다. polars import 전에 호출."""
    threads = download_threads()
    for name in _THREAD_ENV:
        os.environ[name] = str(threads)
    return threads


# import 시점에 심어야 뒤이은 polars import 가 이 값을 본다.
_PINNED = pin_threads()


def download_entry(result_queue, *, product: str, filters: dict, wanted: list,
                   agg: str, is_admin: bool, path: str) -> None:
    """spawn 자식 본체 — 계산 결과를 CSV 파일로 쓰고 큐로 보고한다."""
    try:
        from pathlib import Path

        from routers.reformatize import (
            Filters, _build_download_frame, _write_csv_file,
        )

        def _progress(phase: str, done=None, total=None):
            result_queue.put({"type": "progress", "phase": phase, "done": done, "total": total})

        f = Filters(**(filters or {}))
        wide, vehicle_csv, raw_rows = _build_download_frame(
            product, f, list(wanted or []), agg, is_admin, progress=_progress)
        _progress(f"CSV 파일 만드는 중 (0/{wide.height:,}행)", 0, wide.height)
        _write_csv_file(wide, Path(path), progress=_progress)
        result_queue.put({
            "type": "result", "vehicle_csv": vehicle_csv, "raw_rows": raw_rows,
            "rows": wide.height, "cols": wide.width, "threads": _PINNED,
        })
    except BaseException as exc:  # child must always report a terminal message
        detail = getattr(exc, "detail", None)
        result_queue.put({
            "type": "error",
            "error": str(detail if detail is not None else exc),
            "status": int(getattr(exc, "status_code", 0) or 0),
        })
