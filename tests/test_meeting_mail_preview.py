from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import meetings  # noqa: E402


class _DisconnectedRequest:
    async def is_disconnected(self):
        return True


def _mail_fixture():
    meeting = {"id": "MT-1", "title": "Weekly Sync", "owner": "owner"}
    session = {
        "id": "SS-1",
        "idx": 2,
        "scheduled_at": "2026-05-12T10:00:00",
        "agendas": [{"title": "Agenda One", "description": "Agenda text", "owner": "owner"}],
        "minutes": {
            "body": "Minutes body",
            "decisions": [{"id": "d1", "text": "Decision One"}],
            "action_items": [{"id": "a1", "text": "Action One", "owner": "worker", "due": "2026-05-20"}],
        },
    }
    return meeting, session


def _ask_fixture():
    return {
        "id": "MT-ASK",
        "title": "Device Change Sync",
        "owner": "owner",
        "created_by": "owner",
        "status": "active",
        "group_ids": [],
        "sessions": [{
            "id": "SS-ASK",
            "idx": 1,
            "scheduled_at": "2026-05-12T09:00:00",
            "status": "completed",
            "agendas": [{"title": "Mask change review", "description": "Check split table result", "owner": "owner"}],
            "minutes": {
                "body": "Reviewed mask split change.",
                "decisions": [{"id": "d1", "text": "Proceed with MASK_A", "due": "2026-05-13"}],
                "action_items": [{"id": "a1", "text": "Send inform mail", "owner": "worker", "due": "2026-05-14", "status": "pending"}],
            },
        }],
    }


def _ask_fixture_named(mid: str, title: str, *, group_ids=None, decision="Proceed with MASK_A", action="Send inform mail"):
    item = _ask_fixture()
    item["id"] = mid
    item["title"] = title
    item["group_ids"] = list(group_ids or [])
    item["sessions"] = [{
        **item["sessions"][0],
        "id": f"SS-{mid}",
        "minutes": {
            **item["sessions"][0]["minutes"],
            "decisions": [{"id": f"d-{mid}", "text": decision, "due": "2026-05-13"}],
            "action_items": [{"id": f"a-{mid}", "text": action, "owner": "worker", "due": "2026-05-14", "status": "pending"}],
        },
    }]
    return item


def test_meeting_mail_preview_content_flags(monkeypatch):
    monkeypatch.setattr(meetings, "_load_mail_cfg", lambda: {"from_addr": "flow@example.com"})
    meeting, session = _mail_fixture()

    preview = meetings._build_minutes_mail_preview(
        meeting,
        session,
        to_addrs=["user@example.com"],
        subject="Preview",
        include_agenda=False,
        include_minutes=True,
        include_decisions=False,
        include_action_items=True,
    )
    html = preview["content"]

    assert preview["ok"] is True
    assert preview["to"] == ["user@example.com"]
    assert "Agenda One" not in html
    assert "Minutes body" in html
    assert "Decision One" not in html
    assert "Action One" in html
    assert preview["preview_data"]["content"] == html
    assert "mailSendString" in preview["preview_data_wrapped"]


def test_meeting_send_mail_dry_run_uses_preview_html(monkeypatch):
    monkeypatch.setattr(
        meetings,
        "_load_mail_cfg",
        lambda: {"enabled": True, "api_url": "dry-run", "from_addr": "flow@example.com"},
    )
    meeting, session = _mail_fixture()
    kwargs = {
        "to_addrs": ["user@example.com"],
        "subject": "Preview",
        "mail_body": "",
        "include_agenda": True,
        "include_minutes": False,
        "include_decisions": True,
        "include_action_items": False,
    }

    preview = meetings._build_minutes_mail_preview(meeting, session, **kwargs)
    sent = meetings._send_minutes_mail(meeting, session, actor="owner", **kwargs)

    assert sent["ok"] is True
    assert sent["dry_run"] is True
    assert sent["content"] == preview["content"]
    assert sent["preview_data"]["content"] == preview["preview_data"]["content"]


def test_meeting_issue_ref_hydrates_monitor_lot_summary(monkeypatch):
    from core import lot_progress_cache
    from routers import tracker

    monkeypatch.setattr(
        tracker,
        "_load",
        lambda: [{
            "id": "ISS-1",
            "title": "Monitor issue",
            "category": "Monitor",
            "description": "Issue body",
            "lots": [{"product": "PRODA", "lot_id": "A1000A.1", "purpose": "hold"}],
        }],
    )
    monkeypatch.setattr(tracker, "_category_source", lambda _category, _default="fab": "fab")
    monkeypatch.setattr(tracker, "_render_description", lambda text: f"<p>{text}</p>")
    monkeypatch.setattr(
        lot_progress_cache,
        "lot_progress_summary",
        lambda **_kwargs: {
            "product": "PRODA",
            "root_lot_id": "A1000",
            "lot_id": "A1000A.1",
            "wafer_ids": ["1", "2"],
            "wafer_count": 2,
            "wafer_label": "#1~2",
            "rows": [
                {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "1", "step_id": "STEP_010", "func_step": "STI", "update_time": "2026-05-08T10:00:00"},
                {"product": "PRODA", "root_lot_id": "A1000", "lot_id": "A1000A.1", "wafer_id": "2", "step_id": "STEP_020", "func_step": "GATE", "update_time": "2026-05-08T11:00:00"},
            ],
        },
    )

    snap = meetings._hydrate_tracker_issue_ref({"issue_id": "ISS-1"})

    assert snap["lot_count"] == 1
    lot = snap["lots"][0]
    assert lot["lot_id"] == "A1000A.1"
    assert lot["wafer_ids"] == ["1", "2"]
    assert lot["wafer_label"] == "#1~2"
    assert lot["current_step"] == "STEP_020"
    assert lot["func_step"] == "GATE"


