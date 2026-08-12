#!/usr/bin/env python3
"""scripts/worker_watchdog.py — 개발서버 상주 워치독 (원격 워커 기동).

개발서버 머신에 이 스크립트를 상시 띄워 두면, 운영서버 관리자 탭
(모니터 → 워커 서버)의 "개발서버 켜기" 버튼이 shared workspace 에 남긴
start_request 를 소비해 flow 워커(uvicorn, FLOW_SERVER_ROLE=worker)를
띄운다. flow 앱 프로세스가 죽어 있어도 이 워치독만 살아 있으면 원격
기동이 가능하다.

외부 의존성 없음 (stdlib only). systemd / Windows 작업 스케줄러 /
nohup 등으로 부팅 시 자동 시작해 두는 것을 권장:

    FLOW_DATA_ROOT=/config/work/sharedworkspace/flow-data \
        python scripts/worker_watchdog.py --port 8081

동작:
  - 시작 시 {data_root}/worker/roles/<hostname>/worker.marker 와 hostname별
    역할 JSON을 만든다. 워치독이 도는 머신 = 개발서버라는 선언이므로,
    이후 이 머신에서 flow 를 어떻게 켜든 (원격 기동이든 수동 uvicorn 이든) 항상
    워커 역할로 뜬다. 앱은 마커 파일을 1순위로 보고 server_role.json 만 있는
    'worker' 는 무시하므로(운영서버가 재시작 후 조용히 워커로 뜨는 것 방지),
    이 마커가 개발서버 지정의 실제 근거다.
    env FLOW_SERVER_ROLE 이 설정돼 있으면 그게 항상 우선한다 (고정 생략).
  - {data_root}/worker/control/watchdog.json 에 5초 주기 heartbeat 작성
    → 관리자 탭이 "원격 기동 가능" 여부를 이걸로 판단한다.
  - {data_root}/worker/control/start_request.json 이 나타나면:
      워커 heartbeat 가 이미 신선하면 요청만 소비(중복 기동 방지),
      아니면 uvicorn app:app 를 FLOW_SERVER_ROLE=worker 로 spawn.
    소비한 요청 파일은 삭제한다.
  - 자기가 띄운 자식 프로세스의 생존을 watchdog.json 에 함께 기록한다.
  - --keep-alive 모드에서는 uvicorn 을 즉시 시작하고, OOM/비정상/정상 종료를
    가리지 않고 다시 기동한다. 짧은 시간에 반복 종료되면 지수 백오프로 재시작
    폭주를 막는다. tmux에서는 scripts/run_dev_worker_tmux.sh 사용을 권장한다.

환경변수 / 인자:
  FLOW_DATA_ROOT     공유 flow-data 루트 (운영서버와 같은 값, 필수에 준함)
  FLOW_APP_ROOT      flow 체크아웃 루트 (기본: 이 스크립트의 상위 디렉터리)
  --port / FLOW_WORKER_PORT   워커 uvicorn 포트 (기본 8081)
  --python           워커를 띄울 파이썬 (기본: 이 워치독의 sys.executable)
  --keep-alive       uvicorn 이 없으면 즉시 시작하고 종료 시 계속 재시작
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

# Windows cp949 콘솔에서 em-dash 등 non-ASCII print 가 터지는 것 방지 (setup.py 와 동일 패턴).
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

APP_ROOT = Path(os.environ.get("FLOW_APP_ROOT") or Path(__file__).resolve().parent.parent)
HEARTBEAT_STALE_SEC = 45.0   # core/worker_dispatch.py stale_sec 기본값과 동일
INTERVAL_SEC = 5.0
HEALTHY_RUNTIME_SEC = 60.0
LOG_MAX_BYTES = 20 * 1024 * 1024
_INSTANCE_LOCK_HANDLE = None


def _data_root() -> Path:
    raw = os.environ.get("FLOW_DATA_ROOT", "").strip()
    if raw:
        return Path(raw)
    # 앱과 같은 로컬 기본값 (개발 PC 단독 테스트용)
    return APP_ROOT / "data" / "flow-data"


def _control_dir() -> Path:
    d = _data_root() / "worker" / "control"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _host_token() -> str:
    return re.sub(r"[^a-z0-9._-]", "_", socket.gethostname().strip().lower()) or "unknown"


def _host_role_marker() -> Path:
    return _data_root() / "worker" / "roles" / _host_token() / "worker.marker"


def _host_role_config() -> Path:
    return _data_root() / "worker" / "roles" / f"{_host_token()}.json"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_json_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def _worker_heartbeat_fresh() -> bool:
    meta = _read_json(_data_root() / "worker" / "heartbeat.json")
    ts = float(meta.get("ts") or 0.0)
    # 같은 개발 머신의 예전 heartbeat라면 PID 생존도 확인한다. watchdog까지
    # 재시작된 상황에서 OOM으로 죽은 uvicorn의 45초 stale 만료를 기다리지 않는다.
    if str(meta.get("host") or "").casefold() == socket.gethostname().casefold():
        try:
            pid = int(meta.get("pid") or 0)
            if pid > 0:
                os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        except Exception:
            pass
    return bool(ts and (time.time() - ts) < HEARTBEAT_STALE_SEC)


def _acquire_instance_lock() -> bool:
    """Prevent two tmux/watchdog processes from spawning duplicate uvicorn workers."""
    global _INSTANCE_LOCK_HANDLE
    lock_fp = _control_dir() / f"worker_watchdog.{_host_token()}.lock"
    try:
        handle = open(lock_fp, "a+", encoding="utf-8")
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            if not handle.read(1):
                handle.write("0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        _INSTANCE_LOCK_HANDLE = handle
        return True
    except Exception as exc:
        try:
            handle.close()
        except Exception:
            pass
        print(f"[watchdog] another supervisor is already running or lock failed ({lock_fp}): {exc}", flush=True)
        return False


def _rotate_worker_log(log_fp: Path) -> None:
    try:
        if log_fp.is_file() and log_fp.stat().st_size >= LOG_MAX_BYTES:
            rotated = log_fp.with_suffix(log_fp.suffix + ".1")
            rotated.unlink(missing_ok=True)
            os.replace(log_fp, rotated)
    except Exception as exc:
        print(f"[watchdog] log rotation skipped: {exc}", flush=True)


def _spawn_worker(port: int, python_exe: str) -> subprocess.Popen | None:
    env = dict(os.environ)
    env["FLOW_SERVER_ROLE"] = "worker"
    cmd = [python_exe, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]
    log_fp = _control_dir() / "worker_uvicorn.log"
    log = None
    try:
        _rotate_worker_log(log_fp)
        log = open(log_fp, "ab")
        proc = subprocess.Popen(
            cmd, cwd=str(APP_ROOT), env=env,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
        log.close()
        print(f"[watchdog] spawned worker pid={proc.pid} port={port} (log: {log_fp})", flush=True)
        return proc
    except Exception as exc:
        try:
            if log is not None:
                log.close()
        except Exception:
            pass
        print(f"[watchdog] spawn failed: {exc}", flush=True)
        return None


def _exit_reason(returncode: int | None) -> str:
    if returncode in (-9, 9, 137):
        return "OOM/SIGKILL 의심"
    if returncode == 0:
        return "정상 종료"
    return f"비정상 종료(code={returncode})"


def _restart_delay(failures: int, base_sec: float, max_sec: float) -> float:
    return min(max_sec, base_sec * (2 ** max(0, min(10, failures - 1))))


def _stop_child(child: subprocess.Popen | None) -> None:
    if child is None or child.poll() is not None:
        return
    try:
        child.terminate()
        child.wait(timeout=15)
    except Exception:
        try:
            child.kill()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("FLOW_WORKER_PORT") or 8081))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--keep-alive", action="store_true",
                    default=str(os.environ.get("FLOW_WORKER_KEEP_ALIVE") or "").lower() in {"1", "true", "yes", "on"})
    ap.add_argument("--restart-delay", type=float,
                    default=float(os.environ.get("FLOW_WORKER_RESTART_DELAY_SEC") or 5.0))
    ap.add_argument("--max-restart-delay", type=float,
                    default=float(os.environ.get("FLOW_WORKER_MAX_RESTART_DELAY_SEC") or 120.0))
    args = ap.parse_args()

    if not _acquire_instance_lock():
        return 2

    print(f"[watchdog] app_root={APP_ROOT}", flush=True)
    print(f"[watchdog] data_root={_data_root()}", flush=True)
    print(f"[watchdog] keep_alive={args.keep_alive} port={args.port}", flush=True)

    # 이 머신을 개발서버(워커)로 영구 고정 — 수동 uvicorn 기동도 워커로 뜬다.
    env_role = os.environ.get("FLOW_SERVER_ROLE", "").strip().lower()
    if env_role:
        print(f"[watchdog] role pin skipped — FLOW_SERVER_ROLE={env_role!r} takes precedence", flush=True)
    else:
        # 역할 파일은 소스 트리 밖 hostname별 data_root 에만 둔다. 체크아웃을
        # Git/설치 번들로 복제해도 운영 머신으로 worker 역할이 따라가지 않는다.
        marker_fp = _host_role_marker()
        if marker_fp.is_file():
            print(f"[watchdog] dev worker marker present ({marker_fp})", flush=True)
        else:
            try:
                marker_fp.parent.mkdir(parents=True, exist_ok=True)
                marker_fp.write_text(
                    "# flow 서버 역할 마커 — 이 파일이 있으면 이 서버는 'worker' 역할로 뜬다.\n"
                    "# 파일을 지우면 다음 기동부터 운영(api) 서버가 된다. 내용은 읽지 않는다.\n"
                    "# created by scripts/worker_watchdog.py\n", encoding="utf-8")
                print(f"[watchdog] created dev worker marker ({marker_fp})", flush=True)
            except Exception as exc:
                print(f"[watchdog] WARNING: marker 생성 실패 ({marker_fp}): {exc} — "
                      f"이 머신은 FLOW_SERVER_ROLE=worker 로 띄워야 워커가 된다", flush=True)
        role_fp = _host_role_config()
        if _read_json(role_fp).get("role") != "worker":
            role_fp.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomic(role_fp, {"role": "worker", "updated_at": time.time(),
                                         "pinned_by": "worker_watchdog"})
            print(f"[watchdog] pinned server role to 'worker' ({role_fp})", flush=True)
        else:
            print(f"[watchdog] server role already pinned to 'worker' ({role_fp})", flush=True)

    child: subprocess.Popen | None = None
    child_started_at = 0.0
    consecutive_failures = 0
    next_restart_at = 0.0
    owned_child_exited = False

    def _request_shutdown(_signum, _frame):
        raise KeyboardInterrupt

    for _signal_name in ("SIGTERM", "SIGHUP"):
        _signal_value = getattr(signal, _signal_name, None)
        if _signal_value is not None:
            try:
                signal.signal(_signal_value, _request_shutdown)
            except Exception:
                pass

    while True:
        try:
            now = time.time()
            if child is not None and child.poll() is not None:
                returncode = child.returncode
                runtime = max(0.0, now - child_started_at)
                consecutive_failures = 1 if runtime >= HEALTHY_RUNTIME_SEC else consecutive_failures + 1
                delay = _restart_delay(
                    consecutive_failures,
                    max(1.0, args.restart_delay),
                    max(max(1.0, args.restart_delay), args.max_restart_delay),
                )
                next_restart_at = now + delay
                owned_child_exited = True
                print(
                    f"[watchdog] worker pid={child.pid} {_exit_reason(returncode)} "
                    f"after {runtime:.1f}s — restart in {delay:.1f}s "
                    f"(failure #{consecutive_failures})",
                    flush=True,
                )
                child = None

            child_running = bool(child and child.poll() is None)
            _write_json_atomic(_control_dir() / "watchdog.json", {
                "ts": time.time(),
                "host": socket.gethostname(),
                "pid": os.getpid(),
                "app_root": str(APP_ROOT),
                "port": args.port,
                "child_pid": child.pid if child_running else None,
                "child_running": child_running,
                "worker_heartbeat_fresh": _worker_heartbeat_fresh(),
                "keep_alive": bool(args.keep_alive),
                "consecutive_failures": consecutive_failures,
                "next_restart_at": next_restart_at or None,
            })

            req_fp = _control_dir() / "start_request.json"
            start_requested = False
            if req_fp.is_file():
                req = _read_json(req_fp)
                try:
                    req_fp.unlink()   # 소비 — 성공/실패와 무관하게 재시도는 새 요청으로
                except OSError:
                    pass
                if _worker_heartbeat_fresh():
                    print(f"[watchdog] start request from {req.get('requested_by')!r} ignored — worker already alive", flush=True)
                elif child_running:
                    print("[watchdog] start request ignored — child still starting up", flush=True)
                else:
                    print(f"[watchdog] start request from {req.get('requested_by')!r} — scheduling worker", flush=True)
                    start_requested = True
                    next_restart_at = 0.0

            heartbeat_fresh = _worker_heartbeat_fresh()
            should_start = bool(
                not child_running
                and (start_requested or args.keep_alive)
                and (owned_child_exited or not heartbeat_fresh)
                and time.time() >= next_restart_at
            )
            if should_start:
                child = _spawn_worker(args.port, args.python)
                if child is not None:
                    child_started_at = time.time()
                    owned_child_exited = False
                else:
                    consecutive_failures += 1
                    delay = _restart_delay(
                        consecutive_failures,
                        max(1.0, args.restart_delay),
                        max(max(1.0, args.restart_delay), args.max_restart_delay),
                    )
                    next_restart_at = time.time() + delay
                    owned_child_exited = True
            time.sleep(INTERVAL_SEC)
        except KeyboardInterrupt:
            print("[watchdog] stopped", flush=True)
            _stop_child(child)
            return 0
        except Exception as exc:
            print(f"[watchdog] loop error: {exc}", flush=True)
            time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
