from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from routers import llm as llm_router  # noqa: E402
from routers.llm import _handle_flowi_query, _matched_feature_entrypoints, _run_flowi_chat  # noqa: E402


def test_flowi_feature_router_matches_korean_splittable_alias():
    matches = _matched_feature_entrypoints("A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘")

    assert matches
    assert matches[0]["key"] == "splittable"


def test_flowi_general_query_returns_deterministic_unit_action():
    out = _handle_flowi_query("A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘", "", 12)

    assert out["handled"] is True
    assert out["intent"] == "splittable_guidance"
    assert out["action"] == "open_splittable"
    assert out["table"]["kind"] == "flowi_action_plan"
    assert out["slots"]["lots"] == ["A10001"]


def test_flowi_feature_router_prefers_tablemap_relation_terms():
    out = _handle_flowi_query("테이블맵 relation에서 inline item과 knob 연결 보여줘", "", 12)

    assert out["intent"] == "tablemap_guidance"
    assert out["action"] == "open_tablemap"


def test_flowi_feature_router_filters_by_allowed_tabs():
    matches = _matched_feature_entrypoints(
        "테이블맵 relation에서 inline item과 knob 연결 보여줘",
        allowed_keys={"splittable"},
    )

    assert not matches or matches[0]["key"] != "tablemap"


def test_flowi_chart_request_returns_clarifying_unit_plan():
    out = _handle_flowi_query(
        "Inline 1.0 CD와 ET LKG Corr. scatter 그리고 1차식 fitting line 그려줘",
        "PRODA",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "dashboard_scatter_plan"
    assert out["action"] == "build_metric_scatter"
    assert out["chart"]["aggregations"]["INLINE"] == "avg"
    assert out["chart"]["aggregations"]["ET"] == "median"
    assert "linear_fit" in out["chart"]["operations"]
    assert out["clarification"]["choices"]
    assert out["table"]["kind"] == "flowi_chart_plan"


def test_flowi_chart_request_computes_inline_et_scatter(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 12.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "CD_MEAN", "value": 20.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 101.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 99.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "LKG_RAW", "value": 200.0},
    ]).write_parquet(et_fp)
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])

    out = _handle_flowi_query(
        "PRODX A10001 Inline CD와 ET LKG Corr scatter 그리고 1차식 fitting line 그려줘",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["chart"]["status"] == "computed"
    assert out["chart_result"]["kind"] == "dashboard_scatter"
    assert out["chart_result"]["total"] == 2
    assert out["chart_result"]["join_cols"] == ["lot_wf"]
    assert out["chart_result"]["aggregations"] == {"INLINE": "avg", "ET": "median"}
    assert out["chart_result"]["fit"]["r2"] == 1.0
    assert {p["x"] for p in out["chart_result"]["points"]} == {11.0, 20.0}
    assert {p["y"] for p in out["chart_result"]["points"]} == {100.0, 200.0}


def test_flowi_chart_request_colors_and_filters_by_ml_table_knob(tmp_path, monkeypatch):
    inline_fp = tmp_path / "inline.parquet"
    et_fp = tmp_path / "et.parquet"
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 10.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "CD_MEAN", "value": 12.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "CD_MEAN", "value": 20.0},
    ]).write_parquet(inline_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 101.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "item_id": "LKG_RAW", "value": 99.0},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "item_id": "LKG_RAW", "value": 200.0},
    ]).write_parquet(et_fp)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "01", "KNOB_SPLIT": "A"},
        {"product": "PRODX", "root_lot_id": "A10001", "wafer_id": "02", "KNOB_SPLIT": "B"},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_inline_files", lambda _product: [inline_fp])
    monkeypatch.setattr(llm_router, "_et_files", lambda _product: [et_fp])
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    out = _handle_flowi_query(
        "PRODX A10001 Inline CD와 ET LKG Corr scatter KNOB_SPLIT B 제외하고 컬러링",
        "",
        12,
        allowed_keys={"dashboard", "ml"},
    )

    assert out["handled"] is True
    assert out["chart"]["status"] == "computed"
    assert out["chart_result"]["total"] == 1
    assert out["chart_result"]["color_by"] == "SPLIT"
    assert out["chart_result"]["filters"]["excluded_values"] == ["B"]
    assert out["chart_result"]["points"][0]["color_value"] == "A"
    assert out["chart_result"]["color_values"] == [{"value": "A", "count": 1}]


