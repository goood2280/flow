import pytest
from fastapi import HTTPException

from backend.routers import analysis_requests as mod

# 라우터가 import 한 모듈 객체를 그대로 쓴다(backend.core 로 따로 import 하면 다른 모듈이 된다).
core = mod.store
LAYERS = {"ET100M1": "M1DC", "ET300M3": "M3DC"}


def fake_tracking(item, *, actor):
    et = {
        "A7001|1": [{"key": "k1", "step_id": "ET100M1", "step_seq": "M1DCH1", "pgm": "M1DCH1(21pt)", "time": "2026-09-22 09:10:00"}],
        "A7001|2": [{"key": "k2", "step_id": "ET100M1", "step_seq": "M1DCH1", "pgm": "M1DCH1(21pt)", "time": "2026-09-22 09:13:00"},
                    {"key": "k3", "step_id": "ET300M3", "step_seq": "M3DCH1", "pgm": "M3DCH1(21pt)", "time": "2026-09-26 11:00:00"}],
    }
    values = {"A7001|1": "PPID_05_ECN", "A7001|2": "PPID_05_ECN", "A7001|3": "PPID_05_REF",
              "B7002|1": "PPID_05_REF", "B7002|2": "PPID_05_ECN", "B7002|3": "PPID_05_REF"}
    split = {key: {"lot_id": key.split("|")[0] + "A.1", "KNOB_5.0 PC": value} for key, value in values.items()}
    return {"split_status": "ok", "et_status": "ok", "split": split, "et": et, "last_new_et": 0,
            "wafers_by_root": {"A7001": ["1", "2", "3"], "B7002": ["1", "2", "3"]}}


@pytest.fixture()
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "STORE_DIR", tmp_path / "analysis_requests")
    actor = {"username": "alice", "name": "Alice", "role": "user", "tabs": "analysisrequest"}
    monkeypatch.setattr(mod, "_user", lambda _request: dict(actor))
    monkeypatch.setattr(
        mod, "is_page_manager",
        lambda user, page: page == "analysisrequest" and (
            (user or {}).get("role") == "admin" or (user or {}).get("username") == "ana.user"
        ),
    )
    calls = []
    monkeypatch.setattr(core, "collect_tracking", lambda item, *, actor: calls.append(item["id"]) or fake_tracking(item, actor=actor))
    monkeypatch.setattr(core, "_dc_layers", lambda: dict(LAYERS))
    mails = []
    monkeypatch.setattr(mod, "_send_response_mail", lambda request_id, response_id, actor: mails.append((request_id, response_id, actor)))

    class NoThread:
        def __init__(self, target, args, **_kw):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(mod.threading, "Thread", NoThread)
    return {"actor": actor, "calls": calls, "mails": mails}


def payload(**overrides):
    base = dict(request_type="변경점(ECN) 평가", product="ML_TABLE_ECNDEMO", title="[ECN-2609] PC PPID 변경 평가",
                details="<p>PC PPID 변경 전후 비교</p>", requester_team="Module", priority="normal",
                lots="A7001, b7002(1~3), A7001", split_columns=["KNOB_5.0 PC", "root_lot_id"])
    base.update(overrides)
    return mod.RequestWrite(**base)


def test_request_form_only_needs_content_and_lots(board):
    item = mod.create_request(payload(), None)
    assert item["product"] == "ECNDEMO"
    assert item["lots"] == [{"root_lot_id": "A7001", "wafer_id": ""}, {"root_lot_id": "B7002", "wafer_id": "1,2,3"}]
    assert item["lots_text"] == "A7001, B7002(1~3)"
    assert item["split_columns"] == ["KNOB_5.0 PC"]            # identity 열은 표시 열이 아니다
    assert board["calls"] == [item["id"]]                      # 대상 Lot 이 있으면 등록 직후 한 번 읽는다
    summary = item["summary"]
    assert (summary["wafers"], summary["et_measured"]) == (6, 2)
    assert summary["layers"] == {"M1DC": 2, "M3DC": 1}        # step_id → DC layer 는 공용 매핑으로 읽는다
    assert "plan_rows" not in item and "groups" not in item

    no_lots = mod.create_request(payload(lots=""), None)
    assert no_lots["lots"] == [] and len(board["calls"]) == 1  # 대상 Lot 이 없으면 조회하지 않는다


