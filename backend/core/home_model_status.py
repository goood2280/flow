"""Status without endpoints, credentials or provider response bodies."""
import time
from core import llm_adapter


def snapshot():
    cfg = llm_adapter.get_config()
    health = llm_adapter.health_snapshot()
    status, message = "unknown", "연결 검사를 실행해 주세요."
    if not cfg.get("enabled"):
        status, message = "disabled", "관리자 설정에서 모델을 활성화하세요."
    elif not llm_adapter.is_available():
        status, message = "unconfigured", "모델 URL과 인증·시스템 이름 설정을 확인하세요."
    elif health["status"] == "unhealthy":
        status, message = "disconnected", "모델 응답에 실패했습니다. URL·인증·응답 형식·서버 상태를 확인하세요."
    elif health["status"] == "healthy" and time.time() - health.get("last_check_at", 0) < 120:
        status, message = "connected", "최근 모델 응답을 확인했습니다."
    return {"status": status, "message": message, "model": cfg.get("model", ""),
            "provider": cfg.get("provider", ""), "last_check_at": health.get("last_check_at", 0),
            "last_latency_ms": health.get("last_latency_ms", 0)}
