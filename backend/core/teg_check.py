# -*- coding: utf-8 -*-
"""core/teg_check.py — TEG 설비값 검사 (설비 원문 ↔ Teg_location 정답지 대조).

설비에서 복사한 레시피 원문을 파싱해 두 가지를 검사한다.
  1) <SITES> 의 Pattern site 좌표가 #wafer-map 측정 샷(t) 위에 있는지 (판정은 프론트)
  2) #teg-map 의 module 좌표가 flat 변환 후 TEG 위치 조회의 정답지
     (Teg_location 파일의 raw ebeam_x/ebeam_y — ebeam_scale 적용 전 값)와 일치하는지

Mapfile 좌표계 (UI 표기: h=Horizontal, v_R=Vertical(R)):
  · Mapfile 은 PCHK 기준으로 재계산되어 올라간 값 — flat(h/v_R)별로 해당 PCHK 가
    (0,0)이 되는 상대좌표다. 원래 ebeam 절대좌표로 **원복(상대→절대)** 하려면
    해당 PCHK 절대좌표를 "더한다":
      · h    — 회전 없음. 그대로 두고 H_PCHK 절대좌표(flat_offsets['h'])를 더한다.
      · v_R  — 설비의 반시계 90° 세팅을 시계 90° 회전으로 원복: (x, y) → (y, -x).
               V_PCHK 절대좌표(flat_offsets['v_R'])를 더한다.
    마지막으로 모듈(TEG)별 오프셋을 적용(항상 H/TEG 관점 입력, 양수=빼기)해
    ebeam 절대좌표를 복원, 정답지와 비교한다.
오프셋들은 teg_map.json 의 `check` 섹션에 저장 — ⚙️ 설정에서 편집.

원문 형식 (Streamlit PoC 'Wafer Map 검사기 v17' 파서 포팅):
  · 각 줄 앞의 "행번호 " 프리픽스는 제거 (행번호 없는 원문도 허용)
  · #wafer-map <이름> 섹션 안의 ! ~ ! 블록이 맵 (t=측정 샷, -=빈칸)
  · <SITES> 섹션 안의 "Pattern <이름>" + "pt x y" 행들
  · #teg-map 섹션 안의 "module <이름> (x, y) ! 꼬리표" 행들
    — module 이름은 꼬리표를 ','로 나눈 첫 토큰 (없으면 module 뒤 이름)
    — 꼬리표의 H_PCHK / V_PCHK 로 flat 자동 판정 (TEG 별로 개별 판정)
      · H_PCHK 이 없으면 H_PRBCHK, V_PCHK 이 없으면 V_PRBCHK 을 폴백으로 인식
    — module 이름에 MAIN 이 들어간 행은 기본 제외 (검사 대상 아님)
"""
from __future__ import annotations

import re
from typing import Any

from core import teg_map as _tm

# ────────────────────────────────────────── 변환 규칙
# flat 별 PCHK 절대좌표(offset)·모듈(TEG)별 오프셋은 teg_map.json 의
# `check` 섹션(teg_map.DEFAULT_CHECK_CFG)에 저장하고 ⚙️ 설정에서 편집한다.
# '!' 뒤 꼬리표 → flat. 우선순위대로: PCHK 우선, 없으면 PRBCHK 폴백.
# 설비/사이트에 따라 기준 PCHK 표기가 다를 수 있다 — 내장 마커로 안 잡히면
# 사용자 입력 마커(inspect custom_markers / ⚙️ 설정 check.custom_markers)를
# 내장보다 먼저 매칭한다.
FLAT_MARKERS = {
    "H_PCHK": "h", "V_PCHK": "v_R",              # 우선
    "H_PRBCHK": "h", "V_PRBCHK": "v_R",          # 폴백 (PCHK 없을 때)
}
FLATS = ("h", "v_R")                              # 저장 키 (UI: Horizontal / Vertical(R))
# flat 별 기준 PCHK/PRBCHK 의 정답지 이름 후보 (우선순위). Mapfile 은 이 기준
# PCHK 이 (0,0)인 상대좌표라, 원래 ebeam 절대좌표로 원복할 때 더하는 기준점(dx, dy)은
# 이 PCHK 의 DB ebeam_x/ebeam_y 다. 정답지(Teg_location)에서 이 이름을 찾아 기준으로 쓴다.
PCHK_REF_NAMES = {
    "h": ["H_PCHK", "H_PRBCHK"],
    "v_R": ["V_PCHK", "V_PRBCHK"],
}
TOL = 1e-6                                        # 좌표 비교 허용오차 (이 이내면 완전 일치)
# ΔX·ΔY 가 각각 이 값 이내로만 어긋나면 '확인필요'(△) — 완전 일치는 아니지만
# 소수점 반올림·설비 세팅 차이 정도의 작은 오차일 수 있어 불일치와 구분한다.
# 이 값을 넘으면 '불일치'(✕). 필요 시 여기서 임계값을 조정한다.
WARN_TOL = 3.0
MAIN_RE = re.compile(r"(?<![A-Za-z])MAIN", re.IGNORECASE)   # module 이름의 MAIN 판별

