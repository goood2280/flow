"""core/cache_budget.py — 프로세스 전체 캐시 예산 총량 상한 코디네이터.

각 캐시(root RAM, filebrowser preview, reformatize, SplitTable view/product)는
자체 적응형 예산을 갖지만, 개별 예산의 합이 호스트 메모리를 넘어설 수 있었다
(예: 10GB 호스트에서 root 2~6GB + preview ~1.6GB + reformatize ~1.5GB + view
~1.5GB → 캐시만으로 OOM 사정권). 이 모듈은 호스트 총 메모리 × 총량 비율
(기본 45%)을 단일 풀로 두고, 안정 운용 목표 계수(기본 80%)로 여유를 확보한
뒤 캐시별 고정 지분(share)으로 나눠 개별 예산에 상한(cap)을 건다.

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
  FLOW_CACHE_MEMORY_TARGET_RATIO    위 캐시 풀에 곱하는 안정 운용 목표 계수
                                    (기본 0.80, 0.5~1.0 클램프). 기존 캐시
                                    용량의 약 80%를 유지해 프로세스·요청 순간
                                    메모리용 headroom을 남긴다.
  FLOW_DEV_CACHE_BUDGET_FACTOR      비운영 개발 PC 에서 풀에 곱하는 축소 계수
                                    (기본 0.35). 운영 서버에는 적용되지 않는다.
"""
from __future__ import annotations

import os
import threading
import time

_POOL_FRACTION_DEFAULT = 0.45
_LARGE_POOL_FRACTION_DEFAULT = 0.6
_MEMORY_TARGET_RATIO_DEFAULT = 0.80
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


_DEV_FACTOR_DEFAULT = 0.35  # 비운영 개발 환경 캐시 풀 축소 계수


def _is_dev() -> bool:
    """비운영 개발 환경(개발 PC) 여부."""
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
    try:
        from core.runtime_limits import is_large_profile
        if is_large_profile():
            # 전용 대형 서버: 메모리 대부분을 캐시에 쓴다(128GB 기준 ~77GB).
            return _LARGE_POOL_FRACTION_DEFAULT
    except Exception:
        pass
    return _POOL_FRACTION_DEFAULT


def memory_target_ratio() -> float:
    """기존 캐시 풀 대비 실제 운용 목표 비율.

    캐시 저장 형식과 hit 경로는 건드리지 않고 모든 조정 대상 캐시의 상한만
    같은 비율로 줄인다. 잘못된 env 값은 안정적인 기본 0.80으로 복귀한다.
    """
    raw = os.environ.get("FLOW_CACHE_MEMORY_TARGET_RATIO", "")
    if raw not in (None, ""):
        try:
            return max(0.5, min(1.0, float(raw)))
        except Exception:
            pass
    return _MEMORY_TARGET_RATIO_DEFAULT


def worker_budget_factor() -> float:
    """캐시 풀 추가 축소 계수 (이름은 호환용으로 유지).

    - 운영(prod): 1.0 (축소 없음)
    - 비운영 개발 PC: 개발 풀 비율(pool_fraction_dev)이 **명시되면 1.0**(그 값이
      이미 최종이라 중복 축소 안 함). 미설정이면 기본 0.35(env·톱니바퀴 dev_factor 로 조정)."""
    if not _is_dev():
        return 1.0
    # 개발 풀 비율이 명시되면 추가 축소를 곱하지 않는다(중복 축소 방지).
    try:
        from core import cache_settings
        if cache_settings.read().get("pool_fraction_dev") not in (None, ""):
            return 1.0
    except Exception:
        pass
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
    pool = int(
        total_bytes * _pool_fraction() * factor * memory_target_ratio()
    ) if total_bytes > 0 else 0
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


def large_host() -> bool:
    """전용 대형 서버(운영) 여부 — 개발/worker 는 대형이어도 기존 작은 기본값을 쓴다."""
    try:
        from core.runtime_limits import is_large_profile
        return bool(is_large_profile()) and not _is_dev()
    except Exception:
        return False


def default_bytes(name: str, small_default_bytes: int) -> int:
    """캐시 name 의 **자동** 기본 예산. 대형 서버면 풀 지분 전체, 아니면 기존 작은 값.

    소형 서버용 기본값(수백 MB~2GB)은 128GB 서버에서 캐시 히트율을 스스로 막는다.
    대형 서버에서는 지분 상한까지 쓰게 해 재조회(디스크·원천 재스캔)를 줄인다.
    운영자 명시값(env·⚙)은 이 함수를 거치지 않는다."""
    if large_host():
        cap = cap_bytes(name)
        if cap > small_default_bytes:
            return cap
    return int(small_default_bytes)


def overview() -> dict:
    """관리자 화면용 — 풀 총량과 캐시별 상한."""
    pool = pool_bytes()
    return {
        "pool_bytes": pool,
        "pool_fraction": _pool_fraction(),
        "memory_target_ratio": memory_target_ratio(),
        "worker_budget_factor": worker_budget_factor(),
        "caps": {name: cap_bytes(name) for name in SHARES},
        # 명시 예산일 때 적용되는 상한 — 화면에서 "설정값이 왜 깎였나"를 설명한다.
        "caps_explicit": {name: cap_bytes(name, ignore_role_factor=True) for name in SHARES},
    }


def diagnostics() -> tuple[dict, list[str]]:
    """Report effective budgets and restrictive saved overrides without editing them."""
    info = overview()
    summary = {
        "cache_pool_gb": round(info["pool_bytes"] / 1024**3, 2),
        "cache_pool_fraction": info["pool_fraction"],
        "cache_memory_target_ratio": info["memory_target_ratio"],
    }
    warnings: list[str] = []
    if not large_host():
        return summary, warnings
    from core import cache_settings

    if info["pool_fraction"] < _LARGE_POOL_FRACTION_DEFAULT:
        source = "FLOW_CACHE_TOTAL_BUDGET_FRACTION" if os.environ.get(
            "FLOW_CACHE_TOTAL_BUDGET_FRACTION", ""
        ).strip() else "cache_budget_settings.json: pool_fraction"
        warnings.append(
            f"{source} 적용값 {info['pool_fraction']:g}가 large 자동값 "
            f"{_LARGE_POOL_FRACTION_DEFAULT:g}보다 작습니다. 이전 서버의 값이면 "
            "해당 환경변수를 제거하거나 캐시관리 설정을 자동으로 되돌리세요."
        )
    for key, env, cache, unit in (
        ("view_mb", "FLOW_SPLITTABLE_VIEW_CACHE_MAX_MB", "splittable_view_payload", 1024**2),
        (None, "FLOW_PREVIEW_MEMORY_CACHE_GB", "filebrowser_preview", 1024**3),
    ):
        raw = os.environ.get(env, "").strip()
        source = env if raw or key is None else "cache_budget_settings.json: " + key
        value = raw if raw else (cache_settings.get_float_role(key, False) if key else None)
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        cap = info["caps"][cache]
        if 0 < value * unit < cap:
            warnings.append(
                f"{source}={value:g}가 {cache}의 현재 풀 지분 "
                f"{cap / unit:.1f}보다 작게 고정되어 있습니다. 의도한 제한이면 유지하고, "
                "이전 서버의 값이면 해당 환경변수를 제거하거나 캐시관리 설정을 자동으로 되돌리세요."
            )
    return summary, warnings