def test_flowi_value_lookup_returns_mltable_preview(tmp_path, monkeypatch):
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "R1000", "wafer_id": "01", "KNOB_ALPHA": "ON", "INLINE_CD": 12.3},
        {"product": "PRODX", "root_lot_id": "R1000", "wafer_id": "02", "KNOB_ALPHA": "OFF", "INLINE_CD": 12.7},
        {"product": "PRODX", "root_lot_id": "R2000", "wafer_id": "01", "KNOB_ALPHA": "ON", "INLINE_CD": 10.1},
    ]).write_parquet(ml_fp)
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])

    out = _handle_flowi_query(
        "PRODX R1000 KNOB_ALPHA 값 얼마야",
        "",
        12,
        allowed_keys={"filebrowser", "splittable"},
    )

    assert out["handled"] is True
    assert out["intent"] == "db_table_lookup"
    assert out["table"]["kind"] == "flowi_db_table"
    assert out["table"]["total"] == 2
    assert {row["KNOB_ALPHA"] for row in out["table"]["rows"]} == {"ON", "OFF"}


def test_flowi_knob_fastest_lot_joins_latest_fab_step(tmp_path, monkeypatch):
    ml_fp = tmp_path / "ML_TABLE_PRODX.parquet"
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "R1000", "wafer_id": "01", "KNOB_SPEED": "FAST"},
        {"product": "PRODX", "root_lot_id": "R2000", "wafer_id": "01", "KNOB_SPEED": "FAST"},
    ]).write_parquet(ml_fp)
    fab_dir = tmp_path / "1.RAWDATA_DB_FAB" / "PRODX" / "date=20260429"
    fab_dir.mkdir(parents=True)
    pl.DataFrame([
        {"product": "PRODX", "root_lot_id": "R1000", "lot_id": "R1000A.1", "fab_lot_id": "R1000A.1", "wafer_id": "01", "step_id": "STEP_010", "tkout_time": "2026-04-29T08:00:00"},
        {"product": "PRODX", "root_lot_id": "R2000", "lot_id": "R2000A.1", "fab_lot_id": "R2000A.1", "wafer_id": "01", "step_id": "STEP_020", "tkout_time": "2026-04-29T09:00:00"},
    ]).write_parquet(fab_dir / "part.parquet")
    monkeypatch.setattr(llm_router, "_ml_files", lambda _product: [ml_fp])
    monkeypatch.setattr(llm_router, "_db_root_candidates", lambda kind: [tmp_path / "1.RAWDATA_DB_FAB"] if kind.upper() == "FAB" else [])

    import core.lot_step as lot_step
    monkeypatch.setattr(lot_step, "lookup_step_meta", lambda product="", step_id="": {"func_step": {"STEP_010": "STI", "STEP_020": "GATE"}.get(step_id, "")})

    out = _handle_flowi_query(
        "PRODX KNOB_SPEED FAST 가진 것 중에 가장 빠른게 지금 어디있어",
        "",
        12,
        allowed_keys={"splittable", "ml"},
    )

    assert out["handled"] is True
    assert out["intent"] == "knob_fastest_lot"
    assert out["table"]["rows"][0]["root_lot_id"] == "R2000"
    assert out["table"]["rows"][0]["current_step_id"] == "STEP_020"
    assert out["table"]["rows"][0]["func_step"] == "GATE"


