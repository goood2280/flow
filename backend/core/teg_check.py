# -*- coding: utf-8 -*-
"""core/teg_check.py — TEG 설비값 검사 (설비 원문 ↔ Teg_location 정답지 대조).

설비에서 복사한 레시피 원문을 파싱해 두 가지를 검사한다.
  1) <SITES> 의 Pattern site 좌표가 #wafer-map 측정 샷(t) 위에 있는지 (판정은 프론트)
  2) #teg-map 의 module 좌표가 flat 변환 후 TEG 위치 조회의 정답지
     (Teg_location 파일의 raw ebeam_x/ebeam_y — ebeam_scale 적용 전 값)와 일치하는지

flat 변환 (UI 표기: h=Horizontal, v_R=Vertical(R)):
  · h    — 그대로
  · v_R  — 설비에 반시계 90° 회전으로 세팅된 값을 원복: (x, y) → (y, -x + v_r_offset)
이후 flat 별 기본 오프셋, 모듈(TEG)별 오프셋을 더한다.
오프셋들은 teg_map.json 의 `check` 섹션에 저장 — ⚙️ 설정에서 편집.

원문 형식 (Streamlit PoC 'Wafer Map 검사기 v17' 파서 포팅):
  · 각 줄 앞의 "행번호 " 프리픽스는 제거 (행번호 없는 원문도 허용)
  · #wafer-map <이름> 섹션 안의 ! ~ ! 블록이 맵 (t=측정 샷, -=빈칸)
  · <SITES> 섹션 안의 "Pattern <이름>" + "pt x y" 행들
  · #teg-map 섹션 안의 "module <이름> (x, y) ! 꼬리표" 행들
    — module 이름은 꼬리표를 ','로 나눈 첫 토큰 (없으면 module 뒤 이름)
    — 꼬리표의 H_PCHK / V_PCHK 로 flat 자동 판정
"""
from __future__ import annotations

import re
from typing import Any

from core import teg_map as _tm

# ────────────────────────────────────────── 변환 규칙
# v_R 회전 offset·flat 별 기본 오프셋·모듈(TEG)별 오프셋은 teg_map.json 의
# `check` 섹션(teg_map.DEFAULT_CHECK_CFG)에 저장하고 ⚙️ 설정에서 편집한다.
FLAT_MARKERS = {"H_PCHK": "h", "V_PCHK": "v_R"}   # '!' 뒤 꼬리표 → flat
FLATS = ("h", "v_R")                              # 저장 키 (UI: Horizontal / Vertical(R))
TOL = 1e-6                                        # 좌표 비교 허용오차

# ────────────────────────────────────────── 파서
NUM = r"-?\d+(?:\.\d+)?"
NUMBERED_RE = re.compile(r"^\s*(\d+)\s+(.*)$")                 # "행번호 공백 내용"
PATTERN_RE = re.compile(r"\bPattern\b\s*[,:=]?\s*(.*)$", re.IGNORECASE)
MODULE_RE = re.compile(
    rf"\bmodule\b\s*[,:=]?\s*(.+?)\s*\(\s*({NUM})\s*,\s*({NUM})\s*\)", re.IGNORECASE
)
MODULE_FALLBACK_RE = re.compile(rf"^\s*(.+?)\s*\(\s*({NUM})\s*,\s*({NUM})\s*\)")


def _num(s: Any) -> float | int:
    f = float(s)
    return int(f) if f.is_integer() else f


def strip_line_numbers(text: str) -> list[str]:
    """"행번호 내용" 프리픽스 제거.

    설비 복사본은 모든 줄에 행번호가 붙는다. 행번호 없는 원문에서는 site 행
    ("1 5 4")만 행번호 형식으로 오인될 수 있으므로, 비어 있지 않은 줄의
    80% 이상이 행번호 형식일 때만 제거하고 아니면 원문 그대로 쓴다.
    """
    lines = str(text or "").splitlines()
    nonempty = [ln for ln in lines if ln.strip()]
    n_match = sum(1 for ln in nonempty if NUMBERED_RE.match(ln))
    if nonempty and n_match >= max(1, int(len(nonempty) * 0.8)):
        return [NUMBERED_RE.match(ln).group(2).rstrip()
                for ln in lines if NUMBERED_RE.match(ln)]
    return [ln.rstrip() for ln in lines]


def _section(lines: list[str], tag: str, stop: tuple[str, ...] = ("#",)) -> list[str]:
    body, inside = [], False
    for line in lines:
        s = line.strip()
        if s.lower().startswith(tag.lower()):
            inside = True
            rest = s[len(tag):].strip()
            if rest:
                body.append(rest)
            continue
        if inside and s and any(s.startswith(p) for p in stop):
            inside = False
            continue
        if inside:
            body.append(s)
    return body


