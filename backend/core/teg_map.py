# -*- coding: utf-8 -*-
"""core/teg_map.py — TEG 위치 조회 (WF MAP geometry + TEG 좌표 계산).

Auto Report 의 WF MAP geometry(My_Function._wafer_circle_params)와 같은 수학을 쓴다:
  Chip_Radius r 은 'shot 센터 ↔ wafer 원점 거리(mm)' 이므로, shot 격자좌표
  (chip_x_adj, chip_y_adj)와의 관계
      r² = kx²·(x-cx)² + ky²·(y-cy)²   (kx=shot_width[mm/격자], ky=shot_height, (cx,cy)=wafer 중심)
  를 선형화  r² = A·x² + B·y² + p·x + q·y + C  로 최소자승 fit 하면
      cx=-p/2A,  cy=-q/2B,  kx=√A,  ky=√B.
  이후 shot 센터의 실좌표(mm) = ((x-cx)·kx, (y-cy)·ky), TEG 좌하단 실좌표 =
  shot 센터 + (ebeam_x, ebeam_y)·scale, radius = 원점과의 유클리드 거리.

입력 파일 (파일탐색기 Files 위치 = DB root, 상대경로는 db_root 기준):
  · chip layout: Mask(vehicle), chip_x_adj, chip_y_adj, Chip_Radius 열 (열 이름 대소문자 무관)
  · Teg_location.csv: vehicle, teg, ebeam_x, ebeam_y (shot 센터 기준 TEG 좌하단, TEG 는 직사각형)
    선택 열: teg_w, teg_h — 없으면 설정의 TEG 기본 사이즈 사용

설정/그림 저장소: DB root 의 `teg_location/` 폴더 (파일탐색기 위치 안).
  · teg_location/teg_map.json — 파일 경로·ebeam 배율·wafer 반경/최외곽·TEG 기본 사이즈,
    vehicle 별 shot 표시 방식(mode: none|image|grid):
      grid  = shot 안 칩 배열 — cols×rows, 칩 크기(chip_w/h mm, 0=칸 자동), 칩 사이 간격(gap_x/y mm),
              칩 블록은 shot 센터 기준 좌우/상하 대칭 배치
      image = shot 안에 그림 표시 (teg_location/ 폴더에 업로드된 vehicle 별 이미지)
  · teg_location/<vehicle>.<ext> — vehicle 별 그림 파일
"""
from __future__ import annotations

import logging
import math
import re
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

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_IMAGE_BYTES = 8 * 1024 * 1024

VEHICLE_MODES = ("none", "image", "grid")

DEFAULT_VEHICLE_CFG = {
    "mode": "none",     # none=기본(TEG 만) | image=그림 | grid=칩 격자
    "cols": 1,          # shot 안 칩 가로 개수
    "rows": 1,          # shot 안 칩 세로 개수
    "chip_w": 0.0,      # 칩 크기 mm (0 = shot 을 cols/rows 로 균등 분할)
    "chip_h": 0.0,
    "gap_x": 0.0,       # 칩 사이 간격 mm (좌우)
    "gap_y": 0.0,       # 칩 사이 간격 mm (위아래)
    "image": "",        # teg_location/ 폴더 안 그림 파일명
}

DEFAULT_CFG = {
    # 파일탐색기 Files(DB root) 기준 상대경로 또는 절대경로
    "layout_file": "Chip_Radius.csv",
    "teg_file": "Teg_location.csv",
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
    # 설정 스키마 버전 — v2: teg 기본 3000×100µm, ebeam 기본 µm 배율(0.001)
    "cfg_version": 2,
}

# 구 기본값 — cfg_version<2 파일이 이 값 그대로면 새 기본값으로 1회 이관.
_OLD_DEFAULTS = {"ebeam_scale": 1.0, "teg_default_w": 2.0, "teg_default_h": 2.0}

_LOCK = threading.RLock()


# ────────────────────────────────────────── 저장소 위치
def teg_dir() -> Path:
    """teg_location 폴더 (DB root 안) — 설정 json + vehicle 그림 파일."""
    return roots.get_db_root() / DIR_NAME


def _cfg_path() -> Path:
    return teg_dir() / CFG_NAME


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


def _clean_vehicles(raw: Any) -> dict:
    out: dict[str, dict] = {}
    if not isinstance(raw, dict):
        return out
    for veh, v in raw.items():
        cleaned = _clean_vehicle_cfg(v)
        if cleaned is not None:
            out[str(veh).strip()] = cleaned
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
    for k in ("layout_file", "teg_file"):
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
        for k in ("layout_file", "teg_file"):
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
        path = _cfg_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        save_json(path, cur)
        return cur


