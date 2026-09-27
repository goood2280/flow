# -*- coding: utf-8 -*-
"""core/et_run_service.py — ET 조회(/run) 계산을 맡는 상주 계산 프로세스.

왜 필요한가
-----------
ET 조회 1건은 수백 MB~수 GB 의 raw parquet 을 읽어 group_by·pivot 한다. 다운로드는
이미 spawn 자식에서 돌지만(`routers.reformatize._run_download_isolated`), 화면 조회
(/run)는 운영 API 프로세스 안에서 제한 없이 돌았다. 그러면

1. SplitTable 검색과 **같은 polars 스레드풀**을 나눠 써서 검색이 느려지고
   (측정: ET 조회 동시 실행 시 SplitTable 첫 조회 최대 50ms → 268ms),
2. 계산에 쓴 메모리가 운영 프로세스 할당자에 남아 메모리 워치독이 SplitTable
   캐시부터 축출한다.

어떻게 하나
-----------
spawn 자식(기본 1개)을 띄워 두고 계산 요청을 하나씩 넘긴다. 조회마다 새로 띄우면
import 비용(1~2초)이 매번 붙으므로 한 번 띄운 자식을 재사용한다.

- 자식은 스레드 2개·낮은 OS 우선순위로 돈다(`core.et_run_child`).
- 유휴 `FLOW_REFORMATIZE_RUN_IDLE_SEC`(기본 600초)면 스스로 종료해 메모리를 돌려준다.
- 계산 뒤 RSS 가 `FLOW_REFORMATIZE_RUN_RECYCLE_MB`(기본 1536)를 넘으면 종료한다.
- 제한시간·메모리 한도 초과, 호스트 메모리 부족이면 부모가 자식을 끝내고 오류를 준다.
- 결과 wide 표는 로컬 임시 폴더의 Arrow IPC 파일로 넘긴다(dtype 그대로 보존).
- 동시 계산 수는 슬롯 수로 제한하고, 나머지는 대기열에서 기다린다.

자식을 띄울 수 없는 환경(spawn 불가·import 실패)이면 `ServiceUnavailable` 을 던져
호출측이 예전처럼 운영 프로세스에서 계산하게 한다 — 기능이 멈추지 않는 것이 우선이다.
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import queue
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("flow.reformatize")

_OFF = {"0", "false", "no", "off"}


class ServiceUnavailable(RuntimeError):
    """계산 프로세스를 쓸 수 없다 — 호출측은 운영 프로세스 계산으로 폴백한다."""


class _ChildGone(Exception):
    """요청을 받기 전에 자식이 정상 종료(유휴 만료·재활용)했다 — 새로 띄워 1회 재시도."""


class RunError(Exception):
    """자식이 보고한 계산 오류(HTTP 상태 포함) — 호출측이 HTTPException 으로 바꾼다."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = int(status or 500)
        self.detail = str(detail or "ET 조회 계산 실패")


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


# 자식을 띄우지 못하면(spawn 불가·import 실패) 한동안 운영 프로세스 계산으로 간다 —
# 조회마다 1~2초씩 기동을 재시도하다 실패하는 것을 막는 차단기.
_DISABLED_UNTIL = 0.0
_DISABLED_REASON = ""


def _trip(reason: str) -> None:
    global _DISABLED_UNTIL, _DISABLED_REASON
    cooldown = _env_float("FLOW_REFORMATIZE_RUN_RETRY_SEC", 600.0, 10.0, 86400.0)
    _DISABLED_UNTIL = time.monotonic() + cooldown
    _DISABLED_REASON = str(reason or "")[:300]
    logger.warning("ET 조회 계산 프로세스 사용 중지 %d초 (운영 프로세스에서 계산): %s",
                   int(cooldown), _DISABLED_REASON)


def enabled() -> bool:
    if os.environ.get("FLOW_REFORMATIZE_IN_CHILD") == "1":
        return False
    if time.monotonic() < _DISABLED_UNTIL:
        return False
    return str(os.environ.get("FLOW_REFORMATIZE_RUN_ISOLATION", "1")).strip().lower() not in _OFF


