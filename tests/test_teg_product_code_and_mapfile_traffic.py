import json
import concurrent.futures
import time
import os
import pytest
from pathlib import Path
from unittest.mock import patch

from core import teg_map
from core import mapfile_traffic


def test_file_version_tracks_content_not_download_timestamp(tmp_path):
    path = tmp_path / "PA10_recipe.unusual"
    path.write_bytes(b"first")
    first = mapfile_traffic.file_signature(path)
    stamp = path.stat().st_mtime_ns
    os.utime(path, ns=(stamp + 1000000000, stamp + 1000000000))
    assert mapfile_traffic.file_signature(path) == first
    path.write_bytes(b"other")
    os.utime(path, ns=(stamp, stamp))
    assert mapfile_traffic.file_signature(path) != first


def test_product_codes_save_and_load(tmp_path):
    # Mock cfg path
    fake_cfg_path = tmp_path / "teg_map.json"
    with patch.object(teg_map, "_cfg_path", return_value=fake_cfg_path):
        # 1. Save product codes
        res = teg_map.save_cfg({"product_codes": {"PROD_A": "PA100", "PROD_B": "PB200"}})
        assert res["product_codes"]["PROD_A"] == "PA100"
        assert res["product_codes"]["PROD_B"] == "PB200"

        # 2. Load cfg verifies persistence
        loaded = teg_map.load_cfg()
        assert loaded["product_codes"]["PROD_A"] == "PA100"
        assert loaded["product_codes"]["PROD_B"] == "PB200"

        # 3. Update single vehicle code
        res2 = teg_map.save_cfg({"product_codes": {"PROD_A": "PA999"}})
        assert res2["product_codes"]["PROD_A"] == "PA999"
        assert res2["product_codes"]["PROD_B"] == "PB200"

        # 4. Remove code by passing empty string
        res3 = teg_map.save_cfg({"product_codes": {"PROD_B": ""}})
        assert "PROD_B" not in res3["product_codes"]


def test_mapfile_traffic_discovery_and_caching(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()

    # Create dummy mapfiles
    f1 = mapfile_dir / "PA100_v1.txt"
    f1.write_text("MAPFILE CONTENT 1", encoding="utf-8")
    
    f2 = mapfile_dir / "PA100_v2.txt"
    f2.write_text("MAPFILE CONTENT 2", encoding="utf-8")

    other_file = mapfile_dir / "OTHER_v1.txt"
    other_file.write_text("OTHER CONTENT", encoding="utf-8")

    cache_file = tmp_path / "cache.json"

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=cache_file), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA100"), \
         patch.object(mapfile_traffic._tc, "inspect") as mock_inspect:
        
        mock_inspect.return_value = {
            "flat": {"detected": "Horizontal"},
            "teg": {
                "summary": {"match": 5, "warning": 0, "mismatch": 0},
                "targets": {"matched": 5, "missing": 0, "total": 5},
                "rows": [
                    {"name": f"H_TEG{i}", "light": "green", "status": "match"}
                    for i in range(5)
                ],
            },
        }

        # First run: discovers 2 files matching PA100 prefix
        res = mapfile_traffic.inspect_mapfiles_for_product("PROD_A", force=False)
        assert res["summary"]["total_files"] == 2
        assert len(res["files"]) == 2
        assert res["summary"]["green_files"] == 2
        assert res["overall_light"] == "green"
        assert mock_inspect.call_count == 2

        # Second run without change: should use cache, mock_inspect not called again!
        mock_inspect.reset_mock()
        res2 = mapfile_traffic.inspect_mapfiles_for_product("PROD_A", force=False)
        assert res2["summary"]["total_files"] == 2
        assert mock_inspect.call_count == 0  # 0 calls because signatures matched!
        assert all(f.get("is_cached") for f in res2["files"])

        # Modify one file: only that file should be re-inspected (call_count == 1)
        f1.write_text("MAPFILE CONTENT 1 MODIFIED", encoding="utf-8")
        res3 = mapfile_traffic.inspect_mapfiles_for_product("PROD_A", force=False)
        assert mock_inspect.call_count == 1

        # A changed signature is cached after that one inspection too.
        mock_inspect.reset_mock()
        res4 = mapfile_traffic.inspect_mapfiles_for_product("PROD_A", force=False)
        assert mock_inspect.call_count == 0
        assert all(f.get("is_cached") for f in res4["files"])


