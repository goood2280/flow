"""core/scheduler_health.py — 백그라운드 스케줄러 기동 실패의 지속 감시 + 관리자 알림.

## 왜 필요한가

`app_v2/runtime/startup.py` 는 스케줄러를 하나씩 import 해서 start 한다. 실패하면
error 로그 한 줄과 `SCHEDULER_ERRORS` 리스트에 남을 뿐, **앱은 정상 기동하고 화면도
뜬다.** 그 기능만 조용히 멈춘다 — `product dedup scheduler` 가 그렇게 6일간,
랏 캐시 스케줄러가 며칠간 죽어 있었고 아무도 몰랐다. 로그를 매일 보는 사람이 없기
때문이다.

그래서 "실패했다"가 아니라 **"하루 넘게 실패한 채로 있다"** 를 알린다. 재시작 직후
잠깐 뜨는 실패로 알람을 남발하면 아무도 안 보게 된다.

## 동작

- 기동 시 `record_startup(SCHEDULER_ERRORS)` 로 이번 기동의 실패 목록을 파일에 남긴다.
  이미 기록돼 있던 서비스는 `first_failed_at` 을 **보존**한다(= 며칠째인지 계산 기준).
  이번에 실패 목록에 없는 서비스는 복구된 것으로 보고 지운다.
- `start_monitor()` 가 1시간마다 검사해서, `first_failed_at` 이 임계(기본 24h)를
  넘긴 서비스마다 관리자 bell 알림을 보낸다. 같은 서비스는 하루 1회만 재알림한다.
- 상태 파일은 **서버(호스트+역할)별로 분리**한다. 운영 api 서버와 개발 worker 서버는
  같은 데이터 루트를 보지만 띄우는 스케줄러 세트가 다르다 — 한 파일을 공유하면
  한쪽이 다른 쪽 기록을 "복구됨"으로 지워 버린다.

## 임계 조정

    FLOW_SCHEDULER_ALERT_AFTER_HOURS   기본 24   — 이 시간을 넘겨야 첫 알림
    FLOW_SCHEDULER_ALERT_REPEAT_HOURS  기본 24   — 재알림 간격
    FLOW_SCHEDULER_HEALTH_CHECK_MINUTES 기본 60  — 검사 주기
"""
from __future__ import annotations

import datetime
import os
import re
import socket
import threading

from core.paths import PATHS
from core.utils import load_json, save_json

HEALTH_DIR = PATHS.data_root / "scheduler_health"

_MONITOR_STARTED = False
_MONITOR_LOCK = threading.Lock()


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, "").strip())
        return value if value > 0 else default
    except Exception:
        return default


def _alert_after_hours() -> float:
    return _env_float("FLOW_SCHEDULER_ALERT_AFTER_HOURS", 24.0)


def _alert_repeat_hours() -> float:
    return _env_float("FLOW_SCHEDULER_ALERT_REPEAT_HOURS", 24.0)


def _check_interval_seconds() -> float:
    return _env_float("FLOW_SCHEDULER_HEALTH_CHECK_MINUTES", 60.0) * 60.0


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def _parse(ts: str):
    try:
        return datetime.datetime.fromisoformat(str(ts))
    except Exception:
        return None


def _server_role() -> str:
    """운영(api) / 개발 worker. 판정 실패는 운영으로 본다."""
    try:
        from core.worker_dispatch import server_role
        return str(server_role() or "api")
    except Exception:
        return "unknown"


def _role_label(role: str) -> str:
    return {"api": "운영서버", "worker": "개발 worker", "standalone": "운영서버"}.get(role, role)


def _host_key() -> str:
    host = ""
    try:
        host = socket.gethostname() or ""
    except Exception:
        host = ""
    raw = f"{host or 'unknown'}__{_server_role()}"
    # 파일명으로 쓰므로 경로 구분자/특수문자를 제거한다.
    return re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:100] or "unknown"


def _state_path():
    return HEALTH_DIR / f"{_host_key()}.json"


def _load_state() -> dict:
    state = load_json(_state_path(), {})
    if not isinstance(state, dict):
        return {}
    services = state.get("services")
    if not isinstance(services, dict):
        state["services"] = {}
    return state


def _save_state(state: dict) -> None:
    try:
        HEALTH_DIR.mkdir(parents=True, exist_ok=True)
        save_json(_state_path(), state, indent=2)
    except Exception:
        pass


def _humanize(delta: datetime.timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days}일 {hours}시간"
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