def test_split_view_appends_et_rows_under_wafer_columns(board):
    item = mod.create_request(payload(lots="A7001"), None)
    stored = next(row for row in core.load_rows() if row["id"] == item["id"])

    def loader(product, lot_id, custom_cols):
        assert (product, lot_id, custom_cols) == ("ECNDEMO", "A7001", ["KNOB_5.0 PC"])
        return {"st_view": {"headers": ["#1", "#2", "#3"], "wafer_keys": ["01", "02", "03"],
                            "rows": [{"_param": "KNOB_5.0 PC", "_cells": {"0": {"actual": "PPID_05_ECN"}}}]},
                "rows": [], "source": "SplitTable"}

    view = core.split_view(stored, loader=loader)
    rows = view["lots"][0]["embed"]["st_view"]["rows"]
    assert [row["_param"] for row in rows] == ["KNOB_5.0 PC", "ET_M1DC", "ET_M3DC"]
    m1 = rows[1]["_cells"]
    assert set(m1) == {"0", "1"} and m1["0"]["actual"] == "M1DCH1(21pt) 09/22"
    assert rows[2]["_cells"]["1"]["actual"].startswith("M3DCH1(21pt)")
    assert view["lots"][0]["et_measured"] == 2

    # wafer 를 지정한 Lot 은 그 wafer 열만 남기고 번호를 다시 매긴다.
    stored["lots"] = [{"root_lot_id": "A7001", "wafer_id": "2,3"}]
    view = core.split_view(stored, loader=loader)
    st = view["lots"][0]["embed"]["st_view"]
    assert st["headers"] == ["#2", "#3"]
    assert st["rows"][0]["_cells"] == {}                       # 원래 0번(#1) 칸은 빠진다
    assert st["rows"][1]["_cells"]["0"]["actual"].startswith("M1DCH1")


def test_view_change_by_processor_bumps_revision(board):
    item = mod.create_request(payload(lots="A7001"), None)
    board["actor"].update(username="bob")
    with pytest.raises(HTTPException):
        mod.update_view(item["id"], mod.ViewWrite(lots="B7002"), None)
    board["actor"].update(username="ana.user")
    changed = mod.update_view(item["id"], mod.ViewWrite(lots="B7002", split_columns=["KNOB_5.0 PC", "FAB_5.0 PC"]), None)
    assert changed["revision"] == 2 and changed["lots_text"] == "B7002"
    assert core.apply_tracking(item["id"], 1, {}) is None      # 이전 revision 결과는 버린다


def test_response_with_attachment_mails_requester(board, tmp_path):
    item = mod.create_request(payload(), None)
    meta = mod._store_file(b"PK\x03\x04pptx", "ECN 보고서_A7001.pptx", {"username": "ana.user"}, kind="attachment")
    assert meta["name"] == "ECN 보고서_A7001.pptx" and meta["stored"].endswith(".pptx")
    with pytest.raises(HTTPException):
        mod.create_response(item["id"], mod.ResponseWrite(body="확인"), None)   # 처리 담당자만
    board["actor"].update(username="ana.user", name="Ana")
    with pytest.raises(HTTPException):
        mod.create_response(item["id"], mod.ResponseWrite(body=""), None)      # 내용도 첨부도 없음
    detail = mod.create_response(item["id"], mod.ResponseWrite(
        body="", attachments=[mod.AttachmentRef(uid=meta["uid"], name=meta["name"])]), None)
    response = detail["responses"][0]
    assert response["attachments"][0]["name"] == "ECN 보고서_A7001.pptx"
    assert board["mails"] == [(item["id"], response["id"], "ana.user")]

    stored = next(row for row in core.load_rows() if row["id"] == item["id"])
    mail = mod.response_mail(stored, {**stored["responses"][0], "body": "<p>결과 첨부합니다</p>"})
    assert "[분석의뢰 답글]" in mail["subject"] and "결과 첨부합니다" in mail["html"]
    assert mail["files"][0][0] == "ECN 보고서_A7001.pptx"
    with pytest.raises(HTTPException):
        mod.create_response(item["id"], mod.ResponseWrite(body="x", attachments=[mod.AttachmentRef(uid="nope")]), None)


def test_dc_layer_dict_from_auto_report_is_parsed():
    from backend.core import dc_layer_mapping as dc
    rows = dc.parse_mapping_text("self.dc_step_to_ids = {'MFDC': ['NU111'], 'M8DC': ['NU222', 'nu111']}")
    assert rows == [{"dc_layer": "MFDC", "step_ids": ["NU111"]}, {"dc_layer": "M8DC", "step_ids": ["NU222", "NU111"]}]
    inverted = dc.parse_mapping_text('{"NU1": "M1DC", "NU2": "M1DC", "NU3": "M3DC"}')
    assert inverted == [{"dc_layer": "M1DC", "step_ids": ["NU1", "NU2"]}, {"dc_layer": "M3DC", "step_ids": ["NU3"]}]
    assert dc.parse_mapping_text("M1DC: NU1, NU2\nM2DC\tNU5") == [
        {"dc_layer": "M1DC", "step_ids": ["NU1", "NU2"]}, {"dc_layer": "M2DC", "step_ids": ["NU5"]}]