def slot_count() -> int:
    """동시에 도는 ET 조회 계산 수. 기본: 8코어 미만 1, 그 이상 2."""
    try:
        cores = int(os.cpu_count() or 1)
        from core.runtime_limits import effective_cpu_count
        cores = int(effective_cpu_count())
    except Exception:
        pass
    default = 1 if cores < 8 else 2
    return int(_env_float("FLOW_REFORMATIZE_RUN_PROCS", default, 1, 4))


def idle_sec() -> float:
    return _env_float("FLOW_REFORMATIZE_RUN_IDLE_SEC", 600.0, 30.0, 86400.0)


def timeout_sec() -> float:
    return _env_float("FLOW_REFORMATIZE_RUN_TIMEOUT_SEC", 300.0, 10.0, 3600.0)


def queue_wait_sec() -> float:
    return _env_float("FLOW_REFORMATIZE_RUN_QUEUE_WAIT_SEC", 120.0, 1.0, 1800.0)


def recycle_bytes() -> int:
    return int(_env_float("FLOW_REFORMATIZE_RUN_RECYCLE_MB", 1536.0, 0.0, 262144.0) * 1024 * 1024)


def linger_bytes() -> int:
    """계산 뒤 이보다 크면 짧게만(linger_sec) 다음 요청을 기다리고 종료한다 (기본 512MB)."""
    return int(_env_float("FLOW_REFORMATIZE_RUN_LINGER_MB", 512.0, 0.0, 262144.0) * 1024 * 1024)


def linger_sec() -> float:
    return _env_float("FLOW_REFORMATIZE_RUN_LINGER_SEC", 120.0, 10.0, 86400.0)


def max_rss_bytes() -> int:
    """자식 1개가 쓸 수 있는 메모리 상한. 기본 = 호스트(컨테이너) 총량의 30%, 2~8GB."""
    raw = os.environ.get("FLOW_REFORMATIZE_RUN_MAX_RSS_MB", "")
    if str(raw).strip():
        return int(_env_float("FLOW_REFORMATIZE_RUN_MAX_RSS_MB", 4096.0, 256.0, 1048576.0) * 1024 * 1024)
    total_gb = 0.0
    try:
        from core.runtime_limits import system_memory_snapshot
        total_gb = float(system_memory_snapshot().get("system_memory_total_gb") or 0.0)
    except Exception:
        total_gb = 0.0
    gb = min(8.0, max(2.0, total_gb * 0.30)) if total_gb > 0 else 4.0
    return int(gb * 1024 ** 3)


def lowmem_kill_bytes() -> int:
    """호스트 메모리 부족 시 끊을 자식 크기 기준 (기본 1024MB)."""
    return int(_env_float("FLOW_REFORMATIZE_RUN_LOWMEM_KILL_MB", 1024.0, 0.0, 1048576.0) * 1024 * 1024)


def tmp_dir() -> Path:
    raw = os.environ.get("FLOW_REFORMATIZE_RUN_DIR", "").strip()
    base = Path(raw) if raw else Path(tempfile.gettempdir()) / "flow_et_run"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _sweep_tmp(max_age_sec: float = 3600.0) -> None:
    """비정상 종료로 남은 결과 파일 청소 (자식을 새로 띄울 때 1회)."""
    try:
        now = time.time()
        for fp in tmp_dir().glob("*.arrow"):
            try:
                if now - fp.stat().st_mtime > max_age_sec:
                    fp.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _unlink(path: Path | str | None) -> None:
    if not path:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.debug("ET 조회 결과 파일 삭제 실패: %s", path, exc_info=True)


def _system_memory_low() -> bool:
    try:
        from core.runtime_limits import system_memory_snapshot
        return bool(system_memory_snapshot().get("system_memory_low"))
    except Exception:
        return False


