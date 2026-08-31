import copy
import math
import re

import pandas as pd
import pytest

from core import teg_check
from core import teg_map
from core import auth
from fastapi import HTTPException
from routers import filebrowser
from routers import teg_map as teg_router


def test_sl_coordinate_warning_is_limited_to_two_raw_units_per_axis():
    assert teg_check._status_of(0, 0) == "match"
    assert teg_check._status_of(2, -2) == "warning"
    assert teg_check._status_of(2.01, 0) == "mismatch"
    assert teg_check._status_of(0, -2.01) == "mismatch"


def test_map_payload_ignores_legacy_mapfile_main_overlays(tmp_path, monkeypatch):
    """위치 조회 목록은 Teg_location 만 사용하고 Mapfile 역반영 파일은 읽지 않는다."""
    layout = pd.DataFrame([
        {"vehicle": "P", "x": 0, "y": 0, "r": 0.0},
    ])
    tegs = pd.DataFrame([
        {"vehicle": "P", "teg": "MAIN01", "ebeam_x": 1.0, "ebeam_y": 2.0,
         "teg_w": 3.0, "teg_h": 0.1, "flat_zone": "h"},
    ])
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg["vehicles"] = {}

    (tmp_path / "main_overlays.json").write_text(
        '{"P":{"MAIN01":{"applied_at":"2026-01-01T00:00:00+09:00",'
        '"source":"mapfile-check","tegs":[{"teg":"INNER01","x":10,"y":20}]}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(teg_map, "teg_dir", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_map, "load_layout", lambda: (layout, tmp_path / "Chip_Radius.csv"))
    monkeypatch.setattr(teg_map, "load_tegs", lambda: (tegs, tmp_path / "Teg_location.csv"))
    monkeypatch.setattr(teg_map, "load_main_chips", lambda: ({}, tmp_path / "Main_chip_info.csv"))

    payload = teg_map.map_payload("P")

    assert [item["teg"] for item in payload["tegs"]] == ["MAIN01"]
    assert "main_overlays" not in payload
    assert all("overlay_group" not in item for item in payload["tegs"])


def test_main_chip_purpose_loader_and_name_normalization(tmp_path, monkeypatch):
    (tmp_path / "Main_chip_info.csv").write_text(
        "vehicle,chip_name,chipsize_x,chipsize_y,purpose\n"
        "P,MAIN_M01,9000,6000,NO_TEG\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: {"main_chip_file": "Main_chip_info.csv"})

    purposes, _ = teg_map.load_main_chip_purposes()

    assert teg_map.main_purpose_for("P", "MAIN01", purposes) == "NO_TEG"
    assert teg_map.normalize_main_purpose(" no-teg ") == "NO TEG"
    assert teg_map.is_main_purpose_warning("no teg") is True
    assert teg_map.is_main_purpose_warning("IP") is True
    assert teg_map.is_main_purpose_warning("LOGIC") is False


def test_mapfile_main_inside_is_yellow_and_forbidden_purpose_is_red(monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg["ebeam_scale"] = 0.001
    cfg["teg_default_w"] = 0.1
    cfg["teg_default_h"] = 0.1
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (None, {}, "", "정답지 없음"))
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": True, "checked": False, "cells": [],
        "main_cells": [{"name": "MAIN_M01", "x": 0, "y": 0, "w": 9, "h": 6}],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    purposes = {"P": {"MAIN_M01": ""}}
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: (purposes, None))
    source = (
        "#teg-map\n"
        "module MAIN_BLOCK (0,0) ! MAIN_M01,H_PCHK\n"
        "module INNER_A (1000,1000) ! MAIN_M01,INNER_A,H_PCHK\n"
    )

    normal = teg_check.inspect("P", source, flat="h")
    normal_group = normal["teg"]["main_groups"][0]
    assert normal_group["tegs"][1]["light"] == "yellow"
    assert normal_group["tegs"][1]["light_reason"] == "MAIN_M01 die 안"
    assert normal["teg"]["main_purpose_warnings"] == []

    purposes["P"]["MAIN_M01"] = "IP"
    forbidden = teg_check.inspect("P", source, flat="h")
    forbidden_group = forbidden["teg"]["main_groups"][0]
    assert forbidden_group["purpose_warning"] is True
    assert forbidden_group["tegs"][1]["light"] == "red"
    assert "purpose IP" in forbidden_group["tegs"][1]["light_reason"]
    assert forbidden["teg"]["main_purpose_warnings"] == [{
        "group": "MAIN_M01", "purpose": "IP",
        "reason": "Main_chip_info.csv에서 TEG 배치 금지 purpose로 지정됨",
    }]


def test_teg_location_unregistered_module_is_orange_main_info_missing_not_die_intrusion(monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg["ebeam_scale"] = 0.001
    cfg["teg_default_w"] = 0.1
    cfg["teg_default_h"] = 0.1
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: ({
        "SL_A": [{"x": 0, "y": 0, "w": 0.1, "h": 0.1,
                  "dir": "h", "top_cell": ""}],
    }, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": True, "checked": True,
        "cells": [{"name": "DIE01", "x": 0, "y": 0, "w": 10, "h": 10}],
        "main_cells": [{"name": "MAIN_M01", "x": 0, "y": 0, "w": 10, "h": 10}],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    result = teg_check.inspect(
        "P", "#teg-map\nmodule UNKNOWN (1000,1000) ! UNKNOWN\n", flat="h",
    )

    row = result["teg"]["rows"][0]
    assert row["status"] == "missing"
    assert row["teg_kind"] == "main_info_missing"
    assert row["light"] == "orange"
    assert row["main_group"] == "MAIN_M01"
    assert row["light_reason"] == "MAIN 정보없음 · 소속 MAIN_M01"
    assert row["die_state"] is None
    assert row["chip_overlap"] is None


def test_explicit_main_group_without_main_chip_info_is_orange(monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg["ebeam_scale"] = 0.001
    cfg["teg_default_w"] = 0.1
    cfg["teg_default_h"] = 0.1
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (None, {}, "", "정답지 없음"))
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": True, "checked": False, "cells": [], "main_cells": [],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))
    source = (
        "#teg-map\n"
        "module MAIN_BLOCK (0,0) ! MAIN_M01,H_PCHK\n"
        "module INNER_A (1000,1000) ! MAIN_M01,INNER_A,H_PCHK\n"
    )

    result = teg_check.inspect("P", source, flat="h")

    group = result["teg"]["main_groups"][0]
    assert group["main_info_missing"] is True
    assert group["tegs"][1]["main_group"] == "MAIN_M01"
    assert group["tegs"][1]["light"] == "orange"
    assert group["tegs"][1]["light_reason"] == "MAIN 정보없음 · 소속 MAIN_M01"


@pytest.mark.parametrize("module_name, ref_name, rule_name", [
    ("H_QAF01", "QAF01H", "H/V 접두사를 뒤로 이동"),
    ("H_QAB03", "QA03HB", "끝 영문자를 H/V 뒤로 이동"),
    ("H_DFM01", "DFMSL01", "DFM의 H/V를 SL로 변환"),
    ("H_SRAM24", "SRAM24", "SRAM의 H/V 접두사 제거"),
    ("V_QAB03", "QA03VB", "끝 영문자를 H/V 뒤로 이동"),
])
def test_each_builtin_macro_maps_one_pattern_to_one_reference_name(
        monkeypatch, module_name, ref_name, rule_name):
    """각 내장 매크로는 정규식 하나와 정답지 치환식 하나로 매칭한다.

    변환 결과와 같은 정답지 이름이 있을 때만 채택하고 좌표 비교를 정상 수행한다.
    """
    ref = {
        ref_name: [{"x": 100.5, "y": 200.5, "w": 1.0, "h": 1.0,
                    "dir": "h", "top_cell": ""}],
    }
    monkeypatch.setattr(teg_check, "load_ref",
                        lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": teg_map._clean_check({}), "ebeam_scale": 1.0,
        "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda v, n: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0})
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    result = teg_check.inspect("P", f"#teg-map\nmodule {module_name} (100.5,200.5) !\n",
                               flat="h")
    row = result["teg"]["rows"][0]

    assert row["ref_teg"] == ref_name
    assert row["match_rule"] == "macro"
    assert row["match_rule_label"] == rule_name
    assert row["status"] == "match"
    assert row["light"] == "green"


@pytest.mark.parametrize(("ref_name", "module_name"), [
    ("H_QAF01", "QAF01H"),
    ("H_QAB06", "QA06HB"),
    ("V_QAB03", "QA03VB"),
    ("H_DFM01", "DFMSL01"),
    ("H_SRAM24", "SRAM24"),
])
def test_reverse_alias_notations_match_reference_and_check_coordinates(
        monkeypatch, ref_name, module_name):
    """내장 표기 매크로의 역방향도 같은 TEG로 보고 실제 좌표를 검사한다."""
    ref = {
        ref_name: [{"x": 100.5, "y": 200.5, "w": 1.0, "h": 1.0,
                    "dir": "h", "top_cell": ""}],
    }
    monkeypatch.setattr(teg_check, "load_ref",
                        lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": teg_map._clean_check({}), "ebeam_scale": 1.0,
        "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda v, n: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0})
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    result = teg_check.inspect(
        "P", f"#teg-map\nmodule {module_name} (101.5,201.5) !\n", flat="h")
    row = result["teg"]["rows"][0]

    assert row["ref_teg"] == ref_name
    assert row["match_rule"] == "alias"
    assert row["extended"] is False
    assert row["status"] == "warning"
    assert row["dx"] == 1.0
    assert row["dy"] == 1.0
    assert row["light"] == "yellow"
    assert result["teg"]["criteria"]["sl_coordinate_tolerance"] == 2.0


def test_remaining_builtin_name_rules_still_resolve():
    """01 제거·접두사 재배치·분할 번호 제거 규칙도 계속 동작한다."""
    extended_ref = {"TEGA": [{"x": 1.0, "y": 2.0}]}
    reordered_ref = {"AAA01H01": [{"x": 1.0, "y": 2.0}]}
    split_ref = {"TEGA": [{"x": 1.0, "y": 2.0}]}

    assert teg_check.resolve_ref_teg_extended(
        {"name": "TEGA01", "candidates": ["TEGA01"], "tail": ""},
        extended_ref, {}) == ("TEGA", "teg", "TEGA01")
    assert teg_check.resolve_ref_teg_reorder(
        {"name": "H_AAA01", "candidates": ["H_AAA01"], "tail": ""},
        reordered_ref, {}) == ("AAA01H01", "teg", "H_AAA01")
    assert teg_check.resolve_ref_teg_split(
        {"name": "TEGA_1", "candidates": ["TEGA_1"], "tail": ""},
        split_ref, {}) == ("TEGA", "teg", "TEGA_1")


@pytest.mark.parametrize("module_name, ref_name", [
    ("H_QAB03", "QAB30"),       # 숫자가 다르면 다른 TEG
    ("H_SRAM24", "SRAM25"),
    ("H_DFM01", "DFN01"),
])
def test_alias_matching_keeps_different_tegs_apart(monkeypatch, module_name, ref_name):
    """헐거운 규칙이 엉뚱한 TEG 를 집으면 안 된다 — 특히 숫자가 다르면 별개다."""
    ref = {ref_name: [{"x": 100.5, "y": 200.5, "w": 1.0, "h": 1.0,
                       "dir": "h", "top_cell": ""}]}
    monkeypatch.setattr(teg_check, "load_ref",
                        lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": teg_map._clean_check({}), "ebeam_scale": 1.0,
        "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda v, n: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0})
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    result = teg_check.inspect("P", f"#teg-map\nmodule {module_name} (100.5,200.5) !\n",
                               flat="h")

    assert result["teg"]["rows"][0]["ref_teg"] is None


def test_alias_matching_refuses_ambiguous_keys():
    """한 alias 키에 정답지 TEG 가 둘이면 매칭하지 않는다 (오매칭보다 미등록이 낫다)."""
    ref = {
        "QAB03": [{"x": 1.0, "y": 1.0, "w": 1.0, "h": 1.0, "dir": "h", "top_cell": ""}],
        "QA03B": [{"x": 2.0, "y": 2.0, "w": 1.0, "h": 1.0, "dir": "h", "top_cell": ""}],
    }
    assert teg_check.alias_key("QAB03") == teg_check.alias_key("QA03B")
    row = {"name": "H_QAB03", "candidates": ["H_QAB03"], "tail": ""}
    assert teg_check.resolve_ref_teg_alias(row, ref, {}) == (None, None, None)


@pytest.mark.parametrize(("module_name", "pattern", "replacement", "ref_name"), [
    ("H_ALPHA", r"^H_(.+)$", "${1}H01", "ALPHAH01"),
    ("DOE_SRAM24", r"^[A-Z]+_(SRAM\d+)$", "$1", "SRAM24"),
])
def test_user_extension_macro_maps_operator_variations_to_reference_name(
        module_name, pattern, replacement, ref_name):
    row = {"name": module_name, "candidates": [module_name], "tail": ""}
    ref = {ref_name: [{"x": 1.0, "y": 2.0, "w": 0.1, "h": 0.1,
                       "dir": "h", "top_cell": ""}]}
    rules = [{"name": "담당자 표기", "pattern": pattern,
              "replacement": replacement, "note": ""}]

    assert teg_check.resolve_ref_teg_macro(row, ref, {}, rules) == (
        ref_name, "teg", module_name, "담당자 표기",
    )


def test_extension_macro_config_preserves_builtin_switches_and_rejects_bad_regex():
    cleaned = teg_map.clean_extension_macros({
        "builtins": {"01strip": False, "alias": True},
        "rules": [{"name": "H 변환", "pattern": r"^H_(.+)$",
                   "replacement": "${1}H01", "note": "variation"}],
    }, strict=True)

    assert cleaned["builtins"]["01strip"] is False
    assert cleaned["builtins"]["reorder"] is True
    assert all(cleaned["builtins"][key] is True
               for key in teg_map.LEGACY_ALIAS_BUILTIN_KEYS)
    assert "alias" not in cleaned["builtins"]
    assert cleaned["rules"][0]["replacement"] == "${1}H01"
    with pytest.raises(ValueError, match="정규식/치환식 오류"):
        teg_map.clean_extension_macros({
            "rules": [{"name": "broken", "pattern": "([", "replacement": "$1"}],
        }, strict=True)
    with pytest.raises(ValueError, match="중첩 반복"):
        teg_map.clean_extension_macros({
            "rules": [{"name": "unsafe", "pattern": r"(A+)+$", "replacement": "$1"}],
        }, strict=True)


def test_main_token_takes_priority_over_exact_and_global_macro_matching():
    row = {
        "name": "H_ALPHA",
        "candidates": ["H_ALPHA", "MAIN01"],
        "tail": "H_ALPHA,MAIN01,H_PCHK",
    }
    ref = {
        "H_ALPHA": [{"x": 1.0, "y": 2.0}],
        "ALPHAH01": [{"x": 1.0, "y": 2.0}],
    }
    rules = [{"name": "담당자 H 표기", "pattern": r"^H_(.+)$",
              "replacement": "${1}H01", "note": ""}]

    assert teg_check.is_main_row(row) is True
    assert teg_check.resolve_ref_teg(row, ref, {}) == (None, None, None)
    assert teg_check.resolve_ref_teg_macro(row, ref, {}, rules) == (None, None, None, None)


def test_global_extension_macros_are_saved_under_db_root_teg_location(tmp_path, monkeypatch):
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    macros = {
        "builtins": {"reorder": False},
        "rules": [{"name": "공통 표기", "pattern": r"^OP_(.+)$",
                   "replacement": "$1", "note": "전 제품"}],
    }

    teg_map.save_cfg({"check": {"extension_macros": macros}})

    path = tmp_path / "teg_location" / "teg_map.json"
    stored = teg_map.load_json(path, {})
    assert path.is_file()
    assert stored["check"]["extension_macros"]["rules"][0]["name"] == "공통 표기"
    assert stored["check"]["extension_macros"]["builtins"]["reorder"] is False
    assert all("extension_macros" not in product
               for product in stored["check"]["products"].values())


def test_inspect_applies_global_macro_to_every_product_and_reports_its_name(monkeypatch):
    check = teg_map._clean_check({
        "extension_macros": {
            "builtins": {"reorder": False},
            "rules": [{"name": "담당자 H 표기", "pattern": r"^H_(.+)$",
                       "replacement": "${1}H01", "note": ""}],
        },
    })
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg.update({"check": check, "ebeam_scale": 1.0,
                "teg_default_w": 0.1, "teg_default_h": 0.1})
    ref = {"ALPHAH01": [{"x": 10.0, "y": 20.0, "w": 0.1, "h": 0.1,
                           "dir": "h", "top_cell": ""}]}
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check, "load_ref",
                        lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": False, "checked": False, "cells": [], "main_cells": [],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    for vehicle in ("P", "OTHER_PRODUCT"):
        result = teg_check.inspect(
            vehicle, "#teg-map\nmodule H_ALPHA (10,20) ! H_ALPHA,H_PCHK\n", flat="h",
        )
        row = result["teg"]["rows"][0]

        assert row["ref_teg"] == "ALPHAH01"
        assert row["match_rule"] == "macro"
        assert row["match_rule_label"] == "담당자 H 표기"
        assert row["light"] == "green"


def test_date_tokens_never_become_teg_names():
    """꼬리표 앞의 작업 날짜가 TEG 이름으로 잡히면 안 된다."""
    assert teg_check.is_date_token("2026-08-28")
    assert teg_check.is_date_token("2026.08.28")
    assert teg_check.is_date_token("2026/08/28")
    # 순수 숫자는 날짜로 보지 않는다 — 숫자로만 된 TEG 이름을 잃지 않기 위해서다.
    assert not teg_check.is_date_token("20260828")
    assert not teg_check.is_date_token("SRAM24")

    parsed = teg_check.parse_teg([
        "#teg-map",
        "module SRAM24 (100.5,200.5) ! 2026-08-28, MAIN01, DFM01",
    ])
    row = parsed[0]
    assert row["name"] == "SRAM24"          # 날짜가 아니라 module 뒤 이름
    assert "2026-08-28" not in row["candidates"]


def test_main_grid_with_zero_gap_is_centered_inside_die(monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg.update({"ebeam_scale": 1.0, "teg_default_w": 2.0, "teg_default_h": 2.0})
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (None, {}, "", "missing"))
    monkeypatch.setattr(teg_check, "_main_anchor_map", lambda vehicle: {
        "MAIN01": {"name": "MAIN01", "x": 10.0, "y": 20.0, "w": 5.0, "h": 5.0},
    })

    result = teg_check.build_main_grid("P", ["MAIN01"], gap_x=0, gap_y=0)

    main = result["mains"][0]
    assert (main["cols"], main["rows"]) == (2, 2)
    assert (main["edge_margin_x"], main["edge_margin_y"]) == (0.5, 0.5)
    assert [(cell["mm_x"], cell["mm_y"]) for cell in main["cells"]] == [
        (10.5, 20.5), (12.5, 20.5), (10.5, 22.5), (12.5, 22.5),
    ]


def test_main_inner_teg_name_skips_a_leading_date(monkeypatch):
    """MAIN 행 꼬리표 앞에 날짜가 있으면 MAIN 뒤 이름을 내부 TEG 로 잡는다."""
    monkeypatch.setattr(teg_check, "load_ref",
                        lambda vehicle: ({}, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": teg_map._clean_check({}), "ebeam_scale": 1.0,
        "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda v, n: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0})
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips",
                        lambda: ({"P": {"MAIN01": (9.0, 6.0)}}, None))

    result = teg_check.inspect(
        "P", "#teg-map\nmodule DUMMY (140.5,240.5) ! 2026-08-28,MAIN01,INNER01\n",
        flat="h")

    group = result["teg"]["main_groups"][0]
    assert group["group"] == "MAIN01"
    assert group["tegs"][0]["teg"] == "INNER01"


def test_main_token_anywhere_blocks_sl_exact_and_reorder_matching(monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg["ebeam_scale"] = 0.001
    cfg["teg_default_w"] = 0.1
    cfg["teg_default_h"] = 0.1
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    ref = {
        "DEVICE_H01": [{"x": 1, "y": 1, "w": 0.1, "h": 0.1,
                        "dir": "h", "top_cell": ""}],
        "DVC01H01": [{"x": 1, "y": 1, "w": 0.1, "h": 0.1,
                      "dir": "h", "top_cell": ""}],
    }
    monkeypatch.setattr(
        teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""),
    )
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": True, "checked": False, "cells": [], "main_cells": [],
    })
    seen_tokens = set()

    def targets(_vehicle, names):
        seen_tokens.update(names)
        return {"source": "default", "items": [], "matched": 0, "missing": 0, "total": 0}

    monkeypatch.setattr(teg_check._tm, "target_verification", targets)
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({
        "P": {"MAIN01": (9.0, 6.0)},
    }, None))
    source = (
        "#teg-map\n"
        "module H_DVC01 (1000,1000) ! H_DVC01,MAIN01,DEVICE_H01,H_PCHK\n"
    )

    parsed = teg_check.parse_teg(source.splitlines())
    assert teg_check.main_group_name(parsed[0]) == "MAIN01"
    assert teg_check.resolve_ref_teg(parsed[0], ref, {}) == (None, None, None)
    assert teg_check.resolve_ref_teg_reorder(parsed[0], ref, {}) == (None, None, None)

    result = teg_check.inspect("P", source, flat="h")

    assert result["teg"]["rows"] == []
    assert result["teg"]["excluded_main"] == 1
    assert result["teg"]["main_groups"][0]["group"] == "MAIN01"
    assert result["teg"]["main_groups"][0]["tegs"][0]["teg"] == "H_DVC01"
    assert "DEVICE_H01" not in seen_tokens
    assert "H_DVC01" not in seen_tokens


def test_mapfile_main_position_covers_inside_boundary_other_and_outside():
    cells = [
        {"name": "MAIN_M01", "x": 0.0, "y": 0.0, "w": 9.0, "h": 6.0},
        {"name": "MAIN_M02", "x": 12.0, "y": 0.0, "w": 9.0, "h": 6.0},
    ]

    assert teg_check.main_die_light(cells, "MAIN_M01", 1.0, 1.0, 0.1, 0.1) == (
        "yellow", "MAIN_M01 die 안")
    assert teg_check.main_die_light(cells, "MAIN_M01", 8.95, 1.0, 0.1, 0.1) == (
        "red", "MAIN_M01 경계 넘어감")
    assert teg_check.main_die_light(cells, "MAIN_M01", 13.0, 1.0, 0.1, 0.1) == (
        "red", "다른 MAIN(MAIN_M02) 안")
    assert teg_check.main_die_light(cells, "MAIN_M01", 24.0, 1.0, 0.1, 0.1) == (
        "red", "MAIN_M01 밖")

    adjacent = [
        {"name": "MAIN_M01", "x": 0.0, "y": 0.0, "w": 9.0, "h": 6.0},
        {"name": "MAIN_M02", "x": 9.0, "y": 0.0, "w": 9.0, "h": 6.0},
    ]
    assert teg_check.main_die_light(adjacent, "MAIN_M01", 8.95, 1.0, 0.1, 0.1) == (
        "red", "여러 MAIN(MAIN_M01, MAIN_M02)에 걸침")
    assert teg_check.main_die_light(
        adjacent, "MAIN_M01", 8.95, 1.0, 0.1, 0.1, tol=0.05,
    ) == ("yellow", "MAIN_M01 die 안")


def test_die_contact_and_configured_overlap_tolerance_are_allowed():
    cells = [{"name": "DIE01", "x": 0.0, "y": 0.0, "w": 10.0, "h": 10.0}]

    # 선만 정확히 맞닿는 것은 tol=0이어도 실제 면적 겹침이 아니다.
    assert teg_check.die_proximity(cells, 10.0, 1.0, 0.1, 0.1, tol=0.0) == (
        teg_check.DIE_OUT, [])
    assert not teg_check._overlaps_chip(cells, 10.0, 1.0, 0.1, 0.1, tol=0.0)

    # die 안쪽 0.002 mm 걸침은 0.003 mm 허용오차 안이라 정상이다.
    assert teg_check.die_proximity(cells, 9.998, 1.0, 0.1, 0.1, tol=0.003) == (
        teg_check.DIE_OUT, [])
    assert not teg_check._overlaps_chip(cells, 9.998, 1.0, 0.1, 0.1, tol=0.003)

    # 같은 조건에서 허용오차를 넘는 0.004 mm 침범만 경고한다.
    state, hit = teg_check.die_proximity(cells, 9.996, 1.0, 0.1, 0.1, tol=0.003)
    assert state == teg_check.DIE_IN
    assert hit == cells
    assert teg_check._overlaps_chip(cells, 9.996, 1.0, 0.1, 0.1, tol=0.003)


def test_mapfile_mixed_main_purposes_keep_ip_red_and_normal_inside_yellow(monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["check"] = teg_map._clean_check({})
    cfg["ebeam_scale"] = 0.001
    cfg["teg_default_w"] = 0.1
    cfg["teg_default_h"] = 0.1
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (None, {}, "", "정답지 없음"))
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": True,
        "checked": False,
        "cells": [],
        "main_cells": [
            {"name": "MAIN_M01", "x": 0, "y": 0, "w": 9, "h": 6},
            {"name": "MAIN_M02", "x": 12, "y": 0, "w": 9, "h": 6},
        ],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({
        "P": {"MAIN_M01": "IP", "MAIN_M02": "LOGIC"},
    }, None))
    source = (
        "#teg-map\n"
        "module MAIN_1 (0,0) ! MAIN_M01,H_PCHK\n"
        "module INNER_IP (1000,1000) ! MAIN_M01,INNER_IP,H_PCHK\n"
        "module MAIN_2 (12000,0) ! MAIN_M02,H_PCHK\n"
        "module INNER_NORMAL (13000,1000) ! MAIN_M02,INNER_NORMAL,H_PCHK\n"
    )

    result = teg_check.inspect("P", source, flat="h")
    groups = {group["group"]: group for group in result["teg"]["main_groups"]}

    assert groups["MAIN_M01"]["tegs"][1]["light"] == "red"
    assert groups["MAIN_M01"]["tegs"][1]["light_reason"] == "purpose IP — TEG 배치 금지"
    assert groups["MAIN_M02"]["tegs"][1]["light"] == "yellow"
    assert groups["MAIN_M02"]["tegs"][1]["light_reason"] == "MAIN_M02 die 안"


@pytest.mark.parametrize(
    ("flat", "expected"),
    [
        ("h", (98.0, 198.0)),
        ("v_R", (106.0, 196.0)),
        ("v_L", (94.0, 204.0)),
    ],
)
def test_global_module_calibration_round_trips_for_h_r_l(flat, expected):
    rules = {(flat, "T1"): (3.0, 4.0, "legacy")}
    absolute = teg_check.transform("T1", 1.0, 2.0, flat, 100.0, 200.0, rules)
    assert absolute == pytest.approx(expected)
    relative = teg_check.inverse_transform("T1", *absolute, flat, 100.0, 200.0, rules)
    assert relative == pytest.approx((1.0, 2.0))


@pytest.mark.parametrize(
    ("flat", "expected"),
    [("v_R", (111.0, 190.0)), ("v_L", (97.0, 200.0))],
)
def test_product_xy_is_applied_after_r_l_normalisation(flat, expected):
    absolute = teg_check.transform(
        "T1", 5.0, 7.0, flat, 100.0, 200.0,
        flat_correction=(4.0, -5.0),
    )
    assert absolute == pytest.approx(expected)
    relative = teg_check.inverse_transform(
        "T1", *absolute, flat, 100.0, 200.0,
        flat_correction=(4.0, -5.0),
    )
    assert relative == pytest.approx((5.0, 7.0))


def test_product_config_keeps_l_map_and_shape_rules():
    check = teg_map._clean_check({
        "mapfile_departments": [
            {"match": "DVC", "label": "DVC_TEAM"},
            {"match": "SRAM", "label": "SRAM_A"},
            "dvc",
            "",
        ],
        "first_pad_default": [1, 2],
        "pchk_first_pad_default": [5, 6],
        "first_pad_modules": [{"name": "V_PCHK", "dx": 3, "dy": 4}],
        "products": {"PROD_A": {
            "flat_corrections": {"v_L": [5, 6]},
            "first_pad_default": [7, 8],
            "first_pad_modules": [{"name": "SPECIAL", "dx": 9, "dy": 10}],
            "modules": [{"flat": "v_L", "name": "SPECIAL", "dx": 11, "dy": 12}],
        }},
    })
    product = check["products"]["PROD_A"]
    assert check["mapfile_departments"] == [
        {"match": "DVC", "label": "DVC_TEAM"},
        {"match": "SRAM", "label": "SRAM_A"},
    ]
    assert product["flat_corrections"]["v_L"] == [5.0, 6.0]
    assert product["first_pad_default"] == [7.0, 8.0]
    assert product["first_pad_modules"][0]["name"] == "SPECIAL"
    assert product["modules"][0]["flat"] == "v_L"
    assert teg_map.normalize_direction("", "VL_PCHK") == "v_L"
    assert teg_map.normalize_direction("", "V_L_SPECIAL") == "v_L"


def test_legacy_mapfile_department_names_keep_same_display_label():
    check = teg_map._clean_check({"mapfile_departments": ["DVC", "SRAM"]})

    assert check["mapfile_departments"] == [
        {"match": "DVC", "label": "DVC"},
        {"match": "SRAM", "label": "SRAM"},
    ]


def test_teg_reference_file_allowlist_validates_and_saves_with_conflict_guard(tmp_path, monkeypatch):
    files = {
        "layout_file": "Chip_Radius.csv",
        "main_chip_file": "Main_chip_info.csv",
        "teg_file": "Teg_location.csv",
    }
    (tmp_path / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\nP,0,0,0\n", encoding="utf-8"
    )
    (tmp_path / "Main_chip_info.csv").write_text(
        "vehicle,chip_name,chipsize_x,chipsize_y\nP,MAIN01,100,200\n", encoding="utf-8"
    )
    (tmp_path / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y,first_pad_dx,first_pad_dy\nP,V_PCHK,1,2,,\n", encoding="utf-8"
    )
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: files)
    monkeypatch.setattr(teg_map, "_snapshot_edm_file", lambda *args, **kwargs: None)

    listing = teg_map.reference_files_payload()
    listed = {item["kind"]: item for item in listing["files"]}
    assert listed["chip_radius"]["path"] == "Chip_Radius.csv"
    assert listed["teg_location"]["source"] == "db_root"
    assert listed["teg_location"]["editable"] is True

    payload = teg_map.read_reference_file("teg_location")
    assert payload["rows"] == [["P", "V_PCHK", "1", "2", "", ""]]
    result = teg_map.save_reference_file(
        "teg_location",
        ["vehicle", "teg", "ebeam_x", "ebeam_y", "first_pad_dx", "first_pad_dy"],
        [["P", "V_PCHK", "1", "2", "3", "4"]],
        "tester",
        expected_modified_ns=payload["source_modified_ns"],
    )
    assert result["rows"] == 1
    assert teg_map.read_reference_file("teg_location")["rows"][0][-2:] == ["3", "4"]
    with pytest.raises(ValueError, match="다른 사용자가"):
        teg_map.save_reference_file(
            "teg_location", ["vehicle", "teg", "ebeam_x", "ebeam_y"],
            [["P", "T1", "1", "2"]], "tester", expected_modified_ns=payload["source_modified_ns"]
        )


def test_teg_reference_file_rejects_non_numeric_required_coordinates(tmp_path, monkeypatch):
    path = tmp_path / "Teg_location.csv"
    path.write_text("vehicle,teg,ebeam_x,ebeam_y\nP,T1,1,2\n", encoding="utf-8")
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: {
        "layout_file": "Chip_Radius.csv", "main_chip_file": "Main_chip_info.csv",
        "teg_file": "Teg_location.csv",
    })
    monkeypatch.setattr(teg_map, "_snapshot_edm_file", lambda *args, **kwargs: None)
    with pytest.raises(ValueError, match="숫자가 아님"):
        teg_map.save_reference_file(
            "teg_location", ["vehicle", "teg", "ebeam_x", "ebeam_y"],
            [["P", "T1", "not-a-number", "2"]], "tester"
        )


PRODUCT_PASTE = """Item\tx\ty
unused field\t99\t98
Mask Map offset(Even)\t2\t3
RETICLE Chip Size(um)\t5000\t4000
Design S/L Size(um)\t400\t300
Shot\t4\t3
Shot Size(um)\t24000\t18000
Map offset(Odd)\t6\t8
"""


def test_product_paste_parser_uses_named_rows_and_requires_odd_offset():
    preview = teg_map.product_info_preview(PRODUCT_PASTE)
    assert preview["values"]["chip_size_x_um"] == 5000
    assert preview["values"]["map_offset_odd_x"] == 6
    assert preview["shot_count"] == 133
    assert preview["wafer_edge_mm"] == 147
    assert preview["radius_decimals"] >= 5
    shots = teg_map._product_shots(preview["values"], preview["wafer_edge_mm"])
    terms = teg_map._product_geometry_values(preview["values"])
    for shot in shots:
        mm_x = (shot["x"] - terms["cx"]) * terms["shot_w_mm"]
        mm_y = (shot["y"] - terms["cy"]) * terms["shot_h_mm"]
        assert math.hypot(abs(mm_x) + 12, abs(mm_y) + 9) <= 147 + 1e-10
    assert preview["display"] == {
        "mode": "grid", "cols": 4, "rows": 3,
        "chip_w": 5.0, "chip_h": 4.0, "gap_x": 0.4, "gap_y": 0.3,
    }
    with pytest.raises(ValueError, match=r"Map offset\(Odd\)"):
        teg_map.parse_product_info_table(PRODUCT_PASTE.replace("Map offset(Odd)", "Map offset(Even)"))


def test_product_paste_parser_matches_size_names_with_micro_units_and_prefixes():
    pasted = (PRODUCT_PASTE
              .replace("RETICLE Chip Size(um)", "Item RETICLE Chip Size(μm)")
              .replace("Design S/L Size(um)", "Item Design S/L Size(µm)")
              .replace("Shot Size(um)", "Item Shot Size(㎛)"))

    values = teg_map.parse_product_info_table(pasted)

    assert values["chip_size_x_um"] == 5000
    assert values["sl_size_x_um"] == 400
    assert values["shot_size_x_um"] == 24000


def test_rc_count_maps_full_grid_to_one_based_top_left_coordinates():
    pasted = PRODUCT_PASTE + "Item R/C Count\t13\t17\n"

    preview = teg_map.product_info_preview(pasted)
    assert preview["values"]["rc_cols"] == 13
    assert preview["values"]["rc_rows"] == 17

    terms = teg_map._product_geometry_values(preview["values"])
    shots = teg_map._product_shots(preview["values"], preview["wafer_edge_mm"])
    assert all(1 <= shot["x"] <= 13 and 1 <= shot["y"] <= 17 for shot in shots)

    # The 13x17 rectangle must sit on the physical columns that actually reach
    # the wafer, i.e. -6..+6 for this geometry. math.floor here used to shift the
    # whole grid to -7..+5, which left display column 1 permanently empty and gave
    # physical column +6 no public coordinate at all.
    assert (terms["grid_origin_x"], terms["grid_origin_y"]) == (-6, -8)

    payload = {
        "shots": shots,
        "geometry": {
            "fit": "radius",
            "cx": terms["display_cx"], "cy": terms["display_cy"],
            "kx": terms["shot_w_mm"], "ky": terms["shot_h_mm"],
            "pitch_x": 1, "pitch_y": 1,
            "shot_w_mm": terms["shot_w_mm"], "shot_h_mm": terms["shot_h_mm"],
            "wafer_radius_mm": 150,
            "grid_cols": 13, "grid_rows": 17,
        },
    }
    full = teg_map.full_shots_for_payload(payload)
    coords = {(shot["x"], shot["y"]) for shot in full}
    # R/C Count is just the rectangular grid size; cells whose shot rectangle
    # falls entirely outside the wafer circle must not be drawn. The four
    # corners of a 13x17 rectangle never reach a 150mm circle.
    assert (1, 1) not in coords and (13, 17) not in coords
    assert (13, 1) not in coords and (1, 17) not in coords
    # ...but every column and every row of the declared R/C rectangle must still
    # carry at least one shot. This is what pins the grid origin: an off-by-one
    # leaves an entire edge column/row empty.
    assert {x for x, _ in coords} == {float(i) for i in range(1, 14)}
    assert {y for _, y in coords} == {float(i) for i in range(1, 18)}
    # Independently counted from the geometry (13x17 grid, 24x18mm shots,
    # 150mm radius): 28 of the 221 cells lie fully outside the wafer.
    assert len(full) == 13 * 17 - 28

    radius = payload["geometry"]["wafer_radius_mm"]
    shot_w, shot_h = terms["shot_w_mm"], terms["shot_h_mm"]
    for shot in full:
        if not shot.get("synthetic"):
            continue
        dx = max(0.0, abs(shot["mm_x"]) - shot_w / 2)
        dy = max(0.0, abs(shot["mm_y"]) - shot_h / 2)
        assert math.hypot(dx, dy) < radius


def test_product_info_recovers_rc_count_from_preserved_raw_config(tmp_path, monkeypatch):
    path = tmp_path / teg_map.PRODUCT_INFO_FILE_NAME
    pd.DataFrame([{
        "vehicle": "P",
        "chip_size_x_um": 5000, "chip_size_y_um": 4000,
        "sl_size_x_um": 400, "sl_size_y_um": 300,
        "shot_cols": 4, "shot_rows": 3,
        "shot_size_x_um": 24000, "shot_size_y_um": 18000,
        "map_offset_odd_x": 6, "map_offset_odd_y": 8,
        "node_path": "",
        "raw_config_json": '[{"Item":"Item R/C Count","X":"13","Y":"17"}]',
    }]).to_csv(path, index=False)
    monkeypatch.setattr(teg_map, "product_info_path", lambda: path)

    products, _ = teg_map.load_product_info()
    row = products.iloc[0]

    assert row["rc_cols"] == 13
    assert row["rc_rows"] == 17
    geometry = teg_map.product_geometry("P")
    assert geometry["grid_cols"] == 13
    assert geometry["grid_rows"] == 17
    assert geometry["cx"] > 1
    assert geometry["cy"] > 1


def test_stored_rc_count_wins_and_is_never_mixed_with_recovered_pair(tmp_path, monkeypatch):
    """R/C Count는 한 쌍이다 — 저장값이 우선이고, 두 출처를 섞으면 안 된다.

    rc_cols/rc_rows 를 각각 fillna 하면 한쪽만 저장된 행에서 저장값과 raw 복구값이
    한 쌍으로 합쳐져, 어느 출처에도 없던 격자 크기(13x30 / 20x17)가 만들어졌다.
    """
    raw = '[{"Item":"Item R/C Count","X":"20","Y":"30"}]'
    base = {
        "chip_size_x_um": 5000, "chip_size_y_um": 4000,
        "sl_size_x_um": 400, "sl_size_y_um": 300,
        "shot_cols": 4, "shot_rows": 3,
        "shot_size_x_um": 24000, "shot_size_y_um": 18000,
        "map_offset_odd_x": 6, "map_offset_odd_y": 8,
        "node_path": "",
    }
    path = tmp_path / teg_map.PRODUCT_INFO_FILE_NAME
    pd.DataFrame([
        {"vehicle": "BOTH", "rc_cols": 13, "rc_rows": 17, "raw_config_json": raw, **base},
        {"vehicle": "ONLY_COLS", "rc_cols": 13, "rc_rows": "", "raw_config_json": raw, **base},
        {"vehicle": "ONLY_ROWS", "rc_cols": "", "rc_rows": 17, "raw_config_json": raw, **base},
        {"vehicle": "NEITHER", "rc_cols": "", "rc_rows": "", "raw_config_json": raw, **base},
        {"vehicle": "HALF_NO_RAW", "rc_cols": 13, "rc_rows": "", "raw_config_json": "", **base},
    ]).to_csv(path, index=False)
    monkeypatch.setattr(teg_map, "product_info_path", lambda: path)

    products, _ = teg_map.load_product_info()
    rc = {row["vehicle"]: (row["rc_cols"], row["rc_rows"]) for _, row in products.iterrows()}

    # 저장된 쌍이 온전하면 raw(20x30)로 덮어쓰지 않는다.
    assert rc["BOTH"] == (13, 17)
    # 반쪽만 저장된 행은 raw 쌍을 통째로 쓴다 — 섞인 13x30 / 20x17 이 나오면 안 된다.
    assert rc["ONLY_COLS"] == (20, 30)
    assert rc["ONLY_ROWS"] == (20, 30)
    assert rc["NEITHER"] == (20, 30)
    # 복구할 raw 가 없으면 남은 반쪽을 지우지 않는다 — 저장 시 다른 제품 행까지
    # 다시 쓰는 경로에서 CSV의 값이 영구히 사라지기 때문이다.
    assert rc["HALF_NO_RAW"][0] == 13
    assert rc["HALF_NO_RAW"][1] != rc["HALF_NO_RAW"][1]   # NaN
    # 그 반쪽은 geometry 로는 쓰이지 않는다 (쌍이 아니므로 무시).
    assert "rc_cols" not in teg_map._product_geometry_values(
        {**base, "rc_cols": 13, "rc_rows": float("nan")}
    )


def test_map_offset_um_is_converted_to_fractional_grid_center_not_shot_index():
    info = {
        "shot_size_x_um": 24000,
        "shot_size_y_um": 36000,
        "map_offset_odd_x": -1,
        "map_offset_odd_y": 16470,
    }

    terms = teg_map._product_geometry_values(info)
    assert terms["cx"] == pytest.approx(1 / 24000)
    assert terms["cy"] == pytest.approx(16470 / 36000)

    shots = teg_map._product_shots(info, 147)
    # Regression: the raw µm values must never become coordinates (-1, 16470).
    assert max(abs(shot["x"]) for shot in shots) < 20
    assert max(abs(shot["y"]) for shot in shots) < 20
    assert (-1.0, 16470.0) not in {(shot["x"], shot["y"]) for shot in shots}

    nearest = min(shots, key=lambda shot: shot["r"])
    mm_x = (nearest["x"] - terms["cx"]) * terms["shot_w_mm"]
    mm_y = (nearest["y"] - terms["cy"]) * terms["shot_h_mm"]
    assert (nearest["x"], nearest["y"]) == (0.0, 0.0)
    assert mm_x * 1000 == pytest.approx(-1)
    assert -mm_y * 1000 == pytest.approx(16470)

    # The generated Chip_Radius fallback (the previous location-view path)
    # must recover the same phase and real-center difference.
    fitted = teg_map.fit_geometry(
        [shot["x"] for shot in shots],
        [shot["y"] for shot in shots],
        [shot["r"] for shot in shots],
    )
    assert fitted is not None
    assert fitted["cx"] == pytest.approx(terms["cx"], abs=1e-10)
    assert fitted["cy"] == pytest.approx(terms["cy"], abs=1e-10)
    assert fitted["kx"] == pytest.approx(terms["shot_w_mm"], abs=1e-10)
    assert fitted["ky"] == pytest.approx(terms["shot_h_mm"], abs=1e-10)


def test_product_shots_include_a_shot_touching_the_147mm_boundary():
    half_shot_mm = 1.0
    boundary_x_mm = math.sqrt(147 ** 2 - half_shot_mm ** 2)
    touching_x = 73
    center_x = touching_x - ((boundary_x_mm - half_shot_mm) / 2.0)
    # cx = -offset_x_mm / shot_w_mm, so preserve the intended fractional
    # grid center while supplying the public Map offset in µm.
    offset_x_um = -center_x * 2000.0
    shots = teg_map._product_shots({
        "shot_size_x_um": 2000,
        "shot_size_y_um": 2000,
        "map_offset_odd_x": offset_x_um,
        "map_offset_odd_y": 0,
    }, 147)
    coordinates = {(shot["x"], shot["y"]) for shot in shots}
    assert (touching_x, 0.0) in coordinates
    assert (touching_x + 1, 0.0) not in coordinates


def test_inline_map_accepts_full_shot_coordinate_and_protects_referenced_map(tmp_path, monkeypatch):
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    payload = {
        "shots": [{"x": 1, "y": 1, "mm_x": 20, "mm_y": 20, "radius": math.hypot(20, 20)}],
        "geometry": {
            "fit": "radius", "cx": 0, "cy": 0, "kx": 20, "ky": 20,
            "pitch_x": 1, "pitch_y": 1, "shot_w_mm": 20, "shot_h_mm": 20,
            "wafer_radius_mm": 150, "wafer_edge_mm": 147,
        },
    }
    full = teg_map.full_shots_for_payload(payload)
    synthetic = next(shot for shot in full if shot.get("synthetic"))
    monkeypatch.setattr(teg_map, "map_payload", lambda _vehicle: payload)

    saved = teg_map.save_inline_map_table(
        "MAP_FULL", "PROD", [{"shot_x": synthetic["x"], "shot_y": synthetic["y"], "subitem_id": "P01"}],
        "tester", "full-shot 신규 등록",
    )
    assert saved["tables"][0]["shots"][0]["subitem_id"] == "P01"
    assert saved["tables"][0]["comment"] == "full-shot 신규 등록"
    with pytest.raises(ValueError, match="comment"):
        teg_map.save_inline_map_table(
            "MAP_NO_COMMENT", "PROD",
            [{"shot_x": synthetic["x"], "shot_y": synthetic["y"], "subitem_id": "P01"}], "tester",
        )

    (tmp_path / "inline_shot_matching.csv").write_text(
        "product,step_id,item_id,map_name\nPROD,STEP1,ITEM1,MAP_FULL\n", encoding="utf-8",
    )
    assert teg_map.load_inline_shot_matching()["rows"] == [{
        "product": "PROD", "step_id": "STEP1", "item_id": "ITEM1", "map_name": "MAP_FULL",
    }]
    with pytest.raises(ValueError, match="연결 행을 먼저 삭제"):
        teg_map.delete_inline_map_table("MAP_FULL")


def test_product_node_access_supports_users_and_future_sso_departments(monkeypatch):
    catalog = [
        {"vehicle": "P2", "node_path": "2나노 / 2나노A", "root_node": "2나노",
         "full_path": "2나노 / 2나노A / P2"},
        {"vehicle": "OPEN", "node_path": "미분류", "root_node": "미분류",
         "full_path": "미분류 / OPEN"},
    ]
    monkeypatch.setattr(teg_map, "product_catalog", lambda: catalog)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: {"node_access": {
        "2나노": {"users": ["alice"], "departments": ["R&D-2NM"]},
    }})

    assert teg_map.clean_node_path("2나노 - 2나노A") == "2나노 / 2나노A"
    assert teg_map.can_access_product({"role": "admin", "username": "root"}, "P2")
    assert teg_map.can_access_product({"role": "user", "username": "ALICE"}, "P2")
    assert teg_map.can_access_node_path({"role": "user", "username": "ALICE"}, "2나노 / 신규")
    assert teg_map.can_access_product({
        "role": "user", "username": "bob", "claims": {"department": "r&d-2nm"},
    }, "P2")
    assert not teg_map.can_access_product({"role": "user", "username": "bob"}, "P2")
    assert not teg_map.can_access_node_path({"role": "user", "username": "bob"}, "2나노 / 신규")
    assert teg_map.can_access_product({"role": "user", "username": "bob"}, "OPEN")


