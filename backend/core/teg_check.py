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
from core import teg_shape as _teg_shape

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
    "VL_PCHK": "v_L", "V_L_PCHK": "v_L", "L_PCHK": "v_L",
    "VL_PRBCHK": "v_L", "V_L_PRBCHK": "v_L", "L_PRBCHK": "v_L",
}
FLATS = ("h", "v_R", "v_L")                     # 저장 키
# flat 별 기준 PCHK/PRBCHK 의 정답지 이름 후보 (우선순위). Mapfile 은 이 기준
# PCHK 이 (0,0)인 상대좌표라, 원래 ebeam 절대좌표로 원복할 때 더하는 기준점(dx, dy)은
# 이 PCHK 의 DB ebeam_x/ebeam_y 다. 정답지(Teg_location)에서 이 이름을 찾아 기준으로 쓴다.
PCHK_REF_NAMES = {
    "h": ["H_PCHK", "H_PRBCHK"],
    "v_R": ["V_PCHK", "V_PRBCHK"],
    "v_L": ["VL_PCHK", "V_L_PCHK", "L_PCHK", "VL_PRBCHK", "V_L_PRBCHK", "L_PRBCHK"],
}
TOL = 1e-6                                        # 좌표 비교 허용오차 (이 이내면 완전 일치)
# ΔX·ΔY 가 각각 이 값 이내로만 어긋나면 '확인필요'(△) — 완전 일치는 아니지만
# 소수점 반올림·설비 세팅 차이 정도의 작은 오차일 수 있어 불일치와 구분한다.
# 이 값을 넘으면 '불일치'(✕). 필요 시 여기서 임계값을 조정한다.
WARN_TOL = 3.0
MAIN_RE = _tm.MAIN_RE                                       # module 이름의 MAIN 판별

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

    새 bracket 양식은 맵 위/아래 경계를 ``----`` 장식 줄로 표시할 수 있다. 이런
    테두리 줄은 콘텐츠 행보다 **짧은** 전부-하이픈 줄이므로, 콘텐츠 폭보다 짧은
    경우에만 한 쌍을 제거한다. 콘텐츠와 **같은 폭**의 전부-하이픈 줄(원형 웨이퍼
    가장자리의 빈 샷 행)은 진짜 맵 행이므로 좌표 보존을 위해 그대로 유지한다.
    """
    body = _section(lines, POINT_TAG, stop=SECTION_STOPS)
    rows = [s for s in body if s and MAP_ROW_RE.fullmatch(s)]
    if not rows:
        return []
    # 테두리 제거는 첫/끝 줄이 (a) 서로 같고 (b) 전부 '-' 이며 (c) 내부 콘텐츠 폭보다
    # 짧을 때만. 같은 폭이면 빈 샷 행으로 보고 유지 → 격자 좌표가 밀리지 않는다.
    if (
        len(rows) >= 3
        and rows[0] == rows[-1]
        and set(rows[0]) == {"-"}
        and any("t" in row.lower() for row in rows[1:-1])
        and len(rows[0]) < max(len(r) for r in rows[1:-1])
    ):
        rows = rows[1:-1]
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


def merge_custom_markers(custom_markers: dict | None, saved: dict | None) -> dict:
    """요청 마커 + ⚙️ 설정 저장 마커 병합 (요청 값이 앞 = 우선, 중복 제거)."""
    return {
        f: list(dict.fromkeys(
            [str(m).strip() for m in ((custom_markers or {}).get(f) or []) if str(m).strip()]
            + [str(m).strip() for m in ((saved or {}).get(f) or []) if str(m).strip()]
        ))
        for f in FLATS
    }


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


def rotate_flat(flat: str, x: float, y: float) -> tuple[float, float]:
    """Equipment/local coordinates -> Horizontal-normalised wafer coordinates."""
    if flat == "v_R":
        return y, -x
    if flat == "v_L":
        return -y, x
    return x, y


def inverse_rotate_flat(flat: str, x: float, y: float) -> tuple[float, float]:
    if flat == "v_R":
        return -y, x
    if flat == "v_L":
        return y, -x
    return x, y


def module_effect(flat: str, dx: float, dy: float) -> tuple[float, float]:
    """Legacy positive=subtract module calibration expressed in real axes."""
    if flat == "v_R":
        return dy, -dx
    if flat == "v_L":
        return -dy, dx
    return -dx, -dy


def transform(name: str, x: float, y: float, flat: str, dx: float, dy: float,
              rules: dict[tuple[str, str], tuple[float, float, str]] | None = None,
              *, flat_correction: tuple[float, float] = (0.0, 0.0),
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
    x, y = rotate_flat(flat, x, y)
    # PCHK와 대상 TEG는 같은 first-pad 기준 형상으로 취급한다. 두 first-pad 항은
    # 상대좌표에서 서로 상쇄되므로 별도 형상 보정을 두지 않고 제품 ΔX/ΔY만 더한다.
    x = x + dx + float(flat_correction[0])
    y = y + dy + float(flat_correction[1])
    rule = (rules or {}).get((flat, name))
    if rule:
        ex, ey = module_effect(flat, rule[0], rule[1])
        return x + ex, y + ey
    return x, y


def inverse_transform(name: str, X: float, Y: float, flat: str, dx: float, dy: float,
                      rules: dict[tuple[str, str], tuple[float, float, str]] | None = None,
                      *, flat_correction: tuple[float, float] = (0.0, 0.0),
                      ) -> tuple[float, float]:
    """transform 의 역함수 — ebeam 절대좌표 → Mapfile 상대좌표(해당 PCHK = (0,0)).

    Mapfile **생성**(정답지 → 설비 원문)에 쓴다. 적용 순서는 transform 을 정확히
    거꾸로 밟는다: 모듈별 오프셋 원복 → PCHK 절대좌표 차감 → v_R 은 반시계 90°
    회전((x, y) → (-y, x))으로 설비 세팅 좌표계로 되돌림.
    transform(inverse_transform(p)) == p 가 항상 성립해야 한다 (round-trip 테스트).
    """
    rule = (rules or {}).get((flat, name))
    if rule:
        ex, ey = module_effect(flat, rule[0], rule[1])
        X, Y = X - ex, Y - ey
    x = X - dx - float(flat_correction[0])
    y = Y - dy - float(flat_correction[1])
    return inverse_rotate_flat(flat, x, y)


# ────────────────────────────────────────── 정답지 (Teg_location raw 값)
def load_ref(vehicle: str) -> tuple[dict[str, list[dict]] | None, dict[str, str], str, str]:
    """Teg_location 의 raw ebeam 값 → (ref, top_cell→teg 맵, 경로, 오류문).

    ref = {teg: [{x, y, w, h, dir, top_cell}, ...]}. 설비 원문의 좌표는 배율 적용 전
    값이므로 raw ebeam_x/ebeam_y 와 직접 비교한다. w/h 는 TEG 실물 크기(mm) —
    배율·기본값 적용은 `teg_map.teg_size` 한 곳(map_payload 와 동일)이다. 파일의
    teg_w/teg_h 는 실제 배치 방향 그대로라 vertical 이라고 다시 스왑하지 않는다.
    dir 는 direction 열('h'|'v') 원본으로, Mapfile용 좌표 생성에서 어느 flat 표에
    넣을지 가르는 데 쓴다.
    동명 TEG 가 여러 행이면 모두 후보로 두고 가장 가까운 행과 대조한다.

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
        teg = str(row["teg"]).strip()
        fz = _tm.normalize_direction(row.get("flat_zone"), teg)
        tw, th = _tm.teg_size(row["teg_w"], row["teg_h"], scale, cfg, fz)
        tc0 = str(row.get("top_cell") or "").strip()
        ref.setdefault(teg, []).append(
            {"x": float(row["ebeam_x"]), "y": float(row["ebeam_y"]), "w": tw, "h": th,
             "dir": fz, "top_cell": tc0})
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


