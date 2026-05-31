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
from routers import informs, splittable, tracker  # noqa: E402


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
    assert "+ LOT 노트 ({drawerRoot})" in ui


def test_splittable_knob_rule_modal_checks_current_row_values():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_SplitTable.jsx").read_text(encoding="utf-8")
    assert "matchKnobRuleToRowValues" in ui
    assert "normalizeKnobRuleValue(group?.category)" in ui
    assert "[\"actual\",cell?.actual]" in ui
    assert "[\"plan\",cell?.plan]" in ui
    assert "[\"pending\",pending]" in ui
    assert "openRuleMatchView(rowMatchKind,rowParam,row)" in ui


def test_splittable_view_has_split_check_display_toggle():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_SplitTable.jsx").read_text(encoding="utf-8")
    snapshot_view = (ROOT / "frontend" / "src" / "components" / "SplitTableSnapshotView.jsx").read_text(encoding="utf-8")
    backend = (ROOT / "backend" / "routers" / "splittable.py").read_text(encoding="utf-8")
    assert "showSplitCheckView" in ui
    assert "Split 체크 표시" in ui
    assert "isInlineVmSplitParam" in ui
    assert "splitCheckDisabled" in ui
    assert "disabled={splitCheckDisabled}" in ui
    assert "display_mode=split_check" in ui
    assert 'import SplitTableSnapshotView, { buildSplitCheckStView, SPLIT_CHECK_PREFIX_COLUMNS }' in ui
    assert "const splitCheckStView=buildSplitCheckStView" in ui
    assert "prefix_columns:SPLIT_CHECK_PREFIX_COLUMNS" in ui
    assert 'display_mode:"split_check"' in ui
    assert "<SplitTableSnapshotView" in ui
    assert "splitCheckRows" not in ui
    assert 'export const SPLIT_CHECK_PREFIX_COLUMNS = ["항목", "값", "Split"]' in snapshot_view
    assert "rowSpan: span" in snapshot_view
    assert "Split 체크로 표시할 값이 없습니다" in snapshot_view
    assert "KNOB별 step_desc → step_id 요약" in ui
    assert "KNOB별 step_desc → step_id 요약" in snapshot_view
    assert "function_step</th>" not in snapshot_view
    assert "복수 step_id 이므로 적용 전 담당 엔지니어가 실제 사용 step_id를 확인해 주세요." not in snapshot_view
    assert 'display_mode: str = Query("")' in backend
    assert "SPLIT_CHECK_XLSX_PREFIX_COLUMNS = [\"항목\", \"값\", \"Split\"]" in backend


def test_splittable_split_check_xlsx_rows_use_plan_as_display_value():
    rows = splittable._build_split_check_export_rows(
        ["KNOB_GATE"],
        3,
        {"KNOB_GATE": ({0: "R1", 1: "R1", 2: "R3"}, {1: "R2"})},
        {"KNOB_GATE": "KNOB_GATE"},
    )

    assert rows == [
        ["KNOB_GATE", "R1", "S0", "✓", "", ""],
        ["KNOB_GATE", "R2", "S1", "", "✓", ""],
        ["KNOB_GATE", "R3", "S2", "", "", "✓"],
    ]
    assert not splittable._split_check_export_supported(["INLINE_TEMP"])
    assert not splittable._split_check_export_supported(["VM_STEP_ITEM"])


def test_meeting_page_does_not_embed_flowi_prompt_box():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Meeting.jsx").read_text(encoding="utf-8")
    assert "FlowiPromptBox" not in ui
    assert "Flow-i 회의 질문" not in ui