class _Slot:
    """상주 자식 1개 — 한 번에 요청 1건만 처리한다(점유는 _FREE 큐가 보장)."""

    def __init__(self, idx: int):
        self.idx = idx
        self.proc = None
        self.req_q = None
        self.resp_q = None
        self.pid = 0
        self.jobs = 0
        self.spawned_at = 0.0
        self.last_used = 0.0

    def alive(self) -> bool:
        return self.proc is not None and self.proc.is_alive()

    def spawn(self) -> None:
        self.discard()
        _sweep_tmp()
        try:
            from core.et_run_child import serve
            ctx = multiprocessing.get_context("spawn")
            self.req_q = ctx.Queue()
            self.resp_q = ctx.Queue()
            proc = ctx.Process(
                target=serve,
                args=(self.req_q, self.resp_q),
                kwargs={"idle_sec": idle_sec(), "recycle_bytes": recycle_bytes(),
                        "linger_bytes": linger_bytes(), "linger_sec": linger_sec()},
                name=f"flow-et-run-{self.idx}",
                daemon=True,
            )
            proc.start()
        except Exception as exc:
            self.discard()
            _trip(f"spawn 실패: {exc}")
            raise ServiceUnavailable(f"ET 조회 계산 프로세스를 시작하지 못했습니다: {exc}") from exc
        self.proc = proc
        self.pid = int(proc.pid or 0)
        self.jobs = 0
        self.spawned_at = self.last_used = time.monotonic()
        logger.info("ET 조회 계산 프로세스 시작 slot=%d pid=%s", self.idx, self.pid)

    def rss(self) -> int:
        if not self.pid:
            return 0
        try:
            import psutil  # type: ignore

            return int(psutil.Process(self.pid).memory_info().rss)
        except Exception:
            return 0

    def discard(self) -> None:
        """자식을 끝내고 큐를 닫는다 (살아 있으면 강제 종료)."""
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                if proc.is_alive():
                    proc.terminate()
                    proc.join(timeout=3.0)
                    if proc.is_alive() and hasattr(proc, "kill"):
                        proc.kill()
                        proc.join(timeout=2.0)
                else:
                    proc.join(timeout=0.1)
            except Exception:
                pass
        for q in (self.req_q, self.resp_q):
            if q is None:
                continue
            try:
                q.close()
                q.cancel_join_thread()
            except Exception:
                pass
        self.req_q = self.resp_q = None
        self.pid = 0


_INIT_LOCK = threading.Lock()
_SLOTS: list[_Slot] = []
_FREE: "queue.Queue[_Slot] | None" = None
_WAITING = 0
_WAITING_LOCK = threading.Lock()
_STATS = {"jobs": 0, "errors": 0, "timeouts": 0, "memory_kills": 0, "crashes": 0,
          "spawns": 0, "fallbacks": 0}


def _free_queue() -> "queue.Queue[_Slot]":
    global _FREE
    with _INIT_LOCK:
        if _FREE is None:
            # LIFO: 방금 쓴(이미 떠 있는) 자식을 다시 쓴다. FIFO 면 동시 요청이 없어도
            # 슬롯을 번갈아 써서 자식이 슬롯 수만큼 떠 메모리를 그만큼 잡는다.
            _FREE = queue.LifoQueue()
            for i in range(slot_count()):
                slot = _Slot(i)
                _SLOTS.append(slot)
                _FREE.put(slot)
        return _FREE


def _acquire(report: Callable) -> _Slot:
    global _WAITING
    free = _free_queue()
    try:
        return free.get_nowait()
    except queue.Empty:
        pass
    with _WAITING_LOCK:
        _WAITING += 1
        ahead = _WAITING
    deadline = time.monotonic() + queue_wait_sec()
    try:
        while True:
            report(f"다른 ET 조회 계산이 끝나기를 기다리는 중 (대기 {ahead}건)")
            try:
                return free.get(timeout=1.0)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    raise RunError(
                        429,
                        "ET 조회 계산이 몰려 있습니다. 잠시 후 다시 조회해 주세요 "
                        f"(대기 {int(queue_wait_sec())}초 초과).",
                    )
    finally:
        with _WAITING_LOCK:
            _WAITING = max(0, _WAITING - 1)


