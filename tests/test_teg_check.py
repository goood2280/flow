# -*- coding: utf-8 -*-
"""TEG 설비값 검사 (core/teg_check) — 원문 파싱·flat 변환·정답지 대조 검증."""
import pytest

from core import teg_check, teg_map

SAMPLE = """\
1 #wafer-map MAP1
2 !
3 --ttt--
4 -ttttt-
5 ttttttt
6 -ttttt-
7 --ttt--
8 !
9 <SITES>
10 Pattern, P1
11 1 4 3
12 2 2 2
13 Pattern, P2
14 1 1 1
15 # end
16 #teg-map
17 module m1 (100, 200) ! TEG_A, H_PCHK
18 module m2 (-30, 40) ! TEG_B
19 # end
"""


def test_strip_line_numbers_and_fallback():
    lines = teg_check.strip_line_numbers("1 foo\n2 bar")
    assert lines == ["foo", "bar"]
    # 행번호 없는 원문은 그대로 사용
    assert teg_check.strip_line_numbers("#teg-map\nmodule a (1, 2)") == [
        "#teg-map", "module a (1, 2)"]
    # 행번호 없는 원문에 site 행("1 5 4")이 섞여도 행번호로 오인하지 않는다
    raw = "<SITES>\nPattern, P1\n1 5 4\n2 4 3\n# end"
    assert teg_check.strip_line_numbers(raw) == raw.splitlines()
    sites = teg_check.parse_sites(teg_check.strip_line_numbers(raw))
    assert sites[0]["points"] == [{"pt": 1, "x": 5, "y": 4}, {"pt": 2, "x": 4, "y": 3}]


def test_parse_sample():
    lines = teg_check.strip_line_numbers(SAMPLE)
    maps = teg_check.parse_wafer_maps(lines)
    assert len(maps) == 1
    assert maps[0]["name"] == "MAP1"
    assert maps[0]["w"] == 7 and maps[0]["h"] == 5
    assert maps[0]["rows"][2] == "ttttttt"

    patterns = teg_check.parse_sites(lines)
    assert [p["name"] for p in patterns] == ["P1", "P2"]
    assert patterns[0]["points"] == [{"pt": 1, "x": 4, "y": 3}, {"pt": 2, "x": 2, "y": 2}]

    tegs = teg_check.parse_teg(lines)
    assert [(t["name"], t["x"], t["y"]) for t in tegs] == [
        ("TEG_A", 100, 200), ("TEG_B", -30, 40)]
    flat, why = teg_check.detect_flat(tegs)
    assert flat == "h"
    assert "H_PCHK" in why


NEW_SAMPLE = """\
[TEST_POINT]
----
--ttt--
-ttttt-
ttttttt
-ttttt-
--ttt--
----
[TEST_SITES]
P1(Pattern) = (2)(4,3),(2,2)
P2(Pattern) = (1)(1,1)
[MODULES_COORDINATE]
CAND_A (100, 200) ! TEG_A
CAND_B (-30, 40) ! TEG_B
"""


def test_parse_new_bracket_format():
    lines = teg_check.strip_line_numbers(NEW_SAMPLE)
    maps = teg_check.parse_wafer_maps(lines)
    assert len(maps) == 1 and maps[0]["name"] == "TEST_POINT"
    # 위/아래 '----' 테두리는 제외 → 콘텐츠 5행, 가운데(index 2)가 ttttttt
    assert maps[0]["w"] == 7 and maps[0]["h"] == 5 and maps[0]["rows"][2] == "ttttttt"

    sites = teg_check.parse_sites(lines)
    assert [s["name"] for s in sites] == ["P1", "P2"]
    assert sites[0]["points"] == [{"pt": 1, "x": 4, "y": 3}, {"pt": 2, "x": 2, "y": 2}]
    assert sites[1]["points"] == [{"pt": 1, "x": 1, "y": 1}]

    tegs = teg_check.parse_teg(lines)
    assert [(t["name"], t["x"], t["y"]) for t in tegs] == [
        ("TEG_A", 100, 200), ("TEG_B", -30, 40)]
    # 후보에 앞 토큰(CAND_A)과 ! 뒤 이름(TEG_A) 둘 다 포함
    assert "CAND_A" in tegs[0]["candidates"] and "TEG_A" in tegs[0]["candidates"]


def test_new_format_with_line_numbers():
    # 행번호 프리픽스가 붙어 와도 strip 후 동일하게 파싱
    numbered = "\n".join(f"{i + 1} {ln}" for i, ln in enumerate(NEW_SAMPLE.splitlines()))
    lines = teg_check.strip_line_numbers(numbered)
    assert len(teg_check.parse_wafer_maps(lines)) == 1
    assert [s["name"] for s in teg_check.parse_sites(lines)] == ["P1", "P2"]
    assert len(teg_check.parse_teg(lines)) == 2


def test_inspect_new_format_end_to_end(teg_env):
    # 새 양식이 기존과 동일하게 검증된다 — 앞 토큰(candidate)으로도 정답지 매칭.
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,,100,200\n"
        "VH_T,CAND_B,,-30,40\n",       # ! 뒤 TEG_B 가 아니라 앞 토큰 CAND_B 가 정답지 이름
        encoding="utf-8")
    res = teg_check.inspect("VH_T", NEW_SAMPLE)
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match" and rows["TEG_A"]["ref_teg"] == "TEG_A"
    # 두 번째 module: 표시 이름은 TEG_B(꼬리표)지만 후보 CAND_B 로 정답지에 매칭
    assert rows["TEG_B"]["ref_teg"] == "CAND_B" and rows["TEG_B"]["status"] == "match"
    assert res["teg"]["summary"]["match"] == 2


