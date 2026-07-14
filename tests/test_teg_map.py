# -*- coding: utf-8 -*-
"""TEG 위치 조회 (core/teg_map) — geometry fit·radius 계산·설정/그림 저장 검증."""
import math

import pytest

from core import teg_map


def _synthetic_layout(kx=24.0, ky=18.0, cx=6.5, cy=8.5, nx=12, ny=16):
    xs, ys, rs = [], [], []
    for x in range(1, nx + 1):
        for y in range(1, ny + 1):
            r = math.hypot((x - cx) * kx, (y - cy) * ky)
            if r <= 160.0:
                xs.append(float(x)); ys.append(float(y)); rs.append(r)
    return xs, ys, rs


def test_fit_geometry_recovers_parameters():
    xs, ys, rs = _synthetic_layout()
    geo = teg_map.fit_geometry(xs, ys, rs)
    assert geo is not None
    assert geo["cx"] == pytest.approx(6.5, abs=1e-6)
    assert geo["cy"] == pytest.approx(8.5, abs=1e-6)
    assert geo["kx"] == pytest.approx(24.0, abs=1e-6)
    assert geo["ky"] == pytest.approx(18.0, abs=1e-6)


def test_fit_geometry_rejects_degenerate():
    # radius 가 상수면(분산 0) fit 불가
    assert teg_map.fit_geometry([1, 2, 3, 4, 5, 6], [1, 1, 2, 2, 3, 3], [5] * 6) is None
    # 표본 부족
    assert teg_map.fit_geometry([1, 2], [1, 2], [3, 4]) is None


def test_grid_pitch():
    assert teg_map._grid_pitch([1, 2, 3, 4, 6]) == pytest.approx(1.0)  # 결측 격자 있어도 중앙값
    assert teg_map._grid_pitch([2, 4, 6]) == pytest.approx(2.0)
    assert teg_map._grid_pitch([3]) == pytest.approx(1.0)              # 단일값 폴백


def _write_demo_files(root, with_teg_size=False):
    kx, ky, cx, cy = 20.0, 15.0, 5.0, 6.0
    lines = ["Mask,chip_x_adj,chip_y_adj,Chip_Radius"]
    for x in range(1, 10):
        for y in range(1, 12):
            r = math.hypot((x - cx) * kx, (y - cy) * ky)
            if r <= 155.0:
                lines.append(f"VH_T,{x},{y},{r:.4f}")
    (root / "Chip_Radius.csv").write_text("\n".join(lines), encoding="utf-8")
    if with_teg_size:
        (root / "Teg_location.csv").write_text(
            "vehicle,teg,ebeam_x,ebeam_y,teg_w,teg_h\nVH_T,TEG_A,-5.0,-3.0,2.5,1.5\n",
            encoding="utf-8")
    else:
        (root / "Teg_location.csv").write_text(
            "vehicle,teg,ebeam_x,ebeam_y\nVH_T,TEG_A,-5.0,-3.0\nVH_T,TEG_B,2.0,4.0\n",
            encoding="utf-8")
    return kx, ky, cx, cy


@pytest.fixture()
def teg_env(tmp_path, monkeypatch):
    monkeypatch.setattr(teg_map.roots, "get_db_root", lambda: tmp_path)
    # 레거시 설정(실 저장소의 data/flow-data/teg_map.json) 이관이 끼어들지 않게 격리
    monkeypatch.setattr(teg_map, "LEGACY_CFG_PATH", tmp_path / "_no_legacy.json")
    return tmp_path


def test_map_payload_and_radius(teg_env):
    kx, ky, cx, cy = _write_demo_files(teg_env)
    payload = teg_map.map_payload("VH_T")
    geo = payload["geometry"]
    assert geo["fit"] == "radius"
    assert geo["kx"] == pytest.approx(kx, abs=1e-4)
    assert geo["ky"] == pytest.approx(ky, abs=1e-4)
    assert geo["wafer_edge_mm"] == pytest.approx(147.0)
    assert payload["tegs"][0]["teg"] == "TEG_A"
    # teg_w/teg_h 열 없음 → TEG 기본 사이즈로 채움
    assert payload["tegs"][0]["teg_w"] == pytest.approx(2.0)
    assert payload["tegs"][0]["teg_h"] == pytest.approx(2.0)
    assert payload["display"]["mode"] == "none"

    # shot (5,6) = wafer 중심 → TEG_A 좌하단 radius = hypot(-5,-3)
    tab = teg_map.teg_radius_table("VH_T", "TEG_A")
    center = [r for r in tab["rows"] if r["shot_x"] == 5 and r["shot_y"] == 6][0]
    assert center["radius"] == pytest.approx(math.hypot(5, 3), abs=1e-3)
    # 표는 radius 오름차순
    rads = [r["radius"] for r in tab["rows"]]
    assert rads == sorted(rads)


def test_teg_size_from_file_overrides_default(teg_env):
    _write_demo_files(teg_env, with_teg_size=True)
    payload = teg_map.map_payload("VH_T")
    assert payload["tegs"][0]["teg_w"] == pytest.approx(2.5)
    assert payload["tegs"][0]["teg_h"] == pytest.approx(1.5)


def test_map_payload_missing(teg_env):
    with pytest.raises(FileNotFoundError):
        teg_map.map_payload("VH_T")
    _write_demo_files(teg_env)
    with pytest.raises(LookupError):
        teg_map.map_payload("NO_SUCH_VEHICLE")
    with pytest.raises(LookupError):
        teg_map.teg_radius_table("VH_T", "NO_TEG")


