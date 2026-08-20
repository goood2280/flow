import copy

import pandas as pd
import pytest

from core import teg_check
from core import teg_map
from core import auth
from fastapi import HTTPException
from routers import filebrowser
from routers import teg_map as teg_router


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
    assert product["flat_corrections"]["v_L"] == [5.0, 6.0]
    assert product["first_pad_default"] == [7.0, 8.0]
    assert product["first_pad_modules"][0]["name"] == "SPECIAL"
    assert product["modules"][0]["flat"] == "v_L"
    assert teg_map.normalize_direction("", "VL_PCHK") == "v_L"
    assert teg_map.normalize_direction("", "V_L_SPECIAL") == "v_L"


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
    with pytest.raises(ValueError, match="숫자가 아님"):
        teg_map.save_reference_file(
            "teg_location", ["vehicle", "teg", "ebeam_x", "ebeam_y"],
            [["P", "T1", "not-a-number", "2"]], "tester"
        )


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
