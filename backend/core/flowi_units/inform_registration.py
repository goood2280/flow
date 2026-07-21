"""Inform registration Unit AI metadata for the Agent unit tab."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from core.flowi_units.base import BaseUnitAI, CodeRef, DataSourceRef


class InformRegistrationUnitAI(BaseUnitAI):
    KEY = "inform_registration"
    TITLE = "Inform 등록 도우미"
    DESCRIPTION = (
        "자연어로 Inform 등록 slot을 채우고, 누락값은 short memory session으로 이어 물은 뒤 "
        "명시 확인 때만 Inform을 저장한다."
    )
    LLM_PROFILE = "deterministic slot extract + optional SplitTable snapshot preview"
    DATA_SOURCES = (
        DataSourceRef(
            kind="inform_store",
            path="FLOW_DATA_ROOT/informs/informs.json",
            description="Confirm action only writes through routers.informs.create_inform().",
        ),
        DataSourceRef(
            kind="short_memory",
            path="FLOW_DATA_ROOT/agent_unit_ai_sessions/inform_registration/*.json",
            description="One-hour registration conversation state.",
        ),
        DataSourceRef(
            kind="splittable_snapshot",
            path="SplitTable view pipeline",
            description="Optional KNOB/CUSTOM/set snapshot embedded only when requested.",
        ),
    )
    HANDLER_ENTRY = CodeRef(
        module="backend.core.flowi_units.inform_registration_runtime",
        function="run_inform_registration_runtime",
        description="context_seed -> slot_extract -> validate_missing -> snapshot_preview -> review -> register",
    )
    INPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "session_id": {"type": "string"},
            "action": {"type": "string", "enum": ["continue", "confirm", "cancel"]},
            "slot_overrides": {"type": "object"},
        },
        "required": ["prompt"],
    }
    OUTPUT_SCHEMA = {
        "type": "object",
        "properties": {
            "status": {"type": "string"},
            "missing": {"type": "array"},
            "slots": {"type": "object"},
            "draft": {"type": "object"},
            "requires_confirmation": {"type": "boolean"},
            "created_inform": {"type": "object"},
            "trace": {"type": "array"},
        },
        "required": ["status", "missing", "slots", "draft", "requires_confirmation", "trace"],
    }
    EXAMPLES = (
        {
            "prompt": "PRODA A1000 GATE 이상 내용으로 alice@example.test에 Inform 준비",
            "slot_overrides": {"product": "PRODA", "lot_id": "A1000", "module": "GATE"},
        },
        {
            "prompt": "KNOB_GATE snapshot도 붙여서 등록 준비",
            "session_id": "inform_reg_xxx",
            "slot_overrides": {"snapshot_custom_cols": ["KNOB_GATE"]},
        },
    )

    def feature_md_path(self) -> Path:
        return Path("docs/features/inform.md")

    def prompt_template_path(self) -> Optional[Path]:
        return None

    def handle(
        self,
        prompt: str,
        slots: dict[str, Any],
        ctx: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        return None
