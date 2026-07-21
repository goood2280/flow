"""core/worker_tasks.py — 워커(개발서버)가 실행하는 오프로드 작업 핸들러.

core.worker_dispatch.start_services() 가 worker 역할일 때 import 하며, import
부수효과로 핸들러가 등록된다. 새 오프로드 작업을 추가하려면 여기에
@handler("...") 함수를 더하고, 호출부는 worker_dispatch.run_heavy() 를 쓴다.

규칙:
  - 기본은 결과가 shared workspace 파일로 남는 작업 (pivot 캐시, 인덱스,
    파티션 등). 프로세스 RAM 캐시 워밍은 서버별 자원이라 오프로드 대상이 아니다.
  - 예외적으로 응답-페이로드 작업도 허용하되, ①지연 허용(자체 처리 시간이
    큐 왕복 ~1초를 압도)이고 ②부수 상태가 전부 공유 data_root 에 남아 어느
    서버가 실행해도 이후 요청을 서빙할 수 있는 작업만 (예: flowi_chat_turn —
    LLM 대기가 지배, 차트 세션/유저 이벤트는 공유 파일).
  - 핸들러 반환값은 JSON 직렬화 가능한 작은 dict — 큰 데이터는 파일로 남기고
    경로/통계만 돌려준다.
  - cross-server 이중 실행 가드(shared_lease / 빌드 락)는 핸들러 안에서
    실행측이 잡는다 — api 서버가 로컬 폴백할 때도 같은 가드를 지나므로
    최악의 경쟁도 기존과 동일하게 수렴한다.
  - 무거운 모듈은 함수 안에서 lazy import (앱 전반의 관례).
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.worker_dispatch import handler

logger = logging.getLogger("flow.worker_tasks")


@handler("ping")
def _ping(payload: dict) -> dict:
    """헬스/배선 점검용 — 페이로드를 그대로 돌려준다."""
    return {"ok": True, "pong": payload.get("echo", "")}


@handler("splittable_pivot_build")
def _splittable_pivot_build(payload: dict) -> dict:
    """SplitTable product pivot 캐시 재빌드 (결과: db_root/cache/split_table/...)."""
    product = str(payload.get("product") or "").strip()
    if not product:
        return {"ok": False, "error": "missing product"}
    source_raw = str(payload.get("product_path") or "").strip()
    source_fp = Path(source_raw) if source_raw else None
    from core import shared_lease
    lease_name = f"splittable_pivot_{product}"
    if not shared_lease.try_acquire(lease_name, ttl_sec=1800.0):
        return {"ok": False, "error": f"lease_held:{shared_lease.holder(lease_name)}"}
    try:
        from app_v2.modules.splittable.cache_builder import build_pivoted_cache_for_product
        ok = bool(build_pivoted_cache_for_product(product, product_path=source_fp))
        return {"ok": ok, "product": product}
    finally:
        shared_lease.release(lease_name)


@handler("splittable_fab_lot_index_build")
def _splittable_fab_lot_index_build(payload: dict) -> dict:
    """SplitTable fab lot index (재)빌드 (결과: 공유 인덱스 파일 + meta json)."""
    product = str(payload.get("product") or "").strip()
    if not product:
        return {"ok": False, "error": "missing product"}
    fab_source = str(payload.get("fab_source") or "")
    include_all = bool(payload.get("include_all"))
    from core import shared_lease
    lease_name = f"splittable_fabidx_{product}"
    if not shared_lease.try_acquire(lease_name, ttl_sec=1800.0):
        return {"ok": False, "error": f"lease_held:{shared_lease.holder(lease_name)}"}
    try:
        from routers.splittable import _build_fab_lot_index
        ok = bool(_build_fab_lot_index(product, fab_source, include_all))
        return {"ok": ok, "product": product}
    finally:
        shared_lease.release(lease_name)


@handler("flowi_chat_turn")
def _flowi_chat_turn(payload: dict) -> dict:
    """Flow-i 채팅 턴 실행 (데이터 컨텍스트 빌드 + LLM 호출).

    응답-페이로드 위임의 예외 케이스 — LLM 대기가 지배해 큐 왕복이 체감되지
    않고, 부수 상태(차트 세션·유저 이벤트 md·활동 로그)는 공유 data_root 에
    남는다. HTTPException 은 http_error 봉투로 돌려 api 가 로컬 재실행 없이
    그대로 변환한다 (LLM 이중 호출·이중 이벤트 기록 방지)."""
    from fastapi import HTTPException

    from core import llm_adapter
    from routers.llm import _run_flowi_chat

    # LLM 설정은 공유 admin_settings.json 이 기본이지만 API 키가 운영 env 에만
    # 있는 배포도 있다 — 이 워커에서 LLM 이 불가하면 실행하지 않고 돌려보내
    # api 가 로컬로 돌게 한다 (저품질 "LLM 미설정" 응답이 조용히 나가는 것 방지).
    if not llm_adapter.is_available():
        return {"ok": False, "error": "llm_unavailable_on_worker"}

    agent_context = payload.get("agent_context")
    try:
        result = _run_flowi_chat(
            prompt=str(payload.get("prompt") or ""),
            product=str(payload.get("product") or ""),
            max_rows=int(payload.get("max_rows") or 0),
            me=payload.get("me") or {},
            source_ai=str(payload.get("source_ai") or ""),
            client_run_id=str(payload.get("client_run_id") or ""),
            agent_context=agent_context if isinstance(agent_context, dict) else None,
            allow_rag_update=bool(payload.get("allow_rag_update")),
        )
        return {"ok": True, "result": result}
    except HTTPException as exc:
        return {"ok": True, "http_error": {"status": int(exc.status_code), "detail": exc.detail}}


@handler("lot_progress_cache_refresh")
def _lot_progress_cache_refresh(payload: dict) -> dict:
    """LOT progress(latest lot) 캐시 재스캔 (결과: 공유 lot_progress 캐시 JSON).

    refresh_lot_progress_cache 자체가 cross-process refresh lock + fresh-file
    스킵을 갖고 있어 그대로 위임한다 — api 가 로컬 폴백해도 같은 락을 지난다.
    items 목록은 크므로 요약만 반환 (본문은 공유 캐시 파일로 전달)."""
    from core import lot_progress_cache

    raw_age = payload.get("max_age_seconds")
    try:
        max_age = int(raw_age) if raw_age else None
    except Exception:
        max_age = None
    state = lot_progress_cache.load_lot_progress_cache(max_age_seconds=max_age)
    return {
        "ok": bool(state.get("generated_at")),
        "count": int(state.get("count") or 0),
        "files_scanned": int(state.get("files_scanned") or 0),
        "generated_at": str(state.get("generated_at") or ""),
        "skipped_by_lock": bool(state.get("skipped_by_lock")),
    }


@handler("ml_lookup_cache_build")
def _ml_lookup_cache_build(payload: dict) -> dict:
    """ML_TABLE root_lot 파티션 lookup 캐시 빌드 (결과: db cache 파티션 트리).

    build_lookup_cache 자체가 cross-server 빌드 락 + fresh 스킵을 갖고 있어
    그대로 위임한다."""
    source_raw = str(payload.get("source_path") or "").strip()
    if not source_raw:
        return {"ok": False, "error": "missing source_path"}
    fp = Path(source_raw)
    if not fp.is_file():
        return {"ok": False, "error": f"source not found: {source_raw}"}
    from core import ml_table_lookup
    res = ml_table_lookup.build_lookup_cache(fp, force=bool(payload.get("force")))
    return {
        "ok": bool(res.get("ok")),
        "skipped": bool(res.get("skipped")),
        "reason": str(res.get("reason") or ""),
        "cache_dir": str(res.get("cache_dir") or ""),
        "build_seconds": (res.get("meta") or {}).get("build_seconds"),
    }