def _noop(*_a, **_k) -> None:
    return None


def compute(job: dict[str, Any], *, progress: Callable | None = None) -> dict[str, Any]:
    """자식에게 계산 1건을 맡기고 결과 메타(+IPC 경로)를 돌려준다.

    job: {product, filters(dict), selected_items(list|None), max_mb, auto_trim}
    반환: {path, out_cols, errors, vehicle_csv, table, raw_rows, notice, rows, cols}
    — 호출측이 path 의 IPC 를 읽고 지운다.
    """
    report = progress or _noop
    slot = _acquire(report)
    try:
        try:
            return _run_on_slot(slot, job, report)
        except _ChildGone:
            return _run_on_slot(slot, job, report)
    finally:
        slot.last_used = time.monotonic()
        _free_queue().put(slot)


def _run_on_slot(slot: _Slot, job: dict[str, Any], report: Callable) -> dict[str, Any]:
    fresh = False
    # 유휴 만료 직전의 자식에게 보내면 자식이 요청을 못 보고 종료할 수 있다 — 미리 교체.
    # (큰 작업 뒤의 자식은 linger_sec 만 기다리므로 그 기준도 본다. 놓쳐도 _ChildGone 재시도.)
    if slot.alive():
        idle_for = time.monotonic() - slot.last_used
        limit = idle_sec()
        if 0 < linger_bytes() < slot.rss():
            limit = min(limit, linger_sec())
        if idle_for > limit - 5.0:
            slot.discard()
    if not slot.alive():
        report("ET 계산 프로세스 준비 중")
        slot.spawn()
        _STATS["spawns"] += 1
        fresh = True
    job_id = uuid.uuid4().hex
    path = tmp_dir() / f"{job_id}.arrow"
    try:
        slot.req_q.put(dict(job, id=job_id, path=str(path)))
    except Exception as exc:
        slot.discard()
        raise ServiceUnavailable(f"ET 조회 계산 요청 전달 실패: {exc}") from exc

    timeout = timeout_sec()
    # 새로 띄운 자식은 import(1~2초)가 끝나야 계산을 시작한다 — 그 시간은 빼 준다.
    deadline = time.monotonic() + timeout + (30.0 if fresh else 0.0)
    rss_limit = max_rss_bytes()
    last_guard = 0.0
    heard = False                             # 이 요청에 대한 자식 응답을 받았는가
    ready = not fresh                         # 자식이 살아서 무엇이든 보냈는가
    try:
        while True:
            try:
                msg = slot.resp_q.get(timeout=0.5)
            except queue.Empty:
                msg = None
            except Exception as exc:          # 큐가 깨졌다 = 자식이 죽었다
                slot.discard()
                _STATS["crashes"] += 1
                raise RunError(500, f"ET 조회 계산 프로세스와 연결이 끊겼습니다: {exc}") from exc
            if msg is None:
                if not slot.alive():
                    code = getattr(slot.proc, "exitcode", None)
                    slot.discard()
                    if code == 0 and not heard and not fresh:
                        raise _ChildGone()
                    if not ready:
                        # 기동 단계에서 죽었다 — 계산 문제가 아니라 환경 문제다.
                        _trip(f"기동 중 종료 exit={code}")
                        raise ServiceUnavailable(f"ET 조회 계산 프로세스가 기동 중 종료했습니다 (exit={code})")
                    _STATS["crashes"] += 1
                    raise RunError(
                        500,
                        f"ET 조회 계산 프로세스가 비정상 종료했습니다 (exit={code}). 메모리 부족일 수 "
                        "있으니 최근 N일·LOT·STEP 필터를 좁혀 다시 조회해 주세요.",
                    )
                now = time.monotonic()
                if now >= deadline:
                    slot.discard()
                    _STATS["timeouts"] += 1
                    raise RunError(
                        504,
                        f"ET 조회 계산이 {int(timeout)}초를 넘어 서버 보호를 위해 중단했습니다. "
                        "최근 N일·LOT·STEP 필터를 좁히거나 다운로드(대기열)를 이용해 주세요.",
                    )
                if now - last_guard >= 2.0:
                    last_guard = now
                    rss = slot.rss()
                    if rss_limit > 0 and rss > rss_limit:
                        slot.discard()
                        _STATS["memory_kills"] += 1
                        raise RunError(
                            400,
                            f"ET 조회 데이터가 너무 커서 계산을 중단했습니다 (계산 메모리 "
                            f"{rss / 1024 ** 3:.1f}GB > 한도 {rss_limit / 1024 ** 3:.1f}GB). "
                            "최근 N일·LOT·STEP 필터를 좁혀 주세요.",
                        )
                    # 호스트 메모리가 부족해도 자식이 작으면 원인이 아니다 — 그때 끊으면
                    # 늘 빠듯한 서버에서 조회만 못 쓰게 된다. 자식이 클 때만 끊는다.
                    if rss >= lowmem_kill_bytes() and _system_memory_low():
                        slot.discard()
                        _STATS["memory_kills"] += 1
                        raise RunError(
                            503,
                            "서버 메모리가 부족해 ET 조회 계산을 중단했습니다. 잠시 후 다시 조회하거나 "
                            "필터를 좁혀 주세요.",
                        )
                continue
            ready = True
            kind = msg.get("type")
            if kind == "fatal":
                slot.discard()
                _trip(f"초기화 실패: {msg.get('error')}")
                raise ServiceUnavailable(f"ET 조회 계산 프로세스 초기화 실패: {msg.get('error')}")
            if msg.get("id") != job_id:
                continue                      # ready 알림 등
            heard = True
            if kind == "progress":
                report(msg.get("phase") or "", msg.get("done"), msg.get("total"))
                continue
            if kind == "error":
                _STATS["errors"] += 1
                raise RunError(int(msg.get("status") or 500), str(msg.get("error") or ""))
            if kind == "result":
                slot.jobs += 1
                _STATS["jobs"] += 1
                return msg
    except BaseException:
        _unlink(path)
        raise