def parse_wafer_maps(lines: list[str]) -> list[dict]:
    """#wafer-map 섹션들 → [{name, rows(문자열 행), w, h}, ...]  (! ~ ! 블록)"""
    maps: list[dict] = []
    inside, name, buf, open_ = False, None, None, False

    def emit():
        if not buf:
            return
        rows = [r for r in buf if r]
        if rows:
            w = max(len(r) for r in rows)
            maps.append({"name": name or f"MAP {len(maps) + 1}",
                         "rows": [r.ljust(w, "-") for r in rows],
                         "w": w, "h": len(rows)})

    for line in lines:
        s = line.strip()
        if s.lower().startswith("#wafer-map"):
            if open_:
                emit()
            inside, open_, buf = True, False, None
            rest = s[len("#wafer-map"):].strip(" :,-")
            name = rest or f"MAP {len(maps) + 1}"
            continue
        if not inside:
            continue
        if s.startswith("#"):
            if open_:
                emit()
            inside, open_ = False, False
            continue
        if s == "!":
            if open_:
                emit()
                open_, buf = False, None
            else:
                open_, buf = True, []
            continue
        if open_:
            buf.append(s)

    if open_:
        emit()
    return maps


def parse_sites(lines: list[str]) -> list[dict]:
    """<SITES> 안의 Pattern 블록들 → [{name, points:[{pt,x,y}]}, ...] (원문 순서)"""
    body = _section(lines, "<SITES>")
    sites: list[dict] = []
    name, pts = None, []

    def flush():
        if name is None or not pts:
            return
        key, n = name, 2
        taken = {s["name"] for s in sites}
        while key in taken:
            key, n = f"{name} ({n})", n + 1
        sites.append({"name": key, "points": list(pts)})

    for s in body:
        if not s:
            continue
        m = PATTERN_RE.search(s)
        if m:
            flush()
            name = m.group(1).strip(" ,:=\t") or f"Pattern {len(sites) + 1}"
            pts = []
            continue
        nums = re.findall(r"-?\d+", s)
        if name is not None and len(nums) >= 3:
            pts.append({"pt": int(nums[0]), "x": int(nums[1]), "y": int(nums[2])})

    flush()
    return sites


def parse_teg(lines: list[str]) -> list[dict]:
    """#teg-map → [{name, x, y, tail}, ...]

    name = '!' 뒤 꼬리표를 ','로 나눈 첫 토큰. 꼬리표가 없으면 'module' 뒤 이름.
    """
    body = _section(lines, "#teg-map")
    out = []
    for s in body:
        if not s:
            continue
        m = MODULE_RE.search(s) or MODULE_FALLBACK_RE.match(s)
        if not m:
            continue
        tail = s.split("!", 1)[1].strip() if "!" in s else ""
        name = tail.split(",")[0].strip() if tail else m.group(1).strip(" ,:=\t")
        out.append({"name": name, "x": _num(m.group(2)), "y": _num(m.group(3)), "tail": tail})
    return out


def detect_flat(tegs: list[dict]) -> tuple[str | None, str | None]:
    """꼬리표의 H_PCHK / V_PCHK 마커로 flat 자동 판정."""
    for t in tegs:
        for marker, flat in FLAT_MARKERS.items():
            if re.search(rf"\b{re.escape(marker)}\b", t["tail"], re.IGNORECASE):
                return flat, f"{marker}  (module {t['name']})"
    return None, None


def transform(name: str, x: float, y: float, flat: str, dx: float, dy: float,
              v_r_offset: float = 0.0,
              rules: dict[tuple[str, str], tuple[float, float, str]] | None = None,
              ) -> tuple[float, float]:
    """flat 변환(v_R = 반시계 90° 회전 원복) → flat 기본 오프셋 → 모듈별 보정.

    rules: {(flat, module_name): (dx, dy, note)} — ⚙️ 설정의 모듈별 오프셋.
    """
    if flat == "v_R":
        x, y = y, -x + v_r_offset
    x, y = x + dx, y + dy
    rule = (rules or {}).get((flat, name))
    if rule:
        return x + rule[0], y + rule[1]
    return x, y


