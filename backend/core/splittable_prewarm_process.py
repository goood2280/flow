"""core/splittable_prewarm_process.py — SplitTable KNOB 예열을 별도 프로세스에서 돌린다.

왜 필요한가
-----------
예열은 최근 검색·진행 중인 root 마다 SplitTable 표를 미리 계산해 둔다. 표 계산 시간의
대부분(로컬 측정 83%)은 파이썬 코드라, 운영 API 프로세스의 스레드로 돌면 사용자 요청과
GIL(파이썬이 한 번에 쓰는 코어 1개)을 나눠 쓴다. 대형 서버(8코어·128GB)는 예열 대상이
최대 3,000 root 라 그 시간이 길고, 그동안 모든 탭의 파이썬 부분이 같이 느려진다.

어떻게 하나
-----------
spawn 자식 하나가 자기 GIL·낮은 OS 우선순위·코어 절반의 Polars 풀로 예열 루프를 돈다.
결과는 이미 있는 디스크 view 캐시(`cache/split_table_view_payload`)에 남고, 운영 프로세스는
RAM 에 없을 때 그 사본을 같은 hard/soft 시그니처 계약으로 읽는다 — 두 프로세스가 주고받는
메시지는 없다.

- 자식은 계산만 한다. 표 계산 중 필요한 캐시 빌드(lookup·pivot·FAB 인덱스)를 대기열에
  넣지 않는다 — 빌드는 운영 프로세스의 제품 순환·공용 캐시 슬롯이 맡는다.
- 부모가 죽으면(감시기의 강제 종료 포함) 자식은 몇 초 안에 스스로 끝난다.
- 자식이 비정상 종료하면 부모가 백오프로 다시 띄우고, 한 시간에 3번 넘게 죽으면 예전처럼
  운영 프로세스 스레드로 예열한다(기능이 멈추지 않는 것이 우선).
- `FLOW_SPLITTABLE_PREWARM_PROCESS=0/1` 로 강제. 기본은 대형 운영 서버에서만 켠다.
  스레드 수 `FLOW_SPLITTABLE_PREWARM_THREADS`(기본 코어의 절반).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable

logger = logging.getLogger("flow.splittable.prewarm")

_OFF = {"0", "false", "no", "off"}
_ON = {"1", "true", "yes", "on"}
_CHILD_MARKER = "FLOW_PREWARM_CHILD"
_PARENT_CHECK_SEC = 5.0
_MAX_CRASHES_PER_HOUR = 3

_STATE: dict = {"mode": "", "pid": 0, "started_at": 0.0, "restarts": 0, "last_exit": None, "fallback_reason": ""}
_STATE_LOCK = threading.Lock()


def in_child() -> bool:
    return os.environ.get(_CHILD_MARKER) == "1"


def enabled() -> bool:
    raw = str(os.environ.get("FLOW_SPLITTABLE_PREWARM_PROCESS", "") or "").strip().lower()
    if in_child() or os.environ.get("FLOW_REFORMATIZE_IN_CHILD") == "1":
        return False
    if raw in _OFF:
        return False
    if raw in _ON:
        return True
    try:
        from core import cache_budget
        return bool(cache_budget.large_host())
    except Exception:
        return False


def child_threads() -> int:
    raw = str(os.environ.get("FLOW_SPLITTABLE_PREWARM_THREADS", "") or "").strip()
    if raw:
        try:
            return max(1, int(float(raw)))
        except ValueError:
            pass
    try:
        from core.runtime_limits import effective_cpu_count
        return max(1, int(effective_cpu_count()) // 2)
    except Exception:
        return 2


def status() -> dict:
    with _STATE_LOCK:
        return dict(_STATE)


def _set_state(**kwargs) -> None:
    with _STATE_LOCK:
        _STATE.update(kwargs)


# ── 자식 ───────────────────────────────────────────────────────────────────────

def _parent_alive(pid: int, created: float) -> bool:
    try:
        import psutil  # type: ignore
    except Exception:
        return True  # 확인할 수 없으면 살아 있다고 본다 — 부모 종료 때 daemon 정리가 끝낸다.
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and abs(proc.create_time() - created) < 1.0
    except psutil.NoSuchProcess:
        return False
    except Exception:
        return True


def _compute_only(splittable_module) -> None:
    """자식에서 표 계산이 캐시 빌드를 대기열에 넣지 않게 한다(이 프로세스 안에서만)."""
    from core import ml_table_lookup

    splittable_module._enqueue_pivot_cache_build = lambda *a, **k: False
    splittable_module._enqueue_fab_lot_index_build = lambda *a, **k: False
    splittable_module._enqueue_latest_lot_index_build = lambda *a, **k: False
    ml_table_lookup.enqueue_build = lambda *a, **k: {
        "ok": True, "queued": False, "skipped": True, "reason": "prewarm_child_compute_only"}


def _child_main(parent_pid: int, parent_created: float, threads: int) -> None:
    # polars 를 import 하기 전에 스레드 상한·우선순위를 심는다(풀 크기는 최초 import 때 고정).
    from core.reformatize_child import lower_priority, pin_threads

    pin_threads(threads)
    lower_priority()
    os.environ[_CHILD_MARKER] = "1"
    # 캐시 풀의 주인은 운영 프로세스다. 자식 RAM 캐시는 다음 주기 중복 계산만 막으면 된다.
    os.environ["FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB"] = os.environ.get(
        "FLOW_SPLITTABLE_PREWARM_CHILD_VIEW_MB", "") or "256"
    os.environ["FLOW_CACHE_TOTAL_BUDGET_FRACTION"] = "0.1"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] [prewarm-child] %(message)s")

    from routers import splittable

    _compute_only(splittable)
    stop = splittable._KNOB_PREWARM_STOP

    def watch_parent() -> None:
        while not stop.wait(_PARENT_CHECK_SEC):
            if not _parent_alive(parent_pid, parent_created):
                logger.info("parent %s gone — prewarm child exits", parent_pid)
                stop.set()
                time.sleep(30.0)  # 진행 중인 계산 하나가 끝날 시간
                os._exit(0)

    threading.Thread(target=watch_parent, name="prewarm-parent-watch", daemon=True).start()
    logger.info("KNOB prewarm child started (pid=%s, polars threads=%s)", os.getpid(), threads)
    splittable._knob_prewarm_loop()


# ── 부모 ───────────────────────────────────────────────────────────────────────

def start(fallback: Callable[[], None]) -> bool:
    """자식을 띄우고 감시한다. 띄울 수 없거나 자꾸 죽으면 fallback()(스레드 예열)."""
    import multiprocessing

    try:
        import psutil  # type: ignore
        created = float(psutil.Process(os.getpid()).create_time())
    except Exception:
        created = 0.0
    ctx = multiprocessing.get_context("spawn")
    threads = child_threads()

    def spawn():
        proc = ctx.Process(target=_child_main, args=(os.getpid(), created, threads),
                           name="flow-splittable-prewarm", daemon=True)
        proc.start()
        _set_state(mode="process", pid=proc.pid, started_at=time.time())
        logger.info("SplitTable KNOB prewarm child spawned (pid=%s, threads=%s)", proc.pid, threads)
        return proc

    try:
        proc = spawn()
    except Exception as exc:
        logger.warning("KNOB prewarm child spawn failed — falling back to thread: %s", exc)
        _set_state(mode="thread", fallback_reason=f"spawn failed: {exc}")
        fallback()
        return False

    def supervise(first) -> None:
        current = first
        crashes: list[float] = []
        backoff = 5.0
        while True:
            current.join()
            code = current.exitcode
            _set_state(last_exit={"code": code, "at": time.time()})
            if code == 0:
                return  # 스스로 정상 종료(중지 요청) — 다시 띄우지 않는다
            now = time.time()
            crashes = [t for t in crashes if now - t < 3600.0] + [now]
            if len(crashes) > _MAX_CRASHES_PER_HOUR:
                logger.warning("KNOB prewarm child keeps exiting (code=%s) — falling back to thread", code)
                _set_state(mode="thread", pid=0, fallback_reason=f"child exited {len(crashes)}x/h, last code {code}")
                fallback()
                return
            logger.warning("KNOB prewarm child exited (code=%s) — restart in %.0fs", code, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300.0)
            try:
                current = spawn()
                with _STATE_LOCK:
                    _STATE["restarts"] = int(_STATE.get("restarts") or 0) + 1
            except Exception as exc:
                logger.warning("KNOB prewarm child respawn failed — falling back to thread: %s", exc)
                _set_state(mode="thread", pid=0, fallback_reason=f"respawn failed: {exc}")
                fallback()
                return

    threading.Thread(target=supervise, args=(proc,), name="splittable-prewarm-supervisor", daemon=True).start()
    return True
