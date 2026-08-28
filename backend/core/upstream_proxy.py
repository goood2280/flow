"""core/upstream_proxy.py — worker의 운영 소유 요청을 운영(api) 서버로 위임.

개발서버는 스플릿테이블 조회가 적어 자체 RAM 캐시를 크게 들고 있을 이유가
없다(cache_budget worker 축소 계수와 한 쌍). 검색 요청이 들어오면 운영서버
API 로 그대로 프록시해 운영의 예열된 캐시를 활용하고, 개발서버 메모리는
낮게 유지한다.

동작 조건 (전부 만족 시에만):
  - server_role() == "worker" (관리자 탭에서 런타임 변경 가능 — 매 요청 확인)
  - FLOW_API_SERVER_URL 설정 (예: http://prod-host:8080)
개발 워커의 SplitTable 조회는 FLOW_API_SERVER_URL이 있으면 운영서버의
예열 캐시를 우선 사용하되, URL이 없거나 운영서버가 응답하지 않으면
개발서버의 공유 DB/캐시로 로컬 검색한다. 브라우저 단일-AI POST(AI SQL,
unit AI, ChartBuilder Assistant)는 인증·실행 이력을 운영에 모으기 위해
기존처럼 운영서버 전용이며, 연결 실패 시 503을 반환한다.

인증: 세션 토큰 저장소({data_root}/sessions/tokens.json)가 서버 간 공유라
개발서버로 들어온 x-session-token 을 그대로 전달하면 운영서버가 동일하게
검증한다 — 별도 자격증명 불필요.

운영서버 다운/타임아웃/5xx 면 None 을 반환한다. 호출측은 SplitTable
GET은 로컬 검색으로 폴백하고, 운영 전용 AI POST만 503을 만든다. 연결
실패 후에는 쿨다운(기본 60초) 동안 프록시 시도 자체를 건너뛰어 운영서버가
죽어 있을 때 매 검색에 연결 대기 지연이 붙는 것을 막는다. 4xx 는 결정적
응답(잘못된 파라미터·인증 실패)이므로 그대로 통과시킨다.

루프 방지: 프록시 요청에 x-flow-upstream-proxied 헤더를 붙이고, 이 헤더가
있는 수신 요청은 절대 다시 프록시하지 않는다 — URL 오설정으로 자기 자신을
가리켜도 1홉 후 로컬 처리로 수렴한다.

환경변수:
  FLOW_API_SERVER_URL                 운영서버 베이스 URL (필수, 없으면 비활성)
  FLOW_SPLITTABLE_PROXY_PATHS         프록시 대상 경로 목록(쉼표) 교체
  FLOW_OPERATING_AI_PROXY_PATHS       운영으로 보낼 AI POST prefix 목록 교체
  FLOW_SPLITTABLE_PROXY_TIMEOUT_SEC   응답 대기 한도 (기본 120초 — 운영측
                                      첫 계산이 느릴 수 있음)
  FLOW_SPLITTABLE_PROXY_COOLDOWN_SEC  연결 실패 후 재시도 금지 창 (기본 60초)
"""
from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger("flow.upstream_proxy")

# 검색 화면이 치는 캐시 의존 read 경로 — 전부 GET.
_PROXY_PATHS_DEFAULT = (
    "/api/splittable/view",
    "/api/splittable/lot-ids",
    "/api/splittable/lot-candidates",
    "/api/splittable/column-values",
)
# 브라우저에서 직접 누르는 단일 AI 실행은 운영 API가 소유한다. 개발 워커에는
# LLM credential/profile이 없을 수 있고, 실행 이력과 breaker 상태도 운영에
# 모여야 한다. 이 POST들은 세션 토큰과 JSON body를 그대로 한 홉 전달한다.
_AI_PROXY_PATHS_DEFAULT = (
    "/api/filebrowser/sql/llm/draft",
    "/api/filebrowser/settings/llm/draft",
    "/api/filebrowser/chart-builder/assistant",
    "/api/agent/unit-ai/",
    "/api/agent/unit/",
    "/api/llm/flowi/chat",
)
LOOP_GUARD_HEADER = "x-flow-upstream-proxied"
_TIMEOUT_SEC_DEFAULT = 120.0
_COOLDOWN_SEC_DEFAULT = 60.0

_LOCK = threading.Lock()
_FAIL_UNTIL_MONO = 0.0
_STATS = {"hit": 0, "passthrough_4xx": 0, "fallback_5xx": 0, "fallback_error": 0, "last_error": ""}


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    raw = os.environ.get(name, "")
    try:
        value = float(raw) if raw not in (None, "") else default
    except Exception:
        value = default
    return max(lo, min(hi, value))


def api_base_url() -> str:
    return str(os.environ.get("FLOW_API_SERVER_URL", "") or "").strip().rstrip("/")


def proxy_paths() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_SPLITTABLE_PROXY_PATHS", "")
    if raw.strip():
        return tuple(p.strip() for p in raw.split(",") if p.strip())
    return _PROXY_PATHS_DEFAULT


def ai_proxy_paths() -> tuple[str, ...]:
    raw = os.environ.get("FLOW_OPERATING_AI_PROXY_PATHS", "")
    if raw.strip():
        return tuple(p.strip() for p in raw.split(",") if p.strip())
    return _AI_PROXY_PATHS_DEFAULT


def _supported_proxy_request(path: str, method: str) -> bool:
    verb = str(method or "").upper()
    if verb == "GET":
        return any(str(path or "").startswith(prefix) for prefix in proxy_paths())
    if verb == "POST":
        return any(str(path or "").startswith(prefix) for prefix in ai_proxy_paths())
    return False