def test_dc_layer_annotation_and_mentions(monkeypatch):
    from backend.core import dc_layer_mapping as dc
    monkeypatch.setattr(dc, "_rows_cached", lambda: [{"dc_layer": "M1DC", "step_ids": ["ET100M1"]},
                                                      {"dc_layer": "M10DC", "step_ids": ["ET1000"]}])
    assert dc.mentioned_layers("A7001 m1dc 측정됐어?") == {"M1DC": ["ET100M1"]}   # M10DC 와 헷갈리지 않는다
    rows, columns = dc.annotate_rows([{"step_id": "ET100M1", "pgm": "x"}, {"step_id": "ZZ"}], ["step_id", "pgm"])
    assert columns == ["step_id", "dc_layer", "pgm"]
    assert [r["dc_layer"] for r in rows] == ["M1DC", ""]


def test_rule_swap_moves_lots_slots_and_split_column(board):
    a = mod.create_request(payload(lots="A7001", report_template_id="tpl"), None)
    b = mod.create_request(payload(lots="B7002", title="[ECN-2610] PC PPID 변경 평가", copied_from=a["id"], report_template_id="tpl"), None)
    template = {"name": "ECN A7001", "options": {"subtitle": "ECN-2609 A7001"}, "pages": [{"title": "A7001", "subtitle": "", "slots": [
        {"kind": "chart", "definition_code": "ROOT_LOTS = A7001\nSQL = SELECT `KNOB_5.0 PC` WHERE `item_id` = 'VTH_N'\nCOLOR = KNOB_5.0 PC"},
        {"kind": "split", "lot": "A7001", "columns": "KNOB_5.0 PC,FAB_5.0 PC"},
        {"kind": "text", "text": "[대상] A7001 (A7001A.1)\nPPID_05_ECN: #1,2 / PPID_05_REF: #3\nET 2/3매"}]}]}
    stored_b = next(row for row in core.load_rows() if row["id"] == b["id"])
    source = core.source_request_for(stored_b, "tpl")
    assert source["id"] == a["id"]
    swapped, changes = core.rule_swap_template(template, stored_b, source=source)
    text = swapped["pages"][0]["slots"][2]["text"]
    assert "B7002 (B7002A.1)" in text
    assert "PPID_05_ECN: #2 / PPID_05_REF: #1,3" in text        # 같은 값끼리 짝지어 slot 만 바뀐다
    assert "ET 0/3매" in text                                   # 측정 매수도 새 랏 값
    assert "ROOT_LOTS = B7002" in swapped["pages"][0]["slots"][0]["definition_code"]
    check = core.verify_report_template(template, swapped, stored_b)
    assert check["ok"], check["checks"]

    # 그럴듯한 숫자 오류 — 실제 측정 매수와 다른 N/M매는 오류, 원본·의뢰에 없는 단위 숫자는 경고.
    import copy as _copy
    wrong = _copy.deepcopy(swapped)
    wrong["pages"][0]["slots"][2]["text"] += "\n2) ET 측정: M1DC 3/3매 · 25pt"
    check = core.verify_report_template(template, wrong, stored_b)
    levels = {c["label"]: (c["level"], c["detail"]) for c in check["checks"]}
    assert not check["ok"] and levels["글의 측정 매수"][0] == "error"
    assert levels["글의 숫자(단위)"] == ("warn", "원본·의뢰에 없는 값: 25pt")

    stored_b["split_columns"] = ["KNOB_7.0 SPACER"]
    swapped, changes = core.rule_swap_template(template, stored_b, source=source)
    code = swapped["pages"][0]["slots"][0]["definition_code"]
    assert "COLOR = KNOB_7.0 SPACER" in code and "`KNOB_7.0 SPACER`" in code
    assert swapped["pages"][0]["slots"][1]["columns"] == "KNOB_7.0 SPACER,FAB_7.0 SPACER"
    assert any(change.startswith("split 열") for change in changes)


def test_config_templates_roundtrip(board):
    board["actor"].update(username="ana.user")
    saved = mod.save_config(mod.ConfigWrite(
        request_types=["ECN 평가"], request_teams=["Module"],
        templates=[mod.TemplateWrite(name="ECN 기본", details="<p>배경:</p>", split_columns=["KNOB_5.0 PC"], report_template_id="rpt1"),
                   mod.TemplateWrite(name="ECN 기본")],
    ), None)
    assert [t["id"] for t in saved["templates"]] == ["ECN-기본", "ECN-기본-2"]
    assert saved["default_template_id"] == "ECN-기본" and saved["notify_on_response"] is True
    board["actor"].update(username="alice")
    with pytest.raises(HTTPException):
        mod.save_config(mod.ConfigWrite(), None)
