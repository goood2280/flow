"""routers/monitor.py v8.8.18 — 시스템 모니터 라우터.

core/sysmon.py 에 실제 수집/부하 로직이 있고 이 라우터는 읽기 전용 API 를 노출.
경로 정리:
  GET /api/monitor/system     — 현재 시스템 스냅샷 (즉시 collect + 반환).
  GET /api/monitor/history    — resource_log tail (차트용). limit 기본 288 = 1일치.
  GET /api/monitor/state      — 유휴/부하/최근 활동 상태 스냅샷.
  GET /api/system/stats       — alias (홈/Admin 위젯용) — state + sample + history tail(60).

레거시 호환:
  POST /api/monitor/heartbeat — 5분 주기 external cron 호환(서버에서도 자동 수집).
  GET  /api/monitor/farm-status → state 로 리다이렉션.
  GET  /api/monitor/resource-log → history.
"""
from fastapi import APIRouter, Query

from core import sysmon

router = APIRouter(tags=["monitor"])

# Two prefixes: 기존 /api/monitor/* 는 호환용, 홈 위젯은 /api/system/stats 로 호출.
mon_router = APIRouter(prefix="/api/monitor")
sys_router = APIRouter(prefix="/api/system")


@mon_router.get("/system")
def system_info():
    return sysmon.collect_once()


@mon_router.get("/history")
def system_history(limit: int = Query(288)):
    return {"logs": sysmon.history(limit=max(1, min(3000, int(limit or 288))))}


@mon_router.get("/state")
def system_state():
    return sysmon.get_state()


@mon_router.post("/heartbeat")
def heartbeat():
    """외부 cron 호환 — 서버도 자체 5분 주기 수집하므로 추가 샘플만 남기고 리턴."""
    s = sysmon.collect_once()
    return {"ok": True, "timestamp": s.get("timestamp", ""), "state": sysmon.get_state()}


@mon_router.get("/resource-log")
def resource_log(limit: int = Query(200)):
    return {"logs": sysmon.history(limit=max(1, min(3000, int(limit or 200))))}


@mon_router.get("/farm-status")
def farm_status():
    # 레거시 이름 유지, 실제 구현은 get_state.
    return sysmon.get_state()


@sys_router.get("/stats")
def stats(history_limit: int = Query(60)):
    """FE 위젯용 통합 엔드포인트: 현재 샘플 + 상태 + 히스토리(기본 1일 = 288, 위젯은 60)."""
    hist = sysmon.history(limit=max(1, min(3000, int(history_limit or 60))))
    state = sysmon.get_state()
    return {
        "current": state.get("sample") or {},
        "state": state,
        "history": hist,
    }


# Merge
router.include_router(mon_router)
router.include_router(sys_router)


# 서버 기동 시 백그라운드 수집 스레드 시작. backend/app.py 에서 router 를
# 자동 로드하는 dynamic importer 가 이 모듈을 import 할 때 실행된다.
try:
    sysmon.start_background()
except Exception:
    pass
