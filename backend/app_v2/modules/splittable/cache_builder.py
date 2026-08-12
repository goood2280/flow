import polars as pl
import json
import os
import time
import logging
from pathlib import Path
from typing import Callable
from core.paths import PATHS
from core import request_priority

try:  # runtime_limits is optional in some minimal contexts (e.g. isolated tests)
    from core.runtime_limits import process_memory_high
except Exception:  # pragma: no cover - defensive import
    def process_memory_high(reserve_gb: float = 1.0) -> bool:  # type: ignore
        return False

try:  # parquet_perf 도 최소 컨텍스트에서는 없을 수 있다
    from core.parquet_perf import collect_streaming
except Exception:  # pragma: no cover - defensive import
    def collect_streaming(lf, fallback: bool = True):  # type: ignore
        return lf.collect()

logger = logging.getLogger(__name__)

CACHE_DIR = PATHS.db_cache_dir / "split_table" if hasattr(PATHS, "db_cache_dir") else Path("data/cache/split_table")

# Background builds run in chunks so an interactive read (SplitTable load, file
# view) always has spare RAM/CPU. When the process is already near its memory
# budget we shrink the chunk and wait longer between chunks so the reserved UI
# lane keeps working instead of tripping the memory guard.
# root 별 쓰기가 sink_parquet 스트리밍으로 바뀐 뒤로 청크 크기는 **읽는 양을
# 정하지 않는다** — 양보/메모리 확인/중단 확인을 얼마나 자주 할지, 그 주기만
# 정한다. 랏은 청크 안에서도 여전히 하나씩 스트리밍되므로 이 값을 올려도 피크
# 메모리는 그대로고, 대신 랏 사이 양보/중단 확인 횟수가 줄어 빌드가 빨라진다.
#
# 기본값이 역할별로 다른 이유는 메모리가 아니라 **양보 대상**이다. 운영서버는
# 사용자 검색과 경쟁하므로 자주 양보할수록 좋지만 30GB 호스트에서 랏 하나마다
# 최대 20초씩 쉬면 빌드가 끝나지 않는다 — 3 랏마다 확인으로 절충한다. 개발
# 워커(10GB)는 검색을 서빙하지 않으므로 1 랏마다 확인해 중단 반응성을 우선한다.
_CHUNK_SIZE_DEFAULT = 3
_CHUNK_SIZE_DEV_DEFAULT = 1
_CHUNK_SIZE_UNDER_MEMORY_PRESSURE = 1
PIVOT_BUILD_CHUNK_MIN = 1
PIVOT_BUILD_CHUNK_MAX = 256

# per-root 파일의 저장 포맷 세대. 포맷이 바뀌면(legacy 전치형 등) 시작 시 일괄
# 제거가 필요하지만, 같은 포맷의 재빌드는 기존 파일을 그대로 서빙하면서 root
# 단위로 원자 교체한다 — 재빌드 중 캐시 미스(전체 스캔 폴백) 구간이 없다.
_CACHE_FORMAT_VERSION = 2
_CACHE_FORMAT_MARKER = ".cache_format.json"

# 증분 재빌드용 per-root 내용 지문. 소스 ML_TABLE 이 갱신돼도 대부분의 root 는
# 내용이 그대로다 — 지문이 같은 root 는 collect/write 를 통째로 건너뛰어
# "root 몇 개 갱신 = 전체 재빌드(수 분)" 를 "변경 root 만(수 초)" 로 줄인다.
# 지문은 한 번의 스트리밍 스캔으로 계산하며, 파일이 없거나 스키마/해시 계산이
# 실패하면 None 을 반환해 기존 전체 재빌드로 폴백한다.
_ROOT_FINGERPRINT_FILE = ".root_fingerprints.json"
# 해시 합이 Int64 를 넘지 않도록 32bit 소수로 접는다 (root 당 수만 행이어도 여유).
_FINGERPRINT_FOLD_PRIME = 4294967291

