"""core/flowi_progress.py — Flow-i 턴의 **공개 실행 단계**를 실시간으로 흘려보내는 채널.

홈 챗은 답이 나올 때까지 "답변 준비 중" 한 줄만 보여줬다. 그건 경과 시간으로
문구를 바꾸는 **가짜 진행 표시**였고, 실제로 어떤 단위기능/오케스트레이터가
떴는지와는 아무 관계가 없었다. 이 모듈은 그 자리에 진짜 신호를 넣는다.

## 무엇을 흘리고 무엇을 안 흘리는가

흘리는 것은 **누가 떴고 어떻게 끝났는지**뿐이다 — 도구 이름/제목, 상태
(running/success/warning/failed/blocked), 소요시간, 짧은 공개 detail.
프롬프트 원문, 모델 추론, SQL, 행 데이터는 담지 않는다. `_public_event()` 가
화이트리스트로 잘라내므로 호출측이 실수로 넘겨도 새어나가지 않는다.

## 왜 파일인가

Flow-i 턴은 `FLOW_FLOWI_OFFLOAD` 로 **개발서버 워커에서 실행될 수 있다.**
그러면 프로세스 메모리 큐는 API 서버에서 보이지 않는다. 두 서버가 공유하는
data_root 에 append-only JSONL 로 남기면 어디서 실행되든 같은 파일을 읽는다
(worker_dispatch 파일큐·shared_lease 와 같은 이유의 같은 선택).

## 쓰는 법

    run_id = flowi_progress.begin(client_run_id)     # 라우터에서 1회
    flowi_progress.bind(run_id)                      # 실행 프로세스에서 1회
    with flowi_progress.step("단위기능", "step_lookup"):
        ...
    flowi_progress.end(run_id, status="success")

`bind()` 로 묶어두면 깊은 코드는 run_id 를 인자로 받지 않고도 `step()` 을 쓴다.
run_id 가 없거나 어떤 이유로든 실패하면 **조용히 no-op** 한다 — 진행 표시가
본 기능을 절대 깨뜨리지 않아야 한다.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from core.paths import PATHS

logger = logging.getLogger("flow.flowi_progress")

# 한 턴이 남길 수 있는 최대 이벤트 수. 루프가 폭주해도 파일이 커지지 않게.
MAX_EVENTS = 200
# 이보다 오래된 진행 파일은 새 턴 시작 시 청소한다.
RETENTION_SEC = 30 * 60
_RUN_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")

_STATUSES = {"running", "success", "warning", "failed", "blocked", "skipped"}
# 이벤트에 실려 나갈 수 있는 키. 이 밖의 값은 버린다.
_PUBLIC_KEYS = ("seq", "ts", "phase", "label", "detail", "status", "ms", "group")

_CURRENT: contextvars.ContextVar[str] = contextvars.ContextVar("flowi_progress_run_id", default="")


def safe_run_id(value: Any) -> str:
    """파일명으로 쓸 수 있는 run id. 경로 조작 문자를 전부 없앤다."""
    clean = _RUN_ID_RE.sub("", str(value or "")).strip("-_")
    return clean[:80]


def new_run_id() -> str:
    stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"flowi-{stamp}-{uuid.uuid4().hex[:8]}"


def _dir() -> Path:
    path = PATHS.data_root / "flowi_progress"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file(run_id: str) -> Path | None:
    clean = safe_run_id(run_id)
    if not clean:
        return None
    return _dir() / f"{clean}.jsonl"


def bind(run_id: str) -> str:
    """이 실행 컨텍스트의 run id 를 정한다. 빈 값이면 진행 표시를 끈다."""
    clean = safe_run_id(run_id)
    _CURRENT.set(clean)
    return clean


def current_run_id() -> str:
    try:
        return _CURRENT.get()
    except Exception:  # noqa: BLE001
        return ""


def _now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def _clip(value: Any, limit: int = 120) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def _public_event(raw: dict[str, Any]) -> dict[str, Any]:
    """공개 키만 남긴다 — 프롬프트/추론/데이터가 실려도 여기서 잘린다."""
    out: dict[str, Any] = {}
    for key in _PUBLIC_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if key in {"seq", "ms"}:
            try:
                out[key] = int(value)
            except Exception:  # noqa: BLE001
                continue
        elif key == "status":
            status = str(value or "")
            out[key] = status if status in _STATUSES else "running"
        else:
            out[key] = _clip(value, 200 if key == "detail" else 120)
    return out


def _append(run_id: str, event: dict[str, Any]) -> None:
    path = _file(run_id)
    if path is None:
        return
    try:
        rows = _read_rows(path)
        if len(rows) >= MAX_EVENTS:
            return
        event = {**event, "seq": len(rows) + 1, "ts": _now_iso()}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_public_event(event), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 진행 표시가 본 기능을 깨뜨리지 않는다.
        logger.debug("flowi progress append failed", exc_info=True)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(row, dict):
                out.append(row)
    except Exception:  # noqa: BLE001
        return []
    return out


def prune(retention_sec: float = RETENTION_SEC) -> int:
    """오래된 진행 파일 청소. 턴 시작 때 한 번 부른다."""
    removed = 0
    try:
        cutoff = time.time() - max(60.0, float(retention_sec))
        for path in _dir().glob("*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        logger.debug("flowi progress prune failed", exc_info=True)
    return removed


def begin(run_id: str = "", *, label: str = "질문 접수") -> str:
    """턴 시작. run id 를 만들고(또는 정리하고) 첫 이벤트를 남긴다.

    이미 파일이 있으면 덮어쓰지 않는다 — 라우터와 실행 프로세스가 같은 run 을
    두 번 열어도 이벤트가 사라지지 않게.
    """
    clean = safe_run_id(run_id) or new_run_id()
    path = _file(clean)
    if path is None:
        return ""
    prune()
    if not path.exists():
        # phase="note" — 짝이 되는 end 가 없는 순간 이벤트다. start 로 두면 화면에서
        # 영원히 "실행 중"으로 남는다.
        _append(clean, {"phase": "note", "label": label, "status": "success"})
    bind(clean)
    return clean


def note(label: str, detail: str = "", *, status: str = "success", run_id: str = "") -> None:
    """순간 이벤트(시작/끝 쌍이 없는 것). 예: 기능 라우팅 결과."""
    target = safe_run_id(run_id) or current_run_id()
    if not target:
        return
    _append(target, {"phase": "note", "label": label, "detail": detail, "status": status})


def step_start(label: str, detail: str = "", *, group: str = "", run_id: str = "") -> dict[str, Any]:
    target = safe_run_id(run_id) or current_run_id()
    token = {"run_id": target, "label": label, "detail": detail, "group": group, "t0": time.perf_counter()}
    if not target:
        return token
    _append(target, {"phase": "start", "label": label, "detail": detail, "group": group, "status": "running"})
    return token


def step_end(token: dict[str, Any], *, status: str = "success", detail: str = "") -> None:
    target = str((token or {}).get("run_id") or "")
    if not target:
        return
    try:
        ms = int((time.perf_counter() - float(token.get("t0") or 0.0)) * 1000)
    except Exception:  # noqa: BLE001
        ms = 0
    _append(target, {
        "phase": "end",
        "label": token.get("label") or "",
        "detail": detail or token.get("detail") or "",
        "group": token.get("group") or "",
        "status": status,
        "ms": ms,
    })


@contextlib.contextmanager
def step(label: str, detail: str = "", *, group: str = "", run_id: str = "") -> Iterator[dict[str, Any]]:
    """`with flowi_progress.step("단위기능", "step_lookup"):` — 예외면 failed 로 닫는다."""
    token = step_start(label, detail, group=group, run_id=run_id)
    try:
        yield token
    except BaseException:
        step_end(token, status="failed")
        raise
    else:
        step_end(token, status="success")


def end(run_id: str = "", *, status: str = "success", label: str = "답변 정리", detail: str = "") -> None:
    target = safe_run_id(run_id) or current_run_id()
    if not target:
        return
    _append(target, {"phase": "done", "label": label, "detail": detail, "status": status})


def read(run_id: str, after: int = 0) -> dict[str, Any]:
    """`after` seq 이후의 이벤트와 종료 여부."""
    path = _file(run_id)
    rows = _read_rows(path) if path is not None else []
    try:
        cursor = max(0, int(after or 0))
    except Exception:  # noqa: BLE001
        cursor = 0
    events = [_public_event(row) for row in rows if int(row.get("seq") or 0) > cursor]
    done = any(str(row.get("phase") or "") == "done" for row in rows)
    return {
        "ok": True,
        "run_id": safe_run_id(run_id),
        "events": events,
        "last_seq": int(rows[-1].get("seq") or 0) if rows else cursor,
        "done": done,
    }


def discard(run_id: str) -> None:
    path = _file(run_id)
    if path is None:
        return
    with contextlib.suppress(OSError):
        path.unlink()
