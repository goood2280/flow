from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from backend.routers import informs  # noqa: E402
from routers import auth as auth_router  # noqa: E402
from routers import groups as groups_router  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _request():
    return object()


def test_module_user_reverse_index_builds_module_to_usernames():
    mapping = {
        "alice": ["GATE", "STI", "GATE", ""],
        "bob": ["PC", "GATE"],
        "": ["GATE"],
        "charlie": [],
    }

    out = informs.build_inform_module_user_index(mapping)

    assert out == {
        "GATE": ["alice", "bob"],
        "STI": ["alice"],
        "PC": ["bob"],
    }


def test_send_mail_auto_groups_empty_recipients_by_inform_module(tmp_path, monkeypatch):
    admin_settings = tmp_path / "admin_settings.json"
    informs_file = tmp_path / "informs.json"
    _write_json(admin_settings, {
        "mail": {
            "enabled": True,
            "api_url": "dry-run",
            "from_addr": "flow@example.test",
            "domain": "example.test",
        },
        "inform_user_modules": {
            "alice": ["GATE"],
            "bob": ["GATE", "STI"],
            "carol": ["PC"],
        },
    })
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "lotmgr"})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_build_inform_snapshot_xlsx", lambda _target: None)
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "alice", "email": "alice@example.test", "status": "approved", "role": "user"},
        {"username": "bob", "email": "", "status": "approved", "role": "user"},
        {"username": "carol", "email": "carol@example.test", "status": "approved", "role": "user"},
    ])
    informs._save([{
        "id": "inf_gate",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "author": "writer",
        "created_at": "2026-04-30T10:20:00",
        "mail_history": [],
    }])

    res = informs.send_mail("inf_gate", informs.SendMailReq(), _request())

    assert res["auto_module_used"] is True
    assert res["to"] == ["alice@example.test", "bob@example.test"]
    assert res["subject"] == "[plan 적용 통보] PRODA R1000 - GATE"
    saved = informs._load()[0]
    assert saved["flow_status"] == "mail_completed"
    assert saved["status_history"][-1]["status"] == "mail_completed"
    payload = res["preview_data"]
    assert payload["receiverList"] == [
        {"email": "alice@example.test", "recipientType": "TO", "seq": 1},
        {"email": "bob@example.test", "recipientType": "TO", "seq": 2},
    ]

    html = payload["content"]
    assert "안녕하세요" not in html
    assert "사유: PEMS" not in html
    assert "작성자" in html
    assert "writer" in html
    assert "작성시간" in html
    assert "2026-04-30 10:20" in html
    assert "Lot 리스트" not in html


def test_send_mail_explicit_to_users_does_not_auto_group(tmp_path, monkeypatch):
    admin_settings = tmp_path / "admin_settings.json"
    informs_file = tmp_path / "informs.json"
    _write_json(admin_settings, {
        "mail": {
            "enabled": True,
            "api_url": "dry-run",
            "from_addr": "flow@example.test",
            "domain": "example.test",
        },
        "inform_user_modules": {
            "alice": ["GATE"],
        },
    })
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "lotmgr"})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_build_inform_snapshot_xlsx", lambda _target: None)
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "alice", "email": "alice@example.test", "status": "approved", "role": "user"},
        {"username": "manual", "email": "manual@example.test", "status": "approved", "role": "user"},
    ])
    informs._save([{
        "id": "inf_gate",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "author": "writer",
        "created_at": "2026-04-30T10:20:00",
        "mail_history": [],
    }])

    req = informs.SendMailReq(to_users=["manual"])
    res = informs.send_mail("inf_gate", req, _request())

    assert res["auto_module_used"] is False
    assert res["to"] == ["manual@example.test"]


def test_create_inform_persists_mail_draft_targets(tmp_path, monkeypatch):
    informs_file = tmp_path / "informs.json"
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "lotmgr"})
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_resolve_fab_lot_snapshot", lambda *_args, **_kwargs: "")

    created = informs.create_inform(
        informs.InformCreate(
            lot_id="R1000",
            product="PRODA",
            module="GATE",
            reason="PEMS",
            text="body",
            mail_draft={
                "to": ["direct@example.test", "not-email"],
                "to_users": ["alice", "alice", "bob"],
                "groups": ["Process Owners", "Process Owners"],
                "extra_emails": ["vendor@example.test"],
                "subject": "Custom subject",
                "body": "Custom body",
            },
        ),
        _request(),
    )["inform"]

    assert created["mail_draft"] == {
        "to": ["direct@example.test"],
        "recipients": [],
        "to_users": ["alice", "bob"],
        "groups": ["Process Owners"],
        "extra_emails": ["vendor@example.test"],
        "subject": "Custom subject",
        "body": "Custom body",
    }
    saved = json.loads(informs_file.read_text("utf-8"))
    assert saved[0]["mail_draft"]["to_users"] == ["alice", "bob"]
    assert saved[0]["mail_draft"]["groups"] == ["Process Owners"]