def test_teg_flat_prbchk_fallback():
    # H_PCHK/V_PCHK 우선
    assert teg_check.teg_flat("TEG_A, H_PCHK") == ("h", "H_PCHK")
    assert teg_check.teg_flat("TEG_B, V_PCHK") == ("v_R", "V_PCHK")
    # PCHK 가 없으면 PRBCHK 을 폴백으로 인식
    assert teg_check.teg_flat("TEG_C, H_PRBCHK") == ("h", "H_PRBCHK")
    assert teg_check.teg_flat("TEG_D, V_PRBCHK") == ("v_R", "V_PRBCHK")
    # 마커 없음
    assert teg_check.teg_flat("TEG_E") == (None, None)


def test_is_main_excludes_only_module_token():
    for n in ("MAIN", "MAIN_TEG", "TEG_MAIN", "P1MAIN", "main1"):
        assert teg_check.is_main(n) is True
    for n in ("domain", "remain", "TEG_A"):        # 영단어 오탐 방지
        assert teg_check.is_main(n) is False


def test_parse_teg_name_fallback_to_module_word():
    lines = teg_check.strip_line_numbers(
        "#teg-map\n"
        "module MOD_A (10, 20) ! , H_PCHK\n"   # 꼬리표 첫 토큰이 빔 → 'module' 뒤 단어
        "module MOD_B (30, 40)\n"              # 꼬리표 없음 → 'module' 뒤 단어
        "# end")
    tegs = teg_check.parse_teg(lines)
    assert [(t["name"], t["x"], t["y"]) for t in tegs] == [
        ("MOD_A", 10, 20), ("MOD_B", 30, 40)]
    # 꼬리표 이름이 있으면 그대로 우선
    lines2 = teg_check.strip_line_numbers("#teg-map\nmodule MOD_C (1, 2) ! TEG_X, V_PCHK\n")
    assert teg_check.parse_teg(lines2)[0]["name"] == "TEG_X"


def test_transform_v_r_and_module_rule():
    # 모듈별 오프셋: H 관점 입력, 양수=빼기
    rules_h = {("h", "AAA"): (400, 0, "AAA offset x 400")}
    rules_v = {("v_R", "BBB"): (400, 0, "BBB offset x 400 in TEG frame")}
    # v_R: 시계 90° 회전 (x, y) → (y, -x), 모듈 오프셋 없으면 그대로
    assert teg_check.transform("m", 3, 7, "v_R", 0, 0) == (7, -3)
    # v_R: 회전 후 PCHK 절대좌표(dx, dy)를 더함
    assert teg_check.transform("m", 3, 7, "v_R", 5, 0) == (12, -3)
    # 모듈별 보정 (h, AAA): H 관점 양수=빼기 → x - 400
    assert teg_check.transform("AAA", 500, 1, "h", 0, 0, rules=rules_h) == (100, 1)
    # 모듈별 보정 (v_R, BBB): TEG x=400 → 실좌표 y 에서 -400 적용
    # 입력(3, 7) → V 회전 → (7, -3) + PCHK(0,0) → (7, -3) → V offset(400,0): real(+0, -400) → (7, -403)
    assert teg_check.transform("BBB", 3, 7, "v_R", 0, 0, rules=rules_v) == (7, -403)
    # flat 기본(PCHK) 오프셋은 모듈 보정 전에 더해짐
    assert teg_check.transform("m", 1, 2, "h", 10, 20) == (11, 22)


@pytest.fixture()
def teg_env(tmp_path, monkeypatch):
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "LEGACY_CFG_PATH", tmp_path / "_no_legacy.json")
    return tmp_path


def test_inspect_compares_raw_ebeam(teg_env):
    # 정답지: TEG_A 는 원문 (100,200) 그대로, TEG_B 는 불일치 값
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,100,200\n"
        "VH_T,TEG_B,-30,41\n",
        encoding="utf-8")
    res = teg_check.inspect("VH_T", SAMPLE)
    assert res["flat"]["used"] == "h"
    assert res["teg"]["ref_ok"] is True
    # TEG_B 는 ΔY=-1 (3 이내) → 완전 일치는 아니지만 '확인필요'(warning)
    assert res["teg"]["summary"] == {"match": 1, "warning": 1, "mismatch": 0, "extended": 0,
                                     "missing": 0, "total": 2, "chip_overlap": 0}
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match"
    assert rows["TEG_B"]["status"] == "warning"
    assert rows["TEG_B"]["dy"] == -1
    # 맵/패턴도 함께 반환
    assert len(res["maps"]) == 1 and len(res["patterns"]) == 2


def test_status_of_three_levels():
    # 완전 일치
    assert teg_check._status_of(0, 0) == "match"
    # ΔX·ΔY 각각 3 이내(경계 포함) → 확인필요
    assert teg_check._status_of(3, 0) == "warning"
    assert teg_check._status_of(0, -3) == "warning"
    assert teg_check._status_of(2, -3) == "warning"
    assert teg_check._status_of(0.4, 0.4) == "warning"
    # 어느 한 축이라도 3 초과 → 불일치
    assert teg_check._status_of(3.1, 0) == "mismatch"
    assert teg_check._status_of(0, 4) == "mismatch"
    assert teg_check._status_of(2, 5) == "mismatch"


