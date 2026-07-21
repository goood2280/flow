"""core/cache_budget.py — 프로세스 전체 캐시 예산 총량 상한 코디네이터.

각 캐시(root RAM, filebrowser preview, reformatize, SplitTable view/product)는
자체 적응형 예산을 갖지만, 개별 예산의 합이 호스트 메모리를 넘어설 수 있었다
(예: 10GB 호스트에서 root 2~6GB + preview ~1.6GB + reformatize ~1.5GB + view
~1.5GB → 캐시만으로 OOM 사정권). 이 모듈은 호스트 총 메모리 × 총량 비율
(기본 45%)을 단일 풀로 두고, 캐시별 고정 지분(share)으로 나눠 개별 예산에
상한(cap)을 건다.

계약:
  - cap_bytes(name) → 해당 캐시가 가질 수 있는 최대 바이트. 0 = 상한 없음
    (호스트 총량을 못 읽는 환경).
  - 각 캐시의 예산 함수는 "운영자가 env 로 명시 고정하지 않은 경우에만"
    min(자체 예산, cap) 을 적용한다 — 명시 env 핀은 항상 우선.
  - 지분 합계는 1.0 — 모든 캐시가 꽉 차도 풀 총량을 넘지 않는다.

환경변수:
  FLOW_CACHE_TOTAL_BUDGET_FRACTION  캐시 풀 = 호스트 총량 × 이 값 (기본 0.45,
                                    0.1~0.8 클램프). 0 에 가깝게 줄이면 모든
                                    적응형 캐시가 함께 줄어든다.
  FLOW_WORKER_CACHE_BUDGET_FACTOR   worker(개발서버) 역할일 때 풀에 곱하는
                                    축소 계수 (기본 0.25 = 1/4). 개발서버는
                                    스플릿테이블 조회가 적어 캐시를 많이 들고
                                    있을 이유가 없다 — 검색은 upstream_proxy 로
                                    운영서버 캐시를 활용한다.
"""
from __future__ import annotations

import os
import threading
import time

_POOL_FRACTION_DEFAULT = 0.45
_POOL_MEMO_TTL_SEC = 60.0
_POOL_MEMO_LOCK = threading.Lock()
_POOL_MEMO: tuple[float, int] | None = None  # (monotonic ts, pool_bytes)

# 캐시별 풀 지분 — 합계 1.0. 값 근거: root RAM 이 히트 가치가 가장 크고,
# preview/view 는 디스크 캐시·재계산 폴백이 있어 축출 비용이 낮다.
SHARES: dict[str, float] = {
    "splittable_root_ram": 0.40,
    "filebrowser_preview": 0.18,
    "splittable_product_ram": 0.14,
    "splittable_view_payload": 0.12,
    "reformatize_raw": 0.10,
    "reformatize_wide": 0.06,
}


_WORKER_FACTOR_DEFAULT = 0.25


def _pool_fraction() -> float:
    raw = os.environ.get("FLOW_CACHE_TOTAL_BUDGET_FRACTION", "")
    try:
        value = float(raw) if raw not in (None, "") else _POOL_FRACTION_DEFAULT
    except Exception:
        value = _POOL_FRACTION_DEFAULT
    return max(0.1, min(0.8, value))


def worker_budget_factor() -> float:
    """worker(개발서버) 역할이면 풀 축소 계수(기본 1/4), 아니면 1.0.

    역할은 관리자 탭에서 런타임에 바뀔 수 있다 — pool memo TTL(60s) 안에
    자동 반영된다."""
    try:
        from core.worker_dispatch import server_role

        if server_role() != "worker":
            return 1.0
    except Exception:
        return 1.0
    raw = os.environ.get("FLOW_WORKER_CACHE_BUDGET_FACTOR", "")
    try:
        value = float(raw) if raw not in (None, "") else _WORKER_FACTOR_DEFAULT
    except Exception:
        value = _WORKER_FACTOR_DEFAULT
    return max(0.05, min(1.0, value))


def pool_bytes() -> int:
    """캐시 풀 총량(bytes). 호스트 총 메모리를 못 읽으면 0 (= 상한 미적용)."""
    global _POOL_MEMO
    now = time.monotonic()
    with _POOL_MEMO_LOCK:
        memo = _POOL_MEMO
        if memo is not None and now - memo[0] < _POOL_MEMO_TTL_SEC:
            return memo[1]
    total_bytes = 0.0
    try:
        from core.runtime_limits import system_memory_snapshot

        total_gb = float(system_memory_snapshot().get("system_memory_total_gb") or 0.0)
        total_bytes = total_gb * (1024.0 ** 3)
    except Exception:
        total_bytes = 0.0
    pool = int(total_bytes * _pool_fraction() * worker_budget_factor()) if total_bytes > 0 else 0
    with _POOL_MEMO_LOCK:
        _POOL_MEMO = (now, pool)
    return pool


def cap_bytes(name: str) -> int:
    """캐시 name 의 풀 지분 상한(bytes). 0 = 상한 없음."""
    share = float(SHARES.get(name) or 0.0)
    if share <= 0:
        return 0
    pool = pool_bytes()
    if pool <= 0:
        return 0
    return int(pool * share)


def capped(name: str, own_budget_bytes: int) -> int:
    """자체 예산에 풀 지분 상한을 적용한 값. cap 이 없으면 자체 예산 그대로."""
    cap = cap_bytes(name)
    if cap <= 0:
        return own_budget_bytes
    return min(int(own_budget_bytes), cap)


def overview() -> dict:
    """관리자 화면용 — 풀 총량과 캐시별 상한."""
    pool = pool_bytes()
    return {
        "pool_bytes": pool,
        "pool_fraction": _pool_fraction(),
        "worker_budget_factor": worker_budget_factor(),
        "caps": {name: cap_bytes(name) for name in SHARES},
    }