# ── KNOB 전용 사이드카 ────────────────────────────────────────────────────
# SplitTable 에서 압도적으로 많이 보는 것이 KNOB_ prefix 다. 전체 per-root 파일은
# 모든 그룹(INLINE/VM/MASK/…)을 담고 있어 파일 열기(footer 파싱)와 컬럼 청크
# 읽기가 컬럼 수에 비례해 비싸다. KNOB 컬럼 + 앵커만 담은 좁은 파일을 함께 써
# 두면 prefix=KNOB 조회가 그 파일만 읽는다 (4000→2000컬럼 실측: 파일 열기
# 18.2→8.3ms, 읽기 22.2→14.1ms).
#
# **하위 폴더**에 둔다 — lot-candidates 가 캐시 폴더의 `*.parquet` stem 으로 랏
# 후보를 만들기 때문에 같은 폴더에 두면 "A1000.KNOB" 같은 가짜 랏이 뜬다.
# 사이드카가 없거나 필요한 컬럼이 빠져 있으면 읽기측이 전체 파일로 폴백하므로
# (구버전 캐시 포함) 이 파일이 결과를 바꿀 수 없다.
KNOB_SIDECAR_DIR = "knob"
_KNOB_PREFIX = "KNOB_"


def _knob_sidecar_columns(columns, anchors) -> list[str]:
    """KNOB 컬럼 + 앵커(root/lot/wafer/fab). 순서는 원본 유지."""
    keep_upper = {str(a).upper() for a in anchors if a}
    # fab lot 라벨/헤더 그룹핑에 쓰이는 컬럼도 함께 — 없으면 읽기측이 폴백한다.
    keep_upper |= {"FAB_LOT_ID", "FAB_LOTID", "FAB_LOT", "PRODUCT"}
    out = []
    for c in columns:
        cu = str(c).upper()
        if cu.startswith(_KNOB_PREFIX) or cu in keep_upper:
            out.append(c)
    # KNOB 컬럼이 하나도 없으면 사이드카를 만들 이유가 없다.
    if not any(str(c).upper().startswith(_KNOB_PREFIX) for c in out):
        return []
    return out


def _cache_format_matches(out_dir: Path) -> bool:
    try:
        meta = json.loads((out_dir / _CACHE_FORMAT_MARKER).read_text("utf-8"))
        return int(meta.get("format") or 0) == _CACHE_FORMAT_VERSION
    except Exception:
        return False


def _write_cache_format_marker(out_dir: Path) -> None:
    try:
        (out_dir / _CACHE_FORMAT_MARKER).write_text(
            json.dumps({"format": _CACHE_FORMAT_VERSION}), "utf-8"
        )
    except Exception:
        pass


def _safe_root_filename(root_id: str) -> str:
    return f"{str(root_id).replace('/', '_').replace(chr(92), '_')}.parquet"


def _compute_root_fingerprints(lf, key_expr, anchor_cols: list | None = None) -> dict | None:
    """Per-root (row_count, folded row-hash sum).

    ML_TABLE 은 행은 적고 컬럼이 수천 개인 wide 형이라, 전체 struct 해시는
    테이블 전체를 한 번에 메모리에 올린다(압축 19MB 파일에서 피크 1.3GB 실측).
    컬럼을 배치로 나눠 배치별 per-root 해시 합을 구하고 합산한다 — 피크가
    (배치 폭 × 행 수)로 제한된다. 각 배치 struct 에 anchor(wafer 키 등)를
    포함해 행 정렬이 배치 간에 뒤바뀌는 변경도 지문에 잡히게 한다.
    배치 폭이 바뀌면 지문값이 달라져 1회 전체 재빌드된다 (정확성 영향 없음).

    집계는 **스트리밍 엔진**으로 돈다. 산출물은 root 당 (행수, 해시합) 두 숫자
    뿐인데, 예전 in-memory collect 는 배치 폭만큼의 원본과 그 struct/hash 중간
    산출물을 통째로 물리화했다 — 44MB 합성 소스(150k 행 × 252 컬럼)에서 피크
    503.9MB 로, 같은 빌드의 root sink(9~20MB) 를 25 배 이상 압도해 pivot 빌드
    전체 피크를 이 패스 혼자 결정했다. 스트리밍은 같은 측정에서 185.8MB 다.
    **지문값은 바뀌지 않으므로** 이 변경으로 재빌드가 유발되지 않는다.

    기본 배치 폭 50 (2026-08-04, 종전 200). 배치를 좁혀도 **느려지지 않는다** —
    컬럼 투영이 밀려 각 패스가 그 배치의 컬럼만 읽으므로 패스 수가 늘어도 총
    읽는 바이트는 거의 같고, 늘어나는 건 footer 파싱·스케줄링 오버헤드뿐이다.
    2000 컬럼 소스 실측: 배치 500(4패스) 0.65s / 121.8MB, 배치 200(10패스)
    0.69s / 121.8MB, 배치 50(40패스) 0.76s / 0.5MB — 25 배의 패스 차이가 0.24 초다.
    **배치 폭을 바꾸면 지문값이 달라져 1 회 전체 재빌드가 뒤따른다**(위 문단 참고)."""
    batch_width = _int_env("FLOW_PIVOT_FINGERPRINT_COL_BATCH", 50, 20, 10000)
    # 호출측 컬럼 감지가 같은 컬럼을 중복 전달할 수 있다(root_col == lot_col 등)
    # — 중복 select 는 polars 에러이므로 반드시 dedupe.
    anchors = list(dict.fromkeys(c for c in (anchor_cols or []) if c))
    try:
        names = lf.collect_schema().names()
        value_cols = [c for c in names if c not in anchors]
        totals: dict[str, list[int]] = {}
        for start in range(0, len(value_cols), batch_width):
            batch = value_cols[start:start + batch_width]
            df = (
                lf.select(batch + anchors)
                .with_columns(key_expr.alias("__root"))
                .with_columns(
                    (pl.struct(pl.all().exclude("__root")).hash(seed=0)
                     % _FINGERPRINT_FOLD_PRIME).cast(pl.Int64).alias("__h")
                )
                .group_by("__root")
                .agg(pl.len().alias("n"), pl.col("__h").sum().alias("h"))
            )
            df = collect_streaming(df)
            for root, n, h in df.iter_rows():
                if root is None:
                    continue
                slot = totals.setdefault(str(root), [int(n), 0])
                slot[1] = (slot[1] + int(h or 0)) % (1 << 62)
        return totals
    except Exception as e:
        logger.warning("root fingerprint 계산 실패 — 전체 재빌드로 폴백: %s", e)
        return None


