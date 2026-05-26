from __future__ import annotations

import datetime
import json
import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core  # noqa: E402
from routers import s3_ingest  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}

    def __init__(self, username: str = "alice", role: str = "user"):
        self.state = _State({"username": username, "role": role})


@pytest.fixture(autouse=True)
def _clear_s3_local_info_cache():
    s3_ingest._LOCAL_INFO_CACHE.clear()
    yield
    s3_ingest._LOCAL_INFO_CACHE.clear()


def _route_dependency(path: str, method: str):
    method = method.upper()
    for route in s3_ingest.router.routes:
        if getattr(route, "path", None) != path:
            continue
        if method not in (getattr(route, "methods", set()) or set()):
            continue
        deps = getattr(getattr(route, "dependant", None), "dependencies", []) or []
        assert deps, f"{method} {path} has no dependency gate"
        return deps[0].call
    raise AssertionError(f"route not found: {method} {path}")


def _assert_allowed(dep, request: _Request) -> None:
    dep(request)


def _assert_denied(dep, request: _Request) -> None:
    with pytest.raises(HTTPException) as exc:
        dep(request)
    assert exc.value.status_code == 403


def test_s3_read_run_routes_allow_filebrowser_manager(monkeypatch):
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {"filebrowser": ["fb_manager"]})
    manager = _Request("fb_manager", "user")
    plain = _Request("plain", "user")

    for method, path in [
        ("GET", "/api/s3ingest/items"),
        ("GET", "/api/s3ingest/history"),
        ("POST", "/api/s3ingest/run"),
    ]:
        _assert_allowed(_route_dependency(path, method), manager)

    _assert_denied(_route_dependency("/api/s3ingest/run", "POST"), plain)


def test_s3_config_mutation_routes_are_admin_only(monkeypatch):
    monkeypatch.setattr(auth_core, "get_page_admins", lambda: {"filebrowser": ["fb_manager"]})
    manager = _Request("fb_manager", "user")
    admin = _Request("root", "admin")

    for method, path in [
        ("POST", "/api/s3ingest/save"),
        ("POST", "/api/s3ingest/delete"),
        ("POST", "/api/s3ingest/schedule/save"),
        ("GET", "/api/s3ingest/aws-config"),
        ("POST", "/api/s3ingest/aws-config/save"),
        ("POST", "/api/s3ingest/aws-config/delete"),
    ]:
        dep = _route_dependency(path, method)
        _assert_denied(dep, manager)
        _assert_allowed(dep, admin)