def test_inform_recipients_lists_approved_users_without_email(monkeypatch):
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "viewer"})
    monkeypatch.setattr(informs, "_load_mail_cfg", lambda: {})
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "hong", "email": "", "status": "approved", "role": "user", "name": "홍길동"},
        {"username": "alice", "email": "alice@example.test", "status": "approved", "role": "user", "name": "Alice"},
        {"username": "pending", "email": "pending@example.test", "status": "pending", "role": "user", "name": "Pending"},
        {"username": "hol", "email": "", "status": "approved", "role": "admin", "name": "Admin"},
    ])

    rows = informs.list_recipients(_request())["recipients"]

    by_name = {r["username"]: r for r in rows}
    assert "hong" in by_name
    assert by_name["hong"]["email_missing"] is True
    assert by_name["hong"]["effective_email"] == ""
    assert by_name["alice"]["email_missing"] is False
    assert "pending" not in by_name
    assert "hol" not in by_name


def test_send_mail_resolves_public_mail_group_members_and_extras(tmp_path, monkeypatch):
    admin_settings = tmp_path / "admin_settings.json"
    informs_file = tmp_path / "informs.json"
    _write_json(admin_settings, {
        "mail": {
            "enabled": True,
            "api_url": "dry-run",
            "from_addr": "flow@example.test",
            "domain": "example.test",
        },
    })
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "lotmgr"})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_build_inform_snapshot_xlsx", lambda _target: None)
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "alice", "email": "alice@example.test", "status": "approved", "role": "user"},
        {"username": "bob", "email": "", "status": "approved", "role": "user"},
    ])
    monkeypatch.setattr(groups_router, "_load", lambda: [{
        "id": "grp_process",
        "name": "Process Owners",
        "members": ["alice", "bob"],
        "extra_emails": ["vendor@example.test"],
    }])
    informs._save([{
        "id": "inf_gate",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "author": "writer",
        "created_at": "2026-04-30T10:20:00",
        "mail_history": [],
    }])

    req = informs.SendMailReq(groups=["Process Owners"])
    res = informs.send_mail("inf_gate", req, _request())

    assert res["auto_module_used"] is False
    assert res["to"] == ["alice@example.test", "bob@example.test", "vendor@example.test"]


def test_mail_preview_uses_same_auto_module_recipients(tmp_path, monkeypatch):
    admin_settings = tmp_path / "admin_settings.json"
    informs_file = tmp_path / "informs.json"
    _write_json(admin_settings, {
        "mail": {"domain": "example.test"},
        "inform_user_modules": {"alice": ["GATE"]},
    })
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "sender"})
    monkeypatch.setattr(informs, "_load_product_contacts", lambda: {"products": {}})
    monkeypatch.setattr(informs, "_build_inform_snapshot_xlsx", lambda _target: None)
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "alice", "email": "", "status": "approved", "role": "user"},
    ])
    informs._save([{
        "id": "inf_gate",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "author": "writer",
        "created_at": "2026-04-30T10:20:00",
    }])

    preview = informs.mail_preview("inf_gate", _request())

    assert preview["auto_module_used"] is True
    assert preview["resolved_recipients"] == ["alice@example.test"]
    assert preview["subject"] == "[plan 적용 통보] PRODA R1000 - GATE"


