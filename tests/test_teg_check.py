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


def test_transform_v_r_and_module_rule():
    rules = {("h", "AAA"): (-400, 0, "AAA offset x 400")}
    # v_R: (x, y) → (y, -x + v_r_offset)
    assert teg_check.transform("m", 3, 7, "v_R", 0, 0, v_r_offset=10) == (7, 7)
    # 모듈별 보정 (h, AAA) → x-400
    assert teg_check.transform("AAA", 500, 1, "h", 0, 0, rules=rules) == (100, 1)
    # flat 기본 오프셋은 모듈 보정 전에 더해짐
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
    assert res["teg"]["summary"] == {"match": 1, "mismatch": 1, "missing": 0, "total": 2}
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match"
    assert rows["TEG_B"]["status"] == "mismatch"
    assert rows["TEG_B"]["dy"] == -1
    # 맵/패턴도 함께 반환
    assert len(res["maps"]) == 1 and len(res["patterns"]) == 2


def test_inspect_v_r_override_and_missing(teg_env):
    # v_R 로 강제: (100,200) → (200, -100+10) = (200, -90)
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


def test_inspect_uses_check_config(teg_env):
    """⚙️ 설정의 flat 기본 오프셋·모듈별 오프셋·v_R 회전 offset 이 반영된다."""
    teg_map.save_cfg({"check": {
        "v_r_offset": 20,
        "flat_offsets": {"h": [5, -5], "v_R": [0, 0]},
        "modules": [{"flat": "h", "name": "TEG_B", "dx": 100, "dy": 0, "note": "B 보정"}],
    }})
    # 정답지: TEG_A = 원본+flat 오프셋, TEG_B = +flat 오프셋+모듈 오프셋
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,105,195\n"
        "VH_T,TEG_B,75,35\n",
        encoding="utf-8")
    res = teg_check.inspect("VH_T", SAMPLE)
    assert res["offset"] == {"dx": 5, "dy": -5}
    rows = {r["name"]: r for r in res["teg"]["rows"]}
    assert rows["TEG_A"]["status"] == "match"                 # (100+5, 200-5)
    assert rows["TEG_B"]["status"] == "match"                 # (-30+5+100, 40-5)
    assert rows["TEG_B"]["rule_note"] == "B 보정"
    # v_R 강제 시 설정된 회전 offset(20) 사용: (100,200) → (200, -100+20)
    res_v = teg_check.inspect("VH_T", SAMPLE, flat="v_R")
    row_a = [r for r in res_v["teg"]["rows"] if r["name"] == "TEG_A"][0]
    assert (row_a["calc_x"], row_a["calc_y"]) == (200, -80)
    assert res_v["v_r_offset"] == 20