def test_product_catalog_reads_names_without_building_full_layout(tmp_path, monkeypatch):
    layout = tmp_path / "Chip_Radius.csv"
    layout.write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\n"
        "LEGACY,0,0,0\nLEGACY,1,0,20\n",
        encoding="utf-8",
    )
    product_info = tmp_path / teg_map.PRODUCT_INFO_FILE_NAME
    product_info.write_text(
        ",".join(teg_map.PRODUCT_INFO_COLUMNS) + "\n"
        "NEW,5000,4000,0,0,2,2,20000,15000,0,0,2나노 / 2나노A\n",
        encoding="utf-8",
    )
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg["layout_file"] = str(layout)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_map, "product_info_path", lambda: product_info)
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(
        teg_map,
        "load_layout",
        lambda: (_ for _ in ()).throw(AssertionError("selector must not build shot geometry")),
    )
    monkeypatch.setattr(
        teg_map,
        "load_product_info",
        lambda: (_ for _ in ()).throw(AssertionError("selector must not import pandas")),
    )

    catalog = teg_map.product_catalog()

    assert [row["vehicle"] for row in catalog] == ["NEW", "LEGACY"]
    assert catalog[0]["node_path"] == "2나노 / 2나노A"
    assert catalog[1]["node_path"] == "미분류"


