from __future__ import annotations

import json
import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
for raw in (str(ROOT), str(BACKEND)):
    if raw not in sys.path:
        sys.path.insert(0, raw)


from app_v2.modules.tracker.repository import TrackerIssueRepository
from app_v2.modules.tracker.service import TrackerService


def _create(service: TrackerService, *, lots: list[dict], request_id: str = "request-1") -> dict:
    result = service.create_legacy_issue(
        issue_id="ISS-1",
        title="issue",
        description="",
        username="owner",
        status="in_progress",
        priority="normal",
        category="Analysis",
        links=[],
        images=[],
        lots=lots,
        group_ids=[],
        client_request_id=request_id,
    )
    assert result.ok
    return result.data["issue"]


def test_delayed_lot_enrichment_does_not_overwrite_a_user_edit(tmp_path):
    path = tmp_path / "issues.json"
    service = TrackerService(TrackerIssueRepository(path))
    original_lots = [{"lot_id": "LOT-1", "wafer_id": "1"}]
    _create(service, lots=original_lots)

    edited_lots = [{"lot_id": "LOT-2", "wafer_id": "2"}]
    edited = service.update_legacy_issue(
        issue_id="ISS-1",
        username="owner",
        lots=edited_lots,
    )
    assert edited.ok and edited.data["updated"]
    edited_revision = edited.data["issue"]["revision"]

    delayed = service.update_legacy_issue(
        issue_id="ISS-1",
        username="owner",
        lots=[{"lot_id": "LOT-1", "wafer_id": "1", "enriched": True}],
        expected_lots=original_lots,
    )

    assert delayed.ok
    assert delayed.data["conflict"] is True
    assert delayed.data["updated"] is False
    stored = json.loads(path.read_text(encoding="utf-8"))[0]
    assert stored["lots"] == edited_lots
    assert stored["revision"] == edited_revision


def test_lot_builder_merges_against_latest_row_inside_update(tmp_path):
    path = tmp_path / "issues.json"
    service = TrackerService(TrackerIssueRepository(path))
    _create(service, lots=[{"lot_id": "LOT-1", "watch": {"target": "A"}}])
    service.update_legacy_issue(
        issue_id="ISS-1",
        username="scanner",
        lots=[{"lot_id": "LOT-1", "watch": {"target": "B"}}],
    )

    seen = {}

    def build_lots(current):
        seen["revision"] = current["revision"]
        return [{**current["lots"][0], "wafer_id": "3"}]

    result = service.update_legacy_issue(
        issue_id="ISS-1",
        username="owner",
        title="renamed",
        lots_builder=build_lots,
    )

    assert result.ok and result.data["updated"]
    assert seen["revision"] == 2
    assert result.data["previous_issue"]["revision"] == 2
    assert result.data["issue"]["lots"] == [
        {"lot_id": "LOT-1", "watch": {"target": "B"}, "wafer_id": "3"}
    ]


def test_duplicate_create_is_idempotent_without_rewriting_store(tmp_path, monkeypatch):
    path = tmp_path / "issues.json"
    repo = TrackerIssueRepository(path)
    service = TrackerService(repo)
    first = _create(service, lots=[], request_id="same-request")

    replace_calls = []
    original_replace = Path.replace

    def track_replace(self, target):
        replace_calls.append((self, target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", track_replace)
    duplicate = service.create_legacy_issue(
        issue_id="ISS-2",
        title="duplicate",
        description="",
        username="owner",
        status="in_progress",
        priority="normal",
        category="Analysis",
        links=[],
        images=[],
        lots=[],
        group_ids=[],
        client_request_id="same-request",
    )

    assert duplicate.ok
    assert duplicate.data["issue"]["id"] == first["id"]
    assert replace_calls == []


def test_tracker_list_cache_is_invalidated_after_repository_write():
    from routers import tracker

    tracker._LIST_ROWS_CACHE["sig"] = (1, 2)
    tracker._LIST_ROWS_CACHE["rows"] = [{"id": "old"}]

    tracker._prime_list_rows_cache({"id": "new"})

    assert tracker._LIST_ROWS_CACHE == {"sig": None, "rows": []}


def test_update_uses_atomic_repository_path_and_defers_followup(tmp_path, monkeypatch):
    from fastapi import BackgroundTasks
    from app_v2.shared.result import ok
    from core import audit
    from routers import tracker

    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "owner"})
    monkeypatch.setattr(tracker, "_load", lambda: (_ for _ in ()).throw(AssertionError("extra issues.json read")))
    monkeypatch.setattr(
        tracker.TRACKER_SERVICE,
        "update_legacy_issue",
        lambda **_kwargs: ok({
            "issue": {"id": "ISS-1", "title": "renamed", "status": "in_progress", "lots": []},
            "previous_issue": {"id": "ISS-1", "title": "issue", "status": "in_progress", "lots": []},
            "updated": True,
            "conflict": False,
        }),
    )
    followups = []
    monkeypatch.setattr(tracker, "_append_tracker_knowledge_events", lambda *_args, **_kwargs: followups.append("knowledge"))
    monkeypatch.setattr(audit, "record_user", lambda *_args, **_kwargs: followups.append("audit"))
    background = BackgroundTasks()

    response = tracker.update_issue(
        tracker.IssueUpdate(issue_id="ISS-1", title="renamed"),
        object(),
        background,
    )

    assert response == {"ok": True}
    assert followups == []
    asyncio.run(background())
    assert followups == ["knowledge", "audit"]
