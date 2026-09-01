"""서버 단위 캐시 스캔 게이트 — 한 서버에서 스캔은 언제나 하나만 돈다.

캐시 관리 화면의 수동 스캔(통합 스캔·전체 셋업), 관리자 개별 갱신 API, 예약
스캔(FAB 매칭·제품 원본 RAM 스케줄러)이 모두 이 게이트를 통과한다. 예전에는
각 작업이 자기 job state 의 running 플래그만 봤기 때문에, 통합 스캔이 2/3 단계에
들어간 사이 스케줄러가 FAB 전체 스캔을 새로 시작하는 식으로 **한 서버에서 두
스캔이 겹쳤다** — 개발서버 OOM 과 "스캔이 끝나지 않는다"의 직접 원인이다.

계약:

- 동시에 실행되는 작업은 항상 1개다(FIFO 대기열).
- **앞 작업이 실패하거나 예외로 죽어도 다음 작업은 반드시 실행된다.** 워커
  스레드는 작업 예외를 삼키고 다음 항목으로 넘어간다.
- 같은 작업이 이미 대기 중이면 중복 적재하지 않는다(`dedupe_key`). 스케줄러가
  긴 스캔 뒤에서 같은 tick 을 수십 개 쌓는 것을 막는다.
- 요청자는 절대 블로킹되지 않는다. `submit()` 은 즉시 대기 순번을 돌려주고,
  진행 상황은 캐시 이벤트 로그와 `snapshot()` 으로 본다.

게이트는 **프로세스(서버) 로컬**이다. 운영/개발 2서버 분산에서 개발 워커로
오프로드된 작업은 그쪽 프로세스의 게이트가 관리한다.

## 대기열 밖에서 도는 캐시 작업 — `exclusive()`

무거운 캐시 산출(FAB 매칭 / 제품 원본 pivot / 랏 lookup 빌드)은 이 대기열을 타지
않는 경로로도 시작된다:

- `ml_table_lookup` 의 빌드 스레드(`enqueue_build` → `_worker_loop`)
- 개발 worker 가 운영에서 넘겨받아 실행하는 오프로드 태스크(`worker_dispatch`)

예전에는 이 셋이 **각자 다른 단일 슬롯**(scan gate / `_LOCAL_HEAVY_GATE` /
worker `slots`)을 잡아서, 한 서버에서 서로 다른 제품의 랏캐시 빌드가 동시에
돌 수 있었다. `exclusive()` 는 대기열 워커와 **같은 슬롯**을 블로킹으로 잡아
"한 서버 = 한 제품 × 한 캐시 작업"을 실제로 보장한다.

같은 스레드 재진입은 허용한다 — 대기열 작업이 내부에서 `run_heavy()` 로컬
폴백을 타면 이미 자기가 슬롯을 들고 있기 때문이다(재진입을 막으면 자기 자신을
기다리는 교착이 된다).

root RAM 예열(warmup)은 파티션을 읽어 메모리에 올리는 가벼운 작업이라 이 슬롯을
잡지 않는다 — 빌드가 도는 동안에도 예열은 진행된다.

## 취소 — `cancel()`

캐시 작업은 한 건이 수십 분씩 걸리므로 "지금 도는 것을 끊고 다음으로 넘기고
싶다"가 실제 운영 요구다. 스레드를 강제로 죽이지는 않는다 — parquet 쓰기
중간에 끊으면 파티션 디렉터리가 깨진다. 대신 **협조적 취소**다:

- **대기 중** 작업은 큐에서 즉시 제거한다(부작용 없음).
- **실행 중** 작업은 취소 플래그만 세우고, 작업 쪽이 안전한 지점(제품 경계·
  배치 경계 — 이미 메모리 가드/사용자 양보를 확인하는 자리)에서
  `cancel_requested()` 를 보고 스스로 접는다.

취소한 작업은 **이어받기가 없다.** 부분 산출을 버리고 다음 스캔에서 처음부터
다시 빌드한다(FAB 매칭 캐시의 기존 '이 제품 중단'과 같은 계약). 완성돼 있던
캐시 파일은 건드리지 않는다.
"""

