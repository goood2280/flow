import json
import pytest
from core import data_chat, llm_adapter
from routers import filebrowser, splittable, admin

CODE = """Q1
TABLE = ET
PRODUCT = PRODUCTA0
SQL = SELECT root_lot_id, tkout_time, value
ROOT_LOTS = AZA11
RECENT_DAYS = 7
DATE_COLUMN = tkout_time

Q2
TABLE = INLINE
PRODUCT = PRODUCTA0
SQL = SELECT root_lot_id, value

JOIN q1 LEFT q2 ON root_lot_id

CHART
TYPE = scatter
X = tkout_time
Y = value
WIDTH = 1000
HEIGHT = 500
"""


def edit(prompt):
    return filebrowser._chart_builder_assistant_plan(filebrowser.ChartBuilderAssistantReq(
        instruction=prompt, definition_code=CODE, columns=["root_lot_id","tkout_time","value"]))


def test_targeted_chart_edits_preserve_other_queries_and_settings(monkeypatch):
    monkeypatch.setattr(llm_adapter,"complete_json",lambda *a,**k:pytest.fail("simple edit called LLM"))
    font=edit("차트 x축 font 사이즈 키워줘")
    assert font["chart"]["x_font_size"] == 16
    assert font["chart"]["width"] == 1000 and font["chart"]["height"] == 500
    assert not font["requires_rerun"]
    original=filebrowser.parse_chart_builder_definition(CODE)
    days=edit("q1 table tkout_time 기준으로 30일로 바꿔줘")
    assert days["sources"][0]["runtime_recent_days"] == 30
    assert days["sources"][0]["runtime_date_column"] == "tkout_time"
    assert days["sources"][1] == original["sources"][1]
    assert days["chart"] == original["chart"]
    assert days["requires_rerun"]
    lot=edit("Q1 현재 root lot AZA11을 BZA22로 바꿔줘")
    assert lot["sources"][0]["runtime_root_lot_ids"] == ["BZA22"]
    assert lot["sources"][1] == original["sources"][1]


def test_multi_query_edit_requires_target():
    with pytest.raises(Exception,match="Query"):
        edit("30일로 바꿔줘")


def test_product_abbreviations_only_resolve_inventory_and_preserve_ambiguity():
    assert data_chat.product_candidates("prod0 AZA11 PC custom set",["PRODUCTA0","OTHER0"]) == ["PRODUCTA0"]
    assert data_chat.product_candidates("pro0",["PRODUCTA0","PRODUCTB0"]) == ["PRODUCTA0","PRODUCTB0"]
    assert data_chat.product_candidates("pro1",["PRODUCTA0"]) == []


def test_location_returns_actual_lot_without_llm(monkeypatch):
    from core import lot_progress_cache
    monkeypatch.setattr(splittable,"list_products",lambda:{"products":[{"name":"PRODUCTA0"}]})
    calls=[]
    def lookup(lots, **kwargs):
        calls.append(kwargs)
        assert lots == ["AZ11A.1"]
        return {"AZ11A.1": {"product":"PRODUCTA0", "rows":[{"lot_id":"AZ11A.1","step_id":"STEP10","product":"PRODUCTA0"}]}}
    monkeypatch.setattr(lot_progress_cache,"canonical_lot_progress_summaries",lookup)
    monkeypatch.setattr(lot_progress_cache,"lookup_lot_progress",lambda **k:pytest.fail("location must read WIP"))
    out=data_chat.execute("prod0 AZ11A.1 위치 확인해줘",{},None)
    assert calls[0]["product"] == "PRODUCTA0"
    assert calls[0]["match_root"] is False
    assert out["tool"]["table"]["rows"][0]["step_id"] == "STEP10"
    assert out["interpretation"]["fab_lot_id"] == "AZ11A.1"
    assert "WIP" in out["interpretation"]["summary"]
    assert "STEP10" in out["reply"]


def test_location_empty_wip_does_not_invent_position(monkeypatch):
    from core import lot_progress_cache
    monkeypatch.setattr(splittable,"list_products",lambda:{"products":[{"name":"PRODUCTA0"}]})
    monkeypatch.setattr(lot_progress_cache,"canonical_lot_progress_summaries",lambda *a,**k:{})
    out=data_chat.execute("prod0 AZ11A.1 위치 확인해줘",{},None)
    assert out["tool"]["table"]["rows"] == []
    assert "확인하지 못했습니다" in out["reply"]
    assert out["interpretation"]["product"] == "PRODUCTA0"


def test_chat_font_edit_does_not_query_data(monkeypatch):
    monkeypatch.setattr(filebrowser,"chart_builder_run",lambda *a,**k:pytest.fail("visual edit queried data"))
    out=data_chat.execute("x축 font 사이즈 키워줘",{"definition_code":CODE},None)
    assert "X_FONT_SIZE = 16" in out["context"]["definition_code"]


def test_gemma_headers_body_and_separate_profile(monkeypatch):
    cfg=llm_adapter._normalize_runtime_config({**admin._llm_defaults("gemma4"),"enabled":True,
        "api_url":"https://internal.example/v1","admin_token":"test-ticket","system_name":"flow-test"})
    with llm_adapter.request_execution_scope({"username":"operator","role":"admin"}):
        headers=llm_adapter._build_request_headers(cfg)
        body=llm_adapter._build_request_body(cfg,"chart",system="data only")
    assert headers["x-dep-ticket"] == "test-ticket"
    assert headers["Send-System-Name"] == "flow-test"
    assert headers["User-Id"] == "operator" and headers["User-Type"] == "admin"
    assert headers["Prompt-Msg-Id"] != headers["Completion-Msg-Id"]
    assert "Authorization" not in headers
    assert body["model"] == "Gemma4-260430" and body["stream"] is False
    assert body["messages"] == [{"role":"system","content":"data only"},{"role":"user","content":"chart"}]
    profiles=admin._llm_profiles_from_admin({"llm_profiles":{"gemma4":cfg,"playground":{"model":"gpt-oss-120b","admin_token":"old-ticket"}}})
    assert profiles["playground"]["admin_token"] == "old-ticket"
    assert profiles["gemma4"]["admin_token"] == "test-ticket"


@pytest.mark.parametrize("path",["/api/llm/error/explain","/api/llm/translate","/api/meetings/summary","/api/knowledge/ask"])
def test_unrelated_features_cannot_spend_llm_calls(path,monkeypatch):
    monkeypatch.setattr(llm_adapter.urllib.request,"urlopen",lambda *a,**k:pytest.fail("unrelated LLM network call"))
    with llm_adapter.request_execution_scope({"username":"admin","role":"admin"},path=path):
        out=llm_adapter.complete("translate")
    assert not out["ok"] and not out["meta"]["invoked"]