def test_cfg_roundtrip_and_validation(teg_env):
    cfg = teg_map.save_cfg({
        "ebeam_scale": 0.001,
        "teg_default_w": 3.0,
        "teg_default_h": 1.2,
        "wafer_edge_mm": 145.0,
        "vehicles": {"VH_T": {"mode": "grid", "cols": 4, "rows": 3,
                              "chip_w": 5.0, "chip_h": 4.0, "gap_x": 0.2, "gap_y": 0.1}},
    })
    assert cfg["ebeam_scale"] == pytest.approx(0.001)
    assert cfg["wafer_edge_mm"] == pytest.approx(145.0)
    v = cfg["vehicles"]["VH_T"]
    assert v["mode"] == "grid" and v["cols"] == 4 and v["rows"] == 3
    assert v["chip_w"] == pytest.approx(5.0)
    assert v["gap_y"] == pytest.approx(0.1)
    # 설정 파일은 teg_location/ 폴더 안에 저장
    assert (teg_env / "teg_location" / "teg_map.json").is_file()
    # 재로딩에도 유지
    cfg2 = teg_map.load_cfg()
    assert cfg2["vehicles"]["VH_T"]["cols"] == 4
    # ebeam scale·기본 사이즈는 TEG payload 에 반영
    _write_demo_files(teg_env)
    payload = teg_map.map_payload("VH_T")
    assert payload["tegs"][0]["ebeam_x"] == pytest.approx(-0.005)
    assert payload["tegs"][0]["teg_w"] == pytest.approx(3.0)
    assert payload["display"]["mode"] == "grid"
    # vehicle 설정 삭제 (None) → 키 제거
    cfg3 = teg_map.save_cfg({"vehicles": {"VH_T": None}})
    assert "VH_T" not in cfg3["vehicles"]
    with pytest.raises(ValueError):
        teg_map.save_cfg({"ebeam_scale": 0})
    with pytest.raises(ValueError):
        teg_map.save_cfg({"teg_default_w": 0})


def test_legacy_cfg_migration(teg_env, tmp_path, monkeypatch):
    import json
    legacy = tmp_path / "legacy_teg_map.json"
    legacy.write_text(json.dumps({
        "layout_file": "My_Chip.csv",
        "ebeam_scale": 0.5,
        "chip_grids": {"VH_T": {"cols": 6, "rows": 2}},
    }), encoding="utf-8")
    monkeypatch.setattr(teg_map, "LEGACY_CFG_PATH", legacy)
    cfg = teg_map.load_cfg()
    assert cfg["layout_file"] == "My_Chip.csv"
    assert cfg["ebeam_scale"] == pytest.approx(0.5)
    assert cfg["vehicles"]["VH_T"]["mode"] == "grid"
    assert cfg["vehicles"]["VH_T"]["cols"] == 6
    # 이관 후 새 위치에 저장됨
    assert (teg_env / "teg_location" / "teg_map.json").is_file()


def test_duplicate_teg_auto_numbering(teg_env):
    """동명 TEG 가 2 개 이상이면 _1, _2, … 접미사가 자동으로 붙는다."""
    kx, ky, cx, cy = 20.0, 15.0, 5.0, 6.0
    lines = ["Mask,chip_x_adj,chip_y_adj,Chip_Radius"]
    for x in range(1, 10):
        for y in range(1, 12):
            r = math.hypot((x - cx) * kx, (y - cy) * ky)
            if r <= 155.0:
                lines.append(f"VH_T,{x},{y},{r:.4f}")
    (teg_env / "Chip_Radius.csv").write_text("\n".join(lines), encoding="utf-8")
    # 동명 TEG 3 개 + 고유 TEG 1 개
    (teg_env / "Teg_location.csv").write_text(
        "vehicle,teg,ebeam_x,ebeam_y\n"
        "VH_T,TEG_A,-5.0,-3.0\n"
        "VH_T,TEG_A,2.0,4.0\n"
        "VH_T,TEG_A,6.0,1.0\n"
        "VH_T,TEG_B,0.5,0.5\n",
        encoding="utf-8")
    payload = teg_map.map_payload("VH_T")
    names = [t["teg"] for t in payload["tegs"]]
    assert names == ["TEG_A_1", "TEG_A_2", "TEG_A_3", "TEG_B"]
    # 넘버링된 이름으로 radius 조회 가능
    tab = teg_map.teg_radius_table("VH_T", "TEG_A_1")
    assert tab["teg"] == "TEG_A_1"
    rads = [r["radius"] for r in tab["rows"]]
    assert rads == sorted(rads)


def test_image_save_and_delete(teg_env):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    name = teg_map.save_image("VH_T", png, ".png")
    assert name == "VH_T.png"
    assert (teg_env / "teg_location" / "VH_T.png").read_bytes() == png
    assert teg_map.load_cfg()["vehicles"]["VH_T"]["image"] == "VH_T.png"
    assert teg_map.image_path("VH_T") is not None
    # 확장자 교체 시 이전 파일 정리
    teg_map.save_image("VH_T", png, ".webp")
    assert not (teg_env / "teg_location" / "VH_T.png").exists()
    assert (teg_env / "teg_location" / "VH_T.webp").exists()
    # 삭제
    teg_map.delete_image("VH_T")
    assert teg_map.image_path("VH_T") is None
    assert not (teg_env / "teg_location" / "VH_T.webp").exists()
    # 검증 실패 케이스
    with pytest.raises(ValueError):
        teg_map.save_image("VH_T", png, ".exe")
    with pytest.raises(ValueError):
        teg_map.save_image("VH_T", b"", ".png")