def test_reason_subject_template_applies_to_mail_preview_and_send(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    admin_settings = tmp_path / "admin_settings.json"
    informs_file = tmp_path / "informs.json"
    _write_json(config_file, {
        "modules": ["GATE"],
        "reasons": ["PEMS"],
        "reason_templates": {
            "PEMS": {"subject": "[{reason}] {product}/{lot}/{module}", "body": ""}
        },
    })
    _write_json(admin_settings, {
        "mail": {
            "enabled": True,
            "api_url": "dry-run",
            "from_addr": "flow@example.test",
        },
    })
    monkeypatch.setattr(informs, "CONFIG_FILE", config_file)
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "lotmgr"})
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(informs, "_audit_record", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(informs, "_build_inform_snapshot_xlsx", lambda _target: None)
    informs._save([{
        "id": "inf_gate",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "author": "writer",
        "created_at": "2026-04-30T10:20:00",
        "mail_history": [],
    }])

    preview = informs.mail_preview("inf_gate", _request())
    sent = informs.send_mail("inf_gate", informs.SendMailReq(to=["owner@example.test"]), _request())

    assert preview["subject"] == "[PEMS] PRODA/R1000/GATE"
    assert sent["subject"] == "[PEMS] PRODA/R1000/GATE"


def test_blank_reason_subject_template_keeps_legacy_subject(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    informs_file = tmp_path / "informs.json"
    _write_json(config_file, {
        "modules": ["GATE"],
        "reasons": ["PEMS"],
        "reason_templates": {"PEMS": {"subject": "   ", "body": ""}},
    })
    monkeypatch.setattr(informs, "CONFIG_FILE", config_file)
    monkeypatch.setattr(informs, "INFORMS_FILE", informs_file)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_SIG", None)
    monkeypatch.setattr(informs, "_INFORMS_CACHE_ITEMS", None)
    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "sender"})
    monkeypatch.setattr(informs, "_load_product_contacts", lambda: {"products": {}})
    monkeypatch.setattr(informs, "_build_inform_snapshot_xlsx", lambda _target: None)
    informs._save([{
        "id": "inf_gate",
        "product": "PRODA",
        "root_lot_id": "R1000",
        "lot_id": "R1000",
        "module": "GATE",
        "reason": "PEMS",
        "author": "writer",
        "created_at": "2026-04-30T10:20:00",
    }])

    preview = informs.mail_preview("inf_gate", _request())

    assert preview["subject"] == "[plan 적용 통보] PRODA R1000 - GATE"


def test_module_knob_highlight_does_not_render_yellow_cells(tmp_path, monkeypatch):
    knob_map = tmp_path / "inform_module_knob_map.json"
    _write_json(knob_map, {"GATE": ["GATE_DOSE"]})
    monkeypatch.setattr(informs, "MODULE_KNOB_MAP_FILE", knob_map)
    embed = {
        "st_view": {
            "root_lot_id": "R1000",
            "headers": ["#1", "#2"],
            "header_groups": [{"label": "R1000", "span": 2}],
            "rows": [
                {"_param": "GATE_DOSE", "_cells": {"0": {"actual": "10"}, "1": {"actual": "11"}}},
                {"_param": "STI_DEPTH", "_cells": {"0": {"actual": "A"}, "1": {"actual": "B"}}},
            ],
        },
    }

    html = informs._render_embed_table_html(embed, module="GATE")

    assert "GATE_DOSE" in html
    assert "background:#fff7cc" not in html
    assert "border:2px solid #ca8a04" not in html


def test_module_recipient_and_knob_map_apis(tmp_path, monkeypatch):
    admin_settings = tmp_path / "admin_settings.json"
    knob_map = tmp_path / "inform_module_knob_map.json"
    _write_json(admin_settings, {
        "mail": {"domain": "example.test"},
        "inform_user_modules": {"alice": ["GATE"], "bob": ["STI"]},
    })
    _write_json(knob_map, {"GATE": ["GATE_DOSE"]})
    monkeypatch.setattr(informs, "ADMIN_SETTINGS_FILE", admin_settings)
    monkeypatch.setattr(informs, "MODULE_KNOB_MAP_FILE", knob_map)
    monkeypatch.setattr(informs, "_audit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(auth_router, "read_users", lambda: [
        {"username": "alice", "email": "alice@example.test", "status": "approved", "role": "user"},
        {"username": "bob", "email": "", "status": "approved", "role": "user"},
    ])

    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "user", "username": "viewer"})
    assert informs.module_recipients(_request(), module="GATE")["recipients"] == [
        {"username": "alice", "email": "alice@example.test"},
    ]
    assert informs.get_module_knob_map(_request())["knob_map"] == {"GATE": ["GATE_DOSE"]}
    with pytest.raises(HTTPException) as excinfo:
        informs.set_module_knob_map(informs.ModuleKnobMapReq(module="GATE", knobs=["GATE_TIME"]), _request())
    assert excinfo.value.status_code == 403

    monkeypatch.setattr(informs, "current_user", lambda _request: {"role": "admin", "username": "admin"})
    saved = informs.set_module_knob_map(
        informs.ModuleKnobMapReq(module="GATE", knobs=["GATE_TIME", "GATE_TIME", ""]),
        _request(),
    )
    assert saved["knob_map"]["GATE"] == ["GATE_TIME"]
    assert informs.get_module_knob_map(_request())["knob_map"]["GATE"] == ["GATE_TIME"]
