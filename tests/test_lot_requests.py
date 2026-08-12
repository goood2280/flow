import json

import pytest
from fastapi import HTTPException

from backend.routers import lot_requests as mod


@pytest.fixture()
def board(tmp_path, monkeypatch):
    store = tmp_path / "lot_requests"
    store.mkdir()
    monkeypatch.setattr(mod, "STORE_DIR", store)
    monkeypatch.setattr(mod, "REQUESTS_FILE", store / "requests.json")
    monkeypatch.setattr(mod, "AUDIT_FILE", store / "audit.jsonl")
    monkeypatch.setattr(mod, "CONFIG_FILE", store / "config.json")
    actor = {"username": "alice", "name": "Alice", "role": "user", "tabs": "lotrequest"}
    monkeypatch.setattr(mod, "_user", lambda _request: dict(actor))
    # 실제 is_page_manager 와 같은 규칙: global admin 은 언제나 통과, 그 외에는 위임 목록.
    monkeypatch.setattr(
        mod, "is_page_manager",
        lambda user, page: page == "lotrequest" and (
            (user or {}).get("role") == "admin" or (user or {}).get("username") == "pi.user"
        ),
    )
    return actor


def create_one():
    return mod.create_request(mod.RequestCreate(
        request_type="lot_assignment", product="PROD-A", title="평가용 랏 2장 배정",
        details="금주 내 평가가 필요합니다.", target_lots="2 lots", requester_team="Module",
        priority="high",
    ), None)


def test_content_ownership_and_full_history(board):
    item = create_one()
    request_id = item["id"]
    assert item["author"] == "alice"
    assert item["status"] == "registered"
    assert item["status_history"][0]["to"] == "registered"

    # Another authenticated user cannot edit the request body.
    board.update(username="bob", name="Bob")
    with pytest.raises(HTTPException) as denied:
        mod.update_request(request_id, mod.RequestUpdate(
            request_type="other", product="PROD-X", title="tamper", details="tamper",
        ), None)
    assert denied.value.status_code == 403

    # PI writes a response, and only that response author can later change it.
    board.update(username="pi.user", name="PI User")
    detail = mod.create_response(request_id, mod.ResponseWrite(
        body="평가 랏을 배정했습니다.", assigned_lots="LOT001, LOT002",
    ), None)
    response_id = detail["responses"][0]["id"]
    assert detail["responses"][0]["author"] == "pi.user"

    board.update(username="alice", name="Alice")
    with pytest.raises(HTTPException) as denied_response:
        mod.update_response(request_id, response_id, mod.ResponseWrite(body="changed"), None)
    assert denied_response.value.status_code == 403

    board.update(username="pi.user", name="PI User")
    detail = mod.update_response(request_id, response_id, mod.ResponseWrite(
        body="평가 랏 배정을 완료했습니다.", assigned_lots="LOT001, LOT002",
    ), None)
    response = detail["responses"][0]
    assert response["edit_history"][0]["snapshot"]["body"] == "평가 랏을 배정했습니다."

    detail = mod.update_status(request_id, mod.StatusUpdate(
        status="completed", note="LOT001, LOT002 배정 완료",
    ), None)
    assert detail["status"] == "completed"
    assert detail["processed_by"] == "pi.user"
    assert detail["status_history"][-1]["actor"] == "pi.user"
    assert detail["status_history"][-1]["note"] == "LOT001, LOT002 배정 완료"
    actions = {event["action"] for event in detail["activity_history"]}
    assert {"request_created", "response_created", "response_updated", "status_changed"} <= actions

    audit = [json.loads(line) for line in mod.AUDIT_FILE.read_text("utf-8").splitlines()]
    assert {row["action"] for row in audit} >= {
        "request_created", "response_created", "response_updated", "status_changed",
    }


def test_only_page_manager_or_admin_can_change_processing_status(board):
    request_id = create_one()["id"]
    with pytest.raises(HTTPException) as denied:
        mod.update_status(request_id, mod.StatusUpdate(status="completed"), None)
    assert denied.value.status_code == 403

    # 처리 권한 = 이 페이지 위임자 + global admin.
    board.update(username="global.admin", name="Global Admin", role="admin")
    detail = mod.update_status(request_id, mod.StatusUpdate(status="completed"), None)
    assert detail["status"] == "completed"
    assert detail["processed_by"] == "global.admin"
    assert detail["permissions"]["can_respond"] is True


def test_update_without_target_lots_keeps_the_legacy_value(board):
    # 화면에서 "배정 요청 랏/수량" 칸이 사라졌다. 그 칸 없이 저장해도 예전 기록은 남아야 한다.
    item = create_one()
    assert item["target_lots"] == "2 lots"
    updated = mod.update_request(item["id"], mod.RequestUpdate(
        request_type="랏 배정", product="PROD-A", title="평가용 랏 2장 배정",
        details="상세 요청만 수정합니다.",
    ), None)
    assert updated["target_lots"] == "2 lots"
    assert updated["details"] == "상세 요청만 수정합니다."

    # 답글의 "배정/처리 랏" 칸도 같은 규칙이다.
    board.update(username="pi.user", name="PI User")
    detail = mod.create_response(item["id"], mod.ResponseWrite(body="배정했습니다.", assigned_lots="LOT001"), None)
    response_id = detail["responses"][0]["id"]
    edited = mod.update_response(item["id"], response_id, mod.ResponseWrite(body="본문만 고칩니다."), None)
    assert edited["responses"][0]["assigned_lots"] == "LOT001"