# ────────────────────────────────────────── 정답지 (Teg_location raw 값)
def load_ref(vehicle: str) -> tuple[dict[str, list[dict]] | None, str, str]:
    """Teg_location 의 raw ebeam 값 → ({teg: [{x, y, w, h}, ...]}, 경로, 오류문).

    설비 원문의 좌표는 배율 적용 전 값이므로 raw ebeam_x/ebeam_y 와 직접 비교한다.
    w/h 는 TEG 실물 크기(mm) — 배율·기본값·flat_zone(v=w/h 스왑) 적용 (map_payload 와 동일).
    동명 TEG 가 여러 행이면 모두 후보로 두고 가장 가까운 행과 대조한다.
    """
    tdf, path = _tm.load_tegs()
    if tdf is None:
        return None, str(path), f"Teg_location 파일 없음/무효: {path}"
    sub = tdf[tdf["vehicle"] == str(vehicle).strip()]
    if sub.empty:
        return None, str(path), f"Teg_location 에 vehicle '{vehicle}' 이(가) 없습니다"
    cfg = _tm.load_cfg()
    scale = float(cfg["ebeam_scale"])
    ref: dict[str, list[dict]] = {}
    for _, row in sub.iterrows():
        tw = float(row["teg_w"]) * scale if row["teg_w"] == row["teg_w"] else float(cfg["teg_default_w"])
        th = float(row["teg_h"]) * scale if row["teg_h"] == row["teg_h"] else float(cfg["teg_default_h"])
        fz = str(row.get("flat_zone") or "h").strip().lower()
        if fz == "v":
            tw, th = th, tw
        ref.setdefault(str(row["teg"]), []).append(
            {"x": float(row["ebeam_x"]), "y": float(row["ebeam_y"]), "w": tw, "h": th})
    return ref, str(path), ""


def _compare(ref: dict[str, list[dict]] | None, name: str, x: float, y: float) -> dict:
    """계산 좌표 ↔ 정답지 대조 → {status, ref_x, ref_y, dx, dy, ref_w, ref_h}.

    status: match | mismatch | missing(정답지에 이름 없음) | noref(정답지 자체 없음)
    동명 후보가 여러 개면 가장 가까운 행 기준. ref_w/ref_h 는 TEG 크기(mm).
    """
    if ref is None:
        return {"status": "noref", "ref_x": None, "ref_y": None, "dx": None, "dy": None,
                "ref_w": None, "ref_h": None}
    cands = ref.get(name)
    if not cands:
        return {"status": "missing", "ref_x": None, "ref_y": None, "dx": None, "dy": None,
                "ref_w": None, "ref_h": None}
    c = min(cands, key=lambda c0: abs(c0["x"] - x) + abs(c0["y"] - y))
    same = abs(c["x"] - x) < TOL and abs(c["y"] - y) < TOL
    return {"status": "match" if same else "mismatch",
            "ref_x": _num(c["x"]), "ref_y": _num(c["y"]),
            "dx": _num(x - c["x"]), "dy": _num(y - c["y"]),
            "ref_w": c["w"], "ref_h": c["h"]}


# ────────────────────────────────────────── shot 칩 격자 겹침 검사
def _chip_cells(display: dict, W: float, H: float) -> list[dict]:
    """shot 센터 기준 칩 셀 배치(mm) — 프론트 chipCells 와 동일 (좌표 = 칩 좌하단)."""
    cols = max(1, int(display.get("cols") or 1))
    rows = max(1, int(display.get("rows") or 1))
    gx = max(0.0, float(display.get("gap_x") or 0))
    gy = max(0.0, float(display.get("gap_y") or 0))
    cw = float(display.get("chip_w") or 0)
    ch = float(display.get("chip_h") or 0)
    if cw <= 0:
        cw = max((W - (cols - 1) * gx) / cols, 0.001)
    if ch <= 0:
        ch = max((H - (rows - 1) * gy) / rows, 0.001)
    bw = cols * cw + (cols - 1) * gx
    bh = rows * ch + (rows - 1) * gy
    x0, y0 = -bw / 2, -bh / 2
    return [{"x": x0 + c * (cw + gx), "y": y0 + r * (ch + gy), "w": cw, "h": ch}
            for r in range(rows) for c in range(cols)]


def _overlaps_chip(cells: list[dict], x0: float, y0: float, w: float, h: float,
                   eps: float = 1e-9) -> bool:
    """TEG 사각형이 칩 셀 위에 겹치는가 — TEG 는 칩 사이(스크라이브)에 있어야 정상.

    TEG 앵커 = 좌하단, shot 확대 뷰 표시 기준으로 y 는 앵커에서 위(-h)로 뻗는다:
    TEG 범위 x [x0, x0+w], y [y0-h, y0]. 경계가 정확히 맞닿는 것은 겹침으로 안 봄.
    """
    tx1, ty0 = x0 + w, y0 - h
    for c in cells:
        if (x0 < c["x"] + c["w"] - eps and tx1 > c["x"] + eps
                and ty0 < c["y"] + c["h"] - eps and y0 > c["y"] + eps):
            return True
    return False


