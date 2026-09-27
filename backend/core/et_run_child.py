# -*- coding: utf-8 -*-
"""core/et_run_child.py — ET 조회(/run) 상주 계산 프로세스의 진입 모듈.

`core.et_run_service` 가 spawn 으로 띄우는 자식이 이 모듈을 import 한다.
다운로드 자식(`core.reformatize_child`)과 같은 이유로 **polars 를 import 하기
전에** 스레드 상한과 OS 우선순위를 정한다 — 풀 크기는 최초 import 때 고정된다.

기본값:
- 스레드 2개(`FLOW_REFORMATIZE_RUN_THREADS`). 조회는 사용자가 결과 화면을
  기다리는 작업이라 다운로드(1)보다 하나 더 준다.
- OS 우선순위 한 단계 낮춤(`FLOW_REFORMATIZE_CHILD_NICE`, 0이면 끔). CPU 가
  비어 있으면 제 속도로 돌고, SplitTable 검색과 CPU 를 다툴 때만 뒤로 밀린다.
"""
from __future__ import annotations

import gc
import os
import queue as _queue
import sys

from core.reformatize_child import lower_priority, pin_threads


def run_threads() -> int:
    """ET 조회 계산에 허용할 스레드 수 (기본 2, 최소 1)."""
    raw = str(os.environ.get("FLOW_REFORMATIZE_RUN_THREADS", "") or "2").strip()
    try:
        return max(1, int(float(raw)))
    except (TypeError, ValueError):
        return 2


# import 시점에 심어야 뒤이은 polars import 가 이 값을 본다. 부모(API)도 serve 를
# 넘기려고 이 모듈을 import 하므로, polars 가 이미 있는 프로세스에서는 건드리지 않는다
# (reformatize_child 와 같은 규칙).
_PINNED = pin_threads(run_threads()) if "polars" not in sys.modules else run_threads()


def _rss_bytes() -> int:
    try:
        import psutil  # type: ignore

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:
        return 0


def serve(req_q, resp_q, *, idle_sec: float, recycle_bytes: int,
          linger_bytes: int = 0, linger_sec: float = 120.0) -> None:
    """요청을 하나씩 계산한다. 유휴 `idle_sec` 초면 종료(메모리 반환).

    계산이 끝날 때마다 자식 안의 캐시를 비운다 — 결과 wide 는 부모가 들고
    있으므로 자식이 같은 것을 또 들고 있을 이유가 없다. RSS 가 `recycle_bytes`
    를 넘으면 응답 뒤 종료해 할당자가 쥐고 있던 메모리를 OS 에 돌려준다
    (부모는 다음 요청 때 새 자식을 띄운다). 그보다 작아도 `linger_bytes` 를
    넘으면 `linger_sec` 만 다음 요청을 기다린다 — 이어지는 조회는 빠르게 받고,
    사용자가 끝나면 큰 메모리를 오래 쥐고 있지 않는다.
    """
    # 자식 안에서 다시 계산 프로세스를 띄우지 않게 하는 표식 — et_run_service.enabled() 가 본다.
    os.environ["FLOW_REFORMATIZE_IN_CHILD"] = "1"
    nice = lower_priority()
    try:
        from routers import reformatize as rf
    except BaseException as exc:  # 부모가 운영 프로세스 계산으로 폴백한다
        resp_q.put({"type": "fatal", "error": f"{type(exc).__name__}: {exc}"})
        return
    resp_q.put({"type": "ready", "pid": os.getpid(), "threads": _PINNED, "nice": nice})
    wait = float(idle_sec)
    while True:
        try:
            job = req_q.get(timeout=max(1.0, wait))
        except _queue.Empty:
            return
        if not job:
            return
        job_id = job.get("id")

        def _progress(phase, done=None, total=None, _jid=job_id):
            resp_q.put({"type": "progress", "id": _jid, "phase": str(phase or ""),
                        "done": done, "total": total})

        wide = None
        try:
            f = rf.Filters(**(job.get("filters") or {}))
            wide, out_cols, errors, vehicle_csv, table, raw_rows, notice = rf._compute(
                job["product"], f,
                selected_items=job.get("selected_items") or None,
                max_mb=job.get("max_mb"),
                auto_trim=bool(job.get("auto_trim")),
                progress=_progress,
            )
            _progress(f"결과 표 전달 준비 중 ({wide.height:,}행)")
            # 무압축 IPC — dtype 을 그대로 보존하고, 부모는 복사 한 번으로 읽는다.
            wide.write_ipc(str(job["path"]), compression="uncompressed")
            resp_q.put({
                "type": "result", "id": job_id, "path": str(job["path"]),
                "out_cols": list(out_cols), "errors": list(errors),
                "vehicle_csv": vehicle_csv, "table": table,
                "raw_rows": int(raw_rows), "notice": notice,
                "rows": int(wide.height), "cols": int(wide.width),
            })
        except BaseException as exc:  # 자식은 반드시 종결 메시지를 보낸다
            detail = getattr(exc, "detail", None)
            resp_q.put({
                "type": "error", "id": job_id,
                "error": str(detail if detail is not None else exc),
                "status": int(getattr(exc, "status_code", 0) or 0),
            })
        finally:
            wide = None
            with rf._CACHE_LOCK:
                rf._CACHE.clear()
                rf._RAW_CACHE.clear()
            gc.collect()
        rss = _rss_bytes()
        if recycle_bytes > 0 and rss > recycle_bytes:
            return
        wait = min(float(idle_sec), float(linger_sec)) if 0 < linger_bytes < rss else float(idle_sec)