def test_filebrowser_manager_can_list_history_and_run_existing_s3_item(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    status = tmp_path / "status.json"
    history = tmp_path / "history.jsonl"
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
    status.write_text(json.dumps({"db1": {"last_status": "ok", "last_end": "2026-05-08T10:00:00"}}), encoding="utf-8")
    history.write_text(json.dumps({"id": "db1", "status": "ok", "cmd": "aws s3 sync"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(s3_ingest, "CONFIG_FILE", cfg)
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)
    monkeypatch.setattr(s3_ingest, "HISTORY_FILE", history)
    monkeypatch.setattr(s3_ingest, "_RUNNING", {})
    monkeypatch.setattr(s3_ingest, "_QUEUED", [])
    monkeypatch.setattr(s3_ingest, "_schedule_run", lambda item_id: item_id == "db1")

    perm = {"username": "fb_manager", "role": "user"}
    items = s3_ingest.list_items(username="fb_manager", _perm=perm)
    history_out = s3_ingest.get_history(username="fb_manager", id="db1", limit=50, _perm=perm)
    run = s3_ingest.run_manual(s3_ingest.IdReq(username="fb_manager", id="db1"), _perm=perm)

    assert items["items"][0]["id"] == "db1"
    assert history_out["entries"][0]["id"] == "db1"
    assert run == {"ok": True, "started": True, "queued": True}


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


def test_status_by_target_fast_skips_local_scan_and_aggregates_parent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    status = tmp_path / "status.json"
    now = datetime.datetime.now().isoformat(timespec="seconds")
    cfg.write_text(json.dumps({
        "items": [
            {
                "id": "db1",
                "kind": "db",
                "target": "FAB/PRODA",
                "s3_url": "s3://bucket/FAB/PRODA",
                "command": "sync",
                "direction": "download",
                "interval_min": 60,
                "enabled": True,
            },
            {
                "id": "db2",
                "kind": "db",
                "target": "FAB/PRODB",
                "s3_url": "s3://bucket/FAB/PRODB",
                "command": "sync",
                "direction": "download",
                "interval_min": 60,
                "enabled": True,
            },
        ]
    }), encoding="utf-8")
    status.write_text(json.dumps({
        "db1": {"last_status": "ok", "last_end": now},
        "db2": {"last_status": "ok", "last_end": now},
    }), encoding="utf-8")
    calls = []

    def fail_local_scan(target):
        calls.append(target)
        raise AssertionError("local scan should be skipped")

    monkeypatch.setattr(s3_ingest, "CONFIG_FILE", cfg)
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)
    monkeypatch.setattr(s3_ingest, "_latest_local_item_info", fail_local_scan)
    monkeypatch.setattr(s3_ingest, "_RUNNING", {})
    monkeypatch.setattr(s3_ingest, "_QUEUED", [])

    out = s3_ingest.status_by_target(include_local=False)

    assert out["local_freshness_included"] is False
    assert calls == []
    assert "latest_item_at" not in out["by_target"]["FAB/PRODA"]
    assert out["by_target"]["FAB"]["aggregate"] is True
    assert out["by_target"]["FAB"]["freshness_state"] == "ok"
    assert out["by_target"]["FAB"]["latest_item_at"] is None


def test_status_by_target_full_caches_local_freshness(tmp_path, monkeypatch):
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
    calls = []

    def fake_local_scan(target):
        calls.append(target)
        return {
            "latest_item_at": "2026-05-08T11:55:00",
            "latest_item_relpath": "part.csv",
            "latest_item_age_hours": 0.1,
            "latest_item_stale_6h": False,
            "latest_item_scan_error": None,
        }

    monkeypatch.setattr(s3_ingest, "CONFIG_FILE", cfg)
    monkeypatch.setattr(s3_ingest, "STATUS_FILE", status)
    monkeypatch.setattr(s3_ingest, "_db_root", lambda: tmp_path)
    monkeypatch.setattr(s3_ingest, "_latest_local_item_info", fake_local_scan)
    monkeypatch.setattr(s3_ingest, "_RUNNING", {})
    monkeypatch.setattr(s3_ingest, "_QUEUED", [])

    first = s3_ingest.status_by_target(include_local=True)
    second = s3_ingest.status_by_target(include_local=True)

    assert first["local_freshness_included"] is True
    assert second["by_target"]["DB1"]["latest_item_relpath"] == "part.csv"
    assert calls == ["DB1"]


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

    item = s3_ingest.status_by_target(include_local=True)["by_target"]["DB1"]

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


def test_aws_config_save_uses_flow_data_paths(tmp_path, monkeypatch):
    aws_home = tmp_path / "flow-data" / "s3_ingest" / "aws"
    credentials = aws_home / "credentials"
    config = aws_home / "config"
    monkeypatch.setattr(s3_ingest, "AWS_HOME", aws_home)
    monkeypatch.setattr(s3_ingest, "AWS_CREDENTIALS", credentials)
    monkeypatch.setattr(s3_ingest, "AWS_CONFIG", config)
    monkeypatch.setattr(s3_ingest.shutil, "which", lambda _name: "/usr/bin/aws")

    out = s3_ingest.aws_config_save(
        s3_ingest.AwsConfigReq(
            username="admin",
            profile="default",
            aws_access_key_id="AKIA1234567890AB",
            aws_secret_access_key="abcDEF1234567890abcDEF1234567890abcDEF12",
            region="ap-northeast-2",
            output="json",
            endpoint_url="https://s3.internal.example:9000",
        ),
        _perm=None,
    )
    got = s3_ingest.aws_config_get(username="admin", _perm=None)
    env = s3_ingest._aws_cli_env()

    assert out == {"ok": True, "profile": "default"}
    assert credentials.exists()
    assert config.exists()
    assert str(tmp_path / "flow-data") in got["credentials_path"]
    assert got["credentials_path"] == str(credentials)
    assert got["config_path"] == str(config)
    assert got["profiles"][0]["aws_access_key_id"] == "AKIA1234567890AB"
    assert got["profiles"][0]["has_secret"] is True
    assert got["profiles"][0]["endpoint_url"] == "https://s3.internal.example:9000"
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == str(credentials)
    assert env["AWS_CONFIG_FILE"] == str(config)


def test_s3_sync_boto3_reads_flow_data_aws_profile(tmp_path, monkeypatch):
    from core import s3_sync

    data_root = tmp_path / "flow-data"
    aws_home = data_root / "s3_ingest" / "aws"
    aws_home.mkdir(parents=True)
    (aws_home / "credentials").write_text(
        "[flow]\n"
        "aws_access_key_id = AKIA1234567890AB\n"
        "aws_secret_access_key = abcDEF1234567890abcDEF1234567890abcDEF12\n",
        encoding="utf-8",
    )
    (aws_home / "config").write_text(
        "[profile flow]\n"
        "region = ap-northeast-2\n"
        "endpoint_url = https://s3.internal.example:9000\n",
        encoding="utf-8",
    )
    artifact_file = data_root / "reformatter" / "PRODA.json"
    artifact_file.parent.mkdir(parents=True)
    artifact_file.write_text("{}", encoding="utf-8")
    calls = {}

    class FakeClient:
        def upload_file(self, file_path, bucket, key):
            calls["upload"] = (file_path, bucket, key)

    class FakeSession:
        def __init__(self, **kwargs):
            calls["session_kwargs"] = kwargs

        def client(self, service, **kwargs):
            calls["client"] = (service, kwargs)
            return FakeClient()

    class FakeBoto3:
        Session = FakeSession

    monkeypatch.setattr(s3_sync, "_HAS_BOTO", True)
    monkeypatch.setattr(s3_sync, "_boto3", FakeBoto3)

    out = s3_sync.sync_one(
        data_root,
        {"path": str(artifact_file), "key": "reformatter/PRODA.json", "type": "reformatter"},
        {"enabled": True, "bucket": "bucket", "prefix": "flow/", "profile": "flow"},
    )

    assert out["status"] == "uploaded"
    assert calls["session_kwargs"]["aws_access_key_id"] == "AKIA1234567890AB"
    assert calls["session_kwargs"]["aws_secret_access_key"] == "abcDEF1234567890abcDEF1234567890abcDEF12"
    assert calls["client"] == (
        "s3",
        {"region_name": "ap-northeast-2", "endpoint_url": "https://s3.internal.example:9000"},
    )