from __future__ import annotations

import contextlib
import datetime
import logging
import os
import threading
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 대기열 상한 — 넘으면 새 요청을 거절한다. 스캔은 하나가 수십 분 걸릴 수 있어
# 무한 적재를 허용하면 "몇 시간 뒤에 뜬금없이 도는 작업"이 쌓인다.
MAX_PENDING = 16

_COND = threading.Condition()
_PENDING: deque[dict[str, Any]] = deque()
_CURRENT: dict[str, Any] | None = None
_LAST: dict[str, Any] | None = None
_WORKER: threading.Thread | None = None
_SEQ = 0

# 서버 전체에서 캐시 작업 1건만 허용하는 슬롯. 대기열 워커와 exclusive() 가 공유한다.
_SLOT = threading.Semaphore(1)
_TLS = threading.local()
# 취소 요청된 작업 id → {"by","at"}. 실행 중 작업이 안전한 지점에서 확인한다.
# 작업이 끝나면 지워지므로 무한히 자라지 않는다(상한도 함께 둔다).
_CANCELED: dict[str, dict[str, Any]] = {}
_CANCEL_MAX = 64
# 대기열 밖에서 슬롯을 잡고 있는 작업 (화면/진단용).
_EXTERNAL: dict[str, Any] | None = None
_EXTERNAL_LOCK = threading.Lock()
_EXCLUSIVE_WAIT_SEC_DEFAULT = 7 * 24 * 3600.0
_PENDING_STALE_SEC_DEFAULT = 7 * 24 * 3600.0


def _exclusive_wait_sec() -> float:
    try:
        raw = os.environ.get("FLOW_CACHE_EXCLUSIVE_WAIT_SEC", "")
        value = float(raw) if raw else _EXCLUSIVE_WAIT_SEC_DEFAULT
    except Exception:
        value = _EXCLUSIVE_WAIT_SEC_DEFAULT
    return max(1.0, min(7 * 24 * 3600.0, value))


def pending_stale_sec() -> float:
    """대기열에 들어간 뒤 시작되지 않은 작업의 최대 대기 시간."""
    try:
        raw = os.environ.get("FLOW_CACHE_QUEUE_STALE_SEC", "")
        value = float(raw) if raw else _PENDING_STALE_SEC_DEFAULT
    except Exception:
        value = _PENDING_STALE_SEC_DEFAULT
    return max(60.0, min(7 * 24 * 3600.0, value))


def holding() -> bool:
    """이 스레드가 이미 캐시 슬롯을 들고 있는가."""
    return int(getattr(_TLS, "depth", 0)) > 0


@contextlib.contextmanager
def lend():
    """슬롯을 든 채 **다른 스레드의 작업을 기다려야 할 때** 잠시 반납한다.

    수동 스캔의 랏캐시 단계처럼 "빌드를 큐에 넣고 완료를 기다리는" 코드가 있다.
    기다리는 동안 슬롯을 붙들고 있으면 그 빌드가 슬롯을 못 잡아 서로를 기다리는
    교착이 된다. 기다리는 쪽은 실제로 아무 자원도 안 쓰므로 반납하고, 대기가
    끝나면 다시 잡는다(다시 잡을 때 다른 작업 뒤에 설 수 있다 — 어차피 기다리던
    중이라 문제 되지 않는다).

    슬롯을 안 들고 있으면 아무것도 하지 않고 False 를 준다."""
    depth = int(getattr(_TLS, "depth", 0))
    if depth <= 0:
        yield False
        return
    _TLS.depth = 0
    _SLOT.release()
    with _COND:
        _COND.notify()
    try:
        yield True
    finally:
        _SLOT.acquire()
        _TLS.depth = depth