def test_inspect_three_level_status(teg_env):
    # TEG_A 완전 일치 · TEG_B ΔY=3 확인필요 · TEG_C ΔX 큰 값 불일치
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,100,200\n"
        "VH_T,TEG_B,-30,43\n"    # 원문 (-30,40) → ΔY=-3 (3 이내)
        "VH_T,TEG_C,999,999\n",
        encoding="utf-8")
    text = ("#teg-map\n"
            "module m1 (100, 200) ! TEG_A, H_PCHK\n"
            "module m2 (-30, 40) ! TEG_B\n"
            "module m3 (10, 10) ! TEG_C\n")
    res = teg_check.inspect("VH_T", text)
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match"
    assert rows["TEG_B"]["status"] == "warning"
    assert rows["TEG_C"]["status"] == "mismatch"
    assert res["teg"]["summary"]["match"] == 1
    assert res["teg"]["summary"]["warning"] == 1
    assert res["teg"]["summary"]["mismatch"] == 1


def test_inspect_v_r_override_and_missing(teg_env):
    # v_R 로 강제: (100,200) → 시계90° 회전 (200,-100) → +flat_offset(0,10) → (200, -90)
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\nVH_T,TEG_A,200,-90\n", encoding="utf-8")
    res = teg_check.inspect("VH_T", SAMPLE, flat="v_R")
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match"
    assert rows["TEG_B"]["status"] == "missing"   # 정답지에 이름 없음


def test_inspect_without_ref(teg_env):
    res = teg_check.inspect("VH_T", SAMPLE)   # Teg_location 파일 자체가 없음
    assert res["teg"]["ref_ok"] is False
    assert all(r["status"] == "noref" for r in res["teg"]["rows"])


def _write_layout(root, kx=20.0, ky=15.0, cx=5.0, cy=6.0):
    import math
    lines = ["Mask,chip_x_adj,chip_y_adj,Chip_Radius"]
    for x in range(1, 10):
        for y in range(1, 12):
            r = math.hypot((x - cx) * kx, (y - cy) * ky)
            if r <= 155.0:
                lines.append(f"VH_T,{x},{y},{r:.4f}")
    (root / "Chip_Radius.csv").write_text("\n".join(lines), encoding="utf-8")


def test_inspect_chip_overlap(teg_env):
    """칩 격자 모드에서 TEG(크기 포함)가 칩 위에 겹치면 chip_overlap=True."""
    _write_layout(teg_env)   # shot 20×15 mm
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_OK,0,0\n"      # 가로 스크라이브(칩 사이) — 정상
        "VH_T,TEG_BAD,1,3\n",    # 칩 위 — 겹침
        encoding="utf-8")
    # 데모 파일은 mm 단위 + 칩 2×2 (9×6mm, 간격 1mm) → 칩 블록 19×13, 중앙 가로띠 y∈[-0.5,0.5]
    teg_map.save_cfg({
        "ebeam_scale": 1.0,
        "vehicles": {"VH_T": {"mode": "grid", "cols": 2, "rows": 2,
                              "chip_w": 9.0, "chip_h": 6.0, "gap_x": 1.0, "gap_y": 1.0}},
    })
    text = "#teg-map\nmodule m1 (0, 0) ! TEG_OK, H_PCHK\nmodule m2 (1, 3) ! TEG_BAD\n# end"
    res = teg_check.inspect("VH_T", text)
    assert res["shot"]["available"] and res["shot"]["checked"]
    assert len(res["shot"]["cells"]) == 4
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_OK"]["chip_overlap"] is False    # 기본 3.0×0.1mm — 칩 사이 띠 안
    assert rows["TEG_BAD"]["chip_overlap"] is True    # (1,3) 은 우상단 칩(0.5~9.5, 0.5~6.5) 위
    assert res["teg"]["summary"]["chip_overlap"] == 1

    # 칩 격자 모드가 아니면 검사 안 함 (chip_overlap=None)
    teg_map.save_cfg({"vehicles": {"VH_T": {"mode": "none"}}})
    res2 = teg_check.inspect("VH_T", text)
    assert res2["shot"]["checked"] is False
    assert all(r["chip_overlap"] is None for r in res2["teg"]["rows"])


def test_inspect_uses_check_config(teg_env):
    """⚙️ 설정의 flat 기본 오프셋·모듈별 오프셋이 반영된다.
    모듈 오프셋: H/TEG 관점 입력, 양수=빼기."""
    teg_map.save_cfg({"check": {
        "flat_offsets": {"h": [5, -5], "v_R": [0, 0]},
        "modules": [{"flat": "h", "name": "TEG_B", "dx": 100, "dy": 0, "note": "B 보정"}],
    }})
    # 정답지: TEG_A = 원본+flat 오프셋, TEG_B = +flat 오프셋 - 모듈 오프셋(양수=빼기)
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,105,195\n"
        "VH_T,TEG_B,-125,35\n",
        encoding="utf-8")
    res = teg_check.inspect("VH_T", SAMPLE)
    assert res["offset"] == {"dx": 5, "dy": -5}
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match"                 # (100+5, 200-5) = (105, 195)
    assert rows["TEG_B"]["status"] == "match"                 # (-30+5-100, 40-5-0) = (-125, 35)
    assert rows["TEG_B"]["rule_note"] == "B 보정"
    # v_R 강제 시: (100,200) → 회전(200, -100) → flat(0,0) → (200, -100)
    res_v = teg_check.inspect("VH_T", SAMPLE, flat="v_R")
    row_a = [r for r in res_v["teg"]["rows"] if r["name"] == "TEG_A"][0]
    assert (row_a["calc_x"], row_a["calc_y"]) == (200, -100)


