"""Declarative registry of 11 Flow-i Unit AIs.

Order matches the 11 feature markdown files in
`data/flow-data/flowi_agent_features/`. Each unit AI's key matches the
corresponding md filename (`filebrowser.md` -> key `filebrowser`).

M1 registers metadata only — `handle()` returns None and the existing
`_run_flowi_chat` if/elif chain runs unchanged.
"""
from __future__ import annotations

from pathlib import Path

from core.flowi_units.base import (
    BaseUnitAI,
    CodeRef,
    DataSourceRef,
    SemanticBindings,
    UnitAI,
)
from core.flowi_units.dashboard import DashboardUnitAI
from core.flowi_units.filebrowser import FileBrowserUnitAI
from core.flowi_units.inform import InformUnitAI
from core.flowi_units.meeting import MeetingUnitAI
from core.flowi_units.remaining_features import (
    CalendarUnitAI,
    DiagnosisUnitAI,
    EttimeUnitAI,
    SplitTableUnitAI,
    TableMapUnitAI,
    WaferLayoutUnitAI,
)
from core.flowi_units.tracker import TrackerUnitAI

_BACKEND_CORE_DIR = Path(__file__).resolve().parent.parent


_UNIT_AI_CLASSES = (
    FileBrowserUnitAI,
    MeetingUnitAI,
    InformUnitAI,
    TrackerUnitAI,
    DashboardUnitAI,
    SplitTableUnitAI,
    TableMapUnitAI,
    DiagnosisUnitAI,
    CalendarUnitAI,
    EttimeUnitAI,
    WaferLayoutUnitAI,
)


UNIT_AIS: dict[str, UnitAI] = {cls.KEY: cls() for cls in _UNIT_AI_CLASSES}


def get_unit_ai(key: str) -> UnitAI | None:
    return UNIT_AIS.get(key)


def all_unit_ais() -> list[UnitAI]:
    return list(UNIT_AIS.values())