def test_mapfile_product_prefix_accepts_all_suffixes_and_file_extensions(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()
    expected = {
        "PA10-layout.map": "FIRST",
        "PA100_future_payload.bin": "SECOND",
        "pa10_notes.no_extension_rule": "THIRD",
    }
    for filename, content in expected.items():
        (mapfile_dir / filename).write_text(content, encoding="utf-8")
    (mapfile_dir / "PA1_other.txt").write_text("OTHER", encoding="utf-8")
    (mapfile_dir / "$PA10_temp.txt").write_text("TEMP", encoding="utf-8")

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA10"):
        code, paths = mapfile_traffic.list_mapfiles_for_product("PROD_A")
        assert code == "PA10"
        assert {path.name for path in paths} == set(expected)
        for path in paths:
            assert mapfile_traffic.read_mapfile_text(path.name) == expected[path.name]


def test_mapfile_dir_fallback_recognizes_non_txt_mapfiles(tmp_path):
    db_root = tmp_path / "db"
    project_root = tmp_path / "project"
    fallback = project_root / "B" / "mapfile"
    fallback.mkdir(parents=True)
    (fallback / "PA10_payload.map").write_text("MAP", encoding="utf-8")

    with patch.object(mapfile_traffic.roots, "get_db_root", return_value=db_root), \
         patch.object(mapfile_traffic, "_PROJECT_ROOT", project_root):
        assert mapfile_traffic.get_mapfile_dir() == fallback


def test_mapfile_errors_and_gray_results_never_aggregate_to_green(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()
    (mapfile_dir / "PA10_good.any").write_text("GOOD", encoding="utf-8")
    (mapfile_dir / "PA10_bad.odd").write_text("BAD", encoding="utf-8")
    cache_file = tmp_path / "cache.json"

    def inspect(_vehicle, content, flat=None):
        if content == "BAD":
            raise ValueError("invalid mapfile")
        return {
            "flat": {"detected": "Horizontal"},
            "teg": {
                "summary": {"match": 1},
                "targets": {"matched": 1, "missing": 0, "total": 1},
                "rows": [{"name": "H_TEG", "light": "green"}],
            },
        }

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=cache_file), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA10"), \
         patch.object(mapfile_traffic._tc, "inspect", side_effect=inspect) as mock_inspect:
        result = mapfile_traffic.inspect_mapfiles_for_product("PROD_A")
        assert result["overall_light"] == "gray"
        assert result["summary"]["green_files"] == 1
        assert result["summary"]["gray_files"] == 1
        assert {f["filename"]: f["traffic_light"] for f in result["files"]} == {
            "PA10_bad.odd": "gray",
            "PA10_good.any": "green",
        }
        assert next(f for f in result["files"] if f["status"] == "error")["overall_light"] == "gray"

        mock_inspect.reset_mock()
        cached = mapfile_traffic.inspect_mapfiles_for_product("PROD_A")
        assert mock_inspect.call_count == 0
        assert cached["overall_light"] == "gray"


def test_all_gray_mapfiles_aggregate_to_gray(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()
    (mapfile_dir / "PA10_waiting.raw").write_text("WAIT", encoding="utf-8")

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=tmp_path / "cache.json"), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA10"), \
         patch.object(mapfile_traffic._tc, "inspect", return_value={
             "flat": {},
             "teg": {"rows": [{"name": "UNKNOWN", "light": "gray"}]},
         }):
        result = mapfile_traffic.inspect_mapfiles_for_product("PROD_A")

    assert result["summary"]["gray_files"] == 1
    assert result["overall_light"] == "gray"


def test_concurrent_requests_inspect_an_unchanged_file_once(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()
    (mapfile_dir / "PA10_shared.payload").write_text("MAP", encoding="utf-8")
    cache_file = tmp_path / "cache.json"

    def slow_inspect(_vehicle, _content, flat=None):
        time.sleep(0.05)
        return {
            "flat": {},
            "teg": {"rows": [{"name": "H_TEG", "light": "green"}]},
        }

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=cache_file), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA10"), \
         patch.object(mapfile_traffic._tc, "inspect", side_effect=slow_inspect) as mock_inspect:
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(
                lambda _: mapfile_traffic.inspect_mapfiles_for_product("PROD_A"),
                range(6),
            ))

    assert mock_inspect.call_count == 1
    assert sum(not result["files"][0]["is_cached"] for result in results) == 1
    assert all(result["overall_light"] == "green" for result in results)


def test_mapfile_traffic_red_light_and_issues(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()

    f1 = mapfile_dir / "PA100_err.txt"
    f1.write_text("MAPFILE CONTENT ERR", encoding="utf-8")

    cache_file = tmp_path / "cache.json"

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=cache_file), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA100"), \
         patch.object(mapfile_traffic._tc, "inspect") as mock_inspect:
        
        mock_inspect.return_value = {
            "flat": {"detected": "Horizontal"},
            "teg": {
                "summary": {"match": 4, "warning": 0, "mismatch": 1},
                "targets": {"matched": 4, "missing": 0, "total": 5},
                "rows": [
                    {"name": "H_TEG01", "light": "green", "status": "match"},
                    {"name": "H_TEG_BAD", "light": "red", "status": "mismatch", "reason": "X/Y delta mismatch"},
                ],
            },
        }

        res = mapfile_traffic.inspect_mapfiles_for_product("PROD_A", force=True)
        assert res["overall_light"] == "red"
        assert res["summary"]["red_files"] == 1
        assert res["files"][0]["issues"][0]["teg_name"] == "H_TEG_BAD"
        assert res["files"][0]["issues"][0]["reason"] == "X/Y delta mismatch"

    # Test read_mapfile_text
    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir):
        content = mapfile_traffic.read_mapfile_text("PA100_err.txt")
        assert content == "MAPFILE CONTENT ERR"