def _shot_info(vehicle: str) -> dict:
    """shot 크기·칩 격자 정보 — 확대 뷰 렌더·칩 겹침 검사용.

    checked=True 는 '칩 격자 모드 + shot 크기 fit 성공'일 때만.
    """
    out = {"available": False, "checked": False}
    try:
        p = _tm.map_payload(vehicle)
    except Exception:
        return out
    geo = p.get("geometry") or {}
    if geo.get("fit") != "radius":
        return out
    W, H = float(geo["shot_w_mm"]), float(geo["shot_h_mm"])
    display = p.get("display") or {}
    out.update({"available": True, "shot_w_mm": W, "shot_h_mm": H,
                "mode": display.get("mode", "none"), "cells": []})
    if display.get("mode") == "grid":
        out["cells"] = _chip_cells(display, W, H)
        out["checked"] = True
    return out


# ────────────────────────────────────────── 검사 payload
def inspect(vehicle: str, text: str, flat: str | None = None) -> dict:
    """원문 전체 검사 payload — 맵/패턴 파싱 + TEG flat 변환·정답지 대조."""
    veh = str(vehicle or "").strip()
    lines = strip_line_numbers(str(text or ""))
    maps = parse_wafer_maps(lines)
    patterns = parse_sites(lines)
    tegs = parse_teg(lines)

    detected, why = detect_flat(tegs)
    used = flat if flat in FLATS else (detected or "h")

    # ⚙️ 설정의 TEG Mapfile 체크 섹션 — v_R 회전 offset, flat 기본 오프셋, 모듈별 오프셋
    cfg = _tm.load_cfg()
    chk = cfg["check"]
    v_r_offset = float(chk["v_r_offset"])
    dx, dy = (float(v) for v in chk["flat_offsets"].get(used, [0.0, 0.0]))
    rules = {(m["flat"], m["name"]): (m["dx"], m["dy"], m.get("note", ""))
             for m in chk["modules"]}

    ref, ref_path, ref_err = load_ref(veh) if veh else (None, "", "제품명(vehicle)이 비어 있습니다")

    # shot 크기·칩 격자 — 칩 겹침 검사(TEG 는 칩 사이 스크라이브에 있어야 정상)
    scale = float(cfg["ebeam_scale"])
    shot = _shot_info(veh) if veh else {"available": False, "checked": False}

    rows = []
    summary = {"match": 0, "mismatch": 0, "missing": 0, "total": len(tegs), "chip_overlap": 0}
    for t in tegs:
        nx, ny = transform(t["name"], t["x"], t["y"], used, dx, dy, v_r_offset, rules)
        cmp_ = _compare(ref, t["name"], nx, ny)
        if cmp_["status"] in summary:
            summary[cmp_["status"]] += 1
        rule = rules.get((used, t["name"]))
        # 설비 계산값의 실좌표(mm) + TEG 크기(mm) — 정답지에 없으면 기본 크기
        mm_x, mm_y = nx * scale, ny * scale
        tw = cmp_["ref_w"] if cmp_["ref_w"] is not None else float(cfg["teg_default_w"])
        th = cmp_["ref_h"] if cmp_["ref_h"] is not None else float(cfg["teg_default_h"])
        overlap = (_overlaps_chip(shot["cells"], mm_x, mm_y, tw, th)
                   if shot.get("checked") else None)
        if overlap:
            summary["chip_overlap"] += 1
        rows.append({
            **t, "calc_x": _num(nx), "calc_y": _num(ny), **cmp_,
            "mm_x": round(mm_x, 4), "mm_y": round(mm_y, 4),
            "teg_w": round(tw, 4), "teg_h": round(th, 4),
            "chip_overlap": overlap,
            "rule_note": (rule[2] or f"모듈 오프셋 ({_num(rule[0])}, {_num(rule[1])})") if rule else "",
        })

    return {
        "ok": True,
        "vehicle": veh,
        "maps": maps,
        "patterns": patterns,
        "flat": {"detected": detected, "why": why, "used": used},
        "offset": {"dx": _num(dx), "dy": _num(dy)},
        "v_r_offset": _num(v_r_offset),
        "v_r_note": f"PCHK V x offset {_num(v_r_offset)}",
        "shot": shot,
        "teg": {
            "rows": rows,
            "summary": summary,
            "ref_ok": ref is not None,
            "ref_error": ref_err,
            "ref_path": ref_path,
            "ref_count": sum(len(v) for v in ref.values()) if ref else 0,
        },
    }