def test_product_info_geometry_is_primary_and_chip_radius_is_fallback(tmp_path, monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg.update({"layout_file": "Chip_Radius.csv", "teg_file": "Teg_location.csv",
                "main_chip_file": "Main_chip_info.csv", "vehicles": {},
                "check": teg_map._clean_check({})})
    (tmp_path / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\nNEW,9,9,1\nOLD,0,0,0\n",
        encoding="utf-8",
    )
    pd.DataFrame([{
        "vehicle": "NEW", "chip_size_x_um": 5000, "chip_size_y_um": 4000,
        "sl_size_x_um": 400, "sl_size_y_um": 300, "shot_cols": 4, "shot_rows": 3,
        "shot_size_x_um": 24000, "shot_size_y_um": 18000,
        "map_offset_odd_x": 6, "map_offset_odd_y": 8,
    }]).to_csv(tmp_path / teg_map.PRODUCT_INFO_FILE_NAME, index=False)
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_map, "load_tegs", lambda: (None, tmp_path / "Teg_location.csv"))

    layout, _ = teg_map.load_layout()
    assert set(layout["vehicle"]) == {"NEW", "OLD"}
    assert set(layout[layout["vehicle"] == "NEW"]["layout_source"]) == {"product_info"}
    assert set(layout[layout["vehicle"] == "OLD"]["layout_source"]) == {"chip_radius"}
    payload = teg_map.map_payload("NEW")
    assert payload["layout_source"] == "product_info"
    assert payload["geometry"]["cx"] == pytest.approx(-6 / 24000)
    assert payload["geometry"]["cy"] == pytest.approx(8 / 18000)
    assert payload["geometry"]["map_offset_odd_x_um"] == 6
    assert payload["geometry"]["map_offset_odd_y_um"] == 8
    assert payload["geometry"]["shot_w_mm"] == 24
    assert payload["geometry"]["shot_h_mm"] == 18