def test_pchk_base_offsets_from_ref():
    """정답지에 H_PCHK/V_PCHK 이 있으면 그 raw ebeam 좌표가 flat 기준점이 된다."""
    ref = {
        "H_PCHK": [{"x": 500.0, "y": -300.0, "w": 3.0, "h": 0.1}],
        "V_PRBCHK": [{"x": 120.0, "y": 90.0, "w": 3.0, "h": 0.1}],   # V_PCHK 없어 폴백
        "TEG_A": [{"x": 0.0, "y": 0.0, "w": 3.0, "h": 0.1}],
    }
    bases = teg_check.pchk_base_offsets(ref)
    assert bases["h"] == (500.0, -300.0, "H_PCHK")
    assert bases["v_R"] == (120.0, 90.0, "V_PRBCHK")
    # 사용자 지정 마커가 정답지에 있으면 내장보다 우선
    ref2 = {"H_TPCHK": [{"x": 7.0, "y": 8.0, "w": 1, "h": 1}],
            "H_PCHK": [{"x": 1.0, "y": 2.0, "w": 1, "h": 1}]}
    b2 = teg_check.pchk_base_offsets(ref2, {"h": ["H_TPCHK"], "v_R": []})
    assert b2["h"] == (7.0, 8.0, "H_TPCHK")
    # 기준 PCHK 이 정답지에 없으면 그 flat 은 결과에서 빠진다
    assert teg_check.pchk_base_offsets({"TEG_A": [{"x": 0, "y": 0, "w": 1, "h": 1}]}) == {}


def test_inspect_uses_pchk_db_base(teg_env):
    """환산X/Y = 기준 PCHK 의 DB Ebeam 좌표 + Mapfile 상대좌표 (사용자 요청).

    정답지에 H_PCHK(기준점 500,-300)이 있으면 m1 의 Mapfile (100,200) 은
    (600,-100) 으로 원복된다. flat_offsets 설정보다 DB 기준점이 우선.
    """
    teg_map.save_cfg({"check": {"flat_offsets": {"h": [9999, 9999], "v_R": [0, 0]}}})
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,H_PCHK,500,-300\n"      # 기준 PCHK — DB Ebeam
        "VH_T,TEG_A,600,-100\n"       # m1(100,200) 원복값 = 기준 + 상대
        "VH_T,TEG_B,470,-260\n",      # m2(-30,40) 원복값 = (500-30, -300+40)
        encoding="utf-8")
    res = teg_check.inspect("VH_T", SAMPLE)
    assert res["pchk_base"]["source"] == "db"
    assert res["pchk_base"]["ref_name"] == "H_PCHK"
    assert res["offset"] == {"dx": 500, "dy": -300}   # 설정(9999)이 아닌 DB 기준점
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert (rows["TEG_A"]["calc_x"], rows["TEG_A"]["calc_y"]) == (600, -100)
    assert rows["TEG_A"]["status"] == "match"
    assert rows["TEG_B"]["status"] == "match"


def test_inspect_per_teg_flat_and_main_exclusion(teg_env):
    """TEG 별 PCHK 마커로 개별 보정하고, MAIN 모듈은 검사에서 제외한다."""
    # TEG_H = H_PCHK → 그대로(100,200) / TEG_V = V_PRBCHK → (x,y)→(y,-x+10) = (40,-30+10)=(40,-20)
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_H,100,200\n"
        "VH_T,TEG_V,40,-20\n",
        encoding="utf-8")
    text = (
        "#teg-map\n"
        "module m0 (5, 5) ! MAIN, H_PCHK\n"       # MAIN → 제외
        "module m1 (100, 200) ! TEG_H, H_PCHK\n"  # h 로 보정
        "module m2 (30, 40) ! TEG_V, V_PRBCHK\n"  # v_R(폴백) 로 보정
        "# end")
    res = teg_check.inspect("VH_T", text)
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert "MAIN" not in rows                       # MAIN 행 제외
    assert res["teg"]["excluded_main"] == 1
    assert res["teg"]["summary"]["total"] == 2
    # 서로 다른 flat 이 TEG 별로 적용되어 둘 다 정답지와 일치
    assert rows["TEG_H"]["flat_used"] == "h" and rows["TEG_H"]["status"] == "match"
    assert rows["TEG_V"]["flat_used"] == "v_R" and rows["TEG_V"]["flat_marker"] == "V_PRBCHK"
    assert (rows["TEG_V"]["calc_x"], rows["TEG_V"]["calc_y"]) == (40, -20)
    assert rows["TEG_V"]["status"] == "match"


