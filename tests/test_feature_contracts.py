from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app_v2.modules.tracker.repository import TrackerIssueRepository  # noqa: E402
from app_v2.modules.tracker.service import TrackerService  # noqa: E402
from routers import splittable, tracker  # noqa: E402


def test_splittable_view_route_renders_fixture_table(tmp_path, monkeypatch):
    pl.DataFrame({
        "root_lot_id": ["R1000", "R1000", "R2000"],
        "wafer_id": [2, 1, 1],
        "fab_lot_id": ["R1000A.1", "R1000A.1", "R2000A.1"],
        "KNOB_GATE": ["B", "A", "Z"],
        "INLINE_TEMP": [102, 101, 120],
    }).write_parquet(tmp_path / "ML_TABLE_CONTRACT.parquet")

    plan_dir = tmp_path / "flow-data" / "splittable"
    plan_dir.mkdir(parents=True)
    monkeypatch.setattr(splittable, "_base_root", lambda: tmp_path)
    monkeypatch.setattr(splittable, "_db_base", lambda: tmp_path)
    monkeypatch.setattr(splittable, "PLAN_DIR", plan_dir)
    monkeypatch.setattr(splittable, "PREFIX_CFG", plan_dir / "prefix_config.json")
    monkeypatch.setattr(splittable, "SOURCE_CFG", plan_dir / "source_config.json")
    monkeypatch.setattr(splittable, "PRECISION_CFG", plan_dir / "precision_config.json")

    body = splittable.view_split(
        product="ML_TABLE_CONTRACT",
        root_lot_id="R1000",
        wafer_ids="",
        prefix="KNOB",
        custom_name="",
        view_mode="all",
        history_mode="all",
        fab_lot_id="",
        custom_cols="",
    )

    assert body["headers"] == ["#1", "#2"]
    assert body["root_lot_id"] == "R1000"
    assert body["selected_count"] == 1
    assert body["rows"][0]["_param"] == "KNOB_GATE"
    assert body["rows"][0]["_cells"]["0"]["actual"] == "A"
    assert body["rows"][0]["_cells"]["1"]["actual"] == "B"


def test_splittable_lot_note_uses_lot_id_without_extra_prefix():
    assert splittable._notes_key_lot("PRODA", "A1000") == "PRODA__LOT__A1000"
    ui = (ROOT / "frontend" / "src" / "pages" / "My_SplitTable.jsx").read_text(encoding="utf-8")
    assert "A{lotId}" not in ui
    assert "+ LOT 노트 ({lotId})" in ui


def test_home_flowi_empty_chat_greeting_copy():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Home.jsx").read_text(encoding="utf-8")
    assert "오늘 어떤 도움을 드릴까요?" in ui
    assert "flow-i 대화가 여기 이어집니다." not in ui
    assert "/api/llm/flowi/verify" in ui
    assert "연결확인중" in ui
    assert "연결끊김" in ui
    assert "flowiStartle" not in ui
    assert "READYING" not in ui


def test_common_loading_component_shows_progress_cues():
    ui = (ROOT / "frontend" / "src" / "components" / "Loading.jsx").read_text(encoding="utf-8")
    assert "flowLoadingSweep" in ui
    assert "aria-live=\"polite\"" in ui
    assert "캐시 확인" in ui
    assert "데이터 준비 중" in ui


