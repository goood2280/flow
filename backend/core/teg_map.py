# -*- coding: utf-8 -*-
"""core/teg_map.py — TEG 위치 조회 (WF MAP geometry + TEG 좌표 계산).

Auto Report 의 WF MAP geometry(My_Function._wafer_circle_params)와 같은 수학을 쓴다:
  Chip_Radius r 은 'shot 센터 ↔ wafer 원점 거리(mm)' 이므로, shot 격자좌표
  (chip_x_adj, chip_y_adj)와의 관계
      r² = kx²·(x-cx)² + ky²·(y-cy)²   (kx=shot_width[mm/격자], ky=shot_height, (cx,cy)=wafer 중심)
  를 선형화  r² = A·x² + B·y² + p·x + q·y + C  로 최소자승 fit 하면
      cx=-p/2A,  cy=-q/2B,  kx=√A,  ky=√B.
  이후 shot 센터의 WF MAP 좌표(mm) = ((x-cx)·kx, (y-cy)·ky)이다. chip_y_adj는
  아래쪽이 +이고 ebeam_y는 위쪽이 +이므로 TEG 좌하단의 Cartesian 좌표는
  (shot_x + ebeam_x, -shot_y + ebeam_y), radius는 원점과의 유클리드 거리다.

입력 파일 (파일탐색기 Files 위치 = DB root, 상대경로는 db_root 기준):
  · chip layout: Mask(vehicle), chip_x_adj, chip_y_adj, Chip_Radius 열 (열 이름 대소문자 무관)
  · Teg_location.csv: vehicle, teg, ebeam_x, ebeam_y (shot 센터 기준 TEG 좌하단, TEG 는 직사각형)
    선택 열: teg_w, teg_h — 없으면 설정의 TEG 기본 사이즈 사용
             top_cell — teg 의 다른 이름(Mapfile 체크 완전 일치용)
             direction — H/Horizontal(가로, 기본) | V/Vertical(세워 놓인 TEG).
               열이 없거나 빈 칸이면 TEG 이름 접두(V_/H_)로 판정한다 (normalize_direction).
               teg_w/teg_h 는 실제 배치 방향 그대로라 V 라고 코드가 스왑하지 않는다
               (기본 사이즈로 채울 때만 세운다 — teg_size).

설정/그림 저장소: DB root 의 `teg_location/` 폴더 (파일탐색기 위치 안).
  · teg_location/teg_map.json — 파일 경로·ebeam 배율·wafer 반경/최외곽·TEG 기본 사이즈,
    vehicle 별 shot 표시 방식(mode: none|image|grid):
      grid  = shot 안 칩 배열 — cols×rows, 칩 크기(chip_w/h mm, 0=칸 자동), 칩 사이 간격(gap_x/y mm),
              칩 블록은 shot 센터 기준 좌우/상하 대칭 배치
      image = shot 안에 그림 표시 (teg_location/ 폴더에 업로드된 vehicle 별 이미지)
  · teg_location/<vehicle>.<ext> — vehicle 별 그림 파일
"""
from __future__ import annotations

import datetime
import csv
import io
import json
import logging
import math
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from core.paths import PATHS
from core.utils import load_json, save_json
from core import roots

logger = logging.getLogger("flow.teg_map")

# 구버전(v1) 설정 위치 — teg_location 폴더 도입 전. 최초 로드 시 이관.
LEGACY_CFG_PATH = PATHS.data_root / "teg_map.json"

CFG_NAME = "teg_map.json"
DIR_NAME = "teg_location"
INLINE_MAP_DIR_NAME = "credential"
INLINE_MAP_FILE_NAME = "inline_map_settings.json"
INLINE_SHOT_MATCHING_FILE_NAME = "inline_shot_matching.csv"
INLINE_SHOT_MATCHING_COLUMNS = ("product", "step_id", "item_id", "map_name")
PRODUCT_INFO_FILE_NAME = "TEG_Product_Info.csv"

PRODUCT_GEOMETRY_COLUMNS = (
    "vehicle",
    "chip_size_x_um", "chip_size_y_um",
    "sl_size_x_um", "sl_size_y_um",
    "shot_cols", "shot_rows",
    "shot_size_x_um", "shot_size_y_um",
    "map_offset_odd_x", "map_offset_odd_y",
)
PRODUCT_OPTIONAL_COLUMNS = ("rc_cols", "rc_rows", "raw_config_json")
PRODUCT_INFO_COLUMNS = (*PRODUCT_GEOMETRY_COLUMNS, "node_path", *PRODUCT_OPTIONAL_COLUMNS)

# 새 제품의 Chip_Radius와 exact geometry 응답은 화면 표시용 4자리 반올림을
# 거치지 않는다. CSV에는 고정 12자리로 저장해 이후 fit fallback으로 읽더라도
# 원래 Shot Size / Map offset(Odd)에 최대한 가깝게 복원되게 한다.
PRODUCT_RADIUS_DECIMALS = 12

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_IMAGE_BYTES = 8 * 1024 * 1024

# shot 안 표시 방식. dev_grid 는 개발제품용 — MAIN TEG 좌표(die 좌하단)에
# Main_chip_info.csv 의 chip 크기로 die 를 그린다(그림 없이도 된다).
VEHICLE_MODES = ("none", "image", "grid", "dev_grid")

DEFAULT_VEHICLE_CFG = {
    "mode": "none",     # none=기본(TEG 만) | image=그림 | grid=칩 격자 | dev_grid=개발 격자
    "cols": 1,          # shot 안 칩 가로 개수
    "rows": 1,          # shot 안 칩 세로 개수
    "chip_w": 0.0,      # 칩 크기 mm (0 = shot 을 cols/rows 로 균등 분할)
    "chip_h": 0.0,
    "gap_x": 0.0,       # 칩 사이 간격 mm (좌우)
    "gap_y": 0.0,       # 칩 사이 간격 mm (위아래)
    "image": "",        # teg_location/ 폴더 안 그림 파일명
    # 제품별 wafer 유효 최외곽 반경(mm). None이면 전역 wafer_edge_mm(기본 147)을 사용.
    "wafer_edge_mm": None,
    # Teg_location에 teg_w/teg_h가 없을 때 쓰는 제품별 기본 크기(mm).
    # None이면 기존 전역 teg_default_w/h를 상속한다.
    "teg_default_w": None,
    "teg_default_h": None,
}

# TEG Mapfile 체크(core/teg_check) 설정 — flat 별 기본 오프셋·모듈(TEG)별 오프셋.
# flat 키는 저장값 기준 "h"/"v_R" (UI 표기는 Horizontal / Vertical(R)).
# 모듈별 오프셋은 항상 Horizontal(TEG) 관점으로 입력, 양수 = 빼기.
CHECK_FLATS = ("h", "v_R", "v_L")
DEFAULT_EXTENSION_MACRO_RULES = (
    {"key": "alias_pchk_to_prbchk", "name": "PCHK를 PRBCHK로 인식",
     "pattern": r"^([HV])_PCHK$", "replacement": "${1}_PRBCHK",
     "note": "같은 기준 TEG 별칭 · V_PCHK → V_PRBCHK / H_PCHK → H_PRBCHK"},
    {"key": "alias_prbchk_to_pchk", "name": "PRBCHK를 PCHK로 인식",
     "pattern": r"^([HV])_PRBCHK$", "replacement": "${1}_PCHK",
     "note": "같은 기준 TEG 별칭 · V_PRBCHK → V_PCHK / H_PRBCHK → H_PCHK"},
    {"key": "01strip", "name": "끝의 01 제거", "pattern": r"^(.+)01$",
     "replacement": "$1", "note": "이름만 확장 확인 · TEGA01 → TEGA"},
    {"key": "reorder", "name": "H_/V_ 접두사 재배치",
     "pattern": r"^([A-Za-z])_(.+)$", "replacement": "${2}${1}01",
     "note": "H_AAA01 → AAA01H01"},
    {"key": "split", "name": "분할 번호 제거", "pattern": r"^(.+)_(\d+)$",
     "replacement": "$1", "note": "TEGA_1 → TEGA"},
    {"key": "alias_flat_suffix", "name": "H/V 접두사를 뒤로 이동",
     "pattern": r"^([HV])_([A-Za-z]+\d+)$", "replacement": "${2}${1}",
     "note": "H_QAF01 → QAF01H"},
    {"key": "alias_tail_letter", "name": "끝 영문자를 H/V 뒤로 이동",
     "pattern": r"^([HV])_([A-Za-z]+)([A-Za-z])(\d+)$",
     "replacement": "${2}${4}${1}${3}", "note": "H_QAB03 → QA03HB · V_QAB03 → QA03VB"},
    {"key": "alias_tail_letter_reverse", "name": "뒤의 H/V와 영문자를 접두사로 복원",
     "pattern": r"^([A-Za-z]+)(\d+)([HV])([A-Za-z])$",
     "replacement": "${3}_${1}${4}${2}", "note": "QA04HB → H_QAB04 · QA03VB → V_QAB03"},
    {"key": "alias_dfm_sl", "name": "DFM의 H/V를 SL로 변환",
     "pattern": r"^[HV]_(DFM)(\d+)$", "replacement": "${1}SL${2}",
     "note": "H_DFM01 → DFMSL01"},
    {"key": "alias_sram_flat", "name": "SRAM의 H/V 접두사 제거",
     "pattern": r"^[HV]_(SRAM\d+)$", "replacement": "$1",
     "note": "H_SRAM24 → SRAM24"},
)
DEFAULT_EXTENSION_MACRO_BUILTINS = {
    rule["key"]: True for rule in DEFAULT_EXTENSION_MACRO_RULES
}
LEGACY_ALIAS_BUILTIN_KEYS = tuple(
    rule["key"] for rule in DEFAULT_EXTENSION_MACRO_RULES
    if str(rule["key"]).startswith("alias_")
)
_MACRO_GROUP_RE = re.compile(r"\$\{([A-Za-z_]\w*|\d+)\}|\$(\d+)")
# 짧은 TEG 이름만 대상으로 해도 중첩 반복 정규식은 백트래킹이 폭발할 수 있다.
# 사용자 매크로에 필요한 단순 캡처/문자군은 허용하되 `(a+)+` 류는 저장하지 않는다.
_UNSAFE_MACRO_PATTERN_RE = re.compile(
    r"\((?:[^()\\]|\\.)*[*+](?:[^()\\]|\\.)*\)\s*(?:[*+]|\{\d)")


def extension_macro_replacement(value: Any) -> str:
    """UI의 `$1`/`${name}` 치환식을 Python `re` 치환식으로 바꾼다."""
    text = str(value or "")
    return _MACRO_GROUP_RE.sub(lambda match: rf"\g<{match.group(1) or match.group(2)}>", text)


def extension_macro_pattern_is_safe(pattern: str) -> bool:
    return not bool(_UNSAFE_MACRO_PATTERN_RE.search(str(pattern or "")))


DEFAULT_CHECK_CFG = {
    # flat 별 기본 (dx, dy). V_ 계열(Vertical(R)) 기본 offset y' = 10.
    "flat_offsets": {"h": [0.0, 0.0], "v_R": [0.0, 10.0], "v_L": [0.0, 0.0]},
    # 모듈(TEG)별 오프셋 — H/TEG 관점 입력(양수=빼기). V: TEG x→실y, TEG y→실-x.
    "modules": [],   # legacy global module calibration
    # first-pad delta is measured from the stored ebeam origin in the TEG's
    # horizontal-normalised local frame (first pad is on the left).
    "first_pad_default": [0.0, 0.0],
    "pchk_first_pad_default": [0.0, 0.0],
    "first_pad_modules": [],  # [{name, dx, dy, note}]
    # Per-product additions/overrides. Product calibration is additive; exact
    # first-pad rules describe geometry and therefore override global defaults.
    "products": {},
    # Mapfile module 표기와 Teg_location 이름을 같은 S/L TEG 로 보는 전 제품 공통 규칙.
    # 기본 규칙은 코드/설치 번들에 포함되고 사용자 추가 규칙은 flow-data 설정에 저장한다.
    "extension_macros": {
        "builtins": dict(DEFAULT_EXTENSION_MACRO_BUILTINS),
        "rules": [],
    },
    # Mapfile 세팅 안 된 S/L TEG를 이름 포함값으로 묶는 전 제품 공통 구분.
    # [{"match":"DVC","label":"DVC_TEAM"}] -> H_DVC11은 DVC_TEAM 그룹.
    "mapfile_departments": [],
    # die 겹침 허용오차 — **ebeam raw 단위**(ΔX/ΔY 와 같은 공간, ebeam_scale 로 mm 환산).
    # 경계선 접촉과 die 안쪽으로 이 값 이하만 걸친 것은 정상으로 허용한다.
    # 0 이어도 선 접촉은 정상이고, 실제 면적이 겹칠 때만 침범이다.
    "die_tol": 3.0,
}

DEFAULT_CFG = {
    # 파일탐색기 Files(DB root) 기준 상대경로 또는 절대경로
    "layout_file": "Chip_Radius.csv",
    "teg_file": "Teg_location.csv",
    # MAIN(die) 크기표 — vehicle, chip_name, chipsize_x, chipsize_y (µm).
    # 그림 모드에서 die 사각형의 크기 기준. 없으면 그림에서 인식한 크기로 폴백.
    "main_chip_file": "Main_chip_info.csv",
    # ebeam_x/y → mm 환산 배율. 기본 = ebeam 파일이 µm 단위(0.001).
    # Chip_Radius 는 mm 단위 전제 (배율 없음). ebeam 이 mm 단위 파일이면 1.0 으로.
    "ebeam_scale": 0.001,
    "wafer_radius_mm": 150.0,
    # wafer 최외곽선 (edge exclusion) — WF MAP 에 점선으로 함께 표시
    "wafer_edge_mm": 147.0,
    # TEG 기본 사이즈 (mm 저장, UI 는 µm 입력) — Teg_location 에 teg_w/teg_h 없을 때 사용.
    # 기본 3000×100 µm.
    "teg_default_w": 3.0,
    "teg_default_h": 0.1,
    # vehicle 별 shot 표시 설정: {vehicle: DEFAULT_VEHICLE_CFG 형태}
    "vehicles": {},
    # TEG Mapfile 체크 설정 (DEFAULT_CHECK_CFG 형태)
    "check": DEFAULT_CHECK_CFG,
    # TEG Mapfile 체크 대상 TEG 목록 — {vehicle: [teg 이름, ...]}.
    # 위치 조회 페이지에서 관리자(page manager 'teg')가 편집. vehicle 키가 없으면
    # 기본값(teg 이름이 H_/V_ 로 시작하는 것 전부)을 대상으로 본다.
    "check_targets": {},
    # 제품 계층: {vehicle: "2나노 / 2나노A"}. 신규 제품은 Product Info CSV에도
    # 같은 경로를 저장하고, 이 맵은 기존 Chip_Radius 전용 제품의 분류를 보완한다.
    "product_nodes": {},
    # 최상위 노드 접근 규칙. 키가 없으면 기존 호환을 위해 공개, 키가 있으면
    # users/departments 중 하나와 일치해야 한다. admin은 항상 전체 접근한다.
    "node_access": {},
    # 설정 스키마 버전 — v2: teg 기본 3000×100µm, ebeam 기본 µm 배율(0.001)
    "cfg_version": 2,
}

# 구 기본값 — cfg_version<2 파일이 이 값 그대로면 새 기본값으로 1회 이관.
_OLD_DEFAULTS = {"ebeam_scale": 1.0, "teg_default_w": 2.0, "teg_default_h": 2.0}

_LOCK = threading.RLock()
_INLINE_MAP_LOCK = threading.RLock()


# ────────────────────────────────────────── 저장소 위치
def teg_dir() -> Path:
    """teg_location 폴더 (DB root 안) — 설정 json + vehicle 그림 파일."""
    return roots.get_db_root() / DIR_NAME


def _cfg_path() -> Path:
    return teg_dir() / CFG_NAME


def inline_map_settings_path() -> Path:
    """Inline 좌표 매칭용 관리자 설정 파일 (DB root/credential)."""
    return roots.get_db_root() / INLINE_MAP_DIR_NAME / INLINE_MAP_FILE_NAME


def inline_shot_matching_path() -> Path:
    """Inline ITEM → map 연결표 (DB root)."""
    return roots.get_db_root() / INLINE_SHOT_MATCHING_FILE_NAME


def _clean_inline_matching_rows(rows: Any) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        row = {
            column: str(raw.get(column) or "").strip()[:200]
            for column in INLINE_SHOT_MATCHING_COLUMNS
        }
        if not any(row.values()):
            continue
        missing = [column for column, value in row.items() if not value]
        if missing:
            raise ValueError(f"Inline shot matching {index}행의 {', '.join(missing)} 값이 비어 있습니다")
        key = tuple(row[column].casefold() for column in INLINE_SHOT_MATCHING_COLUMNS)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(row)
    return cleaned


def load_inline_shot_matching() -> dict:
    path = inline_shot_matching_path()
    with _INLINE_MAP_LOCK:
        rows: list[dict[str, str]] = []
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    rows = _clean_inline_matching_rows(list(csv.DictReader(handle)))
            except (OSError, UnicodeError, csv.Error) as exc:
                raise ValueError(f"{path.name}을 읽을 수 없습니다: {exc}") from exc
        return {
            "columns": list(INLINE_SHOT_MATCHING_COLUMNS),
            "rows": rows,
            "path": str(path),
        }


