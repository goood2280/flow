import concurrent.futures
import hashlib
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from core import mapfile_review


@pytest.fixture(autouse=True)
def isolated_review_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(mapfile_review.traffic, "get_traffic_cache_path", lambda: tmp_path / "traffic.json")
    with mapfile_review._guard:
        mapfile_review._pending.clear()
    yield
    with mapfile_review._guard:
        mapfile_review._pending.clear()


def _version(raw: bytes, name: str = "PA100_a.map"):
    return f"{name}:sha256:{hashlib.sha256(raw).hexdigest()}"


def test_summary_is_compact_and_submits_background_refresh(tmp_path):
    cache = tmp_path / "traffic.json"
    with patch.object(mapfile_review.traffic, "get_traffic_cache_path", return_value=cache), \
         patch.object(mapfile_review._pool, "submit") as submit, \
         patch.object(mapfile_review.traffic, "file_signature") as sig, \
         patch.object(mapfile_review.traffic._tc, "inspect") as inspect:
        result = mapfile_review.get_summary("VEH_A")
    submit.assert_called_once()
    sig.assert_not_called()
    inspect.assert_not_called()
    assert result["overall_light"] == "gray"
    assert result["refreshing"] is True

    published = mapfile_review.publish_snapshot({
        "vehicle": "VEH_A", "product_code": "PA100", "overall_light": "red",
        "summary": {"red_files": 1}, "files": [{"filename": "x", "signature": "v",
            "traffic_light": "red", "issues": [{"secret": 1}], "targets": {"missing": 1}}],
    })
    assert "issues" not in published["files"][0]
    assert "targets" not in published["files"][0]


def test_open_version_and_comments_are_content_versioned(tmp_path):
    root = tmp_path / "mapfile"; root.mkdir()
    path = root / "PA100_a.map"; path.write_bytes(b"one")
    with patch.object(mapfile_review.traffic, "get_mapfile_dir", return_value=root), \
         patch.object(mapfile_review.traffic, "get_product_code_for_vehicle", return_value="PA100"):
        first = mapfile_review.open_version("VEH_A", path.name)
        mapfile_review.version_comments("VEH_A", path.name, first["signature"], text="hello", user={"username": "u"})
        stamp = path.stat().st_mtime_ns; os.utime(path, ns=(stamp + 1000, stamp + 1000))
        same = mapfile_review.open_version("VEH_A", path.name, first["signature"])
        assert mapfile_review.version_comments("VEH_A", path.name, same["signature"])["comments"][0]["text"] == "hello"
        path.write_bytes(b"two")
        second = mapfile_review.open_version("VEH_A", path.name)
        assert second["signature"] != first["signature"]
        assert mapfile_review.version_comments("VEH_A", path.name, second["signature"])["comments"] == []


def test_comments_concurrent_append_and_validation(tmp_path):
    root = tmp_path / "mapfile"; root.mkdir(); path = root / "PA100_a.map"; path.write_bytes(b"x")
    with patch.object(mapfile_review.traffic, "get_mapfile_dir", return_value=root), \
         patch.object(mapfile_review.traffic, "get_product_code_for_vehicle", return_value="PA100"):
        version = mapfile_review.open_version("VEH_A", path.name)["signature"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: mapfile_review.version_comments("VEH_A", path.name, version,
                text=f"c{i}", user={"username": "u"}), range(8)))
        assert len(mapfile_review.version_comments("VEH_A", path.name, version)["comments"]) == 8
        with pytest.raises(ValueError): mapfile_review.version_comments("VEH_A", path.name, version, text=" ", user={"username": "u"})
        with pytest.raises(ValueError): mapfile_review.version_comments("VEH_A", path.name, version, text="x" * 4001, user={"username": "u"})
        with pytest.raises(ValueError): mapfile_review.open_version("VEH_A", "../PA100_a.map")
        with patch.object(mapfile_review.traffic, "get_product_code_for_vehicle", return_value="PB200"):
            with pytest.raises(FileNotFoundError): mapfile_review.open_version("VEH_B", path.name)
        with pytest.raises(FileNotFoundError): mapfile_review.version_comments("VEH_A", path.name, version + "bad")


def test_summary_reads_only_snapshot_and_small_comment_metadata(tmp_path):
    row = {"filename": "a.map", "rel_path": "dev/a.map", "signature": "v1",
           "status": "ok", "traffic_light": "red", "sl": {"light": "red"},
           "issues": [{"reason": "좌표 불일치"}] * 10000}
    mapfile_review.publish_snapshot({"vehicle": "V", "files": [row],
                                    "groups": [{"key": "dev", "files": [row]}]})
    path = mapfile_review._path("mapfile_versions", "V", "dev/a.map", "v1")
    mapfile_review.save_json(path, {"comments": []})
    mapfile_review.version_comments("V", "dev/a.map", "v1", text="확인 중", user={"username": "u"})
    with patch.object(mapfile_review.traffic, "list_mapfiles_for_product") as listing, \
         patch.object(mapfile_review.traffic, "file_signature") as signature, \
         patch.object(mapfile_review.traffic._tc, "inspect") as inspect, \
         patch.object(mapfile_review._pool, "submit") as submit:
        summary = mapfile_review.get_summary("V")
    for forbidden in (listing, signature, inspect, submit):
        forbidden.assert_not_called()
    result = summary["files"][0]
    assert result["comment"] == "좌표 불일치"
    assert "issues" not in result
    assert result["comment_summary"]["latest"]["text"] == "확인 중"
    assert summary["groups"][0]["files"][0]["comment_summary"]["count"] == 1
    assert len(__import__("json").dumps(summary)) < 3000


def test_summary_does_not_wait_for_background_verification_lock():
    mapfile_review.publish_snapshot({"vehicle": "V", "files": [], "groups": []})
    entered = threading.Event()
    release = threading.Event()

    def hold_lock():
        with mapfile_review.file_transaction(mapfile_review._path("traffic_snapshots", "V")):
            entered.set()
            release.wait(5)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(hold_lock)
        assert entered.wait(2)
        try:
            result = pool.submit(mapfile_review.get_summary, "V").result(timeout=1)
            assert result["ok"]
        finally:
            release.set()
        worker.result(timeout=2)