def test_tracker_issue_routes_round_trip_against_configured_store(tmp_path, monkeypatch):
    tracker_dir = tmp_path / "tracker"
    issues_file = tracker_dir / "issues.json"
    cats_file = tracker_dir / "categories.json"
    cats_file.parent.mkdir(parents=True)
    cats_file.write_text(json.dumps([{"name": "Monitor", "color": "#3b82f6"}]), encoding="utf-8")

    monkeypatch.setattr(tracker, "TRACKER_DIR", tracker_dir)
    monkeypatch.setattr(tracker, "IMG_DIR", tracker_dir / "images")
    monkeypatch.setattr(tracker, "ISSUES_FILE", issues_file)
    monkeypatch.setattr(tracker, "CATS_FILE", cats_file)
    monkeypatch.setattr(tracker, "TRACKER_SERVICE", TrackerService(TrackerIssueRepository(issues_file)))
    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "feature_tester", "role": "admin"})

    created = tracker.create_issue(
        tracker.IssueCreate(**{
            "title": "Feature contract tracker issue",
            "description": "DB round trip check",
            "category": "Monitor",
            "lots": [{"root_lot_id": "R1000", "wafer_id": "1", "product": "PRODA"}],
        }),
        object(),
    )

    assert created["ok"] is True
    issue_id = created["id"]
    saved = json.loads(issues_file.read_text(encoding="utf-8"))
    assert saved[0]["id"] == issue_id
    assert saved[0]["username"] == "feature_tester"
    assert saved[0]["lots"][0]["root_lot_id"] == "R1000"

    listed = tracker.list_issues(object(), status="", limit=5)
    assert [row["id"] for row in listed["issues"]] == [issue_id]

    fetched = tracker.get_issue(object(), issue_id=issue_id)
    assert fetched["issue"]["title"] == "Feature contract tracker issue"

    tracker.add_comment(
        tracker.CommentReq(issue_id=issue_id, text="top level comment"),
        object(),
    )
    tracker.add_comment_reply(
        tracker.CommentReplyReq(issue_id=issue_id, parent_index=0, text="nested reply"),
        object(),
    )
    fetched = tracker.get_issue(object(), issue_id=issue_id)
    assert fetched["issue"]["comments"][0]["replies"][0]["text"] == "nested reply"
    listed = tracker.list_issues(object(), status="", limit=5)
    assert listed["issues"][0]["comment_count"] == 2

    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "stranger", "role": "user"})
    with pytest.raises(HTTPException):
        tracker.delete_comment(
            tracker.CommentDeleteReq(issue_id=issue_id, comment_index=0),
            object(),
        )

    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "feature_tester", "role": "user"})
    tracker.delete_comment(
        tracker.CommentDeleteReq(issue_id=issue_id, comment_index=0, reply_index=0),
        object(),
    )
    fetched = tracker.get_issue(object(), issue_id=issue_id)
    assert fetched["issue"]["comments"][0].get("replies") == []

    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "other_user", "role": "user"})
    tracker.add_comment(
        tracker.CommentReq(issue_id=issue_id, text="other user comment"),
        object(),
    )
    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "admin_user", "role": "admin"})
    tracker.delete_comment(
        tracker.CommentDeleteReq(issue_id=issue_id, comment_index=1),
        object(),
    )
    fetched = tracker.get_issue(object(), issue_id=issue_id)
    assert len(fetched["issue"]["comments"]) == 1


def test_tracker_lot_step_route_reads_configured_fab_db(tmp_path, monkeypatch):
    db_root = tmp_path / "db"
    fab_dir = db_root / "1.RAWDATA_DB_FAB" / "PRODA" / "date=20260428"
    fab_dir.mkdir(parents=True)
    pl.DataFrame({
        "product": ["PRODA", "PRODA"],
        "root_lot_id": ["R1000", "R1000"],
        "lot_id": ["R1000A.1", "R1000A.1"],
        "fab_lot_id": ["R1000A.1", "R1000A.1"],
        "wafer_id": ["1", "1"],
        "step_id": ["STEP_010", "STEP_020"],
        "tkout_time": ["2026-04-28T08:00:00", "2026-04-28T09:00:00"],
    }).write_parquet(fab_dir / "part.parquet")

    import core.lot_step as lot_step

    monkeypatch.setattr(lot_step, "_get_db_root", lambda: db_root)
    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "feature_tester", "role": "admin"})

    body = tracker.lot_step(
        object(),
        product="PRODA",
        root_lot_id="R1000",
        lot_id="",
        wafer_id="1",
        monitor_prod="",
        source="fab",
        category="",
    )

    assert body["source"] == "fab"
    assert body["source_root"] == "1.RAWDATA_DB_FAB"
    assert body["snapshot"]["fab"]["step_id"] == "STEP_020"
    assert body["snapshot"]["fab"]["root_lot_id"] == "R1000"
