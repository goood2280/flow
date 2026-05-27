from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import splittable_sets_cache as cache  # noqa: E402
from backend.routers import informs, splittable  # noqa: E402


def _install_paths(tmp_path, monkeypatch):
    plan_dir = tmp_path / "splittable"
    plan_dir.mkdir()
    paste_file = plan_dir / "paste_sets.json"
    monkeypatch.setattr(cache, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(cache, "PASTE_SETS_FILE", paste_file)
    monkeypatch.setattr(splittable, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(splittable, "PASTE_SETS_FILE", paste_file)
    cache.invalidate()
    return plan_dir, paste_file


def test_splittable_sets_cache_hit_ttl_and_invalidation(tmp_path, monkeypatch):
    _plan_dir, paste_file = _install_paths(tmp_path, monkeypatch)
    paste_file.write_text(
        """[{"id":"a","name":"A","product":"ML_TABLE_PRODA","columns":["p","w1"],"rows":[["x","1"]],"username":"u","updated":"2099-01-01T00:00:00"}]""",
        encoding="utf-8",
    )
    now = {"t": 1000.0}
    monkeypatch.setattr(cache.time, "time", lambda: now["t"])
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "tester"})

    first = informs.splittable_sets(object(), product="ML_TABLE_PRODA")
    paste_file.write_text(
        """[{"id":"b","name":"B","product":"ML_TABLE_PRODA","columns":["p"],"rows":[],"username":"u","updated":"2099-01-02T00:00:00"}]""",
        encoding="utf-8",
    )
    cached = informs.splittable_sets(object(), product="ML_TABLE_PRODA")
    now["t"] += cache.TTL_SECONDS + 1
    refreshed = informs.splittable_sets(object(), product="ML_TABLE_PRODA")

    assert first["sets"][0]["id"] == "a"
    assert cached["cached"] is True
    assert cached["sets"][0]["id"] == "a"
    assert refreshed["sets"][0]["id"] == "b"

    splittable.save_paste_set(splittable.PasteSetSaveReq(name="C", product="ML_TABLE_PRODA", columns=["p"], rows=[]))
    invalidated = informs.splittable_sets(object(), product="ML_TABLE_PRODA")
    assert invalidated["cached"] is False
    assert any(row["name"] == "C" for row in invalidated["sets"])


def test_splittable_sets_cache_exposes_only_valid_custom_sets(tmp_path, monkeypatch):
    plan_dir, _paste_file = _install_paths(tmp_path, monkeypatch)
    (plan_dir / "custom_test.json").write_text(
        json.dumps({"name": "test", "columns": ["KNOB_A", "CUSTOM_B"], "updated": "2099-01-01T00:00:00"}),
        encoding="utf-8",
    )
    (plan_dir / "custom_tags.json").write_text(
        json.dumps({
            "columns": [{"product": "ML_TABLE_PRODA", "column": "TAG_1.0_TEST"}],
            "values": {"ML_TABLE_PRODA|A1001|1|TAG_1.0_TEST": {"value": "Y"}},
        }),
        encoding="utf-8",
    )
    (plan_dir / "custom_undefined.json").write_text(
        json.dumps({"name": "undefined", "columns": ["KNOB_BAD"]}),
        encoding="utf-8",
    )
    (plan_dir / "custom_mgmt.json").write_text(
        json.dumps({"name": "mgmt", "columns": ["MGMT_OWNER"]}),
        encoding="utf-8",
    )

    rows = cache.list_sets("ML_TABLE_PRODA")["sets"]
    names = {row["name"] for row in rows}

    assert "test" in names
    assert "tags" not in names
    assert "undefined" not in names
    assert "mgmt" not in names
    assert any(row["source"] == "custom" and row["columns"] == ["KNOB_A", "CUSTOM_B"] for row in rows)
