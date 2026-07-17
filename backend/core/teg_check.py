# -*- coding: utf-8 -*-
"""core/teg_check.py — TEG 설비값 검사 (설비 원문 ↔ Teg_location 정답지 대조).

설비에서 복사한 레시피 원문을 파싱해 두 가지를 검사한다.
  1) <SITES> 의 Pattern site 좌표가 #wafer-map 측정 샷(t) 위에 있는지 (판정은 프론트)
  2) #teg-map 의 module 좌표가 flat 변환 후 TEG 위치 조회의 정답지
     (Teg_location 파일의 raw ebeam_x/ebeam_y — ebeam_scale 적용 전 값)와 일치하는지

flat 변환:
  · h    — 그대로
  · v_R  — 설비에 반시계 90° 회전으로 세팅된 값을 원복: (x, y) → (y, -x + V_R_OFFSET)
이후 제품(vehicle)별 PCHK 오프셋, 모듈별 보정(MODULE_RULES)을 더한다.

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

# ────────────────────────────────────────── 변환 규칙 (PoC v17 과 동일)
V_R_OFFSET = 10          # v_R 변환: (x, y) → (y, -x + OFFSET)
V_R_NOTE = f"PCHK V x offset {V_R_OFFSET}"

# 제품별 PCHK 오프셋: {vehicle: {flat: (dx, dy)}} — 미등록 제품은 기본값 (0, 0)
PCHK_OFFSETS: dict[str, dict[str, tuple[float, float]]] = {
    # "ABC1234": {"h": (0, 0), "v_R": (0, 0)},
}
DEFAULT_PCHK_OFFSET: dict[str, tuple[float, float]] = {"h": (0, 0), "v_R": (0, 0)}

# 모듈별 추가 보정: (flat, module_name) → (dx, dy, 참고문)
MODULE_RULES: dict[tuple[str, str], tuple[float, float, str]] = {
    ("h", "AAA"): (-400, 0, "AAA offset x 400"),
    ("v_R", "BBB"): (0, -400, "BBB offset x 400"),
}

FLAT_MARKERS = {"H_PCHK": "h", "V_PCHK": "v_R"}   # '!' 뒤 꼬리표 → flat
FLATS = ("h", "v_R")
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
    """"행번호 내용" 프리픽스 제거. 행번호 형식이 하나도 없으면 원문 그대로 사용."""
    out = []
    for line in text.splitlines():
        m = NUMBERED_RE.match(line)
        if m:
            out.append(m.group(2).rstrip())
    if not out:
        return [line.rstrip() for line in text.splitlines()]
    return out


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


def get_offset(vehicle: str, flat: str) -> tuple[tuple[float, float], bool]:
    """제품명 → flat 별 PCHK 오프셋. 미등록 제품이면 (기본값, False)."""
    table = PCHK_OFFSETS.get(vehicle)
    if table is None:
        return DEFAULT_PCHK_OFFSET.get(flat, (0, 0)), False
    return table.get(flat, (0, 0)), True


def transform(name: str, x: float, y: float, flat: str,
              dx: float, dy: float) -> tuple[float, float]:
    """flat 변환(v_R = 반시계 90° 회전 원복) → PCHK 오프셋 → 모듈별 보정."""
    if flat == "v_R":
        x, y = y, -x + V_R_OFFSET
    x, y = x + dx, y + dy
    rule = MODULE_RULES.get((flat, name))
    if rule:
        return x + rule[0], y + rule[1]
    return x, y


# ────────────────────────────────────────── 정답지 (Teg_location raw 값)
def load_ref(vehicle: str) -> tuple[dict[str, list[tuple[float, float]]] | None, str, str]:
    """Teg_location 의 raw ebeam 값 → ({teg: [(x, y), ...]}, 경로, 오류문).

    설비 원문의 좌표는 배율 적용 전 값이므로 raw ebeam_x/ebeam_y 와 직접 비교한다.
    동명 TEG 가 여러 행이면 모두 후보로 두고 가장 가까운 행과 대조한다.
    """
    tdf, path = _tm.load_tegs()
    if tdf is None:
        return None, str(path), f"Teg_location 파일 없음/무효: {path}"
    sub = tdf[tdf["vehicle"] == str(vehicle).strip()]
    if sub.empty:
        return None, str(path), f"Teg_location 에 vehicle '{vehicle}' 이(가) 없습니다"
    ref: dict[str, list[tuple[float, float]]] = {}
    for _, row in sub.iterrows():
        ref.setdefault(str(row["teg"]), []).append(
            (float(row["ebeam_x"]), float(row["ebeam_y"])))
    return ref, str(path), ""


def _compare(ref: dict[str, list[tuple[float, float]]] | None,
             name: str, x: float, y: float) -> dict:
    """계산 좌표 ↔ 정답지 대조 → {status, ref_x, ref_y, dx, dy}.

    status: match | mismatch | missing(정답지에 이름 없음) | noref(정답지 자체 없음)
    동명 후보가 여러 개면 가장 가까운 행 기준.
    """
    if ref is None:
        return {"status": "noref", "ref_x": None, "ref_y": None, "dx": None, "dy": None}
    cands = ref.get(name)
    if not cands:
        return {"status": "missing", "ref_x": None, "ref_y": None, "dx": None, "dy": None}
    cx, cy = min(cands, key=lambda c: abs(c[0] - x) + abs(c[1] - y))
    same = abs(cx - x) < TOL and abs(cy - y) < TOL
    return {"status": "match" if same else "mismatch",
            "ref_x": _num(cx), "ref_y": _num(cy),
            "dx": _num(x - cx), "dy": _num(y - cy)}


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
    (dx, dy), known = get_offset(veh, used)

    ref, ref_path, ref_err = load_ref(veh) if veh else (None, "", "제품명(vehicle)이 비어 있습니다")

    rows = []
    summary = {"match": 0, "mismatch": 0, "missing": 0, "total": len(tegs)}
    for t in tegs:
        nx, ny = transform(t["name"], t["x"], t["y"], used, dx, dy)
        cmp_ = _compare(ref, t["name"], nx, ny)
        if cmp_["status"] in summary:
            summary[cmp_["status"]] += 1
        rule = MODULE_RULES.get((used, t["name"]))
        rows.append({
            **t, "calc_x": _num(nx), "calc_y": _num(ny), **cmp_,
            "rule_note": rule[2] if rule else "",
        })

    return {
        "ok": True,
        "vehicle": veh,
        "maps": maps,
        "patterns": patterns,
        "flat": {"detected": detected, "why": why, "used": used},
        "offset": {"dx": _num(dx), "dy": _num(dy), "known": known},
        "v_r_offset": V_R_OFFSET,
        "v_r_note": V_R_NOTE,
        "teg": {
            "rows": rows,
            "summary": summary,
            "ref_ok": ref is not None,
            "ref_error": ref_err,
            "ref_path": ref_path,
            "ref_count": sum(len(v) for v in ref.values()) if ref else 0,
        },
    }