@contextlib.contextmanager
def exclusive(kind: str, label: str, *, product: str = "", source: str = "external",
              timeout: float | None = None):
    """대기열 워커와 같은 슬롯을 블로킹으로 잡는다. `as acquired` 가 False 면 타임아웃.

    같은 스레드에서 이미 잡고 있으면 재진입으로 통과한다(항상 True).

    **취소 가능하다.** 예전에는 `_EXTERNAL["id"]` 가 고정 문자열 `"exclusive"` 라
    화면에 현재 작업으로 뜨는데도 중단 버튼이 404 로 떨어졌고, 블록 안에서 도는
    빌더는 자기 취소 여부를 물어볼 id 조차 없었다. 이제 매 획득마다 고유 id 를
    발급하고 **이 스레드의 `_TLS.task_id` 로 심는다** — 블록 안에서 도는 코드는
    대기열 작업과 똑같이 인자 없는 `cancel_requested()` 로 확인할 수 있다
    (`run_heavy` → `_run_local_heavy` → 빌더가 전부 이 스레드다).
    """
    global _EXTERNAL, _SEQ
    if holding():
        _TLS.depth = int(getattr(_TLS, "depth", 0)) + 1
        try:
            yield True
        finally:
            _TLS.depth -= 1
        return
    wait = _exclusive_wait_sec() if timeout is None else max(0.0, float(timeout))
    started = time.monotonic()
    if not _SLOT.acquire(timeout=wait):
        logger.warning("cache gate timeout after %.0fs: %s (%s)", wait, label, kind)
        _record(f"[스캔 큐] 대기 시간 초과: {label} — 다른 캐시 작업이 {int(wait)}초 넘게 진행 중이라 "
                "이번에는 건너뜁니다(다음 주기에 다시 시도).", ok=False, product=product)
        yield False
        return
    waited = time.monotonic() - started
    _TLS.depth = 1
    with _COND:
        _SEQ += 1
        ext_id = f"ext-{_SEQ}"
    prev_task_id = str(getattr(_TLS, "task_id", "") or "")
    _TLS.task_id = ext_id
    with _EXTERNAL_LOCK:
        _EXTERNAL = {
            "id": ext_id, "kind": str(kind or ""), "label": str(label or kind or ""),
            "product": str(product or ""), "source": str(source or "external"),
            "started_mono": time.monotonic(), "started_iso": _now_iso(),
            "waited_sec": round(waited, 1),
        }
    if waited >= 1.0:
        _record(f"[스캔 큐] 시작: {label} (대기 {int(waited)}초 · 다른 캐시 작업이 끝나길 기다림)",
                product=product)
    try:
        yield True
    finally:
        with _EXTERNAL_LOCK:
            _EXTERNAL = None
        with _COND:
            _CANCELED.pop(ext_id, None)
        _TLS.task_id = prev_task_id
        _TLS.depth = 0
        _SLOT.release()
        with _COND:
            _COND.notify()


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _record(message: str, *, ok: bool = True, product: str = "") -> None:
    """캐시 이벤트 로그에 큐 상태를 남긴다(로그 실패는 스캔을 막지 않는다)."""
    try:
        from core.cache_event_log import record
        record("scan", message, ok=ok, product=product or "")
    except Exception:
        logger.debug("scan gate event log failed", exc_info=True)


def _public(task: dict[str, Any] | None, *, now: float | None = None) -> dict[str, Any] | None:
    """외부 노출용 뷰 — 실행 함수(run)는 뺀다."""
    if not task:
        return None
    now = time.monotonic() if now is None else now
    # Queue tasks use started_at; exclusive() tasks use started_mono.  Reading
    # only the former made every worker/off-thread cache build appear to have
    # elapsed for 0 seconds in the cache-management queue.
    started = task.get("started_at") or task.get("started_mono")
    enqueued = task.get("enqueued_mono")
    waited = (
        float(task.get("waited_sec") or 0.0)
        if enqueued is None
        else float((started or now) - float(enqueued or now))
    )
    return {
        "id": task.get("id") or "",
        "kind": task.get("kind") or "",
        "label": task.get("label") or "",
        "product": task.get("product") or "",
        "source": task.get("source") or "",
        "enqueued_at": task.get("enqueued_iso") or "",
        "started_at": task.get("started_iso") or "",
        "waited_sec": round(max(0.0, waited), 1),
        "elapsed_sec": round(float(now - started), 1) if started else 0.0,
    }


