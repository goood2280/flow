"""WIP 현재위치 Unit AI — latest lot progress cache 기반 결정적 조회.

홈 에이전트(Flow-i)가 "AAAAA 지금 어디 있어", "A1000 #3 어느 step 이야" 류
질문을 LLM 없이 처리한다. 실행 로직은 `core.lot_wip` 에 있고 이 파일은 도구
카탈로그(오케스트레이터가 보는 계약) 쪽 껍데기다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


class LotWipUnitAI(BaseUnitAI):
    KEY = "lot_wip"
    TITLE = "WIP 현재위치 (lot/제품)"
    DESCRIPTION = (
        "전 제품 WIP latest cache 로 '지금 어디 있어 / 어느 step 이야' 에 답한다. "
        "제품명이면 step 별 WIP 분포를, 랏이면 wafer 별 현재 step 을 돌려주고 "
        "step_id 는 Vehicle_matching.csv 의 step_desc 와 합쳐서 말한다. "
        "답에는 항상 BigQuery 적재 지연 고지가 붙는다."
    )
    LLM_PROFILE = "deterministic latest-cache lookup; no LLM"
    DATA_SOURCES = (
        DataSourceRef(
            kind="cache",
            path="{data_root}/cache/lot_progress/lot_wf_current.json",
            description="FAB DB 전 제품 LOT_WF 현재위치 latest cache (product, root_lot_id, wafer_id, step_id, function_step).",
        ),
        DataSourceRef(
            kind="matching_csv",
            path="FLOW_DB_ROOT/Vehicle_matching.csv",
            description="vehicle, product, step_id, step_desc — step_id 를 사람이 읽는 이름으로.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.lot_wip",
        function="answer_wip",
        description="prompt 의 제품/랏 토큰을 캐시 실데이터와 대조해 현재 위치를 답한다",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "product": {"type": "string"},
            "max_rows": {"type": "integer"},
        },
        "required": ["prompt"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "handled": {"type": "boolean"},
            "answer": {"type": "string"},
            "delay_notice": {"type": "string"},
            "table": {"type": "object"},
            "step_groups": {"type": "array"},
        },
        "required": ["handled"],
    }
    EXAMPLES = (
        {"prompt": "AAAAA 지금 어디 있어"},
        {"prompt": "A1000 지금 어느 step 이야"},
        {"prompt": "A1000 #3 현재 위치"},
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/flowi-agent.md")

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        from core import lot_wip

        try:
            max_rows = int((slots or {}).get("max_rows") or 30)
        except Exception:
            max_rows = 30
        return lot_wip.answer_wip(
            str(prompt or ""),
            product=str((slots or {}).get("product") or ""),
            max_rows=max(1, min(max_rows, 200)),
        )
