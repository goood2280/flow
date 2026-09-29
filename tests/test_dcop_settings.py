import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import dcop


def _rule(**patch):
    rule = {
        "id": "rule-1",
        "column": "PRODUCT",
        "operator": "blank",
        "value": "",
        "compareColumn": "",
        "uniqueColumns": ["PRODUCT", "STEP_ID"],
        "uniqueColumnsText": "PRODUCT, STEP_ID",
        "severity": "fail",
        "message": "필수값 누락",
        "enabled": True,
    }
    rule.update(patch)
    return rule


def test_dcop_settings_are_persisted_under_flow_data_and_loaded_again(tmp_path, monkeypatch):
    monkeypatch.setattr(dcop, "PATHS", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(dcop, "_audit_user", lambda *args, **kwargs: None)

    saved = dcop.settings_put(
        dcop.SettingsReq(settings={"rules": [_rule()]}),
        user={"username": "admin", "role": "admin"},
    )

    path = tmp_path / "dcop" / "settings.json"
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["settings"]["rules"][0]["message"] == "필수값 누락"
    assert saved["store"] == "flow-data/dcop/settings.json"

    loaded = dcop.settings_payload({"username": "admin", "role": "admin"})
    assert loaded["exists"] is True
    assert loaded["can_edit"] is True
    assert loaded["settings"] == saved["settings"]


def test_dcop_settings_load_legacy_top_level_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(dcop, "PATHS", SimpleNamespace(data_root=tmp_path))
    path = tmp_path / "dcop" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"rules": [_rule(operator="equals", value="P1")]}), encoding="utf-8")

    document, exists = dcop.load_document()

    assert exists is True
    assert document["settings"]["rules"][0]["operator"] == "equals"
    assert document["settings"]["rules"][0]["value"] == "P1"


