"""core/worker_dispatch.py — 개발서버 워커 오프로드 (v9.4.x).

운영(API)/개발(워커) 2개 서버가 같은 flow-data(shared workspace)를 공유할 때,
무거운 파일 산출 작업(SplitTable pivot 캐시, fab lot index, ML_TABLE lookup
파티션 빌드)을 개발서버로 넘겨 운영서버 부하를 줄인다.

원칙:
  - 외부 브로커(Redis/RabbitMQ) 없이 shared workspace 파일만 사용.
  - 개발서버는 언제든 꺼질 수 있다 → heartbeat 기반 헬스체크. 사용자 동기 작업은
    기존처럼 로컬 폴백할 수 있지만, 자동 캐시 작업은 durable 큐에 남겨 운영 API의
    CPU/메모리를 침범하지 않고 worker 복귀 뒤 계속한다.
  - 오프로드 가능한 작업은 "결과가 shared workspace 파일로 남는 것"만.
    프로세스 RAM 캐시는 API 응답을 서빙하는 운영서버가 소유하며 worker의
    오프로드/백그라운드 예열 대상이 아니다.
  - 이중 실행 안전성은 이 모듈이 아니라 기존 cross-server 가드(shared_lease,
    ml_table_lookup build lock)가 담당한다. 최악의 폴백 경쟁도 중복 빌드
    1회로 수렴 — 기존 무락 동작과 같은 안전도.

역할 (우선순위: env FLOW_SERVER_ROLE > 마커 파일 > server_role.json > 기본 "api"):
  - "api"        운영서버. 작업을 큐에 넣고 결과를 기다린다. 워커 다운이면 로컬 실행.
  - "worker"     개발서버. heartbeat 를 쓰고 큐를 소비한다.

  **기본값은 항상 "api" 다.** 아무 표시가 없는 서버는 운영서버다 — 운영서버는
  껐다 켜도, 코드를 갈아끼워도, prod 자동감지가 빗나가도 항상 운영으로 돌아온다.
  개발서버 역할은 소스 밖 hostname별 data_root 마커로 선언한다:

      {data_root}/worker/roles/<hostname>/worker.marker
  legacy app_root의 `.dev_worker`/`.standalone`은 배포 안전을 위해 무시한다.
  예전 standalone 설정·마커는 하위 호환상 운영(api)으로 해석한다.

  worker 역할은 **마커 파일이나 env 로만** 성립한다. server_role.json 에만
  "worker" 가 남아 있으면 무시하고 api 로 뜬다(경고 로그 1회) — 예전에는 한 번
  워커로 저장된 서버가 영구히 워커로 부팅해서, 운영서버가 재시작 후 조용히
  스케줄러 없는 상태로 올라올 수 있었다.

  역할 JSON도 {data_root}/worker/roles/<host>.json 에 저장한다. hostname 분리로
  shared workspace에서도 서버별 의미를 유지한다. 관리자 탭에서 역할을 저장하면
  마커 파일도 같이 맞춰 쓴다(api 로 바꾸면 마커 삭제). 역할 변경은 재시작
  없이 디스패치/heartbeat/큐 소비에 즉시 반영된다 (단, startup 에서 한 번만
  켜는 heavy 백그라운드 스케줄러 세트는 재시작 후 완전 적용).

파일 레이아웃 ({data_root}/worker/):
  heartbeat.json            워커 생존 신호 (FLOW_WORKER_HEARTBEAT_SEC 주기, 기본 10s)
  api_heartbeat.json        운영 API 생존 신호 (자동 유지보수 claim 게이트)
  roles/<host>/worker.marker 호스트별 개발 worker 역할 마커
  queue/<id>.task.json      대기 작업 {id, type, payload, deadline, ...}
  claimed/<id>.task.json    워커가 os.replace 로 원자적으로 가져간 작업
  results/<id>.json         실행 결과 {id, ok, result|error, ...}
  control/watchdog.json     개발서버 상주 워치독(scripts/worker_watchdog.py) 생존 신호
  control/start_request.json 운영서버 관리자가 요청한 워커 원격 기동 (워치독이 소비)

환경변수:
  FLOW_SERVER_ROLE            api | worker (legacy standalone은 api로 정규화)
  FLOW_WORKER_OFFLOAD         0 이면 api 역할이어도 오프로드 끔 (기본 켬)
  FLOW_WORKER_HEARTBEAT_SEC   워커 heartbeat 주기 (기본 10)
  FLOW_WORKER_STALE_SEC       heartbeat 가 이보다 오래되면 다운 판정 (기본 45)
  FLOW_WORKER_CONCURRENCY     워커 동시 실행 슬롯 (기본 1 — Polars 빌드 두 개가
                              겹쳐 OOM 나는 것을 막고, 필요할 때만 명시적으로 확대)
  FLOW_WORKER_TASK_TIMEOUT_SEC 오프로드 기본 대기 한도 (기본 1800)
  FLOW_WORKER_MAX_QUEUE       큐 깊이 상한 (기본 256, 자동 캐시는 로컬 실행 안 함)
  FLOW_WORKER_MAINTENANCE_EVERY 일반 작업 N-1건 뒤 캐시 작업 1건 보장 (기본 5)
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
_CLAIMED_STALE_SEC = 8 * 24 * 3600  # 직렬 대기까지 실행 중으로 보존한다
_JANITOR_INTERVAL_SEC = 60.0
_POLL_IDLE_SEC = 0.5                # 워커 큐 폴링 주기
_RESULT_POLL_SEC = 0.3              # api 결과 대기 폴링 주기
_ALIVE_CACHE_SEC = 2.0              # heartbeat 파일 read 캐시

_HANDLERS: dict[str, Callable[[dict], dict]] = {}
_STATS_LOCK = threading.Lock()
_STATS = {"offloaded": 0, "remote_ok": 0, "remote_fail": 0, "local_fallback": 0, "local_direct": 0,
          "local_worker_overloaded": 0, "remote_deferred": 0, "deduped": 0}
_ALIVE_CACHE: dict[str, Any] = {"ts": 0.0, "meta": None}
_API_ALIVE_CACHE: dict[str, Any] = {"ts": 0.0, "meta": None}
# heartbeat ts 값이 마지막으로 '바뀐' 시각(이 서버의 monotonic 기준).
# 서버 간 벽시계가 어긋나도(skew) 값의 변화 자체는 신뢰할 수 있다.
_HB_CHANGE: dict[str, Any] = {"ts": None, "mono": None}
_HB_CHANGE_LOCK = threading.Lock()
_STARTED = threading.Event()
_WORKER_RUNNING: set[str] = set()
_WORKER_RUNNING_LOCK = threading.Lock()
_LOCAL_HEAVY_GATE = threading.Semaphore(1)
_LOCAL_UNGUARDED_TYPES = {"ping", "flowi_chat_turn", "filebrowser_sql_query"}
_WORKER_NON_MAINT_CLAIMS = 0

# 제품 단위 캐시 산출 작업 — 한 서버에서 **하나만** 돈다. 로컬 폴백이든 워커가
# 넘겨받은 오프로드든, 캐시 관리의 수동/예약 스캔과 같은 슬롯(core.scan_gate)을
# 잡는다. 예전에는 scan gate / _LOCAL_HEAVY_GATE / worker slots 가 서로 다른
# 단일 슬롯이라, 한 서버에서 서로 다른 제품의 랏캐시 빌드가 동시에 돌았다.
_CACHE_BUILD_TYPES = {
    "ml_lookup_cache_build",          # 랏(lookup) 캐시
    "splittable_match_cache_refresh",  # FAB 매칭 캐시
    "splittable_pivot_build",          # 제품 원본 pivot 캐시
    "splittable_fab_lot_index_build",  # FAB lot 인덱스
    "splittable_lot_progress_cache_refresh",  # 수동 WIP/latest-lot 공유 캐시
    "fab_matching_alert_scan",        # FAB source matching/reticle scan
    "et_tracker_scan",                # ET history/diff background scan
    "auto_report_generate",           # legacy PPT/HTML rendering
    "auto_report_history_refresh",    # rolling ET history snapshot
}


def _cache_gate(task_type: str, label: str, *, product: str = ""):
    """캐시 산출 작업이면 서버 공용 슬롯을, 아니면 통과용 더미를 돌려준다."""
    import contextlib

    if str(task_type) not in _CACHE_BUILD_TYPES:
        @contextlib.contextmanager
        def _pass():
            yield True
        return _pass()
    try:
        from core import scan_gate
        return scan_gate.exclusive(
            str(task_type), label or str(task_type), product=str(product or ""),
            source="worker_dispatch",
        )
    except Exception:
        logger.debug("scan gate unavailable for %s", task_type, exc_info=True)

        @contextlib.contextmanager
        def _fallback():
            yield True
        return _fallback()


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


_ROLE_VALUES = ("api", "worker")
_ROLE_CACHE: dict[str, Any] = {"ts": 0.0, "role": "", "source": ""}
_ROLE_CACHE_SEC = 3.0


def _role_config_path() -> Path:
    from core.paths import PATHS
    return PATHS.app_root / "server_role.json"


# ── 역할 마커 파일 ────────────────────────────────────────────────────────────
# 운영서버는 "아무 표시도 없는 상태"가 곧 운영이다. 개발서버 역할은 소스 트리
# 밖의 hostname별 data_root 마커에만 둔다. 소스 폴더가 복제/배포돼도 서버 역할은
# 따라가지 않는다.
_MARKER_NAMES: dict[str, tuple[str, ...]] = {
    "worker": ("worker.marker",),
    # 제거된 역할의 기존 마커는 역할 판정에서는 무시하고 다음 저장 때 정리한다.
    "standalone": ("standalone.marker",),
}
_LEGACY_SOURCE_MARKERS = (".dev_worker", ".dev_worker.txt", ".standalone", ".standalone.txt")
_MARKER_ROLE_ORDER = ("worker",)
_MARKER_BODY = (
    "# flow 서버 역할 마커 — 이 파일이 있으면 이 서버는 '{role}' 역할로 뜬다.\n"
    "# 파일을 지우면 다음 기동부터 운영(api) 서버가 된다. 내용은 읽지 않는다.\n"
)
_MARKER_WARNED = threading.Event()


def _marker_dirs() -> tuple[Path, ...]:
    """호스트별 역할 마커 위치(data_root/worker/roles/<hostname>/)."""
    try:
        root = _worker_root() / "roles" / _host_token()
    except Exception:
        return ()
    return (root,)


def _host_token() -> str:
    return re.sub(r"[^a-z0-9._-]", "_", socket.gethostname().strip().lower()) or "unknown"


def _legacy_marker_paths() -> tuple[Path, ...]:
    """정리 전용 legacy 소스 마커. 역할 판정에는 사용하지 않는다."""
    try:
        from core.paths import PATHS
        root = Path(PATHS.app_root)
    except Exception:
        return ()
    return tuple(
        directory / name
        for directory in (root, root / "backend")
        for name in _LEGACY_SOURCE_MARKERS
    )


def marker_path(role: str) -> Path | None:
    """해당 역할의 표준 호스트 로컬 마커 경로. api 는 마커가 없다."""
    names = _MARKER_NAMES.get(str(role or "").strip().lower())
    dirs = _marker_dirs()
    if not names or not dirs:
        return None
    return dirs[0] / names[0]


def _find_marker(role: str) -> Path | None:
    for d in _marker_dirs():
        for name in _MARKER_NAMES.get(role, ()):
            fp = d / name
            try:
                if fp.is_file():
                    return fp
            except Exception:
                continue
    return None


def marker_role() -> tuple[str, Path] | None:
    """(role, 마커경로) — 마커가 없으면 None."""
    for role in _MARKER_ROLE_ORDER:
        fp = _find_marker(role)
        if fp is not None:
            return role, fp
    return None


def _sync_role_markers(role: str) -> str | None:
    """역할에 맞는 마커만 남긴다. 성공 시 None, 실패 시 사유 문자열.

    관리자 탭 저장(set_role)이 마커와 어긋나면 저장이 무의미해지므로 —
    마커가 config 보다 우선한다 — 저장 시 마커도 같이 맞춘다."""
    dirs = _marker_dirs()
    if not dirs:
        return "app_root 를 확인할 수 없습니다"
    keep_fp = marker_path(role)
    for d in dirs:
        for names in _MARKER_NAMES.values():
            for name in names:
                fp = d / name
                if keep_fp is not None and fp == keep_fp:
                    continue
                try:
                    if fp.is_file():
                        fp.unlink()
                except Exception as exc:
                    return f"{fp} 삭제 실패 ({type(exc).__name__}: {exc})"
    for fp in _legacy_marker_paths():
        try:
            if fp.is_file():
                fp.unlink()
        except Exception as exc:
            logger.warning("legacy source role marker cleanup failed (%s): %s", fp, exc)
    if keep_fp is not None and not keep_fp.is_file():
        try:
            keep_fp.parent.mkdir(parents=True, exist_ok=True)
            keep_fp.write_text(_MARKER_BODY.format(role=role), "utf-8")
        except Exception as exc:
            return f"{keep_fp} 생성 실패 ({type(exc).__name__}: {exc})"
    return None


def _role_fallback_path() -> Path:
    """서버별 역할 폴백 경로 — app_root 가 읽기전용인 배포용.

    data_root 는 서버 간 공유 workspace 이므로 hostname 으로 파일을 분리해
    '서버별 로컬 역할' 의미를 유지한다."""
    return _worker_root() / "roles" / f"{_host_token()}.json"


def _resolve_role() -> tuple[str, str]:
    """(role, source) — env > 마커 파일 > server_role.json > 기본 "api".

    기본값이 api 인 것이 이 설계의 핵심이다: 표시가 없는 서버는 운영서버이므로
    운영서버는 껐다 켜도 항상 운영으로 돌아온다. 개발 워커만 마커 파일(또는 env)로
    명시한다 — server_role.json 에만 남은 "worker" 는 무시한다."""
    raw = os.environ.get("FLOW_SERVER_ROLE", "").strip().lower()
    if raw == "standalone":
        return "api", "env"
    if raw in _ROLE_VALUES:
        return raw, "env"
    found = marker_role()
    if found is not None:
        return found[0], "marker"
    best_role, best_ts, best_path = "", float("-inf"), None
    candidates: list[Path] = []
    for fn in (_role_config_path, _role_fallback_path):
        try:
            candidates.append(fn())
        except Exception:
            continue        # PATHS 미구성/읽기 불가 — 기본값(api)으로 간다.
    for path in candidates:
        try:
            data = _read_json(path)
            cfg_role = str(data.get("role") or "").strip().lower()
            if cfg_role == "standalone":
                cfg_role = "api"
            if cfg_role not in _ROLE_VALUES:
                continue
            ts = float(data.get("updated_at") or 0.0)
            if ts > best_ts:
                best_role, best_ts, best_path = cfg_role, ts, path
        except Exception:
            continue
    if best_role == "worker":
        # 마커 없는 worker 저장값은 신뢰하지 않는다 — 이 서버를 워커로 쓰려면
        # 마커 파일을 만들어야 한다. 조용히 넘어가면 "왜 워커가 안 뜨지" 가 되므로
        # 프로세스당 한 번 경고를 남긴다.
        if not _MARKER_WARNED.is_set():
            _MARKER_WARNED.set()
            logger.warning(
                f"server_role.json 의 'worker' 를 무시하고 api 로 기동한다 ({best_path}) — "
                f"이 서버를 개발 워커로 쓰려면 마커 파일을 만들어라: {marker_path('worker')}"
            )
        return "api", "default"
    if best_role:
        return best_role, "config"
    return "api", "default"


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
    """현재 역할이 어디서 왔는지 — env | marker | config | default (env 면 UI 편집 불가)."""
    return _role_cached()[1]


def set_role(role: str) -> dict:
    """역할 마커 파일 + server_role.json 저장 (관리자 탭에서 호출). env 지정 시 거부.

    마커가 우선순위에서 config 보다 위이므로 **마커부터 맞춘다** — worker이면
    마커를 만들고, api 면 worker와 옛 standalone 마커를 지운다. 마커를 못 바꾸면
    저장 자체를 거부한다: config 만 써 두면 화면에는 바뀐 것처럼 보이는데 실제
    역할은 그대로인 상태가 된다.

    app_root 가 읽기전용인 배포에서는 {data_root}/worker/roles/<host>.json
    폴백에 저장한다 — _resolve_role() 이 두 파일 중 최신 updated_at 을 택한다.
    저장 즉시 디스패치/heartbeat/큐 소비 루프에 반영된다 — 루프들이 매 반복
    server_role() 을 확인한다."""
    value = str(role or "").strip().lower()
    if value not in _ROLE_VALUES:
        return {"ok": False, "code": "invalid",
                "error": f"invalid role: {role!r} (api|worker)"}
    if os.environ.get("FLOW_SERVER_ROLE", "").strip():
        return {"ok": False, "code": "pinned",
                "error": "role is pinned by FLOW_SERVER_ROLE env — unset it to manage from UI"}
    marker_err = _sync_role_markers(value)
    if marker_err is not None:
        logger.error(f"role marker sync failed for {value!r}: {marker_err}")
        return {"ok": False, "code": "marker_locked",
                "error": f"역할 마커 파일을 바꾸지 못했습니다 — {marker_err}. "
                         f"app_root 가 읽기전용이면 FLOW_SERVER_ROLE 환경변수로 지정하세요."}
    payload = {"role": value, "updated_at": time.time()}
    # 역할 설정도 소스 트리에 쓰지 않는다. 설치 폴더를 복사하거나 Git에 올려도
    # 서버 정체성이 따라가지 않도록 hostname별 data_root 파일만 사용한다.
    path_used = _role_fallback_path()
    try:
        path_used.parent.mkdir(parents=True, exist_ok=True)
        err = _try_write_json(path_used, payload)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    if err is not None:
        logger.error("host-local role save failed — %s: %s", path_used, err)
        _ROLE_CACHE.update(ts=0.0, role="", source="")
        mk = marker_path(value)
        if value != "api" and mk is not None and mk.is_file():
            return {"ok": True, "role": value, "path": str(mk),
                    "warning": f"호스트 마커로 적용됨 — 역할 기록 실패 ({err})"}
        return {"ok": False, "code": "write_failed",
                "error": f"host-local role save failed — {path_used} ({err})"}
    _ROLE_CACHE.update(ts=0.0, role="", source="")
    mk = marker_path(value)
    logger.info(
        f"server role set to {value!r} — config {path_used}"
        + (f", marker {mk}" if value != "api" else " (마커 없음 = 운영)")
    )
    return {"ok": True, "role": value, "path": str(path_used),
            "marker": str(mk) if value != "api" else ""}


def offload_enabled() -> bool:
    return server_role() == "api" and _env_flag("FLOW_WORKER_OFFLOAD", True)


def external_services_enabled() -> bool:
    """외부 서비스 연동(S3 업로드/자동 동기화, 운영 스케줄러, 메일 발송) 허용 여부.

    worker(개발서버) 역할은 로드 분산 전용(SplitTable 조회·데이터 처리)이다 —
    외부로 나가는 부수효과는 운영(api) 서버만 수행한다. 역할은
    재시작 없이 바뀔 수 있으므로 루프형 호출부는 매 반복 이 함수를 확인한다."""
    return server_role() != "worker"


def heartbeat_interval_sec() -> float:
    return _env_float("FLOW_WORKER_HEARTBEAT_SEC", 10.0, 2.0, 300.0)


def stale_sec() -> float:
    return _env_float("FLOW_WORKER_STALE_SEC", 45.0, 5.0, 3600.0)


def default_task_timeout_sec() -> float:
    return _env_float("FLOW_WORKER_TASK_TIMEOUT_SEC", 1800.0, 10.0, 6 * 3600.0)


def worker_concurrency() -> int:
    return int(_env_float("FLOW_WORKER_CONCURRENCY", 1.0, 1.0, 16.0))


def max_queue_depth() -> int:
    # task 파일은 작은 메타데이터뿐이고 dedupe_key로 자동 캐시 중복을 합친다.
    # 제품 수가 32개를 넘는 환경에서도 뒤 제품이 로컬 fallback되지 않게 한다.
    return int(_env_float("FLOW_WORKER_MAX_QUEUE", 256.0, 1.0, 1000.0))


def maintenance_every_claims() -> int:
    """일반 작업이 계속 와도 N번째 claim은 유지보수 작업에 배정한다."""
    return int(_env_float("FLOW_WORKER_MAINTENANCE_EVERY", 5.0, 2.0, 100.0))


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


# ── paths ─────────────────────────────────────────────────────────────────────

def _worker_root() -> Path:
    from core.paths import PATHS
    return PATHS.data_root / "worker"


def _heartbeat_path() -> Path:
    return _worker_root() / "heartbeat.json"


def _api_heartbeat_path() -> Path:
    return _worker_root() / "api_heartbeat.json"


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


def api_heartbeat_meta(*, fresh_read: bool = False) -> dict:
    """운영 API 생존 신호. worker의 자동 유지보수 claim 허용에 사용한다."""
    now = time.time()
    if (
        not fresh_read
        and _API_ALIVE_CACHE["meta"] is not None
        and (now - _API_ALIVE_CACHE["ts"]) < _ALIVE_CACHE_SEC
    ):
        return dict(_API_ALIVE_CACHE["meta"])
    meta = _read_json(_api_heartbeat_path())
    _API_ALIVE_CACHE.update(ts=now, meta=dict(meta))
    return meta


def api_alive(*, fresh_read: bool = False) -> bool:
    """운영 API가 실제로 켜져 있는지 판정한다.

    자동 캐시(maintenance)는 이 신호가 있을 때만 worker가 새 작업을 잡는다.
    수동 스캔/일반 작업은 이 조건과 무관해 API 복구 작업을 막지 않는다.
    """
    meta = api_heartbeat_meta(fresh_read=fresh_read)
    ts = float(meta.get("ts") or 0.0)
    return bool(ts and str(meta.get("role") or "") == "api" and abs(time.time() - ts) < stale_sec())


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


def _invalid_generated_cache_task(task: dict) -> bool:
    """Detect FAB tasks created by mistaking `<product>.build` for a product."""
    if str((task or {}).get("type") or "") != "splittable_fab_lot_index_build":
        return False
    payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
    product = str(payload.get("product") or "").strip().rstrip("/\\")
    return bool(product) and product.casefold().endswith(".build")


def queued_cache_stale_sec() -> float:
    """Heavy task maximum unclaimed queue age (default: seven days)."""
    return _env_float(
        "FLOW_CACHE_QUEUE_STALE_SEC", 7 * 24 * 3600.0,
        3600.0, 30 * 24 * 3600.0,
    )


def _stale_queued_cache_task(task: dict, *, now: float | None = None) -> bool:
    if str((task or {}).get("type") or "") not in _CACHE_BUILD_TYPES:
        return False
    submitted = float((task or {}).get("submitted_at") or (task or {}).get("created_at") or 0.0)
    if submitted <= 0:
        return False
    current = time.time() if now is None else float(now)
    return current - submitted >= queued_cache_stale_sec()


def _prune_invalid_generated_cache_tasks() -> int:
    """Remove only unclaimed, invalid staging-product tasks from the durable queue."""
    removed = 0
    try:
        candidates = list(_queue_dir().glob("*.task.json"))
    except Exception:
        return 0
    for fp in candidates:
        if not _invalid_generated_cache_task(_read_json(fp)):
            continue
        try:
            fp.unlink()
            removed += 1
        except Exception:
            pass
    if removed:
        logger.warning("removed %d invalid FAB staging-product task(s) from durable queue", removed)
    return removed


def _prune_stale_queued_cache_tasks(*, now: float | None = None) -> int:
    """Remove unclaimed cache builds that made no transition for five minutes."""
    current = time.time() if now is None else float(now)
    removed = 0
    try:
        candidates = list(_queue_dir().glob("*.task.json"))
    except Exception:
        return 0
    for fp in candidates:
        task = _read_json(fp)
        if not _stale_queued_cache_task(task, now=current):
            continue
        task_id = str(task.get("id") or fp.name.removesuffix(".task.json"))
        try:
            fp.unlink()
        except Exception:
            continue
        removed += 1
        _write_json_atomic(_results_dir() / f"{task_id}.json", {
            "id": task_id,
            "type": str(task.get("type") or ""),
            "ok": False,
            "error": "stale_queued_no_progress",
            "finished_at": current,
            "queue_stale_sec": queued_cache_stale_sec(),
        })
    if removed:
        logger.warning("removed %d cache task(s) queued without progress for five minutes", removed)
    return removed


def queue_snapshot(limit: int = 50) -> dict:
    """관리 화면용 공유 worker queue 요약. payload의 논리 식별자만 노출한다."""
    _prune_invalid_generated_cache_tasks()
    _prune_stale_queued_cache_tasks()
    rows: list[dict] = []
    try:
        candidates = sorted(
            (p for p in _queue_dir().iterdir() if p.name.endswith(".task.json")),
            key=lambda p: p.name,
        )[:max(1, min(200, int(limit or 50)))]
    except Exception:
        candidates = []
    for fp in candidates:
        task = _read_json(fp)
        payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
        rows.append({
            "id": str(task.get("id") or fp.stem),
            "type": str(task.get("type") or ""),
            "submitted_at": task.get("submitted_at") or task.get("created_at") or "",
            "product": str(payload.get("product") or payload.get("file") or ""),
            "priority": str(task.get("priority") or "normal"),
            "dedupe_key": str(task.get("dedupe_key") or ""),
        })
    meta = heartbeat_meta()
    prod_alive = api_alive()
    return {
        "depth": _queue_depth(),
        "queued": rows,
        "running": list(meta.get("running") or []),
        "worker_alive": worker_alive(),
        "accepting_tasks": bool(meta.get("accepting_tasks", True)),
        "overloaded_reason": str(meta.get("overloaded_reason") or ""),
        "load": dict(meta.get("load") or {}),
        "production_api_alive": prod_alive,
        "automatic_cache_paused": bool(not prod_alive and any(
            row.get("priority") == "maintenance" for row in rows
        )),
    }


def _bump(key: str) -> None:
    with _STATS_LOCK:
        _STATS[key] = _STATS.get(key, 0) + 1


def _find_deduped_task(dedupe_key: str) -> tuple[str, Path] | None:
    key = str(dedupe_key or "").strip()
    if not key:
        return None
    for directory in (_queue_dir(), _claimed_dir()):
        try:
            candidates = list(directory.glob("*.task.json"))
        except Exception:
            continue
        for fp in candidates:
            task = _read_json(fp)
            if str(task.get("dedupe_key") or "") == key:
                task_id = str(task.get("id") or "")
                if task_id:
                    # wait 경로는 queue 파일명으로 claimed/result 위치도 계산한다.
                    return task_id, _queue_dir() / fp.name
    return None


def _submit(
    task_type: str,
    payload: dict,
    timeout_sec: float,
    *,
    priority: str = "normal",
    dedupe_key: str = "",
) -> tuple[str, Path, bool] | None:
    _prune_stale_queued_cache_tasks()
    existing = _find_deduped_task(dedupe_key)
    if existing is not None:
        _bump("deduped")
        return existing[0], existing[1], True
    task_id = f"{int(time.time())}-{uuid.uuid4().hex[:10]}"
    task = {
        "id": task_id,
        "type": str(task_type),
        "payload": payload or {},
        "submitted_by": _owner_id(),
        "submitted_at": time.time(),
        "deadline": time.time() + float(timeout_sec),
        "priority": (
            str(priority).lower()
            if str(priority).lower() in {"interactive", "normal", "maintenance"}
            else "normal"
        ),
        "dedupe_key": str(dedupe_key or ""),
    }
    try:
        _ensure_dirs()
        fp = _queue_dir() / f"{task_id}.task.json"
        if not _write_json_atomic(fp, task):
            return None
        return task_id, fp, False
    except Exception:
        return None


def submit_async(
    task_type: str,
    payload: dict,
    *,
    timeout_sec: float | None = None,
    priority: str = "normal",
    dedupe_key: str = "",
) -> dict:
    """Persist a worker task and return immediately without a local fallback.

    This is intentionally different from :func:`run_heavy`: user-triggered
    durable jobs (for example Auto report generation) must never execute on
    the production API server when the development worker is unavailable.
    The queue file remains in the shared data root until a worker claims it.
    """
    if server_role() != "api":
        return {"ok": False, "error": "async submission is only allowed on the api server"}
    if _queue_depth() >= max_queue_depth() and not _find_deduped_task(dedupe_key):
        return {"ok": False, "error": "worker queue is full"}
    timeout = float(timeout_sec if timeout_sec is not None else default_task_timeout_sec())
    submitted = _submit(
        task_type,
        payload,
        timeout,
        priority=priority,
        dedupe_key=dedupe_key,
    )
    if not submitted:
        return {"ok": False, "error": "failed to persist worker task"}
    task_id, _queue_fp, deduped = submitted
    _bump("deduped" if deduped else "offloaded")
    return {
        "ok": True,
        "task_id": task_id,
        "deduped": bool(deduped),
        "worker_alive": worker_alive(fresh_read=True),
    }


def async_status(task_id: str) -> dict:
    """Return a non-destructive snapshot for an asynchronously submitted task."""
    clean = str(task_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", clean):
        return {"state": "missing", "task_id": clean}
    _ensure_dirs()
    result_fp = _results_dir() / f"{clean}.json"
    if result_fp.is_file():
        result = _read_json(result_fp)
        return {
            "state": "completed" if result.get("ok") else "failed",
            "task_id": clean,
            "result": result,
        }
    name = f"{clean}.task.json"
    if (_claimed_dir() / name).is_file():
        return {"state": "running", "task_id": clean}
    if (_queue_dir() / name).is_file():
        return {"state": "queued", "task_id": clean}
    return {"state": "missing", "task_id": clean}


def _wait_for_result(
    task_id: str,
    queue_fp: Path,
    deadline: float,
    *,
    preserve_queued: bool = False,
) -> dict | None:
    """결과 파일 대기. None = 원격 실패/워커 사망/타임아웃 → 호출측 로컬 폴백.

    워커가 죽은 걸 감지하면 큐에 남은(미클레임) 작업은 지워서 부활한 워커가
    한참 뒤 낡은 작업을 실행하는 일을 막는다. 이미 claimed 된 작업은 그대로
    둔다 — 결과 파일은 무시되고, 이중 실행은 shared_lease/빌드 락이 막는다."""
    result_fp = _results_dir() / f"{task_id}.json"
    claimed_fp = _claimed_dir() / queue_fp.name
    while time.time() < deadline:
        if result_fp.is_file():
            res = _read_json(result_fp)
            # durable/deduped cache 작업은 여러 coordinator가 같은 task 결과를
            # 기다릴 수 있다. 첫 waiter가 지우지 않고 janitor가 TTL 뒤 정리한다.
            if not preserve_queued:
                try:
                    result_fp.unlink()
                except Exception:
                    pass
            if res.get("id") == task_id:
                return res
            return None
        if not worker_alive():
            if preserve_queued:
                return None
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
    if not preserve_queued and queue_fp.is_file() and not claimed_fp.is_file():
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
      FLOW_WORKER_OFFLOAD_MIN_AVAIL_GB  워커 호스트 가용 메모리 하한
                                        (기본 총량 25%, 2.5~4GB)
      FLOW_WORKER_OFFLOAD_MAX_MEM_PCT   워커 프로세스 메모리/한도 비율 상한 (기본 80)
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
    total = _f("system_memory_total_gb")
    default_min_avail = max(2.5, min(4.0, total * 0.25)) if total > 0 else 3.0
    min_avail = _env_float("FLOW_WORKER_OFFLOAD_MIN_AVAIL_GB", default_min_avail, 0.0, 64.0)
    if avail > 0 and avail < min_avail:
        return f"low_host_memory (avail {avail:.1f}GB < {min_avail:.1f}GB)"
    effective = _f("mem_effective_gb")
    limit = _f("mem_limit_gb")
    max_pct = _env_float("FLOW_WORKER_OFFLOAD_MAX_MEM_PCT", 80.0, 10.0, 100.0)
    if limit > 0 and effective > 0 and (100.0 * effective / limit) >= max_pct:
        return f"process_memory_high ({100.0 * effective / limit:.0f}% >= {max_pct:.0f}%)"
    return ""


def _run_local_heavy(
    task_type: str,
    name: str,
    local_fn: Callable[[], dict | None],
    *,
    idle_only: bool = False,
    product: str = "",
) -> dict | None:
    """워커 폴백 캐시/DB 작업을 프로세스당 하나로 직렬화하고 메모리를 admission.

    락 순서 주의 — 캐시 산출 작업(`_CACHE_BUILD_TYPES`)은 **`_LOCAL_HEAVY_GATE`
    를 쓰지 않고 서버 공용 스캔 슬롯만** 잡는다. 둘 다 잡으면 락 순서가 엇갈려
    교착이 난다: 스캔 게이트 작업이 슬롯을 든 채 `_LOCAL_HEAVY_GATE` 를 기다리고,
    동시에 pivot 빌드 스레드가 `_LOCAL_HEAVY_GATE` 를 든 채 슬롯을 기다리는 형태다.
    스캔 슬롯은 `_LOCAL_HEAVY_GATE` 보다 강한 직렬화(수동/예약 스캔·오프로드
    태스크까지 포함)라 캐시 작업에는 그것만으로 충분하다. 캐시가 아닌 heavy
    작업(view 재계산·lot_progress·ET 스캔)은 예전 그대로 `_LOCAL_HEAVY_GATE` 만
    쓰고, 이들은 스캔 슬롯을 요구하지 않으므로 순환이 생기지 않는다.
    """
    if task_type in _LOCAL_UNGUARDED_TYPES:
        return local_fn()
    is_cache_build = str(task_type) in _CACHE_BUILD_TYPES
    if not is_cache_build:
        queue_timeout = _env_float("FLOW_LOCAL_HEAVY_QUEUE_TIMEOUT_SEC", 600.0, 1.0, 3600.0)
        if not _LOCAL_HEAVY_GATE.acquire(timeout=queue_timeout):
            logger.warning("local heavy queue timeout: %s", name)
            return {"ok": False, "error": "local_heavy_queue_timeout"}
    try:
        if idle_only:
            idle_wait = _env_float("FLOW_LOCAL_HEAVY_IDLE_WAIT_SEC", 1800.0, 0.0, 21600.0)
            quiet_for = _env_float("FLOW_LOCAL_HEAVY_IDLE_QUIET_SEC", 10.0, 0.0, 300.0)
            idle_deadline = time.monotonic() + idle_wait
            while True:
                try:
                    from core import request_priority

                    busy = request_priority.users_active(quiet_for_sec=quiet_for)
                except Exception:
                    busy = False
                if not busy:
                    break
                if time.monotonic() >= idle_deadline:
                    logger.info("local heavy deferred until next idle window: %s", name)
                    return {"ok": False, "error": "local_heavy_waiting_for_idle"}
                time.sleep(2.0)
        memory_wait = _env_float("FLOW_LOCAL_HEAVY_MEMORY_WAIT_SEC", 120.0, 0.0, 1800.0)
        deadline = time.monotonic() + memory_wait
        while True:
            reason = ""
            try:
                from core.runtime_limits import process_memory_high, process_memory_snapshot

                snap = process_memory_snapshot()
                total = float(snap.get("system_memory_total_gb") or 0.0)
                available = float(snap.get("system_memory_available_gb") or 0.0)
                default_reserve = max(2.5, min(6.0, total * 0.15)) if total > 0 else 3.0
                reserve = _env_float("FLOW_LOCAL_HEAVY_MIN_AVAILABLE_GB", default_reserve, 0.0, 64.0)
                if process_memory_high():
                    reason = "process_memory_high"
                elif available > 0 and available < reserve:
                    reason = f"low_host_memory:{available:.1f}GB<{reserve:.1f}GB"
            except Exception:
                reason = ""
            if not reason:
                if not is_cache_build:
                    return local_fn()
                # 캐시 산출 작업은 서버 공용 슬롯을 잡고 실행한다 — 수동/예약
                # 스캔이나 오프로드 태스크와 겹치지 않는다. admission(위 idle·
                # 메모리 대기)을 지난 뒤에 잡아, 대기하는 동안 슬롯을 붙들고
                # 다른 캐시 작업을 막지 않는다.
                with _cache_gate(task_type, name, product=product) as acquired:
                    if not acquired:
                        return {"ok": False, "error": "cache_gate_timeout", "reason": "다른 캐시 작업 진행 중"}
                    return local_fn()
            if time.monotonic() >= deadline:
                logger.warning("local heavy memory admission timeout: %s (%s)", name, reason)
                return {"ok": False, "error": "local_heavy_memory_guard", "reason": reason}
            time.sleep(2.0)
    finally:
        if not is_cache_build:
            _LOCAL_HEAVY_GATE.release()


def run_heavy(
    task_type: str,
    payload: dict,
    local_fn: Callable[[], dict | None],
    *,
    timeout_sec: float | None = None,
    label: str = "",
    local_idle_only: bool = False,
    local_fallback: bool = True,
    durable: bool = False,
    priority: str = "normal",
    dedupe_key: str = "",
) -> dict | None:
    """무거운 파일 산출 작업을 워커로 오프로드하고, 실패하면 로컬 실행.

    - api 역할 + 워커 생존 + 워커 메모리 여유 + 큐 여유 → 큐에 넣고 결과 대기.
      원격 성공 시 결과 dict 반환 (로컬 실행 없음).
    - 그 외 모든 경우(워커 역할 직접 실행, 워커 다운/과부하, 타임아웃, 원격
      에러) → local_fn() 실행 결과 반환. 디스패치 계층에서 예외를 밖으로 던지지
      않는다 (local_fn 자체의 예외는 그대로 전파 — 기존 호출부 동작 유지)."""
    name = label or task_type

    # Automatic shared-cache maintenance is a durable queue, not a
    # synchronous RPC. Persist it even while the worker is offline and return
    # immediately so the production API never starts an overlapping fallback.
    if durable and server_role() == "api":
        existing = _find_deduped_task(dedupe_key)
        if _queue_depth() >= max_queue_depth() and existing is None:
            return {"ok": False, "queued": False, "deferred": True, "error": "worker queue is full"}
        timeout = max(
            float(timeout_sec if timeout_sec is not None else default_task_timeout_sec()),
            7 * 24 * 3600.0,
        )
        submitted = _submit(
            task_type, payload, timeout, priority=priority, dedupe_key=dedupe_key,
        )
        if not submitted:
            return {"ok": False, "queued": False, "deferred": True, "error": "failed to persist worker task"}
        task_id = submitted[0]
        deduped = bool(submitted[2]) if len(submitted) > 2 else False
        _bump("deduped" if deduped else "offloaded")
        return {
            "ok": True, "queued": True, "deferred": True,
            "task_id": task_id, "deduped": deduped,
            "worker_alive": worker_alive(fresh_read=True),
        }

    def _local_or_deferred(reason: str):
        if not local_fallback:
            return {"ok": False, "queued": False, "deferred": True, "error": reason}
        return _run_local_heavy(
            task_type, name, local_fn, idle_only=local_idle_only,
            product=str((payload or {}).get("product") or ""),
        )

    if not offload_enabled():
        _bump("local_direct")
        return _local_or_deferred("worker offload is disabled")
    alive = worker_alive()
    if not alive:
        _bump("local_direct")
        return _local_or_deferred("development worker is offline")
    overload = worker_overloaded_reason()
    if overload:
        logger.info(f"worker overloaded ({overload}) — running {name} locally")
        _bump("local_worker_overloaded")
        return _local_or_deferred(f"development worker overloaded: {overload}")
    if _queue_depth() >= max_queue_depth():
        existing = _find_deduped_task(dedupe_key)
        if existing is None:
            logger.info(f"worker queue full — running {name} locally")
            _bump("local_direct")
            return _local_or_deferred("worker queue is full")
    timeout = float(timeout_sec if timeout_sec is not None else default_task_timeout_sec())
    submitted = _submit(
        task_type, payload, timeout, priority=priority, dedupe_key=dedupe_key)
    if not submitted:
        _bump("local_direct")
        return _local_or_deferred("failed to persist worker task")
    # 구버전 테스트/플러그인이 2-tuple _submit 스텁을 제공하는 경우도 유지한다.
    task_id, queue_fp = submitted[0], submitted[1]
    deduped = bool(submitted[2]) if len(submitted) > 2 else False
    _bump("offloaded")
    logger.info(f"offloaded {name} to worker (task={task_id})")
    res = _wait_for_result(
        task_id, queue_fp, time.time() + timeout, preserve_queued=durable)
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
    # durable 작업이라도 아직 claim되지 않았다면 원격 큐에서 회수한다. 운영 로컬
    # 폴백 뒤 워커가 복귀했을 때 같은 작업을 다시 실행하는 것을 줄인다.
    claimed_fp = _claimed_dir() / queue_fp.name
    if queue_fp.is_file() and not claimed_fp.is_file():
        try:
            queue_fp.unlink()
        except Exception:
            pass
    return _local_or_deferred(str((res or {}).get("error") or "worker task unresolved"))


# ── worker side: consume loop ─────────────────────────────────────────────────

def _load_snapshot() -> dict:
    try:
        from core.runtime_limits import process_cpu_snapshot, process_memory_snapshot
        cpu = process_cpu_snapshot()
        mem = process_memory_snapshot()
        try:
            from core.cache_event_log import peak_rss_bytes
            peak_rss_gb = peak_rss_bytes() / (1024 ** 3)
        except Exception:
            peak_rss_gb = 0.0
        return {
            "cpu_cores": cpu.get("process_cpu_cores"),
            "cpu_budget_cores": cpu.get("process_cpu_budget_cores"),
            "mem_effective_gb": mem.get("process_memory_effective_gb"),
            "mem_limit_gb": mem.get("process_memory_limit_gb"),
            "system_memory_total_gb": mem.get("system_memory_total_gb"),
            "system_memory_available_gb": mem.get("system_memory_available_gb"),
            "peak_rss_gb": round(peak_rss_gb, 3),
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
            load = _load_snapshot()
            overloaded = worker_overloaded_reason({"load": load})
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
                "load": load,
                "accepting_tasks": not bool(overloaded),
                "overloaded_reason": overloaded,
                "production_api_alive": api_alive(),
            })
        except Exception:
            logger.debug("worker heartbeat write failed", exc_info=True)
        time.sleep(heartbeat_interval_sec())


def _claim_next() -> tuple[dict, Path] | None:
    global _WORKER_NON_MAINT_CLAIMS
    try:
        paths = sorted(
            (p for p in _queue_dir().iterdir() if p.name.endswith(".task.json")),
            key=lambda p: p.name,
        )
    except Exception:
        return None
    rows = [(fp, _read_json(fp)) for fp in paths]
    maintenance = [row for row in rows if str(row[1].get("priority") or "normal") == "maintenance"]
    interactive = [row for row in rows if str(row[1].get("priority") or "normal") == "interactive"]
    normal = [row for row in rows if str(row[1].get("priority") or "normal") == "normal"]
    # 개발 worker는 운영 API가 살아 있을 때만 자동 캐시를 진행한다. API가 내려가면
    # durable 큐는 그대로 보존되고, 복구 후 이어서 처리한다. 수동 스캔(normal)은
    # 운영 장애 복구에도 필요하므로 언제든 claim할 수 있다.
    if maintenance and not api_alive():
        maintenance = []
    reserve_maintenance = bool(
        maintenance and (
            _WORKER_NON_MAINT_CLAIMS >= maintenance_every_claims() - 1
            or not (interactive or normal)
        ))
    non_maintenance = interactive + normal
    candidates = (maintenance + non_maintenance) if reserve_maintenance else (non_maintenance + maintenance)
    for fp, preloaded in candidates:
        dst = _claimed_dir() / fp.name
        try:
            os.replace(fp, dst)          # 원자적 claim — 경쟁 시 한 쪽만 성공
        except OSError:
            continue
        task = preloaded if preloaded else _read_json(dst)
        if not task.get("id") or not task.get("type"):
            try:
                dst.unlink()
            except Exception:
                pass
            continue
        if str(task.get("priority") or "normal") == "maintenance":
            _WORKER_NON_MAINT_CLAIMS = 0
        else:
            _WORKER_NON_MAINT_CLAIMS += 1
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
        elif _invalid_generated_cache_task(task):
            # 캐시 슬롯을 기다리기 전에 즉시 폐기한다. 이미 claimed로 넘어간
            # `.BUILD...` 작업도 정상 제품 뒤에서 장시간 대기하지 않는다.
            result.update(ok=False, error="invalid_staging_product")
        else:
            fn = _HANDLERS.get(task_type)
            if fn is None:
                result.update(ok=False, error=f"no_handler:{task_type}")
            else:
                logger.info(f"worker executing {label}")
                started = time.monotonic()
                try:
                    # 캐시 산출 태스크는 이 서버의 스캔 슬롯을 잡고 실행한다.
                    # 예전에는 worker slots(별도 세마포어)만 봐서, 워커가 자기
                    # 예약 스캔/빌드 스레드로 다른 제품을 빌드하는 중에도
                    # 오프로드 빌드를 같이 시작했다.
                    task_payload = dict(task.get("payload") or {})
                    with _cache_gate(
                        task_type, label,
                        product=str(task_payload.get("product") or ""),
                    ) as acquired:
                        if not acquired:
                            out = {"ok": False, "error": "cache_gate_timeout",
                                   "reason": "이 서버에서 다른 캐시 작업이 진행 중"}
                        else:
                            out = fn(task_payload)
                    payload = out if isinstance(out, dict) else {"ok": bool(out)}
                    # 핸들러가 정상 return 했더라도 payload.ok=False 면 작업 실패다.
                    # 예전에는 transport 성공으로 포장되어 api 측 run_heavy()가
                    # 로컬 폴백하지 않았고, 특히 워커에서 source path를 못 찾은
                    # ML lookup 빌드가 조용히 유실됐다.
                    if isinstance(payload, dict) and payload.get("ok") is False:
                        result.update(
                            ok=False,
                            result=payload,
                            error=str(payload.get("error") or payload.get("reason") or "handler_returned_not_ok"),
                        )
                    else:
                        result.update(ok=True, result=payload)
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
    _prune_invalid_generated_cache_tasks()
    _prune_stale_queued_cache_tasks(now=now)
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
            # 제출 시점(api heartbeat 판단)과 실제 claim 시점 사이에 메모리 상태가
            # 바뀔 수 있다. 워커 자신도 claim 직전에 다시 확인해, 직전 빌드의
            # allocator/RSS가 남아 있는 상태에서 다음 대형 작업을 겹치지 않는다.
            overload = worker_overloaded_reason({"load": _load_snapshot()})
            if overload:
                time.sleep(max(_POLL_IDLE_SEC, 1.0))
                continue
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
            _ensure_dirs()
            _write_json_atomic(_api_heartbeat_path(), {
                "owner": _owner_id(),
                "role": "api",
                "ts": time.time(),
                "pid": os.getpid(),
                "host": socket.gethostname(),
            })
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
    found_marker = marker_role()
    where = str(found_marker[1]) if found_marker else f"마커 없음 → 기본 운영 (worker 마커: {marker_path('worker')})"
    logger.info(
        f"worker dispatch: role={role} (source={source}, {where}), concurrency={worker_concurrency()}, "
        f"handlers={sorted(_HANDLERS.keys())}, root={_worker_root()}"
    )


def status() -> dict:
    """모니터/관리용 스냅샷 (routers/monitor.py, routers/admin.py 에서 노출)."""
    meta = heartbeat_meta(fresh_read=True)
    api_meta = api_heartbeat_meta(fresh_read=True)
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
    found_marker = marker_role()
    return {
        "role": role,
        "role_source": source,          # env | marker | config | default — env 면 UI 편집 불가
        "role_editable": source != "env",
        # 역할 마커 파일 — 없으면 이 서버는 운영(api)이다.
        "role_marker": str(found_marker[1]) if found_marker else "",
        "worker_marker_path": str(marker_path("worker") or ""),
        "offload_enabled": offload_enabled(),
        "external_services_enabled": external_services_enabled(),
        "worker_alive": alive,
        "production_api_alive": api_alive(fresh_read=True),
        "api_heartbeat": api_meta,
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