def test_custom_marker_user_input():
    """기준 PCHK 이 내장 마커로 안 잡히는 표기(H_TPCHK 등) — 사용자 입력 마커 인식."""
    # 내장 마커만으로는 미인식
    assert teg_check.teg_flat("TEG_A, H_TPCHK") == (None, None)
    # 사용자 마커 merge 후 인식 (TEG 별 개별 판정)
    mm = teg_check.build_marker_map({"h": ["H_TPCHK"], "v_R": ["V_TPCHK"]})
    assert teg_check.teg_flat("TEG_A, H_TPCHK", mm) == ("h", "H_TPCHK")
    assert teg_check.teg_flat("TEG_B, V_TPCHK", mm) == ("v_R", "V_TPCHK")
    # 내장 마커는 그대로 유지 (custom 이 우선순위만 앞섬)
    assert teg_check.teg_flat("TEG_C, H_PCHK", mm) == ("h", "H_PCHK")
    # detect_flat 도 동일 매핑 사용
    tegs = [{"name": "m1", "tail": "TEG_A, H_TPCHK"}]
    flat, why = teg_check.detect_flat(tegs, mm)
    assert flat == "h" and "H_TPCHK" in why


def test_build_marker_map_dedup_and_priority():
    # 대소문자 중복 제거, custom 이 내장과 겹치면 custom 표기 유지
    mm = teg_check.build_marker_map({"h": ["h_pchk", "H_XX", "H_XX"], "v_R": []})
    keys_upper = [k.upper() for k in mm]
    assert keys_upper.count("H_PCHK") == 1 and keys_upper.count("H_XX") == 1


def test_inspect_needs_input_flag():
    """마커 미인식 + flat 강제 없음 → needs_input, 마커 입력 시 해소."""
    raw = "#teg-map\nmodule m1 (1, 2) ! TEG_A, H_TPCHK\n# end"
    res = teg_check.inspect("", raw)
    assert res["flat"]["needs_input"] is True
    res2 = teg_check.inspect("", raw, custom_markers={"h": ["H_TPCHK"], "v_R": []})
    assert res2["flat"]["needs_input"] is False
    assert res2["flat"]["detected"] == "h"
    assert res2["teg"]["rows"][0]["flat_marker"] == "H_TPCHK"
    # flat 강제 시에도 needs_input 은 해소
    res3 = teg_check.inspect("", raw, flat="v_R")
    assert res3["flat"]["needs_input"] is False


def test_parse_teg_name_candidates():
    # 이름 후보 = module~( 토큰 + 꼬리표 토큰 전체 (순서 유지·중복 제거)
    lines = teg_check.strip_line_numbers(
        "#teg-map\n"
        "module DUMMY1 (12.5, -3.0) ! TEG_A, H_PCHK, LOT7\n"
        "module MOD_B (30, 40)\n"
        "# end")
    tegs = teg_check.parse_teg(lines)
    assert tegs[0]["idx"] == 0
    assert tegs[0]["name"] == "TEG_A" and tegs[0]["auto_name"] == "TEG_A"
    assert tegs[0]["name_source"] == "tail0"
    assert tegs[0]["candidates"] == ["DUMMY1", "TEG_A", "H_PCHK", "LOT7"]
    assert tegs[1]["name"] == "MOD_B" and tegs[1]["name_source"] == "module"
    assert tegs[1]["candidates"] == ["MOD_B"]


def test_inspect_name_overrides(teg_env):
    # 이름 기반 정확 매칭: 자동 인식 이름(TEG_A)이 정답지에 없어도, 후보 토큰(DUMMY1)이
    # 정답지 teg 와 완전 일치하면 그 teg 로 자동 대조된다 (순서 기반 X).
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,DUMMY1,100,200\n",
        encoding="utf-8")
    text = "#teg-map\nmodule DUMMY1 (100, 200) ! TEG_A, H_PCHK\n# end"
    res = teg_check.inspect("VH_T", text)
    rows = res["teg"]["rows"]
    # 표시 이름은 여전히 자동 인식값(TEG_A)이지만 후보 DUMMY1 로 정답지와 매칭됨
    assert rows[0]["name"] == "TEG_A"
    assert rows[0]["ref_teg"] == "DUMMY1" and rows[0]["match_source"] == "teg"
    assert rows[0]["status"] == "match"
    # override 는 표시 이름/되돌리기 기준을 바꾸며, 매칭에도 앞선 후보로 쓰인다
    res2 = teg_check.inspect("VH_T", text, name_overrides={"0": "DUMMY1"})
    rows2 = res2["teg"]["rows"]
    assert rows2[0]["name"] == "DUMMY1"
    assert rows2[0]["name_source"] == "override"
    assert rows2[0]["auto_name"] == "TEG_A"          # 되돌리기 기준은 유지
    assert rows2[0]["status"] == "match" and rows2[0]["ref_teg"] == "DUMMY1"
    # int 키·빈 값·범위 밖 idx 는 무시
    res3 = teg_check.inspect("VH_T", text, name_overrides={0: "DUMMY1", 99: "X", "1": ""})
    assert res3["teg"]["rows"][0]["name"] == "DUMMY1"


