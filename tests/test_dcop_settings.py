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
