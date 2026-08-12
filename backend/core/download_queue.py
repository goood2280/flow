"""core/download_queue.py — 사용자 CSV 추출(다운로드) 작업 대기열.

여러 사용자가 같은 시각에 ET 다운로드를 걸면, 요청마다 제품 원본 parquet 을
읽고 wide pivot 을 만드는 **무거운 계산이 동시에** 시작된다. 그러면 (a) 서버
메모리가 몇 배로 튀어 OOM/503 이 나고, (b) 요청 스레드가 수 분간 응답을 못
보내 프록시가 502 로 끊고, (c) 화면은 아무 표시 없이 멈춰 있어 사용자는 다시
누른다 — 더 나빠진다.

그래서 다운로드는 **대기열에 넣고 하나씩** 처리한다. 느려도 된다. 대신 요청자는
즉시 job_id 를 받고, 화면은 `status()` 로 "대기 N번째 / 원본 읽는 중 3/12 /
CSV 생성 중"을 계속 보여준다.

계약:

- 동시에 실행되는 작업은 기본 1건(FIFO). `FLOW_DOWNLOAD_JOB_WORKERS` 로 조절.
- **요청자는 절대 블로킹되지 않는다.** `submit()` 은 즉시 반환한다.
- **앞 작업이 실패·예외로 죽어도 다음 대기 작업은 반드시 실행된다**(워커가
  예외를 삼킨다). scan_gate 와 같은 원칙이다.
- 같은 사용자가 같은 조건으로 다시 누르면 새 작업을 만들지 않고 진행 중인
  작업을 돌려준다(더블클릭 방어).
- 결과 CSV 는 임시 파일로 떨어진다 — 완성된 결과를 프로세스 메모리에 들고
  있지 않는다. 받아간 뒤(유예 시간) 또는 TTL 경과 시 지워진다.

이 모듈은 **프로세스 로컬**이다(WEB_CONCURRENCY=1 전제). job 은 서버가 재시작
되면 사라지고, 남은 임시 파일은 다음 기동 때 TTL 스윕이 지운다.
"""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 대기열 상한 — 넘으면 새 요청을 거절한다. 무한 적재를 허용하면 "10분 뒤에
# 뜬금없이 시작되는 다운로드"가 쌓인다.
MAX_PENDING_DEFAULT = 16
# 한 사용자가 동시에 걸어둘 수 있는 작업 수(대기+실행).
USER_LIMIT_DEFAULT = 2
# 완료된 결과 파일 보관 시간. 받아가지 않은 결과도 이 시간이 지나면 지운다.
TTL_SEC_DEFAULT = 1800.0
# 받아간 뒤 유예 — 브라우저 저장이 실패했을 때 다시 받을 수 있게 잠깐 남긴다.
FETCHED_GRACE_SEC = 300.0
# 보관하는 job 기록 수(완료 이력 포함).
MAX_JOBS = 200

_COND = threading.Condition()
_PENDING: deque[dict[str, Any]] = deque()
_JOBS: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_WORKERS: list[threading.Thread] = []
_SEQ = 0

ACTIVE_STATES = ("queued", "running")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(float(os.environ.get(name, "") or default))
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def worker_count() -> int:
    """동시에 도는 작업 수. 기본 1 = 완전 직렬."""
    return _env_int("FLOW_DOWNLOAD_JOB_WORKERS", 1, 1, 4)


def max_pending() -> int:
    return _env_int("FLOW_DOWNLOAD_JOB_MAX_PENDING", MAX_PENDING_DEFAULT, 1, 128)


def user_limit() -> int:
    return _env_int("FLOW_DOWNLOAD_JOB_USER_LIMIT", USER_LIMIT_DEFAULT, 1, 16)


def ttl_sec() -> float:
    return _env_float("FLOW_DOWNLOAD_JOB_TTL_SEC", TTL_SEC_DEFAULT, 60.0, 86400.0)