# ────────────────────────────────────────── 파서
NUM = r"-?\d+(?:\.\d+)?"
NUMBERED_RE = re.compile(r"^\s*(\d+)\s+(.*)$")                 # "행번호 공백 내용"
PATTERN_RE = re.compile(r"\bPattern\b\s*[,:=]?\s*(.*)$", re.IGNORECASE)
MODULE_RE = re.compile(
    rf"\bmodule\b\s*[,:=]?\s*(.+?)\s*\(\s*({NUM})\s*,\s*({NUM})\s*\)", re.IGNORECASE
)
MODULE_FALLBACK_RE = re.compile(rf"^\s*(.+?)\s*\(\s*({NUM})\s*,\s*({NUM})\s*\)")
MODULE_WORD_RE = re.compile(r"\bmodule\b\s*[,:=]?\s*([^\s(,:=]+)", re.IGNORECASE)  # 'module' 뒤 단어

# ── 새 Mapfile 양식(대괄호 섹션) — 기존(#wafer-map / <SITES> / #teg-map)과 함께 자동 처리.
#   [TEST_POINT]          = 웨이퍼 맵 (맵 문자 행)
#   [TEST_SITES]          = 패턴 site: "<이름>(Pattern) = (전체point수)(x1,y1),(x2,y2)..."
#   [MODULES_COORDINATE]  = module 좌표: "<후보2> (x, y) ! <module_name>"
#   대괄호/샵/꺾쇠 로 시작하는 줄이 섹션 경계 (SECTION_STOPS).
SECTION_STOPS = ("#", "<", "[")
POINT_TAG = "[test_point]"
TEST_SITES_TAG = "[test_sites]"
MODULES_TAG = "[modules_coordinate]"
MAP_ROW_RE = re.compile(r"[-.tT]+")                                  # 맵 행(문자만)
TEST_SITES_LINE_RE = re.compile(r"^(.*?)\s*=\s*(.*)$")               # "이름 = 좌표들"
PATTERN_ANNOT_RE = re.compile(r"^(.*?)\s*\(\s*pattern\s*\)\s*$", re.IGNORECASE)  # 끝의 (Pattern) 주석


def _parse_point_map(lines: list[str]) -> list[dict]:
    """[TEST_POINT] 섹션 → 웨이퍼 맵 1개 (맵 문자 행만). 없으면 [].

    위/아래의 순수 '----' 경계선(측정 셀 t 가 하나도 없는 테두리 행)은 맵 테두리
    표시로 보고 행에서 제외한다 — site 좌표(x,y)의 격자 기준을 콘텐츠 행에 맞춘다.
    (내부의 빈 행은 유지.)
    """
    body = _section(lines, POINT_TAG, stop=SECTION_STOPS)
    rows = [s for s in body if s and MAP_ROW_RE.fullmatch(s)]
    while rows and "t" not in rows[0].lower():
        rows.pop(0)
    while rows and "t" not in rows[-1].lower():
        rows.pop()
    if not rows:
        return []
    w = max(len(r) for r in rows)
    return [{"name": "TEST_POINT", "rows": [r.ljust(w, "-") for r in rows],
             "w": w, "h": len(rows), "origin": "top-left"}]


def _parse_test_sites(lines: list[str]) -> list[dict]:
    """[TEST_SITES] 섹션 → [{name, points:[{pt,x,y}]}].

    줄: '<이름>(Pattern) = (전체point수)(x1,y1),(x2,y2)...'.
      · 이름  = '=' 앞 (끝의 '(Pattern)' 주석 제거)
      · point = '=' 뒤의 (x,y) 쌍(순서대로 pt=1..N). 단일 숫자 괄호((전체수))는 건너뜀.
    """
    body = _section(lines, TEST_SITES_TAG, stop=SECTION_STOPS)
    sites: list[dict] = []
    for s in body:
        if not s or "=" not in s:
            continue
        m = TEST_SITES_LINE_RE.match(s)
        if not m:
            continue
        left, right = m.group(1), m.group(2)
        annot = PATTERN_ANNOT_RE.match(left.strip())
        name = (annot.group(1) if annot else left).strip() or f"Pattern {len(sites) + 1}"
        pts = []
        for g in re.findall(r"\(([^)]*)\)", right):
            nums = re.findall(NUM, g)
            if len(nums) >= 2:                       # (x,y) 쌍만 site — (전체수) 단일값은 skip
                pts.append({"pt": len(pts) + 1, "x": _num(nums[0]), "y": _num(nums[1])})
        if not pts:
            continue
        key, n = name, 2
        taken = {si["name"] for si in sites}
        while key in taken:
            key, n = f"{name} ({n})", n + 1
        sites.append({"name": key, "points": pts})
    return sites


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
    maps += _parse_point_map(lines)   # 새 양식 [TEST_POINT] 도 함께 처리
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
    sites += _parse_test_sites(lines)   # 새 양식 [TEST_SITES] 도 함께 처리
    return sites