def _load_root_fingerprints(out_dir: Path) -> dict | None:
    try:
        data = json.loads((out_dir / _ROOT_FINGERPRINT_FILE).read_text("utf-8"))
        if int(data.get("format") or 0) != _CACHE_FORMAT_VERSION:
            return None
        roots = data.get("roots")
        return roots if isinstance(roots, dict) else None
    except Exception:
        return None


def _save_root_fingerprints(out_dir: Path, fingerprints: dict) -> None:
    try:
        (out_dir / _ROOT_FINGERPRINT_FILE).write_text(
            json.dumps({"format": _CACHE_FORMAT_VERSION, "roots": fingerprints}),
            "utf-8",
        )
    except Exception:
        pass


def _emit(product: str, event: str, *, ok: bool = True, detail: dict | None = None) -> None:
    """캐시 이벤트 로그로 진행/사유를 내보낸다.

    이 빌더는 `logger` 로만 말해서, 관리자 캐시관리 화면에는 "큐 등록 완료" 뒤로
    아무것도 안 나왔다 — 소스 없음/컬럼 없음 같은 조용한 `return False` 도 화면상
    구분이 안 됐다. 로그 수집기가 없는 컨텍스트(단독 테스트)에서도 빌드가 죽지
    않도록 실패는 통째로 삼킨다.
    """
    try:
        from core.cache_event_log import record as _rec
        _rec("cache_op", event, ok=ok, detail=detail or {}, product=product)
    except Exception:
        pass


def _stage(phase: str) -> dict:
    """제품별 캐시 이력 집계용 수명주기 표식. 수집기가 없으면 빈 dict."""
    try:
        from core.cache_event_log import stage_detail
        return stage_detail("pivot", phase)
    except Exception:
        return {}


def _progress(done: int, total: int, state: str = "running") -> dict:
    """전체 랏 진행률 집계용 표준 블록. 수집기가 없으면 빈 dict."""
    try:
        from core.cache_event_log import progress_detail
        return progress_detail("pivot", done, total, state=state)
    except Exception:
        return {}


def _progress_gap_sec() -> float:
    """진행 로그 최소 간격(초). root 가 수천 개여도 이벤트 로그를 덮지 않게 한다."""
    try:
        value = float(os.environ.get("FLOW_PIVOT_CACHE_LOG_GAP_SEC", "") or 5.0)
    except Exception:
        value = 5.0
    return max(0.0, min(120.0, value))


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except Exception:
        value = default
    return max(lo, min(hi, value))


def _use_dev_role() -> bool:
    """개발 워커 컨텍스트 여부. 판정 실패는 '운영' 으로 본다 — 역할을 모를 때
    빌드를 개발 기본값으로 느리게 돌리는 쪽이 더 나쁘다."""
    try:
        from core.ml_table_lookup import _root_ram_cache_use_dev
        return bool(_root_ram_cache_use_dev())
    except Exception:
        return False


