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
  - 시작 시 {app_root}/server_role.json 을 {"role": "worker"} 로 고정한다.
    워치독이 도는 머신 = 개발서버라는 선언이므로, 이후 이 머신에서 flow 를
    어떻게 켜든 (원격 기동이든 수동 uvicorn 이든) 항상 워커 역할로 뜬다.
    env FLOW_SERVER_ROLE 이 설정돼 있으면 그게 항상 우선한다 (고정 생략).
  - {data_root}/worker/control/watchdog.json 에 5초 주기 heartbeat 작성
    → 관리자 탭이 "원격 기동 가능" 여부를 이걸로 판단한다.
  - {data_root}/worker/control/start_request.json 이 나타나면:
      워커 heartbeat 가 이미 신선하면 요청만 소비(중복 기동 방지),
      아니면 uvicorn app:app 를 FLOW_SERVER_ROLE=worker 로 spawn.
    소비한 요청 파일은 삭제한다.
  - 자기가 띄운 자식 프로세스의 생존을 watchdog.json 에 함께 기록한다.
    (자동 재시작은 하지 않는다 — 기동은 항상 관리자의 명시 요청으로만.)

환경변수 / 인자:
  FLOW_DATA_ROOT     공유 flow-data 루트 (운영서버와 같은 값, 필수에 준함)
  FLOW_APP_ROOT      flow 체크아웃 루트 (기본: 이 스크립트의 상위 디렉터리)
  --port / FLOW_WORKER_PORT   워커 uvicorn 포트 (기본 8081)
  --python           워커를 띄울 파이썬 (기본: 이 워치독의 sys.executable)
"""
from __future__ import annotations

import argparse
import json
import os
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
    return bool(ts and (time.time() - ts) < HEARTBEAT_STALE_SEC)


def _spawn_worker(port: int, python_exe: str) -> subprocess.Popen | None:
    env = dict(os.environ)
    env["FLOW_SERVER_ROLE"] = "worker"
    cmd = [python_exe, "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(port)]
    log_fp = _control_dir() / "worker_uvicorn.log"
    try:
        log = open(log_fp, "ab")
        proc = subprocess.Popen(
            cmd, cwd=str(APP_ROOT), env=env,
            stdout=log, stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"),
        )
        print(f"[watchdog] spawned worker pid={proc.pid} port={port} (log: {log_fp})", flush=True)
        return proc
    except Exception as exc:
        print(f"[watchdog] spawn failed: {exc}", flush=True)
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("FLOW_WORKER_PORT") or 8081))
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    print(f"[watchdog] app_root={APP_ROOT}", flush=True)
    print(f"[watchdog] data_root={_data_root()}", flush=True)

    # 이 머신을 개발서버(워커)로 영구 고정 — 수동 uvicorn 기동도 워커로 뜬다.
    env_role = os.environ.get("FLOW_SERVER_ROLE", "").strip().lower()
    if env_role:
        print(f"[watchdog] role pin skipped — FLOW_SERVER_ROLE={env_role!r} takes precedence", flush=True)
    else:
        role_fp = APP_ROOT / "server_role.json"
        if _read_json(role_fp).get("role") != "worker":
            _write_json_atomic(role_fp, {"role": "worker", "updated_at": time.time(),
                                         "pinned_by": "worker_watchdog"})
            print(f"[watchdog] pinned server role to 'worker' ({role_fp})", flush=True)
        else:
            print(f"[watchdog] server role already pinned to 'worker' ({role_fp})", flush=True)

    child: subprocess.Popen | None = None

    while True:
        try:
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
            })

            req_fp = _control_dir() / "start_request.json"
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
                    print(f"[watchdog] start request from {req.get('requested_by')!r} — spawning worker", flush=True)
                    child = _spawn_worker(args.port, args.python)
        except KeyboardInterrupt:
            print("[watchdog] stopped", flush=True)
            return 0
        except Exception as exc:
            print(f"[watchdog] loop error: {exc}", flush=True)
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    raise SystemExit(main())