MAX_NAME_CANDIDATES = 6   # 행별 이름 후보 표시 상한 (module 토큰 + 꼬리표 토큰)


def parse_teg(lines: list[str]) -> list[dict]:
    """#teg-map → [{idx, name, x, y, tail, candidates, name_source}, ...]

    name = '!' 뒤 꼬리표를 ','로 나눈 첫 토큰. 꼬리표가 없으면 'module' 뒤 이름.
    그래도 이름이 비면 해당 행의 'module' 키워드 뒤 단어를 이름으로 쓴다.

    엔지니어마다 module 이름을 적는 위치가 다르다(module~( 사이 / 꼬리표 1번째 /
    꼬리표 2번째, 양쪽에 서로 다른 값을 적기도 함) — 후보 전체를 candidates 로
    내보내 UI 에서 행별로 이름을 재지정(inspect name_overrides)할 수 있게 한다.
    idx 는 파싱 순서 고유 번호 — 같은 원문이면 안정적이라 재지정 키로 쓴다.
    """
    # 기존 #teg-map + 새 양식 [MODULES_COORDINATE] 를 모두 대상으로 (자동 처리).
    body = (_section(lines, "#teg-map", stop=SECTION_STOPS)
            + _section(lines, MODULES_TAG, stop=SECTION_STOPS))
    out = []
    for s in body:
        if not s:
            continue
        m = MODULE_RE.search(s) or MODULE_FALLBACK_RE.match(s)
        if not m:
            continue
        tail = s.split("!", 1)[1].strip() if "!" in s else ""
        mod_token = m.group(1).strip(" ,:=\t")
        tail_tokens = [t.strip() for t in tail.split(",") if t.strip()] if tail else []
        # 자동 인식 규칙(기존과 동일): 꼬리표 첫 토큰(비어 있으면 다음 토큰이 아니라
        # 'module' 뒤 단어로 폴백) > module~( 이름 > 'module' 뒤 단어.
        name = tail.split(",")[0].strip() if tail else mod_token
        source = "tail0" if tail else "module"
        if not name:   # 이름이 비면 'module' 뒤 단어로 폴백
            mw = MODULE_WORD_RE.search(s)
            name = mw.group(1).strip(" ,:=\t") if mw else ""
            source = "word" if name else source
        candidates: list[str] = []
        for v in ([mod_token] if mod_token else []) + tail_tokens:
            if v and v not in candidates:
                candidates.append(v)
        out.append({"idx": len(out), "name": name, "auto_name": name,
                    "x": _num(m.group(2)), "y": _num(m.group(3)), "tail": tail,
                    "candidates": candidates[:MAX_NAME_CANDIDATES],
                    "name_source": source})
    return out


def is_main(name: str) -> bool:
    """module 이름에 MAIN 이 들어가면 검사 대상에서 제외 (domain 등 오탐 방지)."""
    return bool(MAIN_RE.search(str(name or "")))


def build_marker_map(custom_markers: dict | None = None) -> dict[str, str]:
    """마커→flat 매핑 — 사용자 입력 마커를 내장(FLAT_MARKERS)보다 먼저 둔다.

    custom_markers: {"h": ["H_TPCHK", ...], "v_R": ["V_TPCHK", ...]} 형태.
    H_PCHK/H_PRBCHK 같은 내장 표기로 기준 PCHK 이 안 잡히는 설비 원문에서
    사용자가 직접 기준 마커를 지정할 수 있게 한다."""
    merged: dict[str, str] = {}
    if isinstance(custom_markers, dict):
        for flat in FLATS:
            for name in custom_markers.get(flat) or []:
                token = str(name or "").strip()
                if token and token.upper() not in {k.upper() for k in merged}:
                    merged[token] = flat
    for marker, flat in FLAT_MARKERS.items():
        if marker.upper() not in {k.upper() for k in merged}:
            merged[marker] = flat
    return merged