def test_flowi_user_file_write_request_is_blocked(monkeypatch):
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)

    out = _run_flowi_chat(
        prompt="files sample.csv 삭제해줘",
        product="",
        max_rows=12,
        me={"username": "normal_user", "role": "user"},
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "blocked_write_request"
    assert out["tool"]["blocked"] is True
    assert out["llm"]["blocked"] is True


def test_flowi_admin_file_delete_requires_structured_confirmation(tmp_path, monkeypatch):
    (tmp_path / "sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_flowi_file_roots", lambda: [("DB", tmp_path)])

    out = _run_flowi_chat(
        prompt="files sample.csv 삭제해줘",
        product="",
        max_rows=12,
        me={"username": "admin_user", "role": "admin"},
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "admin_file_operation"
    assert out["tool"]["requires_confirmation"] is True
    assert out["tool"]["clarification"]["choices"][0]["prompt"].startswith("FLOWI_FILE_OP")
    assert (tmp_path / "sample.csv").exists()


def test_flowi_admin_file_delete_archives_file(tmp_path, monkeypatch):
    target = tmp_path / "sample.csv"
    target.write_text("a,b\n1,2\n", encoding="utf-8")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_flowi_file_roots", lambda: [("DB", tmp_path)])

    out = _run_flowi_chat(
        prompt='FLOWI_FILE_OP {"op":"delete","path":"sample.csv","confirm":"DELETE sample.csv"}',
        product="",
        max_rows=12,
        me={"username": "admin_user", "role": "admin"},
    )

    assert out["ok"] is True
    assert out["tool"]["intent"] == "admin_file_operation"
    assert out["tool"]["action"] == "delete"
    assert out["tool"]["file_operation"]["executed"] is True
    assert not target.exists()
    assert list((tmp_path / ".trash").glob("*_sample.csv"))


def test_flowi_admin_replace_text_requires_exact_match_and_backup(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"mode":"old"}\n', encoding="utf-8")
    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_flowi_file_roots", lambda: [("Files", tmp_path)])

    out = _run_flowi_chat(
        prompt='FLOWI_FILE_OP {"op":"replace_text","path":"settings.json","old":"old","new":"new","confirm":"REPLACE settings.json"}',
        product="",
        max_rows=12,
        me={"username": "admin_user", "role": "admin"},
    )

    assert out["tool"]["action"] == "replace_text"
    assert target.read_text(encoding="utf-8") == '{"mode":"new"}\n'
    assert list((tmp_path / ".trash").glob("*_settings.json.bak"))


def test_flowi_chat_route_passes_home_conversation_context(monkeypatch):
    seen = {}

    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})

    def fake_run_flowi_chat(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "answer": "ok"}

    monkeypatch.setattr(llm_router, "_run_flowi_chat", fake_run_flowi_chat)

    out = llm_router.flowi_chat(
        llm_router.FlowiChatReq(
            prompt="그 조건으로 다음도 봐줘",
            context={"type": "home_flowi_chat", "messages": [{"role": "user", "text": "A10001 먼저 봐줘"}]},
        ),
        request=object(),
    )

    assert out["ok"] is True
    assert seen["prompt"] == "그 조건으로 다음도 봐줘"
    assert seen["agent_context"]["type"] == "home_flowi_chat"
    assert seen["agent_context"]["messages"][0]["text"] == "A10001 먼저 봐줘"


def test_flowi_verify_tests_llm_connection_for_home_start(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": True, "text": "확인완료"})

    out = llm_router.flowi_verify(llm_router.FlowiVerifyReq(), object())

    assert out == {"ok": True, "message": "확인완료"}


def test_flowi_verify_reports_disconnected_when_llm_test_fails(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": False, "error": "timeout"})

    out = llm_router.flowi_verify(llm_router.FlowiVerifyReq(), object())

    assert out["ok"] is False
    assert out["error"] == "timeout"


def test_flowi_verify_requires_confirmation_text(monkeypatch):
    monkeypatch.setattr(llm_router, "current_user", lambda _request: {"username": "home_user", "role": "user"})
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", lambda *_args, **_kwargs: {"ok": True, "text": "pong"})

    out = llm_router.flowi_verify(llm_router.FlowiVerifyReq(), object())

    assert out["ok"] is False
    assert out["error"] == "pong"


def test_flowi_agent_chat_accepts_codex_source_and_returns_web_actions(monkeypatch):
    seen = {}

    def fake_complete(prompt, **_kwargs):
        seen["prompt"] = prompt
        return {"ok": True, "text": "Codex 입력 기준으로 스플릿 테이블을 열고 plan/actual을 확인하세요."}

    monkeypatch.setattr(llm_router, "_append_user_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(llm_router, "_profile_context", lambda _username: "")
    monkeypatch.setattr(llm_router.llm_adapter, "is_available", lambda: True)
    monkeypatch.setattr(llm_router.llm_adapter, "complete", fake_complete)

    out = _run_flowi_chat(
        prompt="A10001 1.0 STI 스플릿테이블에서 plan actual 보여줘",
        product="",
        max_rows=12,
        me={"username": "codex_tester", "role": "admin"},
        source_ai="codex",
        client_run_id="codex-smoke-1",
        agent_context={"origin": "codex-test", "surface": "api"},
    )

    assert out["ok"] is True
    assert out["llm"]["used"] is True
    assert out["answer"].startswith("Codex 입력 기준")
    assert out["agent_api"]["received"] is True
    assert out["agent_api"]["source_ai"] == "codex"
    assert out["agent_api"]["auth_user"] == "codex_tester"
    assert any(a["type"] == "open_tab" and a["tab"] == "splittable" for a in out["agent_api"]["actions"])
    assert any(a["type"] == "flowi_unit_action" and a["action"] == "open_splittable" for a in out["agent_api"]["actions"])
    assert "외부 AI source: codex" in seen["prompt"]
    assert "codex-smoke-1" in seen["prompt"]
    assert "codex-test" in seen["prompt"]