def test_dcop_settings_reject_unknown_rule_operator(tmp_path, monkeypatch):
    monkeypatch.setattr(dcop, "PATHS", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(dcop, "_audit_user", lambda *args, **kwargs: None)

    with pytest.raises(HTTPException, match="지원하지 않거나"):
        dcop.settings_put(
            dcop.SettingsReq(settings={"rules": [_rule(operator="drop_everything")]}),
            user={"username": "admin", "role": "admin"},
        )

    assert not (tmp_path / "dcop" / "settings.json").exists()


def _fake_llm(monkeypatch, obj, *, ok=True, available=True):
    from core import llm_adapter

    calls = []
    monkeypatch.setattr(llm_adapter, "is_available", lambda: available)
    monkeypatch.setattr(llm_adapter, "get_config", lambda redact=True: {"model": "fake-model"})

    def complete_json(prompt, **kwargs):
        calls.append(json.loads(prompt))
        return {"ok": ok, "obj": obj, "error": "" if ok else "timeout"}

    monkeypatch.setattr(llm_adapter, "complete_json", complete_json)
    return calls


def _draft(prompt="규칙", columns=("PRODUCT", "STEP_ID", "DCOP_NAME", "USE_YN", "LOW", "HIGH")):
    return dcop.rules_llm_draft(
        dcop.RuleDraftReq(prompt=prompt, columns=list(columns), sample_rows=[["P1", "S1", "N", "Y", "1", "2"]]),
        user={"username": "admin", "role": "admin"},
    )


def test_dcop_llm_draft_is_admin_only():
    from core.auth import require_admin

    route = next(r for r in dcop.router.routes if r.path == "/api/dcop/rules/llm/draft")
    assert any(dep.call is require_admin for dep in route.dependant.dependencies)
    request = SimpleNamespace(state=SimpleNamespace(user={"username": "mgr", "role": "user"}), headers={})
    with pytest.raises(HTTPException) as excinfo:
        require_admin(request)
    assert excinfo.value.status_code == 403


def test_dcop_llm_draft_is_allowed_by_llm_gate():
    from core import llm_adapter

    assert "/api/dcop/rules/llm/draft" in llm_adapter._DATA_TASK_PATHS


def test_dcop_llm_draft_normalizes_validates_and_dedupes(tmp_path, monkeypatch):
    monkeypatch.setattr(dcop, "PATHS", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(dcop, "_audit_user", lambda *args, **kwargs: None)
    dcop.settings_put(dcop.SettingsReq(settings={"rules": [_rule()]}), user={"username": "admin", "role": "admin"})
    calls = _fake_llm(monkeypatch, {
        "rules": [
            {"operator": "max_length", "column": "DCOP_NAME", "value": 40, "severity": "warning", "message": "40자 초과"},
            {"operator": "allowed_values", "column": "USE_YN", "value": ["Y", "N"]},
            {"operator": "order_asc", "columns": ["LOW", "HIGH"]},
            {"operator": "blank", "columns": ["STEP_ID", "PRODUCT"]},        # 기존 규칙과 같음
            {"operator": "drop_table", "column": "PRODUCT"},                 # 지원 안 함
            {"operator": "gt", "column": "LOW", "value": "abc"},             # 숫자 아님
            {"operator": "regex", "column": "DCOP_NAME", "value": "("},      # 잘못된 정규식
            {"operator": "order_asc", "columns": ["LOW"]},                   # 열 부족
            {"operator": "equals", "column": "LOT_TYPE", "value": "ENG"},    # 표에 없는 열 → 경고만
        ],
        "warnings": ["단위 검사는 지원하지 않음"],
    })

    out = _draft()

    assert out["ok"] is True and out["model"] == "fake-model"
    rules = out["rules"]
    assert [rule["operator"] for rule in rules] == ["max_length", "allowed_values", "order_asc", "equals"]
    assert all(rule["source"] == "llm" and rule["enabled"] for rule in rules)
    assert rules[0]["value"] == "40" and rules[0]["severity"] == "warning"
    assert rules[1]["value"] == "Y,N" and rules[1]["severity"] == "fail"
    assert rules[2]["uniqueColumns"] == ["LOW", "HIGH"] and rules[2]["column"] == "LOW"
    text = " ".join(out["warnings"])
    for expected in ("단위 검사", "이미 있는 규칙", "drop_table", "숫자가 아니라", "정규식", "LOT_TYPE"):
        assert expected in text
    # 초안은 저장하지 않는다.
    assert len(dcop.load_document()[0]["settings"]["rules"]) == 1
    sent = calls[0]
    assert sent["columns"][0] == "PRODUCT" and sent["sample_rows"][0]["USE_YN"] == "Y"
    assert sent["existing_rules"][0]["operator"] == "blank"


def test_dcop_llm_draft_reports_disconnected_or_failed_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(dcop, "PATHS", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(dcop, "_audit_user", lambda *args, **kwargs: None)

    _fake_llm(monkeypatch, {}, available=False)
    assert _draft()["reason"] == "llm_not_connected"

    _fake_llm(monkeypatch, {}, ok=False)
    out = _draft()
    assert out["ok"] is False and out["reason"] == "llm_call_failed" and "timeout" in out["message"]

    with pytest.raises(HTTPException):
        _draft(prompt="   ")


def test_dcop_llm_rule_source_survives_save_and_manual_rules_stay_unmarked(tmp_path, monkeypatch):
    monkeypatch.setattr(dcop, "PATHS", SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(dcop, "_audit_user", lambda *args, **kwargs: None)

    dcop.settings_put(
        dcop.SettingsReq(settings={"rules": [_rule(), _rule(id="rule-2", source="llm"), _rule(id="rule-3", source="hacked")]}),
        user={"username": "admin", "role": "admin"},
    )

    rules = dcop.load_document()[0]["settings"]["rules"]
    assert "source" not in rules[0]
    assert rules[1]["source"] == "llm"
    assert "source" not in rules[2]