def _sum_module_rules(global_rows: list[dict], product_rows: list[dict]) -> dict:
    """Add global and product module calibration for identical flat/name keys."""
    out: dict[tuple[str, str], tuple[float, float, str]] = {}
    for source, rows in (("global", global_rows or []), ("product", product_rows or [])):
        for item in rows:
            key = (str(item.get("flat") or "h"), str(item.get("name") or "").strip())
            if not key[1]:
                continue
            prev = out.get(key, (0.0, 0.0, ""))
            note = " / ".join(x for x in (prev[2], f"{source}: {item.get('note') or ''}".strip()) if x)
            out[key] = (prev[0] + float(item.get("dx") or 0),
                        prev[1] + float(item.get("dy") or 0), note)
    return out


def coordinate_context(check: dict, vehicle: str) -> dict:
    product = ((check.get("products") or {}).get(str(vehicle or "").strip()) or {})
    global_rules = _sum_module_rules(check.get("modules") or [], [])
    product_rules = _sum_module_rules([], product.get("modules") or [])
    rules = _sum_module_rules(check.get("modules") or [], product.get("modules") or [])
    corrections = {f: tuple((product.get("flat_corrections") or {}).get(f, [0.0, 0.0])) for f in FLATS}
    return {"product": product, "rules": rules, "global_rules": global_rules,
            "product_rules": product_rules, "flat_corrections": corrections}


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


_SPLIT_SUFFIX_RE = re.compile(r"^(.+)_(\d+)$")


def resolve_ref_teg_split(t: dict, ref: dict[str, list[dict]] | None,
                           tc_to_teg: dict[str, str] | None,
                           ) -> tuple[str | None, str | None, str | None]:
    """분할 TEG 재매칭 — 이름 뒤의 _1, _2 등 분할 번호를 제거하고 base name 으로 매칭.

    TEG 위치 조회에서 동명 TEG 가 여러 행이면 _1, _2, … 접미사가 자동 부여된다.
    이 접미사를 제거한 원래 이름으로 정답지의 teg/top_cell 완전 일치를 시도한다.
    같은 base name 의 정답지에 여러 후보가 있으면 _compare 가 가장 가까운 것을 대조.
    """
    if not ref:
        return None, None, None
    tc_to_teg = tc_to_teg or {}
    for tok in _name_tokens(t):
        m = _SPLIT_SUFFIX_RE.match(tok)
        if m:
            base = m.group(1)
            if base in ref:
                return base, "teg", tok
            teg = tc_to_teg.get(base)
            if teg is not None:
                return teg, "top_cell", tok
    return None, None, None


_PREFIX_NAME_RE = re.compile(r"^([A-Za-z])_(.+)$")


def resolve_ref_teg_reorder(t: dict, ref: dict[str, list[dict]] | None,
                             tc_to_teg: dict[str, str] | None,
                             ) -> tuple[str | None, str | None, str | None]:
    """확장체크 — 접두사_이름 → 이름접두사01 변환 재매칭.

    H_AAA01 형태의 이름을 AAA01H01 로 변환해 정답지 매칭을 시도한다.
    패턴: {prefix}_{name} → {name}{prefix}01 (prefix=H, V 등 1글자).
    예: H_AAA01 → AAA01H01, V_BBB02 → BBB02V01.
    """
    if not ref:
        return None, None, None
    tc_to_teg = tc_to_teg or {}
    for tok in _name_tokens(t):
        m = _PREFIX_NAME_RE.match(tok)
        if m:
            prefix, rest = m.group(1), m.group(2)
            reordered = f"{rest}{prefix}01"
            if reordered in ref:
                return reordered, "teg", tok
            teg = tc_to_teg.get(reordered)
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


def _die_relation(cells: list[dict], x0: float, y0: float, w: float, h: float,
                  eps: float = 1e-9) -> tuple[dict | None, list[dict]]:
    """TEG 사각형 ↔ die 셀 관계 → (완전히 품은 셀, 조금이라도 겹친 셀들).

    좌표 규약은 _overlaps_chip 과 같다 (mm, 좌표 = 셀/TEG 좌하단).
    '걸침'(die 경계를 물고 있음) = 겹친 셀은 있는데 어느 셀에도 완전히 들어가지
    않은 상태 — inside 가 None 이고 touched 가 비지 않은 경우다.
    """
    tx1, ty1 = x0 + w, y0 + h
    inside: dict | None = None
    touched: list[dict] = []
    for c in cells:
        cx1, cy1 = c["x"] + c["w"], c["y"] + c["h"]
        if not (x0 < cx1 - eps and tx1 > c["x"] + eps
                and y0 < cy1 - eps and ty1 > c["y"] + eps):
            continue
        touched.append(c)
        if (inside is None and x0 >= c["x"] - eps and tx1 <= cx1 + eps
                and y0 >= c["y"] - eps and ty1 <= cy1 + eps):
            inside = c
    return inside, touched


DIE_IN, DIE_NEAR, DIE_OUT = "in", "near", "out"


def _die_sep(c: dict, x0: float, y0: float, x1: float, y1: float) -> float:
    """TEG 사각형 ↔ die 셀 한 개의 분리거리.

    · > 0 : 떨어져 있다 (그만큼의 간격, 축별 최대 = Chebyshev 근사)
    · < 0 : 겹쳐 있다 (|값| = 밖으로 빼내는 데 필요한 **최소 이동량**)
    얇은 TEG 가 큰 die 한가운데 있어도 최소 이동량은 크게 나오므로,
    '살짝 걸침' 과 '완전히 박힘' 을 이 하나로 가른다.
    """
    cx1, cy1 = c["x"] + c["w"], c["y"] + c["h"]
    gx = max(c["x"] - x1, x0 - cx1)
    gy = max(c["y"] - y1, y0 - cy1)
    if gx < 0 and gy < 0:
        return -min(x1 - c["x"], cx1 - x0, y1 - c["y"], cy1 - y0)
    return max(gx, gy)


def die_proximity(cells: list[dict], x0: float, y0: float, w: float, h: float,
                  tol: float = 0.0) -> tuple[str, list[dict]]:
    """TEG ↔ die 관계를 허용오차 tol 로 3 단계로 가른다 → (상태, 관련 셀들).

    경계에서 tol 만큼은 들어가나 나가나 '경계 근처' 로 본다 — 설비 세팅의 소수점
    차이로 살짝 물린 것까지 침범으로 잡으면 판정이 너무 타이트하다
    (2026-07-29 사용자 요청).
      · sep < -tol      → in   (tol 보다 깊이 박힘 — 진짜 침범)
      · -tol ≤ sep ≤ tol → near (경계 ±tol — 확인필요)
      · sep > tol       → out  (확실히 밖 — 문제없음)
    가장 강한 관계를 돌려준다 (in > near > out). 두 번째 값은 그 상태를 만든 셀들.
    """
    x1, y1 = x0 + w, y0 + h
    near: list[dict] = []
    for c in cells:
        sep = _die_sep(c, x0, y0, x1, y1)
        if sep < -tol:
            return DIE_IN, [c]
        if sep <= tol:
            near.append(c)
    return (DIE_NEAR, near) if near else (DIE_OUT, [])