def test_create_one_by_one_product_appends_reference_csvs_and_edm_versions(tmp_path, monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg.update({"layout_file": "Chip_Radius.csv", "teg_file": "Teg_location.csv",
                "main_chip_file": "Main_chip_info.csv", "vehicles": {},
                "check": teg_map._clean_check({})})
    (tmp_path / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\nOLD,0,0,0\n", encoding="utf-8")
    (tmp_path / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y,teg_w,teg_h,flat_zone,top_cell\n"
        "OLD,T0,0,0,100,50,h,TOP0\n", encoding="utf-8")
    (tmp_path / "Main_chip_info.csv").write_text(
        "vehicle,chip_name,chipsize_x,chipsize_y\nOLD,MAIN0,1000,2000\n", encoding="utf-8")
    saved_cfg = []
    versions = []
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "teg_dir", lambda: tmp_path / "teg_location")
    monkeypatch.setattr(teg_map, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_map, "vehicles", lambda: ["OLD"])
    monkeypatch.setattr(teg_map, "save_cfg", lambda patch: saved_cfg.append(patch) or cfg)
    monkeypatch.setattr(teg_map, "_snapshot_edm_file",
                        lambda path, username, note, backup=None: versions.append(path.name) or {"display_version": "v1.0"})
    one = PRODUCT_PASTE.replace("Shot\t4\t3", "Shot\t1\t1")
    result = teg_map.create_product_from_table(
        one, "NEW", [{"teg": "MAIN01", "top_cell": "TOP1", "direction": "V",
                      "ebeam_x": 10, "ebeam_y": 20, "teg_w": 100, "teg_h": 200}],
        {"chip_name": "MAIN01", "chipsize_x": 5000, "chipsize_y": 4000}, "tester",
        "2나노 / 2나노A",
    )
    assert result["one_by_one"] is True
    assert set(versions) == {
        "Chip_Radius.csv", "Teg_location.csv", "Main_chip_info.csv",
        teg_map.PRODUCT_INFO_FILE_NAME,
    }
    tegs = pd.read_csv(tmp_path / "Teg_location.csv")
    assert tegs.iloc[-1]["vehicle"] == "NEW"
    assert tegs.iloc[-1]["flat_zone"] == "V"
    mains = pd.read_csv(tmp_path / "Main_chip_info.csv")
    assert mains.iloc[-1].to_dict() == {
        "vehicle": "NEW", "chip_name": "MAIN01", "chipsize_x": 5000, "chipsize_y": 4000,
    }
    radii = pd.read_csv(tmp_path / "Chip_Radius.csv", dtype=str, keep_default_na=False)
    new_radii = radii[radii["Mask"] == "NEW"]
    assert len(new_radii) == result["shot_count"] == 133
    assert all(re.fullmatch(r"\d+\.\d{12}", value) for value in new_radii["Chip_Radius"])
    products = pd.read_csv(tmp_path / teg_map.PRODUCT_INFO_FILE_NAME)
    saved_product = products[products["vehicle"] == "NEW"].iloc[-1]
    assert saved_product["shot_size_x_um"] == 24000
    assert saved_product["shot_size_y_um"] == 18000
    assert saved_product["map_offset_odd_x"] == 6
    assert saved_product["map_offset_odd_y"] == 8
    assert saved_product["node_path"] == "2나노 / 2나노A"
    assert saved_cfg[-1]["vehicles"]["NEW"]["mode"] == "none"


def test_update_legacy_product_replaces_radius_rows_and_uses_exact_geometry(tmp_path, monkeypatch):
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg.update({
        "layout_file": "Chip_Radius.csv", "teg_file": "Teg_location.csv",
        "main_chip_file": "Main_chip_info.csv",
        "vehicles": {"LEGACY": {**teg_map.DEFAULT_VEHICLE_CFG, "mode": "grid"}},
        "product_nodes": {"LEGACY": "2나노 / 기존"},
        "check": teg_map._clean_check({}),
    })
    (tmp_path / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\n"
        "LEGACY,0,0,0\nLEGACY,1,0,20\nOTHER,3,4,5\n",
        encoding="utf-8",
    )
    saved_cfg = []
    edm_versions = []
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "teg_dir", lambda: tmp_path / "teg_location")
    monkeypatch.setattr(teg_map, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_map, "save_cfg", lambda patch: saved_cfg.append(patch) or cfg)
    monkeypatch.setattr(
        teg_map, "_snapshot_edm_file",
        lambda path, *args, **kwargs: edm_versions.append(path.name) or {"display_version": "v1.0"},
    )

    result = teg_map.update_legacy_product_from_table(PRODUCT_PASTE, "legacy", "tester")

    assert result["vehicle"] == "LEGACY"
    assert result["layout_source"] == "product_info"
    radii = pd.read_csv(tmp_path / "Chip_Radius.csv", dtype=str, keep_default_na=False)
    legacy = radii[radii["Mask"] == "LEGACY"]
    assert len(legacy) == result["shot_count"] == 133
    assert len(radii[radii["Mask"] == "OTHER"]) == 1
    assert all(re.fullmatch(r"-?\d+", value) for value in legacy["chip_x_adj"])
    assert all(re.fullmatch(r"-?\d+", value) for value in legacy["chip_y_adj"])
    assert all(re.fullmatch(r"\d+\.\d{12}", value) for value in legacy["Chip_Radius"])

    product = pd.read_csv(tmp_path / teg_map.PRODUCT_INFO_FILE_NAME).iloc[-1]
    assert product["vehicle"] == "LEGACY"
    assert product["shot_size_x_um"] == 24000
    assert product["shot_size_y_um"] == 18000
    assert product["map_offset_odd_x"] == 6
    assert product["map_offset_odd_y"] == 8
    assert product["node_path"] == "2나노 / 기존"
    assert saved_cfg[-1]["vehicles"]["LEGACY"]["mode"] == "grid"
    assert edm_versions.count("Chip_Radius.csv") == 1
    assert edm_versions.count(teg_map.PRODUCT_INFO_FILE_NAME) == 1

    current = teg_map.product_info_payload("legacy")
    assert current["exists"] is True
    assert current["rows"] == [
        {"Item": "unused field", "X": "99", "Y": "98"},
        {"Item": "Mask Map offset(Even)", "X": "2", "Y": "3"},
        {"Item": "RETICLE Chip Size(um)", "X": "5000", "Y": "4000"},
        {"Item": "Design S/L Size(um)", "X": "400", "Y": "300"},
        {"Item": "Shot", "X": "4", "Y": "3"},
        {"Item": "Shot Size(um)", "X": "24000", "Y": "18000"},
        {"Item": "Map offset(Odd)", "X": "6", "Y": "8"},
    ]

    payload = teg_map.map_payload("LEGACY")
    assert payload["layout_source"] == "product_info"
    assert payload["geometry"]["shot_w_mm"] == 24
    assert payload["geometry"]["shot_h_mm"] == 18
    assert payload["geometry"]["cx"] == pytest.approx(-6 / 24000)
    assert payload["geometry"]["cy"] == pytest.approx(8 / 18000)

    changed = PRODUCT_PASTE.replace("Shot Size(um)\t24000\t18000", "Shot Size(um)\t25000\t19000")
    changed = changed.replace("Map offset(Odd)\t6\t8", "Map offset(Odd)\t7\t9")
    second = teg_map.update_product_from_table(changed, "LEGACY", "tester")
    assert second["previous_layout_source"] == "product_info"
    products = pd.read_csv(tmp_path / teg_map.PRODUCT_INFO_FILE_NAME)
    assert len(products[products["vehicle"] == "LEGACY"]) == 1
    changed_product = products[products["vehicle"] == "LEGACY"].iloc[0]
    assert changed_product["shot_size_x_um"] == 25000
    assert changed_product["shot_size_y_um"] == 19000
    assert changed_product["map_offset_odd_x"] == 7
    assert changed_product["map_offset_odd_y"] == 9
    changed_radii = pd.read_csv(tmp_path / "Chip_Radius.csv", dtype=str, keep_default_na=False)
    assert len(changed_radii[changed_radii["Mask"] == "LEGACY"]) == second["shot_count"]
    assert len(changed_radii[changed_radii["Mask"] == "OTHER"]) == 1
    assert edm_versions.count("Chip_Radius.csv") == 2
    assert edm_versions.count(teg_map.PRODUCT_INFO_FILE_NAME) == 2


