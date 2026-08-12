from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.paths import PATHS
from core.utils import load_json_cached, save_json


ADAPTERS_FILE = PATHS.data_root / "adapters" / "profiles.json"


@dataclass(slots=True)
class AdapterResolution:
    canonical: str
    matched: str
    strategy: str


def load_profiles() -> dict:
    """어댑터 프로필 (읽기 전용 — 반환 dict 를 수정하면 안 된다).

    `load_json` 이었을 때는 이 파일을 **호출마다 디스크에서 다시 읽고 파싱**했다.
    resolve_existing_root() 가 이걸 타고, 그 위에 splittable 의 `_base_root()` /
    `_db_base()` 가 있다 — 즉 요청 경로에서 수십 번 불리는 함수가 매번 JSON 파싱을
    했다. 실측 1회 1.6ms, `_product_path` 3.2ms 의 대부분이 이것이었다.

    load_json_cached 는 mtime+size 로 무효화하므로 관리자가 프로필을 바꾸면 다음
    호출부터 즉시 반영된다(save_profiles 가 mtime 을 바꾼다). 캐시를 공유하므로
    반환값을 수정하는 호출자가 생기면 안 된다 — 지금은 get_profile() 하나뿐이고
    거기서도 읽기만 한다.
    """
    data = load_json_cached(ADAPTERS_FILE, {})
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