def test_meeting_ask_fallback_reads_agenda_decision_action(monkeypatch):
    from core import llm_adapter

    meeting = _ask_fixture()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    summary = meetings._build_meeting_ask_summary(meeting, meeting["sessions"])
    answer, llm = meetings._meeting_ask_llm_answer("회의록, 결정사항, 액션아이템, 아젠다 확인", summary)

    assert llm == {"available": False, "used": False}
    assert "Mask change review" in answer
    assert "Reviewed mask split change." in answer
    assert "Proceed with MASK_A" in answer
    assert "Send inform mail" in answer


def test_meeting_ask_skips_llm_when_session_has_no_content(monkeypatch):
    from core import llm_adapter

    meeting = {
        **_ask_fixture(),
        "sessions": [{
            "id": "SS-EMPTY",
            "idx": 1,
            "scheduled_at": "2026-05-12T11:36:00",
            "status": "scheduled",
            "agendas": [],
            "minutes": None,
        }],
    }
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    summary = meetings._build_meeting_ask_summary(meeting, meeting["sessions"])
    answer, llm = meetings._meeting_ask_llm_answer("아젠다 확인", summary)

    assert llm == {"available": True, "used": False, "skipped": "no_meeting_content"}
    assert "아젠다: 0건" in answer
    assert "저장된 아젠다" in answer


def test_meeting_ask_sanitizes_llm_auth_error(monkeypatch):
    from core import llm_adapter

    meeting = _ask_fixture()
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(
        llm_adapter,
        "complete",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": 'HTTP 401: [{ "error": { "message": "Request had invalid authentication credentials" } }]',
        },
    )

    summary = meetings._build_meeting_ask_summary(meeting, meeting["sessions"])
    answer, llm = meetings._meeting_ask_llm_answer("아젠다 확인", summary)

    assert "Mask change review" in answer
    assert llm["available"] is True
    assert llm["used"] is False
    assert llm["error_code"] == "auth"
    assert llm["error"] == "LLM 인증 설정을 확인하세요. 저장 데이터 답변을 사용했습니다."
    assert "HTTP 401" not in llm["error"]
    assert "invalid authentication" not in llm["error"].lower()


def test_meeting_ask_endpoint_enforces_visibility(monkeypatch):
    hidden = {
        **_ask_fixture(),
        "id": "MT-HIDDEN",
        "owner": "owner",
        "created_by": "owner",
        "group_ids": ["secret-group"],
    }
    monkeypatch.setattr(meetings, "_load", lambda: [hidden])
    monkeypatch.setattr(meetings, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())

    with pytest.raises(meetings.HTTPException) as exc:
        meetings.ask_meeting(
            meetings.MeetingAskReq(meeting_id="MT-HIDDEN", session_id="all", question="결정사항 알려줘"),
            object(),
        )

    assert exc.value.status_code == 403