def test_routers_teg_map_endpoints(tmp_path):
    from routers import teg_map as teg_router

    admin_user = {"username": "admin", "role": "admin"}
    normal_user = {"username": "user1", "role": "user"}

    fake_cfg_path = tmp_path / "teg_map.json"
    cache_file = tmp_path / "cache.json"
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()
    (mapfile_dir / "CODE1_a.txt").write_text("MAP 1", encoding="utf-8")

    with patch.object(teg_map, "_cfg_path", return_value=fake_cfg_path), \
         patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=cache_file), \
         patch.object(teg_router, "_require_product_access"):

        # 1. PUT /product-access saves product_codes
        req = teg_router.ProductAccessReq(
            product_nodes={"VH_1": "2나노"},
            product_codes={"VH_1": "CODE1"},
            node_access={},
        )
        put_res = teg_router.product_access_put(req, admin=admin_user)
        assert put_res["ok"] is True
        assert put_res["product_codes"]["VH_1"] == "CODE1"

        # 2. GET /product-access returns product_codes
        with patch("routers.auth.read_users", return_value=[]):
            get_res = teg_router.product_access_get(_admin=admin_user)
            assert get_res["ok"] is True
            assert get_res["product_codes"]["VH_1"] == "CODE1"

        # 3. GET /mapfile-traffic returns inspection result
        with patch.object(mapfile_traffic._tc, "inspect") as mock_tc:
            mock_tc.return_value = {
                "flat": {"detected": "Horizontal"},
                "teg": {"summary": {}, "targets": {}, "rows": []},
            }
            traffic_res = teg_router.mapfile_traffic_get(vehicle="VH_1", force=False, user=normal_user)
            assert traffic_res["ok"] is True
            assert traffic_res["product_code"] == "CODE1"
            assert traffic_res["summary"]["total_files"] == 1
            assert traffic_res["files"][0]["filename"] == "CODE1_a.txt"

        # 4. GET /mapfile-traffic/content returns text
        content_res = teg_router.mapfile_traffic_content_get(filename="CODE1_a.txt", user=normal_user)
        assert content_res["ok"] is True
        assert content_res["content"] == "MAP 1"


def test_mapfile_traffic_sl_and_main_breakdown(tmp_path):
    mapfile_dir = tmp_path / "mapfile"
    mapfile_dir.mkdir()

    f1 = mapfile_dir / "PA100_split.txt"
    f1.write_text("MAPFILE SPLIT", encoding="utf-8")

    cache_file = tmp_path / "cache.json"

    with patch.object(mapfile_traffic, "get_mapfile_dir", return_value=mapfile_dir), \
         patch.object(mapfile_traffic, "get_traffic_cache_path", return_value=cache_file), \
         patch.object(mapfile_traffic, "get_product_code_for_vehicle", return_value="PA100"), \
         patch.object(mapfile_traffic._tc, "inspect") as mock_inspect:

        # S/L is GREEN (3 matched, 0 warning, 0 mismatch)
        # Main is RED (1 mismatch)
        mock_inspect.return_value = {
            "flat": {"detected": "Horizontal"},
            "teg": {
                "summary": {"match": 3, "warning": 0, "mismatch": 0},
                "targets": {"matched": 3, "missing": 0, "total": 3},
                "rows": [
                    {"name": f"H_SL_{i}", "light": "green", "status": "match"}
                    for i in range(3)
                ],
                "main_rows": [
                    {"name": "M_DIE_01", "light": "red", "status": "mismatch", "reason": "Die boundary overflow"},
                ],
                "main_groups": {
                    "MAIN01": {"purpose_warning": False, "chip_overlap": False}
                },
            },
        }

        res = mapfile_traffic.inspect_mapfiles_for_product("PROD_A", force=True)
        assert res["overall_light"] == "red"
        file_info = res["files"][0]
        assert file_info["sl"]["light"] == "green"
        assert file_info["sl"]["green"] == 3
        assert file_info["main"]["light"] == "red"
        assert file_info["main"]["red"] == 1
        assert any(i["category"] == "Main" and "Die boundary" in i["reason"] for i in file_info["main"]["issues"])
        assert res["summary"]["sl_green_files"] == 1
        assert res["summary"]["main_red_files"] == 1


def test_filebrowser_hides_mapfile_and_confidential():
    from routers import filebrowser

    assert filebrowser._is_filebrowser_hidden_dir_name("mapfile") is True
    assert filebrowser._is_filebrowser_hidden_dir_name("MAPFILE") is True
    assert filebrowser._is_filebrowser_hidden_dir_name("confidential") is True
    assert filebrowser._is_filebrowser_hidden_dir_name("credential") is True
    assert filebrowser._is_filebrowser_hidden_dir_name("teg_location") is True
    assert filebrowser._is_filebrowser_hidden_dir_name("normal_folder") is False
