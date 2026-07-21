"""core/worker_dispatch.py — 개발서버 워커 오프로드 (v9.4.x).

운영(API)/개발(워커) 2개 서버가 같은 flow-data(shared workspace)를 공유할 때,
무거운 파일 산출 작업(SplitTable pivot 캐시, fab lot index, ML_TABLE lookup
파티션 빌드)을 개발서버로 넘겨 운영서버 부하를 줄인다.

원칙:
  - 외부 브로커(Redis/RabbitMQ) 없이 shared workspace 파일만 사용.
  - 개발서버는 언제든 꺼질 수 있다 → heartbeat 기반 헬스체크 + 로컬 폴백.
    워커가 죽으면 run_heavy() 가 조용히 local_fn 으로 대체 실행하고,
    heartbeat 가 다시 신선해지면 자동으로 오프로드가 재개된다 (상태 저장 없음
    — 매 디스패치마다 heartbeat 신선도만 본다).
  - 오프로드 가능한 작업은 "결과가 shared workspace 파일로 남는 것"만.
    프로세스 RAM 캐시 워밍은 서버별 로컬 자원이므로 대상이 아니다.
  - 이중 실행 안전성은 이 모듈이 아니라 기존 cross-server 가드(shared_lease,
    ml_table_lookup build lock)가 담당한다. 최악의 폴백 경쟁도 중복 빌드
    1회로 수렴 — 기존 무락 동작과 같은 안전도.

역할 (우선순위: env FLOW_SERVER_ROLE > {app_root}/server_role.json > 자동):
  - "api"        운영서버. 작업을 큐에 넣고 결과를 기다린다. 워커 다운이면 로컬 실행.
  - "worker"     개발서버. heartbeat 를 쓰고 큐를 소비한다.
  - "standalone" 단일 서버(로컬 dev). 오프로드 없이 전부 로컬 실행.
  자동 판정: prod 배포(PATHS.is_prod)는 "api", 그 외 "standalone".
  server_role.json 은 관리자 탭(모니터 → 워커 서버)에서 편집한다 — 서버별
  로컬 파일(app_root)이라 공유 workspace 를 타지 않는다. app_root 가
  읽기전용인 배포에서는 {data_root}/worker/roles/<host>.json 폴백에 저장하고,
  해석 시 두 파일 중 updated_at 이 최신인 쪽을 택한다 (hostname 분리로
  공유 workspace 위에서도 서버별 의미 유지). 역할 변경은 재시작
  없이 디스패치/heartbeat/큐 소비에 즉시 반영된다 (단, startup 에서 한 번만
  켜는 heavy 백그라운드 스케줄러 세트는 재시작 후 완전 적용).

파일 레이아웃 ({data_root}/worker/):
  heartbeat.json            워커 생존 신호 (FLOW_WORKER_HEARTBEAT_SEC 주기, 기본 10s)
  queue/<id>.task.json      대기 작업 {id, type, payload, deadline, ...}
  claimed/<id>.task.json    워커가 os.replace 로 원자적으로 가져간 작업
  results/<id>.json         실행 결과 {id, ok, result|error, ...}
  control/watchdog.json     개발서버 상주 워치독(scripts/worker_watchdog.py) 생존 신호
  control/start_request.json 운영서버 관리자가 요청한 워커 원격 기동 (워치독이 소비)

환경변수:
  FLOW_SERVER_ROLE            api | worker | standalone
  FLOW_WORKER_OFFLOAD         0 이면 api 역할이어도 오프로드 끔 (기본 켬)
  FLOW_WORKER_HEARTBEAT_SEC   워커 heartbeat 주기 (기본 10)
  FLOW_WORKER_STALE_SEC       heartbeat 가 이보다 오래되면 다운 판정 (기본 45)
  FLOW_WORKER_CONCURRENCY     워커 동시 실행 슬롯 (기본 2 — 5코어/15GB 개발서버
                              기준, polars 스레드 예산과 곱해지는 걸 감안한 값)
  FLOW_WORKER_TASK_TIMEOUT_SEC 오프로드 기본 대기 한도 (기본 1800)
  FLOW_WORKER_MAX_QUEUE       큐 깊이가 이보다 크면 로컬 실행 (기본 32)
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("flow.worker_dispatch")

_RESULT_TTL_SEC = 2 * 3600          # 결과/고아 파일 정리 기준
_CLAIMED_STALE_SEC = 2 * 3600       # 워커 crash 로 남은 claimed 정리 기준
_JANITOR_INTERVAL_SEC = 60.0
_POLL_IDLE_SEC = 0.5                # 워커 큐 폴링 주기
_RESULT_POLL_SEC = 0.3              # api 결과 대기 폴링 주기
_ALIVE_CACHE_SEC = 2.0              # heartbeat 파일 read 캐시

_HANDLERS: dict[str, Callable[[dict], dict]] = {}
_STATS_LOCK = threading.Lock()
_STATS = {"offloaded": 0, "remote_ok": 0, "remote_fail": 0, "local_fallback": 0, "local_direct": 0,
          "local_worker_overloaded": 0}
_ALIVE_CACHE: dict[str, Any] = {"ts": 0.0, "meta": None}
# heartbeat ts 값이 마지막으로 '바뀐' 시각(이 서버의 monotonic 기준).
# 서버 간 벽시계가 어긋나도(skew) 값의 변화 자체는 신뢰할 수 있다.
_HB_CHANGE: dict[str, Any] = {"ts": None, "mono": None}
_HB_CHANGE_LOCK = threading.Lock()
_STARTED = threading.Event()
_WORKER_RUNNING: set[str] = set()
_WORKER_RUNNING_LOCK = threading.Lock()


# ── env / role ────────────────────────────────────────────────────────────────

def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name) or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


_ROLE_VALUES = ("api", "worker", "standalone")
_ROLE_CACHE: dict[str, Any] = {"ts": 0.0, "role": "", "source": ""}
_ROLE_CACHE_SEC = 3.0


def _role_config_path() -> Path:
    from core.paths import PATHS
    return PATHS.app_root / "server_role.json"


def _role_fallback_path() -> Path:
    """서버별 역할 폴백 경로 — app_root 가 읽기전용인 배포용.

    data_root 는 서버 간 공유 workspace 이므로 hostname 으로 파일을 분리해
    '서버별 로컬 역할' 의미를 유지한다."""
    host = re.sub(r"[^a-z0-9._-]", "_", socket.gethostname().strip().lower()) or "unknown"
    return _worker_root() / "roles" / f"{host}.json"


def _resolve_role() -> tuple[str, str]:
    """(role, source) — env > server_role.json(주경로/폴백 중 최신 updated_at) > 자동 판정."""
    raw = os.environ.get("FLOW_SERVER_ROLE", "").strip().lower()
    if raw in _ROLE_VALUES:
        return raw, "env"
    best_role, best_ts = "", float("-inf")
    for path in (_role_config_path(), _role_fallback_path()):
        try:
            data = _read_json(path)
            cfg_role = str(data.get("role") or "").strip().lower()
            if cfg_role not in _ROLE_VALUES:
                continue
            ts = float(data.get("updated_at") or 0.0)
            if ts > best_ts:
                best_role, best_ts = cfg_role, ts
        except Exception:
            continue
    if best_role:
        return best_role, "config"
    try:
        from core.paths import PATHS
        if PATHS.is_prod:
            return "api", "auto"
    except Exception:
        pass
    return "standalone", "auto"


def _role_cached() -> tuple[str, str]:
    now = time.time()
    if _ROLE_CACHE["role"] and (now - _ROLE_CACHE["ts"]) < _ROLE_CACHE_SEC:
        return _ROLE_CACHE["role"], _ROLE_CACHE["source"]
    role, source = _resolve_role()
    _ROLE_CACHE.update(ts=now, role=role, source=source)
    return role, source


def server_role() -> str:
    return _role_cached()[0]


def role_source() -> str:
    """현재 역할이 어디서 왔는지 — env | config | auto (env 면 UI 편집 불가)."""
    return _role_cached()[1]


def set_role(role: str) -> dict:
    """server_role.json 에 역할 저장 (관리자 탭에서 호출). env 지정 시 거부.

    app_root 가 읽기전용인 배포에서는 {data_root}/worker/roles/<host>.json
    폴백에 저장한다 — _resolve_role() 이 두 파일 중 최신 updated_at 을 택한다.
    저장 즉시 디스패치/heartbeat/큐 소비 루프에 반영된다 — 루프들이 매 반복
    server_role() 을 확인한다."""
    value = str(role or "").strip().lower()
    if value not in _ROLE_VALUES:
        return {"ok": False, "code": "invalid",
                "error": f"invalid role: {role!r} (api|worker|standalone)"}
    if os.environ.get("FLOW_SERVER_ROLE", "").strip():
        return {"ok": False, "code": "pinned",
                "error": "role is pinned by FLOW_SERVER_ROLE env — unset it to manage from UI"}
    payload = {"role": value, "updated_at": time.time()}
    primary = _role_config_path()
    err = _try_write_json(primary, payload)
    path_used = primary
    if err is not None:
        fallback = _role_fallback_path()
        try:
            fallback.parent.mkdir(parents=True, exist_ok=True)
            fb_err = _try_write_json(fallback, payload)
        except Exception as e:
            fb_err = f"{type(e).__name__}: {e}"
        if fb_err is not None:
            logger.error(f"role save failed — {primary}: {err} / fallback {fallback}: {fb_err}")
            return {"ok": False, "code": "write_failed",
                    "error": f"role save failed — {primary} ({err}); fallback {fallback} ({fb_err})"}
        path_used = fallback
        logger.warning(f"app_root not writable ({err}) — role saved to fallback {fallback}")
    _ROLE_CACHE.update(ts=0.0, role="", source="")
    logger.info(f"server role set to {value!r} via config ({path_used})")
    return {"ok": True, "role": value, "path": str(path_used)}


def offload_enabled() -> bool:
    return server_role() == "api" and _env_flag("FLOW_WORKER_OFFLOAD", True)


def external_services_enabled() -> bool:
    """외부 서비스 연동(S3 업로드/자동 동기화, 운영 스케줄러, 메일 발송) 허용 여부.

    worker(개발서버) 역할은 로드 분산 전용(SplitTable 조회·데이터 처리)이다 —
    외부로 나가는 부수효과는 운영(api)·standalone 서버만 수행한다. 역할은
    재시작 없이 바뀔 수 있으므로 루프형 호출부는 매 반복 이 함수를 확인한다."""
    return server_role() != "worker"


def heartbeat_interval_sec() -> float:
    return _env_float("FLOW_WORKER_HEARTBEAT_SEC", 10.0, 2.0, 300.0)


def stale_sec() -> float:
    return _env_float("FLOW_WORKER_STALE_SEC", 45.0, 5.0, 3600.0)


def default_task_timeout_sec() -> float:
    return _env_float("FLOW_WORKER_TASK_TIMEOUT_SEC", 1800.0, 10.0, 6 * 3600.0)


def worker_concurrency() -> int:
    return int(_env_float("FLOW_WORKER_CONCURRENCY", 2.0, 1.0, 16.0))


def max_queue_depth() -> int:
    return int(_env_float("FLOW_WORKER_MAX_QUEUE", 32.0, 1.0, 1000.0))


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# ── paths ─────────────────────────────────────────────────────────────────────

def _worker_root() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "worker"


def _heartbeat_path() -> Path:
    return _worker_root() / "heartbeat.json"


def _queue_dir() -> Path:
    return _worker_root() / "queue"


def _claimed_dir() -> Path:
    return _worker_root() / "claimed"


def _results_dir() -> Path:
    return _worker_root() / "results"


def _ensure_dirs() -> None:
    for d in (_queue_dir(), _claimed_dir(), _results_dir()):
        d.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: dict) -> bool:
    return _try_write_json(path, payload) is None


def _try_write_json(path: Path, payload: dict) -> str | None:
    """원자적 JSON 쓰기 — 성공 시 None, 실패 시 예외 요약 문자열."""
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)
        return None
    except Exception as e:
        try:
            tmp.unlink()
        except Exception:
            pass
        return f"{type(e).__name__}: {e}"


# ── health ────────────────────────────────────────────────────────────────────

def heartbeat_meta(*, fresh_read: bool = False) -> dict:
    """워커 heartbeat 파일 내용 (없으면 {}). 2초 캐시로 파일 stat 폭주 방지."""
    now = time.time()
    if not fresh_read and _ALIVE_CACHE["meta"] is not None and (now - _ALIVE_CACHE["ts"]) < _ALIVE_CACHE_SEC:
        return dict(_ALIVE_CACHE["meta"])
    meta = _read_json(_heartbeat_path())
    _ALIVE_CACHE["ts"] = now
    _ALIVE_CACHE["meta"] = dict(meta)
    return meta


def worker_alive(*, fresh_read: bool = False) -> bool:
    """개발서버(워커) 생존 여부.

    자기 자신의 heartbeat 는 무시한다 (같은 프로세스가 워커이자 api 인
    잘못된 구성이 오프로드 루프를 만드는 것 방지)."""
    return _judge_alive(heartbeat_meta(fresh_read=fresh_read))


def _judge_alive(meta: dict) -> bool:
    """heartbeat 메타로 생존 판정 — 두 경로 중 하나면 살아있음:

    1) 절대 나이: |now - ts| < stale 한도. 두 서버 벽시계가 맞을 때의 기본 경로.
    2) 변화 감지: heartbeat ts '값'이 이 서버의 monotonic 시계 기준으로 stale
       한도 안에 바뀌었으면 살아있음. 서버 간 벽시계가 어긋난 배포에서 (1)이
       영구 실패해 워커가 항상 오프라인으로 보이는 문제를 막는다. 첫 관측만으로는
       살았다고 치지 않는다 — 죽은 워커가 남긴 낡은 파일을 api 재시작 직후
       살아있다고 오판하지 않기 위해, 실제 '변화'를 한 번 본 뒤부터 유효하다."""
    ts = float(meta.get("ts") or 0.0)
    if not ts:
        return False
    if str(meta.get("owner") or "") == _owner_id():
        return False
    limit = stale_sec()
    mono = time.monotonic()
    with _HB_CHANGE_LOCK:
        if _HB_CHANGE["ts"] != ts:
            _HB_CHANGE["mono"] = mono if _HB_CHANGE["ts"] is not None else None
            _HB_CHANGE["ts"] = ts
        changed_at = _HB_CHANGE["mono"]
    if abs(time.time() - ts) < limit:
        return True
    return changed_at is not None and (mono - changed_at) < limit


# ── handler registry (worker side) ────────────────────────────────────────────

def register_handler(task_type: str, fn: Callable[[dict], dict]) -> None:
    _HANDLERS[str(task_type)] = fn


def handler(task_type: str):
    def _wrap(fn: Callable[[dict], dict]):
        register_handler(task_type, fn)
        return fn
    return _wrap


# ── api side: submit + wait + fallback ────────────────────────────────────────

def _queue_depth() -> int:
    try:
        return sum(1 for p in _queue_dir().iterdir() if p.name.endswith(".task.json"))
    except Exception:
        return 0


def _bump(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + 1


def _submit(task_type: str, payload: dict, timeout_sec: float) -> tuple[str, Path] | None:
    task_id = f"{int(time.time())}-{uuid.uuid4().hex[:10]}"
    task = {
        "id": task_id,
        "type": str(task_type),
        "payload": payload or {},
        "submitted_by": _owner_id(),
        "submitted_at": time.time(),
        "deadline": time.time() + float(timeout_sec),
    }
    try:
        _ensure_dirs()
        fp = _queue_dir() / f"{task_id}.task.json"
        if not _write_json_atomic(fp, task):
            return None
        return task_id, fp
    except Exception:
        return None


def _wait_for_result(task_id: str, queue_fp: Path, deadline: float) -> dict | None:
    """결과 파일 대기. None = 원격 실패/워커 사망/타임아웃 → 호출측 로컬 폴백.

    워커가 죽은 걸 감지하면 큐에 남은(미클레임) 작업은 지워서 부활한 워커가
    한참 뒤 낡은 작업을 실행하는 일을 막는다. 이미 claimed 된 작업은 그대로
    둔다 — 결과 파일은 무시되고, 이중 실행은 shared_lease/빌드 락이 막는다."""
    result_fp = _results_dir() / f"{task_id}.json"
    claimed_fp = _claimed_dir() / queue_fp.name
    while time.time() < deadline:
        if result_fp.is_file():
            res = _read_json(result_fp)
            try:
                result_fp.unlink()
            except Exception:
                pass
            if res.get("id") == task_id:
                return res
            return None
        if not worker_alive():
            if queue_fp.is_file() and not claimed_fp.is_file():
                try:
                    queue_fp.unlink()
                except Exception:
                    pass
                return None
            # claimed 상태에서 죽었으면 stale 한도만큼 더 기다려볼 가치가 없다.
            return None
        time.sleep(_RESULT_POLL_SEC)
    # 타임아웃 — 미클레임 작업은 회수.
    if queue_fp.is_file() and not claimed_fp.is_file():
        try:
            queue_fp.unlink()
        except Exception:
            pass
    return None


def worker_overloaded_reason(meta: dict | None = None) -> str:
    """heartbeat load 스냅샷 기반 워커 과부하 판정 — 과부하면 사유, 아니면 "".

    메모리만 본다: CPU 포화는 워커 자체 동시성(worker_concurrency)이 큐 대기로
    직렬화해 흡수하지만, 메모리가 부족한 워커에 빌드를 더 얹으면 워커 OOM
    (개발서버는 자동 재시작 없음)으로 이어질 수 있다. 과부하면 run_heavy 가
    큐잉 대신 로컬 실행을 택한다 — 운영은 항상 로컬 폴백이 있으므로 기능은
    동일하고 실행 위치만 바뀐다.

    임계값:
      FLOW_WORKER_OFFLOAD_MIN_AVAIL_GB  워커 호스트 가용 메모리 하한 (기본 2.0)
      FLOW_WORKER_OFFLOAD_MAX_MEM_PCT   워커 프로세스 메모리/한도 비율 상한 (기본 90)
    """
    if meta is None:
        meta = heartbeat_meta()
    load = (meta or {}).get("load") or {}

    def _f(key: str) -> float:
        try:
            return float(load.get(key) or 0.0)
        except Exception:
            return 0.0

    avail = _f("system_memory_available_gb")
    min_avail = _env_float("FLOW_WORKER_OFFLOAD_MIN_AVAIL_GB", 2.0, 0.0, 64.0)
    if avail > 0 and avail < min_avail:
        return f"low_host_memory (avail {avail:.1f}GB < {min_avail:.1f}GB)"
    effective = _f("mem_effective_gb")
    limit = _f("mem_limit_gb")
    max_pct = _env_float("FLOW_WORKER_OFFLOAD_MAX_MEM_PCT", 90.0, 10.0, 100.0)
    if limit > 0 and effective > 0 and (100.0 * effective / limit) >= max_pct:
        return f"process_memory_high ({100.0 * effective / limit:.0f}% >= {max_pct:.0f}%)"
    return ""


def run_heavy(
    task_type: str,
    payload: dict,
    local_fn: Callable[[], dict | None],
    *,
    timeout_sec: float | None = None,
    label: str = "",
) -> dict | None:
    """무거운 파일 산출 작업을 워커로 오프로드하고, 실패하면 로컬 실행.

    - api 역할 + 워커 생존 + 워커 메모리 여유 + 큐 여유 → 큐에 넣고 결과 대기.
      원격 성공 시 결과 dict 반환 (로컬 실행 없음).
    - 그 외 모든 경우(standalone/worker 역할, 워커 다운/과부하, 타임아웃, 원격
      에러) → local_fn() 실행 결과 반환. 디스패치 계층에서 예외를 밖으로 던지지
      않는다 (local_fn 자체의 예외는 그대로 전파 — 기존 호출부 동작 유지)."""
    name = label or task_type
    if not offload_enabled() or not worker_alive():
        _bump("local_direct")
        return local_fn()
    overload = worker_overloaded_reason()
    if overload:
        logger.info(f"worker overloaded ({overload}) — running {name} locally")
        _bump("local_worker_overloaded")
        return local_fn()
    if _queue_depth() >= max_queue_depth():
        logger.info(f"worker queue full — running {name} locally")
        _bump("local_direct")
        return local_fn()
    timeout = float(timeout_sec if timeout_sec is not None else default_task_timeout_sec())
    submitted = _submit(task_type, payload, timeout)
    if not submitted:
        _bump("local_direct")
        return local_fn()
    task_id, queue_fp = submitted
    _bump("offloaded")
    logger.info(f"offloaded {name} to worker (task={task_id})")
    res = _wait_for_result(task_id, queue_fp, time.time() + timeout)
    if res is not None and res.get("ok"):
        _bump("remote_ok")
        out = res.get("result")
        return out if isinstance(out, dict) else {"ok": True}
    if res is not None:
        _bump("remote_fail")
        logger.warning(f"worker task {name} failed remotely: {res.get('error')!r} — falling back to local")
    else:
        _bump("local_fallback")
        logger.warning(f"worker task {name} unresolved (worker down/timeout) — falling back to local")
    return local_fn()


# ── worker side: consume loop ─────────────────────────────────────────────────

def _load_snapshot() -> dict:
    try:
        from core.runtime_limits import process_cpu_snapshot, process_memory_snapshot
        cpu = process_cpu_snapshot()
        mem = process_memory_snapshot()
        return {
            "cpu_cores": cpu.get("process_cpu_cores"),
            "cpu_budget_cores": cpu.get("process_cpu_budget_cores"),
            "mem_effective_gb": mem.get("process_memory_effective_gb"),
            "mem_limit_gb": mem.get("process_memory_limit_gb"),
            "system_memory_available_gb": mem.get("system_memory_available_gb"),
        }
    except Exception:
        return {}


def _heartbeat_loop() -> None:
    started_at = time.time()
    while True:
        try:
            if server_role() != "worker":
                # 역할이 관리자 탭에서 바뀔 수 있다 — worker 가 아니면 쓰지 않고
                # 대기 (기존 heartbeat 는 자연히 stale 되어 api 가 로컬 전환).
                time.sleep(heartbeat_interval_sec())
                continue
            _ensure_dirs()
            with _WORKER_RUNNING_LOCK:
                running = sorted(_WORKER_RUNNING)
            _write_json_atomic(_heartbeat_path(), {
                "owner": _owner_id(),
                "role": "worker",
                "ts": time.time(),
                "started_at": started_at,
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "concurrency": worker_concurrency(),
                "running": running,
                "queue_depth": _queue_depth(),
                "handlers": sorted(_HANDLERS.keys()),
                "load": _load_snapshot(),
            })
        except Exception:
            logger.debug("worker heartbeat write failed", exc_info=True)
        time.sleep(heartbeat_interval_sec())


def _claim_next() -> tuple[dict, Path] | None:
    try:
        candidates = sorted(
            (p for p in _queue_dir().iterdir() if p.name.endswith(".task.json")),
            key=lambda p: p.name,
        )
    except Exception:
        return None
    for fp in candidates:
        dst = _claimed_dir() / fp.name
        try:
            os.replace(fp, dst)          # 원자적 claim — 경쟁 시 한 쪽만 성공
        except OSError:
            continue
        task = _read_json(dst)
        if not task.get("id") or not task.get("type"):
            try:
                dst.unlink()
            except Exception:
                pass
            continue
        return task, dst
    return None


def _execute_task(task: dict, claimed_fp: Path) -> None:
    task_id = str(task.get("id"))
    task_type = str(task.get("type"))
    label = f"{task_type}({task_id})"
    result: dict = {"id": task_id, "type": task_type, "worker": _owner_id(), "finished_at": time.time()}
    deadline = float(task.get("deadline") or 0.0)
    with _WORKER_RUNNING_LOCK:
        _WORKER_RUNNING.add(label)
    try:
        if deadline and time.time() >= deadline:
            result.update(ok=False, error="expired_before_start")
        else:
            fn = _HANDLERS.get(task_type)
            if fn is None:
                result.update(ok=False, error=f"no_handler:{task_type}")
            else:
                logger.info(f"worker executing {label}")
                started = time.monotonic()
                try:
                    out = fn(dict(task.get("payload") or {}))
                    result.update(ok=True, result=out if isinstance(out, dict) else {"ok": bool(out)})
                except Exception as exc:
                    logger.warning(f"worker task {label} raised: {exc}", exc_info=True)
                    result.update(ok=False, error=f"{type(exc).__name__}: {exc}")
                result["seconds"] = round(time.monotonic() - started, 3)
    finally:
        with _WORKER_RUNNING_LOCK:
            _WORKER_RUNNING.discard(label)
        _write_json_atomic(_results_dir() / f"{task_id}.json", result)
        try:
            claimed_fp.unlink()
        except Exception:
            pass
        logger.info(f"worker finished {label} ok={result.get('ok')}")


def _janitor_once() -> None:
    now = time.time()
    for d, ttl in ((_results_dir(), _RESULT_TTL_SEC), (_claimed_dir(), _CLAIMED_STALE_SEC)):
        try:
            for fp in d.iterdir():
                try:
                    if (now - fp.stat().st_mtime) > ttl:
                        fp.unlink()
                except Exception:
                    pass
        except Exception:
            pass
    # deadline 지난 미클레임 작업 정리 (제출측이 죽어 회수 못한 경우).
    try:
        for fp in _queue_dir().iterdir():
            task = _read_json(fp)
            deadline = float(task.get("deadline") or 0.0)
            if deadline and now >= deadline:
                try:
                    fp.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def _consume_loop() -> None:
    pool = ThreadPoolExecutor(max_workers=worker_concurrency(), thread_name_prefix="flow-worker")
    slots = threading.Semaphore(worker_concurrency())
    last_janitor = 0.0
    while True:
        try:
            if server_role() != "worker":
                time.sleep(2.0)
                continue
            if time.time() - last_janitor > _JANITOR_INTERVAL_SEC:
                last_janitor = time.time()
                _janitor_once()
            if not slots.acquire(timeout=_POLL_IDLE_SEC):
                continue
            claimed = _claim_next()
            if claimed is None:
                slots.release()
                time.sleep(_POLL_IDLE_SEC)
                continue
            task, fp = claimed

            def _run(task=task, fp=fp):
                try:
                    _execute_task(task, fp)
                finally:
                    slots.release()

            pool.submit(_run)
        except Exception:
            logger.debug("worker consume loop error", exc_info=True)
            time.sleep(2.0)


def _api_watch_loop() -> None:
    """api 역할: 워커 생존 상태 전이를 주기적으로 로그 — 운영 가시성용."""
    last: bool | None = None
    while True:
        try:
            if server_role() != "api" or not _env_flag("FLOW_WORKER_OFFLOAD", True):
                last = None
                time.sleep(10.0)
                continue
            alive = worker_alive(fresh_read=True)
            if alive != last:
                if alive:
                    meta = heartbeat_meta()
                    logger.info(f"worker ONLINE — heavy jobs offloaded to {meta.get('owner')}")
                elif last is not None:
                    logger.warning("worker OFFLINE — heavy jobs run locally until it returns")
                else:
                    logger.info("worker not detected — heavy jobs run locally (offload resumes when a worker heartbeat appears)")
                last = alive
        except Exception:
            pass
        time.sleep(max(5.0, heartbeat_interval_sec()))


# ── watchdog / remote start (api 관리자 탭 → 개발서버 원격 기동) ───────────────

_WATCHDOG_STALE_SEC = 30.0          # scripts/worker_watchdog.py 는 5~10s 주기
_START_REQUEST_FRESH_SEC = 180.0    # 이 안의 미소비 요청 = "기동 중" 표시


def _control_dir() -> Path:
    d = _worker_root() / "control"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _watchdog_path() -> Path:
    return _control_dir() / "watchdog.json"


def _start_request_path() -> Path:
    return _control_dir() / "start_request.json"


def watchdog_meta() -> dict:
    return _read_json(_watchdog_path())


def watchdog_alive() -> bool:
    """개발서버 상주 워치독 생존 여부 — 원격 '켜기' 가능 조건."""
    ts = float(watchdog_meta().get("ts") or 0.0)
    return bool(ts and (time.time() - ts) < _WATCHDOG_STALE_SEC)


def start_request_pending() -> bool:
    meta = _read_json(_start_request_path())
    ts = float(meta.get("ts") or 0.0)
    return bool(ts and (time.time() - ts) < _START_REQUEST_FRESH_SEC)


def request_worker_start(requested_by: str = "") -> dict:
    """워커 원격 기동 요청 파일 작성 — 개발서버의 워치독이 소비해 uvicorn 을 띄운다.

    워커가 이미 살아 있으면 no-op. 워치독이 죽어 있으면 요청은 남겨두되
    ok=False 로 알린다 (워치독이 나중에 살아나면 그때 소비될 수 있음)."""
    if worker_alive(fresh_read=True):
        return {"ok": True, "already_running": True}
    payload = {
        "ts": time.time(),
        "id": uuid.uuid4().hex[:10],
        "requested_by": str(requested_by or ""),
    }
    if not _write_json_atomic(_start_request_path(), payload):
        return {"ok": False, "error": f"failed to write {_start_request_path()}"}
    alive = watchdog_alive()
    if not alive:
        return {
            "ok": False,
            "requested": True,
            "error": "개발서버 워치독(scripts/worker_watchdog.py)이 응답하지 않습니다 — 개발서버 머신에서 워치독을 먼저 상주시켜야 원격 기동이 됩니다.",
        }
    logger.info(f"worker start requested by {requested_by!r} — watchdog will spawn the worker")
    return {"ok": True, "requested": True}


# ── startup / status ──────────────────────────────────────────────────────────

def start_services() -> None:
    """백그라운드 스레드 기동 (app_v2.runtime.startup 에서 호출).

    역할과 무관하게 세 루프(heartbeat/큐 소비/api 워처)를 모두 띄우고, 각
    루프가 매 반복 server_role() 을 확인한다 — 관리자 탭에서 역할을 바꾸면
    재시작 없이 다음 반복부터 반영된다."""
    if _STARTED.is_set():
        return
    _STARTED.set()
    from core import worker_tasks  # noqa: F401 — 핸들러 등록 부수효과
    role, source = _role_cached()
    if role == "worker":
        _ensure_dirs()
    threading.Thread(target=_heartbeat_loop, daemon=True, name="flow-worker-heartbeat").start()
    threading.Thread(target=_consume_loop, daemon=True, name="flow-worker-consume").start()
    threading.Thread(target=_api_watch_loop, daemon=True, name="flow-worker-watch").start()
    logger.info(
        f"worker dispatch: role={role} (source={source}), concurrency={worker_concurrency()}, "
        f"handlers={sorted(_HANDLERS.keys())}, root={_worker_root()}"
    )


def status() -> dict:
    """모니터/관리용 스냅샷 (routers/monitor.py, routers/admin.py 에서 노출)."""
    meta = heartbeat_meta(fresh_read=True)
    ts = float(meta.get("ts") or 0.0)
    alive = _judge_alive(meta)
    # 신호등 진단 — 관리자 탭이 오프라인 사유를 구체적으로 안내한다.
    #   no_heartbeat: heartbeat 파일 없음/비정상 → 개발서버 data_root 불일치 의심.
    #   stale:        heartbeat 는 있으나 낡음 → 워커 다운 (또는 역할이 worker 아님).
    # clock_skew_suspected: 절대 나이 기준으로는 stale 인데 변화 감지 경로로
    # 살아있다고 판정된 경우 — 서버 간 벽시계 차이가 stale 한도를 넘는 배포.
    offline_reason = None if alive else ("stale" if ts else "no_heartbeat")
    clock_skew_suspected = bool(alive and ts and abs(time.time() - ts) >= stale_sec())
    wd_meta = watchdog_meta()
    wd_ts = float(wd_meta.get("ts") or 0.0)
    with _STATS_LOCK:
        stats = dict(_STATS)
    role, source = _role_cached()
    return {
        "role": role,
        "role_source": source,          # env | config | auto — env 면 UI 편집 불가
        "role_editable": source != "env",
        "offload_enabled": offload_enabled(),
        "external_services_enabled": external_services_enabled(),
        "worker_alive": alive,
        "offline_reason": offline_reason,
        # 살아있지만 메모리 과부하로 새 오프로드를 받지 않는 상태 ("" = 정상).
        "worker_overloaded_reason": worker_overloaded_reason(meta) if alive else "",
        "clock_skew_suspected": clock_skew_suspected,
        "heartbeat_age_sec": round(time.time() - ts, 1) if ts else None,
        "heartbeat": meta,
        "queue_depth": _queue_depth(),
        "stale_sec": stale_sec(),
        "watchdog_alive": watchdog_alive(),
        "watchdog_age_sec": round(time.time() - wd_ts, 1) if wd_ts else None,
        "watchdog": wd_meta,
        "start_request_pending": start_request_pending(),
        "stats": stats,
        # worker 역할의 SplitTable 검색 운영서버 위임 상태 (core/upstream_proxy).
        "upstream_proxy": _upstream_proxy_status(),
    }


def _upstream_proxy_status() -> dict:
    try:
        from core import upstream_proxy

        return upstream_proxy.status()
    except Exception:
        return {}