def tmp_dir() -> Path:
    """결과 CSV 를 떨어뜨릴 임시 폴더.

    기본은 OS 임시 폴더다 — 결과물은 몇 분짜리 산출물이라 공유 data_root(사내
    네트워크 스토리지)에 쓰면 느리고 지저분해진다. `FLOW_DOWNLOAD_JOB_DIR` 로
    바꿀 수 있다(로컬 경로 하드코딩 금지 규칙 때문에 기본값도 env 기반).
    """
    raw = os.environ.get("FLOW_DOWNLOAD_JOB_DIR", "").strip()
    base = Path(raw) if raw else Path(tempfile.gettempdir()) / "flow_downloads"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


class JobContext:
    """작업 함수에 넘기는 진행 보고 핸들.

    `progress()` 로 남긴 단계 문구가 그대로 화면(모래시계 옆 설명)에 뜬다.
    `canceled` 는 사용자가 취소했는지 — 긴 루프 안에서 확인해 일찍 빠져나온다.
    """

    def __init__(self, job: dict[str, Any]):
        self._job = job
        self._last_guard_check = 0.0

    @property
    def job_id(self) -> str:
        return str(self._job.get("id") or "")

    @property
    def canceled(self) -> bool:
        with _COND:
            return bool(self._job.get("cancel_requested"))

    def progress(self, phase: str, done: int | None = None, total: int | None = None) -> None:
        self.guard()
        with _COND:
            self._job["phase"] = str(phase or "")
            self._job["done"] = int(done) if done is not None else None
            self._job["total"] = int(total) if total is not None else None
            self._job["updated_mono"] = time.monotonic()

    def guard(self, *, force: bool = False) -> None:
        """Stop a protected job at file/chunk/stage boundaries."""
        now = time.monotonic()
        started = float(self._job.get("started_mono") or now)
        max_runtime = float(self._job.get("max_runtime_sec") or 0.0)
        if max_runtime > 0 and now - started >= max_runtime:
            raise JobResourceLimit(
                f"계산 시간이 {int(max_runtime)}초를 넘었습니다. 서버 보호를 위해 중단했습니다 — "
                "최근 N일·LOT·STEP 등 필터 조건을 더 좁혀 주세요."
            )
        if not self._job.get("memory_guard"):
            return
        if not force and now - self._last_guard_check < 2.0:
            return
        self._last_guard_check = now
        try:
            from core.runtime_limits import process_memory_snapshot
            snap = process_memory_snapshot()
        except Exception:
            return
        if bool(snap.get("process_memory_over_limit")) or bool(snap.get("system_memory_low")):
            effective = float(snap.get("process_memory_effective_gb") or snap.get("process_rss_gb") or 0.0)
            limit = float(snap.get("process_memory_limit_gb") or 0.0)
            available = float(snap.get("system_memory_available_gb") or 0.0)
            raise JobResourceLimit(
                "서버 메모리 사용량이 보호 기준에 도달해 다운로드를 중단했습니다 "
                f"(프로세스 {effective:.1f}/{limit:.1f}GB, 시스템 가용 {available:.1f}GB). "
                "최근 N일·LOT·STEP 등 필터 조건을 더 좁혀 주세요."
            )


class JobCanceled(Exception):
    """작업 함수가 취소를 감지했을 때 던진다 — 실패가 아니라 취소로 기록된다."""


class JobResourceLimit(Exception):
    """Runtime/memory guard stopped a job; filters must be narrowed."""