def _timeout_sec() -> float:
    return _env_float("FLOW_SPLITTABLE_PROXY_TIMEOUT_SEC", _TIMEOUT_SEC_DEFAULT, 5.0, 600.0)


def _cooldown_sec() -> float:
    return _env_float("FLOW_SPLITTABLE_PROXY_COOLDOWN_SEC", _COOLDOWN_SEC_DEFAULT, 0.0, 3600.0)


def enabled() -> bool:
    if not api_base_url():
        return False
    try:
        from core.worker_dispatch import server_role

        return server_role() == "worker"
    except Exception:
        return False


def requires_operating(path: str, method: str, incoming_headers) -> bool:
    """개발 워커에서 로컬 실행을 금지해야 하는 운영 소유 요청인가."""
    # SplitTable GET은 운영 캐시를 우선할 뿐 로컬 폴백이 허용된다.
    # LLM credential·breaker·실행 이력을 운영에 모아야 하는 AI POST만 필수다.
    if str(method or "").upper() != "POST" or not any(
            str(path or "").startswith(prefix) for prefix in ai_proxy_paths()):
        return False
    try:
        if incoming_headers is not None and incoming_headers.get(LOOP_GUARD_HEADER):
            return False
        from core.worker_dispatch import server_role
        return server_role() == "worker"
    except Exception:
        return False


def should_proxy(path: str, method: str, incoming_headers) -> bool:
    """이 요청을 운영서버로 위임할지 — 빠른 사전 판정 (미들웨어 hot path)."""
    if not _supported_proxy_request(path, method):
        return False
    try:
        if incoming_headers is not None and incoming_headers.get(LOOP_GUARD_HEADER):
            return False
    except Exception:
        pass
    if not enabled():
        return False
    with _LOCK:
        if time.monotonic() < _FAIL_UNTIL_MONO:
            return False
    return True


def _record_failure(kind: str, error: str) -> None:
    global _FAIL_UNTIL_MONO
    with _LOCK:
        _STATS[kind] = int(_STATS.get(kind) or 0) + 1
        _STATS["last_error"] = error[:300]
        _FAIL_UNTIL_MONO = time.monotonic() + _cooldown_sec()


def forward(
    path: str,
    query: str,
    session_token: str,
    method: str = "GET",
    body: bytes = b"",
    content_type: str = "",
) -> tuple[int, bytes, str] | None:
    """운영서버로 GET/단일-AI POST 전달 (블로킹 — 호출측이 스레드로 감쌀 것).

    반환: (status_code, body, content_type) — 2xx/4xx 는 그대로 통과.
    None: 운영 연결 실패/타임아웃/5xx."""
    base = api_base_url()
    if not base:
        return None
    url = base + path + (f"?{query}" if query else "")
    verb = str(method or "GET").upper()
    req = urllib.request.Request(url, data=body if verb == "POST" else None, method=verb)
    # gzip 등 압축 협상 없이 원문 그대로 — 재인코딩 없이 바이트 통과 전달.
    req.add_header("Accept-Encoding", "identity")
    req.add_header(LOOP_GUARD_HEADER, "1")
    if verb == "POST":
        req.add_header("Content-Type", content_type or "application/json")
    if session_token:
        req.add_header("x-session-token", session_token)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=_timeout_sec()) as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type") or "application/json"
            with _LOCK:
                _STATS["hit"] = int(_STATS.get("hit") or 0) + 1
            return int(resp.status), body, content_type
    except urllib.error.HTTPError as exc:
        # 4xx = 결정적(파라미터/인증) — 로컬로 다시 계산해도 같은 답. 그대로 통과.
        if 400 <= int(exc.code) < 500:
            try:
                body = exc.read()
            except Exception:
                body = b""
            content_type = exc.headers.get("Content-Type") if exc.headers else "application/json"
            with _LOCK:
                _STATS["passthrough_4xx"] = int(_STATS.get("passthrough_4xx") or 0) + 1
            return int(exc.code), body, content_type or "application/json"
        # 5xx — 쿨다운 뒤 운영을 재시도한다. 그 사이 SplitTable GET은
        # 호출측이 로컬로 계속하고, AI POST는 503을 반환한다.
        _record_failure("fallback_5xx", f"HTTP {exc.code} from {base}")
        logger.warning("upstream proxy %s -> HTTP %s — upstream unavailable", path, exc.code)
        return None
    except Exception as exc:
        elapsed = time.perf_counter() - started
        _record_failure("fallback_error", f"{type(exc).__name__}: {exc}")
        logger.warning(
            "upstream proxy %s failed after %.1fs (%s) — upstream unavailable (cooldown %.0fs)",
            path, elapsed, type(exc).__name__, _cooldown_sec(),
        )
        return None


def status() -> dict:
    """관리자/모니터용 상태."""
    with _LOCK:
        stats = dict(_STATS)
        cooldown_remaining = max(0.0, _FAIL_UNTIL_MONO - time.monotonic())
    try:
        from core.worker_dispatch import server_role
        operating_required = server_role() == "worker"
    except Exception:
        operating_required = False
    return {
        "enabled": enabled(),
        "operating_required": operating_required,
        "ai_operating_required": operating_required,
        "splittable_local_fallback": True,
        "configuration_ready": bool(api_base_url()),
        "api_base_url": api_base_url(),
        "paths": list(proxy_paths()),
        "ai_paths": list(ai_proxy_paths()),
        "timeout_sec": _timeout_sec(),
        "cooldown_sec": _cooldown_sec(),
        "cooldown_remaining_sec": round(cooldown_remaining, 1),
        "stats": stats,
    }