def _save_inline_shot_matching_rows(rows: list[dict[str, str]]) -> None:
    path = inline_shot_matching_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=INLINE_SHOT_MATCHING_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def _clean_inline_table(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    table_name = str(raw.get("table_name") or "").strip()[:120]
    vehicle = str(raw.get("vehicle") or "").strip()[:200]
    if not table_name or not vehicle:
        return None
    shots: list[dict] = []
    seen: set[tuple[float, float]] = set()
    for item in (raw.get("shots") or [])[:10000]:
        if not isinstance(item, dict):
            continue
        try:
            x, y = float(item.get("shot_x")), float(item.get("shot_y"))
        except (TypeError, ValueError):
            continue
        subitem_id = str(item.get("subitem_id") or item.get("name") or "").strip()[:200]
        key = (round(x, 6), round(y, 6))
        if not subitem_id or not all(math.isfinite(v) for v in key) or key in seen:
            continue
        seen.add(key)
        shots.append({
            "shot_x": key[0], "shot_y": key[1],
            "subitem_id": subitem_id,
            # v1 UI/저장 파일 호환. 신규 소비자는 subitem_id를 authoritative로 쓴다.
            "name": subitem_id,
        })
    return {
        "table_name": table_name,
        "vehicle": vehicle,
        "shots": shots,
        "comment": str(raw.get("comment") or "").strip()[:1000],
        "updated_at": str(raw.get("updated_at") or ""),
        "updated_by": str(raw.get("updated_by") or "")[:200],
    }


def load_inline_map_settings() -> dict:
    """관리자 전용 API가 소비하는 Inline shot 이름표 저장소."""
    path = inline_map_settings_path()
    with _INLINE_MAP_LOCK:
        raw = load_json(path, {}) if path.is_file() else {}
        tables: list[dict] = []
        source = raw.get("tables", []) if isinstance(raw, dict) else []
        if isinstance(source, dict):
            source = list(source.values())
        for item in source if isinstance(source, list) else []:
            cleaned = _clean_inline_table(item)
            if cleaned is not None:
                tables.append(cleaned)
        tables.sort(key=lambda item: item["table_name"].casefold())
        return {"version": 1, "tables": tables}


def full_shots_for_payload(payload: dict) -> list[dict]:
    """기존 격자를 연장해 제품 유효 반경과 겹치거나 닿는 full shot을 만든다."""
    geometry = payload.get("geometry") or {}
    real = list(payload.get("shots") or [])
    if geometry.get("fit") != "radius" or not real:
        return real
    shot_w = abs(float(geometry.get("shot_w_mm") or 0))
    shot_h = abs(float(geometry.get("shot_h_mm") or 0))
    edge = float(geometry.get("wafer_edge_mm") or geometry.get("wafer_radius_mm") or 0)
    if min(shot_w, shot_h, edge) <= 0:
        return real

    # 원과 정확히 한 점/선으로 닿는 shot도 full shot이다. 좌표 연산의 float 오차만
    # 흡수하고 실제 147mm 밖의 shot을 더 포함하지 않도록 나노미터 수준만 허용한다.
    edge_tolerance = 1e-9

    def intersects_edge(shot: dict) -> bool:
        try:
            mm_x = (float(shot.get("mm_x")) if shot.get("mm_x") is not None
                    else (float(shot.get("x")) - float(geometry.get("cx") or 0))
                    * float(geometry.get("kx") or 0))
            mm_y = (float(shot.get("mm_y")) if shot.get("mm_y") is not None
                    else (float(shot.get("y")) - float(geometry.get("cy") or 0))
                    * float(geometry.get("ky") or 0))
        except (TypeError, ValueError):
            return False
        dx = max(0.0, abs(mm_x) - shot_w / 2)
        dy = max(0.0, abs(mm_y) - shot_h / 2)
        return math.hypot(dx, dy) <= edge + edge_tolerance

    grid_cols = int(geometry.get("grid_cols") or 0)
    grid_rows = int(geometry.get("grid_rows") or 0)
    if grid_cols > 0 and grid_rows > 0:
        if grid_cols * grid_rows > 6000:
            return real
        key_of = lambda x, y: (round(float(x), 6), round(float(y), 6))
        seen = {key_of(shot.get("x"), shot.get("y")) for shot in real}
        out = [shot for shot in real if intersects_edge(shot)]
        cx, cy = float(geometry.get("cx") or 0), float(geometry.get("cy") or 0)
        kx, ky = float(geometry.get("kx") or 0), float(geometry.get("ky") or 0)
        # R/C Count는 wafer 밖까지 포함할 수 있는 사각 격자 크기다. 기존처럼
        # shot 사각형과 원의 교차·접촉을 쓰되 판정 반경만 150mm가 아닌 edge로 쓴다.
        for x in range(1, grid_cols + 1):
            for y in range(1, grid_rows + 1):
                if key_of(x, y) in seen:
                    continue
                mm_x, mm_y = (x - cx) * kx, (y - cy) * ky
                dx = max(0.0, abs(mm_x) - shot_w / 2)
                dy = max(0.0, abs(mm_y) - shot_h / 2)
                if math.hypot(dx, dy) > edge + edge_tolerance:
                    continue
                out.append({
                    "x": x, "y": y, "synthetic": True,
                    "mm_x": round(mm_x, 4), "mm_y": round(mm_y, 4),
                    "radius": round(math.hypot(mm_x, mm_y), 4),
                })
        return out
    pitch_x = abs(float(geometry.get("pitch_x") or 0))
    pitch_y = abs(float(geometry.get("pitch_y") or 0))
    kx = abs(float(geometry.get("kx") or 0))
    ky = abs(float(geometry.get("ky") or 0))
    step_x, step_y = pitch_x * kx, pitch_y * ky
    if min(pitch_x, pitch_y, step_x, step_y) <= 0:
        return real
    if math.pi * edge * edge / (step_x * step_y) > 6000:
        return real

    anchor = min(real, key=lambda shot: float(shot.get("radius", math.inf)))
    nx = math.ceil((edge + shot_w) / step_x) + 1
    ny = math.ceil((edge + shot_h) / step_y) + 1
    key_of = lambda x, y: (round(float(x), 6), round(float(y), 6))
    seen = {key_of(shot.get("x"), shot.get("y")) for shot in real}
    out = [shot for shot in real
           if abs(float(shot.get("x") or 0)) > 1e-9
           and abs(float(shot.get("y") or 0)) > 1e-9
           and intersects_edge(shot)]
    cx, cy = float(geometry.get("cx") or 0), float(geometry.get("cy") or 0)
    for i in range(-nx, nx + 1):
        for j in range(-ny, ny + 1):
            x = round(float(anchor["x"]) + i * pitch_x, 6)
            y = round(float(anchor["y"]) + j * pitch_y, 6)
            key = key_of(x, y)
            if key in seen or abs(x) <= 1e-9 or abs(y) <= 1e-9:
                continue
            mm_x = (x - cx) * float(geometry["kx"])
            mm_y = (y - cy) * float(geometry["ky"])
            dx = max(0.0, abs(mm_x) - shot_w / 2)
            dy = max(0.0, abs(mm_y) - shot_h / 2)
            if math.hypot(dx, dy) > edge + edge_tolerance:
                continue
            seen.add(key)
            out.append({
                "x": x, "y": y, "synthetic": True,
                "mm_x": round(mm_x, 4), "mm_y": round(mm_y, 4),
                "radius": round(math.hypot(mm_x, mm_y), 4),
            })
    return out


def save_inline_map_table(table_name: str, vehicle: str, shots: list[dict], username: str,
                          comment: str = "") -> dict:
    """TABLE 이름을 키로 제품별 shot 위치/이름을 원자적으로 upsert 한다."""
    cleaned = _clean_inline_table({
        "table_name": table_name,
        "vehicle": vehicle,
        "shots": shots,
        "comment": comment,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "updated_by": username,
    })
    if cleaned is None:
        raise ValueError("TABLE 이름과 제품(vehicle)이 필요합니다")
    if not cleaned["shots"]:
        raise ValueError("이름을 입력한 shot을 1개 이상 선택해 주세요")
    if not cleaned["comment"]:
        raise ValueError("저장 comment를 입력해 주세요")

    # 화면에 존재하지 않는 좌표를 저장하지 않는다. 이후 ET 좌표 매칭의 기준 데이터이므로
    # 수기 요청이나 오래 열린 브라우저가 잘못된 위치를 밀어 넣는 것을 서버에서 차단한다.
    payload = map_payload(cleaned["vehicle"])
    available = {
        (round(float(s["x"]), 6), round(float(s["y"]), 6))
        for s in [*(payload.get("shots") or []), *full_shots_for_payload(payload)]
    }
    invalid = [s for s in cleaned["shots"] if (s["shot_x"], s["shot_y"]) not in available]
    if invalid:
        raise ValueError(f"제품 map에 없는 shot 좌표가 포함되어 있습니다: {invalid[0]['shot_x']}, {invalid[0]['shot_y']}")

    with _INLINE_MAP_LOCK:
        current = load_inline_map_settings()
        tables = [t for t in current["tables"]
                  if t["table_name"].casefold() != cleaned["table_name"].casefold()]
        tables.append(cleaned)
        tables.sort(key=lambda item: item["table_name"].casefold())
        out = {"version": 1, "tables": tables}
        path = inline_map_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, out, indent=2)
        return out


def delete_inline_map_table(table_name: str) -> dict:
    name = str(table_name or "").strip()
    if not name:
        raise ValueError("TABLE 이름이 필요합니다")
    with _INLINE_MAP_LOCK:
        references = [row for row in load_inline_shot_matching().get("rows", [])
                      if row["map_name"].casefold() == name.casefold()]
        if references:
            raise ValueError(
                f"inline_shot_matching.csv에서 {len(references)}개 행이 사용하는 map입니다. "
                "연결 행을 먼저 삭제해 주세요"
            )
        current = load_inline_map_settings()
        tables = [t for t in current["tables"] if t["table_name"].casefold() != name.casefold()]
        if len(tables) == len(current["tables"]):
            raise LookupError(f"저장된 TABLE이 없습니다: {name}")
        out = {"version": 1, "tables": tables}
        path = inline_map_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, out, indent=2)
        return out


# ────────────────────────────────────────── 설정
def _clean_vehicle_cfg(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = dict(DEFAULT_VEHICLE_CFG)
    mode = str(raw.get("mode", "none")).strip().lower()
    out["mode"] = mode if mode in VEHICLE_MODES else "none"
    for k, lo, hi, cast in (
        ("cols", 1, 100, int), ("rows", 1, 100, int),
        ("chip_w", 0.0, 1000.0, float), ("chip_h", 0.0, 1000.0, float),
        ("gap_x", 0.0, 1000.0, float), ("gap_y", 0.0, 1000.0, float),
        ("wafer_edge_mm", 0.001, 1000.0, float),
        ("teg_default_w", 0.001, 1000.0, float),
        ("teg_default_h", 0.001, 1000.0, float),
    ):
        try:
            v = cast(raw.get(k, DEFAULT_VEHICLE_CFG[k]))
        except (TypeError, ValueError):
            continue
        if lo <= v <= hi and math.isfinite(float(v)):
            out[k] = v
    img = str(raw.get("image", "") or "").strip()
    # 파일명만 허용 (경로 이탈 방지)
    if img and img == Path(img).name and Path(img).suffix.lower() in IMAGE_EXTS:
        out["image"] = img
    return out


def _clean_check(raw: Any) -> dict:
    """TEG Mapfile 체크 설정 정리 — 잘못된 항목은 조용히 버리고 기본값으로."""
    out = {
        "flat_offsets": {f: list(DEFAULT_CHECK_CFG["flat_offsets"][f]) for f in CHECK_FLATS},
        "modules": [],
        "first_pad_default": list(DEFAULT_CHECK_CFG["first_pad_default"]),
        "pchk_first_pad_default": list(DEFAULT_CHECK_CFG["pchk_first_pad_default"]),
        "first_pad_modules": [],
        "products": {},
        "extension_macros": {
            "builtins": dict(DEFAULT_EXTENSION_MACRO_BUILTINS),
            "rules": [],
        },
        "mapfile_departments": [],
        # 기준 PCHK 이 내장 마커(H_PCHK/H_PRBCHK 등)로 안 잡히는 설비 표기 —
        # 사용자 지정 flat 마커. teg_check 가 내장보다 먼저 매칭한다.
        "custom_markers": {f: [] for f in CHECK_FLATS},
        "die_tol": DEFAULT_CHECK_CFG["die_tol"],
    }
    if not isinstance(raw, dict):
        return out
    try:
        tol = float(raw.get("die_tol", out["die_tol"]))
        if math.isfinite(tol) and 0.0 <= tol <= 1e6:
            out["die_tol"] = tol
    except (TypeError, ValueError):
        pass
    cm = raw.get("custom_markers")
    if isinstance(cm, dict):
        for f in CHECK_FLATS:
            names = cm.get(f)
            if isinstance(names, (list, tuple)):
                seen: list[str] = []
                for n in names[:50]:
                    token = str(n or "").strip()[:80]
                    if token and token.upper() not in {s.upper() for s in seen}:
                        seen.append(token)
                out["custom_markers"][f] = seen
    departments = raw.get("mapfile_departments")
    if isinstance(departments, (list, tuple)):
        seen_departments: set[str] = set()
        for value in departments[:100]:
            if isinstance(value, dict):
                match = str(value.get("match") or value.get("key") or "").strip()[:80]
                label = str(value.get("label") or value.get("name") or match).strip()[:80]
            else:
                # 구버전 ["DVC", "SRAM"] 형식은 판정값=표시명으로 자동 호환.
                match = str(value or "").strip()[:80]
                label = match
            folded = match.casefold()
            if match and folded not in seen_departments:
                seen_departments.add(folded)
                out["mapfile_departments"].append({"match": match, "label": label or match})
    fo = raw.get("flat_offsets")
    if isinstance(fo, dict):
        for f in CHECK_FLATS:
            pair = fo.get(f)
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                try:
                    dx, dy = float(pair[0]), float(pair[1])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(dx) and math.isfinite(dy):
                    out["flat_offsets"][f] = [dx, dy]

    def _pair(value: Any, default: list[float] | None = None) -> list[float] | None:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return default
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return default
        return [x, y] if math.isfinite(x) and math.isfinite(y) else default

    def _modules(value: Any) -> list[dict]:
        cleaned: list[dict] = []
        if not isinstance(value, list):
            return cleaned
        for m in value[:1000]:
            if not isinstance(m, dict):
                continue
            flat = str(m.get("flat", "h")).strip()
            name = str(m.get("name", "")).strip()[:200]
            pair = _pair([m.get("dx", 0), m.get("dy", 0)])
            if flat not in CHECK_FLATS or not name or pair is None:
                continue
            cleaned.append({"flat": flat, "name": name, "dx": pair[0], "dy": pair[1],
                            "note": str(m.get("note", "") or "").strip()[:500]})
        return cleaned

    def _first_pad_rules(value: Any) -> list[dict]:
        cleaned: list[dict] = []
        if not isinstance(value, list):
            return cleaned
        for item in value[:1000]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:200]
            pair = _pair([item.get("dx", 0), item.get("dy", 0)])
            if not name or pair is None:
                continue
            cleaned.append({"name": name, "dx": pair[0], "dy": pair[1],
                            "note": str(item.get("note", "") or "").strip()[:500]})
        return cleaned

    def _extension_macros(value: Any, *, strict: bool = False) -> dict:
        cleaned = {
            "builtins": dict(DEFAULT_EXTENSION_MACRO_BUILTINS),
            "rules": [],
        }
        if not isinstance(value, dict):
            return cleaned
        builtins = value.get("builtins")
        if isinstance(builtins, dict):
            for key in DEFAULT_EXTENSION_MACRO_BUILTINS:
                if key in builtins:
                    cleaned["builtins"][key] = bool(builtins[key])
            # 구버전의 복합 alias 토글을 새 1행 1규칙 매크로들로 이어받는다.
            if "alias" in builtins:
                for key in LEGACY_ALIAS_BUILTIN_KEYS:
                    if key not in builtins:
                        cleaned["builtins"][key] = bool(builtins["alias"])
        rules = value.get("rules")
        if not isinstance(rules, list):
            return cleaned
        for index, item in enumerate(rules[:200], start=1):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()[:80]
            pattern = str(item.get("pattern") or "").strip()[:300]
            replacement = str(item.get("replacement") or "").strip()[:300]
            note = str(item.get("note") or "").strip()[:500]
            if not any((name, pattern, replacement, note)):
                continue
            if not name or not pattern:
                if strict:
                    raise ValueError(f"확장 매크로 {index}행의 이름과 Mapfile 정규식은 필수입니다")
                continue
            if not extension_macro_pattern_is_safe(pattern):
                if strict:
                    raise ValueError(f"확장 매크로 {index}행은 중첩 반복 정규식을 사용할 수 없습니다")
                continue
            try:
                compiled = re.compile(pattern)
                compiled.sub(extension_macro_replacement(replacement), "")
            except re.error as exc:
                if strict:
                    raise ValueError(f"확장 매크로 {index}행 정규식/치환식 오류: {exc}") from exc
                continue
            cleaned["rules"].append({
                "name": name,
                "pattern": pattern,
                "replacement": replacement,
                "note": note,
            })
        return cleaned

    mods = raw.get("modules")
    if isinstance(mods, list):
        out["modules"] = _modules(mods)
    out["first_pad_default"] = _pair(raw.get("first_pad_default"), out["first_pad_default"])
    out["pchk_first_pad_default"] = _pair(raw.get("pchk_first_pad_default"), out["pchk_first_pad_default"])
    out["first_pad_modules"] = _first_pad_rules(raw.get("first_pad_modules"))
    out["extension_macros"] = _extension_macros(raw.get("extension_macros"))
    products = raw.get("products")
    if isinstance(products, dict):
        for vehicle, value in list(products.items())[:2000]:
            name = str(vehicle or "").strip()[:200]
            if not name or not isinstance(value, dict):
                continue
            flat_corrections = {f: [0.0, 0.0] for f in CHECK_FLATS}
            raw_fc = value.get("flat_corrections")
            if isinstance(raw_fc, dict):
                for f in CHECK_FLATS:
                    flat_corrections[f] = _pair(raw_fc.get(f), flat_corrections[f])
            product: dict[str, Any] = {
                "flat_corrections": flat_corrections,
                "modules": _modules(value.get("modules")),
                "first_pad_modules": _first_pad_rules(value.get("first_pad_modules")),
            }
            if "first_pad_default" in value:
                product["first_pad_default"] = _pair(value.get("first_pad_default"), [0.0, 0.0])
            if "pchk_first_pad_default" in value:
                product["pchk_first_pad_default"] = _pair(value.get("pchk_first_pad_default"), [0.0, 0.0])
            out["products"][name] = product
    return out


def clean_extension_macros(raw: Any, *, strict: bool = False) -> dict:
    """외부 API용 확장 매크로 정리·검증.

    `_clean_check`의 전체 설정 계약을 재사용하되 strict 저장에서는 빈 필수값과
    잘못된 정규식을 조용히 버리지 않고 사용자에게 오류로 돌려준다.
    """
    holder = {"extension_macros": raw}
    if strict:
        # strict 검증은 `_clean_check` 내부와 같은 제한을 직접 적용한다. 전체 설정
        # 정리는 기존 호환을 위해 관대한 동작을 유지해야 하므로 API 저장만 엄격하다.
        rules = (raw or {}).get("rules") if isinstance(raw, dict) else []
        for index, item in enumerate(rules if isinstance(rules, list) else [], start=1):
            if not isinstance(item, dict):
                continue
            values = [str(item.get(key) or "").strip()
                      for key in ("name", "pattern", "replacement", "note")]
            if not any(values):
                continue
            if not values[0] or not values[1]:
                raise ValueError(f"확장 매크로 {index}행의 이름과 Mapfile 정규식은 필수입니다")
            if not extension_macro_pattern_is_safe(values[1][:300]):
                raise ValueError(f"확장 매크로 {index}행은 중첩 반복 정규식을 사용할 수 없습니다")
            try:
                compiled = re.compile(values[1][:300])
                compiled.sub(extension_macro_replacement(values[2][:300]), "")
            except re.error as exc:
                raise ValueError(f"확장 매크로 {index}행 정규식/치환식 오류: {exc}") from exc
    return _clean_check(holder)["extension_macros"]


def check_profile(cfg: dict, vehicle: str) -> dict:
    """Return the global/product coordinate calibration used for one vehicle."""
    check = (cfg or {}).get("check") or _clean_check({})
    product = ((check.get("products") or {}).get(str(vehicle or "").strip()) or {})
    return {
        "global": check,
        "product": product,
        "flat_corrections": product.get("flat_corrections")
                            or {f: [0.0, 0.0] for f in CHECK_FLATS},
    }


def _clean_vehicles(raw: Any) -> dict:
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for veh, v in raw.items():
        cleaned = _clean_vehicle_cfg(v)
        if cleaned is not None:
            out[str(veh).strip()] = cleaned
    return out


def vehicle_wafer_edge_mm(cfg: dict, vehicle: str) -> float:
    """제품별 최외곽 반경. 미설정 제품은 전역 기본값(기본 147 mm)을 상속한다."""
    fallback = float((cfg or {}).get("wafer_edge_mm", DEFAULT_CFG["wafer_edge_mm"]))
    target = str(vehicle or "").strip().casefold()
    vehicles_cfg = (cfg or {}).get("vehicles") or {}
    product_cfg = next(
        (value for key, value in vehicles_cfg.items()
         if str(key).strip().casefold() == target and isinstance(value, dict)),
        {},
    )
    try:
        value = float(product_cfg.get("wafer_edge_mm"))
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) and value > 0 else fallback


def vehicle_teg_default_size(cfg: dict, vehicle: str) -> tuple[float, float]:
    """제품별 TEG 기본 크기(mm). 미설정 축은 기존 전역값을 상속한다.

    제품 키는 화면/CSV의 대소문자 차이로 설정이 끊기지 않게 case-insensitive로
    찾는다. 구버전 teg_map.json에는 제품별 키가 없으므로 그대로 전역값을 쓴다.
    """
    config = cfg or {}
    def _global_value(key: str) -> float:
        try:
            value = float(config.get(key, DEFAULT_CFG[key]))
        except (TypeError, ValueError):
            value = float(DEFAULT_CFG[key])
        return value if math.isfinite(value) and value > 0 else float(DEFAULT_CFG[key])

    fallbacks = (_global_value("teg_default_w"), _global_value("teg_default_h"))
    target = str(vehicle or "").strip().casefold()
    product_cfg = next(
        (value for key, value in (config.get("vehicles") or {}).items()
         if str(key).strip().casefold() == target and isinstance(value, dict)),
        {},
    )
    resolved: list[float] = []
    for key, fallback in zip(("teg_default_w", "teg_default_h"), fallbacks):
        try:
            value = float(product_cfg.get(key))
        except (TypeError, ValueError):
            value = fallback
        resolved.append(value if math.isfinite(value) and value > 0 else fallback)
    return resolved[0], resolved[1]