def test_inspect_name_based_topcell_match(teg_env):
    # module name 후보가 정답지의 top_cell(별칭)과 완전 일치하면 그 teg 로 매칭된다.
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,ALIAS_A,100,200\n",
        encoding="utf-8")
    # module 이름/후보에 teg(TEG_A)는 없고 top_cell(ALIAS_A)만 있음
    text = "#teg-map\nmodule mod (100, 200) ! ALIAS_A, H_PCHK\n# end"
    res = teg_check.inspect("VH_T", text)
    row = res["teg"]["rows"][0]
    assert row["ref_teg"] == "TEG_A" and row["match_source"] == "top_cell"
    assert row["match_token"] == "ALIAS_A"
    assert row["status"] == "match"


def test_inspect_extended_check_strip_01(teg_env):
    # 확장체크: TEGA01 이 정답지 teg/top_cell 어디에도 없으면 '01' 을 떼고 TEGA 로 재매칭.
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\n"
        "VH_T,TEGA,,100,200\n"
        "VH_T,BCELL,B_ALIAS,5,6\n",
        encoding="utf-8")
    text = ("#teg-map\n"
            "module m1 (100, 200) ! TEGA01, H_PCHK\n"     # TEGA01 → '01' 떼고 TEGA (teg)
            "module m2 (9, 9) ! B_ALIAS01\n"              # B_ALIAS01 → B_ALIAS (top_cell→BCELL)
            "module m3 (1, 1) ! ZZZ01\n"                  # ZZZ 도 없음 → 미등록
            "# end")
    res = teg_check.inspect("VH_T", text)
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEGA01"]["status"] == "extended"
    assert rows["TEGA01"]["ref_teg"] == "TEGA" and rows["TEGA01"]["match_token"] == "TEGA01"
    assert rows["B_ALIAS01"]["status"] == "extended"
    assert rows["B_ALIAS01"]["ref_teg"] == "BCELL" and rows["B_ALIAS01"]["match_source"] == "top_cell"
    assert rows["ZZZ01"]["status"] == "missing"
    assert res["teg"]["summary"]["extended"] == 2
    assert res["teg"]["summary"]["missing"] == 1
    # 완전 일치가 있으면 확장체크로 내려가지 않는다 — TEGA(정확)면 그대로 좌표 판정
    text2 = "#teg-map\nmodule m (100, 200) ! TEGA, H_PCHK\n# end"
    r2 = teg_check.inspect("VH_T", text2)["teg"]["rows"][0]
    assert r2["status"] == "match" and r2["extended"] is False


def test_inspect_main_rows_and_override_revives(teg_env):
    # MAIN 으로 인식돼 제외된 행이 main_rows 로 노출되고,
    # override 로 이름을 바로잡으면 검사 대상으로 돌아온다.
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_C,5,6\n",
        encoding="utf-8")
    text = "#teg-map\nmodule TEG_C (5, 6) ! MAIN_X, H_PCHK\n# end"
    res = teg_check.inspect("VH_T", text)
    assert res["teg"]["summary"]["total"] == 0
    assert res["teg"]["excluded_main"] == 1
    assert res["teg"]["main_rows"][0]["idx"] == 0
    assert res["teg"]["main_rows"][0]["candidates"] == ["TEG_C", "MAIN_X", "H_PCHK"]
    res2 = teg_check.inspect("VH_T", text, name_overrides={"0": "TEG_C"})
    assert res2["teg"]["excluded_main"] == 0
    assert res2["teg"]["main_rows"] == []
    assert res2["teg"]["rows"][0]["status"] == "match"


def test_inspect_main_groups(teg_env):
    # MAIN02 = die 급 블록 — 내부 TEG 행들이 같은 그룹명으로 나열됨.
    # 그룹명 뒤 빈 토큰을 건너뛰고 첫 유효 토큰(마커 제외)이 내부 TEG 이름.
    text = ("#teg-map\n"
            "module m0 (0, 0) ! H_PCHK\n"
            "module DUMMY1 (12.5, -3.0) ! MAIN02, ,module_detail, LOT7\n"
            "module DUMMY1 (1, 2) ! MAIN02, ,module_detail2, X\n"
            "module DUMMY1 (3, 4) ! MAIN02, , , Z\n"       # 유효 토큰 없음(마지막 Z) → Z
            "# end")
    res = teg_check.inspect("VH_T", text)
    groups = res["teg"]["main_groups"]
    assert len(groups) == 1 and groups[0]["group"] == "MAIN02"
    names = [t["teg"] for t in groups[0]["tegs"]]
    assert names == ["module_detail", "module_detail2", "Z"]
    # 변환은 일반 행과 동일 (h flat, 기본 오프셋 0) → 원문 좌표 그대로
    assert (groups[0]["tegs"][0]["x"], groups[0]["tegs"][0]["y"]) == (12.5, -3.0)
    assert groups[0]["applied_at"] == ""      # 아직 미반영
    # H_PCHK 마커 행 자체는 MAIN 그룹이 아님
    assert res["teg"]["summary"]["total"] >= 1