# ─────────────────────────────────────────────────────────
# 기록
# ─────────────────────────────────────────────────────────
def record_startup(errors, logger=None) -> dict:
    """이번 기동의 실패 목록을 반영한다.

    errors: startup.SCHEDULER_ERRORS 와 같은 [{"service": str, "error": str}, ...]

    이미 실패 중이던 서비스는 `first_failed_at` 을 보존하고(며칠째인지 계산 기준),
    이번에 목록에 없는 서비스는 복구된 것으로 보고 제거한다.
    """
    now = _now()
    now_iso = now.isoformat()
    failed = {}
    for item in errors or []:
        try:
            name = str(item.get("service") or "").strip()
        except Exception:
            continue
        if name:
            failed[name] = str(item.get("error") or "")

    state = _load_state()
    previous = state.get("services") or {}
    services = {}
    recovered = []

    for name, error in failed.items():
        prior = previous.get(name) or {}
        first = prior.get("first_failed_at") or now_iso
        services[name] = {
            "first_failed_at": first,
            "last_failed_at": now_iso,
            "error": error,
            # 알림 이력은 이어받는다 — 재시작마다 알림이 다시 터지면 안 된다.
            "last_alerted_at": prior.get("last_alerted_at", ""),
            "alert_count": int(prior.get("alert_count") or 0),
        }

    for name in previous:
        if name not in failed:
            recovered.append(name)

    state["services"] = services
    state["host"] = _host_key()
    state["role"] = _server_role()
    state["updated_at"] = now_iso
    _save_state(state)

    if logger is not None:
        if recovered:
            logger.info("scheduler health: 복구 확인 — %s", ", ".join(sorted(recovered)))
        if services:
            logger.warning(
                "scheduler health: %d개 스케줄러가 기동 실패 상태 — %s "
                "(%s 시간 이상 지속되면 관리자 알림)",
                len(services), ", ".join(sorted(services)), _alert_after_hours(),
            )
    return state


# ─────────────────────────────────────────────────────────
# 알림
# ─────────────────────────────────────────────────────────
def check_and_alert(logger=None) -> list:
    """임계를 넘긴 실패에 대해 관리자 bell 알림을 보낸다. 보낸 서비스명 목록 반환."""
    state = _load_state()
    services = state.get("services") or {}
    if not services:
        return []

    now = _now()
    threshold = datetime.timedelta(hours=_alert_after_hours())
    repeat = datetime.timedelta(hours=_alert_repeat_hours())
    role_label = _role_label(state.get("role") or _server_role())
    sent = []
    changed = False

    for name, info in services.items():
        first = _parse(info.get("first_failed_at"))
        if first is None:
            continue
        down_for = now - first
        if down_for < threshold:
            continue
        last_alerted = _parse(info.get("last_alerted_at"))
        if last_alerted is not None and (now - last_alerted) < repeat:
            continue

        title = f"[스케줄러 정지] {name}"
        body = (
            f"{role_label} · {first.strftime('%Y-%m-%d %H:%M')}부터 "
            f"{_humanize(down_for)}째 기동 실패 — {info.get('error') or '원인 미상'} · "
            f"재시작 전까지 이 기능은 멈춥니다"
        )
        try:
            from core.notify import send_to_admins
            send_to_admins(title, body, "warning")
        except Exception:
            if logger is not None:
                logger.debug("scheduler health: 알림 발송 실패 (%s)", name, exc_info=True)
            continue

        info["last_alerted_at"] = now.isoformat()
        info["alert_count"] = int(info.get("alert_count") or 0) + 1
        sent.append(name)
        changed = True
        if logger is not None:
            logger.error("scheduler health ALERT: %s — %s", title, body)

    if changed:
        state["services"] = services
        _save_state(state)
    return sent


def snapshot() -> dict:
    """관리자 화면/deploy-info 용 현재 상태 (이 서버 기준)."""
    state = _load_state()
    now = _now()
    out = []
    for name, info in (state.get("services") or {}).items():
        first = _parse(info.get("first_failed_at"))
        out.append({
            "service": name,
            "error": info.get("error") or "",
            "first_failed_at": info.get("first_failed_at") or "",
            "down_for": _humanize(now - first) if first else "",
            "down_hours": round((now - first).total_seconds() / 3600.0, 1) if first else 0.0,
            "alerted": bool(info.get("last_alerted_at")),
            "last_alerted_at": info.get("last_alerted_at") or "",
        })
    out.sort(key=lambda x: -x["down_hours"])
    return {
        "host": state.get("host") or _host_key(),
        "role": state.get("role") or _server_role(),
        "alert_after_hours": _alert_after_hours(),
        "updated_at": state.get("updated_at") or "",
        "failed": out,
    }


# ─────────────────────────────────────────────────────────
# 상주 감시
# ─────────────────────────────────────────────────────────
def start_monitor(logger=None) -> None:
    """1시간 주기 검사 스레드. 기동은 1회만 (중복 호출 무해)."""
    global _MONITOR_STARTED
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return
        _MONITOR_STARTED = True

    interval = _check_interval_seconds()

    def _loop():
        # 기동 직후 한 번 검사한다 — 어제부터 죽어 있었다면 재시작해도 여전히
        # 실패할 가능성이 높고, 그 경우 첫 검사에서 바로 알려야 한다.
        while True:
            try:
                from core.background_owner import is_owner
                if is_owner():
                    check_and_alert(logger)
            except Exception:
                if logger is not None:
                    logger.debug("scheduler health monitor tick failed", exc_info=True)
            threading.Event().wait(interval)

    thread = threading.Thread(target=_loop, name="scheduler-health-monitor", daemon=True)
    thread.start()
    if logger is not None:
        logger.info(
            "scheduler health monitor started (%.0f분 주기, %.0f시간 이상 정지 시 관리자 알림)",
            interval / 60.0, _alert_after_hours(),
        )