def _ensure_worker_locked() -> None:
    """워커 스레드 보장 — 죽어 있으면 다시 띄운다.

    워커가 사라진 채로 대기열만 쌓이면 모든 스캔이 영원히 멈춘다. 대기열은
    유지한 채 스레드만 새로 만들어 이어서 처리한다."""
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    if _WORKER is not None:
        logger.warning("scan gate worker vanished — restarting (pending=%d)", len(_PENDING))
    _WORKER = threading.Thread(target=_worker_loop, name="flow-scan-gate", daemon=True)
    _WORKER.start()


def _reap_stale_pending() -> list[dict[str, Any]]:
    """Remove abandoned pending work after the long safety horizon."""
    now = time.monotonic()
    limit = pending_stale_sec()
    removed: list[dict[str, Any]] = []
    with _COND:
        for task in list(_PENDING):
            queued_at = float(task.get("enqueued_mono") or now)
            if now - queued_at < limit:
                continue
            _PENDING.remove(task)
            removed.append(task)
        if removed:
            _COND.notify_all()
    for task in removed:
        _record(
            f"[스캔 큐] 대기 자동 제거: {task.get('label') or task.get('kind')} — "
            f"{int(limit // 60)}분 동안 시작되지 않아 큐에서 제거했습니다.",
            ok=False,
            product=task.get("product") or "",
        )
    return removed


def _run_task(task: dict[str, Any]) -> bool:
    """작업 1건 실행. 예외를 삼켜 큐가 멈추지 않게 한다."""
    global _CURRENT, _LAST
    ok = True
    error = ""
    try:
        result = task["run"]()
        # skipped(비활성·이미 실행 중)는 실패가 아니다 — 실패로 세면 예약 tick 마다
        # 빨간 로그가 쌓인다.
        if isinstance(result, dict) and result.get("ok") is False and not result.get("skipped"):
            ok = False
            error = str(result.get("error") or result.get("detail") or "").strip()
    except Exception as exc:
        ok = False
        error = f"{type(exc).__name__}: {exc}"
        logger.warning("scan gate task failed kind=%s label=%s: %s",
                       task.get("kind"), task.get("label"), exc, exc_info=True)
    finally:
        finished = time.monotonic()
        with _COND:
            pending_next = _PENDING[0]["label"] if _PENDING else ""
            canceled = _CANCELED.pop(str(task.get("id") or ""), None)
            _LAST = {
                **(_public(task, now=finished) or {}),
                "ok": ok,
                "error": error,
                "canceled": bool(canceled),
                "finished_at": _now_iso(),
            }
            _CURRENT = None
            _COND.notify_all()
    if canceled:
        # 취소는 실패가 아니다 — 사용자가 의도한 중단이므로 그렇게 기록한다.
        _record(f"[스캔 큐] 중단됨: {task.get('label') or task.get('kind')}"
                + (f" — {canceled.get('by')}" if canceled.get("by") else "")
                + (f" · 다음 대기 작업({pending_next})을 이어서 진행합니다."
                   if pending_next else " · 대기 중인 다음 작업은 없습니다."),
                ok=False, product=task.get("product") or "")
        return ok
    if not ok:
        _record(f"[스캔 큐] 실패: {task.get('label') or task.get('kind')}"
                + (f" — {error}" if error else "")
                + (f" · 다음 대기 작업({pending_next})을 이어서 진행합니다."
                   if pending_next else " · 대기 중인 다음 작업은 없습니다."),
                ok=False, product=task.get("product") or "")
    return ok