def test_main_overlay_apply_exists_overwrite(teg_env):
    groups = [{"group": "MAIN02",
               "tegs": [{"teg": "module_detail", "x": 12.5, "y": -3.0},
                        {"teg": "module_detail2", "x": 1, "y": 2}]}]
    r1 = teg_map.apply_main_overlays("VH_T", groups)
    assert r1["ok"] is True and r1["saved"] == ["MAIN02"]
    # 같은 그룹 재반영 — overwrite 없이는 거부 + exists 반환 (UI 확인 계약)
    r2 = teg_map.apply_main_overlays("VH_T", groups)
    assert r2["ok"] is False
    assert r2["exists"][0]["group"] == "MAIN02" and r2["exists"][0]["applied_at"]
    # overwrite=True 면 덮어씀
    r3 = teg_map.apply_main_overlays("VH_T", groups, overwrite=True)
    assert r3["ok"] is True
    ov = teg_map.get_main_overlays("VH_T")
    assert ov["MAIN02"]["source"] == "mapfile-check"
    assert len(ov["MAIN02"]["tegs"]) == 2
    # inspect 가 기존 반영 시각을 되돌려준다 (다시 검사 시 "기존 반영" 표시용)
    text = "#teg-map\nmodule d (5, 6) ! MAIN02, ,inner\n# end"
    res = teg_check.inspect("VH_T", text)
    assert res["teg"]["main_groups"][0]["applied_at"] == ov["MAIN02"]["applied_at"]
    # 삭제
    assert teg_map.delete_main_overlay("VH_T", "MAIN02") is True
    assert teg_map.get_main_overlays("VH_T") == {}


def test_main_overlay_merges_into_map_payload(teg_env):
    # map_payload 가 overlay TEG 를 "그룹·이름" 으로 병합 + main_overlays 메타 노출
    (teg_env / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\n"
        + "\n".join(f"VH_T,{x},{y},{((x - 3) ** 2 * 25 + (y - 3) ** 2 * 25) ** 0.5}"
                    for x in range(1, 6) for y in range(1, 6)),
        encoding="utf-8")
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\nVH_T,MAIN02,0,0\n", encoding="utf-8")
    teg_map.apply_main_overlays("VH_T", [
        {"group": "MAIN02", "tegs": [{"teg": "inner1", "x": 100, "y": 200}]}])
    p = teg_map.map_payload("VH_T")
    names = {t["teg"] for t in p["tegs"]}
    assert "MAIN02" in names and "MAIN02·inner1" in names
    ov_teg = next(t for t in p["tegs"] if t["teg"] == "MAIN02·inner1")
    assert ov_teg["overlay_group"] == "MAIN02"
    scale = teg_map.load_cfg()["ebeam_scale"]
    assert ov_teg["ebeam_x"] == 100 * scale and ov_teg["ebeam_y"] == 200 * scale
    assert p["main_overlays"]["MAIN02"]["count"] == 1
    assert p["main_overlays"]["MAIN02"]["applied_at"]


def test_default_check_targets_prefix():
    # H_/V_ 로 시작하는 것만 (대소문자 무관, 순서 유지). substring 아닌 prefix.
    names = ["H_PCHK", "V_PRBCHK", "TEG_A", "h_low", "XH_PCHK", "TEGA", "V_X"]
    assert teg_map.default_check_targets(names) == ["H_PCHK", "V_PRBCHK", "h_low", "V_X"]


def test_teg_target_options_and_verification(teg_env):
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\n"
        "VH_T,H_PCHK,HCELL,100,200\n"
        "VH_T,V_PCHK,VCELL,50,60\n"
        "VH_T,TEGA,,10,20\n"
        "VH_T,TEGA01,,11,21\n",
        encoding="utf-8")
    opts = teg_map.teg_target_options("VH_T")
    assert opts["source"] == "default"
    assert opts["targets"] == ["H_PCHK", "V_PCHK"]          # H_/V_ 기본 대상
    assert opts["tegs"][0] == {"teg": "H_PCHK", "top_cell": ["HCELL"]}

    # 완전 일치만: HCELL 은 H_PCHK 의 top_cell 로 매칭, V_PCHK 은 미설정
    tv = teg_map.target_verification("VH_T", {"HCELL", "TEGA"})
    by = {i["teg"]: i for i in tv["items"]}
    assert by["H_PCHK"]["matched"] and by["H_PCHK"]["matched_by"] == "top_cell"
    assert by["H_PCHK"]["matched_module"] == "HCELL"
    assert by["V_PCHK"]["matched"] is False
    assert tv["matched"] == 1 and tv["missing"] == 1


def test_target_verification_exact_match_no_substring(teg_env):
    # TEGA 가 대상일 때 module name 이 TEGA01 뿐이면 TEGA 는 미설정이어야 한다
    # (substring/포함 매칭 금지 — TEGA01 을 TEGA 로 인식하면 안 됨).
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\n"
        "VH_T,TEGA,,10,20\n"
        "VH_T,TEGA01,TOPA,11,21\n",
        encoding="utf-8")
    teg_map.save_cfg({"check_targets": {"VH_T": ["TEGA", "TEGA01"]}})
    tv = teg_map.target_verification("VH_T", {"TEGA01"})
    by = {i["teg"]: i for i in tv["items"]}
    assert by["TEGA"]["matched"] is False           # TEGA01 ⊃ TEGA 지만 완전 일치 아님
    assert by["TEGA01"]["matched"] and by["TEGA01"]["matched_by"] == "teg"


def test_check_targets_config_save_and_reset(teg_env):
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,H_PCHK,1,2\nVH_T,TEG_A,3,4\n",
        encoding="utf-8")
    # 명시 저장 → source=config (빈 리스트라도 '지정됨' 으로 유지)
    teg_map.save_cfg({"check_targets": {"VH_T": ["TEG_A"]}})
    o1 = teg_map.teg_target_options("VH_T")
    assert o1["source"] == "config" and o1["targets"] == ["TEG_A"]
    teg_map.save_cfg({"check_targets": {"VH_T": []}})
    assert teg_map.teg_target_options("VH_T")["source"] == "config"
    # None → 삭제(기본값 복귀)
    teg_map.save_cfg({"check_targets": {"VH_T": None}})
    o2 = teg_map.teg_target_options("VH_T")
    assert o2["source"] == "default" and o2["targets"] == ["H_PCHK"]