def test_meeting_ask_endpoint_returns_session_scope(monkeypatch):
    from core import llm_adapter

    meeting = _ask_fixture()
    monkeypatch.setattr(meetings, "_load", lambda: [meeting])
    monkeypatch.setattr(meetings, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = meetings.ask_meeting(
        meetings.MeetingAskReq(meeting_id="MT-ASK", session_id="SS-ASK", question="아젠다는?"),
        object(),
    )

    assert out["ok"] is True
    assert out["scope"] == "session"
    assert out["sources"] == [{
        "session_id": "SS-ASK",
        "label": "1차 · 2026-05-12 09:00",
        "agendas": 1,
        "decisions": 1,
        "action_items": 1,
        "has_minutes": True,
    }]
    assert "Mask change review" in out["answer"]


def test_meeting_ask_auto_mode_clarifies_ambiguous_meeting(monkeypatch):
    from core import llm_adapter

    sync = _ask_fixture_named("MT-SYNC", "Device Change Sync")
    review = _ask_fixture_named("MT-REVIEW", "Device Change Review")
    hidden = _ask_fixture_named("MT-HIDDEN", "Device Change Secret", group_ids=["secret-group"])
    monkeypatch.setattr(meetings, "_load", lambda: [sync, review, hidden])
    monkeypatch.setattr(meetings, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = meetings.ask_meeting(
        meetings.MeetingAskReq(question="Device Change 회의 결정사항 정리해줘"),
        object(),
    )

    assert out["ok"] is True
    assert out["needs_clarification"] is True
    assert out["reason"] == "meeting_ambiguous"
    titles = [c["title"] for c in out["candidates"]]
    assert set(titles) == {"Device Change Sync", "Device Change Review"}
    assert "Device Change Secret" not in titles


def test_meeting_ask_auto_mode_focuses_clear_meeting(monkeypatch):
    from core import llm_adapter
    from routers import calendar as calendar_router

    sync = _ask_fixture_named("MT-SYNC", "Device Change Sync", action="Send mask owner due mail")
    review = _ask_fixture_named("MT-REVIEW", "Device Change Review", action="Review unrelated PM window")
    monkeypatch.setattr(meetings, "_load", lambda: [sync, review])
    monkeypatch.setattr(meetings, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(calendar_router, "_load_events", lambda: [])
    monkeypatch.setattr(calendar_router, "_my_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)

    out = meetings.ask_meeting(
        meetings.MeetingAskReq(question="Device Change Sync 회의 액션 담당자와 마감일 알려줘"),
        object(),
    )

    assert out["ok"] is True
    assert out["needs_clarification"] is False
    assert out["scope"] == "meeting_auto"
    assert out["meeting"]["title"] == "Device Change Sync"
    assert "Send mask owner due mail" in out["answer"]
    assert "Review unrelated PM window" not in out["answer"]


def test_meeting_ask_auto_fallback_includes_visible_calendar_events(monkeypatch):
    from core import llm_adapter
    from routers import calendar as calendar_router

    sync = _ask_fixture_named("MT-SYNC", "Device Change Sync")
    hidden = _ask_fixture_named("MT-HIDDEN", "Hidden Device Meeting", group_ids=["secret-group"])
    monkeypatch.setattr(meetings, "_load", lambda: [sync, hidden])
    monkeypatch.setattr(meetings, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(calendar_router, "_my_group_ids", lambda _username, _role: set())
    monkeypatch.setattr(calendar_router, "_load_events", lambda: [
        {
            "id": "cal-manual",
            "date": "2026-05-14",
            "title": "Litho recipe release",
            "body": "Manual release event",
            "category": "릴리즈",
            "author": "viewer",
            "status": "pending",
            "source_type": "manual",
            "meeting_ref": None,
            "group_ids": [],
        },
        {
            "id": "cal-sync",
            "date": "2026-05-13",
            "title": "1차 회의 결정사항: Proceed with MASK_A",
            "body": "",
            "category": "회의 결정사항",
            "author": "owner",
            "status": "pending",
            "source_type": "meeting_decision",
            "meeting_ref": {"meeting_id": "MT-SYNC", "session_id": "SS-MT-SYNC", "action_item_id": "d-MT-SYNC", "meeting_title": "Device Change Sync"},
            "group_ids": [],
        },
        {
            "id": "cal-hidden-meeting",
            "date": "2026-05-15",
            "title": "Secret hidden meeting decision",
            "body": "",
            "category": "회의 결정사항",
            "author": "owner",
            "status": "pending",
            "source_type": "meeting_decision",
            "meeting_ref": {"meeting_id": "MT-HIDDEN", "session_id": "SS-MT-HIDDEN", "action_item_id": "d-MT-HIDDEN", "meeting_title": "Hidden Device Meeting"},
            "group_ids": [],
        },
        {
            "id": "cal-hidden-group",
            "date": "2026-05-16",
            "title": "Secret group PM",
            "body": "",
            "category": "장비 PM",
            "author": "owner",
            "status": "pending",
            "source_type": "manual",
            "meeting_ref": None,
            "group_ids": ["secret-group"],
        },
    ])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": False, "error": "forced failure"})

    out = meetings.ask_meeting(
        meetings.MeetingAskReq(question="회의에 등록된 이벤트와 변경점 관리 일반 이벤트를 같이 요약해줘"),
        object(),
    )

    assert out["ok"] is True
    assert out["scope"] == "auto"
    assert out["llm"]["used"] is False
    assert "Litho recipe release" in out["answer"]
    assert "Proceed with MASK_A" in out["answer"]
    assert "Secret hidden meeting decision" not in out["answer"]
    assert "Secret group PM" not in out["answer"]
    assert {e["title"] for e in out["calendar_events"]} == {
        "Litho recipe release",
        "1차 회의 결정사항: Proceed with MASK_A",
    }


def test_meeting_stream_route_precedes_dynamic_mid():
    get_paths = [
        route.path
        for route in meetings.router.routes
        if "GET" in (getattr(route, "methods", None) or set())
    ]

    assert get_paths.index("/api/meetings/stream") < get_paths.index("/api/meetings/{mid}")


def test_meeting_stream_uses_visibility_helper_signature(monkeypatch):
    meeting = _ask_fixture()
    monkeypatch.setattr(meetings, "_load", lambda: [meeting])
    monkeypatch.setattr(meetings, "current_user", lambda _request: {"username": "viewer", "role": "user"})
    monkeypatch.setattr(meetings, "_my_meeting_group_ids", lambda _username, _role: set())

    response = asyncio.run(meetings.stream_minutes(_DisconnectedRequest(), meeting_id="MT-ASK"))

    assert response.media_type == "text/event-stream"
