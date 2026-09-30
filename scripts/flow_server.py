#!/usr/bin/env python3
"""scripts/flow_server.py — Flow 서버 상시 기동 감시기 (uvicorn 을 직접 치지 않는다).

bash(Git Bash 포함)에서 한 줄로 띄운다:

    python scripts/flow_server.py                 # 0.0.0.0:8080
    python scripts/flow_server.py --port 8090     # 포트 변경
    python scripts/flow_server.py --status        # 감시기·서버 상태만 출력
    python scripts/flow_server.py --stop          # 서버와 감시기를 끈다(재시작 안 함)
    python scripts/flow_server.py --restart       # 서버만 다시 띄운다(배포 뒤 등)

하는 일:
  - `python -m uvicorn app:app` 를 자식 프로세스로 띄우고, 죽으면(정상·비정상·OOM 무관)
    다시 띄운다. 60초 안에 연달아 죽으면 5s→10s→20s…(최대 2분) 로 간격을 늘려 폭주를 막는다.
  - 멈춤 감시: 기동 유예(기본 180초) 뒤 15초마다 `/health` 를 부른다. 4번 연속 응답이 없으면
    프로세스는 살아 있어도 멈춘 것으로 보고 재시작한다.
  - 메모리 감시(psutil 이 있으면): 서버와 그 자식(ET 계산 등) 합계가 한도(기본 호스트 총량의
    90%)를 1분 넘게 넘으면 재시작한다. 앱 안의 메모리 워치독이 먼저 캐시를 비우므로 이것은
    최후 수단이다 — Windows 는 메모리가 차면 죽는 대신 디스크 페이징으로 전체가 느려진다.
  - 로그: `<로그 폴더>/uvicorn.log`(20MB × 5개 순환) + 화면 출력, 재시작 이력
    `flow_restarts.log`, 현재 상태 `flow_supervisor.json`.
  - 앱 폴더에 `.flow_stop` 이 생기면 서버를 끄고 감시기도 끝낸다(예전 flow_run.bat 과 같은 규약).
    `.flow_restart` 가 생기면 서버만 다시 띄운다.
  - 같은 포트로 감시기를 두 번 띄우면 두 번째는 바로 끝난다.

외부 의존성 없음(stdlib). psutil 이 있으면 메모리 감시와 자식 프로세스 정리가 정확해진다.

환경변수(인자로도 지정 가능):
  FLOW_HOST / FLOW_PORT                     기본 0.0.0.0 / 8080
  FLOW_LOG_DIR                              로그 폴더 (기본 FLOW_DATA_ROOT/logs)
  FLOW_SUPERVISOR_HEALTH_GRACE_SEC          기동 뒤 헬스체크 유예 (기본 180)
  FLOW_SUPERVISOR_HEALTH_FAILS              연속 실패 몇 번에 재시작 (기본 4, 0=끔)
  FLOW_SUPERVISOR_MAX_MEMORY_GB             메모리 재시작 한도 (기본 총량×0.90, 0=끔)
  FLOW_SUPERVISOR_RESTART_DELAY_SEC         기본 재시작 간격 (기본 5)
  FLOW_SUPERVISOR_MAX_RESTART_DELAY_SEC     최대 재시작 간격 (기본 120)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:  # 선택 의존성
    import psutil  # type: ignore
except Exception:  # pragma: no cover - psutil 없는 설치
    psutil = None

IS_WINDOWS = os.name == "nt"
APP_ROOT = Path(os.environ.get("FLOW_APP_ROOT") or Path(__file__).resolve().parent.parent)
STOP_FILE = APP_ROOT / ".flow_stop"
RESTART_FILE = APP_ROOT / ".flow_restart"
HEALTHY_RUNTIME_SEC = 60.0
LOOP_SEC = 1.0
HEALTH_INTERVAL_SEC = 15.0
MEMORY_INTERVAL_SEC = 20.0
MEMORY_OVER_CHECKS = 3          # 20초 × 3 = 1분 연속 초과면 재시작
LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_BACKUPS = 5
STOP_GRACE_SEC = 25.0
_LOCK_HANDLE = None


# ── 설정 ──────────────────────────────────────────────────────────────
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    try:
        return float(raw) if str(raw).strip() else default
    except ValueError:
        return default


def _default_log_dir() -> Path:
    raw = os.environ.get("FLOW_LOG_DIR", "").strip()
    if raw:
        return Path(raw)
    data_root = os.environ.get("FLOW_DATA_ROOT", "").strip()
    if data_root:
        return Path(data_root) / "logs"
    if IS_WINDOWS:
        storage = Path(os.environ.get("FLOW_STORAGE_ROOT", "D:\\") or "D:\\")
        if (storage / "flow-data").is_dir():
            return storage / "flow-data" / "logs"
    return APP_ROOT / "data" / "flow-data" / "logs"


def _default_max_memory_gb() -> float:
    if psutil is None:
        return 0.0
    try:
        return round(psutil.virtual_memory().total / (1024 ** 3) * 0.90, 1)
    except Exception:
        return 0.0


def _now() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── 로그 ──────────────────────────────────────────────────────────────
class RotatingLog:
    """자식 출력과 감시기 메시지를 한 파일에 쓰고 크기로 순환한다(스레드 안전)."""

    def __init__(self, path: Path, echo: bool):
        self.path = path
        self.echo = echo
        self._lock = threading.Lock()
        self._fh = None
        # 화면 출력은 별도 스레드가 맡는다. 콘솔 창에서 글자를 드래그(빠른 편집)하거나
        # 창이 멈추면 sys.stdout.write 가 풀릴 때까지 막히는데, 그 호출이 uvicorn
        # 출력 펌프나 감시 루프 안에 있으면 파이프가 차서 서버 요청까지 멈춘다.
        # 파일 기록이 정본이고 화면 줄은 밀리면 버린다.
        self._echo_q: "queue.Queue[bytes]" = queue.Queue(maxsize=2000)
        self._echo_dropped = 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self._open()
        if echo:
            threading.Thread(target=self._echo_loop, name="flow-log-echo", daemon=True).start()

    def _echo_loop(self) -> None:
        while True:
            data = self._echo_q.get()
            try:
                if self._echo_dropped:
                    dropped, self._echo_dropped = self._echo_dropped, 0
                    sys.stdout.write(f"[flow-server] 화면 출력 {dropped}줄 생략(파일 로그에는 모두 있음)\n")
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
            except Exception:
                pass

    def _open(self) -> None:
        self._fh = open(self.path, "ab")

    def _rotate_locked(self) -> None:
        try:
            if self._fh is not None:
                self._fh.close()
            for i in range(LOG_BACKUPS - 1, 0, -1):
                src = self.path.with_name(f"{self.path.name}.{i}")
                if src.exists():
                    os.replace(src, self.path.with_name(f"{self.path.name}.{i + 1}"))
            if self.path.exists():
                os.replace(self.path, self.path.with_name(f"{self.path.name}.1"))
        except Exception as exc:
            sys.stderr.write(f"[flow-server] log rotation skipped: {exc}\n")
        self._open()

    def write(self, data: bytes) -> None:
        with self._lock:
            try:
                self._fh.write(data)
                self._fh.flush()
                if self._fh.tell() >= LOG_MAX_BYTES:
                    self._rotate_locked()
            except Exception:
                pass
        if self.echo:
            try:
                self._echo_q.put_nowait(data)
            except queue.Full:
                self._echo_dropped += 1

    def line(self, text: str) -> None:
        self.write(f"[{_now()}] [flow-server] {text}\n".encode("utf-8"))


def _append_history(log_dir: Path, text: str) -> None:
    try:
        with open(log_dir / "flow_restarts.log", "a", encoding="utf-8") as fh:
            fh.write(f"[{_now()}] {text}\n")
    except Exception:
        pass


def _write_state(log_dir: Path, payload: dict) -> None:
    fp = log_dir / "flow_supervisor.json"
    tmp = fp.with_suffix(f".json.tmp.{os.getpid()}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, fp)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass


def _read_state(log_dir: Path) -> dict:
    try:
        data = json.loads((log_dir / "flow_supervisor.json").read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ── 중복 실행 방지 ────────────────────────────────────────────────────
def _acquire_lock(log_dir: Path, port: int) -> bool:
    global _LOCK_HANDLE
    fp = log_dir / f"flow_supervisor.{port}.lock"
    handle = None
    try:
        handle = open(fp, "a+", encoding="utf-8")
        if IS_WINDOWS:
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
        _LOCK_HANDLE = handle
        return True
    except Exception:
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass
        return False


# ── 자식 프로세스 ─────────────────────────────────────────────────────
def _child_env() -> dict:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONUNBUFFERED"] = "1"
    env["FLOW_SUPERVISED"] = "1"   # 앱이 감시기 아래에서 도는지 알 수 있게
    return env


def _spawn(args, log: RotatingLog) -> subprocess.Popen:
    cmd = [args.python, "-m", "uvicorn", "app:app",
           "--host", args.host, "--port", str(args.port),
           "--timeout-keep-alive", "30"]
    # 요청마다 한 줄씩 이벤트 루프가 파이프로 동기 출력한다. 로그 파일 쓰기가 밀리면
    # (백신 검사·디스크 지연) 파이프가 차서 전체 요청이 멈춘다. 요청 기록은 sysmon 이 따로 남긴다.
    if str(os.environ.get("FLOW_UVICORN_ACCESS_LOG", "")).strip().lower() not in {"1", "true", "yes", "on"}:
        cmd.append("--no-access-log")
    cmd += list(args.uvicorn_args)
    kwargs: dict = {}
    if IS_WINDOWS:
        # 자식은 따로 콘솔 그룹 — Ctrl-C 는 감시기가 받아 CTRL_BREAK 로 정리해서 넘긴다.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, cwd=str(APP_ROOT), env=_child_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, **kwargs)

    def _pump():
        try:
            for chunk in iter(proc.stdout.readline, b""):
                log.write(chunk)
        except Exception:
            pass

    threading.Thread(target=_pump, name="flow-server-log", daemon=True).start()
    log.line(f"started uvicorn pid={proc.pid} {args.host}:{args.port} (app={APP_ROOT})")
    return proc


def _tree_pids(pid: int) -> list[int]:
    if psutil is None:
        return [pid]
    try:
        parent = psutil.Process(pid)
        return [pid] + [c.pid for c in parent.children(recursive=True)]
    except Exception:
        return [pid]


def _tree_rss_gb(pid: int) -> float:
    if psutil is None:
        return 0.0
    total = 0
    for p in _tree_pids(pid):
        try:
            total += psutil.Process(p).memory_info().rss
        except Exception:
            pass
    return total / (1024 ** 3)


def _stop_child(proc: subprocess.Popen | None, log: RotatingLog, reason: str) -> None:
    """정상 종료 신호 → 유예 → 프로세스 트리 강제 종료."""
    if proc is None or proc.poll() is not None:
        return
    log.line(f"stopping uvicorn pid={proc.pid} ({reason})")
    pids = _tree_pids(proc.pid)
    try:
        if IS_WINDOWS:
            os.kill(proc.pid, signal.CTRL_BREAK_EVENT)   # uvicorn 은 SIGBREAK 를 종료로 처리
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass
    try:
        proc.wait(timeout=STOP_GRACE_SEC)
    except Exception:
        pass
    # 남은 자식(ET 계산 등)까지 정리한다.
    leftovers = [p for p in pids if p != proc.pid] + ([proc.pid] if proc.poll() is None else [])
    for pid in leftovers:
        try:
            if psutil is not None:
                psutil.Process(pid).kill()
            elif IS_WINDOWS:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def _health_ok(host: str, port: int, timeout: float = 10.0) -> bool:
    target = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
    try:
        with urllib.request.urlopen(f"http://{target}:{port}/health", timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def _exit_reason(code: int | None) -> str:
    if code == 0:
        return "정상 종료"
    if code in (-9, 9, 137):
        return "강제 종료(OOM 의심)"
    if IS_WINDOWS and code in (3221225477, -1073741819):
        return "접근 위반(0xC0000005)"
    return f"비정상 종료(code={code})"


def _restart_delay(failures: int, base: float, cap: float) -> float:
    return min(cap, base * (2 ** max(0, min(10, failures - 1))))


# ── 명령: status / stop / restart ────────────────────────────────────
def _cmd_status(args, log_dir: Path) -> int:
    state = _read_state(log_dir)
    alive = False
    sup_pid = int(state.get("pid") or 0)
    if sup_pid and psutil is not None:
        alive = psutil.pid_exists(sup_pid)
    print(json.dumps({
        "supervisor": state or None,
        "supervisor_alive": alive if psutil is not None else "unknown(psutil 없음)",
        "health_ok": _health_ok(args.host, args.port, timeout=5.0),
        "log_dir": str(log_dir),
    }, ensure_ascii=False, indent=1))
    return 0


def _touch(fp: Path, text: str) -> None:
    fp.write_text(f"{text} {_now()}\n", encoding="utf-8")


# ── 감시 루프 ─────────────────────────────────────────────────────────
def _disable_console_quick_edit() -> str:
    """Windows 콘솔의 '빠른 편집'을 끈다.

    켜져 있으면 창 안을 한 번 클릭하는 것만으로 콘솔 출력이 멈추고, 그동안 출력하는
    프로세스가 통째로 대기한다(Miniforge Prompt·cmd 창에서 서버를 띄울 때 흔한 '서버가
    멈췄다' 원인). 콘솔이 없거나 실패하면 조용히 넘어간다."""
    if not IS_WINDOWS:
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if not handle or not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return ""
        enable_quick_edit, enable_extended_flags = 0x0040, 0x0080
        if not mode.value & enable_quick_edit:
            return ""
        new_mode = (mode.value & ~enable_quick_edit) | enable_extended_flags
        if kernel32.SetConsoleMode(handle, new_mode):
            return "console quick-edit disabled (창 클릭으로 서버가 멈추지 않게)"
    except Exception:
        pass
    return ""


def run(args, log_dir: Path) -> int:
    if not _acquire_lock(log_dir, args.port):
        print(f"[flow-server] 포트 {args.port} 감시기가 이미 실행 중입니다 "
              f"(python scripts/flow_server.py --status 로 확인).", flush=True)
        return 2
    if not (APP_ROOT / "app.py").is_file():
        print(f"[flow-server] app.py 가 없습니다: {APP_ROOT}", flush=True)
        return 2
    for stale in (STOP_FILE, RESTART_FILE):
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass

    quick_edit_note = _disable_console_quick_edit()
    log = RotatingLog(log_dir / "uvicorn.log", echo=not args.quiet)
    log.line(f"supervisor pid={os.getpid()} python={args.python} log_dir={log_dir}")
    if quick_edit_note:
        log.line(quick_edit_note)
    log.line(f"health grace={args.health_grace:.0f}s fails={args.health_fails} "
             f"memory_limit={args.max_memory_gb or 'off'}GB psutil={'yes' if psutil else 'no'}")

    stop_event = threading.Event()

    def _on_signal(_signum, _frame):
        stop_event.set()

    for name in ("SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _on_signal)
            except Exception:
                pass

    proc: subprocess.Popen | None = None
    started_at = 0.0
    failures = 0
    restarts = 0
    next_start_at = 0.0
    health_fails = 0
    healthy_seen = False
    last_health = 0.0
    last_memory = 0.0
    memory_over = 0
    last_reason = ""
    last_state = 0.0

    def _state(status: str) -> None:
        _write_state(log_dir, {
            "ts": time.time(), "updated_at": _now(), "status": status,
            "pid": os.getpid(), "child_pid": proc.pid if proc and proc.poll() is None else None,
            "host": args.host, "port": args.port, "app_root": str(APP_ROOT),
            "restarts": restarts, "consecutive_failures": failures,
            "last_restart_reason": last_reason, "healthy": healthy_seen and health_fails == 0,
            "child_started_at": started_at or None,
        })

    def _restart(reason: str) -> None:
        nonlocal proc, next_start_at, last_reason
        last_reason = reason
        log.line(f"restart: {reason}")
        _append_history(log_dir, f"restart port={args.port} reason={reason}")
        _stop_child(proc, log, reason)
        proc = None
        next_start_at = time.time() + max(1.0, args.restart_delay)

    try:
        while not stop_event.is_set():
            now = time.time()

            if STOP_FILE.exists():
                log.line(".flow_stop 발견 — 서버를 끄고 감시기를 종료합니다")
                _append_history(log_dir, f"stop requested port={args.port}")
                try:
                    STOP_FILE.unlink()
                except Exception:
                    pass
                break

            if RESTART_FILE.exists():
                try:
                    RESTART_FILE.unlink()
                except Exception:
                    pass
                failures = 0
                _restart("재시작 요청(.flow_restart)")
                restarts += 1

            # 자식 종료 감지
            if proc is not None and proc.poll() is not None:
                code = proc.returncode
                runtime = max(0.0, now - started_at)
                failures = 1 if runtime >= HEALTHY_RUNTIME_SEC else failures + 1
                delay = _restart_delay(failures, max(1.0, args.restart_delay),
                                       max(args.restart_delay, args.max_restart_delay))
                last_reason = f"{_exit_reason(code)} after {runtime:.0f}s"
                log.line(f"uvicorn pid={proc.pid} {last_reason} — {delay:.0f}초 뒤 재시작 (연속 {failures}회)")
                _append_history(log_dir, f"exit port={args.port} code={code} runtime={runtime:.0f}s next={delay:.0f}s")
                proc = None
                next_start_at = now + delay
                restarts += 1

            # 기동
            if proc is None and now >= next_start_at:
                try:
                    proc = _spawn(args, log)
                    started_at = time.time()
                    health_fails = 0
                    healthy_seen = False
                    memory_over = 0
                    last_health = started_at
                    _append_history(log_dir, f"start port={args.port} pid={proc.pid}")
                except Exception as exc:
                    failures += 1
                    delay = _restart_delay(failures, max(1.0, args.restart_delay),
                                           max(args.restart_delay, args.max_restart_delay))
                    last_reason = f"기동 실패: {exc}"
                    log.line(f"{last_reason} — {delay:.0f}초 뒤 재시도")
                    next_start_at = now + delay
                    proc = None

            running = proc is not None and proc.poll() is None

            # 멈춤 감시(/health)
            if running and args.health_fails > 0 and now - last_health >= HEALTH_INTERVAL_SEC:
                last_health = now
                if _health_ok(args.host, args.port):
                    if not healthy_seen:
                        log.line(f"health ok — 기동 완료 ({now - started_at:.0f}s)")
                    healthy_seen = True
                    health_fails = 0
                    if now - started_at >= HEALTHY_RUNTIME_SEC:
                        failures = 0
                elif healthy_seen or now - started_at >= args.health_grace:
                    health_fails += 1
                    log.line(f"health 응답 없음 ({health_fails}/{args.health_fails})")
                    if health_fails >= args.health_fails:
                        _restart(f"/health {health_fails}회 연속 무응답(멈춤)")
                        restarts += 1
                        running = False

            # 메모리 감시
            if running and args.max_memory_gb > 0 and psutil is not None and now - last_memory >= MEMORY_INTERVAL_SEC:
                last_memory = now
                used = _tree_rss_gb(proc.pid)
                if used >= args.max_memory_gb:
                    memory_over += 1
                    log.line(f"메모리 {used:.1f}GB ≥ 한도 {args.max_memory_gb:.1f}GB ({memory_over}/{MEMORY_OVER_CHECKS})")
                    if memory_over >= MEMORY_OVER_CHECKS:
                        _restart(f"메모리 {used:.1f}GB 가 한도 {args.max_memory_gb:.1f}GB 를 1분 넘게 초과")
                        restarts += 1
                else:
                    memory_over = 0

            if now - last_state >= 5.0:
                last_state = now
                _state("running" if proc is not None and proc.poll() is None else "waiting")
            stop_event.wait(LOOP_SEC)
    finally:
        _stop_child(proc, log, "감시기 종료")
        last_reason = last_reason or "stopped"
        _state("stopped")
        log.line("supervisor stopped")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Flow 서버 상시 기동 감시기 — 죽거나 멈추면 다시 띄운다.",
        epilog="uvicorn 에 넘길 추가 인자는 -- 뒤에 적는다. 예: python scripts/flow_server.py -- --log-level warning",
    )
    ap.add_argument("--host", default=os.environ.get("FLOW_HOST") or "0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("FLOW_PORT") or 8080))
    ap.add_argument("--python", default=os.environ.get("FLOW_PYTHON") or sys.executable,
                    help="서버를 띄울 파이썬 (기본: 이 스크립트를 실행한 파이썬)")
    ap.add_argument("--log-dir", default="", help="로그 폴더 (기본 FLOW_DATA_ROOT/logs)")
    ap.add_argument("--health-grace", type=float, default=_env_float("FLOW_SUPERVISOR_HEALTH_GRACE_SEC", 180.0))
    ap.add_argument("--health-fails", type=int, default=int(_env_float("FLOW_SUPERVISOR_HEALTH_FAILS", 4)))
    ap.add_argument("--max-memory-gb", type=float,
                    default=_env_float("FLOW_SUPERVISOR_MAX_MEMORY_GB", _default_max_memory_gb()))
    ap.add_argument("--restart-delay", type=float, default=_env_float("FLOW_SUPERVISOR_RESTART_DELAY_SEC", 5.0))
    ap.add_argument("--max-restart-delay", type=float,
                    default=_env_float("FLOW_SUPERVISOR_MAX_RESTART_DELAY_SEC", 120.0))
    ap.add_argument("--quiet", action="store_true", help="화면에는 출력하지 않고 로그 파일에만 쓴다")
    ap.add_argument("--status", action="store_true", help="상태만 출력")
    ap.add_argument("--stop", action="store_true", help="실행 중인 감시기와 서버를 끈다")
    ap.add_argument("--restart", action="store_true", help="실행 중인 감시기에게 서버 재시작을 요청한다")
    ap.add_argument("uvicorn_args", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    if args.uvicorn_args and args.uvicorn_args[0] == "--":
        args.uvicorn_args = args.uvicorn_args[1:]

    log_dir = Path(args.log_dir) if args.log_dir else _default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    if args.status:
        return _cmd_status(args, log_dir)
    if args.stop:
        _touch(STOP_FILE, "stop")
        print(f"[flow-server] 종료 요청을 남겼습니다 ({STOP_FILE}). 몇 초 안에 서버와 감시기가 꺼집니다.")
        return 0
    if args.restart:
        _touch(RESTART_FILE, "restart")
        print(f"[flow-server] 재시작 요청을 남겼습니다 ({RESTART_FILE}).")
        return 0
    return run(args, log_dir)


if __name__ == "__main__":
    raise SystemExit(main())