# ────────────────────────────────────────── vehicle 그림 파일
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


def save_image(vehicle: str, data: bytes, ext: str) -> str:
    """vehicle 그림 저장 (기존 그림 교체) → 저장된 파일명 반환."""
    ext = str(ext or "").lower()
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
        name = _safe_vehicle_stem(veh) + ext
        (d / name).write_bytes(data)
        # 확장자가 바뀌면 이전 파일 정리
        cur = load_cfg()["vehicles"].get(veh, {})
        old = (cur or {}).get("image") or ""
        if old and old != name:
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
    p = Path(str(name or "").strip())
    if p.is_absolute():
        return p
    return roots.get_db_root() / p


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
    path = resolve_path(cfg["layout_file"])
    if not path.is_file():
        return None, path
    try:
        df = _read_table(path)
    except Exception as e:
        logger.warning(f"layout 파일 읽기 실패 {path}: {e}")
        return None, path
    mc = _find_col(df, "mask", "vehicle")
    xc = _find_col(df, "chip_x_adj", "chip_x")
    yc = _find_col(df, "chip_y_adj", "chip_y")
    rc = _find_col(df, "chip_radius", "radius")
    if not (mc and xc and yc):
        logger.warning(f"layout 필수 열 누락 (Mask/chip_x_adj/chip_y_adj): {list(df.columns)}")
        return None, path
    out = pd.DataFrame({
        "vehicle": df[mc].astype(str).str.strip(),
        "x": pd.to_numeric(df[xc], errors="coerce"),
        "y": pd.to_numeric(df[yc], errors="coerce"),
    })
    out["r"] = pd.to_numeric(df[rc], errors="coerce") if rc else float("nan")
    out = out.dropna(subset=["x", "y"])
    return out, path


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
    wc = _find_col(df, "teg_w", "width")
    hc = _find_col(df, "teg_h", "height")
    out["teg_w"] = pd.to_numeric(df[wc], errors="coerce") if wc else float("nan")
    out["teg_h"] = pd.to_numeric(df[hc], errors="coerce") if hc else float("nan")
    # flat_zone: h(기본, TEG 수평) | v(TEG 세움 — 가로가 높이가 됨, w/h 스왑)
    fz = _find_col(df, "flat_zone", "flatzone")
    out["flat_zone"] = (
        df[fz].astype(str).str.strip().str.lower() if fz else "h"
    )
    out = out.dropna(subset=["ebeam_x", "ebeam_y"])
    return out, path


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

    geo = fit_geometry(xs, ys, rs)
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
        for _, row in tsub.iterrows():
            # teg_w/teg_h 없으면 설정의 TEG 기본 사이즈로 채움
            tw = float(row["teg_w"]) * scale if row["teg_w"] == row["teg_w"] else float(cfg["teg_default_w"])
            th = float(row["teg_h"]) * scale if row["teg_h"] == row["teg_h"] else float(cfg["teg_default_h"])
            # flat_zone=v → TEG 를 세워서 배치: 가로가 높이가 된다 (w/h 스왑).
            fz = str(row.get("flat_zone") or "h").strip().lower()
            fz = "v" if fz == "v" else "h"
            if fz == "v":
                tw, th = th, tw
            tegs.append({
                "teg": row["teg"],
                "ebeam_x": float(row["ebeam_x"]) * scale,
                "ebeam_y": float(row["ebeam_y"]) * scale,
                "teg_w": tw,
                "teg_h": th,
                "flat_zone": fz,
            })
        # ── 동명 TEG 자동 넘버링: 같은 이름이 2 개 이상이면 _1, _2, … 접미사 ──
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
    return {
        "ok": True,
        "vehicle": veh,
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
        },
        "shots": shots,
        "tegs": tegs,
        "display": {**vcfg, "has_image": has_image},
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
        ay = s["mm_y"] + t["ebeam_y"]
        rows.append({
            "shot_x": s["x"], "shot_y": s["y"],
            "abs_x": round(ax, 4), "abs_y": round(ay, 4),
            "radius": round(math.hypot(ax, ay), 4),
        })
    rows.sort(key=lambda r: r["radius"])
    return {"ok": True, "vehicle": payload["vehicle"], "teg": t["teg"],
            "ebeam_x": t["ebeam_x"], "ebeam_y": t["ebeam_y"], "rows": rows}


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