def test_product_identity_rename_propagates_to_all_teg_references_and_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(
        teg_map, "_snapshot_edm_file",
        lambda path, *args, **kwargs: {"display_version": "v1.0", "file": path.name},
    )
    (tmp_path / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\nOLD,0,0,0\nOTHER,1,1,2\n",
        encoding="utf-8",
    )
    (tmp_path / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y,teg_w,teg_h\n"
        "OLD,T0,0,0,100,50\nOTHER,T1,1,1,100,50\n",
        encoding="utf-8",
    )
    (tmp_path / "Main_chip_info.csv").write_text(
        "vehicle,chip_name,chipsize_x,chipsize_y\n"
        "OLD,MAIN0,1000,2000\nOTHER,MAIN1,1000,2000\n",
        encoding="utf-8",
    )
    pd.DataFrame([{
        "vehicle": "OLD", "chip_size_x_um": 5000, "chip_size_y_um": 4000,
        "sl_size_x_um": 400, "sl_size_y_um": 300, "shot_cols": 4, "shot_rows": 3,
        "shot_size_x_um": 24000, "shot_size_y_um": 18000,
        "map_offset_odd_x": 6, "map_offset_odd_y": 8,
        "node_path": "",
    }]).to_csv(tmp_path / teg_map.PRODUCT_INFO_FILE_NAME, index=False)

    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    cfg.update({
        "vehicles": {"OLD": {**teg_map.DEFAULT_VEHICLE_CFG, "mode": "grid"}},
        "product_nodes": {"OLD": ""},
        "check_targets": {"OLD": ["T0"]},
        "check": teg_map._clean_check({
            "products": {"OLD": {"flat_corrections": {"h": [1, 2]}}},
        }),
    })
    teg_map.teg_dir().mkdir(parents=True, exist_ok=True)
    teg_map.save_json(teg_map._cfg_path(), cfg, indent=2)
    inline_path = teg_map.inline_map_settings_path()
    inline_path.parent.mkdir(parents=True, exist_ok=True)
    teg_map.save_json(inline_path, {
        "version": 1,
        "tables": [{
            "table_name": "INLINE_A", "vehicle": "OLD", "shots": [
                {"shot_x": 0, "shot_y": 0, "subitem_id": "S0", "name": "S0"},
            ],
        }],
    }, indent=2)
    (tmp_path / teg_map.INLINE_SHOT_MATCHING_FILE_NAME).write_text(
        "product,step_id,item_id,map_name\nOLD,S1,I1,INLINE_A\n",
        encoding="utf-8",
    )

    result = teg_map.update_product_identity("old", "NEW", "2나노 / 2나노A", "tester")

    assert result["previous_vehicle"] == "OLD"
    assert result["vehicle"] == "NEW"
    assert result["node_path"] == "2나노 / 2나노A"
    for filename, column in (
        ("Chip_Radius.csv", "Mask"),
        ("Teg_location.csv", "vehicle"),
        ("Main_chip_info.csv", "vehicle"),
        (teg_map.PRODUCT_INFO_FILE_NAME, "vehicle"),
    ):
        values = pd.read_csv(tmp_path / filename)[column].astype(str).tolist()
        assert "OLD" not in values
        assert "NEW" in values
    product_info = pd.read_csv(tmp_path / teg_map.PRODUCT_INFO_FILE_NAME)
    assert product_info.iloc[0]["node_path"] == "2나노 / 2나노A"

    saved = teg_map.load_cfg()
    assert "OLD" not in saved["vehicles"]
    assert saved["vehicles"]["NEW"]["mode"] == "grid"
    assert saved["product_nodes"] == {"NEW": "2나노 / 2나노A"}
    assert saved["check_targets"] == {"NEW": ["T0"]}
    assert "OLD" not in saved["check"]["products"]
    assert "NEW" in saved["check"]["products"]
    inline = teg_map.load_inline_map_settings()
    assert inline["tables"][0]["vehicle"] == "NEW"
    assert teg_map.load_inline_shot_matching()["rows"][0]["product"] == "NEW"