def test_home_flowi_empty_chat_greeting_copy():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Home.jsx").read_text(encoding="utf-8")
    assert "오늘 어떤 도움을 드릴까요?" in ui
    assert "flow-i 대화가 여기 이어집니다." not in ui
    assert "/api/llm/flowi/verify" in ui
    assert "연결확인중" in ui
    assert "연결 확인 지연" in ui
    assert "LLM 확인 실패" in ui
    assert 'd?.error==="llm unavailable"' in ui
    assert "flowiPromptProgressLines" in ui
    assert "flowiIsStepIdToken" in ui
    assert "공정/기능 step 정보를 확인하는 요청" in ui
    assert "관련 데이터를 확인하는 요청" in ui
    assert "SplitTable 데이터를 조회해 화면에 바로 보여줄 결과" in ui
    assert "요청 해석" in ui
    assert "답변 준비 중" in ui
    assert "FLOWI_LIVE_STEPS" in ui
    assert "FLOWI_CLIENT_TIMEOUT_MS=105000" in ui
    assert "flowiLiveExecutionLines" not in ui
    assert "예상 조회 경로" not in ui
    assert "예상 단위기능: SplitTable view" not in ui
    assert "후보가 하나면 자동 확정" not in ui
    assert "실제 실행" in ui
    assert "flowiSplitApiCall" in ui
    assert "data/Fab/ML_TABLE 계열" not in ui
    assert "result_renderer" not in ui
    assert "워크플로우 리스트" not in ui
    assert "워크플로우 관리" not in ui
    assert "/api/llm/flowi/workflows" not in ui
    assert "/api/llm/flowi/workflows/draft" not in ui
    assert "AI 형식화" not in ui
    assert "연결끊김" not in ui
    assert "flowiStartle" not in ui
    assert "READYING" not in ui


def test_home_flowi_split_table_uses_snapshot_renderer_and_collapsed_context():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Home.jsx").read_text(encoding="utf-8")

    assert 'import SplitTableSnapshotView from "../components/SplitTableSnapshotView"' in ui
    assert "function flowiSplitStView" in ui
    assert "<SplitTableSnapshotView" in ui
    assert "return <details" in ui
    assert "요청 해석 / 진행 방식" in ui
    assert "<FlowiMarkdown text={result.answer||emptyHint}/>" in ui
    assert ui.index("<FlowiMarkdown text={result.answer||emptyHint}/>") < ui.index("<FlowiInterpretationSummary")


def test_home_flowi_clarification_renders_plain_choice_reply():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Home.jsx").read_text(encoding="utf-8")

    assert "isClarificationOnly" in ui
    assert "flowiResultShellStyle" in ui
    assert "!isClarificationOnly&&<FlowiInterpretationSummary" in ui
    assert "!isClarificationOnly&&<FlowiExecutionProof" in ui
    assert "!isClarificationOnly&&<FlowiActionLogPanel" in ui


def test_agent_page_exposes_unit_ai_and_llm_settings():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Diagnosis.jsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend" / "src" / "global.css").read_text(encoding="utf-8")
    assert "에이전트" in ui
    assert "단위기능 AI" in ui
    assert "FileBrowser AI SQL" in ui
    assert "Inform 등록 도우미" in ui
    assert "Step ID 매칭" in ui
    assert "PPID Knob 분류" in ui
    assert "Flow-i" in ui
    assert "FLOWI_FEW_SHOT_QUESTIONS" not in ui
    assert "/api/llm/flowi/workflows" in ui
    assert "flowiWorkflowPromptPreview" in ui
    assert "주요 few-shot 질문" in ui
    assert "Semantic layer" in ui
    assert "LLM 설정" in ui
    assert "질문 이력" in ui
    assert "State" in ui
    assert "LangGraph" in ui
    assert "Test prompt" in ui
    assert "/api/agent/catalog" in ui
    assert "/api/agent/unit/${encodeURIComponent(unitKey)}/run" in ui
    assert 'agentUnitHistoryEndpoint("dashboard_agent")' in ui
    assert "/api/agent/semantic/lexicon" in ui
    assert "/api/agent/semantic/sources" in ui
    assert "/api/agent/semantic/measurements" in ui
    assert "Source catalog" in ui
    assert "source_catalog" in ui
    assert "source 저장" in ui
    assert "source 추가" in ui
    assert "Measurement terms" in ui
    assert "measurement 추가" in ui
    assert 'method: "DELETE"' in ui
    assert "measurement_terms" in ui
    assert "related_question_ids" in ui
    assert "active Agent unit route" in ui
    assert "Inform graph fetch 진단" in ui
    assert "Human review" in ui
    assert "can_confirm" in ui
    assert "approval_status" in ui
    assert "step_lookup" in ui
    assert "ppid_knob" in ui
    assert "state_design" in ui
    assert "Persona" in ui
    assert "State I/O" in ui
    assert "공유 state" in ui
    assert "실행 결과" in ui
    assert "flow-agent-unit-grid" in ui
    assert "flow-agent-node-grid" in ui
    assert ".flow-agent-unit-grid" in css
    assert "repeat(auto-fit" in css
    assert "LlmTab" in ui
    assert "/api/agent/runtime" not in ui