def test_legacy_in_progress_is_exposed_and_counted_as_registered(board):
    item = create_one()
    stored = json.loads(mod.REQUESTS_FILE.read_text("utf-8"))
    stored[0]["status"] = "in_progress"
    mod.REQUESTS_FILE.write_text(json.dumps(stored, ensure_ascii=False), "utf-8")

    detail = mod.get_request(item["id"], None)
    stats = mod.request_stats(None, product="", request_type="", requester_team="", author="", q="", mine=False)
    assert detail["status"] == "registered"
    assert stats["status"]["registered"] == 1


def test_rich_request_and_response_are_sanitized(board):
    item = mod.create_request(mod.RequestCreate(
        request_type="hot_grade", product="PROD-A", title="Hot grade 요청",
        details=(
            '<p>아래 표 기준으로 처리해주세요.</p>'
            '<table><tr><th>LOT</th></tr><tr><td>L001</td></tr></table>'
            '<img src="/api/lot-requests/files/abc/p.png" onerror="alert(1)">'
        ),
    ), None)
    assert "<table" in item["details"]
    assert "onerror" not in item["details"]

    board.update(username="pi.user", name="PI User")
    detail = mod.create_response(item["id"], mod.ResponseWrite(
        body='<b>완료</b><script>alert(1)</script>',
    ), None)
    assert detail["responses"][0]["body"] == "<b>완료</b>"


def test_delete_is_owner_only_and_soft_deletes_request(board):
    request_id = create_one()["id"]
    board.update(username="bob")
    with pytest.raises(HTTPException) as denied:
        mod.delete_request(request_id, None)
    assert denied.value.status_code == 403

    board.update(username="alice")
    assert mod.delete_request(request_id, None) == {"ok": True}
    stored = json.loads(mod.REQUESTS_FILE.read_text("utf-8"))
    assert stored[0]["deleted_by"] == "alice"
    listed = mod.list_requests(None, status="", request_type="", product="", requester_team="", author="", q="", mine=False)
    assert listed["requests"] == []


def test_product_team_author_filters_and_stats_facets(board):
    create_one()
    mod.create_request(mod.RequestCreate(
        request_type="hot_grade", product="PROD-B", title="B제품 Hot grade",
        details="B제품 처리 요청", requester_team="수율개선",
    ), None)

    listed = mod.list_requests(
        None, status="", request_type="", product="PROD-A", requester_team="Module",
        author="alice", q="평가용", mine=False,
    )
    stats_a = mod.request_stats(None, product="prod-a", request_type="", requester_team="Module", author="alice", q="", mine=False)
    stats_b = mod.request_stats(None, product="PROD-B", request_type="", requester_team="", author="", q="", mine=False)

    assert len(listed["requests"]) == 1
    assert stats_a["total"] == 1
    assert stats_a["status"]["registered"] == 1
    assert stats_b["total"] == 1
    assert stats_b["mine"] == 1
    assert stats_b["facets"]["products"] == ["PROD-A", "PROD-B"]
    assert {row["username"] for row in stats_b["facets"]["authors"]} == {"alice"}


def test_only_delegated_processor_can_reply_or_mail(board):
    item = create_one()
    assert item["permissions"]["can_respond"] is False
    assert item["permissions"]["can_mail"] is False

    with pytest.raises(HTTPException) as reply_denied:
        mod.create_response(item["id"], mod.ResponseWrite(body="처리 답변"), None)
    assert reply_denied.value.status_code == 403

    with pytest.raises(HTTPException) as mail_denied:
        mod.preview_mail(item["id"], mod.MailWrite(to_users=["pi.user"]), None)
    assert mail_denied.value.status_code == 403

    board.update(username="pi.user", name="PI User")
    detail = mod.create_response(item["id"], mod.ResponseWrite(body="위임자 처리 답변"), None)
    assert detail["permissions"]["can_respond"] is True
    assert detail["permissions"]["can_mail"] is True


def test_mail_preview_and_send_are_recorded_in_issue_history(board, monkeypatch):
    item = create_one()
    board.update(username="pi.user", name="PI User")
    mod.create_response(item["id"], mod.ResponseWrite(
        body="LOT001을 배정했습니다.", assigned_lots="LOT001",
    ), None)

    payload = mod.MailWrite(to_users=["alice"], subject="PI 처리 공유", body="처리 결과를 공유합니다.")
    preview = mod.preview_mail(item["id"], payload, None)
    assert preview["subject"] == "PI 처리 공유"
    assert "LOT001을 배정했습니다." in preview["html"]
    assert "업무 히스토리" in preview["html"]
    assert "go/flow_process-request" in preview["html"]

    from core import mail as mail_core
    monkeypatch.setattr(mail_core, "send_mail", lambda *args, **kwargs: {
        "ok": True, "status": 200, "to": ["alice@example.com"], "dry_run": True,
    })
    sent = mod.send_request_mail(item["id"], payload, None)
    assert sent["ok"] is True

    detail = mod.get_request(item["id"], None)
    assert detail["mail_history"][-1]["actor"] == "pi.user"
    assert detail["mail_history"][-1]["to"] == ["alice@example.com"]
    assert detail["activity_history"][0]["action"] == "mail_sent"


def test_page_manager_can_configure_request_types_and_requesting_teams(board):
    defaults = mod.get_board_config(None)
    assert "랏 배정" in defaults["request_types"]
    assert defaults["can_edit"] is False

    board.update(username="pi.user", name="PI User")
    saved = mod.save_board_config(mod.ConfigWrite(
        request_types=["랏 배정", "Hot grade", "분석 요청"],
        request_teams=["Module", "수율개선", "PI"],
    ), None)
    assert saved["can_edit"] is True
    assert saved["request_types"][-1] == "분석 요청"
    assert mod.get_board_config(None)["request_teams"] == ["Module", "수율개선", "PI"]