def _worker_loop() -> None:
    global _CURRENT
    while True:
        try:
            _reap_stale_pending()
            with _COND:
                while not _PENDING:
                    _COND.wait(timeout=300.0)
            # 슬롯을 **꺼내기 전에** 잡는다 — 대기열 밖 캐시 작업(exclusive)이
            # 들고 있으면 여기서 기다린다. 먼저 pop 하면 그 작업이 _CURRENT 로
            # 표시된 채 실행되지 않는 구간이 생긴다.
            # _COND 를 잡은 채로 기다리면 submit() 이 막히므로 반드시 밖에서.
            # 무한 대기 대신 주기적으로 깨어나 로그를 남긴다 — 슬롯을 든 쪽이
            # 멈추면 "대기열이 조용히 영원히 멈춘" 상태가 되어 진단이 어렵다.
            if not _SLOT.acquire(timeout=300.0):
                with _EXTERNAL_LOCK:
                    holder = dict(_EXTERNAL) if _EXTERNAL else None
                logger.warning(
                    "scan gate queue waiting for cache slot (holder=%s)",
                    (holder or {}).get("label") or "unknown",
                )
                continue
            _TLS.depth = 1
            try:
                with _COND:
                    if not _PENDING:
                        continue
                    task = _PENDING.popleft()
                    # 실행 중 작업이 자기 id 로 취소 여부를 볼 수 있게 한다 —
                    # 깊은 호출 스택에서도 인자 없이 cancel_requested() 로 확인.
                    _TLS.task_id = task.get("id") or ""
                    task["started_mono"] = time.monotonic()
                    task["started_at"] = task["started_mono"]
                    task["started_iso"] = _now_iso()
                    _CURRENT = task
                    waited = task["started_mono"] - float(task.get("enqueued_mono") or task["started_mono"])
                    remaining = len(_PENDING)
                if waited >= 1.0:
                    _record(f"[스캔 큐] 시작: {task.get('label')} (대기 {int(waited)}초"
                            + (f" · 뒤에 {remaining}건 남음" if remaining else "") + ")",
                            product=task.get("product") or "")
                _run_task(task)
            finally:
                _TLS.depth = 0
                _TLS.task_id = ""
                _SLOT.release()
        except Exception:
            # 루프 자체는 어떤 경우에도 죽지 않는다 — 죽으면 모든 스캔이 멈춘다.
            logger.warning("scan gate worker loop error", exc_info=True)
            time.sleep(1.0)