def test_product_identity_classifies_legacy_product_without_product_info(tmp_path, monkeypatch):
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    monkeypatch.setattr(teg_map, "_snapshot_edm_file", lambda *args, **kwargs: None)
    (tmp_path / "Chip_Radius.csv").write_text(
        "Mask,chip_x_adj,chip_y_adj,Chip_Radius\nLEGACY,0,0,0\n",
        encoding="utf-8",
    )
    cfg = copy.deepcopy(teg_map.DEFAULT_CFG)
    teg_map.teg_dir().mkdir(parents=True, exist_ok=True)
    teg_map.save_json(teg_map._cfg_path(), cfg, indent=2)

    result = teg_map.update_product_identity("LEGACY", "LEGACY", "미세공정 / 평가", "tester")

    assert result["vehicle"] == "LEGACY"
    assert result["node_path"] == "미세공정 / 평가"
    assert teg_map.load_cfg()["product_nodes"] == {"LEGACY": "미세공정 / 평가"}
    assert not (tmp_path / teg_map.PRODUCT_INFO_FILE_NAME).exists()


def test_teg_reference_files_do_not_require_filebrowser_permission_but_require_teg():
    user = {"role": "user", "tabs": "dashboard,teg"}
    assert teg_router._require_teg_user(user) is user
    with pytest.raises(HTTPException) as exc:
        teg_router._require_teg_user({"role": "user", "tabs": "dashboard,filebrowser"})
    assert exc.value.status_code == 403