def test_common_loading_component_shows_progress_cues():
    ui = (ROOT / "frontend" / "src" / "components" / "Loading.jsx").read_text(encoding="utf-8")
    assert "flowLoadingSweep" in ui
    assert "aria-live=\"polite\"" in ui
    assert "데이터 확인" in ui
    assert "데이터 준비 중" in ui


def test_meeting_issue_import_renders_lot_table():
    ui = (ROOT / "frontend" / "src" / "pages" / "My_Meeting.jsx").read_text(encoding="utf-8")
    assert "function IssueLotTable" in ui
    assert "data-testid=\"meeting-issue-lot-table\"" in ui
    assert "LOT 테이블" in ui
    assert "mergeIssueForDisplay(a.issue_ref" in ui
    assert "mergeIssueForDisplay(agendaDraft.issue_ref" in ui


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


def test_tracker_comment_alert_title_and_mail_gate(tmp_path, monkeypatch):
    import core.mail as flow_mail
    import core.notify as flow_notify

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

    events = []
    mails = []
    monkeypatch.setattr(flow_notify, "emit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(flow_mail, "send_mail", lambda **kwargs: mails.append(kwargs) or {"ok": True})

    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "issue_owner", "role": "admin"})
    created = tracker.create_issue(
        tracker.IssueCreate(title="Mail gate issue", description="mail gate", category="Monitor"),
        object(),
    )
    issue_id = created["id"]

    monkeypatch.setattr(tracker, "current_user", lambda _request: {"username": "commenter", "role": "user"})
    tracker.add_comment(tracker.CommentReq(issue_id=issue_id, text="first alert comment"), object())

    assert events[-1][0][0] == "my_tracker_comment"
    assert events[-1][1]["target_user"] == "issue_owner"
    assert events[-1][1]["title"].startswith("FLOW 알림 - ")
    assert "/ 이슈 댓글 · Mail gate issue" in events[-1][1]["title"]
    assert mails == []

    issues = json.loads(issues_file.read_text(encoding="utf-8"))
    issues[0]["mail_watch"] = {"enabled": True, "mail_group_ids": []}
    issues_file.write_text(json.dumps(issues), encoding="utf-8")

    tracker.add_comment(tracker.CommentReq(issue_id=issue_id, text="mail enabled comment"), object())

    assert len(mails) == 1
    assert mails[0]["receiver_usernames"] == ["issue_owner"]
    assert mails[0]["title"].startswith("FLOW 알림 - ")
    assert "/ 이슈 댓글 · Mail gate issue" in mails[0]["title"]