def clean_node_path(value: Any) -> str:
    """`2나노 - 2나노A`, `2나노/2나노A`를 정규화한 상위 노드 경로."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = re.split(r"\s*(?:/|>|→|\s+-\s+)\s*", raw)
    cleaned: list[str] = []
    for part in parts[:12]:
        name = re.sub(r"\s+", " ", str(part or "").strip())[:120]
        if name and name.casefold() not in {item.casefold() for item in cleaned}:
            cleaned.append(name)
    return " / ".join(cleaned)[:1000]


def _clean_product_nodes(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for vehicle, path in list(raw.items())[:10000]:
        name = str(vehicle or "").strip()[:200]
        node_path = clean_node_path(path)
        if name and node_path:
            out[name] = node_path
    return out


def _clean_access_values(raw: Any) -> list[str]:
    values = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for value in list(values)[:2000]:
        text = str(value or "").strip()[:200]
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _clean_node_access(raw: Any) -> dict[str, dict[str, list[str]]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, list[str]]] = {}
    for root, rule in list(raw.items())[:1000]:
        root_name = clean_node_path(root).split(" / ")[0] if clean_node_path(root) else ""
        if not root_name or not isinstance(rule, dict):
            continue
        out[root_name] = {
            "users": _clean_access_values(rule.get("users")),
            "departments": _clean_access_values(rule.get("departments")),
        }
    return out


MAX_CHECK_TARGETS = 5000        # vehicle 당 체크 대상 TEG 상한


def _clean_target_list(raw: Any) -> list[str]:
    """체크 대상 TEG 이름 리스트 정리 — 문자열만, 공백 제거, 순서 유지·중복 제거."""
    if not isinstance(raw, (list, tuple)):
        return []
    seen: list[str] = []
    marks = set()
    for n in raw[:MAX_CHECK_TARGETS]:
        token = str(n or "").strip()[:200]
        if token and token not in marks:
            marks.add(token)
            seen.append(token)
    return seen


def _clean_check_targets(raw: Any) -> dict:
    """{vehicle: [teg 이름, ...]} 정리 — vehicle 키가 있으면 (빈 리스트라도) 명시적
    설정으로 본다. 빈 dict 는 '설정 없음(기본값 사용)' 을 의미."""
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for veh, names in raw.items():
        key = str(veh).strip()
        if key:
            out[key] = _clean_target_list(names)
    return out


def _migrate_legacy_cfg() -> dict | None:
    """v1(data/flow-data/teg_map.json) → teg_location/teg_map.json 이관.

    v1 의 chip_grids {veh:{cols,rows}} 는 vehicles {veh:{mode:"grid",cols,rows}} 로 변환.
    """
    try:
        if not LEGACY_CFG_PATH.is_file():
            return None
        old = load_json(LEGACY_CFG_PATH, None)
        if not isinstance(old, dict):
            return None
        cfg: dict[str, Any] = {}
        for k in ("layout_file", "teg_file", "ebeam_scale", "wafer_radius_mm"):
            if k in old:
                cfg[k] = old[k]
        vehicles = {}
        for veh, g in (old.get("chip_grids") or {}).items():
            if isinstance(g, dict):
                vehicles[str(veh)] = {"mode": "grid",
                                      "cols": g.get("cols", 1), "rows": g.get("rows", 1)}
        if vehicles:
            cfg["vehicles"] = vehicles
        logger.info(f"teg_map 설정 이관: {LEGACY_CFG_PATH} → {_cfg_path()}")
        return cfg
    except Exception as e:
        logger.warning(f"teg_map 레거시 설정 이관 실패: {e}")
        return None


def load_cfg() -> dict:
    with _LOCK:
        path = _cfg_path()
        cfg = load_json(path, None) if path.is_file() else None
        if cfg is None:
            migrated = _migrate_legacy_cfg()
            if migrated is not None:
                cfg = migrated
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    save_json(path, migrated)
                except Exception as e:
                    logger.warning(f"teg_map 설정 저장 실패({path}): {e}")
        if not isinstance(cfg, dict):
            cfg = {}
        cfg = _migrate_cfg_v2(cfg, path)
    out = dict(DEFAULT_CFG)
    for k in ("layout_file", "teg_file", "main_chip_file"):
        if isinstance(cfg.get(k), str):
            out[k] = cfg[k].strip() or DEFAULT_CFG[k]
    for k, lo, hi in (("ebeam_scale", 1e-9, 1e9),
                      ("wafer_radius_mm", 10.0, 1000.0),
                      ("wafer_edge_mm", 0.0, 1000.0),
                      ("teg_default_w", 0.001, 1000.0),
                      ("teg_default_h", 0.001, 1000.0)):
        try:
            v = float(cfg.get(k, DEFAULT_CFG[k]))
            if lo <= v <= hi and math.isfinite(v):
                out[k] = v
        except (TypeError, ValueError):
            pass
    out["vehicles"] = _clean_vehicles(cfg.get("vehicles"))
    out["check"] = _clean_check(cfg.get("check"))
    out["check_targets"] = _clean_check_targets(cfg.get("check_targets"))
    out["product_nodes"] = _clean_product_nodes(cfg.get("product_nodes"))
    out["node_access"] = _clean_node_access(cfg.get("node_access"))
    return out


def _migrate_cfg_v2(cfg: dict, path: Path) -> dict:
    """1회성 이관 — 구버전(cfg_version<2) 파일이 예전 하드코드 기본값을 그대로
    들고 있으면 새 기본값(teg 3000×100µm, ebeam µm 배율 0.001)으로 바꾼다.
    버전 마커를 기록해 이후 사용자가 같은 값을 의도적으로 저장해도 덮어쓰지 않는다."""
    if not isinstance(cfg, dict) or not cfg:
        return cfg
    try:
        version = int(cfg.get("cfg_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version >= 2:
        return cfg
    def _f(key):
        try:
            return float(cfg.get(key))
        except (TypeError, ValueError):
            return None
    if _f("teg_default_w") == _OLD_DEFAULTS["teg_default_w"] and _f("teg_default_h") == _OLD_DEFAULTS["teg_default_h"]:
        cfg["teg_default_w"] = DEFAULT_CFG["teg_default_w"]
        cfg["teg_default_h"] = DEFAULT_CFG["teg_default_h"]
    if _f("ebeam_scale") == _OLD_DEFAULTS["ebeam_scale"]:
        cfg["ebeam_scale"] = DEFAULT_CFG["ebeam_scale"]
    cfg["cfg_version"] = 2
    try:
        save_json(path, cfg)
    except Exception as e:
        logger.warning(f"teg_map 설정 이관 저장 실패({path}): {e}")
    return cfg


def save_cfg(patch: dict) -> dict:
    """설정 부분 갱신 후 저장. 알 수 없는 키는 무시.

    vehicles 는 vehicle 단위 병합 — patch 의 값이 None/빈 dict 면 해당 vehicle 삭제.
    """
    with _LOCK:
        cur = load_cfg()
        patch = patch if isinstance(patch, dict) else {}
        for k in ("layout_file", "teg_file", "main_chip_file"):
            if k in patch:
                cur[k] = str(patch[k] or "").strip() or DEFAULT_CFG[k]
        for k, lo, hi, label in (
            ("ebeam_scale", 1e-9, 1e9, "ebeam_scale 은 0 보다 커야 합니다"),
            ("wafer_radius_mm", 10.0, 1000.0, "wafer_radius_mm 범위(10~1000)를 벗어났습니다"),
            ("wafer_edge_mm", 0.0, 1000.0, "wafer_edge_mm 범위(0~1000)를 벗어났습니다"),
            ("teg_default_w", 0.001, 1000.0, "teg_default_w 범위(0.001~1000mm)를 벗어났습니다"),
            ("teg_default_h", 0.001, 1000.0, "teg_default_h 범위(0.001~1000mm)를 벗어났습니다"),
        ):
            if k in patch:
                v = float(patch[k])
                if not (lo <= v <= hi and math.isfinite(v)):
                    raise ValueError(label)
                cur[k] = v
        if "vehicles" in patch:
            merged = dict(cur["vehicles"])
            incoming = patch["vehicles"] if isinstance(patch["vehicles"], dict) else {}
            for veh, v in incoming.items():
                key = str(veh).strip()
                if v in (None, "", {}):
                    merged.pop(key, None)
                    continue
                cleaned = _clean_vehicle_cfg(v)
                if cleaned is not None:
                    merged[key] = cleaned
            cur["vehicles"] = merged
        if "check" in patch:
            cur["check"] = _clean_check(patch["check"])
        if "check_targets" in patch:
            # vehicle 단위 병합 — 값이 None 이면 해당 vehicle 삭제(기본값으로 복귀),
            # 리스트면 명시적 대상 목록으로 저장(빈 리스트도 '대상 없음' 으로 명시).
            merged = dict(cur.get("check_targets") or {})
            incoming = patch["check_targets"] if isinstance(patch["check_targets"], dict) else {}
            for veh, names in incoming.items():
                key = str(veh).strip()
                if not key:
                    continue
                if names is None:
                    merged.pop(key, None)
                else:
                    merged[key] = _clean_target_list(names)
            cur["check_targets"] = merged
        if "product_nodes" in patch:
            merged = dict(cur.get("product_nodes") or {})
            incoming = patch["product_nodes"] if isinstance(patch["product_nodes"], dict) else {}
            for vehicle, node_path in incoming.items():
                key = str(vehicle or "").strip()[:200]
                if not key:
                    continue
                cleaned = clean_node_path(node_path)
                if cleaned:
                    merged[key] = cleaned
                else:
                    merged.pop(key, None)
            cur["product_nodes"] = merged
        if "node_access" in patch:
            # 이 설정은 전체 교체다. 행을 지우면 해당 최상위 노드는 다시 공개된다.
            cur["node_access"] = _clean_node_access(patch["node_access"])
        path = _cfg_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, cur)
        return cur


# ────────────────────────────────────────── MAIN die helpers
# MAIN die/그룹은 정확히 MAIN + 숫자 두 자리(MAIN01, MAIN02, ...)만 인정한다.
# ASb_MAIN, MAIN_BLOCK, MAIN_M01처럼 이 규약과 다른 일반 TEG는 MAIN이 아니다.
MAIN_RE = re.compile(r"^MAIN\d{2}$", re.IGNORECASE)


_CHIP_NUM_RE = re.compile(r"^(.*?)(\d+)$")


def normalize_chip_name(name: str) -> str:
    """chip_name / MAIN 이름 비교용 정규화 키.

    엔지니어마다 표기가 갈린다 — 크기표에는 `MAIN_M01`, TEG 이름은 `MAIN01` 처럼
    쓰는 식이다. 대소문자·구분기호를 지우고, 끝의 숫자 앞 `M`(도면 번호 접두)과
    0 패딩을 없애 같은 die 를 같은 키로 만든다:
      MAIN01 · main_01 · MAIN_M01 · MAIN-M1 → "main1"
    """
    t = re.sub(r"[^0-9a-z]", "", str(name or "").lower())
    m = _CHIP_NUM_RE.match(t)
    if not m:
        return t
    base, num = m.group(1), m.group(2)
    if base.endswith("m"):          # MAIN_M01 → MAIN01 (M = 도면 번호 접두)
        base = base[:-1]
    return f"{base}{int(num)}"


def normalize_main_purpose(value: Any) -> str:
    """Main_chip_info purpose 비교용 정규화 값.

    ``NO_TEG``/``NO-TEG``/여러 공백도 운영자가 의도한 ``NO TEG``로 본다.
    """
    return re.sub(r"[\s_-]+", " ", str(value or "").strip().upper())


def is_main_purpose_warning(value: Any) -> bool:
    """MAIN purpose가 지정돼 TEG를 배치하면 안 되는 구간인지."""
    return bool(normalize_main_purpose(value))


def main_purpose_for(vehicle: str, name: str, purposes: dict | None = None) -> str:
    """MAIN 이름 → purpose. chip_size_for와 같은 이름 매칭 순서를 쓴다."""
    table = purposes if purposes is not None else load_main_chip_purposes()[0]
    rows = table.get(str(vehicle or "").strip()) or {}
    if not rows:
        return ""
    key = str(name or "").strip().lower()
    for chip_name, purpose in rows.items():
        if str(chip_name).strip().lower() == key:
            return str(purpose or "").strip()
    nkey = normalize_chip_name(name)
    if nkey:
        for chip_name, purpose in rows.items():
            if normalize_chip_name(chip_name) == nkey:
                return str(purpose or "").strip()
    return str(next(iter(rows.values())) or "").strip() if len(rows) == 1 else ""


def chip_size_for(vehicle: str, name: str, chips: dict | None = None) -> tuple[float, float] | None:
    """MAIN 이름 → die 크기(mm). Main_chip_info.csv 기준, 없으면 None.

    찾는 순서: ① chip_name 완전 일치(대소문자 무관) → ② 정규화 키 일치
    (`MAIN01` ↔ `MAIN_M01`, normalize_chip_name) → ③ 그 vehicle 에 행이 딱 하나면
    이름이 달라도 그 크기 (die 종류가 하나인 제품이 흔하다).
    """
    table = chips if chips is not None else load_main_chips()[0]
    rows = table.get(str(vehicle or "").strip()) or {}
    if not rows:
        return None
    key = str(name or "").strip().lower()
    for cn, size in rows.items():
        if str(cn).strip().lower() == key:
            return size
    nkey = normalize_chip_name(name)
    if nkey:
        for cn, size in rows.items():
            if normalize_chip_name(cn) == nkey:
                return size
    return next(iter(rows.values())) if len(rows) == 1 else None


def main_anchors(payload: dict, chips: dict | None = None) -> list[dict]:
    """MAIN 으로 이름 붙은 TEG → die 앵커 [{name, x, y, (w, h)}] (mm, 좌표=좌하단).

    MAIN 은 die 급 블록이고, 그 TEG 좌표가 곧 die 의 좌하단이다. 크기는
    Main_chip_info.csv(µm→mm)에서만 온다 — 크기가 안 붙은 앵커는 die 로 그리지
    않는다(teg_shape.anchor_cells).
    """
    veh = str(payload.get("vehicle") or "").strip()
    table = chips if chips is not None else load_main_chips()[0]
    out: list[dict] = []
    seen: set[tuple[float, float]] = set()
    for t in payload.get("tegs") or []:
        name = str(t.get("teg") or "")
        if "·" in name or not MAIN_RE.search(name):
            continue
        # 크기 조회는 **표시 이름이 아니라 원래 이름**으로 한다 — 동명 TEG 자동
        # 넘버링이 붙인 "_1" 이 그대로 들어가면 Main_chip_info 매칭이 깨져
        # (MAIN01_1 → main11 ≠ main1) die 크기가 안 붙고 개발 격자가 비어 버린다.
        src = str(t.get("teg_src") or name)
        try:
            key = (round(float(t["ebeam_x"]), 6), round(float(t["ebeam_y"]), 6))
        except (TypeError, ValueError, KeyError):
            continue
        if key in seen:
            continue
        seen.add(key)
        a = {"name": name, "x": key[0], "y": key[1]}
        size = chip_size_for(veh, src, table)
        if size:
            a["w"], a["h"] = size
        out.append(a)
    return out


def _safe_vehicle_stem(vehicle: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", str(vehicle or "").strip()).strip("._-")
    return stem or "vehicle"


def image_path(vehicle: str) -> Path | None:
    """vehicle 설정에 등록된 그림 파일 경로. 없으면 None."""
    cfg = load_cfg()
    v = cfg["vehicles"].get(str(vehicle).strip())
    name = (v or {}).get("image") or ""
    if not name:
        return None
    p = teg_dir() / Path(name).name
    return p if p.is_file() else None


def sniff_image_ext(data: bytes) -> str:
    """매직 바이트로 이미지 확장자 판별 — 실패하면 "".

    클립보드에서 붙여넣은 그림은 파일명 자체가 없을 수 있다. 그 때만 실제 바이트로
    형식을 정한다 — 확장자가 붙어 있으면 그것을 존중하고, 허용 목록에 없으면 그대로
    거부한다(내용으로 구제해 주면 "이미지만 허용"이라는 약속이 흐려진다).
    """
    b = bytes(data or b"")
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if b.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if b.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(b) >= 12 and b.startswith(b"RIFF") and b[8:12] == b"WEBP":
        return ".webp"
    return ""


def save_image(vehicle: str, data: bytes, ext: str) -> str:
    """vehicle 그림 저장 (기존 그림 교체) → 저장된 파일명 반환."""
    ext = str(ext or "").lower()
    if not ext:
        # 확장자가 아예 없을 때만 내용으로 판별 — 붙여넣기 업로드 경로.
        ext = sniff_image_ext(data)
    if ext not in IMAGE_EXTS:
        raise ValueError(f"이미지 형식만 가능합니다 ({', '.join(IMAGE_EXTS)})")
    if not data:
        raise ValueError("빈 파일입니다")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("파일이 너무 큽니다 (최대 8MB)")
    veh = str(vehicle or "").strip()
    if not veh:
        raise ValueError("vehicle 필요")
    with _LOCK:
        d = teg_dir()
        d.mkdir(parents=True, exist_ok=True)
        stem = _safe_vehicle_stem(veh)
        name = stem + ext
        (d / name).write_bytes(data)
        # 제품당 그림은 하나다 — 같은 vehicle 의 이전 그림은 확장자가 무엇이든 지운다.
        # (설정에 적힌 이전 파일 + 예전 업로드가 남긴 다른 확장자 파일)
        cur = load_cfg()["vehicles"].get(veh, {})
        stale = {(cur or {}).get("image") or ""} | {stem + e for e in IMAGE_EXTS}
        for old in stale:
            if not old or Path(old).name == name:
                continue
            try:
                (d / Path(old).name).unlink(missing_ok=True)
            except OSError:
                pass
        save_cfg({"vehicles": {veh: {**(cur or DEFAULT_VEHICLE_CFG), "image": name}}})
        return name


def delete_image(vehicle: str) -> None:
    veh = str(vehicle or "").strip()
    with _LOCK:
        cur = load_cfg()["vehicles"].get(veh)
        if not cur or not cur.get("image"):
            return
        try:
            (teg_dir() / Path(cur["image"]).name).unlink(missing_ok=True)
        except OSError:
            pass
        save_cfg({"vehicles": {veh: {**cur, "image": ""}}})


# ────────────────────────────────────────── 파일 로딩
def resolve_path(name: str) -> Path:
    """설정 경로를 현재 DB root 기준으로 해석한다.

    개발 Windows와 운영 Linux가 같은 설정을 공유할 수 있으므로 두 차이를
    흡수한다.
      · Linux의 대소문자 구분 (`chip_radius.csv` ↔ `Chip_Radius.csv`)
      · 개발 PC 절대경로가 저장된 뒤 운영으로 넘어온 경우(파일명으로 재연결)
    """
    raw = str(name or "").strip()
    root = roots.get_db_root()
    if not raw:
        return root

    # PosixPath는 D:\...를 절대경로로 보지 않는다. 운영에서 개발 설정을 읽으면
    # DB root 아래에 `D:\...`라는 파일을 찾는 오류가 되므로 basename만 이관한다.
    windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")
    p = Path(raw)
    if windows_abs:
        p = root / re.split(r"[\\/]", raw)[-1]
    elif not p.is_absolute():
        p = root / p

    if p.exists():
        return p

    # 경로의 각 세그먼트를 case-insensitive 하게 다시 찾는다. 상대경로는 DB root
    # 안에서만 걷고, 절대경로는 존재하는 가장 가까운 parent부터 안전하게 푼다.
    try:
        if p.is_absolute() and p.parent.exists():
            hit = next((c for c in p.parent.iterdir() if c.name.casefold() == p.name.casefold()), None)
            if hit is not None:
                return hit
    except OSError:
        pass

    # 다른 개발/운영 root의 절대경로가 남았어도 현재 DB root 최상위의 동명
    # 파일을 우선 사용한다. TEG 설정 파일은 원래 Files 최상위 파일 계약이다.
    try:
        basename = re.split(r"[\\/]", raw)[-1]
        hit = next((c for c in root.iterdir()
                    if c.is_file() and c.name.casefold() == basename.casefold()), None)
        if hit is not None:
            return hit
    except OSError:
        pass
    return p


def _read_table(path: Path):
    import pandas as pd
    suf = path.suffix.lower()
    if suf == ".parquet":
        return pd.read_parquet(path)
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path, encoding="utf-8-sig")


def _find_col(df, *cands) -> str | None:
    """열 이름 매칭 — 정확 일치(대소문자 무관) 우선, 다음 부분 포함."""
    low = {str(c).strip().lower(): c for c in df.columns}
    for cand in cands:
        if cand.lower() in low:
            return low[cand.lower()]
    for cand in cands:
        for k, orig in low.items():
            if cand.lower() in k:
                return orig
    return None


def product_info_path() -> Path:
    """붙여넣기로 등록한 제품 geometry 원본(EDM 단일 CSV)."""
    return resolve_path(PRODUCT_INFO_FILE_NAME)


def _paste_cells(line: str) -> list[str]:
    """Excel TSV를 우선하고 CSV/공백 정렬 표도 받아 한 행의 셀로 나눈다."""
    raw = str(line or "").strip()
    if not raw:
        return []
    if "\t" in raw:
        return [cell.strip() for cell in raw.split("\t")]
    try:
        cells = next(csv.reader(io.StringIO(raw), skipinitialspace=True))
    except Exception:
        cells = [raw]
    if len(cells) >= 3:
        return [cell.strip() for cell in cells]
    # 화면/메일에서 복사한 공백 정렬 표: 끝의 숫자 두 개만 x/y로 보고 앞은 Item.
    match = re.match(
        r"^(.*?)\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
        r"\s+([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*$",
        raw,
    )
    return [match.group(1).strip(), match.group(2), match.group(3)] if match else cells


def _product_item_key(label: str) -> str | None:
    """계산에 쓰는 Item을 찾는다. 원문 행 보존은 별도 저장 경로가 담당한다."""
    text = str(label or "").strip().casefold().replace("μ", "u").replace("µ", "u")
    text = re.sub(r"\s+", " ", text)
    compact = re.sub(r"\s+", "", text)
    # 크기 항목은 현업 표마다 Item/RETICLE/Design 같은 설명과 µ/μ/u 단위가
    # 붙으므로 단위 표기와 관계없이 핵심 이름 포함 여부로 찾는다.
    if "chipsize" in compact:
        return "chip_size"
    if "s/lsize" in compact or "slsize" in compact:
        return "sl_size"
    if "shotsize" in compact:
        return "shot_size"
    # 현업 표에는 `Item R/C Count`, `RETICLE R/C Count`처럼 설명 접두사가
    # 붙기도 한다. 포함 여부로 찾아 raw 원문에는 값이 있는데 rc_cols/rows가
    # 비어 중앙 (0,0) 좌표로 폴백하는 일을 막는다.
    if any(token in compact for token in (
            "r/ccount", "rccount", "row/columncount", "rowcolumncount")):
        return "rc_count"
    # 아래 두 값은 비슷한 다른 항목과 섞이지 않도록 지정된 이름만 허용한다.
    unitless = re.sub(r"\((?:u)?m\)$", "", compact)
    if unitless == "shot":
        return "shot"
    if re.fullmatch(r"mapoffset\(odd\)(?:\((?:u?m|㎛)\))?", compact):
        return "map_offset_odd"
    return None


def parse_product_info_table(text: str) -> dict[str, float | int]:
    """Item/x/y 표에서 제품 geometry에 필요한 다섯 쌍을 읽고 검증한다.

    ``Map offset(Odd)`` X/Y는 shot 격자 번호가 아니라 wafer 실 center에서
    기준 shot center까지의 물리 거리(µm)다. 저장은 원값을 보존하고 geometry를
    만들 때만 :func:`_product_geometry_values`에서 격자 위상으로 변환한다.
    """
    values: dict[str, tuple[float, float]] = {}
    duplicates: list[str] = []
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cells = _paste_cells(line)
        if len(cells) < 3:
            continue
        key = _product_item_key(cells[0])
        if key is None:
            continue
        try:
            x = float(str(cells[-2]).strip().replace(",", ""))
            y = float(str(cells[-1]).strip().replace(",", ""))
        except (TypeError, ValueError):
            raise ValueError(f"{cells[0]}의 x, y가 숫자가 아닙니다")
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError(f"{cells[0]}의 x, y가 유효한 숫자가 아닙니다")
        if key in values:
            duplicates.append(str(cells[0]).strip())
        values[key] = (x, y)
    if duplicates:
        raise ValueError("같은 제품 항목이 여러 번 있습니다: " + ", ".join(duplicates[:5]))
    required = ("chip_size", "sl_size", "shot", "shot_size", "map_offset_odd")
    missing = [name for name in required if name not in values]
    if missing:
        labels = {
            "chip_size": "Chip Size(um)", "sl_size": "S/L Size(um)",
            "shot": "Shot", "shot_size": "Shot Size(um)",
            "map_offset_odd": "Map offset(Odd)",
        }
        raise ValueError("필수 Item을 찾지 못했습니다: " + ", ".join(labels[name] for name in missing))
    for key in ("chip_size", "shot_size"):
        if values[key][0] <= 0 or values[key][1] <= 0:
            raise ValueError(f"{key} x, y는 0보다 커야 합니다")
    if values["sl_size"][0] < 0 or values["sl_size"][1] < 0:
        raise ValueError("S/L Size x, y는 0 이상이어야 합니다")
    cols, rows = values["shot"]
    if cols < 1 or rows < 1 or not cols.is_integer() or not rows.is_integer():
        raise ValueError("Shot x, y는 1 이상의 정수(칩 격자 개수)여야 합니다")
    out: dict[str, float | int] = {
        "chip_size_x_um": values["chip_size"][0],
        "chip_size_y_um": values["chip_size"][1],
        "sl_size_x_um": values["sl_size"][0],
        "sl_size_y_um": values["sl_size"][1],
        "shot_cols": int(cols), "shot_rows": int(rows),
        "shot_size_x_um": values["shot_size"][0],
        "shot_size_y_um": values["shot_size"][1],
        "map_offset_odd_x": values["map_offset_odd"][0],
        "map_offset_odd_y": values["map_offset_odd"][1],
    }
    if "rc_count" in values:
        rc_cols, rc_rows = values["rc_count"]
        if (rc_cols < 1 or rc_rows < 1
                or not rc_cols.is_integer() or not rc_rows.is_integer()):
            raise ValueError("R/C Count x, y는 1 이상의 정수(full-shot X/Y 개수)여야 합니다")
        out["rc_cols"] = int(rc_cols)
        out["rc_rows"] = int(rc_rows)
    return out


def _product_info_raw_rows(text: str) -> list[dict[str, str]]:
    """사용자가 붙여넣은 Item/X/Y의 비어 있지 않은 모든 행을 순서대로 보존한다."""
    rows: list[dict[str, str]] = []
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        cells = _paste_cells(line)
        if not cells or not any(str(cell or "").strip() for cell in cells):
            continue
        cells = [str(cell or "").strip() for cell in cells]
        if (not rows and len(cells) >= 3
                and cells[0].casefold() == "item"
                and cells[1].casefold() == "x" and cells[2].casefold() == "y"):
            continue
        cells += [""] * (3 - len(cells))
        rows.append({"Item": cells[0], "X": cells[1], "Y": cells[2]})
    return rows


def _product_info_raw_rc_count(value: Any) -> tuple[int, int] | None:
    """저장된 raw_config_json에서 R/C Count를 복구한다.

    10.4.131 초기 저장본처럼 원문 행은 보존됐지만 구조화 rc_cols/rc_rows가
    비어 있는 제품도 config를 다시 붙여넣지 않고 1-based 좌표로 전환한다.
    """
    try:
        rows = json.loads(str(value or ""))
    except (TypeError, ValueError):
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict) or _product_item_key(row.get("Item")) != "rc_count":
            continue
        try:
            cols = float(str(row.get("X") or "").strip().replace(",", ""))
            rows_count = float(str(row.get("Y") or "").strip().replace(",", ""))
        except (TypeError, ValueError):
            return None
        if (math.isfinite(cols) and math.isfinite(rows_count)
                and cols >= 1 and rows_count >= 1
                and cols.is_integer() and rows_count.is_integer()):
            return int(cols), int(rows_count)
        return None
    return None


def _product_geometry_values(info: dict[str, Any]) -> dict[str, float]:
    """제품 원값을 WF MAP 내부 geometry 단위로 변환한다.

    Product Info의 Map offset은 µm 물리 좌표이고 화면의 실center 차이와 같은
    Cartesian 부호다(+X=오른쪽, +Y=위쪽). WF MAP 내부는 정수 shot index와
    아래쪽이 +인 ``mm_y``를 쓰므로 다음처럼 분수 격자 center로 바꾼다.

      shot(0, 0).mm_x = offset_x_mm
      shot(0, 0).mm_y = -offset_y_mm
      cx = -offset_x_mm / shot_w_mm
      cy =  offset_y_mm / shot_h_mm

    이 변환을 하지 않고 µm 원값을 cx/cy에 직접 넣으면 ``(-1, 16470)`` 같은
    값이 shot 좌표로 오인된다.
    """
    shot_w_mm = float(info["shot_size_x_um"]) / 1000.0
    shot_h_mm = float(info["shot_size_y_um"]) / 1000.0
    offset_x_um = float(info["map_offset_odd_x"])
    offset_y_um = float(info["map_offset_odd_y"])
    if not all(math.isfinite(value) for value in (
            shot_w_mm, shot_h_mm, offset_x_um, offset_y_um)):
        raise ValueError("Shot Size와 Map offset(Odd)은 유효한 숫자여야 합니다")
    if shot_w_mm <= 0 or shot_h_mm <= 0:
        raise ValueError("Shot Size x, y는 0보다 커야 합니다")
    offset_x_mm = offset_x_um / 1000.0
    offset_y_mm = offset_y_um / 1000.0
    out = {
        "shot_w_mm": shot_w_mm,
        "shot_h_mm": shot_h_mm,
        "offset_x_um": offset_x_um,
        "offset_y_um": offset_y_um,
        "offset_x_mm": offset_x_mm,
        "offset_y_mm": offset_y_mm,
        "cx": -offset_x_mm / shot_w_mm,
        "cy": offset_y_mm / shot_h_mm,
    }
    try:
        rc_cols = int(float(info.get("rc_cols")))
        rc_rows = int(float(info.get("rc_rows")))
    except (TypeError, ValueError, OverflowError):
        rc_cols = rc_rows = 0
    if rc_cols > 0 and rc_rows > 0:
        # R/C Count는 full-shot 사각형의 X/Y 개수다. 내부 물리 격자의 위상은
        # 그대로 두고, 그 사각형의 좌상단을 공개 좌표 (1, 1)로 평행 이동한다.
        #
        # 반올림은 **가장 가까운 정수**로 한다. 예전의 math.floor 는 Map offset이
        # 조금이라도 음수쪽이면(cx = -0.00025 → cx-6 = -6.00025) 격자 전체를 한 칸
        # 밀어버려, wafer에 실제로 걸치는 마지막 열이 공개 좌표를 못 받고 반대편
        # 첫 열은 원 밖이라 빈 칸으로 남았다 (13열 제품에서 실측 확인).
        # ceil(v - 0.5) 는 정확히 .5 인 경우(짝수 R/C Count + offset 0)에는 floor 와
        # 같은 값을 주므로 짝수 격자의 기존 tie-break 는 그대로 보존된다.
        origin_x = math.ceil(out["cx"] - (rc_cols - 1) / 2.0 - 0.5)
        origin_y = math.ceil(out["cy"] - (rc_rows - 1) / 2.0 - 0.5)
        out.update({
            "rc_cols": rc_cols,
            "rc_rows": rc_rows,
            "grid_origin_x": origin_x,
            "grid_origin_y": origin_y,
            "display_cx": out["cx"] - origin_x + 1,
            "display_cy": out["cy"] - origin_y + 1,
        })
    return out


def _product_shots(info: dict[str, Any], wafer_edge_mm: float) -> list[dict[str, float]]:
    """명시 geometry로 제품별 edge 안에 *전체가* 들어오는 shot만 만든다.

    Chip_Radius 값은 shot 중심과 wafer 중심 사이 거리다. 포함 여부는 중심점만
    보지 않고 직사각형 shot의 네 꼭짓점 중 가장 먼 점까지 edge 안인지 검사한다.
    """
    terms = _product_geometry_values(info)
    sx, sy = terms["shot_w_mm"], terms["shot_h_mm"]
    cx, cy = terms["cx"], terms["cy"]
    radius = float(wafer_edge_mm)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("wafer edge 반경은 0보다 커야 합니다")
    has_rc_grid = bool(terms.get("rc_cols") and terms.get("rc_rows"))
    if has_rc_grid:
        xmin, xmax = 1, int(terms["rc_cols"])
        ymin, ymax = 1, int(terms["rc_rows"])
        physical_x = lambda value: int(terms["grid_origin_x"]) + value - 1
        physical_y = lambda value: int(terms["grid_origin_y"]) + value - 1
    else:
        xmin, xmax = math.floor(cx - radius / sx), math.ceil(cx + radius / sx)
        ymin, ymax = math.floor(cy - radius / sy), math.ceil(cy + radius / sy)
        physical_x = lambda value: value
        physical_y = lambda value: value
    estimate = max(1, xmax - xmin + 1) * max(1, ymax - ymin + 1)
    if estimate > REFERENCE_MAX_ROWS:
        raise ValueError(f"Shot Size가 너무 작아 제품 map이 {estimate:,}칸을 넘습니다")
    rows: list[dict[str, float]] = []
    for x in range(xmin, xmax + 1):
        for y in range(ymin, ymax + 1):
            internal_x, internal_y = physical_x(x), physical_y(y)
            mm_x = (internal_x - cx) * sx
            mm_y = (internal_y - cy) * sy
            # 축별로 중심에서 더 먼 쪽 꼭짓점이 직사각형 네 꼭짓점 중 wafer
            # 중심에서 가장 멀다. 이 점까지 edge 안이면 full shot 전체가 들어온다.
            farthest_corner = math.hypot(abs(mm_x) + sx / 2.0,
                                         abs(mm_y) + sy / 2.0)
            if farthest_corner <= radius + 1e-10:
                rows.append({
                    "x": float(x), "y": float(y),
                    "r": round(math.hypot(mm_x, mm_y), PRODUCT_RADIUS_DECIMALS),
                })
    if not rows:
        raise ValueError(f"wafer {radius:g}mm 안에 전체가 들어오는 shot이 없습니다")
    return rows


def load_product_info():
    """제품 추가 원본 CSV → 정규화 DataFrame. 잘못된 행은 제품 단위로 제외한다."""
    import pandas as pd
    path = product_info_path()
    if not path.is_file():
        return pd.DataFrame(columns=PRODUCT_INFO_COLUMNS), path
    try:
        df = _read_table(path)
    except Exception as exc:
        logger.warning("TEG product info 읽기 실패 %s: %s", path, exc)
        return pd.DataFrame(columns=PRODUCT_INFO_COLUMNS), path
    lookup = {str(c).strip().casefold(): c for c in df.columns}
    if any(col.casefold() not in lookup for col in PRODUCT_GEOMETRY_COLUMNS):
        logger.warning("TEG product info 필수 열 누락: %s", list(df.columns))
        return pd.DataFrame(columns=PRODUCT_INFO_COLUMNS), path
    out = pd.DataFrame({
        col: (df[lookup[col.casefold()]] if col.casefold() in lookup else "")
        for col in PRODUCT_INFO_COLUMNS
    })
    out["vehicle"] = out["vehicle"].fillna("").astype(str).str.strip()
    out["node_path"] = out["node_path"].fillna("").astype(str).map(clean_node_path)
    for col in (*PRODUCT_GEOMETRY_COLUMNS[1:], "rc_cols", "rc_rows"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    # R/C Count는 Item 한 행의 X/Y 한 쌍이다. **저장된 구조화 열이 authoritative**
    # 이며, 둘 다 있으면 raw 원문 값으로 덮어쓰지 않는다.
    #
    # 두 열을 각각 fillna 하면 안 된다 — rc_cols만 저장되고 rc_rows가 빈 행에서
    # 저장값과 raw 복구값이 한 쌍으로 섞여, 어느 쪽에도 없던 격자 크기가
    # 만들어진다. 쌍이 온전히 저장돼 있을 때만 저장값을 쓰고, 아니면 raw 원문의
    # 쌍을 통째로 쓴다 (10.4.131 초기 저장본처럼 구조화 열만 비어 있는 제품 복구).
    # 복구는 raw에 온전한 쌍이 있을 때만 한다. 한쪽만 저장된 행을 NaN으로 밀어
    # 버리면, 저장 시 다른 제품 행까지 함께 다시 쓰는 _save_product_info_row 를
    # 거치며 그 값이 CSV에서 영구히 지워진다. 남은 반쪽은 그대로 두고 downstream
    # (_product_geometry_values)이 쌍이 아니라 무시하게 둔다.
    recovered_rc = out["raw_config_json"].map(_product_info_raw_rc_count)
    stored_pair = out["rc_cols"].notna() & out["rc_rows"].notna()
    use_recovered = (~stored_pair) & recovered_rc.notna()
    out["rc_cols"] = out["rc_cols"].where(
        ~use_recovered, recovered_rc.map(lambda pair: pair[0] if pair else math.nan)
    )
    out["rc_rows"] = out["rc_rows"].where(
        ~use_recovered, recovered_rc.map(lambda pair: pair[1] if pair else math.nan)
    )
    out = out.dropna(subset=list(PRODUCT_GEOMETRY_COLUMNS)).drop_duplicates("vehicle", keep="last")
    out = out[out["vehicle"] != ""]
    return out, path


def product_geometry(vehicle: str) -> dict[str, float | str] | None:
    """등록 제품의 exact geometry. 없으면 호출부가 Chip_Radius fit으로 fallback한다."""
    info_df, _ = load_product_info()
    key = str(vehicle or "").strip().casefold()
    if not key or info_df.empty:
        return None
    matched = info_df[info_df["vehicle"].astype(str).str.casefold() == key]
    if matched.empty:
        return None
    info = matched.iloc[-1]
    terms = _product_geometry_values(info)
    return {
        "source": "product_info",
        "vehicle": str(info["vehicle"]),
        "cx": terms.get("display_cx", terms["cx"]),
        "cy": terms.get("display_cy", terms["cy"]),
        "kx": terms["shot_w_mm"], "ky": terms["shot_h_mm"],
        "shot_w_mm": terms["shot_w_mm"], "shot_h_mm": terms["shot_h_mm"],
        "map_offset_odd_x_um": terms["offset_x_um"],
        "map_offset_odd_y_um": terms["offset_y_um"],
        "grid_cols": int(terms.get("rc_cols") or 0),
        "grid_rows": int(terms.get("rc_rows") or 0),
    }


def product_info_payload(vehicle: str) -> dict[str, Any]:
    """config 변경 창에 채울 현재 Item/X/Y 원본을 반환한다.

    아직 Chip_Radius fit만 쓰는 제품은 ``exists=False``와 빈 행을 반환한다.
    TEG_Product_Info에 저장된 제품은 원래 단위(크기·offset=µm, Shot=격자 개수)를 그대로 돌려줘
    화면에서 열자마자 현재 설정을 확인하고 수정할 수 있게 한다.
    """
    requested = str(vehicle or "").strip()
    if not requested:
        raise ValueError("vehicle을 입력해 주세요")
    info_df, path = load_product_info()
    matched = (info_df[
        info_df["vehicle"].astype(str).str.strip().str.casefold() == requested.casefold()
    ] if not info_df.empty else info_df)
    if matched.empty:
        catalog_row = next((row for row in product_catalog()
                            if row["vehicle"].casefold() == requested.casefold()), None)
        node_path = "" if not catalog_row or catalog_row["node_path"] == "미분류" else catalog_row["node_path"]
        return {
            "ok": True, "vehicle": requested, "exists": False,
            "values": {}, "rows": [], "node_path": node_path, "path": str(path),
            "wafer_edge_mm": vehicle_wafer_edge_mm(load_cfg(), requested),
        }
    row = matched.iloc[-1]
    def display_number(value: Any) -> int | float:
        number = float(value)
        return int(number) if number.is_integer() else number
    values = {
        "chip_size_x_um": display_number(row["chip_size_x_um"]),
        "chip_size_y_um": display_number(row["chip_size_y_um"]),
        "sl_size_x_um": display_number(row["sl_size_x_um"]),
        "sl_size_y_um": display_number(row["sl_size_y_um"]),
        "shot_cols": int(row["shot_cols"]),
        "shot_rows": int(row["shot_rows"]),
        "shot_size_x_um": display_number(row["shot_size_x_um"]),
        "shot_size_y_um": display_number(row["shot_size_y_um"]),
        "map_offset_odd_x": display_number(row["map_offset_odd_x"]),
        "map_offset_odd_y": display_number(row["map_offset_odd_y"]),
    }
    if row.get("rc_cols") == row.get("rc_cols") and row.get("rc_rows") == row.get("rc_rows"):
        values["rc_cols"] = int(row["rc_cols"])
        values["rc_rows"] = int(row["rc_rows"])
    rows: list[dict[str, Any]] = []
    raw_json = str(row.get("raw_config_json") or "").strip()
    if raw_json and raw_json.casefold() != "nan":
        try:
            loaded_rows = json.loads(raw_json)
            if isinstance(loaded_rows, list):
                rows = [
                    {"Item": str(item.get("Item") or ""),
                     "X": str(item.get("X") or ""), "Y": str(item.get("Y") or "")}
                    for item in loaded_rows if isinstance(item, dict)
                ]
        except (TypeError, ValueError):
            rows = []
    if not rows:
        rows = [
        {"Item": "Chip Size(um)", "X": values["chip_size_x_um"], "Y": values["chip_size_y_um"]},
        {"Item": "S/L Size(um)", "X": values["sl_size_x_um"], "Y": values["sl_size_y_um"]},
        {"Item": "Shot", "X": values["shot_cols"], "Y": values["shot_rows"]},
        {"Item": "Shot Size(um)", "X": values["shot_size_x_um"], "Y": values["shot_size_y_um"]},
        {"Item": "Map offset(Odd)(um)", "X": values["map_offset_odd_x"], "Y": values["map_offset_odd_y"]},
        ]
        if values.get("rc_cols") and values.get("rc_rows"):
            rows.append({"Item": "R/C Count", "X": values["rc_cols"], "Y": values["rc_rows"]})
    return {
        "ok": True, "vehicle": str(row["vehicle"]), "exists": True,
        "values": values, "rows": rows, "node_path": clean_node_path(row.get("node_path")),
        "path": str(path),
        "wafer_edge_mm": vehicle_wafer_edge_mm(load_cfg(), str(row["vehicle"])),
    }


def _generated_product_layout(cfg: dict):
    """TEG_Product_Info 제품을 직접 geometry로 펼친 primary layout."""
    import pandas as pd
    info_df, path = load_product_info()
    frames = []
    for _, info in info_df.iterrows():
        try:
            shots = _product_shots(
                info.to_dict(), vehicle_wafer_edge_mm(cfg, str(info.get("vehicle") or ""))
            )
        except (TypeError, ValueError) as exc:
            logger.warning("TEG product geometry 제외 vehicle=%s: %s", info.get("vehicle"), exc)
            continue
        frame = pd.DataFrame(shots)
        frame["vehicle"] = str(info["vehicle"])
        frame["layout_source"] = "product_info"
        terms = _product_geometry_values(info)
        frame["defined_cx"] = terms.get("display_cx", terms["cx"])
        frame["defined_cy"] = terms.get("display_cy", terms["cy"])
        frame["defined_kx"] = terms["shot_w_mm"]
        frame["defined_ky"] = terms["shot_h_mm"]
        frame["defined_grid_cols"] = int(terms.get("rc_cols") or 0)
        frame["defined_grid_rows"] = int(terms.get("rc_rows") or 0)
        frame["defined_offset_x_um"] = terms["offset_x_um"]
        frame["defined_offset_y_um"] = terms["offset_y_um"]
        frames.append(frame)
    return (pd.concat(frames, ignore_index=True) if frames else
            pd.DataFrame(columns=("vehicle", "x", "y", "r", "layout_source",
                                  "defined_cx", "defined_cy", "defined_kx", "defined_ky",
                                  "defined_grid_cols", "defined_grid_rows",
                                  "defined_offset_x_um", "defined_offset_y_um"))), path


def load_layout():
    """제품 정의를 primary로, Chip_Radius 계열 파일을 fallback으로 합친 layout."""
    import pandas as pd
    cfg = load_cfg()
    configured = resolve_path(cfg["layout_file"])
    candidates: list[Path] = []

    def add(path: Path) -> None:
        if path not in candidates:
            candidates.append(path)

    add(configured)
    # 개발 설정이 `*_test.csv`나 개발 절대경로를 가리킨 채 운영으로 넘어와도
    # 운영 표준 파일로 soft-land 한다.
    add(resolve_path(DEFAULT_CFG["layout_file"]))
    try:
        for path in sorted(roots.get_db_root().iterdir()):
            name = path.name.casefold()
            if (path.is_file() and path.suffix.casefold() in (".csv", ".parquet", ".xlsx", ".xls")
                    and (("chip" in name and "radius" in name)
                         or ("chip" in name and "layout" in name))):
                add(path)
    except OSError:
        pass

    fallback = None
    fallback_path = configured
    for path in candidates:
        if not path.is_file():
            continue
        try:
            df = _read_table(path)
        except Exception as e:
            logger.warning(f"layout 파일 읽기 실패 {path}: {e}")
            continue
        mc = _find_col(df, "mask", "vehicle")
        xc = _find_col(df, "chip_x_adj", "chip_x")
        yc = _find_col(df, "chip_y_adj", "chip_y")
        rc = _find_col(df, "chip_radius", "radius")
        if not (mc and xc and yc):
            logger.warning(f"layout 필수 열 누락 (Mask/chip_x_adj/chip_y_adj): {list(df.columns)}")
            continue
        out = pd.DataFrame({
            "vehicle": df[mc].astype(str).str.strip(),
            "x": pd.to_numeric(df[xc], errors="coerce"),
            "y": pd.to_numeric(df[yc], errors="coerce"),
        })
        out["r"] = pd.to_numeric(df[rc], errors="coerce") if rc else float("nan")
        out = out.dropna(subset=["x", "y"])
        if not out.empty:
            if path != configured:
                logger.warning("configured chip layout unavailable; fallback selected: %s → %s",
                               configured, path)
            out["layout_source"] = "chip_radius"
            fallback, fallback_path = out, path
            break

    primary, primary_path = _generated_product_layout(cfg)
    if not primary.empty:
        primary_names = {str(v).casefold() for v in primary["vehicle"].dropna().tolist()}
        if fallback is not None:
            fallback = fallback[~fallback["vehicle"].astype(str).str.casefold().isin(primary_names)]
            primary = pd.concat([primary, fallback], ignore_index=True, sort=False)
        return primary, fallback_path if fallback_path.is_file() else primary_path
    return (fallback, fallback_path) if fallback is not None else (None, configured)


def load_main_chips():
    """MAIN(die) 크기표 → ({vehicle: {chip_name: (w_mm, h_mm)}}, 경로).

    열: vehicle, chip_name, chipsize_x, chipsize_y. **크기는 µm 단위**라 mm 로
    나눠 담는다(ebeam_scale 과 무관 — 이 파일은 단위가 고정이다). 그림 모드에서
    die 사각형의 크기는 이 표가 1순위이고, 없을 때만 그림에서 인식한 크기를 쓴다.
    """
    cfg = load_cfg()
    path = resolve_path(cfg["main_chip_file"])
    if not path.is_file():
        return {}, path
    try:
        df = _read_table(path)
    except Exception as e:
        logger.warning(f"MAIN chip 파일 읽기 실패 {path}: {e}")
        return {}, path
    import pandas as pd
    vc = _find_col(df, "vehicle", "mask")
    nc = _find_col(df, "chip_name", "chipname", "chip", "main")
    xc = _find_col(df, "chipsize_x", "chip_size_x", "size_x")
    yc = _find_col(df, "chipsize_y", "chip_size_y", "size_y")
    if not (vc and nc and xc and yc):
        logger.warning("MAIN chip 필수 열 누락 (vehicle/chip_name/chipsize_x/chipsize_y): "
                       f"{list(df.columns)}")
        return {}, path
    out: dict[str, dict[str, tuple[float, float]]] = {}
    for veh, name, sx, sy in zip(df[vc].astype(str).str.strip(),
                                 df[nc].astype(str).str.strip(),
                                 pd.to_numeric(df[xc], errors="coerce"),
                                 pd.to_numeric(df[yc], errors="coerce")):
        if not veh or not name or not (sx == sx and sy == sy):
            continue
        w, h = float(sx) / 1000.0, float(sy) / 1000.0   # µm → mm
        if w <= 0 or h <= 0 or not (math.isfinite(w) and math.isfinite(h)):
            continue
        out.setdefault(veh, {})[name] = (w, h)
    return out, path


def load_main_chip_purposes():
    """MAIN purpose 표 → ({vehicle: {chip_name: purpose}}, 경로).

    ``purpose``는 선택 열이다. 기존 파일처럼 열이 없으면 빈 표를 돌려 기존 판정에
    영향을 주지 않는다. 값이 있는 모든 purpose의 배치 금지 판정은
    is_main_purpose_warning에서 담당한다.
    """
    cfg = load_cfg()
    path = resolve_path(cfg["main_chip_file"])
    if not path.is_file():
        return {}, path
    try:
        df = _read_table(path)
    except Exception as e:
        logger.warning(f"MAIN chip purpose 파일 읽기 실패 {path}: {e}")
        return {}, path
    vc = _find_col(df, "vehicle", "mask")
    nc = _find_col(df, "chip_name", "chipname", "chip", "main")
    pc = _find_col(df, "purpose")
    if not (vc and nc and pc):
        return {}, path
    out: dict[str, dict[str, str]] = {}
    for veh, name, purpose in zip(df[vc].fillna("").astype(str).str.strip(),
                                  df[nc].fillna("").astype(str).str.strip(),
                                  df[pc].fillna("").astype(str).str.strip()):
        if veh and name:
            out.setdefault(veh, {})[name] = purpose
    return out, path


def normalize_direction(value: Any, teg_name: str = "") -> str:
    """TEG 방향 → "h"(가로) | "v"(Vertical(R)) | "v_L"(Vertical(L)).

    판정 순서 (Mapfile 체크 체크리스트·체크 대상 기본값과 같은 규칙):
      1. direction(=flat_zone) 열 값
         · V / Vertical / Vertical(R) / v_R / VR / 세로 → v
         · Vertical(L) / v_L / VL / 왼쪽                    → v_L
         · H / Horizontal / 가로                        → h
         · 숫자 flat_zone — 0/180 → h, 270 → v(R), 90 → v_L
           90°는 notch가 왼쪽인 별도 방향이므로 v_R 계산에 합치지 않는다.
      2. 열이 없거나 빈 칸/모르는 값이면 **TEG 이름 접두** — V_ 로 시작하면 v
      3. 그래도 모르면 h

    2번이 없으면 direction 열이 없는 Teg_location 에서 V_ TEG 가 전부 가로로
    그려진다 (화면에 vertical 이 반영되지 않는다).
    """
    s = str("" if value is None else value).strip().lower()
    if s and s not in ("nan", "none"):
        try:
            ang = float(s)
        except ValueError:
            ang = None
        if ang is not None:
            norm = int(round(ang)) % 360
            if norm == 90:
                return "v_L"
            if norm == 270:
                return "v"
            if norm in (0, 180):
                return "h"
        if s.startswith(("v_l", "vl", "vertical(l)", "vertical l", "왼쪽")):
            return "v_L"
        if s.startswith(("v", "세로")):
            return "v"
        if s.startswith(("h", "가로")):
            return "h"
    name = str(teg_name or "").strip().upper()
    if name.startswith(("V_L", "VL_", "L_PCHK", "L_PRBCHK")):
        return "v_L"
    return "v" if name.startswith("V_") else "h"


def teg_size(raw_w: Any, raw_h: Any, scale: float, cfg: dict,
             direction: str, vehicle: str = "") -> tuple[float, float]:
    """Teg_location 한 행의 TEG 크기 (mm) → (w, h).

    **파일의 teg_w/teg_h 는 실제 배치 방향 그대로다.** vertical TEG 는 파일에
    이미 가로/세로가 뒤집힌 값으로 들어 있다 (가로 기본이 1000×50 이면 vertical
    행은 50×1000). 그래서 direction=V 라고 코드가 다시 스왑하면 **원래 가로로
    되돌아간다** — 화면에 vertical 이 반영되지 않는 그 증상이다. 파일 값은 그대로 쓴다.

    열이 없어 설정의 TEG 기본 사이즈(teg_default_w/h)로 채울 때만 그 값이
    가로 기준이라, vertical 이면 그때 세운다.
    """
    def _has(v) -> bool:
        return v is not None and v == v      # NaN 제외
    has_w, has_h = _has(raw_w), _has(raw_h)
    default_w, default_h = vehicle_teg_default_size(cfg, vehicle)
    w = float(raw_w) * scale if has_w else default_w
    h = float(raw_h) * scale if has_h else default_h
    # 한쪽만 있는 행은 파일 값을 존중한다 — 섞어서 스왑하면 더 틀어진다.
    if direction in ("v", "v_L") and not has_w and not has_h:
        w, h = h, w
    return w, h


def load_tegs():
    """Teg_location 파일 → (DataFrame[vehicle,teg,ebeam_x,ebeam_y,(teg_w,teg_h)], 경로)."""
    import pandas as pd
    cfg = load_cfg()
    path = resolve_path(cfg["teg_file"])
    if not path.is_file():
        return None, path
    try:
        df = _read_table(path)
    except Exception as e:
        logger.warning(f"teg 파일 읽기 실패 {path}: {e}")
        return None, path
    vc = _find_col(df, "vehicle", "mask")
    tc = _find_col(df, "teg")
    xc = _find_col(df, "ebeam_x")
    yc = _find_col(df, "ebeam_y")
    if not (vc and tc and xc and yc):
        logger.warning(f"teg 필수 열 누락 (vehicle/teg/ebeam_x/ebeam_y): {list(df.columns)}")
        return None, path
    out = pd.DataFrame({
        "vehicle": df[vc].astype(str).str.strip(),
        "teg": df[tc].astype(str).str.strip(),
        "ebeam_x": pd.to_numeric(df[xc], errors="coerce"),
        "ebeam_y": pd.to_numeric(df[yc], errors="coerce"),
    })
    # top_cell: TEG Mapfile 체크에서 module name 을 teg 뿐 아니라 top_cell 과도
    # 완전 일치로 대조하기 위한 선택 열. 없으면 빈 문자열.
    ccx = _find_col(df, "top_cell", "topcell")
    out["top_cell"] = df[ccx].fillna("").astype(str).str.strip() if ccx else ""
    wc = _find_col(df, "teg_w", "width")
    hc = _find_col(df, "teg_h", "height")
    out["teg_w"] = pd.to_numeric(df[wc], errors="coerce") if wc else float("nan")
    out["teg_h"] = pd.to_numeric(df[hc], errors="coerce") if hc else float("nan")
    # Optional geometry landmark. Values are in ebeam raw units and describe
    # the vector from the stored origin to the first-pad centre after the shape
    # has been normalised to Horizontal (first pad on the left).
    fpx = _find_col(df, "first_pad_dx", "firstpad_dx", "pad1_dx")
    fpy = _find_col(df, "first_pad_dy", "firstpad_dy", "pad1_dy")
    out["first_pad_dx"] = pd.to_numeric(df[fpx], errors="coerce") if fpx else float("nan")
    out["first_pad_dy"] = pd.to_numeric(df[fpy], errors="coerce") if fpy else float("nan")
    # direction(=flat_zone): H/Horizontal → h(기본, 가로) | V/Vertical → v(세로로 세움).
    # v 는 TEG 패턴을 세워 그린다 — 좌하단 좌표는 유지하고 가로/세로(w/h)만 스왑.
    # 열이 없거나 빈 칸이면 TEG 이름 접두(V_)로 폴백한다 — normalize_direction 참고.
    dc = _find_col(df, "direction", "flat_zone", "flatzone", "flat")
    raw = df[dc].tolist() if dc else [""] * len(out)
    out["flat_zone"] = [normalize_direction(raw[i], out["teg"].iat[i]) for i in range(len(out))]
    out = out.dropna(subset=["ebeam_x", "ebeam_y"])
    return out, path


# ────────────────────────────────────────── TEG Mapfile 체크 대상 TEG
def default_check_targets(names: list[str]) -> list[str]:
    """기본 체크 대상 — H_/V_/VL_/V_L 계열 (대소문자 무관, 순서 유지)."""
    out: list[str] = []
    for n in names:
        s = str(n or "").strip()
        up = s.upper()
        if s and up.startswith(("H_", "V_", "VL_", "V_L")):
            out.append(s)
    return out


def _teg_rows_for(vehicle: str) -> tuple[list[str], dict[str, list[str]], bool, str,
                                          dict[str, str]]:
    """vehicle 의 Teg_location 행 → (teg 이름 목록(중복 제거·순서 유지),
    {teg: [top_cell, ...]}, teg 파일 존재 여부, 파일 경로, {teg: direction}).

    같은 teg 이름이 여러 행이면 top_cell 을 모아 둔다 (완전 일치 대조용).
    direction 은 첫 행 기준 'h'|'v'|'v_L' — 한 Mapfile 로 판정할 수 있는 방향인지
    가리는 데 쓴다 (Mapfile 은 flat 하나 기준이라 반대 방향 TEG 는 안 나온다)."""
    tdf, path = load_tegs()
    if tdf is None:
        return [], {}, False, str(path), {}
    veh = str(vehicle or "").strip()
    sub = tdf[tdf["vehicle"] == veh]
    order: list[str] = []
    top_cells: dict[str, list[str]] = {}
    dirs: dict[str, str] = {}
    for _, row in sub.iterrows():
        teg = str(row["teg"]).strip()
        if not teg:
            continue
        if teg not in top_cells:
            top_cells[teg] = []
            order.append(teg)
            dirs[teg] = normalize_direction(row.get("flat_zone"), teg)
        tc = str(row.get("top_cell") or "").strip()
        if tc and tc not in top_cells[teg]:
            top_cells[teg].append(tc)
    return order, top_cells, True, str(path), dirs


def teg_target_options(vehicle: str) -> dict:
    """위치 조회의 '체크 대상 설정' UI 용 — vehicle 의 teg 목록 + 현재 대상 선택.

    반환: {ok, teg_ok, teg_path, tegs:[{teg, top_cell:[...], direction}], targets:[teg 이름],
           source:"config"|"default"}. source=config 는 관리자가 저장한 명시적 목록.
    """
    names, top_cells, teg_ok, path, dirs = _teg_rows_for(vehicle)
    cfg = load_cfg()
    stored = cfg["check_targets"].get(str(vehicle or "").strip())
    if isinstance(stored, list):
        source, targets = "config", list(stored)
    else:
        source, targets = "default", default_check_targets(names)
    return {
        "ok": True,
        "teg_ok": teg_ok,
        "teg_path": path,
        "tegs": [{"teg": t, "top_cell": top_cells.get(t, []),
                  "direction": dirs.get(t, "h")} for t in names],
        "targets": targets,
        "source": source,
    }


def target_verification(vehicle: str, module_tokens) -> dict:
    """체크 대상 TEG 가 module name 목록에 (teg 또는 top_cell 완전 일치로) 있는지 검증.

    module_tokens: 설비 원문에서 받은 module name(및 이름 후보) 집합.
    각 대상 teg 는 그 teg 이름 또는 top_cell 중 하나가 module_tokens 에 **완전히**
    일치하면 '설정됨(matched)', 아니면 '미설정(missing)'.
    반환: {source, items:[{teg, top_cell, direction, matched, matched_by, matched_module}],
           matched, missing, total}.

    direction 은 Teg_location 의 direction 열('h'|'v') — Mapfile 은 flat 하나
    기준이라 반대 방향 TEG 는 애초에 그 원문에 없는 게 정상이다. '미설정' 과
    '이 Mapfile 로는 판정 대상이 아님' 을 화면에서 가르는 근거로 쓴다.
    """
    tokens = {str(t).strip() for t in (module_tokens or []) if str(t).strip()}
    opts = teg_target_options(vehicle)
    tc_map = {row["teg"]: list(row["top_cell"]) for row in opts["tegs"]}
    dir_map = {row["teg"]: row.get("direction", "h") for row in opts["tegs"]}
    items: list[dict] = []
    for teg in opts["targets"]:
        tcs = tc_map.get(teg, [])
        by, mod = None, None
        if teg in tokens:                       # teg 이름 완전 일치
            by, mod = "teg", teg
        else:                                   # top_cell 완전 일치
            for tc in tcs:
                if tc in tokens:
                    by, mod = "top_cell", tc
                    break
        items.append({"teg": teg, "top_cell": tcs,
                      "direction": dir_map.get(teg, "h"),
                      "matched": by is not None,
                      "matched_by": by, "matched_module": mod})
    matched = sum(1 for i in items if i["matched"])
    return {"source": opts["source"], "items": items,
            "matched": matched, "missing": len(items) - matched, "total": len(items)}


# ────────────────────────────────────────── geometry
def fit_geometry(xs, ys, rs) -> dict | None:
    """Chip_Radius fit → {cx, cy, kx, ky} (격자→mm). 실패/degenerate 면 None.

    r² = A·x² + B·y² + p·x + q·y + C 최소자승 (Auto Report 와 동일).
    """
    import numpy as np
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    rs = np.asarray(rs, dtype=float)
    m = np.isfinite(xs) & np.isfinite(ys) & np.isfinite(rs)
    xs, ys, rs = xs[m], ys[m], rs[m]
    if len(xs) < 6 or float(np.nanstd(rs)) <= 1e-9:
        return None
    M = np.column_stack([xs ** 2, ys ** 2, xs, ys, np.ones_like(xs)])
    try:
        sol, *_ = np.linalg.lstsq(M, rs ** 2, rcond=None)
    except Exception:
        return None
    A, B, p, q, _C = [float(v) for v in sol]
    if not (math.isfinite(A) and math.isfinite(B) and A > 0 and B > 0):
        return None
    cx = -p / (2.0 * A)
    cy = -q / (2.0 * B)
    kx = A ** 0.5
    ky = B ** 0.5
    if not all(math.isfinite(v) for v in (cx, cy, kx, ky)):
        return None
    aspect = ky / kx
    if not (0.1 <= aspect <= 10.0):
        return None
    return {"cx": cx, "cy": cy, "kx": kx, "ky": ky}


def fit_geometry_diagnosed(xs, ys, rs) -> tuple[dict | None, dict]:
    """fit + Chip_Radius 이상치 진단.

    잘못 입력된 radius(오타/단위 실수)는 최소자승 해를 조용히 왜곡하거나
    아예 해를 못 찾게 만든다. 전략:
      1) 전체 fit 성공 → 잔차(|측정 r − fit r|, mm)가 큰 점을 잔차순으로
         제거·재fit (최대 20%, 남는 점 ≥6). 임계 = max(2mm, 6×중앙잔차).
      2) 전체 fit 실패 → leave-one-out 으로 단일 오염 행 탐지 (≤200샷).
    반환: (geo|None, diag) — diag = {used, dropped:[{x,y,r,residual_mm}],
    max_residual_mm, note}. dropped 는 UI 에 "의심 행"으로 노출한다."""
    pts = [(float(x), float(y), float(r)) for x, y, r in zip(xs, ys, rs)
           if all(math.isfinite(float(v)) for v in (x, y, r))]
    diag: dict[str, Any] = {"used": len(pts), "dropped": [], "max_residual_mm": 0.0, "note": ""}

    def _residuals(geo, sub):
        out = []
        for x, y, r in sub:
            rf = math.hypot((x - geo["cx"]) * geo["kx"], (y - geo["cy"]) * geo["ky"])
            out.append(abs(r - rf))
        return out

    def _fit(sub):
        return fit_geometry([p[0] for p in sub], [p[1] for p in sub], [p[2] for p in sub])

    cur = list(pts)
    geo = _fit(cur)
    if geo is None:
        # 단일 오염 행이 해 자체를 깨는 경우 — leave-one-out 시도.
        if 7 <= len(cur) <= 200:
            for i in range(len(cur)):
                trial = cur[:i] + cur[i + 1:]
                g = _fit(trial)
                if g is not None:
                    x, y, r = cur[i]
                    rf = math.hypot((x - g["cx"]) * g["kx"], (y - g["cy"]) * g["ky"])
                    diag["dropped"].append({"x": x, "y": y, "r": r, "residual_mm": round(abs(r - rf), 3)})
                    diag["used"] = len(trial)
                    diag["note"] = "leave-one-out 으로 오염 행 1개 제외 후 fit 성공"
                    resid = _residuals(g, trial)
                    diag["max_residual_mm"] = round(max(resid), 3) if resid else 0.0
                    return g, diag
        diag["note"] = "fit 불가 — 샷 6개 미만이거나 radius 오염이 2행 이상일 수 있음"
        return None, diag

    # 성공 → 잔차 큰 점을 반복 제거 (최대 20%).
    max_drop = max(0, min(len(cur) - 6, int(len(cur) * 0.2)))
    for _ in range(max_drop):
        resid = _residuals(geo, cur)
        med = sorted(resid)[len(resid) // 2]
        threshold = max(2.0, 6.0 * med)
        worst_i = max(range(len(resid)), key=lambda i: resid[i])
        if resid[worst_i] <= threshold:
            break
        x, y, r = cur.pop(worst_i)
        diag["dropped"].append({"x": x, "y": y, "r": r, "residual_mm": round(resid[worst_i], 3)})
        g = _fit(cur)
        if g is None:  # 제거가 오히려 해를 깨면 되돌림
            cur.append((x, y, r))
            diag["dropped"].pop()
            break
        geo = g
    resid = _residuals(geo, cur)
    diag["used"] = len(cur)
    diag["max_residual_mm"] = round(max(resid), 3) if resid else 0.0
    if diag["dropped"]:
        diag["note"] = f"Chip_Radius 이상치 {len(diag['dropped'])}개 제외 후 fit"
    return geo, diag


def _grid_pitch(vals) -> float:
    """정렬된 unique 좌표값의 양수 diff 중앙값 (Auto Report _wfmap_shot_pitch)."""
    import numpy as np
    u = np.unique(np.round(np.asarray(vals, dtype=float), 6))
    if len(u) < 2:
        return 1.0
    dif = np.diff(u)
    dif = dif[dif > 0]
    return float(np.median(dif)) if len(dif) else 1.0


def vehicles() -> list[str]:
    lay, _ = load_layout()
    if lay is None or lay.empty:
        return []
    return sorted(lay["vehicle"].dropna().unique().tolist())


def _catalog_column(columns: list[str], *candidates: str) -> str | None:
    """Column lookup for the lightweight product catalog path (no pandas import)."""
    lookup = {str(column).strip().casefold(): str(column) for column in columns}
    for candidate in candidates:
        if candidate.casefold() in lookup:
            return lookup[candidate.casefold()]
    for candidate in candidates:
        needle = candidate.casefold()
        for normalized, original in lookup.items():
            if needle in normalized:
                return original
    return None


def _catalog_layout_vehicles(path: Path) -> list[str] | None:
    """Read only the product keys needed by the selector.

    The old selector called ``load_layout()``, which imports pandas and expands
    every product into its full shot geometry before returning a few names.
    CSV is the normal operational format, so keep that hot path in the standard
    library. Parquet/Excel remain supported through the existing reader.
    """
    if not path.is_file():
        return None
    if path.suffix.casefold() == ".csv":
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
                reader = csv.DictReader(stream)
                columns = [str(column) for column in (reader.fieldnames or [])]
                vehicle_col = _catalog_column(columns, "mask", "vehicle")
                x_col = _catalog_column(columns, "chip_x_adj", "chip_x")
                y_col = _catalog_column(columns, "chip_y_adj", "chip_y")
                if not (vehicle_col and x_col and y_col):
                    return None
                rows: dict[str, str] = {}
                for row in reader:
                    vehicle = str(row.get(vehicle_col) or "").strip()
                    try:
                        x = float(str(row.get(x_col) or "").strip())
                        y = float(str(row.get(y_col) or "").strip())
                    except (TypeError, ValueError):
                        continue
                    if vehicle and math.isfinite(x) and math.isfinite(y):
                        rows.setdefault(vehicle.casefold(), vehicle)
                return list(rows.values()) or None
        except OSError:
            return None

    try:
        frame = _read_table(path)
    except Exception as exc:
        logger.warning("TEG 제품 목록 파일 읽기 실패 %s: %s", path, exc)
        return None
    vehicle_col = _find_col(frame, "mask", "vehicle")
    x_col = _find_col(frame, "chip_x_adj", "chip_x")
    y_col = _find_col(frame, "chip_y_adj", "chip_y")
    if not (vehicle_col and x_col and y_col):
        return None
    import pandas as pd
    valid = pd.to_numeric(frame[x_col], errors="coerce").notna() & pd.to_numeric(frame[y_col], errors="coerce").notna()
    names = frame.loc[valid, vehicle_col].fillna("").astype(str).str.strip()
    return list(dict.fromkeys(name for name in names if name)) or None


def _catalog_product_info(cfg: dict) -> dict[str, dict[str, str]]:
    """Return renderable Product Info rows keyed by case-folded vehicle."""
    path = product_info_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = [str(column) for column in (reader.fieldnames or [])]
            lookup = {column.casefold(): column for column in columns}
            if any(column.casefold() not in lookup for column in PRODUCT_GEOMETRY_COLUMNS):
                return {}
            out: dict[str, dict[str, str]] = {}
            for row in reader:
                vehicle = str(row.get(lookup["vehicle"]) or "").strip()
                if not vehicle:
                    continue
                radius = vehicle_wafer_edge_mm(cfg, vehicle)
                try:
                    values = {
                        column: float(str(row.get(lookup[column.casefold()]) or "").strip())
                        for column in PRODUCT_GEOMETRY_COLUMNS[1:]
                    }
                except (TypeError, ValueError):
                    continue
                if not all(math.isfinite(value) for value in values.values()):
                    continue
                try:
                    terms = _product_geometry_values(values)
                except ValueError:
                    continue
                shot_x = terms["shot_w_mm"]
                shot_y = terms["shot_h_mm"]
                if radius <= 0 or shot_x <= 0 or shot_y <= 0:
                    continue
                # Nearest integer shot centre is also the best possible full-shot
                # fit. Reject the same impossible products that load_layout omits.
                cx = terms["cx"]
                cy = terms["cy"]
                nearest_x = round(cx)
                nearest_y = round(cy)
                farthest_corner = math.hypot(
                    abs((nearest_x - cx) * shot_x) + shot_x / 2.0,
                    abs((nearest_y - cy) * shot_y) + shot_y / 2.0,
                )
                estimate = (
                    math.ceil(cx + radius / shot_x) - math.floor(cx - radius / shot_x) + 1
                ) * (
                    math.ceil(cy + radius / shot_y) - math.floor(cy - radius / shot_y) + 1
                )
                if estimate > REFERENCE_MAX_ROWS or farthest_corner > radius + 1e-10:
                    continue
                node_col = lookup.get("node_path")
                out[vehicle.casefold()] = {
                    "vehicle": vehicle,
                    "node_path": clean_node_path(row.get(node_col) if node_col else ""),
                }
            return out
    except OSError as exc:
        logger.warning("TEG Product Info 목록 읽기 실패 %s: %s", path, exc)
        return {}


def _catalog_fallback_vehicles(cfg: dict) -> list[str]:
    """Find the first usable layout source without expanding shot geometry."""
    candidates: list[Path] = []

    def add(path: Path) -> None:
        if path not in candidates:
            candidates.append(path)

    add(resolve_path(cfg["layout_file"]))
    add(resolve_path(DEFAULT_CFG["layout_file"]))
    for path in candidates:
        names = _catalog_layout_vehicles(path)
        if names:
            return names

    # Preserve the legacy soft-landing search, but only pay for a DB-root scan
    # when both the configured and standard files are unusable.
    try:
        for path in sorted(roots.get_db_root().iterdir()):
            name = path.name.casefold()
            if (path.is_file() and path.suffix.casefold() in (".csv", ".parquet", ".xlsx", ".xls")
                    and (("chip" in name and "radius" in name)
                         or ("chip" in name and "layout" in name))):
                add(path)
    except OSError:
        return []
    for path in candidates[2:]:
        names = _catalog_layout_vehicles(path)
        if names:
            return names
    return []


def product_catalog() -> list[dict[str, str]]:
    """모든 제품의 노드 경로. 목록 조회에서는 전체 shot geometry를 만들지 않는다."""
    cfg = load_cfg()
    configured = {str(k).casefold(): clean_node_path(v)
                  for k, v in (cfg.get("product_nodes") or {}).items()}
    product_info = _catalog_product_info(cfg)
    names = {str(vehicle).casefold(): str(vehicle) for vehicle in _catalog_fallback_vehicles(cfg)}
    # Product Info is primary in load_layout too, including its original casing.
    names.update({key: row["vehicle"] for key, row in product_info.items()})
    rows = []
    for key, vehicle in names.items():
        node_path = configured.get(key) or product_info.get(key, {}).get("node_path") or "미분류"
        root_node = node_path.split(" / ", 1)[0]
        rows.append({
            "vehicle": str(vehicle),
            "node_path": node_path,
            "root_node": root_node,
            "full_path": f"{node_path} / {vehicle}",
        })
    return sorted(rows, key=lambda row: (row["node_path"].casefold(), row["vehicle"].casefold()))


def user_departments(user: dict | None) -> list[str]:
    """현재/향후 SSO 세션의 부서 claim을 권한 비교용 문자열 목록으로 정리."""
    user = user if isinstance(user, dict) else {}
    claims = user.get("claims") if isinstance(user.get("claims"), dict) else {}
    values: list[Any] = []
    for source in (user, claims):
        for key in ("department", "departments", "dept", "department_name", "org", "org_name"):
            value = source.get(key)
            if isinstance(value, (list, tuple, set)):
                values.extend(value)
            elif value not in (None, ""):
                values.extend(str(value).split(","))
    return _clean_access_values(values)


def can_access_root_node(user: dict | None, root_node: str, rules: dict | None = None) -> bool:
    user = user if isinstance(user, dict) else {}
    if user.get("role") == "admin":
        return True
    rules = rules if isinstance(rules, dict) else (load_cfg().get("node_access") or {})
    rule = next((value for root, value in rules.items()
                 if str(root).casefold() == str(root_node or "").strip().casefold()), None)
    if rule is None:
        return True
    username = str(user.get("username") or "").strip().casefold()
    allowed_users = {str(value).casefold() for value in rule.get("users") or []}
    allowed_departments = {str(value).casefold() for value in rule.get("departments") or []}
    departments = {value.casefold() for value in user_departments(user)}
    return bool((username and username in allowed_users) or departments.intersection(allowed_departments))


def can_access_node_path(user: dict | None, node_path: str) -> bool:
    cleaned = clean_node_path(node_path)
    return bool(cleaned and can_access_root_node(user, cleaned.split(" / ", 1)[0]))


def _can_access_catalog_row(user: dict, row: dict[str, str], rules: dict) -> bool:
    return can_access_root_node(user, row["root_node"], rules)


def can_access_product(user: dict | None, vehicle: str) -> bool:
    """admin 전체 허용, 규칙 없는 대분류 공개, 규칙이 있으면 사용자/부서 일치."""
    user = user if isinstance(user, dict) else {}
    row = next((item for item in product_catalog()
                if item["vehicle"].casefold() == str(vehicle or "").strip().casefold()), None)
    return bool(row and _can_access_catalog_row(user, row, load_cfg().get("node_access") or {}))


def visible_product_catalog(user: dict | None) -> list[dict[str, str]]:
    user = user if isinstance(user, dict) else {}
    rules = load_cfg().get("node_access") or {}
    return [row for row in product_catalog() if _can_access_catalog_row(user, row, rules)]


def map_payload(vehicle: str) -> dict:
    """vehicle 의 WF MAP 전체 payload — geometry + shot 목록 + TEG 목록 + 표시 설정."""
    cfg = load_cfg()
    lay, lay_path = load_layout()
    if lay is None:
        raise FileNotFoundError(f"chip layout 파일 없음/무효: {lay_path}")
    veh = str(vehicle).strip()
    wafer_edge_mm = vehicle_wafer_edge_mm(cfg, veh)
    sub = lay[lay["vehicle"] == veh]
    if sub.empty:
        raise LookupError(f"layout 에 vehicle 없음: {vehicle}")
    # shot 레이아웃만: (x,y) 중복 제거(대표 radius = 중앙값) — 측정 밀도 편향 제거
    grp = sub.groupby(["x", "y"], as_index=False)["r"].median()
    xs = grp["x"].tolist()
    ys = grp["y"].tolist()
    rs = grp["r"].tolist()

    source = str(sub["layout_source"].iloc[0]) if "layout_source" in sub.columns else "chip_radius"
    if source == "product_info":
        first = sub.iloc[0]
        geo = {
            "cx": float(first["defined_cx"]), "cy": float(first["defined_cy"]),
            "kx": float(first["defined_kx"]), "ky": float(first["defined_ky"]),
            "map_offset_odd_x_um": float(first["defined_offset_x_um"]),
            "map_offset_odd_y_um": float(first["defined_offset_y_um"]),
            "grid_cols": int(first.get("defined_grid_cols") or 0),
            "grid_rows": int(first.get("defined_grid_rows") or 0),
        }
        fit_diag = {
            "used": len(grp), "dropped": [], "max_residual_mm": 0.0,
            "note": "제품 추가 정보(Shot Size / Map offset(Odd)) 직접 계산",
        }
    else:
        geo, fit_diag = fit_geometry_diagnosed(xs, ys, rs)
    pitch_x = _grid_pitch(xs)
    pitch_y = _grid_pitch(ys)

    exact_geometry = source == "product_info"
    output_decimals = PRODUCT_RADIUS_DECIMALS if exact_geometry else 4
    shots = []
    for x, y, r in zip(xs, ys, rs):
        s: dict[str, Any] = {"x": x, "y": y}
        if r == r:  # not NaN
            s["r"] = float(r)
        if geo:
            mmx = (x - geo["cx"]) * geo["kx"]
            mmy = (y - geo["cy"]) * geo["ky"]
            s["mm_x"] = round(mmx, output_decimals)
            s["mm_y"] = round(mmy, output_decimals)
            s["radius"] = round(math.hypot(mmx, mmy), output_decimals)
        shots.append(s)

    tegs = []
    tdf, teg_path = load_tegs()
    if tdf is not None:
        scale = float(cfg["ebeam_scale"])
        tsub = tdf[tdf["vehicle"] == veh]
        try:
            chip_table = load_main_chips()[0]
        except Exception:
            chip_table = {}
        # 목록 순서 = **Teg_location 에 저장된 순서** (이 vehicle 행만 걸러 낸 순서).
        # 이름/좌표로 다시 정렬하지 않는다 — 파일은 보통 공정 순서로 정리돼 있고,
        # 화면 목록·좌표 패널·radius 표 열 순서가 모두 이 순서를 그대로 따른다.
        for src_row, (_, row) in enumerate(tsub.iterrows()):
            # 파일의 teg_w/teg_h 는 실제 배치 방향 그대로(vertical 은 이미 스왑된 값).
            # 열이 없어 기본 사이즈로 채울 때만 vertical 이면 세운다 — teg_size 참고.
            fz = normalize_direction(row.get("flat_zone"), row["teg"])
            tw, th = teg_size(row["teg_w"], row["teg_h"], scale, cfg, fz, veh)
            item = {
                "teg": row["teg"],
                "src_row": src_row,   # 이 vehicle 안에서의 파일 행 순번 (0-based)
                # teg 는 아래 동명 넘버링에서 "_1" 이 붙을 수 있다. teg_src 는 파일의
                # 원래 이름 — MAIN 크기 조회·die 앵커는 표시 이름이 아니라 이걸 쓴다.
                "teg_src": str(row["teg"]),
                "ebeam_x": float(row["ebeam_x"]) * scale,
                "ebeam_y": float(row["ebeam_y"]) * scale,
                "teg_w": tw,
                "teg_h": th,
                "flat_zone": fz,
            }
            # MAIN 계열 TEG 는 die 급 블록이라 teg_w/teg_h(패턴 크기)가 아니라
            # Main_chip_info 의 chip 크기로 그린다 — 크기표에 없으면 붙이지 않는다
            # (근거 없는 사각형을 그리지 않는 규칙, 화면이 점만 찍는다).
            if MAIN_RE.search(str(row["teg"])):
                size = chip_size_for(veh, item["teg_src"], chip_table)
                if size:
                    item["chip_w"], item["chip_h"] = size
            tegs.append(item)
        # ── 같은 이름·같은 자리 행 접기 ──
        # Teg_location 은 top_cell 등 부가 열 때문에 **같은 TEG 가 같은 좌표로 여러 줄**
        # 나오는 경우가 흔하다(체크 대상 목록 _teg_rows_for 도 이미 이름으로 합친다).
        # 그런 줄까지 _1, _2 로 번호를 붙이면 목록이 통째로 "_1" 투성이가 되고,
        # 이름이 달라져 MAIN 크기 조회까지 어긋난다. 위치가 같으면 한 줄로 접는다.
        def _same_spot(t: dict) -> tuple:
            return (t["teg_src"], round(t["ebeam_x"], 6), round(t["ebeam_y"], 6),
                    round(t["teg_w"], 6), round(t["teg_h"], 6))
        folded, spots = [], set()
        for t in tegs:
            k = _same_spot(t)
            if k in spots:
                continue
            spots.add(k)
            folded.append(t)
        tegs = folded
        # ── 동명 TEG 자동 넘버링: **자리가 다른** 동명이 2 개 이상일 때만 _1, _2, … ──
        from collections import Counter
        name_counts = Counter(t["teg"] for t in tegs)
        dup_names = {n for n, c in name_counts.items() if c > 1}
        if dup_names:
            counters: dict[str, int] = {}
            for t in tegs:
                orig = t["teg"]
                if orig in dup_names:
                    counters[orig] = counters.get(orig, 0) + 1
                    t["teg"] = f"{orig}_{counters[orig]}"

    vcfg = cfg["vehicles"].get(veh) or dict(DEFAULT_VEHICLE_CFG)
    has_image = bool(vcfg.get("image")) and (teg_dir() / Path(vcfg["image"]).name).is_file()
    profile = check_profile(cfg, veh)
    return {
        "ok": True,
        "vehicle": veh,
        "geometry": {
            "fit": "radius" if geo else "none",
            **({"cx": round(geo["cx"], PRODUCT_RADIUS_DECIMALS if exact_geometry else 6),
                "cy": round(geo["cy"], PRODUCT_RADIUS_DECIMALS if exact_geometry else 6),
                "kx": round(geo["kx"], PRODUCT_RADIUS_DECIMALS if exact_geometry else 6),
                "ky": round(geo["ky"], PRODUCT_RADIUS_DECIMALS if exact_geometry else 6),
                # product_info의 kx/ky 자체가 붙여넣은 Shot Size다. 격자 pitch를
                # 다시 곱하거나 Chip_Radius를 fit하지 않고 원본값을 그대로 쓴다.
                "shot_w_mm": round(geo["kx"] if exact_geometry else pitch_x * geo["kx"],
                                     PRODUCT_RADIUS_DECIMALS if exact_geometry else 4),
                "shot_h_mm": round(geo["ky"] if exact_geometry else pitch_y * geo["ky"],
                                     PRODUCT_RADIUS_DECIMALS if exact_geometry else 4),
                **({"map_offset_odd_x_um": round(geo["map_offset_odd_x_um"], PRODUCT_RADIUS_DECIMALS),
                    "map_offset_odd_y_um": round(geo["map_offset_odd_y_um"], PRODUCT_RADIUS_DECIMALS),
                    "grid_cols": int(geo.get("grid_cols") or 0),
                    "grid_rows": int(geo.get("grid_rows") or 0)}
                   if exact_geometry else {})} if geo else {}),
            "pitch_x": pitch_x,
            "pitch_y": pitch_y,
            "wafer_radius_mm": float(cfg["wafer_radius_mm"]),
            "wafer_edge_mm": wafer_edge_mm,
            # Chip_Radius 오입력 진단 — 이상치로 제외된 행과 fit 잔차를 UI 에 노출.
            "fit_used": fit_diag.get("used", 0),
            "fit_dropped": fit_diag.get("dropped", []),
            "fit_max_residual_mm": fit_diag.get("max_residual_mm", 0.0),
            "fit_note": fit_diag.get("note", ""),
        },
        "shots": shots,
        "tegs": tegs,
        "display": {**vcfg, "has_image": has_image},
        "layout_source": source,
        "coordinate_model": {
            "normalised_frame": "Horizontal; PCHK and target TEG share one origin convention",
            "shape_policy": "teg_w/teg_h affect rectangles and overlap only; shape position differences use product ΔX/ΔY",
            "rotation": {"h": "(u,v)", "v_R": "(v,-u)", "v_L": "(-v,u)"},
            "global_flat_base": profile["global"].get("flat_offsets") or {},
            "product_flat_corrections": profile["flat_corrections"],
            "global_module_count": len(profile["global"].get("modules") or []),
            "product_module_count": len(profile["product"].get("modules") or []),
            "formula": {
                "inspect": "Ocalc = Obase + R(flat)·p + Cproduct + Kglobal + Kproduct",
                "generate": "p = R(flat)^-1·(Otarget - Obase - Cproduct - Kglobal - Kproduct)",
            },
        },
        "layout_path": str(lay_path),
        "teg_path": str(teg_path) if tdf is not None else "",
    }


def teg_radius_table(vehicle: str, teg: str) -> dict:
    """특정 TEG 의 shot 별 좌하단 실좌표(mm)·원점 radius 표."""
    payload = map_payload(vehicle)
    tsel = [t for t in payload["tegs"] if t["teg"] == str(teg).strip()]
    if not tsel:
        raise LookupError(f"TEG 없음: {teg}")
    t = tsel[0]
    if payload["geometry"]["fit"] != "radius":
        raise ValueError("Chip_Radius fit 불가 — 실좌표(mm)를 계산할 수 없습니다")
    decimals = PRODUCT_RADIUS_DECIMALS if payload.get("layout_source") == "product_info" else 4
    rows = []
    for s in payload["shots"]:
        ax = s["mm_x"] + t["ebeam_x"]
        # chip_y_adj/WF-map y is down-positive; ebeam_y is up-positive.
        ay = -s["mm_y"] + t["ebeam_y"]
        rows.append({
            "shot_x": s["x"], "shot_y": s["y"],
            "abs_x": round(ax, decimals), "abs_y": round(ay, decimals),
            "radius": round(math.hypot(ax, ay), decimals),
        })
    rows.sort(key=lambda r: r["radius"])
    return {"ok": True, "vehicle": payload["vehicle"], "teg": t["teg"],
            "ebeam_x": t["ebeam_x"], "ebeam_y": t["ebeam_y"], "rows": rows}


REFERENCE_FILE_KEYS = {
    "chip_radius": ("layout_file", ("vehicle|mask", "chip_x_adj", "chip_y_adj", "chip_radius")),
    "main_chip_info": ("main_chip_file", ("vehicle", "chip_name", "chipsize_x", "chipsize_y")),
    "teg_location": ("teg_file", ("vehicle", "teg", "ebeam_x", "ebeam_y")),
}
REFERENCE_MAX_ROWS = 50000
REFERENCE_MAX_CELLS = 2_000_000


def reference_file_path(kind: str) -> Path:
    key = str(kind or "").strip().lower()
    spec = REFERENCE_FILE_KEYS.get(key)
    if not spec:
        raise ValueError("허용되지 않은 TEG 기준 파일입니다")
    path = resolve_path(load_cfg()[spec[0]]).resolve()
    root = roots.get_db_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("TEG 기준 파일은 DB root 안에 있어야 합니다") from exc
    return path


def reference_files_payload() -> dict:
    items = []
    root = roots.get_db_root().resolve()
    for kind in REFERENCE_FILE_KEYS:
        path = reference_file_path(kind)
        relative = path.relative_to(root).as_posix()
        stat = path.stat() if path.is_file() else None
        items.append({
            "kind": kind,
            "name": path.name,
            "path": relative,
            "absolute_path": str(path),
            "exists": path.is_file(),
            "editable": path.suffix.lower() in (".csv", ".parquet"),
            "size": stat.st_size if stat else 0,
            "modified": stat.st_mtime if stat else 0,
            "ext": path.suffix.lower().lstrip("."),
            "source": "db_root",
            "role": "TEG reference",
            "description": {
                "chip_radius": "Chip_Radius 기준파일",
                "main_chip_info": "Main_chip_info 기준파일",
                "teg_location": "Teg_location 기준파일",
            }.get(kind, kind),
        })
    return {"ok": True, "files": items}


def read_reference_file(kind: str) -> dict:
    path = reference_file_path(kind)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    df = _read_table(path)
    if len(df) > REFERENCE_MAX_ROWS or len(df) * max(1, len(df.columns)) > REFERENCE_MAX_CELLS:
        raise ValueError(f"TEG 페이지 편집 상한을 넘었습니다: {len(df):,}행 × {len(df.columns):,}열")
    import pandas as pd
    rows = [["" if pd.isna(value) else str(value) for value in row]
            for row in df.itertuples(index=False, name=None)]
    return {"ok": True, "kind": str(kind).lower(), "name": path.name, "path": str(path),
            "columns": [str(c) for c in df.columns], "rows": rows, "total": len(rows),
            "editable": path.suffix.lower() in (".csv", ".xlsx"),
            "source_modified_ns": path.stat().st_mtime_ns}


def _validate_reference_columns(kind: str, columns: list[str]) -> None:
    norms = {str(c).strip().casefold() for c in columns}
    missing = []
    for token in REFERENCE_FILE_KEYS[str(kind).lower()][1]:
        aliases = {x.casefold() for x in token.split("|")}
        if not norms.intersection(aliases):
            missing.append(token.replace("|", "/"))
    if missing:
        raise ValueError("필수 열이 없습니다: " + ", ".join(missing))


def _validate_reference_rows(kind: str, columns: list[str], rows: list[list[str]]) -> None:
    index = {str(c).strip().casefold(): i for i, c in enumerate(columns)}
    aliases = {
        "vehicle": next((index[x] for x in ("vehicle", "mask") if x in index), None),
        "teg": index.get("teg"), "chip_name": index.get("chip_name"),
        "chip_x_adj": index.get("chip_x_adj"), "chip_y_adj": index.get("chip_y_adj"),
        "chip_radius": index.get("chip_radius"), "ebeam_x": index.get("ebeam_x"),
        "ebeam_y": index.get("ebeam_y"), "chipsize_x": index.get("chipsize_x"),
        "chipsize_y": index.get("chipsize_y"),
    }
    required_text = ["vehicle"] + (["teg"] if kind == "teg_location" else ["chip_name"] if kind == "main_chip_info" else [])
    numeric = (["chip_x_adj", "chip_y_adj", "chip_radius"] if kind == "chip_radius" else
               ["chipsize_x", "chipsize_y"] if kind == "main_chip_info" else ["ebeam_x", "ebeam_y"])
    for optional in ("teg_w", "teg_h", "first_pad_dx", "first_pad_dy"):
        if kind == "teg_location" and optional in index:
            aliases[optional] = index[optional]
            numeric.append(optional)
    errors: list[str] = []
    for row_no, row in enumerate(rows, 2):
        for key in required_text:
            ci = aliases.get(key)
            if ci is not None and not str(row[ci] if ci < len(row) else "").strip():
                errors.append(f"{row_no}행 {columns[ci]}: 빈 값")
        for key in numeric:
            ci = aliases.get(key)
            if ci is None:
                continue
            value = str(row[ci] if ci < len(row) else "").strip()
            if not value and key in ("teg_w", "teg_h", "first_pad_dx", "first_pad_dy"):
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                number = float("nan")
            if not math.isfinite(number):
                errors.append(f"{row_no}행 {columns[ci]}: 숫자가 아님 ({value!r})")
        if kind == "teg_location" and "first_pad_dx" in index and "first_pad_dy" in index:
            vx = str(row[index["first_pad_dx"]] if index["first_pad_dx"] < len(row) else "").strip()
            vy = str(row[index["first_pad_dy"]] if index["first_pad_dy"] < len(row) else "").strip()
            if bool(vx) != bool(vy):
                errors.append(f"{row_no}행 first_pad_dx/dy: 두 값을 함께 입력해야 함")
        if len(errors) >= 20:
            break
    if errors:
        raise ValueError("기준 파일 검증 실패: " + "; ".join(errors))


def _snapshot_edm_file(path: Path, username: str, note: str,
                       backup: Path | None = None) -> dict | None:
    """FileBrowser와 같은 EDM 단일파일 버전 저장소에 변경본을 남긴다."""
    try:
        from routers import filebrowser as _fb
        relative = path.resolve().relative_to(roots.get_db_root().resolve()).as_posix()
        meta = _fb._snapshot_base_file_version(
            path, relative, actor=str(username or ""), action="edit",
            note=str(note or "TEG product edit"),
            diff_previous=backup if backup and backup.is_file() else None,
        )
        if meta:
            _fb._archive_base_file_every_n_edits(path, meta)
        return meta
    except Exception as exc:
        # 원본 저장 성공을 EDM 보조 이력 문제로 되돌리지는 않되 운영 로그에는 남긴다.
        logger.warning("TEG EDM version snapshot skipped file=%s: %s", path, exc)
        return None


def save_reference_file(kind: str, columns: list[str], rows: list[list[Any]], username: str,
                        note: str = "", expected_modified_ns: int | None = None) -> dict:
    key = str(kind or "").strip().lower()
    path = reference_file_path(key)
    if path.suffix.lower() not in (".csv", ".xlsx"):
        raise ValueError("CSV/XLSX 기준 파일만 표 편집을 지원합니다")
    cols = [str(c or "").strip() for c in columns]
    if not cols or any(not c for c in cols) or len({c.casefold() for c in cols}) != len(cols):
        raise ValueError("열 이름은 비어 있거나 중복될 수 없습니다")
    if len(rows) > REFERENCE_MAX_ROWS or len(rows) * len(cols) > REFERENCE_MAX_CELLS:
        raise ValueError("편집 가능한 행/셀 상한을 넘었습니다")
    _validate_reference_columns(key, cols)
    normalised = []
    for row in rows:
        values = list(row or [])[:len(cols)]
        values += [""] * (len(cols) - len(values))
        normalised.append(["" if value is None else str(value) for value in values])
    _validate_reference_rows(key, cols, normalised)
    if expected_modified_ns is not None and path.is_file() and path.stat().st_mtime_ns != int(expected_modified_ns):
        raise ValueError("다른 사용자가 파일을 먼저 변경했습니다. 다시 읽은 뒤 수정해 주세요")
    import pandas as pd
    frame = pd.DataFrame(normalised, columns=cols)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = None
    if path.is_file():
        history = teg_dir() / "reference_versions" / key
        history.mkdir(parents=True, exist_ok=True)
        backup = history / f"{stamp}_{path.name}"
        shutil.copy2(path, backup)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp{path.suffix}")
    try:
        if path.suffix.lower() == ".xlsx":
            frame.to_excel(temp, index=False)
        else:
            frame.to_csv(temp, index=False, encoding="utf-8-sig", lineterminator="\n")
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
    version = _snapshot_edm_file(path, username, note, backup)
    logger.info("TEG reference saved kind=%s rows=%d actor=%s note=%s", key, len(frame), username, note)
    return {"ok": True, "kind": key, "path": str(path), "rows": len(frame), "cols": len(cols),
            "backup": str(backup) if backup else "", "version": version}


def product_info_preview(text: str, vehicle: str = "") -> dict:
    info = parse_product_info_table(text)
    edge = vehicle_wafer_edge_mm(load_cfg(), vehicle)
    shots = _product_shots(info, edge)
    one_by_one = info["shot_cols"] == 1 and info["shot_rows"] == 1
    return {
        "ok": True, "values": info, "shot_count": len(shots),
        "wafer_edge_mm": edge, "radius_decimals": PRODUCT_RADIUS_DECIMALS,
        "one_by_one": one_by_one,
        "display": {
            "mode": "none" if one_by_one else "grid",
            "cols": info["shot_cols"], "rows": info["shot_rows"],
            "chip_w": info["chip_size_x_um"] / 1000.0,
            "chip_h": info["chip_size_y_um"] / 1000.0,
            "gap_x": info["sl_size_x_um"] / 1000.0,
            "gap_y": info["sl_size_y_um"] / 1000.0,
        },
    }


def _append_frame_rows(kind: str, additions: list[dict[str, Any]], username: str,
                       note: str, replace_vehicle: str = "") -> dict:
    """기존 열 순서를 보존하고 기준 CSV에 행을 추가한다.

    ``replace_vehicle``가 있으면 그 제품의 기존 행을 먼저 제거한다. 기존
    Chip_Radius 기반 제품을 명시 geometry로 전환할 때 낡은 radius 행과 새 행이
    섞이지 않게 하는 원자적 제품 단위 교체 경로다.
    """
    import pandas as pd
    path = reference_file_path(kind)
    if path.is_file():
        frame = _read_table(path)
        columns = [str(c) for c in frame.columns]
    else:
        columns = (["vehicle", "teg", "top_cell", "direction", "ebeam_x", "ebeam_y", "teg_w", "teg_h"]
                   if kind == "teg_location" else
                   ["vehicle", "chip_name", "chipsize_x", "chipsize_y", "purpose"]
                   if kind == "main_chip_info" else
                   ["Mask", "chip_x_adj", "chip_y_adj", "Chip_Radius"])
        frame = pd.DataFrame(columns=columns)

    def exact(*names: str) -> str | None:
        lookup = {str(c).strip().casefold(): str(c) for c in columns}
        return next((lookup[name.casefold()] for name in names if name.casefold() in lookup), None)

    if kind == "teg_location":
        optional = (("top_cell", ("top_cell", "topcell")),
                    ("direction", ("direction", "flat_zone", "flatzone", "flat")),
                    ("teg_w", ("teg_w",)), ("teg_h", ("teg_h",)))
        for canonical, aliases in optional:
            if exact(*aliases) is None:
                columns.append(canonical)
                frame[canonical] = ""
    rows: list[dict[str, Any]] = []
    for addition in additions:
        row = {column: "" for column in columns}
        for key, value in addition.items():
            aliases = {
                "vehicle": ("vehicle", "mask"), "teg": ("teg",),
                "top_cell": ("top_cell", "topcell"),
                "direction": ("direction", "flat_zone", "flatzone", "flat"),
                "ebeam_x": ("ebeam_x",), "ebeam_y": ("ebeam_y",),
                "teg_w": ("teg_w",), "teg_h": ("teg_h",),
                "chip_name": ("chip_name",), "chipsize_x": ("chipsize_x",),
                "chipsize_y": ("chipsize_y",), "purpose": ("purpose",),
                "chip_x_adj": ("chip_x_adj", "chip_x"),
                "chip_y_adj": ("chip_y_adj", "chip_y"),
                "chip_radius": ("chip_radius", "radius"),
            }.get(key, (key,))
            target = exact(*aliases)
            if target is not None:
                row[target] = value
        rows.append(row)
    if replace_vehicle:
        vehicle_column = exact("vehicle", "mask")
        if vehicle_column is None:
            raise ValueError(f"{kind} 파일에 vehicle/Mask 열이 없습니다")
        vehicle_key = str(replace_vehicle).strip().casefold()
        frame = frame[
            frame[vehicle_column].fillna("").astype(str).str.strip().str.casefold() != vehicle_key
        ]
    combined = pd.concat([frame.reindex(columns=columns), pd.DataFrame(rows, columns=columns)],
                         ignore_index=True)
    serialised = [["" if pd.isna(value) else str(value) for value in row]
                  for row in combined.itertuples(index=False, name=None)]
    return save_reference_file(kind, columns, serialised, username, note)


def _save_product_info_row(vehicle: str, info: dict[str, Any], node_path: str,
                           username: str, note: str, replace_vehicle: str = "",
                           raw_text: str = "") -> dict:
    import pandas as pd
    path = product_info_path()
    current, _ = load_product_info()
    def number_text(value: Any) -> str:
        number = float(value)
        return str(int(number)) if number.is_integer() else format(number, ".15g")
    def optional_number_text(key: str) -> str:
        try:
            number = float(info.get(key))
        except (TypeError, ValueError):
            return ""
        return number_text(number) if math.isfinite(number) and number > 0 else ""
    raw_config_json = ""
    if raw_text:
        raw_config_json = json.dumps(
            _product_info_raw_rows(raw_text), ensure_ascii=False, separators=(",", ":"),
        )
    else:
        stored_raw = str(info.get("raw_config_json") or "").strip()
        if stored_raw.casefold() != "nan":
            raw_config_json = stored_raw
    row = {"vehicle": vehicle,
           **{key: number_text(info[key]) for key in PRODUCT_GEOMETRY_COLUMNS[1:]},
           "rc_cols": optional_number_text("rc_cols"),
           "rc_rows": optional_number_text("rc_rows"),
           "raw_config_json": raw_config_json,
           "node_path": clean_node_path(node_path)}
    addition = pd.DataFrame([row], columns=PRODUCT_INFO_COLUMNS)
    if not current.empty:
        vehicle_keys = {
            str(value).strip().casefold()
            for value in (vehicle, replace_vehicle)
            if str(value or "").strip()
        }
        current = current[
            ~current["vehicle"].fillna("").astype(str).str.strip().str.casefold().isin(vehicle_keys)
        ]
    frame = (addition if current.empty else
             pd.concat([current.reindex(columns=PRODUCT_INFO_COLUMNS), addition], ignore_index=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = None
    if path.is_file():
        history = teg_dir() / "reference_versions" / "product_info"
        history.mkdir(parents=True, exist_ok=True)
        backup = history / f"{stamp}_{path.name}"
        shutil.copy2(path, backup)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp.csv")
    try:
        frame.to_csv(temp, index=False, encoding="utf-8-sig", lineterminator="\n")
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
    version = _snapshot_edm_file(path, username, note, backup)
    return {"path": str(path), "rows": len(frame), "backup": str(backup) if backup else "",
            "version": version}


def _casefold_mapping_key(mapping: dict, vehicle: str) -> str | None:
    target = str(vehicle or "").strip().casefold()
    return next((str(key) for key in mapping if str(key).strip().casefold() == target), None)


def _restore_file_bytes(path: Path, content: bytes | None) -> None:
    """제품 식별자 다중 파일 갱신 실패 시 호출하는 원본 복구 경로."""
    if content is None:
        try:
            path.unlink(missing_ok=True)
        except TypeError:  # Python 3.7 호환 배포 환경
            if path.exists():
                path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.rollback")
    try:
        with open(temp, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass


def update_product_identity(current_vehicle: str, vehicle: str, node_path: str,
                            username: str) -> dict:
    """제품명/분류를 모든 TEG 기준 파일과 제품별 설정에 함께 반영한다.

    vehicle은 여러 CSV와 JSON의 조인 키다. 한 파일만 바꾸면 위치 조회나 Mapfile
    체크가 조용히 끊기므로, 먼저 전체 변경본과 충돌을 검증한 뒤 저장한다. 저장 중
    하나라도 실패하면 이 호출이 건드린 파일은 호출 전 bytes로 복구한다.
    """
    requested = str(current_vehicle or "").strip()
    new_vehicle = str(vehicle or "").strip()[:200]
    clean_path = clean_node_path(node_path)
    if not requested:
        raise ValueError("변경할 제품명이 비어 있습니다")
    if not new_vehicle:
        raise ValueError("제품명을 입력해 주세요")
    if not clean_path:
        raise ValueError("제품 분류를 입력해 주세요 (예: 2나노 / 2나노A)")

    catalog = product_catalog()
    current = next((row for row in catalog
                    if row["vehicle"].casefold() == requested.casefold()), None)
    if current is None:
        raise ValueError(f"등록된 제품이 아닙니다: {requested}")
    old_vehicle = str(current["vehicle"]).strip()
    rename = old_vehicle != new_vehicle
    if rename and any(row is not current and row["vehicle"].casefold() == new_vehicle.casefold()
                      for row in catalog):
        raise ValueError(f"이미 등록된 제품명입니다: {new_vehicle}")

    # 모든 기준 파일을 먼저 읽어 변경 위치와 새 이름 충돌을 검증한다. 검증이 끝나기
    # 전에는 디스크를 건드리지 않는다.
    reference_updates: list[tuple[str, dict, list[list[str]], int]] = []
    for kind in REFERENCE_FILE_KEYS:
        path = reference_file_path(kind)
        if not path.is_file():
            continue
        payload = read_reference_file(kind)
        lookup = {str(column).strip().casefold(): index
                  for index, column in enumerate(payload["columns"])}
        vehicle_index = next((lookup[name] for name in ("vehicle", "mask") if name in lookup), None)
        if vehicle_index is None:
            raise ValueError(f"{path.name} 파일에 vehicle/Mask 열이 없습니다")
        rows = [list(row) for row in payload["rows"]]
        old_indexes = [index for index, row in enumerate(rows)
                       if str(row[vehicle_index]).strip().casefold() == old_vehicle.casefold()]
        if rename and any(
            str(row[vehicle_index]).strip().casefold() == new_vehicle.casefold()
            for index, row in enumerate(rows) if index not in old_indexes
        ):
            raise ValueError(f"{path.name}에 새 제품명 {new_vehicle} 행이 이미 있습니다")
        if rename and old_indexes:
            for index in old_indexes:
                rows[index][vehicle_index] = new_vehicle
            reference_updates.append((kind, payload, rows, len(old_indexes)))

    info_df, info_path = load_product_info()
    info_match = (info_df[
        info_df["vehicle"].astype(str).str.strip().str.casefold() == old_vehicle.casefold()
    ] if not info_df.empty else info_df)
    if rename and not info_df.empty:
        collision = info_df[
            (info_df["vehicle"].astype(str).str.strip().str.casefold() == new_vehicle.casefold())
            & (info_df["vehicle"].astype(str).str.strip().str.casefold() != old_vehicle.casefold())
        ]
        if not collision.empty:
            raise ValueError(f"{PRODUCT_INFO_FILE_NAME}에 새 제품명 {new_vehicle} 행이 이미 있습니다")

    cfg = load_cfg()
    inline = load_inline_map_settings()
    inline_matching = load_inline_shot_matching()
    changed_inline = rename and any(
        str(table.get("vehicle") or "").strip().casefold() == old_vehicle.casefold()
        for table in inline.get("tables", [])
    )
    changed_inline_matching = rename and any(
        str(row.get("product") or "").strip().casefold() == old_vehicle.casefold()
        for row in inline_matching.get("rows", [])
    )
    affected_paths = {reference_file_path(kind) for kind, *_ in reference_updates}
    if not info_match.empty:
        affected_paths.add(info_path)
    affected_paths.add(_cfg_path())
    if changed_inline:
        affected_paths.add(inline_map_settings_path())
    if changed_inline_matching:
        affected_paths.add(inline_shot_matching_path())
    originals = {path: path.read_bytes() if path.is_file() else None for path in affected_paths}

    note = (f"TEG 제품명·분류 변경: {old_vehicle} → {new_vehicle} / {clean_path}"
            if rename else f"TEG 제품 분류 변경: {old_vehicle} / {clean_path}")
    files: dict[str, Any] = {}
    try:
        with _LOCK:
            for kind, payload, rows, count in reference_updates:
                result = save_reference_file(
                    kind, payload["columns"], rows, username, note,
                    expected_modified_ns=payload["source_modified_ns"],
                )
                files[kind] = {**result, "updated_rows": count}

            if not info_match.empty:
                info = info_match.iloc[-1].to_dict()
                files["product_info"] = _save_product_info_row(
                    new_vehicle, info, clean_path, username, note,
                    replace_vehicle=old_vehicle,
                )

            vehicle_patch: dict[str, Any] = {}
            target_key = _casefold_mapping_key(cfg.get("vehicles") or {}, old_vehicle)
            if rename and target_key is not None:
                vehicle_patch[target_key] = None
                vehicle_patch[new_vehicle] = (cfg.get("vehicles") or {})[target_key]

            target_key = _casefold_mapping_key(cfg.get("check_targets") or {}, old_vehicle)
            target_patch: dict[str, Any] = {}
            if rename and target_key is not None:
                target_patch[target_key] = None
                target_patch[new_vehicle] = (cfg.get("check_targets") or {})[target_key]

            node_patch = {new_vehicle: clean_path}
            target_key = _casefold_mapping_key(cfg.get("product_nodes") or {}, old_vehicle)
            if rename and target_key is not None:
                node_patch[target_key] = ""

            check = cfg.get("check") or _clean_check({})
            products = dict(check.get("products") or {})
            target_key = _casefold_mapping_key(products, old_vehicle)
            if rename and target_key is not None:
                products[new_vehicle] = products.pop(target_key)
                check = {**check, "products": products}

            patch: dict[str, Any] = {"product_nodes": node_patch}
            if vehicle_patch:
                patch["vehicles"] = vehicle_patch
            if target_patch:
                patch["check_targets"] = target_patch
            if rename and target_key is not None:
                patch["check"] = check
            save_cfg(patch)

            if changed_inline:
                tables = []
                for table in inline.get("tables", []):
                    if str(table.get("vehicle") or "").strip().casefold() == old_vehicle.casefold():
                        table = {**table, "vehicle": new_vehicle,
                                 "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                                 "updated_by": str(username or "")[:200]}
                    tables.append(table)
                inline_path = inline_map_settings_path()
                inline_path.parent.mkdir(parents=True, exist_ok=True)
                save_json(inline_path, {"version": 1, "tables": tables}, indent=2)

            if changed_inline_matching:
                matching_rows = []
                for row in inline_matching.get("rows", []):
                    if str(row.get("product") or "").strip().casefold() == old_vehicle.casefold():
                        row = {**row, "product": new_vehicle}
                    matching_rows.append(row)
                _save_inline_shot_matching_rows(matching_rows)
    except Exception:
        for path, content in originals.items():
            try:
                _restore_file_bytes(path, content)
            except Exception as restore_error:
                logger.error("TEG 제품 식별자 롤백 실패 path=%s error=%s", path, restore_error)
        raise

    return {
        "ok": True, "changed": rename or current.get("node_path") != clean_path,
        "previous_vehicle": old_vehicle, "vehicle": new_vehicle,
        "node_path": clean_path, "files": files,
        "inline_tables_updated": changed_inline,
        "inline_matching_updated": changed_inline_matching,
    }


def create_product_from_table(text: str, vehicle: str, tegs: list[dict[str, Any]],
                              main_chip: dict[str, Any] | None, username: str,
                              node_path: str = "") -> dict:
    """제품 geometry를 등록하고 Teg_location/Main_chip_info를 아래로 append한다."""
    veh = str(vehicle or "").strip()[:200]
    if not veh:
        raise ValueError("vehicle을 입력해 주세요")
    clean_path = clean_node_path(node_path)
    if not clean_path:
        raise ValueError("제품의 상위 노드 경로를 입력해 주세요 (예: 2나노 / 2나노A)")
    info = parse_product_info_table(text)
    preview = product_info_preview(text, veh)
    existing = {str(name).casefold() for name in vehicles()}
    if veh.casefold() in existing:
        raise ValueError(f"이미 등록된 제품입니다: {veh}")
    clean_tegs: list[dict[str, Any]] = []
    if not isinstance(tegs, list) or not tegs:
        raise ValueError("Teg_location에 추가할 TEG를 1개 이상 입력해 주세요")
    for index, raw in enumerate(tegs, 1):
        raw = raw if isinstance(raw, dict) else {}
        name = str(raw.get("teg") or "").strip()[:200]
        if not name:
            raise ValueError(f"TEG {index}행의 teg를 입력해 주세요")
        direction = str(raw.get("direction") or "").strip()[:40]
        if not direction:
            raise ValueError(f"TEG {index}행의 direction을 입력해 주세요")
        numbers: dict[str, float] = {}
        for key in ("ebeam_x", "ebeam_y", "teg_w", "teg_h"):
            try:
                number = float(raw.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"TEG {index}행의 {key}가 숫자가 아닙니다")
            if not math.isfinite(number) or (key in ("teg_w", "teg_h") and number <= 0):
                raise ValueError(f"TEG {index}행의 {key}가 유효하지 않습니다")
            numbers[key] = number
        clean_tegs.append({
            "vehicle": veh, "teg": name,
            "top_cell": str(raw.get("top_cell") or "").strip()[:300],
            "direction": direction, **numbers,
        })

    clean_main = None
    if preview["one_by_one"]:
        raw = main_chip if isinstance(main_chip, dict) else {}
        chip_name = str(raw.get("chip_name") or "").strip()[:200]
        if not chip_name:
            raise ValueError("Shot 1×1 제품은 chip_name을 입력해 주세요")
        sizes = {}
        for key in ("chipsize_x", "chipsize_y"):
            try:
                number = float(raw.get(key))
            except (TypeError, ValueError):
                raise ValueError(f"{key}가 숫자가 아닙니다")
            if not math.isfinite(number) or number <= 0:
                raise ValueError(f"{key}는 0보다 커야 합니다")
            sizes[key] = number
        clean_main = {"vehicle": veh, "chip_name": chip_name, **sizes}

    note = f"TEG 제품 추가: {veh}"
    shots = _product_shots(info, float(preview["wafer_edge_mm"]))
    radius_rows = [{
        "vehicle": veh,
        "chip_x_adj": str(int(shot["x"])) if float(shot["x"]).is_integer() else str(shot["x"]),
        "chip_y_adj": str(int(shot["y"])) if float(shot["y"]).is_integer() else str(shot["y"]),
        "chip_radius": f"{float(shot['r']):.{PRODUCT_RADIUS_DECIMALS}f}",
    } for shot in shots]
    with _LOCK:
        # 참조행을 먼저 검증/append하고 제품 geometry는 마지막에 공개한다.
        teg_result = _append_frame_rows("teg_location", clean_tegs, username, note)
        main_result = (_append_frame_rows("main_chip_info", [clean_main], username, note)
                       if clean_main is not None else None)
        radius_result = _append_frame_rows("chip_radius", radius_rows, username, note)
        product_result = _save_product_info_row(
            veh, info, clean_path, username, note, raw_text=text,
        )
        display = preview["display"]
        save_cfg({"vehicles": {veh: {**DEFAULT_VEHICLE_CFG, **display}},
                  "product_nodes": {veh: clean_path}})
    return {
        "ok": True, "vehicle": veh, "values": info,
        "shot_count": preview["shot_count"], "one_by_one": preview["one_by_one"],
        "display": display, "node_path": clean_path,
        "files": {"product_info": product_result, "chip_radius": radius_result,
                  "teg_location": teg_result,
                  "main_chip_info": main_result},
    }


def update_product_from_table(text: str, vehicle: str, username: str) -> dict:
    """기존 제품의 Item/X/Y 명시 geometry와 Chip_Radius를 함께 갱신한다.

    Chip_Radius fit 기반 제품의 최초 전환과 이미 TEG_Product_Info에 등록된 제품의
    재변경을 모두 지원한다. 두 CSV 모두 기존 파일 교체·EDM snapshot 경로를 사용한다.
    """
    requested = str(vehicle or "").strip()
    if not requested:
        raise ValueError("vehicle을 입력해 주세요")
    layout, _ = load_layout()
    if layout is None or layout.empty:
        raise ValueError("등록된 제품 layout이 없습니다")
    matched = layout[
        layout["vehicle"].astype(str).str.strip().str.casefold() == requested.casefold()
    ]
    if matched.empty:
        raise ValueError(f"등록된 제품이 아닙니다: {requested}")
    veh = str(matched.iloc[0]["vehicle"]).strip()
    sources = {str(value or "chip_radius") for value in matched.get(
        "layout_source", ["chip_radius"]
    )}
    previous_source = next(iter(sources), "chip_radius")

    info = parse_product_info_table(text)
    preview = product_info_preview(text, veh)
    shots = _product_shots(info, float(preview["wafer_edge_mm"]))
    radius_rows = [{
        "vehicle": veh,
        "chip_x_adj": str(int(shot["x"])) if float(shot["x"]).is_integer() else str(shot["x"]),
        "chip_y_adj": str(int(shot["y"])) if float(shot["y"]).is_integer() else str(shot["y"]),
        "chip_radius": f"{float(shot['r']):.{PRODUCT_RADIUS_DECIMALS}f}",
    } for shot in shots]
    catalog_row = next((row for row in product_catalog()
                        if row["vehicle"].casefold() == veh.casefold()), None)
    node_path = "" if not catalog_row or catalog_row["node_path"] == "미분류" else catalog_row["node_path"]
    note = f"TEG 제품 config 변경: {veh}"

    with _LOCK:
        # Chip_Radius를 먼저 교체하고 exact geometry를 마지막에 공개한다. 어느 시점에도
        # 한 제품의 구/신 radius 행이 함께 남지 않는다.
        radius_result = _append_frame_rows(
            "chip_radius", radius_rows, username, note, replace_vehicle=veh,
        )
        product_result = _save_product_info_row(
            veh, info, node_path, username, note, raw_text=text,
        )
        cfg = load_cfg()
        current_display = {
            **DEFAULT_VEHICLE_CFG,
            **((cfg.get("vehicles") or {}).get(veh) or {}),
        }
        calculated_display = dict(preview["display"])
        # 그림/개발 격자를 쓰던 제품은 표시 모드를 보존하되, 새 Item 표에서 받은
        # 칩 개수·크기·간격은 갱신한다. 기본/grid 제품은 계산된 모드로 전환한다.
        mode = (current_display.get("mode") if current_display.get("mode") in ("image", "dev_grid")
                else calculated_display.get("mode", "none"))
        display = {**current_display, **calculated_display, "mode": mode}
        save_cfg({"vehicles": {veh: display}})

    return {
        "ok": True, "vehicle": veh, "values": info,
        "shot_count": len(shots), "one_by_one": preview["one_by_one"],
        "display": display, "previous_layout_source": previous_source,
        "layout_source": "product_info",
        "files": {"product_info": product_result, "chip_radius": radius_result},
    }


# 내부 호출 호환. 이제는 기존 Product Info 제품도 같은 경로에서 재변경할 수 있다.
update_legacy_product_from_table = update_product_from_table


def candidate_files() -> list[str]:
    """설정 드롭다운용 — DB root 최상위의 표 형식 파일 목록."""
    try:
        root = roots.get_db_root()
        out = []
        for p in sorted(root.iterdir()):
            if p.is_file() and p.suffix.lower() in (".csv", ".parquet", ".xlsx"):
                out.append(p.name)
        return out
    except Exception:
        return []