def submit(kind: str, label: str, run: Callable[[], Any], *,
           product: str = "", source: str = "manual", dedupe_key: str = "",
           max_pending: int | None = None) -> dict[str, Any]:
    """스캔 작업을 대기열에 넣고 즉시 반환한다.

    반환 dict:
      - `accepted`: 대기열에 새로 들어갔는가
      - `duplicate`: 같은 작업이 이미 대기 중이라 무시했는가
      - `ahead`: 앞에 있는 작업 수(0이면 바로 시작)
      - `running`: 게이트에 실행 중이거나 대기 중인 작업이 있는가
    """
    global _SEQ
    _reap_stale_pending()
    key = dedupe_key or f"{kind}:{product}"
    limit = int(max_pending or MAX_PENDING)
    # 대기열 밖 캐시 작업(빌드 스레드·오프로드 태스크)도 같은 슬롯을 쓰므로
    # "앞에 몇 건" 계산에 포함한다 — 아니면 실제로는 기다리는데 화면엔
    # "바로 시작합니다"로 뜬다.
    with _EXTERNAL_LOCK:
        external = dict(_EXTERNAL) if _EXTERNAL else None
    with _COND:
        _ensure_worker_locked()
        current = _CURRENT or external
        # 같은 제품 파이프라인이 이미 실행 중인 경우도 pending 과 동일하게
        # 중복으로 취급한다. 예전에는 pending 만 확인해 수동 버튼을 다시 누르면
        # 현재 작업 뒤에 같은 제품이 한 번 더 붙었다.
        if _CURRENT and _CURRENT.get("key") == key:
            return {
                "ok": True,
                "accepted": False,
                "duplicate": True,
                "queued": False,
                "running": True,
                "id": _CURRENT.get("id") or "",
                "ahead": 0,
                "depth": len(_PENDING),
                "current": _public(current),
                "detail": "같은 제품 캐시 작업이 이미 실행 중입니다 — 중복 실행하지 않습니다.",
            }
        for idx, pending in enumerate(_PENDING):
            if pending.get("key") == key:
                return {
                    "ok": True,
                    "accepted": False,
                    "duplicate": True,
                    "queued": True,
                    "running": True,
                    "id": pending.get("id") or "",
                    "ahead": idx + (1 if current else 0),
                    "depth": len(_PENDING),
                    "current": _public(current),
                    "detail": "같은 스캔이 이미 대기열에 있습니다 — 중복 실행하지 않습니다.",
                }
        if len(_PENDING) >= limit:
            return {
                "ok": False,
                "accepted": False,
                "duplicate": False,
                "queued": False,
                "running": current is not None,
                "ahead": len(_PENDING) + (1 if current else 0),
                "depth": len(_PENDING),
                "current": _public(current),
                "detail": f"스캔 대기열이 가득 찼습니다({limit}건). 진행 중인 작업이 끝난 뒤 다시 시도하세요.",
            }
        _SEQ += 1
        task = {
            "id": f"scan-{_SEQ}",
            "kind": str(kind or "scan"),
            "label": str(label or kind or "스캔"),
            "product": str(product or ""),
            "source": str(source or "manual"),
            "key": key,
            "run": run,
            "enqueued_mono": time.monotonic(),
            "enqueued_iso": _now_iso(),
            "started_at": None,
        }
        _PENDING.append(task)
        ahead = (len(_PENDING) - 1) + (1 if current else 0)
        current_view = _public(current)
        _COND.notify()
    if ahead:
        _record(f"[스캔 큐] 대기 등록: {label} — 앞선 작업"
                + (f"({(current_view or {}).get('label')})" if current_view else "")
                + f"이 끝나면 이어서 실행합니다 (앞에 {ahead}건). "
                  "앞 작업이 실패해도 대기 작업은 그대로 진행됩니다.",
                product=product or "")
    return {
        "ok": True,
        "accepted": True,
        "duplicate": False,
        "queued": True,
        "running": True,
        "id": task["id"],
        "ahead": ahead,
        "depth": len(_PENDING),
        "current": current_view,
        "detail": ("바로 시작합니다." if not ahead else
                   f"다른 스캔이 진행 중이라 대기열에 넣었습니다 (앞에 {ahead}건) — "
                   "앞 작업이 끝나는 대로(실패해도) 이어서 실행합니다."),
    }


def snapshot(limit: int = 20) -> dict[str, Any]:
    """관리 화면용 게이트 현황."""
    _reap_stale_pending()
    now = time.monotonic()
    with _COND:
        current = _public(_CURRENT, now=now)
        pending = [_public(task, now=now) for task in list(_PENDING)[:max(0, int(limit))]]
        depth = len(_PENDING)
        last = dict(_LAST) if _LAST else None
        worker_alive = bool(_WORKER and _WORKER.is_alive())
    with _EXTERNAL_LOCK:
        external = _public(dict(_EXTERNAL), now=now) if _EXTERNAL else None
    # 대기열 밖 작업(빌드 스레드·오프로드 태스크)도 같은 슬롯을 쓰므로 현재
    # 실행 중인 것으로 함께 보여준다 — 화면에 "아무것도 안 도는데 왜 대기?"가
    # 생기지 않게.
    return {
        "running": current is not None or external is not None,
        "busy": current is not None or external is not None or depth > 0,
        "current": current or external,
        "external": external,
        "pending": pending,
        "depth": depth,
        "last": last,
        "worker_alive": worker_alive,
    }