def test_filebrowser_teg_reference_scope_allows_all_teg_users_but_stays_allowlisted(tmp_path, monkeypatch):
    allowed = tmp_path / "Teg_location.csv"
    other = tmp_path / "other.csv"
    allowed.write_text("vehicle,teg,ebeam_x,ebeam_y\nP,T1,1,2\n", encoding="utf-8")
    other.write_text("a\n1\n", encoding="utf-8")
    user = {"username": "teg-user", "role": "user", "tabs": "dashboard,teg"}
    monkeypatch.setattr(filebrowser, "current_user", lambda request: user)
    monkeypatch.setattr(auth, "is_page_manager", lambda me, page: False)
    monkeypatch.setattr(
        filebrowser,
        "_resolve_base_file_for_version",
        lambda file: allowed if file == "Teg_location.csv" else other,
    )
    monkeypatch.setattr(teg_map, "reference_file_path", lambda kind: allowed)

    me, target = filebrowser._require_base_file_access(
        object(), "Teg_location.csv", "teg_reference"
    )
    assert me is user
    assert target == allowed.resolve()

    managed_by_page_user, managed_target = filebrowser._require_base_file_access(
        object(), "Teg_location.csv", "teg_reference", manage=True
    )
    assert managed_by_page_user is user
    assert managed_target == allowed.resolve()

    with pytest.raises(HTTPException) as exc:
        filebrowser._require_base_file_access(
            object(), "Teg_location.csv", "", manage=True
        )
    assert exc.value.status_code == 403

    user["tabs"] = "dashboard,filebrowser"
    with pytest.raises(HTTPException) as exc:
        filebrowser._require_base_file_access(
            object(), "Teg_location.csv", "teg_reference", manage=True
        )
    assert exc.value.status_code == 403

    user["tabs"] = "dashboard,teg"
    monkeypatch.setattr(auth, "is_page_manager", lambda me, page: True)
    with pytest.raises(HTTPException) as exc:
        filebrowser._require_base_file_access(
            object(), "other.csv", "teg_reference", manage=True
        )
    assert exc.value.status_code == 403


def test_build_mapfile_uses_real_pchk_shape_and_product_delta_for_l_map(monkeypatch):
    check = teg_map._clean_check({
        "pchk_first_pad_default": [5, 6],
        "products": {"P": {
            "first_pad_default": [2, 3],
            "flat_corrections": {"v_L": [4, -5]},
        }},
    })
    ref = {
        "VL_PCHK": [{"x": 100.0, "y": 200.0, "w": 10.0, "h": 20.0,
                      "dir": "v_L", "top_cell": "", "first_pad_dx": None, "first_pad_dy": None}],
        "T_L": [{"x": 130.0, "y": 250.0, "w": 8.0, "h": 30.0,
                  "dir": "v_L", "top_cell": "", "first_pad_dx": None, "first_pad_dy": None}],
    }
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": check, "ebeam_scale": 1.0, "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "teg_target_options", lambda vehicle: {
        "targets": ["T_L"], "source": "config", "tegs": [{"teg": "T_L", "direction": "v_L"}],
    })
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle: {"available": False})

    payload = teg_check.build_mapfile("P")
    block = next(item for item in payload["flats"] if item["flat"] == "v_L")
    row = block["rows"][0]
    assert block["pchk"]["rect"]["x"] == pytest.approx(0.0)
    assert block["pchk"]["rect"]["y"] == pytest.approx(0.0)
    assert block["pchk"]["rect"]["w_mm"] == pytest.approx(10.0)
    assert block["pchk"]["rect"]["h_mm"] == pytest.approx(20.0)
    assert row["rect"]["x"] == pytest.approx(30.0)
    assert row["rect"]["y"] == pytest.approx(50.0)
    assert row["first_pad_point"] == {"x": 30, "y": 50}
    absolute = teg_check.transform(
        "T_L", row["x"], row["y"], "v_L", 100, 200,
        flat_correction=(4, -5),
    )
    assert absolute == pytest.approx((130, 250))


def test_build_mapfile_omits_vertical_l_without_l_reference(monkeypatch):
    check = teg_map._clean_check({})
    ref = {
        "H_PCHK": [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0,
                    "dir": "h", "top_cell": ""}],
        "H_TEG": [{"x": 0.1234567890128, "y": 2.0, "w": 1.0, "h": 1.0,
                   "dir": "h", "top_cell": ""}],
    }
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": check, "ebeam_scale": 1.0, "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "teg_target_options", lambda vehicle: {
        "targets": ["H_TEG"], "source": "config",
        "tegs": [{"teg": "H_TEG", "direction": "h"}],
    })
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle: {
        "available": True, "shot_w_mm": 24.0, "shot_h_mm": 18.0,
        "geometry_source": "product_info", "mode": "none", "cells": [],
    })

    payload = teg_check.build_mapfile("P")
    assert [block["flat"] for block in payload["flats"]] == ["h", "v_R"]
    # Output precision follows the reference data: this H_TEG x carries 13
    # decimals, so the GEN_DECIMALS_MAX cap (6) applies. The old fixed
    # 12-decimal-place rounding exceeded float64's relative precision and let
    # artifacts through (98765.4 - 12345.6 -> 86419.79999999999).
    assert payload["flats"][0]["rows"][0]["x"] == pytest.approx(0.123457)
    assert payload["geometry_source"] == "product_info"
    # Sizes in mm keep the finer GEN_MM_DECIMALS — a TEG can be 0.1mm tall and
    # would collapse to zero at 2 decimals.
    assert payload["flats"][0]["shot"]["w_mm"] == pytest.approx(24.0)
    assert payload["flats"][0]["shot"]["h_mm"] == pytest.approx(18.0)