def _die_names(cells: list[dict]) -> str:
    return ", ".join(dict.fromkeys(str(c.get("name") or "?") for c in cells))


# ────────────────────────────────────────── 신호등 판정
# 화면(TegCheck.jsx)이 색을 다시 계산하지 않도록 행마다 light 를 붙여 내보낸다.
#   red    — 고쳐야 함
#   yellow — 확인 필요 (정답지로 정밀 대조는 못 하지만 자리는 맞음 / 작은 오차)
#   purple — 이름 변환 규칙으로 매칭 (위치가 아닌 이름 검증)
#   green  — 정상
#   gray   — 판정 불가 (정답지 미등록 · die 정보 없음)
def row_light(row: dict) -> tuple[str, str]:
    """정답지 대조 행의 신호등 → (light, 사유).

    정답지(Teg_location)에 있는 TEG 는 **좌표와 자리를 따로 본다**. 둘 다 문제면
    사유에 둘 다 적는다 ("불일치 + die 침범") — 하나만 보고 고치면 나머지가 남는다
    (2026-07-29 사용자 요청).
      · 좌표 — ΔX·ΔY 가 WARN_TOL 초과면 불일치(red), 이내면 확인필요(yellow)
      · 자리 — die 에 깊이 박혔으면 die 침범(red), 경계 ±die_tol 이면 경계 근처(yellow)
    TEG 는 칩 사이 스크라이브에 있어야 하므로 좌표가 맞아 보여도 die 안이면 틀린 것이다.
    """
    st = row.get("status")
    die = row.get("die_state")
    registered = bool(row.get("ref_teg"))
    red: list[str] = []
    yellow: list[str] = []
    if st == "mismatch":
        red.append("불일치")
    # A Mapfile-only module has no reference coordinate to compare, but its
    # calculated rectangle can still be proven to sit inside a die.  Treat
    # that independently verifiable placement problem as red as well.
    if die == DIE_IN:
        red.append("die 침범")
    if st == "warning":
        yellow.append("확인필요")
    if registered and die == DIE_NEAR:
        yellow.append("die 경계 근처")
    if red:
        return "red", " + ".join(red + yellow)
    if yellow:
        return "yellow", " + ".join(yellow)
    if st == "extended":
        return "purple", "확장체크"
    if st == "match":
        return "green", "위치 확인"
    return "gray", "정답지 미등록" if st == "missing" else ""


def main_die_light(cells: list[dict], group: str,
                   x0: float, y0: float, w: float, h: float,
                   tol: float = 0.0) -> tuple[str, str]:
    """MAIN 내부 TEG(정답지 미등록)의 신호등 — 자기 MAIN die 에 있어야 정상.

    정답지에 없어 좌표를 정밀 대조할 수는 없지만, 이름의 MAINxx 가 어느 die 인지
    말해 주므로 "그 die 에 걸쳐 있는가"는 판정할 수 있다 (기본 TEG 크기 기준):
      · 자기 MAINxx die 안 / 그 경계 ±tol → yellow (정밀 대조는 불가)
      · 다른 MAIN die 안·경계 / 어느 die 에도 안 닿음 → red
      · 그 이름의 die 셀 자체가 없으면(크기 미상·격자 모드 등) → gray (판정 불가)

    자기 die 를 먼저 보므로 셀 순서에 판정이 흔들리지 않는다.
    """
    key = _tm.normalize_chip_name(group)
    own: list[dict] = []
    other: list[dict] = []
    for c in cells:
        if not c.get("name"):
            continue
        (own if _tm.normalize_chip_name(c["name"]) == key else other).append(c)
    if not key or not own:
        return "gray", f"{group} die 없음 — 판정 불가"
    own_state, _own_cells = die_proximity(own, x0, y0, w, h, tol)
    if own_state == DIE_IN:
        return "yellow", f"{group} die 안"
    if own_state == DIE_NEAR:
        return "yellow", f"{group} die 경계 근처"
    other_state, hit = die_proximity(other, x0, y0, w, h, tol)
    if other_state == DIE_IN:
        return "red", f"다른 die({_die_names(hit)}) 안"
    if other_state == DIE_NEAR:
        return "red", f"다른 die({_die_names(hit)}) 경계 근처"
    return "red", f"{group} die 밖"