def cancel(task_id: str, by: str = "") -> dict[str, Any]:
    """작업을 취소한다. 대기 중이면 큐에서 빼고, 실행 중이면 취소를 요청한다.

    실행 중 작업은 **즉시 멈추지 않는다** — 안전한 지점(제품/배치 경계)에서
    스스로 접는다. 그 지점이 없는 작업은 끝까지 돈 뒤 종료된다(플래그는
    남지만 해가 없다).
    """
    task_id = str(task_id or "").strip()
    if not task_id:
        return {"ok": False, "state": "invalid", "detail": "취소할 작업 id 가 없습니다."}
    now_iso = _now_iso()
    with _COND:
        for task in list(_PENDING):
            if task.get("id") == task_id:
                _PENDING.remove(task)
                label = task.get("label") or task.get("kind") or task_id
                product = task.get("product") or ""
                break
        else:
            task = None
            label = ""
            product = ""
        if task is None:
            running = _CURRENT if (_CURRENT or {}).get("id") == task_id else None
            if running is None:
                # 대기열 밖에서 슬롯을 잡고 도는 작업(빌드 스레드·오프로드 태스크).
                # 화면에는 '현재 작업'으로 뜨므로 여기서 못 찾으면 중단 버튼이
                # 404 가 된다 — 그게 "중단을 눌러도 아무 일도 안 일어난다"였다.
                with _EXTERNAL_LOCK:
                    external = dict(_EXTERNAL) if _EXTERNAL else None
                if external and external.get("id") == task_id:
                    running = external
            if running is None:
                return {"ok": False, "state": "not_found",
                        "detail": "그 작업을 찾을 수 없습니다 — 이미 끝났을 수 있습니다."}
            label = running.get("label") or running.get("kind") or task_id
            product = running.get("product") or ""
            source = running.get("source") or ""
            key = running.get("key") or ""
            if len(_CANCELED) >= _CANCEL_MAX:
                _CANCELED.pop(next(iter(_CANCELED)), None)
            _CANCELED[task_id] = {"by": str(by or ""), "at": now_iso}
            _COND.notify_all()
    if task is not None:
        source = task.get("source") or ""
        key = task.get("key") or ""
        _record(f"[스캔 큐] 대기 취소: {label}"
                + (f" — {by}" if by else "") + " · 대기열에서 제거했습니다.",
                ok=False, product=product)
        return {"ok": True, "state": "removed", "id": task_id, "label": label,
                "product": product, "source": source, "key": key,
                "detail": "대기 중이던 작업을 큐에서 제거했습니다."}
    _record(f"[스캔 큐] 중단 요청: {label}" + (f" — {by}" if by else "")
            + " · 현재 제품/배치가 끝나는 대로 접고 다음 작업으로 넘어갑니다. "
              "이미 공개된 캐시는 유지되며 FAB 완료 배치는 다음 실행에서 이어받습니다.",
            ok=False, product=product)
    return {"ok": True, "state": "requested", "id": task_id, "label": label,
            "product": product, "source": source, "key": key,
            "detail": "중단을 요청했습니다 — 현재 제품/배치가 끝나는 대로 다음 작업으로 넘어갑니다."}


def cancel_requested(task_id: str = "") -> bool:
    """실행 중 작업이 "나 취소됐나?"를 확인한다.

    인자를 생략하면 **이 스레드가 실행 중인 작업**을 본다(`_TLS.task_id`).
    긴 루프의 안전한 지점 — 제품 경계, 배치 경계처럼 이미 메모리 가드나
    사용자 양보를 확인하는 자리 — 에서 부르면 된다.
    """
    key = str(task_id or "").strip() or str(getattr(_TLS, "task_id", "") or "")
    if not key:
        return False
    with _COND:
        return key in _CANCELED


def current_task_id() -> str:
    """이 스레드가 실행 중인 대기열 작업 id (없으면 빈 문자열)."""
    return str(getattr(_TLS, "task_id", "") or "")


def busy() -> bool:
    """실행 중이거나 대기 중인 스캔이 있는가."""
    _reap_stale_pending()
    with _EXTERNAL_LOCK:
        if _EXTERNAL is not None:
            return True
    with _COND:
        return _CURRENT is not None or bool(_PENDING)


def wait_until_idle(timeout: float = 60.0) -> bool:
    """대기열이 빌 때까지 기다린다(테스트·종료 절차용). 비었으면 True.

    대기열 밖 exclusive() 작업은 보지 않는다 — 그쪽은 큐에 들어오지 않는다."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _COND:
        while _CURRENT is not None or _PENDING:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _COND.wait(timeout=min(remaining, 0.5))
        return True
