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
