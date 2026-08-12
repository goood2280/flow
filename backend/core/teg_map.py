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
}

# TEG Mapfile 체크(core/teg_check) 설정 — flat 별 기본 오프셋·모듈(TEG)별 오프셋.
# flat 키는 저장값 기준 "h"/"v_R" (UI 표기는 Horizontal / Vertical(R)).
# 모듈별 오프셋은 항상 Horizontal(TEG) 관점으로 입력, 양수 = 빼기.
CHECK_FLATS = ("h", "v_R", "v_L")
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
    # die 겹침 허용오차 — **ebeam raw 단위**(ΔX/ΔY 와 같은 공간, ebeam_scale 로 mm 환산).
    # die 경계에서 이 값 안쪽으로 들어가거나 바깥으로 나간 정도는 '경계 근처'(확인필요)
    # 로 본다. 0 이면 예전처럼 조금이라도 닿으면 침범.
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
        name = str(item.get("name") or "").strip()[:200]
        key = (round(x, 6), round(y, 6))
        if not name or not all(math.isfinite(v) for v in key) or key in seen:
            continue
        seen.add(key)
        shots.append({"shot_x": key[0], "shot_y": key[1], "name": name})
    return {
        "table_name": table_name,
        "vehicle": vehicle,
        "shots": shots,
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


def save_inline_map_table(table_name: str, vehicle: str, shots: list[dict], username: str) -> dict:
    """TABLE 이름을 키로 제품별 shot 위치/이름을 원자적으로 upsert 한다."""
    cleaned = _clean_inline_table({
        "table_name": table_name,
        "vehicle": vehicle,
        "shots": shots,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "updated_by": username,
    })
    if cleaned is None:
        raise ValueError("TABLE 이름과 제품(vehicle)이 필요합니다")
    if not cleaned["shots"]:
        raise ValueError("이름을 입력한 shot을 1개 이상 선택해 주세요")

    # 화면에 존재하지 않는 좌표를 저장하지 않는다. 이후 ET 좌표 매칭의 기준 데이터이므로
    # 수기 요청이나 오래 열린 브라우저가 잘못된 위치를 밀어 넣는 것을 서버에서 차단한다.
    payload = map_payload(cleaned["vehicle"])
    available = {
        (round(float(s["x"]), 6), round(float(s["y"]), 6))
        for s in payload.get("shots", [])
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

    mods = raw.get("modules")
    if isinstance(mods, list):
        out["modules"] = _modules(mods)
    out["first_pad_default"] = _pair(raw.get("first_pad_default"), out["first_pad_default"])
    out["pchk_first_pad_default"] = _pair(raw.get("pchk_first_pad_default"), out["pchk_first_pad_default"])
    out["first_pad_modules"] = _first_pad_rules(raw.get("first_pad_modules"))
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
        path = _cfg_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, cur)
        return cur


# ────────────────────────────────────────── vehicle 그림 파일
# ────────────────────────────────────────── MAIN overlay (Mapfile 역반영)
# MAIN 은 개발제품 die 급의 큰 직사각형 블록 — Mapfile 에는 그 내부 TEG 들이
# 같은 그룹명(MAIN02 등)으로 나열된다. TEG Mapfile 체크에서 원복한 내부 TEG
# 절대좌표를 위치 조회에 역반영해 두는 저장소. **설비 Mapfile 세팅에서 가져온
# 값이라 이상이 있을 수 있음** — applied_at 을 함께 저장해 UI 에 참고문으로 표시.
MAIN_RE = re.compile(r"(?<![A-Za-z])MAIN", re.IGNORECASE)   # 이름의 MAIN 판별
MAIN_OVERLAY_NAME = "main_overlays.json"
MAX_OVERLAY_GROUPS = 50          # vehicle 당 그룹 상한
MAX_OVERLAY_TEGS = 2000          # 그룹당 내부 TEG 상한


def _main_overlay_path() -> Path:
    return teg_dir() / MAIN_OVERLAY_NAME


def load_main_overlays() -> dict:
    """전체 overlay — {vehicle: {group: {applied_at, source, tegs:[{teg,x,y}]}}}."""
    with _LOCK:
        raw = load_json(_main_overlay_path(), default={})
    return raw if isinstance(raw, dict) else {}


def get_main_overlays(vehicle: str) -> dict:
    v = load_main_overlays().get(str(vehicle or "").strip())
    return v if isinstance(v, dict) else {}


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
    않는다(teg_shape.anchor_cells). overlay 로 들어온 **내부** TEG("MAIN02·XXX")는
    die 코너가 아니므로 뺀다.
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


def _clean_overlay_tegs(items: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for row in (items or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("teg") or "").strip()
        if not name or name in seen:
            continue
        try:
            x = float(row.get("x"))
            y = float(row.get("y"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        seen.add(name)
        out.append({"teg": name[:120], "x": x, "y": y})
        if len(out) >= MAX_OVERLAY_TEGS:
            break
    return out


def apply_main_overlays(vehicle: str, groups: list, overwrite: bool = False) -> dict:
    """MAIN 그룹들을 vehicle overlay 로 저장.

    overwrite=False 인데 같은 그룹이 이미 반영돼 있으면 저장하지 않고
    {ok: False, exists: [{group, applied_at}]} 를 돌려준다 — UI 가
    "다시 반영할지" 확인 후 overwrite=True 로 재요청하는 계약.
    """
    veh = str(vehicle or "").strip()
    if not veh:
        return {"ok": False, "error": "vehicle 이 비어 있습니다"}
    cleaned: list[tuple[str, list[dict]]] = []
    for g in (groups or []):
        if not isinstance(g, dict):
            continue
        name = str(g.get("group") or "").strip()
        tegs = _clean_overlay_tegs(g.get("tegs"))
        if name and tegs:
            cleaned.append((name[:120], tegs))
    if not cleaned:
        return {"ok": False, "error": "반영할 MAIN 그룹이 없습니다"}
    from datetime import datetime as _dt
    with _LOCK:
        data = load_main_overlays()
        cur = data.get(veh)
        cur = dict(cur) if isinstance(cur, dict) else {}
        exists = [{"group": n, "applied_at": (cur[n] or {}).get("applied_at", "")}
                  for n, _ in cleaned if n in cur]
        if exists and not overwrite:
            return {"ok": False, "exists": exists}
        now = _dt.now().astimezone().isoformat(timespec="seconds")
        for n, tegs in cleaned:
            cur[n] = {"applied_at": now, "source": "mapfile-check", "tegs": tegs}
        # 그룹 상한 — 오래된 applied_at 부터 정리
        if len(cur) > MAX_OVERLAY_GROUPS:
            for drop in sorted(cur, key=lambda k: str(cur[k].get("applied_at") or ""))[
                    :len(cur) - MAX_OVERLAY_GROUPS]:
                cur.pop(drop, None)
        data[veh] = cur
        teg_dir().mkdir(parents=True, exist_ok=True)
        save_json(_main_overlay_path(), data)
    return {"ok": True, "saved": [n for n, _ in cleaned], "applied_at": now}


def delete_main_overlay(vehicle: str, group: str) -> bool:
    veh = str(vehicle or "").strip()
    grp = str(group or "").strip()
    with _LOCK:
        data = load_main_overlays()
        cur = data.get(veh)
        if not isinstance(cur, dict) or grp not in cur:
            return False
        cur.pop(grp, None)
        if cur:
            data[veh] = cur
        else:
            data.pop(veh, None)
        save_json(_main_overlay_path(), data)
    return True


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


def load_layout():
    """chip layout 파일 → (DataFrame[vehicle,x,y,r], 경로). 없으면 (None, 경로)."""
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
            return out, path
    return None, configured


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
             direction: str) -> tuple[float, float]:
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
    w = float(raw_w) * scale if has_w else float(cfg["teg_default_w"])
    h = float(raw_h) * scale if has_h else float(cfg["teg_default_h"])
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


def map_payload(vehicle: str) -> dict:
    """vehicle 의 WF MAP 전체 payload — geometry + shot 목록 + TEG 목록 + 표시 설정."""
    cfg = load_cfg()
    lay, lay_path = load_layout()
    if lay is None:
        raise FileNotFoundError(f"chip layout 파일 없음/무효: {lay_path}")
    veh = str(vehicle).strip()
    sub = lay[lay["vehicle"] == veh]
    if sub.empty:
        raise LookupError(f"layout 에 vehicle 없음: {vehicle}")
    # shot 레이아웃만: (x,y) 중복 제거(대표 radius = 중앙값) — 측정 밀도 편향 제거
    grp = sub.groupby(["x", "y"], as_index=False)["r"].median()
    xs = grp["x"].tolist()
    ys = grp["y"].tolist()
    rs = grp["r"].tolist()

    geo, fit_diag = fit_geometry_diagnosed(xs, ys, rs)
    pitch_x = _grid_pitch(xs)
    pitch_y = _grid_pitch(ys)

    shots = []
    for x, y, r in zip(xs, ys, rs):
        s: dict[str, Any] = {"x": x, "y": y}
        if r == r:  # not NaN
            s["r"] = float(r)
        if geo:
            mmx = (x - geo["cx"]) * geo["kx"]
            mmy = (y - geo["cy"]) * geo["ky"]
            s["mm_x"] = round(mmx, 4)
            s["mm_y"] = round(mmy, 4)
            s["radius"] = round(math.hypot(mmx, mmy), 4)
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
            tw, th = teg_size(row["teg_w"], row["teg_h"], scale, cfg, fz)
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

    # ── MAIN overlay 병합 — Mapfile 체크에서 역반영된 MAIN 내부 TEG.
    #    이름은 "그룹·내부이름" 으로 접두해 Teg_location 실측 TEG 와 구분한다.
    #    좌표는 raw ebeam 저장값 × 배율 (Teg_location 과 동일 규약), 크기는 기본값.
    #    순서: 그룹이 Teg_location 에 있으면 **그 행 바로 뒤**, 없으면 목록 끝
    #    (파일 순서를 흐트러뜨리지 않으면서 그룹과 내부 TEG 를 붙여 둔다).
    overlays = get_main_overlays(veh)
    overlay_meta = {}
    ov_items: list[dict] = []
    if overlays:
        scale = float(cfg["ebeam_scale"])
        dw, dh = float(cfg["teg_default_w"]), float(cfg["teg_default_h"])
        taken = {t["teg"] for t in tegs}
        for g in sorted(overlays):
            meta = overlays[g] or {}
            rows_g = meta.get("tegs") or []
            # 그룹 자체를 가리키는 행 = 이름이 그룹과 같거나 "그룹_숫자"(자동 넘버링).
            # 그런 행이 하나뿐이면 그게 die 블록 자체이므로 접미사를 떼고 그룹 이름으로
            # 쓴다. 이 치유가 없으면 de2fd73a 이전에 적용된 overlay 가 "MAIN01_1" 로
            # 남아 이름이 "MAIN01·MAIN01_1" 이 되고, `·` 가 붙은 이름은 main_anchors
            # 에서 내부 TEG 로 걸러져 **die 앵커가 0 개 → 개발 격자가 통째로 안 그려진다.**
            auto_re = re.compile(rf"^{re.escape(g)}(_\d+)?$")
            block_rows = [r for r in rows_g
                          if auto_re.match(str((r or {}).get("teg") or "").strip())]
            block_row = block_rows[0] if len(block_rows) == 1 else None
            n_added = 0
            for row in rows_g:
                try:
                    ox, oy = float(row.get("x")), float(row.get("y"))
                except (TypeError, ValueError):
                    continue
                inner = str(row.get("teg") or "").strip()
                # 내부 TEG 가 하나뿐이라 이름이 그룹과 같으면 "MAIN02·MAIN02" 로 겹쳐 쓰지 않는다.
                if row is block_row:
                    inner = ""
                name = g if (not inner or inner == g) else f"{g}·{inner}"
                i = 2
                base = name
                while name in taken:
                    name = f"{base}_{i}"
                    i += 1
                taken.add(name)
                # overlay 는 크기 정보가 없어 기본 사이즈로 그린다 — 방향은 내부 TEG
                # 이름(V_)으로 판정해 vertical 이면 기본 사이즈도 세운다.
                fz = normalize_direction("", inner or g)
                ow, oh = (dh, dw) if fz in ("v", "v_L") else (dw, dh)
                ov_items.append({"teg": name, "teg_src": base,
                                 "ebeam_x": ox * scale, "ebeam_y": oy * scale,
                                 "teg_w": ow, "teg_h": oh, "flat_zone": fz,
                                 "overlay_group": g})
                n_added += 1
            overlay_meta[g] = {"applied_at": str(meta.get("applied_at") or ""),
                               "source": str(meta.get("source") or "mapfile-check"),
                               "count": n_added}

    if ov_items:
        # 그룹 이름과 같은 Teg_location 행(teg_src 기준) 뒤에 그 그룹 항목을 끼운다.
        by_group: dict[str, list[dict]] = {}
        for it in ov_items:
            by_group.setdefault(it["overlay_group"], []).append(it)
        merged: list[dict] = []
        for t in tegs:
            merged.append(t)
            for it in by_group.pop(str(t.get("teg_src") or t["teg"]), []):
                merged.append(it)
        for g in sorted(by_group):        # 파일에 없는 그룹은 목록 끝에
            merged.extend(by_group[g])
        tegs = merged

    vcfg = cfg["vehicles"].get(veh) or dict(DEFAULT_VEHICLE_CFG)
    has_image = bool(vcfg.get("image")) and (teg_dir() / Path(vcfg["image"]).name).is_file()
    profile = check_profile(cfg, veh)
    return {
        "ok": True,
        "vehicle": veh,
        "main_overlays": overlay_meta,
        "geometry": {
            "fit": "radius" if geo else "none",
            **({"cx": round(geo["cx"], 6), "cy": round(geo["cy"], 6),
                "kx": round(geo["kx"], 6), "ky": round(geo["ky"], 6),
                "shot_w_mm": round(pitch_x * geo["kx"], 4),
                "shot_h_mm": round(pitch_y * geo["ky"], 4)} if geo else {}),
            "pitch_x": pitch_x,
            "pitch_y": pitch_y,
            "wafer_radius_mm": float(cfg["wafer_radius_mm"]),
            "wafer_edge_mm": float(cfg["wafer_edge_mm"]),
            # Chip_Radius 오입력 진단 — 이상치로 제외된 행과 fit 잔차를 UI 에 노출.
            "fit_used": fit_diag.get("used", 0),
            "fit_dropped": fit_diag.get("dropped", []),
            "fit_max_residual_mm": fit_diag.get("max_residual_mm", 0.0),
            "fit_note": fit_diag.get("note", ""),
        },
        "shots": shots,
        "tegs": tegs,
        "display": {**vcfg, "has_image": has_image},
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
    rows = []
    for s in payload["shots"]:
        ax = s["mm_x"] + t["ebeam_x"]
        # chip_y_adj/WF-map y is down-positive; ebeam_y is up-positive.
        ay = -s["mm_y"] + t["ebeam_y"]
        rows.append({
            "shot_x": s["x"], "shot_y": s["y"],
            "abs_x": round(ax, 4), "abs_y": round(ay, 4),
            "radius": round(math.hypot(ax, ay), 4),
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
    logger.info("TEG reference saved kind=%s rows=%d actor=%s note=%s", key, len(frame), username, note)
    return {"ok": True, "kind": key, "path": str(path), "rows": len(frame), "cols": len(cols),
            "backup": str(backup) if backup else ""}


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
