from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.paths import PATHS
from core.utils import load_json, save_json


ADAPTERS_FILE = PATHS.data_root / "adapters" / "profiles.json"


@dataclass(slots=True)
class AdapterResolution:
    canonical: str
    matched: str
    strategy: str


def load_profiles() -> dict:
    data = load_json(ADAPTERS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_profiles(data: dict) -> None:
    ADAPTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    save_json(ADAPTERS_FILE, data if isinstance(data, dict) else {})


def get_profile(profile_name: str = "default") -> dict:
    profiles = load_profiles()
    prof = profiles.get(profile_name) or profiles.get("default") or {}
    return prof if isinstance(prof, dict) else {}


def resolve_column(columns: list[str], canonical: str, profile_name: str = "default") -> AdapterResolution | None:
    cols = [str(c) for c in (columns or [])]
    if canonical in cols:
        return AdapterResolution(canonical=canonical, matched=canonical, strategy="exact")

    cf = canonical.casefold()
    for col in cols:
        if col.casefold() == cf:
            return AdapterResolution(canonical=canonical, matched=col, strategy="casefold")

    profile = get_profile(profile_name)
    aliases = ((profile.get("column_aliases") or {}).get(canonical) or [])
    for alias in aliases:
        if alias in cols:
            return AdapterResolution(canonical=canonical, matched=alias, strategy="alias")
    alias_cf = {str(a).casefold() for a in aliases}
    for col in cols:
        if col.casefold() in alias_cf:
            return AdapterResolution(canonical=canonical, matched=col, strategy="alias_casefold")

    return None


def candidate_roots(kind: str, profile_name: str = "default") -> list[Path]:
    profile = get_profile(profile_name)
    roots = ((profile.get("roots") or {}).get(kind) or [])
    out = []
    for item in roots:
        p = Path(str(item))
        if p not in out:
            out.append(p)
    return out


def resolve_existing_root(kind: str, fallback: Path, profile_name: str = "default") -> Path:
    for root in candidate_roots(kind, profile_name=profile_name):
        if root.exists():
            return root
    return fallback


def root_aliases(profile_name: str = "default") -> dict:
    profile = get_profile(profile_name)
    data = profile.get("root_aliases") or {}
    return data if isinstance(data, dict) else {}


def resolve_named_child(parent: Path, name: str, profile_name: str = "default") -> Path | None:
    if not parent or not parent.exists() or not name:
        return None
    exact = parent / name
    if exact.exists():
        return exact
    aliases = root_aliases(profile_name=profile_name)
    for alias in aliases.get(name, []) or []:
        cand = parent / str(alias)
        if cand.exists():
            return cand
    target = name.casefold()
    try:
        for child in parent.iterdir():
            if child.name.casefold() == target:
                return child
    except Exception:
        return None
    alias_cf = {str(a).casefold() for a in (aliases.get(name, []) or [])}
    try:
        for child in parent.iterdir():
            if child.name.casefold() in alias_cf:
                return child
    except Exception:
        return None
    return None
