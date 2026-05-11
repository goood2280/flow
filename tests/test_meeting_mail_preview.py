from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import meetings  # noqa: E402


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
