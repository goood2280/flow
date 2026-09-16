"""Offline contracts for home-chat report drafts and explicit, durable saves."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from core import data_chat_report as report
from core.utils import load_json
from routers import template_report


DEFINITION = """Q1
TABLE = ET
PRODUCT = PRODA
SQL = SELECT tkout_time, root_lot_id, wafer_id, value
RECENT_DAYS = 7
DATE_COLUMN = tkout_time

CHART
TYPE = scatter
TITLE = ET trend
X = tkout_time
Y = value
COLOR = root_lot_id
WIDTH = 1200
HEIGHT = 650
"""


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(report.PATHS, "data_root", tmp_path)
    monkeypatch.setattr(template_report, "STORE_FILE", tmp_path / "template_reports.json")
    monkeypatch.setattr(template_report, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(template_report, "BACKGROUND_FILE", tmp_path / "background.png")
    monkeypatch.setattr(template_report, "_chart_history", lambda: {})
    from core import llm_adapter
    monkeypatch.setattr(llm_adapter, "complete_json", lambda *a, **kw: pytest.fail("Simple report drafts must not call an LLM"))
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "admin"}), headers={})
    context = {"definition_code": DEFINITION, "product": "PRODA", "root_lot_id": "LOT01",
               "table": {"rows": [{"value": 1}], "total": 1},
               "chart_result": {"type": "scatter", "points": [{"x": 1, "y": 2}]}, "last_action": "chart"}
    return context, request


def draft(scenario, text="이 차트로 보고서 템플릿 만들어줘"):
    context, request = scenario
    result = report.handle(text, deepcopy(context), request)
    assert result["ok"], result
    return result


def test_validated_draft_does_not_save_and_keeps_chart(scenario):
    context, _ = scenario
    result = draft(scenario)
    assert not template_report.STORE_FILE.exists()
    assert result["context"]["table"] == context["table"]
    assert result["context"]["chart_result"] == context["chart_result"]
    assert result["tool"]["definition_code"] == DEFINITION
    template = result["tool"]["report_template"]
    assert len(template["pages"]) == len(template["pages"][0]["slots"]) == 1
    assert template["pages"][0]["slots"][0]["definition_code"] == DEFINITION
    assert result["tool"]["approval"]["kind"] == "report"
    saved_proposal = load_json(report._path(result["context"]["pending_report_id"]), {})
    assert saved_proposal["user"] == "qa_admin"


def test_explicit_approval_saves_snapshot_once_and_ignores_browser_template(scenario):
    _, request = scenario
    preview = draft(scenario)
    identifier = preview["context"]["pending_report_id"]
    context = deepcopy(preview["context"])
    context["report_template"] = {"name": "browser injection"}
    result = report.handle(f"승인 {identifier}", context, request)
    assert result["ok"], result
    rows = template_report._load_templates()
    assert len(rows) == 1
    assert rows[0]["pages"][0]["slots"][0]["definition_code"] == DEFINITION
    assert rows[0]["name"] != "browser injection"
    again = report.handle(f"승인 {identifier}", result["context"], request)
    assert "중복 저장하지" in again["reply"]
    assert len(template_report._load_templates()) == 1


@pytest.mark.parametrize("decision", ["네", "예", "진행", "진행해줘", "반영", "좋아", "승인하고 제목 바꿔줘"])
def test_ambiguous_utterances_never_save(scenario, decision):
    _, request = scenario
    preview = draft(scenario)
    result = report.handle(decision, deepcopy(preview["context"]), request)
    assert result is None or not template_report.STORE_FILE.exists()
    assert not template_report.STORE_FILE.exists()


def test_plain_approval_requires_current_report_target(scenario):
    _, request = scenario
    preview = draft(scenario)
    old = deepcopy(preview["context"])
    old["last_action"] = "location"
    assert report.handle("승인하겠다", old, request) is None
    assert not template_report.STORE_FILE.exists()
    accepted = report.handle("승인하겠다 진행하겠다", deepcopy(preview["context"]), request)
    assert accepted["ok"], accepted


def test_cancel_and_expiry_do_not_save(scenario, monkeypatch):
    _, request = scenario
    preview = draft(scenario)
    identifier = preview["context"]["pending_report_id"]
    result = report.handle(f"취소 {identifier}", deepcopy(preview["context"]), request)
    assert result["ok"] and "취소" in result["reply"]
    assert not report.handle(f"승인 {identifier}", deepcopy(preview["context"]), request)["ok"]
    preview = draft(scenario)
    monkeypatch.setattr(report.time, "time", lambda: load_json(report._path(preview["context"]["pending_report_id"]), {})["created"] + report.TTL_SECONDS + 1)
    assert not report.handle("승인", deepcopy(preview["context"]), request)["ok"]
    assert not template_report.STORE_FILE.exists()


def test_owner_and_admin_permissions(scenario):
    _, request = scenario
    preview = draft(scenario)
    other = SimpleNamespace(state=SimpleNamespace(user={"username": "another_admin", "role": "admin"}), headers={})
    result = report.handle(f'승인 {preview["context"]["pending_report_id"]}', deepcopy(preview["context"]), other)
    assert not result["ok"] and "본인" in result["reply"]
    ordinary = SimpleNamespace(state=SimpleNamespace(user={"username": "qa_admin", "role": "user"}), headers={})
    with pytest.raises(HTTPException) as exc:
        report.handle("승인", deepcopy(preview["context"]), ordinary)
    assert exc.value.status_code == 403
    assert not template_report.STORE_FILE.exists()


def test_title_revision_requires_new_approval_identifier(scenario):
    _, request = scenario
    preview = draft(scenario)
    original = preview["context"]["pending_report_id"]
    revised = report.handle('보고서 제목을 "주간 ET 추이"로 바꿔줘', deepcopy(preview["context"]), request)
    assert revised["ok"], revised
    assert revised["context"]["pending_report_id"] != original
    assert revised["tool"]["report_template"]["name"] == "주간 ET 추이"
    assert not report.handle(f"승인 {original}", deepcopy(revised["context"]), request)["ok"]
    assert not template_report.STORE_FILE.exists()
    assert report.handle("승인", deepcopy(revised["context"]), request)["ok"]


def test_inline_chart_without_definition_has_actionable_response(scenario):
    context, request = scenario
    context = deepcopy(context)
    context.pop("definition_code")
    result = report.handle("이 차트로 보고서 템플릿 만들어줘", context, request)
    assert not result["ok"]
    assert "ChartBuilder" in result["reply"] and "데이터 원천·SQL" in result["reply"]
    assert result["context"]["chart_result"] == context["chart_result"]
    assert not template_report.STORE_FILE.exists()


def test_invalid_definition_is_rejected_offline(scenario):
    context, request = scenario
    context = {**context, "definition_code": "CHART\nTYPE = made_up"}
    assert not report.handle("보고서 템플릿 만들어줘", context, request)["ok"]
    assert not template_report.STORE_FILE.exists()


def test_tagged_split_approval_is_not_consumed(scenario):
    context, request = scenario
    assert report.handle("승인 " + "a" * 32, context, request) is None


@pytest.mark.parametrize("after_expiry", [False, True])
def test_crash_after_canonical_save_retries_without_duplicate(scenario, monkeypatch, after_expiry):
    _, request = scenario
    preview = draft(scenario)
    identifier = preview["context"]["pending_report_id"]
    original_save = report.save_json

    def fail_applied_write(path, payload):
        if payload.get("status") == "applied":
            raise RuntimeError("simulated crash after canonical commit")
        return original_save(path, payload)

    monkeypatch.setattr(report, "save_json", fail_applied_write)
    with pytest.raises(RuntimeError):
        report.handle(f"승인 {identifier}", deepcopy(preview["context"]), request)
    rows = template_report._load_templates()
    assert len(rows) == 1
    saved_id = rows[0]["id"]
    assert load_json(report._path(identifier), {})["status"] == "pending"
    monkeypatch.setattr(report, "save_json", original_save)
    if after_expiry:
        created = load_json(report._path(identifier), {})["created"]
        monkeypatch.setattr(report.time, "time", lambda: created + report.TTL_SECONDS + 1)
    result = report.handle(f"승인 {identifier}", deepcopy(preview["context"]), request)
    assert result["ok"] and result["context"]["report_template_id"] == saved_id
    assert len(template_report._load_templates()) == 1


def test_concurrent_approvals_create_one_template(scenario):
    _, request = scenario
    preview = draft(scenario)
    identifier = preview["context"]["pending_report_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: report.handle(f"승인 {identifier}", deepcopy(preview["context"]), request), range(4)))
    assert all(result["ok"] for result in results)
    assert len(template_report._load_templates()) == 1


def test_new_report_draft_clears_competing_split_target(scenario):
    context, request = scenario
    result = report.handle("이 차트로 보고서 템플릿 만들어줘", {**context, "pending_split_id": "b" * 32}, request)
    assert result["ok"] and "pending_split_id" not in result["context"]


def test_changed_chart_requires_fresh_report_preview(scenario):
    _, request = scenario
    preview = draft(scenario)
    context = deepcopy(preview["context"])
    context["definition_code"] = DEFINITION.replace("RECENT_DAYS = 7", "RECENT_DAYS = 90")
    result = report.handle("승인", context, request)
    assert not result["ok"] and "차트가 변경" in result["reply"]
    assert not template_report.STORE_FILE.exists()


def test_stale_tagged_report_cannot_save_after_switching_to_split(scenario):
    _, request = scenario
    preview = draft(scenario)
    context = deepcopy(preview["context"])
    identifier = context.pop("pending_report_id")
    context.update(last_action="splittable.plan", pending_split_id="a" * 32)
    result = report.handle(f"승인 {identifier}", context, request)
    assert not result["ok"] and "승인 ID" in result["reply"]
    assert not template_report.STORE_FILE.exists()


def test_execute_draft_approval_preserves_chart_and_removes_pending(scenario):
    from core import data_chat
    context, request = scenario
    preview = data_chat.execute("이 차트로 보고서 템플릿 만들어줘", deepcopy(context), request)
    assert preview["ok"], preview
    assert preview["context"]["definition_code"] == DEFINITION
    assert preview["context"]["chart_result"] == context["chart_result"]
    assert preview["tool"]["chart_result"] == context["chart_result"]
    identifier = preview["context"]["pending_report_id"]
    accepted = data_chat.execute(f"승인 {identifier}", preview["context"], request)
    assert accepted["ok"], accepted
    assert "pending_report_id" not in accepted["context"]
    assert accepted["context"]["definition_code"] == DEFINITION
    assert accepted["context"]["table"] == context["table"]
    repeated = data_chat.execute(f"승인 {identifier}", accepted["context"], request)
    assert repeated["ok"] and "중복 저장하지" in repeated["reply"]
    assert "pending_report_id" not in repeated["context"]
    assert len(template_report._load_templates()) == 1
