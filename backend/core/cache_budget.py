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
  - 각 캐시의 예산 함수는 env 명시값을 희망 상한으로 사용하되 항상
    min(자체 예산, cap) 을 적용한다. 개별 설정 실수로 전체 안전 풀을 우회해
    서버가 OOM 되는 경로를 허용하지 않는다.
  - 지분 합계는 1.0 — 모든 캐시가 꽉 차도 풀 총량을 넘지 않는다.

환경변수:
  FLOW_CACHE_TOTAL_BUDGET_FRACTION  캐시 풀 = 호스트 총량 × 이 값 (기본 0.45,
                                    0.1~0.8 클램프). 0 에 가깝게 줄이면 모든
                                    적응형 캐시가 함께 줄어든다.
  FLOW_WORKER_CACHE_BUDGET_FACTOR   worker(개발서버) 역할일 때 풀에 곱하는
                                    축소 계수 (기본 0.25 = 1/4). 개발서버는
                                    스플릿테이블 조회가 적어 캐시를 많이 들고
                                    있을 이유가 없다 — 운영 연결 시에는
                                    upstream_proxy 캐시를 우선 활용하고,
                                    로컬 폴백 시에도 이 축소 예산 안에서 검색한다.
"""
from __future__ import annotations

import os
import threading
import time

_POOL_FRACTION_DEFAULT = 0.45
_POOL_MEMO_TTL_SEC = 60.0
_POOL_MEMO_LOCK = threading.Lock()
# key: "auto"(역할 축소 적용) | "explicit"(역할 축소 미적용) → (monotonic ts, pool_bytes)
_POOL_MEMO: dict[str, tuple[float, int]] = {}

# 캐시별 풀 지분 — 합계 1.0. 값 근거: root RAM 이 히트 가치가 가장 크고,
# preview/view 는 디스크 캐시·재계산 폴백이 있어 축출 비용이 낮다.
SHARES: dict[str, float] = {
    # SplitTable의 첫 번째 읽기 계층(완성 응답)에 가장 큰 몫을 준다. pivot이
    # 완성된 일반 조회에서는 root/product RAM보다 view payload 히트의 효과가 크다.
    # 합계 0.90만 배정해 ET 다운로드/일시적 빌드용 여유도 남긴다.
    "splittable_view_payload": 0.35,
    "splittable_root_ram": 0.15,
    "splittable_product_ram": 0.05,
    "filebrowser_preview": 0.12,
    "reformatize_raw": 0.13,
    "reformatize_wide": 0.10,
}


_WORKER_FACTOR_DEFAULT = 0.25
_DEV_FACTOR_DEFAULT = 0.35  # 비운영 개발 환경 캐시 풀 축소 계수


def _is_dev() -> bool:
    """개발서버(worker 또는 비운영 개발 환경) 여부."""
    try:
        from core.worker_dispatch import server_role
        if server_role() == "worker":
            return True
    except Exception:
        pass
    try:
        from core.paths import PATHS
        return not bool(PATHS.is_prod)
    except Exception:
        return False


def _pool_fraction() -> float:
    """캐시 풀 비율 — 운영/개발 분리. env > 톱니바퀴(pool_fraction[_dev]) > 기본."""
    raw = os.environ.get("FLOW_CACHE_TOTAL_BUDGET_FRACTION", "")
    if raw not in (None, ""):
        try:
            return max(0.1, min(0.8, float(raw)))
        except Exception:
            pass
    # 톱니바퀴 설정(관리자 UI): 개발서버는 pool_fraction_dev 우선, 없으면 pool_fraction.
    try:
        from core import cache_settings
        v = cache_settings.get_float_role("pool_fraction", _is_dev())
        if v is not None:
            return max(0.1, min(0.8, v))
    except Exception:
        pass
    return _POOL_FRACTION_DEFAULT


def worker_budget_factor() -> float:
    """서버 역할별 캐시 풀 추가 축소 계수.

    - 운영(prod) api: 1.0 (축소 없음)
    - 개발(worker/비운영 개발 환경): 개발 풀 비율(pool_fraction_dev)이 **명시되면
      1.0**(그 값이 이미 최종이라 중복 축소 안 함). 미설정이면 역할별 기본 축소
      계수(worker 0.25 / dev 0.35, env·톱니바퀴 dev_factor 로 조정).

    역할은 런타임에 바뀔 수 있다 — pool memo TTL(60s) 안에 자동 반영된다."""
    try:
        from core.worker_dispatch import server_role
        role = server_role()
    except Exception:
        role = "api"
    if not _is_dev():
        return 1.0
    # 개발 풀 비율이 명시되면 추가 축소를 곱하지 않는다(중복 축소 방지).
    try:
        from core import cache_settings
        if cache_settings.read().get("pool_fraction_dev") not in (None, ""):
            return 1.0
    except Exception:
        pass
    if role == "worker":
        raw = os.environ.get("FLOW_WORKER_CACHE_BUDGET_FACTOR", "")
        try:
            return max(0.05, min(1.0, float(raw))) if raw not in (None, "") else _WORKER_FACTOR_DEFAULT
        except Exception:
            return _WORKER_FACTOR_DEFAULT
    # worker 마커 없이 실행한 비운영 개발 환경
    raw = os.environ.get("FLOW_DEV_CACHE_BUDGET_FACTOR", "")
    if raw not in (None, ""):
        try:
            return max(0.05, min(1.0, float(raw)))
        except Exception:
            pass
    try:
        from core import cache_settings
        v = cache_settings.get_float("dev_factor")
        if v is not None:
            return max(0.05, min(1.0, v))
    except Exception:
        pass
    return _DEV_FACTOR_DEFAULT


def invalidate() -> None:
    """풀 메모(60s TTL)를 즉시 무효화 — 톱니바퀴에서 예산 설정 저장 직후 호출해
    변경이 바로 반영되게 한다."""
    with _POOL_MEMO_LOCK:
        _POOL_MEMO.clear()


def pool_bytes(*, ignore_role_factor: bool = False) -> int:
    """캐시 풀 총량(bytes). 호스트 총 메모리를 못 읽으면 0 (= 상한 미적용).

    ignore_role_factor=True 면 개발/worker **자동** 축소 계수를 곱하지 않는다 —
    운영자가 그 캐시의 개발 예산을 직접 지정한 경우에 쓴다(아래 capped 참고).
    호스트총량 × pool_fraction 이라는 안전 한도는 그대로 유지된다."""
    key = "explicit" if ignore_role_factor else "auto"
    now = time.monotonic()
    with _POOL_MEMO_LOCK:
        memo = _POOL_MEMO.get(key)
        if memo is not None and now - memo[0] < _POOL_MEMO_TTL_SEC:
            return memo[1]
    total_bytes = 0.0
    try:
        from core.runtime_limits import system_memory_snapshot

        total_gb = float(system_memory_snapshot().get("system_memory_total_gb") or 0.0)
        total_bytes = total_gb * (1024.0 ** 3)
    except Exception:
        total_bytes = 0.0
    factor = 1.0 if ignore_role_factor else worker_budget_factor()
    pool = int(total_bytes * _pool_fraction() * factor) if total_bytes > 0 else 0
    with _POOL_MEMO_LOCK:
        _POOL_MEMO[key] = (now, pool)
    return pool


def cap_bytes(name: str, *, ignore_role_factor: bool = False) -> int:
    """캐시 name 의 풀 지분 상한(bytes). 0 = 상한 없음."""
    share = float(SHARES.get(name) or 0.0)
    if share <= 0:
        return 0
    pool = pool_bytes(ignore_role_factor=ignore_role_factor)
    if pool <= 0:
        return 0
    return int(pool * share)


def capped(name: str, own_budget_bytes: int, *, explicit: bool = False) -> int:
    """자체 예산에 풀 지분 상한을 적용한 값. cap 이 없으면 자체 예산 그대로.

    explicit=True 는 "운영자가 이 캐시의 (현재 역할용) 예산을 직접 지정했다"는
    뜻이다. 이때는 개발/worker 자동 축소 계수(0.25/0.35)를 상한 계산에서 빼고
    호스트총량 × pool_fraction × share 만 적용한다. 예전에는 ⚙ 에서
    `root_ram_gb_dev` 를 지정해도 worker 축소 계수가 상한을 다시 1/4 로 깎아
    **개발서버에 설정한 만큼 캐시가 올라가지 않았다** — 화면엔 설정값이,
    실제로는 그 1/4 이 적용되던 괴리의 원인. 자동(적응형) 예산은 예전 그대로
    축소 계수를 받는다.
    """
    cap = cap_bytes(name, ignore_role_factor=explicit)
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
        # 명시 예산일 때 적용되는 상한 — 화면에서 "설정값이 왜 깎였나"를 설명한다.
        "caps_explicit": {name: cap_bytes(name, ignore_role_factor=True) for name in SHARES},
    }