def _shot_info(vehicle: str, extra_anchors: list[dict] | None = None) -> dict:
    """shot 크기·die 셀 정보 — 확대 뷰 렌더·die 겹침 검사용.

    die 셀은 shot 표시 방식에서 나온다. 어느 쪽이든 셀 규약(mm, 좌하단 기준)이 같아서
    아래 _overlaps_chip 판정 코드는 하나를 공유한다.
      · grid     — ⚙️ 설정의 칩 개수/크기/간격으로 계산한 격자
      · image    — 붙여넣은 그림에서 인식한 사각형 (그림과 겹쳐 보이는 격자)
      · dev_grid — MAIN TEG 좌표(die 좌하단) + Main_chip_info 의 chip 크기
    checked=True 는 'shot 크기 fit 성공 + 판정할 die 셀을 얻었을 때'만.

    개발 격자(dev_grid)의 앵커는 Teg_location 의 MAIN 이 1순위, 없으면 검사 중인
    Mapfile 원문의 MAIN 행(extra_anchors)이다. **크기는 Main_chip_info.csv 에만
    의존한다** — 그 제품
    행이 없으면 die 를 그리지도, 겹침을 판정하지도 않는다.
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
    mode = display.get("mode", "none")
    out.update({"available": True, "shot_w_mm": W, "shot_h_mm": H,
                "mode": mode, "cells": [], "cell_source": ""})
    if mode == "grid":
        out["cells"] = _chip_cells(display, W, H)
        out["cell_source"] = "grid"
        out["checked"] = True
    elif mode in ("image", "dev_grid"):
        # die 를 못 얻으면 checked=False 로 두고 조용히 검사만 뺀다 — 근거 없는
        # 사각형으로 정상 TEG 를 'die 안' 이라고 보고하지 않는다.
        out["cell_source"] = mode
        path = _tm.image_path(vehicle)
        anchors = _tm.main_anchors(p) or _sized_anchors(vehicle, extra_anchors)
        det = _teg_shape.shot_cells_detail(path or "", W, H, anchors,
                                           dev_grid=(mode == "dev_grid"))
        out["shape_reason"] = "no_image" if path is None else det["reason"]
        out["shape_source"] = det["source"]
        out["image_count"] = det["image_count"]
        out["align"] = det["align"]
        if det["cells"]:
            out["cells"] = det["cells"]
            out["checked"] = True
    return out


def _sized_anchors(vehicle: str, anchors: list[dict] | None) -> list[dict]:
    """Mapfile 원문에서 뽑은 MAIN 앵커에 Main_chip_info 크기를 붙인다."""
    out = []
    try:
        chips = _tm.load_main_chips()[0]
    except Exception:
        chips = {}
    for a in (anchors or []):
        a = dict(a)
        size = _tm.chip_size_for(vehicle, a.get("name") or "", chips)
        if size:
            a["w"], a["h"] = size
        out.append(a)
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
    merged_custom = merge_custom_markers(custom_markers, chk.get("custom_markers"))
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
    coord_ctx = coordinate_context(chk, veh)
    rules = coord_ctx["rules"]

    def _transform_detail(f: str, target_name: str) -> dict:
        base = pchk_bases.get(f)
        pchk_name = base[2] if base is not None else PCHK_REF_NAMES[f][0]
        # The PCHK row itself is the zero anchor. Product correction describes
        # the target placement relative to that anchor, so it must not move the
        # PCHK away from its own DB reference.
        correction = ((0.0, 0.0) if str(target_name).strip().casefold() == pchk_name.casefold()
                      else tuple(coord_ctx["flat_corrections"].get(f, (0.0, 0.0))))
        return {"pchk_name": pchk_name, "flat_correction": correction}

    # shot 크기·칩 격자 — 칩 겹침 검사(TEG 는 칩 사이 스크라이브에 있어야 정상)
    scale = float(cfg["ebeam_scale"])
    # die 겹침 허용오차: 설정값은 ebeam raw 단위(ΔX/ΔY 와 같은 공간) → mm 로 환산
    die_tol_mm = max(0.0, float(chk.get("die_tol", 0.0))) * scale

    # ── MAIN 행의 꼬리표에서 내부 TEG 이름 뽑기 (없으면 "" = MAIN 블록 자체 행)
    marker_upper = {mk.upper() for mk in marker_map}

    def _main_detail(t: dict) -> str:
        for tok in [tok.strip() for tok in t["tail"].split(",")][1:] if t["tail"] else []:
            if not tok or tok == t["name"] or tok.upper() in marker_upper:
                continue
            return tok
        return ""

    def _main_xy(t: dict, name: str) -> tuple[float, float]:
        t_flat, _mk = teg_flat(t["tail"], marker_map)
        used_t = forced or t_flat or detected or "h"
        tdx, tdy = _offset(used_t)
        detail = _transform_detail(used_t, name)
        return transform(name, t["x"], t["y"], used_t, tdx, tdy, rules,
                         flat_correction=detail["flat_correction"])

    # 그림 모드 die 사각형의 위치 기준 — MAIN 은 die 급 블록이고 이름 토큰이 없는
    # 행(꼬리표에 그룹 이름뿐)이 그 블록 자체다. 그 좌표가 die 좌하단이므로 그림에서
    # 인식한 사각형을 여기에 맞춰 놓는다. Teg_location 에 MAIN 이 있으면 그쪽 우선.
    main_anchors = []
    for t in tegs_all:
        if not is_main(t["name"]) or _main_detail(t):
            continue
        ax, ay = _main_xy(t, t["name"])
        main_anchors.append({"name": t["name"], "x": ax * scale, "y": ay * scale})
    shot = (_shot_info(veh, main_anchors) if veh
            else {"available": False, "checked": False})

    rows = []
    summary = {"match": 0, "warning": 0, "mismatch": 0, "extended": 0, "missing": 0,
               "total": len(tegs), "chip_overlap": 0, "die_in": 0, "die_near": 0,
               "light": {"red": 0, "yellow": 0, "purple": 0, "green": 0, "gray": 0}}
    for t in tegs:
        # TEG 별 flat: 강제값 > 자기 꼬리표 마커 > 전역 기본. 오프셋도 flat 에 맞춰 선택.
        t_flat, t_marker = teg_flat(t["tail"], marker_map)
        used_t = forced or t_flat or detected or "h"
        tdx, tdy = _offset(used_t)
        # 순서 기반이 아니라 이름 기반 정확 매칭 — 후보 토큰 중 정답지 teg/top_cell 과
        # 완전 일치하는 걸 찾아 그 teg 로 대조. 없으면 '01' 제외 재매칭(확장체크).
        ref_teg, msrc, mtok = resolve_ref_teg(t, ref, tc_to_teg)
        extended = False
        match_rule = "exact" if ref_teg is not None else None
        if ref_teg is None and ref is not None:
            ref_teg, msrc, mtok = resolve_ref_teg_extended(t, ref, tc_to_teg)
            if ref_teg is not None:
                extended = True
                match_rule = "01strip"
        # 접두사_이름 → 이름접두사01 변환 재매칭 (H_AAA01 → AAA01H01)
        if ref_teg is None and ref is not None:
            ref_teg, msrc, mtok = resolve_ref_teg_reorder(t, ref, tc_to_teg)
            if ref_teg is not None:
                extended = True
                match_rule = "reorder"
        # 분할 TEG 재매칭 — _1, _2 등 접미사 제거 후 base name 으로 정답지 검색
        if ref_teg is None and ref is not None:
            ref_teg, msrc, mtok = resolve_ref_teg_split(t, ref, tc_to_teg)
            if ref_teg is not None:
                match_rule = "split"
            # 같은 TEG 의 분할이므로 위치 비교 정상 수행 (extended 아님)
        detail = _transform_detail(used_t, ref_teg or t["name"])
        nx, ny = transform(t["name"], t["x"], t["y"], used_t, tdx, tdy, rules,
                           flat_correction=detail["flat_correction"])
        cmp_ = _compare(ref, ref_teg, nx, ny, extended=extended)
        if cmp_["status"] in summary:
            summary[cmp_["status"]] += 1
        rule = rules.get((used_t, t["name"]))
        global_rule = coord_ctx["global_rules"].get((used_t, t["name"]))
        product_rule = coord_ctx["product_rules"].get((used_t, t["name"]))
        # 설비 계산값의 실좌표(mm) + TEG 크기(mm) — 정답지에 없으면 기본 크기
        mm_x, mm_y = nx * scale, ny * scale
        tw = cmp_["ref_w"] if cmp_["ref_w"] is not None else float(cfg["teg_default_w"])
        th = cmp_["ref_h"] if cmp_["ref_h"] is not None else float(cfg["teg_default_h"])
        # flat_used 가 v_R 이면 Vertical TEG — 기본 크기 사용 시 가로/세로 swap
        if cmp_["ref_w"] is None and used_t in ("v_R", "v_L"):
            tw, th = th, tw
        overlap = (_overlaps_chip(shot["cells"], mm_x, mm_y, tw, th)
                   if shot.get("checked") else None)
        # die 관계는 허용오차(die_tol)를 둔 3단계 — 경계 ±tol 은 '근처'(확인필요).
        die_state = (die_proximity(shot["cells"], mm_x, mm_y, tw, th, die_tol_mm)[0]
                     if shot.get("checked") else None)
        if overlap:
            summary["chip_overlap"] += 1
        # die 개수는 **신호등에 실제로 반영되는 행만** 센다 — 정답지에 없는 module 은
        # die 안이어도 '미등록'(회색)이라 화면에 안 그려진다. 여기서 같이 세면
        # 요약은 "die 침범 1671" 인데 배치도에는 아무것도 안 나오는 꼴이 된다.
        if die_state == DIE_IN:
            summary["die_in"] += 1
        elif ref_teg and die_state == DIE_NEAR:
            summary["die_near"] += 1
        light, light_reason = row_light({**cmp_, "ref_teg": ref_teg, "die_state": die_state})
        summary["light"][light] = summary["light"].get(light, 0) + 1
        rows.append({
            **t, "calc_x": _num(nx), "calc_y": _num(ny), **cmp_,
            "light": light, "light_reason": light_reason,
            "flat_used": used_t, "flat_marker": t_marker,
            "coordinate_terms": {
                "base": [_num(tdx), _num(tdy)],
                "flat_correction": [_num(detail["flat_correction"][0]), _num(detail["flat_correction"][1])],
                "global_module": [_num(global_rule[0]), _num(global_rule[1])] if global_rule else [0, 0],
                "product_module": [_num(product_rule[0]), _num(product_rule[1])] if product_rule else [0, 0],
            },
            # 이름 기반 매칭 결과 — 대조한 정답지 teg 와 매칭 근거(teg/top_cell/확장 토큰)
            "ref_teg": ref_teg, "match_source": msrc, "match_token": mtok,
            "extended": extended,
            "match_rule": match_rule,
            "mm_x": round(mm_x, 4), "mm_y": round(mm_y, 4),
            "teg_w": round(tw, 4), "teg_h": round(th, 4),
            "chip_overlap": overlap, "die_state": die_state,
            "rule_note": (rule[2] or f"모듈 오프셋 ({_num(rule[0])}, {_num(rule[1])})") if rule else "",
        })

    # ── MAIN 그룹 — MAIN 은 개발제품 die 급의 큰 직사각형 블록이고, Mapfile 에는
    #    내부 TEG 행들이 같은 그룹명으로 나열된다:
    #      module DUMMY1 (x, y) ! MAIN02, ,module_detail, LOT7
    #    그룹 = MAIN 이름(꼬리표 첫 토큰), 내부 TEG 이름 = 그 뒤 첫 유효 토큰
    #    (빈 토큰·그룹명 재등장·flat 마커 제외). 좌표는 일반 행과 동일하게
    #    ebeam 절대좌표로 원복해 Mapfile 체크 결과 안에서만 표시·판정한다.
    main_groups_map: dict[str, list[dict]] = {}
    for t in tegs_all:
        if not is_main(t["name"]):
            continue
        group = t["name"]
        detail = _main_detail(t)
        nx, ny = _main_xy(t, detail or group)
        entry = main_groups_map.setdefault(group, [])
        auto = not detail
        if auto:
            # 이름 토큰이 없는 행은 그룹 이름으로 넘버링한다. 이 그룹에 자동 이름이
            # 하나뿐이면 아래 후처리에서 접미사를 떼어 그냥 그룹 이름이 된다.
            detail = f"{group}_{sum(1 for e in entry if e['_auto']) + 1}"
        names = {e["teg"] for e in entry}
        base, n = detail, 2
        while detail in names:
            detail = f"{base}_{n}"
            n += 1
        # 칩 격자 모드면 내부 TEG 도 칩(die) 겹침 검사 — 일반 행과 동일 규약
        # (기본 TEG 크기 사용 — MAIN 내부 TEG 는 정답지에 없어 크기 정보가 없음)
        mm_x, mm_y = nx * scale, ny * scale
        dw, dh = float(cfg["teg_default_w"]), float(cfg["teg_default_h"])
        overlap = None
        if shot.get("checked"):
            overlap = _overlaps_chip(shot["cells"], mm_x, mm_y, dw, dh)
        # 신호등 — MAIN 내부 TEG 는 정답지에 없으므로 '자기 MAIN die 안인가'로 본다.
        # 블록 자체 행(auto: 이름 토큰 없음)은 die 좌하단 앵커라 판정 대상이 아니다.
        light, light_reason = ("gray", "")
        if not auto and shot.get("checked"):
            light, light_reason = main_die_light(shot["cells"], group, mm_x, mm_y,
                                                 dw, dh, die_tol_mm)
        entry.append({"teg": detail, "x": _num(nx), "y": _num(ny), "chip_overlap": overlap,
                      "mm_x": round(mm_x, 4), "mm_y": round(mm_y, 4),
                      "teg_w": round(dw, 4), "teg_h": round(dh, 4),
                      "light": None if auto else light,
                      "light_reason": "" if auto else light_reason,
                      "_auto": auto})
    # 자동 이름이 그룹에 하나뿐이면 `_1` 을 떼고 그룹 이름 그대로 쓴다 —
    # 넘버링은 구분이 필요한 2 개 이상일 때만 의미가 있다.
    for group, entry in main_groups_map.items():
        autos = [e for e in entry if e["_auto"]]
        if len(autos) == 1:
            taken = {e["teg"] for e in entry if e is not autos[0]}
            name, n = group, 2
            while name in taken:
                name = f"{group}_{n}"
                n += 1
            autos[0]["teg"] = name
        for e in entry:
            e.pop("_auto", None)
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
    if veh and targets["total"] == 0:
        targets["no_targets"] = True
        targets["warning"] = "체크할 TEG가 설정되어 있지 않습니다"

    main_groups = []
    if main_groups_map:
        for g in sorted(main_groups_map):
            items = main_groups_map[g]
            main_groups.append({"group": g, "tegs": items,
                                "chip_overlap": sum(1 for e in items if e.get("chip_overlap")),
                                "red": sum(1 for e in items if e.get("light") == "red"),
                                "yellow": sum(1 for e in items if e.get("light") == "yellow")})

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


# ────────────────────────────────────────── Mapfile용 좌표 생성 (체크의 역방향)
# 정답지(Teg_location)의 ebeam 절대좌표 → 기준 PCHK 이 (0,0) 인 Mapfile 상대좌표.
# 셋업을 처음 올릴 때 설비 Mapfile 과 크로스체크하는 기준표로 쓴다.
FLAT_LABEL = {"h": "Horizontal", "v_R": "Vertical(R)", "v_L": "Vertical(L)"}
DEFAULT_MARKER = {"h": "H_PCHK", "v_R": "V_PCHK", "v_L": "VL_PCHK"}
GEN_FORMATS = ("teg_map", "bracket")
MAX_GEN_ROWS = 2000          # flat 당 생성 행 상한 (원문이 무한정 커지지 않게)
MAX_PREVIEW_CELLS = 2000     # 미리보기 die 셀 상한 (그림 인식이 많이 잡혀도 브라우저 보호)


def _teg_dir_for_flat(flat: str) -> str:
    """flat → Teg_location direction 값."""
    return "v" if flat == "v_R" else "v_L" if flat == "v_L" else "h"


def mapfile_text(vehicle: str, block: dict, fmt: str = "teg_map") -> str:
    """flat 블록 → 설비 원문 형식 문자열.

    **화면은 표만 쓴다** — 좌표 생성 기능은 원문을 내보내지 않는다(사용자 요구).
    이 함수는 생성 좌표가 체크(transform)의 정확한 역방향인지 확인하는
    round-trip 검증용으로 남겨 둔다: 여기서 만든 원문을 그대로 inspect 에
    넣으면 전부 '일치' 여야 한다.

    fmt='teg_map'  : `module <이름> (x, y) ! <이름>, <마커>` (#teg-map 섹션)
    fmt='bracket'  : `<이름> (x, y) ! <이름>, <마커>`        ([MODULES_COORDINATE] 섹션)
    """
    flat = block["flat"]
    marker, pchk_name = block["marker"], block["pchk"]["teg"]
    head = "#teg-map" if fmt != "bracket" else "[MODULES_COORDINATE]"
    pre = "module " if fmt != "bracket" else ""
    out = [f"# flow Mapfile용 좌표 생성 — {vehicle} / {FLAT_LABEL.get(flat, flat)}"
           f" (기준 {pchk_name} = (0, 0))", head,
           f"{pre}{pchk_name} (0, 0) ! {pchk_name}, {marker}"]
    for r in block["rows"]:
        out.append(f"{pre}{r['teg']} ({_num(r['x'])}, {_num(r['y'])}) ! {r['teg']}, {marker}")
    out.append("# end")
    return "\n".join(out)


def _unrotate(flat: str, x: float, y: float) -> tuple[float, float]:
    """Mapfile 좌표 → **실제 배치(ebeam) 방향**의 PCHK 기준 상대좌표.

    v_R 의 표 좌표는 반시계 90° 돌아간 설비 좌표계 값이라, 그림으로 볼 때는
    다시 시계 90° 로 돌려 wafer 가 horizontal 일 때의 배치로 되돌린다
    (transform 의 회전 부분과 같은 식). h 는 그대로.
    """
    return rotate_flat(flat, x, y)


def _gen_rect(flat: str, x: float, y: float, w_mm: float, h_mm: float,
              scale: float) -> dict:
    """미리보기용 TEG 사각형 — **실제 배치 방향**, 좌하단 기준, 원문 단위.

    두 flat 모두 그림은 wafer 가 horizontal 일 때의 배치로 그린다(사용자 요구).
    크기는 정답지(Teg_location) 값 그대로다 — direction=V 인 TEG 는 파일에 이미
    세운 크기로 들어 있어 **서 있는 모양**으로 나온다 (코드가 스왑하지 않는다).
    좌표(x, y)는 Mapfile 표의 값이므로 v_R 이면 회전을 먼저 되돌린다.
    """
    rx, ry = _unrotate(flat, x, y)
    w_raw = w_mm / scale if scale else w_mm
    h_raw = h_mm / scale if scale else h_mm
    return {"x": round(rx, 4), "y": round(ry, 4),
            "w": round(w_raw, 4), "h": round(h_raw, 4),
            "w_mm": round(w_mm, 4), "h_mm": round(h_mm, 4)}


def _origin_rect(x: float, y: float, w_mm: float, h_mm: float, scale: float) -> dict:
    """Rectangle whose x/y are already in the Horizontal-normalised real frame."""
    return {"x": round(x, 4), "y": round(y, 4),
            "w": round((w_mm / scale if scale else w_mm), 4),
            "h": round((h_mm / scale if scale else h_mm), 4),
            "w_mm": round(w_mm, 4), "h_mm": round(h_mm, 4)}


def _shot_frame(flat: str, dx: float, dy: float, scale: float,
                shot_w_mm: float, shot_h_mm: float) -> dict:
    """미리보기 배경 shot — 실제 배치 방향(가로·세로 그대로), PCHK 기준 상대좌표.

    shot 센터는 ebeam (0, 0) 이므로 PCHK 기준으로는 (-dx, -dy) 다. v_R 도 그림은
    돌리지 않으므로 h 와 같은 식을 쓴다.
    """
    return {"cx": round(-dx, 4),
            "cy": round(-dy, 4),
            "w": round((shot_w_mm / scale if scale else shot_w_mm), 4),
            "h": round((shot_h_mm / scale if scale else shot_h_mm), 4),
            "w_mm": round(shot_w_mm, 4), "h_mm": round(shot_h_mm, 4)}


def build_mapfile(vehicle: str, include_all: bool = False,
                  custom_markers: dict | None = None) -> dict:
    """Mapfile 체크 대상 TEG → flat(Horizontal/Vertical(R)) 별 Mapfile 좌표표.

    Mapfile 체크의 정확한 역방향이다. 체크가 '원문 → 정답지' 라면 이쪽은
    '정답지 → 원문' 으로, 셋업을 처음 올릴 때 설비 Mapfile 과 크로스체크할
    기준표를 만든다.

    · 기준점은 그 flat 의 PCHK 정답지 ebeam 좌표 (없으면 ⚙️ 설정 flat_offsets).
      결과 좌표는 그 PCHK 이 (0,0) 인 상대좌표다.
    · TEG(module)별 오프셋도 체크와 같은 규약으로 되돌려 반영하고, 행마다
      적용 여부(offset_applied)와 값을 함께 낸다.
    · 표는 flat 별로 따로다. Mapfile 은 flat 하나 기준이라 direction 이 다른
      TEG 는 그 표의 대상이 아니다 (include_all=True 면 전부 넣는다).
    · 결과는 **표**다. 설비 원문 문자열은 내보내지 않는다 (mapfile_text 는
      round-trip 검증용 헬퍼로만 남아 있다).
    · 행의 rect / 블록의 shot 은 미리보기 전용이며 **실제 배치 방향**(wafer 가
      horizontal 일 때)이다 — 표 좌표는 회전된 설비값이라도 그림은 돌리지 않고,
      direction=V 인 TEG 만 서 있는 모양으로 나온다.

    반환: {ok, vehicle, ref_ok, ref_error, ref_path, scale, targets:{...},
           flats: [{flat, label, marker, base, pchk, rows, skipped, other_dir,
                    warning, shot}]}
    """
    veh = str(vehicle or "").strip()
    ref, tc_to_teg, ref_path, ref_err = (
        load_ref(veh) if veh else (None, {}, "", "제품명(vehicle)이 비어 있습니다"))
    cfg = _tm.load_cfg()
    chk = cfg["check"]
    scale = float(cfg["ebeam_scale"]) or 1.0
    merged_custom = merge_custom_markers(custom_markers, chk.get("custom_markers"))
    pchk_bases = pchk_base_offsets(ref, merged_custom)
    coord_ctx = coordinate_context(chk, veh)
    rules = coord_ctx["rules"]
    opts = _tm.teg_target_options(veh) if veh else {"targets": [], "source": "default",
                                                    "tegs": []}
    # shot 크기 + die 셀 — 미리보기 배경용. Mapfile 체크의 shot 확대와 **같은 출처**
    # (⚙️ 설정의 표시 방식: 칩 격자 / 그림에서 인식한 사각형 / 개발 격자)를 쓴다.
    # 없으면 배경 없이 TEG 만 그린다.
    shot_w = shot_h = 0.0
    shot_cells: list[dict] = []
    cell_source = ""
    display = {"mode": "none", "has_image": False}
    try:
        info = _shot_info(veh) if veh else {"available": False}
        if info.get("available"):
            shot_w, shot_h = float(info["shot_w_mm"]), float(info["shot_h_mm"])
            shot_cells = list(info.get("cells") or [])[:MAX_PREVIEW_CELLS]
            cell_source = info.get("cell_source") or ""
            # 그림 모드면 미리보기에 그림 자체도 깔 수 있게 알려 준다 (프론트가
            # /image 로 받아 shot 사각형에 맞춰 그린다 — 미리보기는 회전이 없어 1:1).
            display = {"mode": info.get("mode") or "none",
                       "has_image": _tm.image_path(veh) is not None}
    except Exception:
        pass

    known_markers = {k.upper() for k in build_marker_map(merged_custom)}
    flats = []
    for flat in FLATS:
        base = pchk_bases.get(flat)
        if base is not None:
            dx, dy, pchk_name = base[0], base[1], base[2]
            src, warning = "db", ""
        else:
            ox, oy = chk["flat_offsets"].get(flat, [0.0, 0.0])
            dx, dy, pchk_name = float(ox), float(oy), DEFAULT_MARKER[flat]
            src = "config"
            warning = (f"정답지에 {'/'.join(PCHK_REF_NAMES[flat])} 이(가) 없어 "
                       "⚙️ 설정의 기본 오프셋을 기준점으로 씁니다 — "
                       "PCHK 행을 정답지에 넣어야 정확합니다")
        # 꼬리표에 적을 flat 마커 — 기준 이름이 마커로 인식되는 표기면 그대로,
        # 아니면 내장 표기로 적어 생성물이 다시 '검사' 에서 flat 판정되게 한다.
        marker = pchk_name if pchk_name.upper() in known_markers else DEFAULT_MARKER[flat]
        pchk_ref = (ref or {}).get(pchk_name) or []
        flat_correction = tuple(coord_ctx["flat_corrections"].get(flat, (0.0, 0.0)))
        pchk = None
        if pchk_ref:
            c = pchk_ref[0]
            pchk = {"teg": pchk_name, "ebeam_x": _num(c["x"]), "ebeam_y": _num(c["y"]),
                    "x": 0, "y": 0, "direction": c.get("dir", "h"),
                    "rect": _origin_rect(0, 0, c["w"], c["h"], scale),
                    "first_pad_point": {"x": 0.0, "y": 0.0}}
        else:
            pchk = {"teg": pchk_name, "ebeam_x": _num(dx), "ebeam_y": _num(dy),
                    "x": 0, "y": 0, "direction": _teg_dir_for_flat(flat), "rect": None}

        want = _teg_dir_for_flat(flat)
        rows: list[dict] = []
        skipped: list[dict] = []
        other_dir: list[dict] = []
        for teg in opts["targets"]:
            cands = (ref or {}).get(teg)
            if not cands:
                skipped.append({"teg": teg, "reason": "정답지(Teg_location)에 없음"})
                continue
            if teg == pchk_name:
                continue                     # 기준 PCHK 자신 — (0,0) 행으로 이미 나감
            n = len(cands)
            for i, c in enumerate(cands):
                tdir = c.get("dir", "h")
                if tdir != want and not include_all:
                    other_dir.append({"teg": teg, "direction": tdir})
                    break
                # 동명 TEG 가 여러 행이면 _1, _2 … (하나면 접미사 없음 — 이름 규약)
                name = teg if n == 1 else f"{teg}_{i + 1}"
                x, y = inverse_transform(teg, c["x"], c["y"], flat, dx, dy, rules,
                                         flat_correction=flat_correction)
                rule = rules.get((flat, teg))
                global_rule = coord_ctx["global_rules"].get((flat, teg))
                product_rule = coord_ctx["product_rules"].get((flat, teg))
                rows.append({
                    "teg": name, "base_teg": teg, "top_cell": c.get("top_cell", ""),
                    "direction": tdir,
                    "ebeam_x": _num(c["x"]), "ebeam_y": _num(c["y"]),
                    "x": _num(round(x, 4)), "y": _num(round(y, 4)),
                    "offset_applied": bool(rule),
                    "offset_dx": _num(rule[0]) if rule else None,
                    "offset_dy": _num(rule[1]) if rule else None,
                    "offset_note": (rule[2] if rule else ""),
                    "coordinate_terms": {
                        "global_base": [_num(dx), _num(dy)],
                        "product_flat": [_num(flat_correction[0]), _num(flat_correction[1])],
                        "global_module": [_num(global_rule[0]), _num(global_rule[1])] if global_rule else [0, 0],
                        "product_module": [_num(product_rule[0]), _num(product_rule[1])] if product_rule else [0, 0],
                    },
                    "rect": _origin_rect(c["x"] - dx, c["y"] - dy, c["w"], c["h"], scale),
                    "first_pad_point": {
                        "x": _num(c["x"] - dx),
                        "y": _num(c["y"] - dy),
                    },
                })
                if len(rows) >= MAX_GEN_ROWS:
                    break
            if len(rows) >= MAX_GEN_ROWS:
                skipped.append({"teg": "…", "reason": f"생성 상한 {MAX_GEN_ROWS}행 초과"})
                break
        shot = (_shot_frame(flat, dx, dy, scale, shot_w, shot_h)
                if (shot_w and shot_h) else None)
        # die 셀도 PCHK 기준 상대좌표(원문 단위)로 옮긴다 — 셀은 shot 센터 기준 mm,
        # shot 센터의 PCHK 기준 좌표가 (-dx, -dy) 이므로 mm/scale 에서 base 를 뺀다.
        # 그림도 미리보기는 회전하지 않으므로 flat 에 상관없이 같은 식이다.
        cells = [{"x": round(c["x"] / scale - dx, 4),
                  "y": round(c["y"] / scale - dy, 4),
                  "w": round(c["w"] / scale, 4), "h": round(c["h"] / scale, 4)}
                 for c in shot_cells] if (shot and scale) else []
        # 기준 PCHK 은 shot 안에 있어야 정상이다 (설비가 그 자리를 찍는 점이므로).
        # 밖이면 정답지 ebeam 좌표나 shot 크기(Chip_Radius fit)가 잘못된 것이라
        # 생성 좌표 전체가 그만큼 밀린다 — 셋업 크로스체크에서 먼저 잡아야 한다.
        pchk_in_shot = None
        if shot:
            pchk_in_shot = (abs(0 - shot["cx"]) <= shot["w"] / 2
                            and abs(0 - shot["cy"]) <= shot["h"] / 2)
        flats.append({
            "flat": flat, "label": FLAT_LABEL[flat], "marker": marker,
            "base": {"dx": _num(dx), "dy": _num(dy), "ref_name": pchk_name, "source": src},
            "coordinate_terms": {
                "global_base": [_num(dx), _num(dy)],
                "product_flat": [_num(flat_correction[0]), _num(flat_correction[1])],
            },
            "pchk": pchk, "rows": rows, "skipped": skipped, "other_dir": other_dir,
            "offset_count": sum(1 for r in rows if r["offset_applied"]),
            "warning": warning,
            "shot": shot, "pchk_in_shot": pchk_in_shot,
            "cells": cells, "cell_source": cell_source,
        })

    return {
        "ok": True, "vehicle": veh,
        "ref_ok": ref is not None, "ref_error": ref_err, "ref_path": ref_path,
        "scale": scale,
        "targets": {"source": opts.get("source", "default"),
                    "total": len(opts.get("targets") or [])},
        "display": display,
        "flats": flats,
    }


# ────────────────────────────────────────── MAIN 내부 TEG 격자 생성
# MAIN(die 급 블록) 안에도 TEG 가 들어간다. 그 TEG 들은 정답지(Teg_location)에
# 없으므로 좌표를 만들어 줄 근거가 없다 — 대신 **die 를 기본 TEG 사이즈로 격자
# 분할**해 자리를 만들고, 사용자가 칸마다 이름을 적으면 그 자리가 Mapfile
# 상대좌표(기준 PCHK = (0,0))로 나온다.
#
#   · 격자 앵커 = die 좌하단 (MAIN TEG 좌표 규약과 같다)
#   · 한 칸 = 기본 TEG 사이즈(⚙️ 설정 teg_default_w/h). 칸 사이 거리(gap)를 주면
#     pitch = 기본 사이즈 + gap 으로 벌어진다 — die 크기가 기본 사이즈로 딱
#     떨어지지 않을 때 gap 을 조절해 맞춘다 (남는 길이는 remainder 로 알려 준다).
#   · 칸 좌표는 **Horizontal 기준만** 낸다 — MAIN die 는 Vertical(R) 표의 대상이
#     아니다(사용자 확인, 2026-07-29).
#   · TEG(module)별 오프셋은 적용하지 않는다 — 이름을 나중에 붙이는 자리라
#     (flat, name) 규칙을 미리 고를 수 없다.
MAX_GRID_CELLS = 1000        # MAIN 하나당 격자 칸 상한 (화면·응답 보호)
GRID_FLATS = ("h",)          # MAIN 격자는 Horizontal 기준만 낸다


def _grid_count(span: float, size: float, gap: float) -> int:
    """길이 span 에 size 짜리 칸이 gap 간격으로 몇 개 들어가는가 (n*size + (n-1)*gap ≤ span)."""
    pitch = size + gap
    if size <= 0 or pitch <= 0 or span <= 0:
        return 0
    return max(0, int((span + gap) / pitch + 1e-9))


def _main_anchor_map(vehicle: str) -> dict[str, dict]:
    """MAIN 이름 → die 앵커 {name, x, y, w, h} (mm, 좌표 = die 좌하단).

    출처는 위치 조회 payload 의 Teg_location MAIN TEG 다.
    크기는 Main_chip_info.csv 에서만 온다 — 크기가 없으면 격자를 만들 수 없다.
    """
    try:
        payload = _tm.map_payload(vehicle) if vehicle else {}
    except Exception:
        payload = {}
    out: dict[str, dict] = {}
    for a in _tm.main_anchors(payload):
        name = str(a.get("name") or "").strip()
        if name and name not in out:
            out[name] = a
    return out


def build_main_grid(vehicle: str, mains: list[str] | None = None,
                    gap_x: float = 0.0, gap_y: float = 0.0) -> dict:
    """MAIN die → 기본 TEG 사이즈 격자 + 칸별 Mapfile 상대좌표.

    mains 가 비면 격자 없이 선택 가능한 MAIN 목록(available)만 돌려준다.
    이름 매칭은 완전 일치 → normalize_chip_name(`MAIN01` ↔ `MAIN_M01`) 순이다.

    반환: {ok, vehicle, scale, teg, gap, available, flats, mains: [...]}
      mains[i] = {name, found, error?, x, y, w, h, cols, rows, pitch_x, pitch_y,
                  remainder_x, remainder_y, exact, truncated, cells}
      cells[j] = {r, c, x, y(=DB Ebeam raw), mm_x, mm_y, h:{x,y}}
    """
    veh = str(vehicle or "").strip()
    ref, _tc_map, ref_path, ref_err = (
        load_ref(veh) if veh else (None, {}, "", "제품명(vehicle)이 비어 있습니다"))
    cfg = _tm.load_cfg()
    chk = cfg["check"]
    scale = float(cfg["ebeam_scale"]) or 1.0
    tw, th = float(cfg["teg_default_w"]), float(cfg["teg_default_h"])
    gx, gy = max(0.0, float(gap_x or 0.0)), max(0.0, float(gap_y or 0.0))

    merged_custom = merge_custom_markers(None, chk.get("custom_markers"))
    pchk_bases = pchk_base_offsets(ref, merged_custom)
    bases: dict[str, dict] = {}
    for flat in GRID_FLATS:
        b = pchk_bases.get(flat)
        if b is not None:
            bases[flat] = {"dx": float(b[0]), "dy": float(b[1]),
                           "ref_name": b[2], "source": "db"}
        else:
            ox, oy = chk["flat_offsets"].get(flat, [0.0, 0.0])
            bases[flat] = {"dx": float(ox), "dy": float(oy),
                           "ref_name": DEFAULT_MARKER[flat], "source": "config"}

    anchors = _main_anchor_map(veh)
    available = [{"name": n, "w": a.get("w"), "h": a.get("h"),
                  "sized": bool(a.get("w") and a.get("h"))}
                 for n, a in anchors.items()]
    by_key = {}
    for n, a in anchors.items():
        by_key.setdefault(_tm.normalize_chip_name(n), (n, a))

    out_mains: list[dict] = []
    for raw in (mains or []):
        name = str(raw or "").strip()
        if not name:
            continue
        hit = anchors.get(name)
        found_name = name
        if hit is None:
            k = by_key.get(_tm.normalize_chip_name(name))
            if k:
                found_name, hit = k
        if hit is None:
            out_mains.append({"name": name, "found": False,
                              "error": "MAIN 좌표를 찾지 못했습니다 — Teg_location 에 "
                                       "그 이름의 MAIN TEG 가 있어야 합니다"})
            continue
        w, h = float(hit.get("w") or 0), float(hit.get("h") or 0)
        if w <= 0 or h <= 0:
            out_mains.append({"name": found_name, "found": False,
                              "error": "die 크기를 모릅니다 — Main_chip_info.csv 에 "
                                       f"'{found_name}' chip 크기를 넣어야 격자를 만듭니다"})
            continue
        x0, y0 = float(hit["x"]), float(hit["y"])
        cols, rows = _grid_count(w, tw, gx), _grid_count(h, th, gy)
        px, py = tw + gx, th + gy
        rem_x = round(w - (cols * tw + max(0, cols - 1) * gx), 6) if cols else round(w, 6)
        rem_y = round(h - (rows * th + max(0, rows - 1) * gy), 6) if rows else round(h, 6)
        truncated = cols * rows > MAX_GRID_CELLS
        if truncated and cols > 0:
            rows = max(1, MAX_GRID_CELLS // cols)
        cells: list[dict] = []
        for r in range(rows):
            for c in range(cols):
                mmx, mmy = x0 + c * px, y0 + r * py
                rx, ry = mmx / scale, mmy / scale
                cell = {"r": r, "c": c,
                        "mm_x": round(mmx, 4), "mm_y": round(mmy, 4),
                        "x": _num(round(rx, 4)), "y": _num(round(ry, 4))}
                for flat in GRID_FLATS:
                    b = bases[flat]
                    fx, fy = inverse_transform("", rx, ry, flat, b["dx"], b["dy"])
                    cell[flat] = {"x": _num(round(fx, 4)), "y": _num(round(fy, 4))}
                cells.append(cell)
        out_mains.append({
            "name": found_name, "found": True,
            "x": round(x0, 4), "y": round(y0, 4), "w": round(w, 4), "h": round(h, 4),
            "cols": cols, "rows": rows,
            "pitch_x": round(px, 4), "pitch_y": round(py, 4),
            "remainder_x": rem_x, "remainder_y": rem_y,
            "exact": abs(rem_x) < 1e-6 and abs(rem_y) < 1e-6,
            "truncated": truncated, "cells": cells,
        })

    return {
        "ok": True, "vehicle": veh,
        "ref_ok": ref is not None, "ref_error": ref_err, "ref_path": ref_path,
        "scale": scale,
        "teg": {"w": round(tw, 4), "h": round(th, 4)},
        "gap": {"x": round(gx, 4), "y": round(gy, 4)},
        "available": available,
        "flats": bases,
        "mains": out_mains,
    }