def _public(job: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """외부 노출용 뷰 — 실행 함수와 내부 핸들은 빼고, 화면이 쓸 값만."""
    now = time.monotonic() if now is None else now
    started = job.get("started_mono")
    finished = job.get("finished_mono")
    queued = float(job.get("queued_mono") or now)
    end = float(finished or now)
    result = job.get("result") or {}
    total = job.get("total")
    done = job.get("done")
    pct = None
    if total:
        try:
            pct = max(0, min(100, round(float(done or 0) / float(total) * 100)))
        except Exception:
            pct = None
    return {
        "job_id": str(job.get("id") or ""),
        "kind": job.get("kind") or "",
        "state": job.get("state") or "",
        "label": job.get("label") or "",
        "product": job.get("product") or "",
        "username": job.get("username") or "",
        "phase": job.get("phase") or "",
        "done": done,
        "total": total,
        "percent": pct,
        "queued_at": job.get("queued_iso") or "",
        "started_at": job.get("started_iso") or "",
        "finished_at": job.get("finished_iso") or "",
        "waited_sec": round(float((started or end) - queued), 1),
        "elapsed_sec": round(float(end - float(started)), 1) if started else 0.0,
        "total_sec": round(float(end - queued), 1),
        "error": job.get("error") or "",
        "error_status": int(job.get("error_status") or 0),
        "rows": int(result.get("rows") or 0),
        "cols": int(result.get("cols") or 0),
        "filename": str(result.get("filename") or ""),
        "size_bytes": int(result.get("size_bytes") or 0),
    }


def _position_locked(job: dict[str, Any]) -> tuple[int, int]:
    """(앞에 남은 건수, 전체 대기열 길이). 실행 중이면 앞선 건수 0."""
    depth = len(_PENDING)
    if job.get("state") != "queued":
        return 0, depth
    ahead = 0
    for pending in _PENDING:
        if pending is job:
            break
        ahead += 1
    running = sum(1 for j in _JOBS.values() if j.get("state") == "running")
    return ahead + running, depth


def _view_locked(job: dict[str, Any]) -> dict[str, Any]:
    ahead, depth = _position_locked(job)
    running = [j for j in _JOBS.values() if j.get("state") == "running"]
    view = _public(job)
    view["ahead"] = ahead
    view["queue_len"] = depth
    view["running_count"] = len(running)
    # 대기 중일 때 "앞에서 뭐가 도는지" — 사용자에게 멈춘 게 아님을 보여준다.
    if job.get("state") == "queued" and running:
        head = running[0]
        view["current"] = {
            "product": head.get("product") or "",
            "phase": head.get("phase") or "",
            "elapsed_sec": round(time.monotonic() - float(head.get("started_mono") or time.monotonic()), 1),
        }
    else:
        view["current"] = None
    return view


def _delete_file(job: dict[str, Any]) -> None:
    result = job.get("result") or {}
    path = result.get("path")
    if not path:
        return
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("download_queue: 임시 파일 삭제 실패 %s", path, exc_info=True)
    result["path"] = ""


def _sweep_locked() -> None:
    """TTL 지난 결과 파일 정리 + job 기록 상한 유지. 호출자가 _COND 를 잡는다."""
    now = time.monotonic()
    ttl = ttl_sec()
    for job in list(_JOBS.values()):
        if job.get("state") in ACTIVE_STATES:
            continue
        finished = float(job.get("finished_mono") or now)
        fetched = job.get("fetched_mono")
        expired = (now - finished) > ttl
        if fetched is not None and (now - float(fetched)) > FETCHED_GRACE_SEC:
            expired = True
        if expired and (job.get("result") or {}).get("path"):
            _delete_file(job)
            if job.get("state") == "ready":
                job["state"] = "expired"
    while len(_JOBS) > MAX_JOBS:
        _, old = _JOBS.popitem(last=False)
        if old.get("state") in ACTIVE_STATES:
            # 실행/대기 중인 작업은 버리지 않는다 — 다시 뒤로 넣는다.
            _JOBS[old["id"]] = old
            break
        _delete_file(old)


def start_orphan_sweeper() -> None:
    """기동 시 1회 — 이전 프로세스가 남긴 결과 파일을 청소한다.

    job 기록은 프로세스 메모리라 재시작하면 사라지지만 임시 CSV 는 디스크에
    남는다. 서버가 OOM/재시작으로 끊기면 그 파일을 지울 주체가 없어져 임시
    폴더가 계속 커진다(운영에서 수백 MB 짜리가 쌓일 수 있다). 기동 직후 한 번
    쓸어내고, 이후는 submit/status 의 TTL 스윕이 맡는다.
    """
    try:
        removed = sweep_orphan_files()
        if removed:
            logger.info("download queue: 이전 실행이 남긴 결과 파일 %d개 정리", removed)
    except Exception:
        logger.debug("download queue orphan sweep failed", exc_info=True)


def sweep_orphan_files(max_age_sec: float | None = None) -> int:
    """이전 프로세스가 남긴 임시 파일 청소. 반환: 지운 파일 수."""
    limit = ttl_sec() if max_age_sec is None else float(max_age_sec)
    removed = 0
    try:
        base = tmp_dir()
    except Exception:
        return 0
    with _COND:
        live = {str((j.get("result") or {}).get("path") or "") for j in _JOBS.values()}
    now = time.time()
    for fp in base.glob("*"):
        try:
            if str(fp) in live or not fp.is_file():
                continue
            if (now - fp.stat().st_mtime) <= limit:
                continue
            fp.unlink()
            removed += 1
        except Exception:
            continue
    return removed


def _ensure_workers_locked() -> None:
    """워커 스레드 보장 — 죽었으면 다시 띄운다(대기열은 유지)."""
    alive = [t for t in _WORKERS if t.is_alive()]
    if len(alive) != len(_WORKERS):
        logger.warning("download queue worker vanished — restarting (pending=%d)", len(_PENDING))
    _WORKERS[:] = alive
    while len(_WORKERS) < worker_count():
        t = threading.Thread(target=_worker_loop, name=f"flow-download-queue-{len(_WORKERS) + 1}",
                             daemon=True)
        _WORKERS.append(t)
        t.start()


def _error_text(exc: BaseException) -> tuple[str, int]:
    """(사용자에게 보일 문구, HTTP status). HTTPException 계열은 detail/status 보존."""
    detail = getattr(exc, "detail", None)
    status = getattr(exc, "status_code", 0)
    if detail is not None:
        text = detail if isinstance(detail, str) else str(detail)
        return text, int(status or 400)
    return f"{type(exc).__name__}: {exc}", 0


def _run_job(job: dict[str, Any]) -> None:
    """작업 1건 실행. **어떤 예외도 큐를 멈추지 않는다.**"""
    ctx = JobContext(job)
    try:
        result = job["run"](ctx) or {}
        if ctx.canceled:
            raise JobCanceled()
        path = result.get("path")
        size = 0
        if path:
            try:
                size = int(Path(path).stat().st_size)
            except Exception:
                size = 0
        with _COND:
            job["result"] = {
                "path": str(path or ""),
                "filename": str(result.get("filename") or "download.csv"),
                "rows": int(result.get("rows") or 0),
                "cols": int(result.get("cols") or 0),
                "size_bytes": size,
                "meta": result.get("meta") or {},
            }
            job["state"] = "ready"
            job["phase"] = "완료 — 파일 받는 중"
            job["finished_mono"] = time.monotonic()
            job["finished_iso"] = _now_iso()
            _COND.notify_all()
    except JobCanceled:
        with _COND:
            job["state"] = "canceled"
            job["phase"] = "취소됨"
            job["finished_mono"] = time.monotonic()
            job["finished_iso"] = _now_iso()
            _delete_file(job)
            _COND.notify_all()
    except Exception as exc:  # noqa: BLE001 — 큐는 어떤 실패에도 계속 돈다
        text, status = _error_text(exc)
        logger.warning("download job failed kind=%s label=%s: %s",
                       job.get("kind"), job.get("label"), exc, exc_info=True)
        with _COND:
            job["state"] = "error"
            job["phase"] = "필터 조건 변경 필요" if isinstance(exc, JobResourceLimit) else "실패"
            job["error"] = text
            job["error_status"] = status
            job["finished_mono"] = time.monotonic()
            job["finished_iso"] = _now_iso()
            _delete_file(job)
            _COND.notify_all()
    finally:
        # 사용자가 취소했거나 화면을 떠난 뒤 완성된 결과는 붙들지 않는다.
        with _COND:
            if job.get("cancel_requested") and job.get("state") == "ready":
                job["state"] = "canceled"
                job["phase"] = "취소됨"
                _delete_file(job)
                _COND.notify_all()


def _worker_loop() -> None:
    while True:
        try:
            with _COND:
                while not _PENDING:
                    _COND.wait(timeout=300.0)
                job = _PENDING.popleft()
                if job.get("cancel_requested"):
                    job["state"] = "canceled"
                    job["phase"] = "취소됨"
                    job["finished_mono"] = time.monotonic()
                    job["finished_iso"] = _now_iso()
                    _COND.notify_all()
                    continue
                job["state"] = "running"
                job["phase"] = job.get("phase") or "시작하는 중"
                job["started_mono"] = time.monotonic()
                job["started_iso"] = _now_iso()
                _COND.notify_all()
            _run_job(job)
        except Exception:
            # 루프 자체는 죽지 않는다 — 죽으면 모든 다운로드가 영원히 멈춘다.
            logger.warning("download queue worker loop error", exc_info=True)
            time.sleep(1.0)


def submit(kind: str, username: str, label: str, run: Callable[[JobContext], dict],
           *, product: str = "", dedupe_key: str = "",
           meta: dict[str, Any] | None = None, max_runtime_sec: float = 0.0,
           memory_guard: bool = False) -> dict[str, Any]:
    """작업을 대기열에 넣고 즉시 반환한다.

    반환 dict 는 `status()` 와 같은 뷰에 다음이 더해진다:
      - `ok`: 접수 여부 (False 면 `detail` 에 이유)
      - `duplicate`: 같은 조건의 작업이 이미 진행 중이라 그걸 돌려줬는가
    """
    global _SEQ
    user = str(username or "")
    with _COND:
        _sweep_locked()
        _ensure_workers_locked()
        if dedupe_key:
            for job in _JOBS.values():
                if (job.get("state") in ACTIVE_STATES
                        and job.get("username") == user
                        and job.get("dedupe_key") == dedupe_key):
                    view = _view_locked(job)
                    view.update(ok=True, duplicate=True,
                                detail="같은 조건의 다운로드가 이미 진행 중입니다 — 그 작업을 이어서 봅니다.")
                    return view
        mine = [j for j in _JOBS.values()
                if j.get("state") in ACTIVE_STATES and j.get("username") == user]
        if len(mine) >= user_limit():
            return {"ok": False, "duplicate": False, "state": "rejected",
                    "queue_len": len(_PENDING), "ahead": len(_PENDING),
                    "detail": f"이미 진행 중인 다운로드가 {len(mine)}건 있습니다 — "
                              "끝난 뒤 다시 시도하세요."}
        if len(_PENDING) >= max_pending():
            return {"ok": False, "duplicate": False, "state": "rejected",
                    "queue_len": len(_PENDING), "ahead": len(_PENDING),
                    "detail": f"다운로드 대기열이 가득 찼습니다({max_pending()}건). "
                              "잠시 뒤 다시 시도하세요."}
        _SEQ += 1
        job = {
            "id": f"dl-{_SEQ}-{int(time.time())}",
            "kind": str(kind or "download"),
            "username": user,
            "label": str(label or kind or "다운로드"),
            "product": str(product or ""),
            "dedupe_key": str(dedupe_key or ""),
            "meta": dict(meta or {}),
            "run": run,
            "state": "queued",
            "phase": "대기 중",
            "done": None,
            "total": None,
            "queued_mono": time.monotonic(),
            "queued_iso": _now_iso(),
            "started_mono": None,
            "started_iso": "",
            "finished_mono": None,
            "finished_iso": "",
            "updated_mono": time.monotonic(),
            "error": "",
            "error_status": 0,
            "result": {},
            "fetched_mono": None,
            "cancel_requested": False,
            "max_runtime_sec": max(0.0, float(max_runtime_sec or 0.0)),
            "memory_guard": bool(memory_guard),
        }
        _JOBS[job["id"]] = job
        _PENDING.append(job)
        view = _view_locked(job)
        _COND.notify()
    view.update(ok=True, duplicate=False,
                detail=("바로 시작합니다." if not view.get("ahead") else
                        f"다른 다운로드가 진행 중이라 대기열에 넣었습니다 (앞에 {view['ahead']}건) — "
                        "앞 작업이 끝나는 대로(실패해도) 이어서 실행합니다."))
    return view


def get(job_id: str) -> dict[str, Any] | None:
    """내부용 job 원본 — 라우터가 소유자 확인·결과 파일 접근에 쓴다."""
    with _COND:
        return _JOBS.get(str(job_id or ""))


def status(job_id: str) -> dict[str, Any] | None:
    with _COND:
        job = _JOBS.get(str(job_id or ""))
        if job is None:
            return None
        _sweep_locked()
        return _view_locked(job)


def cancel(job_id: str) -> dict[str, Any] | None:
    """취소 — 대기 중이면 즉시 빼고, 실행 중이면 결과를 버린다(계산은 곧 끝난다)."""
    with _COND:
        job = _JOBS.get(str(job_id or ""))
        if job is None:
            return None
        job["cancel_requested"] = True
        if job.get("state") == "queued":
            try:
                _PENDING.remove(job)
            except ValueError:
                pass
            job["state"] = "canceled"
            job["phase"] = "취소됨"
            job["finished_mono"] = time.monotonic()
            job["finished_iso"] = _now_iso()
        elif job.get("state") == "ready":
            job["state"] = "canceled"
            job["phase"] = "취소됨"
            _delete_file(job)
        return _view_locked(job)


def mark_fetched(job_id: str, size_bytes: int = 0) -> None:
    """결과를 받아갔다고 기록. 유예 시간 뒤 스윕이 파일을 지운다."""
    with _COND:
        job = _JOBS.get(str(job_id or ""))
        if job is None:
            return
        first = job.get("fetched_mono") is None
        job["fetched_mono"] = time.monotonic()
        if first and size_bytes:
            (job.get("result") or {})["size_bytes"] = int(size_bytes)
        if job.get("state") == "ready":
            job["phase"] = "완료"


def fetched_once(job_id: str) -> bool:
    with _COND:
        job = _JOBS.get(str(job_id or ""))
        return bool(job and job.get("fetched_mono") is not None)


def snapshot(username: str = "", limit: int = 20) -> dict[str, Any]:
    """대기열 현황. username 을 주면 그 사용자의 작업만 목록에 담는다."""
    with _COND:
        _sweep_locked()
        jobs = list(_JOBS.values())
        running = [j for j in jobs if j.get("state") == "running"]
        views = [_view_locked(j) for j in jobs
                 if (not username or j.get("username") == username)
                 and j.get("state") in ACTIVE_STATES]
        return {
            "queue_len": len(_PENDING),
            "running_count": len(running),
            "workers": worker_count(),
            "jobs": views[-max(1, int(limit)):],
        }


def wait_until_idle(timeout: float = 60.0) -> bool:
    """대기열이 빌 때까지 기다린다(테스트용)."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _COND:
        while _PENDING or any(j.get("state") == "running" for j in _JOBS.values()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _COND.wait(timeout=min(remaining, 0.2))
        return True


def reset_for_tests() -> None:
    """테스트 격리용 — 대기열/기록을 비우고 임시 파일을 지운다."""
    with _COND:
        _PENDING.clear()
        for job in list(_JOBS.values()):
            _delete_file(job)
        _JOBS.clear()
    try:
        base = tmp_dir()
        if base.name == "flow_downloads":
            shutil.rmtree(base, ignore_errors=True)
    except Exception:
        pass