def pivot_build_chunk_default() -> int:
    """단순화된 캐싱 속도의 기본 처리 단위. 운영/개발 모두 1 root."""
    return 1


def pivot_build_chunk_roots() -> int:
    """양보·메모리 확인·중단 확인 사이에 처리할 root 개수.

    우선순위: env(FLOW_PIVOT_CACHE_CHUNK_SIZE) > 캐시관리 톱니바퀴의
    단일 캐싱 속도(1~5단계) > 기본 1 root.
    호출할 때마다 다시 읽으므로 톱니바퀴 저장은 **다음 청크부터** 재시작 없이 적용된다.
    """
    default = pivot_build_chunk_default()
    if "FLOW_PIVOT_CACHE_CHUNK_SIZE" in os.environ:
        return _int_env("FLOW_PIVOT_CACHE_CHUNK_SIZE", default,
                        PIVOT_BUILD_CHUNK_MIN, PIVOT_BUILD_CHUNK_MAX)
    try:
        from core import cache_settings
        return cache_settings.cache_speed_chunk_roots(_use_dev_role())
    except Exception:
        pass
    return default


def _chunk_size(memory_pressured: bool) -> int:
    if memory_pressured:
        # 메모리 압박 중에는 설정값을 상한으로만 쓴다 — 관리자가 3 으로 올려
        # 뒀다고 해서 압박 상황에서까지 확인 주기를 늘리면 안 된다.
        pressured = _int_env(
            "FLOW_PIVOT_CACHE_CHUNK_SIZE_MIN", _CHUNK_SIZE_UNDER_MEMORY_PRESSURE, 1, 64
        )
        return max(PIVOT_BUILD_CHUNK_MIN, min(pressured, pivot_build_chunk_roots()))
    return pivot_build_chunk_roots()


def _cancel_requested(should_cancel) -> bool:
    """중단 요청 확인. 콜백이 없거나 터지면 '중단 아님' 으로 본다 —
    감시가 실패했다고 빌드를 죽이면 안 된다."""
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:
        return False


def _heartbeat(on_chunk_done) -> None:
    """청크 경계 keepalive. 호출측이 shared lease 를 갱신하는 자리다 —
    갱신에 실패해도 빌드는 계속한다(다음 청크에서 다시 시도)."""
    if on_chunk_done is None:
        return
    try:
        on_chunk_done()
    except Exception:
        pass


def _throttled_yield(memory_pressured: bool, should_cancel) -> bool:
    """청크 사이 사용자 양보. 중단이 걸려 있으면 True.

    `yield_to_users` 는 한 번에 최대 60초를 잠들 수 있어서, 중단을 눌러도 그만큼
    반응이 없었다. 총 대기 예산은 그대로 두고 5초 조각으로 나눠 조각 사이마다
    중단을 확인한다. 사용자 활동이 없으면 yield 가 0 을 돌려주므로 즉시 빠진다.
    """
    budget = 60.0 if memory_pressured else 20.0
    time.sleep(0.5 if memory_pressured else 0.1)
    waited = 0.0
    while waited < budget:
        if _cancel_requested(should_cancel):
            return True
        step = request_priority.yield_to_users(max_wait_sec=min(5.0, budget - waited))
        if step <= 0:
            break
        waited += step
    return _cancel_requested(should_cancel)


def canonical_product_dir(product: str) -> str:
    """Single naming rule for the pivot cache directory: full upper-cased
    ML_TABLE_* name, whether callers pass "PRODA" or "ml_table_proda"."""
    raw = str(product or "").strip()
    if not raw:
        return ""
    if raw.casefold().startswith("ml_table_"):
        raw = raw[len("ML_TABLE_"):]
    return f"ML_TABLE_{raw}".upper() if raw else ""


def _detect_col(columns, exact: str, contains: str = "") -> str | None:
    """Case-insensitive column detection (source ML_TABLE uses UPPER_CASE names)."""
    hit = next((c for c in columns if c.lower() == exact.lower()), None)
    if hit is None and contains:
        hit = next((c for c in columns if contains.lower() in c.lower()), None)
    return hit