def test_splittable_note_images_comments_and_tracker_owner_alert(tmp_path, monkeypatch):
    import core.auth as flow_auth
    import core.mail as flow_mail
    import core.notify as flow_notify

    notes_file = tmp_path / "notes.json"
    tracker_file = tmp_path / "issues.json"
    tracker_file.write_text(json.dumps([{
        "id": "ISS-NOTE",
        "title": "Tagged lot issue",
        "username": "issue_owner",
        "product": "PRODA",
        "mail_watch": {"enabled": True, "mail_group_ids": []},
        "lots": [{"product": "PRODA", "root_lot_id": "R1000", "wafer_id": "3"}],
    }]), encoding="utf-8")

    monkeypatch.setattr(splittable, "NOTES_FILE", notes_file)
    monkeypatch.setattr(splittable, "TRACKER_ISSUES_FILE", tracker_file)
    monkeypatch.setattr(flow_auth, "current_user", lambda _request: {"username": "note_writer", "role": "user"})
    monkeypatch.setattr(splittable, "current_user", lambda _request: {"username": "note_writer", "role": "user"})

    events = []
    mails = []
    monkeypatch.setattr(flow_notify, "emit_event", lambda *args, **kwargs: events.append((args, kwargs)))
    monkeypatch.setattr(flow_mail, "send_mail", lambda **kwargs: mails.append(kwargs) or {"ok": True})

    saved = splittable.save_note(
        splittable.NoteSaveReq(
            scope="wafer",
            product="PRODA",
            root_lot_id="R1000",
            wafer_id="3",
            text="\u200B",
            images=[{"filename": "../paste.png", "url": "/api/informs/files/up_1/paste.png?t=token", "size": 42}],
        ),
        object(),
    )
    note_id = saved["entry"]["id"]

    assert saved["entry"]["images"][0]["filename"] == "paste.png"
    assert saved["entry"]["images"][0]["url"] == "/api/informs/files/up_1/paste.png"
    assert saved["entry"]["text"] == ""
    assert events[-1][0][0] == "my_tracker_lot_note"
    assert events[-1][1]["target_user"] == "issue_owner"
    assert events[-1][1]["title"].startswith("FLOW 알림 - ")
    assert mails[-1]["receiver_usernames"] == ["issue_owner"]

    commented = splittable.add_note_comment(
        splittable.NoteCommentReq(
            note_id=note_id,
            text="\u200B",
            images=[{"filename": "reply.png", "url": "/api/informs/files/up_2/reply.png", "size": 7}],
        ),
        object(),
    )
    notes = splittable._load_notes()

    assert commented["comment"]["images"][0]["filename"] == "reply.png"
    assert notes[0]["comments"][0]["text"] == ""
    assert len(mails) == 1


def test_splittable_notes_response_normalizes_legacy_image_shapes(tmp_path, monkeypatch):
    notes_file = tmp_path / "notes.json"
    notes_file.write_text(json.dumps({"entries": [{
        "id": "n_legacy",
        "scope": "lot",
        "key": "PRODA__LOT__R1000",
        "text": "\u200B",
        "username": "hol",
        "created_at": "2026-05-09T16:37:15",
        "images": [{"attachment": {"downloadUrl": "/api/informs/files/up_3/paste.png?t=token"}, "displayName": "paste.png"}],
        "comments": [{
            "id": "c_legacy",
            "text": "\u200B",
            "images": [{"file": {"fileUrl": "files/up_4/reply.png"}, "name": "reply.png"}],
        }],
    }]}), encoding="utf-8")
    monkeypatch.setattr(splittable, "NOTES_FILE", notes_file)

    result = splittable.list_notes(product="PRODA", root_lot_id="R1000")
    note = result["notes"][0]

    assert note["text"] == ""
    assert note["images"] == [{"filename": "paste.png", "url": "/api/informs/files/up_3/paste.png", "size": 0}]
    assert note["comments"][0]["text"] == ""
    assert note["comments"][0]["images"] == [{"filename": "reply.png", "url": "/api/informs/files/up_4/reply.png", "size": 0}]


def test_inform_upload_infers_image_extension_from_mime_for_pasted_images():
    assert informs._image_upload_ext("", "image/png") == ".png"
    assert informs._image_upload_ext("clipboard", "image/jpeg") == ".jpg"
    assert informs._image_upload_ext("already.webp", "") == ".webp"
    assert informs._image_upload_ext("not-image", "application/octet-stream") == ""


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