def prewarm() -> bool:
    """쉬고 있는 슬롯 하나를 미리 띄운다(화면에서 제품을 고른 순간 호출).

    조회 버튼을 누를 때쯤 자식의 import 가 끝나 있게 한다. 바쁜 슬롯은 건드리지
    않고, 이미 살아 있으면 아무것도 안 한다. 실패는 조용히 무시한다.
    """
    if not enabled():
        return False
    free = _free_queue()
    try:
        slot = free.get_nowait()
    except queue.Empty:
        return False
    try:
        if slot.alive():
            return False
        slot.spawn()
        _STATS["spawns"] += 1
        slot.last_used = time.monotonic()
        return True
    except Exception:
        logger.debug("ET 조회 계산 프로세스 예열 실패", exc_info=True)
        return False
    finally:
        free.put(slot)


def note_fallback() -> None:
    _STATS["fallbacks"] += 1


def status() -> dict[str, Any]:
    """관리 화면용 — 슬롯별 pid·생존·처리 건수·RSS."""
    _free_queue()
    slots = []
    for slot in list(_SLOTS):
        alive = slot.alive()
        slots.append({"slot": slot.idx, "alive": alive, "pid": slot.pid if alive else 0,
                      "jobs": slot.jobs, "rss_bytes": slot.rss() if alive else 0})
    paused = max(0.0, _DISABLED_UNTIL - time.monotonic())
    return {"enabled": enabled(), "paused_sec": round(paused, 1),
            "paused_reason": _DISABLED_REASON if paused else "",
            "slots": slots, "waiting": _WAITING, "stats": dict(_STATS),
            "max_rss_bytes": max_rss_bytes(), "timeout_sec": timeout_sec(), "idle_sec": idle_sec()}


def shutdown() -> None:
    """테스트·종료용 — 모든 자식을 끝낸다."""
    for slot in list(_SLOTS):
        slot.discard()