def test_mapfile_coordinates_have_no_float_subtraction_artifacts(monkeypatch):
    """정답지가 소수점 한자리면 생성 좌표에도 1e-10 같은 꼬리가 붙으면 안 된다.

    PCHK(12345.6)와 TEG(98765.4)는 둘 다 소수점 한자리인데, 예전 GEN_DECIMALS=12
    는 '소수점 12자리' 절대 기준이라 float64 상대 정밀도를 넘어
    98765.4 - 12345.6 = 86419.79999999999 를 그대로 통과시켰다.
    """
    check = teg_map._clean_check({})
    ref = {
        "H_PCHK": [{"x": 12345.6, "y": 7890.1, "w": 1.0, "h": 1.0,
                    "dir": "h", "top_cell": ""}],
        "H_TEG": [{"x": 98765.4, "y": 54321.9, "w": 1.0, "h": 1.0,
                   "dir": "h", "top_cell": ""}],
    }
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": check, "ebeam_scale": 1.0, "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "teg_target_options", lambda vehicle: {
        "targets": ["H_TEG"], "source": "config",
        "tegs": [{"teg": "H_TEG", "direction": "h"}],
    })
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle: {
        "available": False, "geometry_source": "none", "mode": "none", "cells": [],
    })

    row = teg_check.build_mapfile("P")["flats"][0]["rows"][0]
    # Exact equality, not approx — the whole point is that no tail survives.
    assert row["x"] == 86419.8
    assert row["y"] == 46431.8
    assert repr(row["x"]) == "86419.8"


def test_inspect_rounds_like_generator_and_applies_canonical_config_to_alias(monkeypatch):
    """생성 좌표를 별칭으로 올려도 같은 config로 0차이 판정해야 한다.

    정답지 H_QAB06을 Mapfile에서 QA06HB로 적으면 과거 검증은 모듈 보정을
    QA06HB 키로만 찾아 H_QAB06 config를 누락했고, float 뺄셈 꼬리도 그대로
    ΔX/ΔY에 노출했다.
    """
    check = teg_map._clean_check({
        "modules": [{"flat": "h", "name": "H_QAB06", "dx": 0.1, "dy": -0.2}],
        "products": {"P": {
            "flat_corrections": {"h": [0.2, -0.1]},
            "modules": [{"flat": "h", "name": "H_QAB06", "dx": 0.2, "dy": 0.0}],
        }},
    })
    ref = {
        "H_PCHK": [{"x": 12345.6, "y": 7890.1, "w": 1.0, "h": 1.0,
                    "dir": "h", "top_cell": ""}],
        "H_QAB06": [{"x": 98765.4, "y": 54321.9, "w": 1.0, "h": 1.0,
                     "dir": "h", "top_cell": ""}],
    }
    cfg = {"check": check, "ebeam_scale": 1.0,
           "teg_default_w": 3.0, "teg_default_h": 0.1}
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: cfg)
    monkeypatch.setattr(teg_check._tm, "teg_target_options", lambda vehicle: {
        "targets": ["H_QAB06"], "source": "config",
        "tegs": [{"teg": "H_QAB06", "direction": "h"}],
    })
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": False, "checked": False, "cells": [], "main_cells": [],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "config", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    generated = teg_check.build_mapfile("P")["flats"][0]["rows"][0]
    assert teg_check._coordinate_decimals(ref, check, teg_check.coordinate_context(check, "P")) == 1
    assert generated["x"] == 86419.9
    assert generated["y"] == 46431.7

    result = teg_check.inspect(
        "P",
        f"#teg-map\nmodule QA06HB ({generated['x']},{generated['y']}) ! QA06HB,H_PCHK\n",
        flat="h",
    )
    row = result["teg"]["rows"][0]

    assert row["ref_teg"] == "H_QAB06"
    assert row["coordinate_terms"]["config_name"] == "H_QAB06"
    assert row["coordinate_terms"]["global_module"] == [0.1, -0.2]
    assert row["coordinate_terms"]["product_module"] == [0.2, 0]
    assert row["coordinate_terms"]["flat_correction"] == [0.2, -0.1]
    assert row["calc_x"] == 98765.4
    assert row["calc_y"] == 54321.9
    assert row["dx"] == 0
    assert row["dy"] == 0
    assert row["status"] == "match"


def test_inspect_preserves_real_mapfile_fraction_beyond_integer_config(monkeypatch):
    """입력 자체가 더 세밀하면 config 자릿수로 뭉개지 않고 실제 차이를 보존한다."""
    check = teg_map._clean_check({})
    ref = {"H_TEG": [{"x": 10.0, "y": 20.0, "w": 1.0, "h": 1.0,
                      "dir": "h", "top_cell": ""}]}
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": check, "ebeam_scale": 1.0, "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle, extra_anchors=None: {
        "available": False, "checked": False, "cells": [], "main_cells": [],
    })
    monkeypatch.setattr(teg_check._tm, "target_verification", lambda vehicle, names: {
        "source": "default", "items": [], "matched": 0, "missing": 0, "total": 0,
    })
    monkeypatch.setattr(teg_check._tm, "load_main_chip_purposes", lambda: ({}, None))
    monkeypatch.setattr(teg_check._tm, "load_main_chips", lambda: ({}, None))

    row = teg_check.inspect(
        "P", "#teg-map\nmodule H_TEG (10.25,20.5) ! H_TEG,H_PCHK\n", flat="h",
    )["teg"]["rows"][0]

    assert row["dx"] == 0.25
    assert row["dy"] == 0.5
    assert row["status"] == "warning"


@pytest.mark.parametrize("ref_x, ref_y, pchk_x, pchk_y", [
    (98765.4, 54321.9, 12345.6, 7890.1),        # 소수점 1자리
    (98765.45, 54321.95, 12345.65, 7890.15),    # 소수점 2자리
    (350.0, 470.0, 100.0, 200.0),               # 정수
    (98765.4, 54321.0, 12345.6, 7890.0),        # 한쪽만 소수점
])
def test_generated_coordinates_never_exceed_teg_location_decimal_length(
        monkeypatch, ref_x, ref_y, pchk_x, pchk_y):
    """생성 좌표는 Teg_location 의 최대 소수 자릿수를 넘을 수 없다.

    좌표는 정답지 값과 오프셋의 덧셈·뺄셈뿐이라 수학적으로 자릿수가 늘 수 없다.
    더 긴 꼬리가 보이면 그건 전부 float64 이진 표현의 부산물이다.
    """
    check = teg_map._clean_check({})
    ref = {
        "H_PCHK": [{"x": pchk_x, "y": pchk_y, "w": 1.0, "h": 1.0,
                    "dir": "h", "top_cell": ""}],
        "H_TEG": [{"x": ref_x, "y": ref_y, "w": 1.0, "h": 1.0,
                   "dir": "h", "top_cell": ""}],
    }
    monkeypatch.setattr(teg_check, "load_ref", lambda vehicle: (ref, {}, "Teg_location.csv", ""))
    monkeypatch.setattr(teg_check._tm, "load_cfg", lambda: {
        "check": check, "ebeam_scale": 1.0, "teg_default_w": 3.0, "teg_default_h": 0.1,
    })
    monkeypatch.setattr(teg_check._tm, "teg_target_options", lambda vehicle: {
        "targets": ["H_TEG"], "source": "config",
        "tegs": [{"teg": "H_TEG", "direction": "h"}],
    })
    monkeypatch.setattr(teg_check, "_shot_info", lambda vehicle: {
        "available": False, "geometry_source": "none", "mode": "none", "cells": [],
    })

    source_dp = max(teg_check._decimal_places(value)
                    for value in (ref_x, ref_y, pchk_x, pchk_y))
    block = teg_check.build_mapfile("P")["flats"][0]
    emitted = [block["rows"][0][axis] for axis in ("x", "y")]
    emitted += [block["rows"][0]["ebeam_x"], block["rows"][0]["ebeam_y"]]
    emitted += [block["base"]["dx"], block["base"]["dy"]]

    for value in emitted:
        assert teg_check._decimal_places(value) <= source_dp, (
            f"{value!r} has more decimals than Teg_location's {source_dp}"
        )


def test_shot_info_prefers_exact_product_geometry_over_chip_radius_payload(monkeypatch):
    monkeypatch.setattr(teg_check._tm, "product_geometry", lambda vehicle: {
        "source": "product_info", "shot_w_mm": 24.123456789012,
        "shot_h_mm": 18.987654321098,
    })
    monkeypatch.setattr(teg_check._tm, "map_payload", lambda vehicle: {
        "geometry": {"fit": "radius", "shot_w_mm": 24.1235, "shot_h_mm": 18.9877},
        "display": {"mode": "none"},
    })

    info = teg_check._shot_info("P")
    assert info["geometry_source"] == "product_info"
    assert info["shot_w_mm"] == pytest.approx(24.123456789012)
    assert info["shot_h_mm"] == pytest.approx(18.987654321098)

    monkeypatch.setattr(teg_check._tm, "product_geometry", lambda vehicle: None)
    fallback = teg_check._shot_info("P")
    assert fallback["geometry_source"] == "chip_radius"
    assert fallback["shot_w_mm"] == pytest.approx(24.1235)
    assert fallback["shot_h_mm"] == pytest.approx(18.9877)
