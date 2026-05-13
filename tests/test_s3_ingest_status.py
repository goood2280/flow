from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import s3_ingest  # noqa: E402


def test_item_due_state_uses_last_end_and_interval():
    now = datetime.datetime(2026, 5, 8, 12, 0, 0).timestamp()
    item = {"interval_min": 30}

    fresh = s3_ingest._item_due_state(
        item,
        {"last_start": "2026-05-08T11:00:00", "last_end": "2026-05-08T11:45:00"},
        now,
    )
    stale = s3_ingest._item_due_state(item, {"last_end": "2026-05-08T11:20:00"}, now)
    never = s3_ingest._item_due_state(item, {}, now)
    manual = s3_ingest._item_due_state({"interval_min": 0}, {"last_end": "2026-05-08T10:00:00"}, now)

    assert fresh["due"] is False
    assert fresh["age_seconds"] == 15 * 60
    assert fresh["next_due"] == "2026-05-08T12:15:00"
    assert stale["due"] is True
    assert never["due"] is True
    assert manual["due"] is False
    assert manual["next_due"] is None


def test_recent_download_sync_is_not_stale_item(tmp_path, monkeypatch):
    target_dir = tmp_path / "DB1"
    target_dir.mkdir()
    fp = target_dir / "part.csv"
    fp.write_text("a\n1\n", encoding="utf-8")
    old = time.time() - 8 * 3600
    os.utime(fp, (old, old))

    cfg = tmp_path / "config.json"
    status = tmp_path / "status.json"
    cfg.write_text(json.dumps({
        "items": [{
            "id": "db1",
            "kind": "db",
            "target": "DB1",
            "s3_url": "s3://bucket/DB1",
            "command": "sync",
            "direction": "download",
            "interval_min": 60,
            "enabled": True,
        }]
    }), encoding="utf-8")
    status.write_text(json.dumps({
        "db1": {
            "last_status": "ok",
            "last_end": datetime.datetime.now().isoformat(timespec="seconds"),
        }
    }), encoding="utf-8")
    monkeypatch.setattr(s3_ingest, "CONFIG_FILE", cfg)
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)
    monkeypatch.setattr(s3_ingest, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(s3_ingest, "_RUNNING", {})

    item = s3_ingest.status_by_target()["by_target"]["DB1"]

    assert item["latest_item_stale_6h"] is True
    assert item["freshness_state"] == "ok"


def test_child_targets_create_parent_aggregate_light():
    now = datetime.datetime.now()
    by_target = {
        "FAB/PRODA": {
            "direction": "download",
            "enabled": True,
            "interval_min": 30,
            "last_status": "ok",
            "last_end": (now - datetime.timedelta(minutes=5)).isoformat(timespec="seconds"),
            "is_running": False,
            "latest_item_stale_6h": False,
        },
        "FAB/PRODB": {
            "direction": "download",
            "enabled": True,
            "interval_min": 30,
            "last_status": "ok",
            "last_end": (now - datetime.timedelta(minutes=7)).isoformat(timespec="seconds"),
            "is_running": False,
            "latest_item_stale_6h": False,
        },
    }

    s3_ingest._aggregate_child_statuses(by_target)

    assert by_target["FAB"]["aggregate"] is True
    assert by_target["FAB"]["child_targets"] == 2
    assert by_target["FAB"]["last_status"] == "ok"
    assert by_target["FAB"]["freshness_state"] == "ok"
    assert by_target["FAB"]["last_end"] == by_target["FAB/PRODA"]["last_end"]


def test_status_by_target_prefers_direction_slot(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    status = tmp_path / "status.json"
    cfg.write_text(json.dumps({
        "items": [{
            "id": "db1",
            "kind": "db",
            "target": "DB1",
            "s3_url": "s3://bucket/DB1",
            "command": "sync",
            "direction": "upload",
            "interval_min": 60,
            "enabled": True,
        }]
    }), encoding="utf-8")
    status.write_text(json.dumps({
        "db1": {
            "last_status": "ok",
            "last_end": "2026-05-08T09:00:00",
            "directions": {
                "download": {"last_status": "ok", "last_end": "2026-05-08T10:00:00"},
                "upload": {"last_status": "error", "last_end": "2026-05-08T11:00:00"},
            },
        }
    }), encoding="utf-8")
    monkeypatch.setattr(s3_ingest, "CONFIG_FILE", cfg)
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)
    monkeypatch.setattr(s3_ingest, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(s3_ingest, "_RUNNING", {})

    item = s3_ingest.status_by_target()["by_target"]["DB1"]

    assert item["direction"] == "upload"
    assert item["last_status"] == "error"
    assert item["last_end"] == "2026-05-08T11:00:00"


def test_parent_aggregate_uses_child_freshness_state_not_raw_stale_flag():
    now = datetime.datetime.now()
    by_target = {
        "FAB/PRODA": {
            "direction": "download",
            "enabled": True,
            "interval_min": 30,
            "last_status": "ok",
            "last_end": (now - datetime.timedelta(minutes=5)).isoformat(timespec="seconds"),
            "is_running": False,
            "latest_item_stale_6h": True,
            "freshness_state": "ok",
        },
        "FAB/PRODB": {
            "direction": "download",
            "enabled": True,
            "interval_min": 30,
            "last_status": "ok",
            "last_end": (now - datetime.timedelta(minutes=7)).isoformat(timespec="seconds"),
            "is_running": False,
            "latest_item_stale_6h": False,
            "freshness_state": "ok",
        },
    }

    s3_ingest._aggregate_child_statuses(by_target)

    assert by_target["FAB"]["latest_item_stale_6h"] is False
    assert by_target["FAB"]["freshness_state"] == "ok"


def test_save_item_reuses_existing_target_and_preserves_last_log(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    status = tmp_path / "status.json"
    cfg.write_text(json.dumps({
        "items": [{
            "id": "db1",
            "kind": "db",
            "target": "DB1",
            "s3_url": "s3://bucket/DB1-old",
            "command": "sync",
            "direction": "download",
            "interval_min": 60,
            "enabled": True,
        }]
    }), encoding="utf-8")
    status.write_text(json.dumps({
        "db1": {
            "last_status": "ok",
            "last_end": "2026-05-08T10:00:00",
            "last_output_tail": "previous sync complete",
            "directions": {
                "download": {
                    "last_status": "ok",
                    "last_end": "2026-05-08T10:00:00",
                    "last_output_tail": "previous sync complete",
                }
            },
        }
    }), encoding="utf-8")
    monkeypatch.setattr(s3_ingest, "CONFIG_FILE", cfg)
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)
    monkeypatch.setattr(s3_ingest, "_is_admin", lambda username: username == "admin")
    monkeypatch.setattr(s3_ingest, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(s3_ingest, "_RUNNING", {})

    out = s3_ingest.save_item(s3_ingest.SaveReq(
        username="admin",
        kind="db",
        target="DB1",
        s3_url="s3://bucket/DB1-new",
        command="sync",
        direction="download",
        interval_min=30,
        enabled=True,
    ))
    item = s3_ingest.list_items(username="admin")["items"][0]

    assert out["id"] == "db1"
    assert item["s3_url"] == "s3://bucket/DB1-new"
    assert item["status"]["last_end"] == "2026-05-08T10:00:00"
    assert item["status"]["last_output_tail"] == "previous sync complete"


def test_running_status_keeps_previous_last_log(tmp_path, monkeypatch):
    status = tmp_path / "status.json"
    status.write_text(json.dumps({
        "db1": {
            "last_status": "ok",
            "last_end": "2026-05-08T10:00:00",
            "last_output_tail": "previous sync complete",
        }
    }), encoding="utf-8")
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)

    s3_ingest._update_status("db1", direction="download", last_start="2026-05-08T11:00:00", last_status="running")
    saved = json.loads(status.read_text("utf-8"))

    assert saved["db1"]["last_status"] == "running"
    assert saved["db1"]["last_end"] == "2026-05-08T10:00:00"
    assert saved["db1"]["last_output_tail"] == "previous sync complete"
    assert saved["db1"]["directions"]["download"]["last_status"] == "running"
