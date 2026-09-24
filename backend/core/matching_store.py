"""flow-data canonical matching tables.

관리자 매칭 테이블(Inline_matching.csv, inline_shot_matching.csv,
inline_map_settings.json, Vehicle_matching.csv, Chip_Radius.csv)은
flow-data 쪽이 정본이다. setup.py 는 data//flow-data 를 건드리지 않으므로
재설치에 유지된다.

규칙:
- 읽기: flow-data 정본 우선, 없으면 레거시(db_root).
- seed 복사: 정본이 없을 때만 1회 (기존 정본을 자동으로 덮어쓰지 않는다.
  Fab 동기 갱신을 따라가려면 resync_from_legacy() 를 명시 호출한다).
- 쓰기: 항상 flow-data 정본.
- db_root/data_root 인자로 호출자 root 를 존중한다 (테스트 격리).
"""
from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import save_json

logger = logging.getLogger("flow.matching_store")

_MATCHING_DIR_NAME = "matching"

# name -> legacy candidates (db_root 기준, 존재하는 첫 파일 사용).
_LEGACY_CANDIDATES: dict[str, list[str]] = {
    "Inline_matching.csv": [
        "Inline_matching.csv",
        "inline_matching.csv",
        "matching/Inline_matching.csv",
        "matching/inline_matching.csv",
    ],
    "inline_shot_matching.csv": [
        "confidential/inline_shot_matching.csv",
        "inline_shot_matching.csv",
    ],
    "inline_map_settings.json": [
        "confidential/inline_map_settings.json",
        "credential/inline_map_settings.json",
    ],
    "Vehicle_matching.csv": [
        "Vehicle_matching.csv",
        "vehicle_matching.csv",
    ],
    "Chip_Radius.csv": [
        "Chip_Radius.csv",
        "chip_radius.csv",
    ],
}


def managed_names() -> list[str]:
    return sorted(_LEGACY_CANDIDATES)


def _data_root(data_root=None) -> Path:
    return Path(data_root) if data_root is not None else PATHS.data_root


def matching_dir(data_root=None) -> Path:
    return _data_root(data_root) / _MATCHING_DIR_NAME


def canonical_path(name: str, data_root=None) -> Path:
    """flow-data 정본 경로 (없어도 쓰기 대상으로 반환)."""
    return matching_dir(data_root) / str(name or "").strip()


def _legacy_paths(name: str, db_root=None) -> list[Path]:
    try:
        root = Path(db_root) if db_root is not None else PATHS.db_root
    except Exception:
        return []
    return [root / candidate for candidate in _LEGACY_CANDIDATES.get(name, [])]


def _seed_copy(legacy: Path, target: Path) -> bool:
    try:
        if legacy.resolve() == target.resolve():
            return True
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(legacy.read_bytes())
        logger.info("matching seed-copied %s -> %s", legacy, target)
        return True
    except OSError:
        logger.debug("matching seed copy failed: %s", legacy, exc_info=True)
        return False


def resolve(name: str, *, seed: bool = True, db_root=None, data_root=None) -> Path:
    """읽기/쓰기용 정본 경로(flow-data). 기존 정본은 절대 자동 덮어쓰기 안 함."""
    target = canonical_path(name, data_root)
    if target.is_file():
        return target
    if seed:
        legacy = next((p for p in _legacy_paths(name, db_root) if p.is_file()), None)
        if legacy is not None and _seed_copy(legacy, target):
            return target
    return target


def resync_from_legacy(name: str, *, db_root=None, data_root=None) -> Path:
    """Fab 동기 갱신을 flow-data 정본에 명시 반영한다 (관리자/배치용)."""
    target = canonical_path(name, data_root)
    legacy = next((p for p in _legacy_paths(name, db_root) if p.is_file()), None)
    if legacy is None:
        raise ValueError(f"레거시 원본이 없습니다: {name}")
    if not _seed_copy(legacy, target):
        raise ValueError(f"동기화에 실패했습니다: {name}")
    return target


def read_csv_rows(name: str, *, db_root=None, data_root=None) -> tuple[list[dict[str, str]], Path]:
    """(행, 실제 읽은 경로). 없으면 ([], 정본 경로)."""
    path = resolve(name, db_root=db_root, data_root=data_root)
    if not path.is_file():
        # seed 복사로 생겼을 수 있으니 한 번 더 확인하지 않고 그대로 반환.
        legacy = next((p for p in _legacy_paths(name, db_root) if p.is_file()), None)
        if legacy is not None:
            path = legacy
        else:
            return [], path
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [{str(k).strip(): str(v or "").strip() for k, v in raw.items() if k is not None}
                    for raw in csv.DictReader(handle)]
        return rows, path
    except (OSError, UnicodeError, csv.Error):
        logger.debug("matching csv read failed: %s", path, exc_info=True)
        return [], path


def save_csv_rows(name: str, rows: list[dict[str, Any]], columns: list[str],
                  *, data_root=None) -> Path:
    """원자 쓰기(utf-8-sig) → 정본 경로 반환."""
    path = canonical_path(name, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(columns))
            writer.writeheader()
            for row in rows or []:
                writer.writerow({col: (row.get(col) if isinstance(row, dict) else "") for col in columns})
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
    return path


def read_json_doc(name: str, *, db_root=None, data_root=None) -> tuple[dict, Path]:
    path = resolve(name, db_root=db_root, data_root=data_root)
    if not path.is_file():
        legacy = next((p for p in _legacy_paths(name, db_root) if p.is_file()), None)
        if legacy is not None:
            path = legacy
        else:
            return {}, path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}, path
    except (OSError, ValueError, UnicodeError):
        logger.debug("matching json read failed: %s", path, exc_info=True)
        return {}, path


def save_json_doc(name: str, doc: dict, *, data_root=None) -> Path:
    path = canonical_path(name, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, doc if isinstance(doc, dict) else {})
    return path