def test_inspect_includes_targets(teg_env):
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\n"
        "VH_T,H_PCHK,HCELL,100,200\n"
        "VH_T,V_PCHK,VCELL,50,60\n",
        encoding="utf-8")
    # module name 으로 H_PCHK(teg) 는 등장, V_PCHK 은 top_cell(VCELL)로도 미등장 → 미설정
    text = "#teg-map\nmodule m1 (100,200) ! H_PCHK\nmodule m2 (1,2) ! TEG_X\n# end"
    res = teg_check.inspect("VH_T", text)
    tgt = res["teg"]["targets"]
    assert tgt["total"] == 2 and tgt["source"] == "default"
    by = {i["teg"]: i for i in tgt["items"]}
    assert by["H_PCHK"]["matched"] and by["H_PCHK"]["matched_by"] == "teg"
    assert by["V_PCHK"]["matched"] is False
    # vehicle 이 비면 targets 는 빈 결과
    assert teg_check.inspect("", text)["teg"]["targets"]["total"] == 0


def test_direction_column_maps_to_flat_zone(teg_env):
    # direction 열: H/Horizontal → h, V/Vertical(대소문자·전체단어 무관) → v, 빈값 → h.
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,direction,ebeam_x,ebeam_y\n"
        "VH_T,A,H,0,0\n"
        "VH_T,B,V,1,1\n"
        "VH_T,C,Vertical,2,2\n"
        "VH_T,D,,3,3\n"
        "VH_T,E,horizontal,4,4\n",
        encoding="utf-8")
    tdf, _ = teg_map.load_tegs()
    fz = {r["teg"]: r["flat_zone"] for _, r in tdf.iterrows()}
    assert fz == {"A": "h", "B": "v", "C": "v", "D": "h", "E": "h"}


def test_load_ref_swaps_wh_for_vertical(teg_env):
    # V(세로)면 좌표는 그대로 두고 w/h 만 스왑해 세워 그린다.
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,direction,ebeam_x,ebeam_y,teg_w,teg_h\n"
        "VH_T,H1,H,5,6,3.0,1.0\n"
        "VH_T,V1,V,5,6,3.0,1.0\n",
        encoding="utf-8")
    ref, _tc, _path, _err = teg_check.load_ref("VH_T")
    scale = teg_map.load_cfg()["ebeam_scale"]
    # 좌표(x,y)는 두 행 동일 — 스왑은 w/h 에만 적용
    assert ref["H1"][0]["x"] == 5 and ref["H1"][0]["y"] == 6
    assert ref["V1"][0]["x"] == 5 and ref["V1"][0]["y"] == 6
    assert ref["H1"][0]["w"] == 3.0 * scale and ref["H1"][0]["h"] == 1.0 * scale
    assert ref["V1"][0]["w"] == 1.0 * scale and ref["V1"][0]["h"] == 3.0 * scale


def test_load_tegs_reads_top_cell(teg_env):
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,top_cell,ebeam_x,ebeam_y\nVH_T,TEG_A,CELL_A,1,2\nVH_T,TEG_B,,3,4\n",
        encoding="utf-8")
    tdf, _ = teg_map.load_tegs()
    rows = {r["teg"]: r for _, r in tdf.iterrows()}
    assert rows["TEG_A"]["top_cell"] == "CELL_A"
    assert rows["TEG_B"]["top_cell"] == ""       # 빈 값은 "nan" 아닌 빈 문자열


def test_inspect_main_groups_chip_overlap(teg_env):
    """칩 격자 모드면 MAIN 내부 TEG 도 칩(die) 겹침 검사 — 일반 행과 동일 규약."""
    _write_layout(teg_env)   # shot 20×15 mm
    teg_map.save_cfg({
        "ebeam_scale": 1.0,
        "vehicles": {"VH_T": {"mode": "grid", "cols": 2, "rows": 2,
                              "chip_w": 9.0, "chip_h": 6.0, "gap_x": 1.0, "gap_y": 1.0}},
    })
    text = ("#teg-map\n"
            "module d1 (0, 0) ! MAIN02, ,inner_ok, H_PCHK\n"   # 가로 스크라이브 — 정상
            "module d2 (1, 3) ! MAIN02, ,inner_bad\n"          # 칩 위 — 겹침
            "# end")
    res = teg_check.inspect("VH_T", text)
    assert res["shot"]["checked"] is True
    g = res["teg"]["main_groups"][0]
    by = {t["teg"]: t for t in g["tegs"]}
    assert by["inner_ok"]["chip_overlap"] is False
    assert by["inner_bad"]["chip_overlap"] is True
    assert g["chip_overlap"] == 1
    # 격자 모드가 아니면 None
    teg_map.save_cfg({"vehicles": {"VH_T": {"mode": "none"}}})
    res2 = teg_check.inspect("VH_T", text)
    assert all(t["chip_overlap"] is None for t in res2["teg"]["main_groups"][0]["tegs"])
