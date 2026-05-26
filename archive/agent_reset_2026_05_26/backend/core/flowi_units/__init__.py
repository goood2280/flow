"""Flow-i Unit AIs — declarative registry of 11 Feature-level Unit AIs.

The Agent tab uses this registry to render a 4-layer view
(Data Sources / Semantic Layer / LLM / Results) per unit AI.

M1 (PR #1): metadata-only scaffold. _run_flowi_chat in backend/routers/llm.py
is untouched, so existing behavior is unchanged.
M2 (PR #2~#7): per-feature handle() wiring via delegation.
M5 (PR #11): _run_flowi_chat dead path removal.
"""
from core.flowi_units.base import (
    BaseUnitAI,
    CodeRef,
    ColumnDoc,
    DataSourceRef,
    SemanticBindings,
    UnitAI,
)
from core.flowi_units.dispatcher import try_dispatch
from core.flowi_units.registry import UNIT_AIS, all_unit_ais, get_unit_ai

__all__ = [
    "BaseUnitAI",
    "CodeRef",
    "ColumnDoc",
    "DataSourceRef",
    "SemanticBindings",
    "UNIT_AIS",
    "UnitAI",
    "all_unit_ais",
    "get_unit_ai",
    "try_dispatch",
]
