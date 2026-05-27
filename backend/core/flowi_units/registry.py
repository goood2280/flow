"""Minimal Unit AI registry after the Agent reset."""
from __future__ import annotations

from core.flowi_units.base import UnitAI
from core.flowi_units.change_management import ChangeManagementUnitAI
from core.flowi_units.filebrowser import FileBrowserAiSqlUnitAI
from core.flowi_units.inform_registration import InformRegistrationUnitAI

_UNIT_AI_CLASSES = (
    FileBrowserAiSqlUnitAI,
    InformRegistrationUnitAI,
    ChangeManagementUnitAI,
)

UNIT_AIS: dict[str, UnitAI] = {cls.KEY: cls() for cls in _UNIT_AI_CLASSES}


def get_unit_ai(key: str) -> UnitAI | None:
    return UNIT_AIS.get(key)


def all_unit_ais() -> list[UnitAI]:
    return list(UNIT_AIS.values())