def teg_flat(tail: str, markers: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """한 TEG 꼬리표에서 flat 판정 → (flat, 매칭 마커).

    우선순위: 사용자 입력 마커 > H_PCHK/V_PCHK > H_PRBCHK/V_PRBCHK 폴백.
    """
    for marker, flat in (markers or FLAT_MARKERS).items():
        if re.search(rf"\b{re.escape(marker)}\b", str(tail or ""), re.IGNORECASE):
            return flat, marker
    return None, None


def detect_flat(tegs: list[dict], markers: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """전체 TEG 중 첫 마커로 전역 기본 flat 판정 (마커 없는 TEG·표시용)."""
    for t in tegs:
        flat, marker = teg_flat(t["tail"], markers)
        if flat:
            return flat, f"{marker}  (module {t['name']})"
    return None, None


def transform(name: str, x: float, y: float, flat: str, dx: float, dy: float,
              rules: dict[tuple[str, str], tuple[float, float, str]] | None = None,
              ) -> tuple[float, float]:
    """Mapfile 상대좌표(해당 PCHK = (0,0)) → 원래 ebeam 절대좌표 **원복**.

    Mapfile 은 PCHK 기준으로 재계산돼 올라간 flat별 상대좌표다. 상대→절대
    원복이므로 해당 PCHK 절대좌표를 "더한다" (빼지 않는다). 순서:
      · v_R — 설비의 반시계 90° 세팅을 시계 90° 회전으로 원복: (x, y) → (y, -x).
              PCHK 절대좌표(dx, dy)를 더한다.
      · h   — 회전 없이 PCHK 절대좌표(dx, dy)를 더한다.
    마지막으로 모듈(TEG)별 보정(rules)을 더한다. 모듈별 오프셋은 항상 Horizontal
    (TEG) 관점으로 입력되며, 양수 = 빼기 규약이다. Vertical(R) TEG 이면 TEG 관점
    x → 실좌표 y, TEG 관점 y → 실좌표 -x 로 축 변환 후 부호 반전한다.

    rules: {(flat, module_name): (ox, oy, note)} — ⚙️ 설정의 모듈별 오프셋
           (Horizontal/TEG 관점 입력값, 양수 = 빼기).
    """
    if flat == "v_R":
        x, y = y, -x            # 시계 90° 회전 (설비 반시계 세팅 원복)
    x, y = x + dx, y + dy       # PCHK 절대좌표 반영 → ebeam 절대좌표 복원
    rule = (rules or {}).get((flat, name))
    if rule:
        ox, oy = rule[0], rule[1]
        if flat == "v_R":
            # TEG 관점(H 기준) offset → 실좌표: TEG x → real y, TEG y → real -x
            # 양수 = 빼기 → real (x + oy, y - ox)
            return x + oy, y - ox
        # Horizontal: 양수 = 빼기 → real (x - ox, y - oy)
        return x - ox, y - oy
    return x, y


# ────────────────────────────────────────── 정답지 (Teg_location raw 값)
def load_ref(vehicle: str) -> tuple[dict[str, list[dict]] | None, dict[str, str], str, str]:
    """Teg_location 의 raw ebeam 값 → (ref, top_cell→teg 맵, 경로, 오류문).

    ref = {teg: [{x, y, w, h}, ...]}. 설비 원문의 좌표는 배율 적용 전 값이므로 raw
    ebeam_x/ebeam_y 와 직접 비교한다. w/h 는 TEG 실물 크기(mm) — 배율·기본값·
    flat_zone(v=w/h 스왑) 적용 (map_payload 와 동일). 동명 TEG 가 여러 행이면 모두
    후보로 두고 가장 가까운 행과 대조한다.

    top_cell 은 그 teg 의 다른 이름(1:1) — module name 을 teg 뿐 아니라 top_cell 로도
    정확 매칭할 수 있게 {top_cell: teg} 맵을 함께 만든다. 같은 top_cell 이 여러 teg 를
    가리키면 첫 행 기준(1:1 전제).
    """
    tdf, path = _tm.load_tegs()
    if tdf is None:
        return None, {}, str(path), f"Teg_location 파일 없음/무효: {path}"
    sub = tdf[tdf["vehicle"] == str(vehicle).strip()]
    if sub.empty:
        return None, {}, str(path), f"Teg_location 에 vehicle '{vehicle}' 이(가) 없습니다"
    cfg = _tm.load_cfg()
    scale = float(cfg["ebeam_scale"])
    ref: dict[str, list[dict]] = {}
    tc_to_teg: dict[str, str] = {}
    for _, row in sub.iterrows():
        tw = float(row["teg_w"]) * scale if row["teg_w"] == row["teg_w"] else float(cfg["teg_default_w"])
        th = float(row["teg_h"]) * scale if row["teg_h"] == row["teg_h"] else float(cfg["teg_default_h"])
        fz = str(row.get("flat_zone") or "h").strip().lower()
        if fz == "v":
            tw, th = th, tw
        teg = str(row["teg"]).strip()
        ref.setdefault(teg, []).append(
            {"x": float(row["ebeam_x"]), "y": float(row["ebeam_y"]), "w": tw, "h": th})
        tc = str(row.get("top_cell") or "").strip()
        # top_cell 은 그 teg 의 별칭 — teg 이름 자체와 충돌하지 않을 때만 등록.
        if tc and tc not in tc_to_teg and tc != teg:
            tc_to_teg[tc] = teg
    return ref, tc_to_teg, str(path), ""


def pchk_base_offsets(ref: dict[str, list[dict]] | None,
                      custom_markers: dict | None = None,
                      ) -> dict[str, tuple[float, float, str]]:
    """flat 별 기준 PCHK/PRBCHK 의 DB ebeam 좌표 → {flat: (dx, dy, ref_name)}.

    Mapfile 은 기준 PCHK = (0,0) 상대좌표이므로, 원래 ebeam 절대좌표 원복의
    기준점(dx, dy)은 그 PCHK 의 DB ebeam_x/ebeam_y 다. flat 별로
      · h    — 사용자 지정 마커 > H_PCHK > H_PRBCHK
      · v_R  — 사용자 지정 마커 > V_PCHK > V_PRBCHK
    순으로 정답지(Teg_location)에서 이름을 찾아 그 raw ebeam 좌표를 기준으로 쓴다.
    정답지에 기준 PCHK 이 없으면 그 flat 은 결과에서 빠지고, 호출부는 ⚙️ 설정의
    flat_offsets 를 폴백으로 쓴다. 동명 후보가 여럿이면 첫 행 기준.
    """
    if not ref:
        return {}
    by_upper: dict[str, tuple[str, dict]] = {}
    for name, cands in ref.items():
        if cands:
            by_upper.setdefault(str(name).strip().upper(), (str(name), cands[0]))
    out: dict[str, tuple[float, float, str]] = {}
    for flat in FLATS:
        names = [str(m).strip() for m in ((custom_markers or {}).get(flat) or []) if str(m).strip()]
        names += PCHK_REF_NAMES.get(flat, [])
        for nm in names:
            hit = by_upper.get(nm.upper())
            if hit:
                ref_name, c0 = hit
                out[flat] = (float(c0["x"]), float(c0["y"]), ref_name)
                break
    return out


def _status_of(ddx: float, ddy: float) -> str:
    """ΔX·ΔY 로 3단계 판정: match(일치) | warning(확인필요) | mismatch(불일치).

    · 둘 다 TOL 이내      → match  (완전 일치)
    · 둘 다 WARN_TOL 이내 → warning(확인필요 — 소수점·세팅 차이 정도의 작은 오차)
    · 그 외              → mismatch(불일치)
    """
    if abs(ddx) < TOL and abs(ddy) < TOL:
        return "match"
    if abs(ddx) <= WARN_TOL and abs(ddy) <= WARN_TOL:
        return "warning"
    return "mismatch"


def _name_tokens(t: dict) -> list[str]:
    """한 module 행의 이름 매칭 후보 — 인식/재지정된 이름 우선, 그 뒤 파싱 후보 전체.

    순서 기반이 아니라 '이름 기반 정확 매칭' 을 위해, 이 토큰들을 순서대로 정답지의
    teg/top_cell 과 완전 일치 검사한다 (앞선 토큰 우선 — override/인식 이름이 먼저)."""
    out: list[str] = []
    for tok in [t.get("name")] + list(t.get("candidates") or []):
        s = str(tok or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def resolve_ref_teg(t: dict, ref: dict[str, list[dict]] | None,
                    tc_to_teg: dict[str, str] | None) -> tuple[str | None, str | None, str | None]:
    """module 이름 후보 중 정답지의 teg 또는 top_cell 과 **완전 일치**하는 걸 찾아
    대조할 teg 를 결정 → (teg, match_source['teg'|'top_cell'], 일치한 토큰).

    순서 기반이 아니라 이름 기반 정확 매칭: 후보를 앞에서부터 보며 teg 이름 일치를
    top_cell 일치보다 우선한다. 없으면 (None, None, None). (확장체크는 별도 함수.)
    """
    if not ref:
        return None, None, None
    tc_to_teg = tc_to_teg or {}
    tokens = _name_tokens(t)
    for tok in tokens:              # teg 이름 완전 일치 우선
        if tok in ref:
            return tok, "teg", tok
    for tok in tokens:              # 그다음 top_cell(별칭) 완전 일치
        teg = tc_to_teg.get(tok)
        if teg is not None:
            return teg, "top_cell", tok
    return None, None, None


def resolve_ref_teg_extended(t: dict, ref: dict[str, list[dict]] | None,
                             tc_to_teg: dict[str, str] | None,
                             ) -> tuple[str | None, str | None, str | None]:
    """확장체크 — 정답지 미등록 module 을 위해 이름 뒤 '01' 을 떼고 재매칭.

    예: TEGA01 이 teg/top_cell 어디에도 없으면 '01' 을 떼고 TEGA 로 다시 teg/top_cell
    완전 일치를 시도한다. 성공하면 (teg, match_source, 원래 토큰) — 원래 토큰은
    UI 에 'TEGA01 → TEGA' 처럼 근거로 보여준다. 실패면 (None, None, None).
    """
    if not ref:
        return None, None, None
    tc_to_teg = tc_to_teg or {}
    for tok in _name_tokens(t):
        if len(tok) > 2 and tok.endswith("01"):
            base = tok[:-2]
            if base in ref:
                return base, "teg", tok
            teg = tc_to_teg.get(base)
            if teg is not None:
                return teg, "top_cell", tok
    return None, None, None


def _compare(ref: dict[str, list[dict]] | None, ref_teg: str | None,
             x: float, y: float, extended: bool = False) -> dict:
    """계산 좌표 ↔ 정답지(대상 teg) 대조 → {status, ref_x, ref_y, dx, dy, ref_w, ref_h, ref_seq}.

    ref_teg = resolve_ref_teg(_extended) 로 결정된 정답지 teg (없으면 미등록).
    status: match | warning | mismatch | extended(확장체크로 매칭) |
            missing(정답지에 일치 이름 없음) | noref(정답지 자체 없음)
      · match   — ΔX·ΔY 모두 0 (완전 일치)
      · warning — ΔX·ΔY 가 각각 WARN_TOL 이내 (확인필요, 작은 오차)
      · mismatch— 그 이상 (불일치)
      · extended— '01' 제외 재매칭으로 정답지 teg 를 찾음 (위치가 아닌 이름 검증)
    동명 후보가 여러 개면 가장 가까운 행 기준. ref_w/ref_h 는 TEG 크기(mm).
    ref_seq: 동명 TEG 중 매칭된 순번(1-based). 후보가 1 개면 None.
    ref_total: 동명 TEG 전체 개수 (후보가 1 개면 None).
    """
    if ref is None:
        return {"status": "noref", "ref_x": None, "ref_y": None, "dx": None, "dy": None,
                "ref_w": None, "ref_h": None, "ref_seq": None, "ref_total": None}
    cands = ref.get(ref_teg) if ref_teg else None
    if not cands:
        return {"status": "missing", "ref_x": None, "ref_y": None, "dx": None, "dy": None,
                "ref_w": None, "ref_h": None, "ref_seq": None, "ref_total": None}
    best_idx = min(range(len(cands)),
                   key=lambda i: abs(cands[i]["x"] - x) + abs(cands[i]["y"] - y))
    c = cands[best_idx]
    ddx, ddy = x - c["x"], y - c["y"]
    # 확장체크(TEGA01→TEGA)는 서로 다른 die 라 좌표는 다를 수 있어 위치 판정 대신
    # 'extended'(이름 검증) 로 표시한다. ΔX·ΔY 는 참고용으로 계산해 함께 노출.
    status = "extended" if extended else _status_of(ddx, ddy)
    n = len(cands)
    return {"status": status,
            "ref_x": _num(c["x"]), "ref_y": _num(c["y"]),
            "dx": _num(ddx), "dy": _num(ddy),
            "ref_w": c["w"], "ref_h": c["h"],
            "ref_seq": best_idx + 1 if n > 1 else None,
            "ref_total": n if n > 1 else None}


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

    TEG 앵커 = 좌하단(Teg_location 규약), y 는 앵커에서 위(+h)로 뻗는다:
    TEG 범위 x [x0, x0+w], y [y0, y0+h]. 경계가 정확히 맞닿는 것은 겹침으로 안 봄.
    """
    tx1, ty1 = x0 + w, y0 + h
    for c in cells:
        if (x0 < c["x"] + c["w"] - eps and tx1 > c["x"] + eps
                and y0 < c["y"] + c["h"] - eps and ty1 > c["y"] + eps):
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
def inspect(vehicle: str, text: str, flat: str | None = None,
            custom_markers: dict | None = None,
            name_overrides: dict | None = None) -> dict:
    """원문 전체 검사 payload — 맵/패턴 파싱 + TEG flat 변환·정답지 대조.

    custom_markers: 기준 PCHK 이 내장 마커(H_PCHK/V_PCHK/H_PRBCHK/V_PRBCHK)로
    안 잡히는 원문에서 사용자가 지정한 마커 {"h": [...], "v_R": [...]}.
    ⚙️ 설정(check.custom_markers)에 저장된 마커와 합쳐 쓴다 (요청 값 우선).

    name_overrides: {idx(str|int): 이름} — 자동 인식된 module 이름이 틀린 행을
    UI 에서 후보 토큰으로 재지정한 것. 같은 원문에서 idx 는 파싱 순서라 안정적.
    override 는 MAIN 제외 판정보다 먼저 적용 — MAIN 으로 잘못 읽혀 제외된 행도
    이름을 바로잡으면 검사 대상으로 돌아온다.
    """
    veh = str(vehicle or "").strip()
    lines = strip_line_numbers(str(text or ""))
    maps = parse_wafer_maps(lines)
    patterns = parse_sites(lines)
    tegs_all = parse_teg(lines)

    overrides: dict[int, str] = {}
    for k, v in (name_overrides or {}).items():
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        v = str(v or "").strip()
        if v:
            overrides[i] = v
    for t in tegs_all:
        ov = overrides.get(t["idx"])
        if ov:
            t["name"] = ov
            t["name_source"] = "override"

    # ⚙️ 설정의 TEG Mapfile 체크 섹션 — flat 별 PCHK 오프셋, 모듈별 오프셋
    cfg = _tm.load_cfg()
    chk = cfg["check"]

    # 사용자 마커: 요청 값 + ⚙️ 설정 저장분 merge (요청 값이 앞 = 우선)
    saved_markers = chk.get("custom_markers") or {}
    merged_custom = {
        f: list(dict.fromkeys(
            [str(m).strip() for m in ((custom_markers or {}).get(f) or []) if str(m).strip()]
            + [str(m).strip() for m in (saved_markers.get(f) or []) if str(m).strip()]
        ))
        for f in FLATS
    }
    marker_map = build_marker_map(merged_custom)

    # module 이름에 MAIN 이 들어간 행은 기본 제외 (마커 판정은 전체에서 하되 검사는 비-MAIN 만)
    detected, why = detect_flat(tegs_all, marker_map)
    tegs = [t for t in tegs_all if not is_main(t["name"])]
    n_excluded = len(tegs_all) - len(tegs)
    # 제외된 MAIN 행도 최소 정보로 노출 — 자동 인식이 엉뚱한 토큰(MAIN 포함)을
    # 집어 잘못 제외된 경우 UI 에서 이름 재지정으로 되살릴 수 있게 한다.
    main_rows = [{"idx": t["idx"], "name": t["name"], "auto_name": t["auto_name"],
                  "x": t["x"], "y": t["y"],
                  "candidates": t["candidates"], "name_source": t["name_source"]}
                 for t in tegs_all if is_main(t["name"])]

    # flat 강제 여부 — 강제 시 모든 TEG 에 적용, 아니면 TEG 별 마커로 개별 판정
    forced = flat if flat in FLATS else None
    used = forced or detected or "h"   # 전역 기본 (마커 없는 TEG·표시용)
    flat_offsets = chk["flat_offsets"]

    ref, tc_to_teg, ref_path, ref_err = (
        load_ref(veh) if veh else (None, {}, "", "제품명(vehicle)이 비어 있습니다"))

    # flat 별 기준점(dx, dy): 정답지에 기준 PCHK/PRBCHK 이 있으면 그 DB ebeam 좌표를
    # 우선 사용(사용자 요청 — 환산X/Y 에 H/V_PCHK·PRBCHK 의 DB Ebeam 반영), 없으면
    # ⚙️ 설정의 flat_offsets 를 폴백으로 쓴다.
    pchk_bases = pchk_base_offsets(ref, merged_custom)

    def _offset(f: str) -> tuple[float, float]:
        base = pchk_bases.get(f)
        if base is not None:
            return base[0], base[1]
        ox, oy = flat_offsets.get(f, [0.0, 0.0])
        return float(ox), float(oy)

    dx, dy = _offset(used)   # 표시용 전역 오프셋
    rules = {(m["flat"], m["name"]): (m["dx"], m["dy"], m.get("note", ""))
             for m in chk["modules"]}

    # shot 크기·칩 격자 — 칩 겹침 검사(TEG 는 칩 사이 스크라이브에 있어야 정상)
    scale = float(cfg["ebeam_scale"])
    shot = _shot_info(veh) if veh else {"available": False, "checked": False}

    rows = []
    summary = {"match": 0, "warning": 0, "mismatch": 0, "extended": 0, "missing": 0,
               "total": len(tegs), "chip_overlap": 0}
    for t in tegs:
        # TEG 별 flat: 강제값 > 자기 꼬리표 마커 > 전역 기본. 오프셋도 flat 에 맞춰 선택.
        t_flat, t_marker = teg_flat(t["tail"], marker_map)
        used_t = forced or t_flat or detected or "h"
        tdx, tdy = _offset(used_t)
        nx, ny = transform(t["name"], t["x"], t["y"], used_t, tdx, tdy, rules)
        # 순서 기반이 아니라 이름 기반 정확 매칭 — 후보 토큰 중 정답지 teg/top_cell 과
        # 완전 일치하는 걸 찾아 그 teg 로 대조. 없으면 '01' 제외 재매칭(확장체크).
        ref_teg, msrc, mtok = resolve_ref_teg(t, ref, tc_to_teg)
        extended = False
        if ref_teg is None and ref is not None:
            ref_teg, msrc, mtok = resolve_ref_teg_extended(t, ref, tc_to_teg)
            extended = ref_teg is not None
        cmp_ = _compare(ref, ref_teg, nx, ny, extended=extended)
        if cmp_["status"] in summary:
            summary[cmp_["status"]] += 1
        rule = rules.get((used_t, t["name"]))
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
            "flat_used": used_t, "flat_marker": t_marker,
            # 이름 기반 매칭 결과 — 대조한 정답지 teg 와 매칭 근거(teg/top_cell/확장 토큰)
            "ref_teg": ref_teg, "match_source": msrc, "match_token": mtok,
            "extended": extended,
            "mm_x": round(mm_x, 4), "mm_y": round(mm_y, 4),
            "teg_w": round(tw, 4), "teg_h": round(th, 4),
            "chip_overlap": overlap,
            "rule_note": (rule[2] or f"모듈 오프셋 ({_num(rule[0])}, {_num(rule[1])})") if rule else "",
        })

    # ── MAIN 그룹 — MAIN 은 개발제품 die 급의 큰 직사각형 블록이고, Mapfile 에는
    #    내부 TEG 행들이 같은 그룹명으로 나열된다:
    #      module DUMMY1 (x, y) ! MAIN02, ,module_detail, LOT7
    #    그룹 = MAIN 이름(꼬리표 첫 토큰), 내부 TEG 이름 = 그 뒤 첫 유효 토큰
    #    (빈 토큰·그룹명 재등장·flat 마커 제외). 좌표는 일반 행과 동일하게
    #    ebeam 절대좌표로 원복 — 위치 조회 역반영(main-overlay) 후보로 내보낸다.
    marker_upper = {mk.upper() for mk in marker_map}
    main_groups_map: dict[str, list[dict]] = {}
    for t in tegs_all:
        if not is_main(t["name"]):
            continue
        group = t["name"]
        detail = ""
        for tok in [tok.strip() for tok in t["tail"].split(",")][1:] if t["tail"] else []:
            if not tok or tok == group or tok.upper() in marker_upper:
                continue
            detail = tok
            break
        t_flat, _mk = teg_flat(t["tail"], marker_map)
        used_t = forced or t_flat or detected or "h"
        tdx, tdy = _offset(used_t)
        nx, ny = transform(detail or group, t["x"], t["y"], used_t, tdx, tdy, rules)
        entry = main_groups_map.setdefault(group, [])
        if not detail:
            detail = f"{group}_{len(entry) + 1}"
        names = {e["teg"] for e in entry}
        base, n = detail, 2
        while detail in names:
            detail = f"{base}_{n}"
            n += 1
        # 칩 격자 모드면 내부 TEG 도 칩(die) 겹침 검사 — 일반 행과 동일 규약
        # (기본 TEG 크기 사용 — MAIN 내부 TEG 는 정답지에 없어 크기 정보가 없음)
        overlap = None
        if shot.get("checked"):
            overlap = _overlaps_chip(shot["cells"], nx * scale, ny * scale,
                                     float(cfg["teg_default_w"]), float(cfg["teg_default_h"]))
        entry.append({"teg": detail, "x": _num(nx), "y": _num(ny), "chip_overlap": overlap})
    # ── 체크 대상 TEG 설정 여부 — 위치 조회에서 지정한 체크 대상 TEG 가 이 Mapfile 의
    #    module name 목록에 (teg 또는 top_cell 완전 일치로) 등장하는지. 등장하지 않은
    #    대상은 '미설정'. 받은 module name 목록 = 파싱된 각 행의 이름 + 이름 후보 토큰
    #    전체 (MAIN 행 포함 — 완전 일치라 substring 오탐 없음).
    module_tokens: set[str] = set()
    for t in tegs_all:
        if t.get("name"):
            module_tokens.add(str(t["name"]).strip())
        for c in t.get("candidates") or []:
            if c:
                module_tokens.add(str(c).strip())
    targets = _tm.target_verification(veh, module_tokens) if veh else {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0}

    main_groups = []
    if main_groups_map:
        try:
            existing = _tm.get_main_overlays(veh) if veh else {}
        except Exception:
            existing = {}
        for g in sorted(main_groups_map):
            prev = existing.get(g) or {}
            items = main_groups_map[g]
            main_groups.append({"group": g, "tegs": items,
                                "chip_overlap": sum(1 for e in items if e.get("chip_overlap")),
                                "applied_at": str(prev.get("applied_at") or "")})

    return {
        "ok": True,
        "vehicle": veh,
        "maps": maps,
        "patterns": patterns,
        "flat": {
            "detected": detected, "why": why, "used": used,
            # 기준 PCHK 마커가 안 잡혔고 flat 강제도 없음 — 프론트가 사용자에게
            # 기준 마커(또는 flat 수동 선택) 입력을 요구해야 하는 상태.
            "needs_input": bool(detected is None and forced is None),
            "custom_markers": {f: list(merged_custom.get(f) or []) for f in FLATS},
        },
        "offset": {"dx": _num(dx), "dy": _num(dy)},
        # 기준점(dx, dy) 출처 — flat 별로 정답지 PCHK/PRBCHK DB 좌표(db)인지
        # ⚙️ 설정 flat_offsets(config)인지. UI 가 "H_PCHK 의 DB Ebeam 반영 중" 표시.
        "pchk_base": {
            "used": used,
            "source": "db" if pchk_bases.get(used) else "config",
            "ref_name": (pchk_bases.get(used) or (None, None, ""))[2],
            "flats": {
                f: {"dx": _num(b[0]), "dy": _num(b[1]), "ref_name": b[2]}
                for f, b in pchk_bases.items()
            },
        },
        "module_offset_note": "TEG(H) 관점 입력, 양수=빼기. V: TEG x→실y, TEG y→실-x",
        "shot": shot,
        "teg": {
            "rows": rows,
            "summary": summary,
            "excluded_main": n_excluded,
            "main_rows": main_rows,
            "main_groups": main_groups,
            "targets": targets,
            "ref_ok": ref is not None,
            "ref_error": ref_err,
            "ref_path": ref_path,
            "ref_count": sum(len(v) for v in ref.values()) if ref else 0,
        },
    }