def build_pivoted_cache_for_product(
    product: str,
    db_root: Path = None,
    product_path: Path = None,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_chunk_done: Callable[[], None] | None = None,
):
    """
    Builds per-root Parquet caches for a specific product, one file per real
    root_lot_id, for instantaneous loading in SplitTable.

    `should_cancel` / `on_chunk_done` 은 **청크 경계에서만** 불린다 (root 단위
    tmp→replace 가 끝난 뒤). 중간에서 끊지 않으므로 캐시가 깨지지 않는다.
      - should_cancel(): True 면 그 자리에서 접고 False 를 반환한다. 지문 저장과
        stale 정리를 건너뛰므로 이미 쓴 root 는 그대로 서빙되고, 남은 root 는
        다음 빌드가 다시 잡는다.
      - on_chunk_done(): 호출측 keepalive (shared lease 갱신). 이게 없으면 30분
        TTL 이 만료돼 다른 서버가 같은 제품을 동시에 빌드한다.
    둘 다 None 이면 종전과 완전히 같은 동작이다.

    v9.2: Stored in **native wide orientation** (wafer rows × parameter columns),
    exactly like the source ML_TABLE, instead of transposed (parameter rows ×
    wafer columns).  This lets the view fast-path use parquet **column
    projection** to read only the requested prefix (KNOB_/FAB_/…) or the custom
    columns — an index-speed read (~1-2ms) instead of loading a 3000-wide pivot.
    It also preserves LOT_ID so the "latest lot" label resolves instead of "-".

    The partition key is the **real** ROOT_LOT_ID (case-insensitive detection);
    previously a lowercase-only check silently missed UPPERCASE ROOT_LOT_ID and
    keyed files by a LOT_ID-prefix derivation, so fast-path lookups never hit.
    """
    canonical = canonical_product_dir(product)
    if not canonical:
        _emit(str(product or ""), "[Pivot캐시] 빌드 불가 — 제품명을 해석할 수 없습니다", ok=False)
        return False

    if product_path is None:
        if db_root is None:
            db_root = PATHS.db_root if hasattr(PATHS, "db_root") else Path("data/db")
        product_path = db_root / f"{canonical}.parquet"
    if not product_path.exists():
        _emit(canonical, f"[Pivot캐시] 빌드 불가 — 원본 parquet 이 없습니다: {product_path}",
              ok=False, detail={"source": str(product_path), "stage": _stage("fail")})
        return False

    out_dir = CACHE_DIR / canonical
    out_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.monotonic()
    try:
        lf = pl.scan_parquet(product_path)
        schema = lf.collect_schema()
        columns = schema.names()

        # Case-insensitive detection — source columns are UPPER_CASE.
        root_col = _detect_col(columns, "root_lot_id")
        lot_col = _detect_col(columns, "lot_id", contains="lot")
        wf_col = _detect_col(columns, "wafer_id", contains="wafer")
        if not wf_col:
            _emit(canonical, "[Pivot캐시] 빌드 불가 — 원본에 wafer 컬럼이 없습니다", ok=False,
                  detail={"source": str(product_path), "stage": _stage("fail")})
            return False

        # Root key expression: prefer the authoritative ROOT_LOT_ID; only fall
        # back to a LOT_ID-prefix derivation when no root column exists at all.
        if root_col:
            key_expr = pl.col(root_col).cast(pl.Utf8)
        elif lot_col:
            key_expr = pl.col(lot_col).cast(pl.Utf8).str.split(".").list.first()
        else:
            _emit(canonical, "[Pivot캐시] 빌드 불가 — 원본에 root_lot_id/lot_id 컬럼이 없습니다",
                  ok=False, detail={"source": str(product_path), "stage": _stage("fail")})
            return False

        # 같은 포맷의 재빌드는 기존 per-root 파일을 지우지 않는다 — 빌드가 도는
        # 동안 SplitTable 조회는 이전 파일을 계속 서빙하고, root 단위 tmp→replace
        # 로만 새 데이터로 교체된다. 포맷 세대가 다른 legacy 캐시(전치형/잘못된
        # 키)만 시작 시 일괄 제거해 신·구 포맷이 섞이지 않게 한다.
        knob_dir = out_dir / KNOB_SIDECAR_DIR
        knob_cols = _knob_sidecar_columns(columns, (root_col, lot_col, wf_col))

        if not _cache_format_matches(out_dir):
            for stale in list(out_dir.glob("*.parquet")) + list(knob_dir.glob("*.parquet")):
                try:
                    stale.unlink()
                except Exception:
                    pass
            _write_cache_format_marker(out_dir)

        # 지문 패스는 컬럼 배치마다 원본을 한 번씩 훑으므로(4000 컬럼이면 20 회)
        # 시작 전에 한 번 접을 기회를 준다 — 여기서 놓치면 중단이 그 시간만큼 늦다.
        if _cancel_requested(should_cancel):
            _emit(canonical, f"[Pivot캐시] {canonical} 중단됨 — 시작 전 관리자 중단",
                  ok=False, detail={"cancelled": True, "stage": _stage("fail")})
            return False

        # 증분 재빌드: per-root 지문이 이전 빌드와 같고 파일도 살아 있으면 그
        # root 는 건너뛴다. 지문 계산 실패(None)면 전체 재빌드.
        # wide ML_TABLE 의 지문 패스는 일시적으로 수백 MB 를 쓸 수 있으므로,
        # 이미 메모리 압박이면 생략하고 메모리 안전한 청크 전체 재빌드로 간다.
        if process_memory_high():
            logger.info("root fingerprint 생략 (메모리 압박) — 청크 전체 재빌드: %s", canonical)
            fingerprints = None
        else:
            fingerprints = _compute_root_fingerprints(
                lf, key_expr,
                anchor_cols=[c for c in (root_col, lot_col, wf_col) if c])
        if fingerprints is not None:
            unique_roots = list(fingerprints.keys())
            previous = _load_root_fingerprints(out_dir) or {}
            # 지문 기록 이후에 다시 기록된 per-root 파일은 빌더 외부에서 쓰인
            # 것이다(테스트 픽스처/수동 조작 등) — 지문이 같아도 재빌드해서
            # self-heal 한다. 전체 재빌드 시절에는 매 빌드가 모든 파일을 덮어써
            # 자연 치유됐던 속성을 증분에서도 유지한다.
            try:
                fingerprints_mtime = (out_dir / _ROOT_FINGERPRINT_FILE).stat().st_mtime
            except OSError:
                fingerprints_mtime = None

            def _root_needs_build(r: str) -> bool:
                if previous.get(r) != fingerprints[r]:
                    return True
                try:
                    st = (out_dir / _safe_root_filename(r)).stat()
                except OSError:
                    return True
                # KNOB 사이드카가 아직 없는 root(구버전 캐시)는 내용이 그대로여도
                # 한 번 다시 써서 backfill 한다 — 증분 빌드가 계속 건너뛰면
                # 사이드카가 영영 안 생긴다.
                if knob_cols and not (knob_dir / _safe_root_filename(r)).exists():
                    return True
                return fingerprints_mtime is not None and st.st_mtime > fingerprints_mtime + 1.0

            build_roots = [r for r in unique_roots if _root_needs_build(r)]
        else:
            unique_roots = (
                lf.select(key_expr.alias("__root")).unique().collect()["__root"]
                .drop_nulls().to_list()
            )
            build_roots = list(unique_roots)

        try:
            catalog_cols = [c for c in (root_col, lot_col, _detect_col(columns, "fab_lot_id", "fab_lot")) if c]
            if catalog_cols:
                cat_df = lf.select([pl.col(c).drop_nulls().unique() for c in catalog_cols]).collect()
                catalog = {c: cat_df[c].drop_nulls().to_list() for c in catalog_cols}
                import json
                with open(out_dir / "_lot_catalog.json", "w", encoding="utf-8") as f:
                    json.dump(catalog, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to build lot catalog for {canonical}: {e}")

        import gc
        partitions_built = 0

        i = 0
        total_roots = len(build_roots)
        skipped_roots = len(unique_roots) - total_roots
        _emit(canonical,
              f"[Pivot캐시] {canonical}: {total_roots}/{len(unique_roots)} root 빌드 시작"
              + (f" · 변경 없음 {skipped_roots} root 건너뜀" if skipped_roots > 0 else ""),
              detail={"build_roots": total_roots, "total_roots": len(unique_roots),
                      "skipped_roots": max(0, skipped_roots),
                      # 진행률의 분모는 **제품의 전 랏**이다. 증분 빌드에서 이번에
                      # 다시 쓸 랏만 세면 "8랏 중 8랏 완료"처럼 보여서, 제품이 실제
                      # 몇 랏짜리인지가 화면에서 사라진다. 이미 캐시된 랏은 완료로
                      # 친다 — 관리자가 묻는 건 "캐시가 몇 랏 됐나"이지
                      # "이번 빌드가 몇 랏 썼나"가 아니다.
                      "progress": _progress(max(0, skipped_roots), len(unique_roots)),
                      "stage": _stage("start")})
        gap = _progress_gap_sec()
        last_progress = time.monotonic()
        while i < total_roots:
            # 중단은 청크 경계에서만 본다 — root 단위 tmp→replace 사이를 끊지
            # 않으므로 이미 쓴 캐시는 온전하다. 지문은 아래 성공 경로에서만
            # 저장하므로, 접고 나가면 남은 root 를 다음 빌드가 그대로 다시 잡는다.
            if _cancel_requested(should_cancel):
                _emit(canonical,
                      f"[Pivot캐시] {canonical} 중단됨 — {partitions_built}/{total_roots} root 기록 후 관리자 중단",
                      ok=False,
                      detail={"built": partitions_built, "total_roots": len(unique_roots),
                              "cancelled": True,
                              "progress": _progress(max(0, skipped_roots) + partitions_built,
                                                    len(unique_roots)),
                              "stage": _stage("fail")})
                logger.info("pivot cache build cancelled for %s (%d/%d roots written)",
                            canonical, partitions_built, total_roots)
                return False

            # 매 청크 전에 현재 메모리 여유를 확인해 청크 크기를 조절한다. 백그라운드
            # 빌드가 메모리 보호 임계값을 건드려 기본 UI 작업을 막지 않도록 한다.
            memory_pressured = process_memory_high()
            chunk_size = _chunk_size(memory_pressured)
            chunk_roots = build_roots[i:i + chunk_size]
            i += chunk_size

            # root 하나씩 lazy 필터 → sink. `collect()` 를 거치지 않으므로 수천 컬럼
            # 짜리 root 프레임이 파이썬에 올라오지 않고, 예전 경로가 피크 시점에
            # 동시에 들고 있던 사본 3 개(partition_by dict · drop · KNOB select)도
            # 함께 사라진다. 기록되는 parquet 내용은 종전과 동일하다.
            #
            # `__root` 헬퍼 컬럼은 더 이상 만들지 않는다 — 예전엔 partition_by 의
            # 키가 필요해서 붙였다가 쓰기 직전에 drop 했다. root 별로 직접 거르는
            # 지금은 붙일 이유가 없다(원본에 root 컬럼이 없는 LOT_ID 파생 경로 포함).
            for root_id_str in chunk_roots:
                root_id_str = str(root_id_str or "")
                if not root_id_str:
                    continue

                safe_root = root_id_str.replace("/", "_").replace("\\", "_")
                tmp_path = out_dir / f"{safe_root}.tmp.parquet"
                final_path = out_dir / f"{safe_root}.parquet"

                root_lf = lf.filter(key_expr == root_id_str)
                root_lf.sink_parquet(tmp_path)

                # 예전 partition_by 경로는 행이 없는 root 의 파일을 아예 만들지
                # 않았다. sink 는 빈 파일도 쓰므로 footer(메타데이터)만 읽어
                # 0 행이면 버린다 — 빈 캐시가 정상 캐시로 서빙되면 안 된다.
                try:
                    empty = int(pl.scan_parquet(tmp_path).select(pl.len()).collect().item()) <= 0
                except Exception:
                    empty = False
                if empty:
                    tmp_path.unlink(missing_ok=True)
                    continue

                tmp_path.replace(final_path)
                partitions_built += 1

                # KNOB 전용 사이드카 — 실패해도 전체 파일이 정답이므로 조용히 넘어간다.
                # 소스는 **방금 쓴 per-root 파일**이다. 예전에는 원본(수백 MB)을
                # root 마다 한 번씩 다시 필터링했는데, 바로 위에서 그 root 의 전체
                # 데이터를 final_path 에 이미 써 둔 상태라 다시 훑을 이유가 없었다.
                # root 파일은 해당 root 분량뿐이라 읽는 양이 원본이 아니라 root 크기에
                # 비례한다 — 44MB 합성 소스 실측 13.5MB → 2.0MB, 원본 풀스캔도 root
                # 수만큼 사라진다. knob_cols 는 원본 스키마에서 뽑았고 final_path 는
                # 원본의 전 컬럼을 그대로 담으므로 컬럼이 빠질 수 없다.
                if knob_cols:
                    try:
                        knob_dir.mkdir(parents=True, exist_ok=True)
                        knob_tmp = knob_dir / f"{safe_root}.tmp.parquet"
                        pl.scan_parquet(final_path).select(knob_cols).sink_parquet(knob_tmp)
                        knob_tmp.replace(knob_dir / f"{safe_root}.parquet")
                    except Exception as exc:
                        logger.debug("KNOB 사이드카 기록 실패 (%s/%s): %s", canonical, safe_root, exc)

            gc.collect()

            # lease keepalive — 30 분 TTL 을 청크마다 밀어 준다. 이게 없으면 30 분
            # 넘는 빌드에서 lease 가 stale 로 판정돼 다른 서버가 같은 제품을 동시에
            # 빌드한다(피크 메모리 2 배 + 같은 out_dir 쓰기 경합).
            _heartbeat(on_chunk_done)

            # 진행률 — 마지막 root 는 완료 로그가 따로 남으므로 중간만 내보낸다.
            now_mono = time.monotonic()
            if i < total_roots and (gap <= 0 or now_mono - last_progress >= gap):
                last_progress = now_mono
                pct = int(partitions_built * 100 / total_roots) if total_roots else 100
                _emit(canonical,
                      f"[Pivot캐시] {canonical}: {partitions_built}/{total_roots} root ({pct}%)"
                      + (" · 메모리 압박으로 감속" if memory_pressured else ""),
                      detail={"built": partitions_built, "total": total_roots,
                              "memory_pressured": bool(memory_pressured),
                              "progress": _progress(max(0, skipped_roots) + partitions_built,
                                                    len(unique_roots))})

            # API 우선 처리 — 사용자 요청이 진행 중이면 다음 청크를 미룬다.
            # 메모리가 빠듯하면 더 길게 쉬면서 RSS 가 내려갈 시간을 준다.
            # 대기 중에도 중단을 확인한다 (그러지 않으면 최대 60 초 무반응).
            if _throttled_yield(memory_pressured, should_cancel):
                _emit(canonical,
                      f"[Pivot캐시] {canonical} 중단됨 — {partitions_built}/{total_roots} root 기록 후 관리자 중단",
                      ok=False,
                      detail={"built": partitions_built, "total_roots": len(unique_roots),
                              "cancelled": True,
                              "progress": _progress(max(0, skipped_roots) + partitions_built,
                                                    len(unique_roots)),
                              "stage": _stage("fail")})
                logger.info("pivot cache build cancelled for %s (%d/%d roots written)",
                            canonical, partitions_built, total_roots)
                return False

        # 소스에서 사라진 root 의 stale 파일은 전체 빌드가 끝난 뒤에만 정리한다.
        # 빌드 실패 시에는 이 지점에 도달하지 않으므로 이전 파일이 그대로 남아
        # 다음 성공 빌드까지 계속 서빙된다.
        expected = {_safe_root_filename(r) for r in unique_roots}
        for stale in out_dir.glob("*.parquet"):
            if stale.name not in expected:
                try:
                    stale.unlink()
                except Exception:
                    pass

        # 성공적으로 끝난 빌드만 지문을 기록한다 — 중간 실패 시 이전 지문이
        # 남아 다음 빌드가 변경 root 를 다시 잡는다.
        if fingerprints is not None:
            _save_root_fingerprints(out_dir, fingerprints)

        elapsed = time.monotonic() - start_time
        logger.info("Built pivoted cache for %s (%d/%d roots, %d unchanged skipped) in %.2fs",
                    canonical, partitions_built, len(unique_roots),
                    len(unique_roots) - len(build_roots), elapsed)
        _emit(canonical,
              f"[Pivot캐시] {canonical} 완료 — {partitions_built}/{len(unique_roots)} root 기록"
              + (f" · 변경 없음 {skipped_roots} root 건너뜀" if skipped_roots > 0 else "")
              + f" · {elapsed:.1f}s",
              detail={"built": partitions_built, "total_roots": len(unique_roots),
                      "skipped_roots": max(0, skipped_roots), "elapsed_sec": round(elapsed, 2),
                      "progress": _progress(len(unique_roots), len(unique_roots), state="done"),
                      "stage": _stage("done")})
        return True
    except Exception as e:
        logger.error("Failed to build pivot cache for %s: %s", canonical, e)
        _emit(canonical, f"[Pivot캐시] {canonical} 빌드 실패 — {e}", ok=False,
              detail={"error": str(e), "stage": _stage("fail")})
        return False

def get_pivoted_cache_path(product: str, root_lot_id: str) -> Path:
    safe_root = str(root_lot_id).replace("/", "_").replace("\\", "_")
    return CACHE_DIR / canonical_product_dir(product) / f"{safe_root}.parquet"
