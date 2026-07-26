from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import flowi_multisource, llm_adapter  # noqa: E402
from core.flowi_units import filebrowser_ai_sql_runtime  # noqa: E402
from core.flowi_units import dashboard_agent_runtime, home_sql_join_dashboard_runtime as runtime  # noqa: E402


def _fake_fb_result(columns=None, rows=None):
    columns = columns or ["wafer_id", "IOFF", "lot_id"]
    rows = rows or [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}]
    return {
        "ok": True,
        "run_id": "fb_sub",
        "trace": [{"node_id": "preview_apply", "status": "success", "warnings": []}],
        "semantic_frame": {"resolved_columns": columns},
        "merged": {"display_sql": "SELECT " + ", ".join(columns), "where_sql": "", "selected_columns": columns, "sort": {}},
        "preview": {"columns": columns, "rows": rows, "total_rows": len(rows), "warnings": []},
    }


def _patch_no_langgraph(monkeypatch):
    monkeypatch.setattr(runtime, "_run_with_langgraph", lambda state: None)
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)


def test_home_sql_join_dashboard_graph_uses_planned_nodes():
    graph = runtime.home_sql_join_dashboard_graph()
    assert [node["id"] for node in graph["nodes"]] == [
        "semantic_layer",
        "source_resolve",
        "filebrowser_sql_draft",
        "data_need_decision",
        "join_candidate_select",
        "join_plan_validate",
        "data_execute",
        "output_route",
        "dashboard_draft",
    ]


def test_prompt_only_single_source_candidate_calls_filebrowser_ai_sql(monkeypatch):
    _patch_no_langgraph(monkeypatch)
    captured = {}

    monkeypatch.setattr(
        runtime,
        "_source_resolve_candidates",
        lambda prompt, product_hint, warnings: ([
            {
                "source_id": "db_FAB",
                "label": "FAB",
                "source_type": "db",
                "scope": "db_product",
                "root": "FAB",
                "product": "PRODA",
                "file": "",
                "score": 9,
                "terms": ["FAB"],
                "needs": [],
                "viable": True,
            }
        ], {"relations": [], "catalog": []}),
    )

    def fake_filebrowser(payload, *, username="", agent_context=None):
        captured["payload"] = payload
        return _fake_fb_result()

    monkeypatch.setattr(filebrowser_ai_sql_runtime, "run_filebrowser_ai_sql_runtime", fake_filebrowser)
    monkeypatch.setattr(flowi_multisource, "_load_schema_registry", lambda: {"relations": [], "column_catalog": []})

    out = runtime.run_home_sql_join_dashboard_runtime(
        {"natural_language": "PRODA FAB IOFF table", "max_rows": 5},
        username="tester",
    )

    assert out["ok"] is True
    assert captured["payload"]["scope"] == "db_product"
    assert captured["payload"]["root"] == "FAB"
    assert captured["payload"]["product"] == "PRODA"
    assert out["source_resolution"]["needs_input"] is False
    assert out["joined"]["single_source"] is True


def test_ambiguous_source_candidates_block_without_guessing(monkeypatch):
    _patch_no_langgraph(monkeypatch)

    monkeypatch.setattr(
        runtime,
        "_source_resolve_candidates",
        lambda prompt, product_hint, warnings: ([
            {"source_id": "db_FAB", "label": "FAB", "source_type": "db", "scope": "db_product", "root": "FAB", "product": "PRODA", "score": 6, "terms": ["FAB"], "needs": [], "viable": True},
            {"source_id": "db_INLINE", "label": "INLINE", "source_type": "db", "scope": "db_product", "root": "INLINE", "product": "PRODA", "score": 6, "terms": ["INLINE"], "needs": [], "viable": True},
        ], {"relations": [], "catalog": []}),
    )

    def fail_filebrowser(*_args, **_kwargs):
        raise AssertionError("FileBrowser SQL should not run for ambiguous sources")

    monkeypatch.setattr(filebrowser_ai_sql_runtime, "run_filebrowser_ai_sql_runtime", fail_filebrowser)
    out = runtime.run_home_sql_join_dashboard_runtime({"natural_language": "PRODA source chart"}, username="tester")

    assert out["ok"] is False
    assert out["status"] == "blocked"
    assert out["source_resolution"]["needs_input"] is True
    assert len(out["source_resolution"]["candidates"]) == 2


def test_dashboard_draft_delegates_to_dashboard_agent_preserving_chart_spec(monkeypatch):
    expected_chart = {
        "ok": True,
        "kind": "dashboard_scatter",
        "chart_type": "scatter",
        "title": "IOFF by wafer",
        "points": [{"x": 1, "y": 0.12}],
        "total": 1,
        "config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
        "chart_config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
        "status": "draft",
    }

    def fake_dashboard_agent(payload, *, username="", agent_context=None):
        assert payload["columns"] == ["wafer_id", "IOFF", "lot_id"]
        assert payload["sample_rows"][0]["IOFF"] == 0.12
        return {
            "ok": True,
            "status": "success",
            "run_id": "dash_sub",
            "trace": [{"node_id": "render_spec", "status": "success", "warnings": []}],
            "chart_type": "scatter",
            "config": expected_chart["config"],
            "chart_result": expected_chart,
            "warnings": [],
        }

    monkeypatch.setattr(dashboard_agent_runtime, "run_dashboard_agent_runtime", fake_dashboard_agent)

    warnings: list[str] = []
    out = runtime._dashboard_draft(
        {
            "username": "tester",
            "request": {"natural_language": "IOFF 산점도"},
            "base_source": {"product": "PRODA"},
            "output_route": {"mode": "chart"},
            "joined": {
                "columns": ["wafer_id", "IOFF", "lot_id"],
                "sample_rows": [{"wafer_id": 1, "IOFF": 0.12, "lot_id": "A1000"}],
                "row_count": 1,
            },
        },
        warnings,
    )

    assert warnings == []
    chart_result = out["dashboard"]["chart_result"]
    assert chart_result["chart_type"] == expected_chart["chart_type"]
    assert chart_result["points"] == expected_chart["points"]
    assert out["dashboard"]["sub_run_id"] == "dash_sub"
    assert out["dashboard"]["sub_trace"][0]["node_id"] == "render_spec"
    evidence = chart_result["config"]["source_evidence"]
    assert evidence["source_ids"] == []
    assert evidence["sub_trace"]["dashboard_agent"][0]["node_id"] == "render_spec"


def test_single_source_chart_request_delegates_without_join(monkeypatch):
    _patch_no_langgraph(monkeypatch)
    captured = {}

    monkeypatch.setattr(filebrowser_ai_sql_runtime, "run_filebrowser_ai_sql_runtime", lambda *_args, **_kwargs: _fake_fb_result())
    monkeypatch.setattr(flowi_multisource, "_load_schema_registry", lambda: {"relations": [], "column_catalog": []})

    def fake_dashboard(payload, *, username="", agent_context=None):
        captured["payload"] = payload
        return {
            "ok": True,
            "status": "success",
            "run_id": "dash_sub",
            "trace": [{"node_id": "render_spec", "status": "success", "warnings": []}],
            "chart_type": "scatter",
            "config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
            "chart_result": {
                "ok": True,
                "kind": "dashboard_scatter",
                "chart_type": "scatter",
                "points": [{"x": 1, "y": 0.12}],
                "total": 1,
                "config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
                "chart_config": {"chart_type": "scatter", "x": "wafer_id", "y": "IOFF"},
            },
            "warnings": [],
        }

    monkeypatch.setattr(dashboard_agent_runtime, "run_dashboard_agent_runtime", fake_dashboard)
    out = runtime.run_home_sql_join_dashboard_runtime(
        {"natural_language": "wafer별 IOFF chart", "root": "FAB", "product": "PRODA"},
        username="tester",
    )

    assert out["ok"] is True
    assert out["data_need"]["needs_join"] is False
    assert out["join_plan"]["single_source"] is True
    assert captured["payload"]["sample_rows"][0]["IOFF"] == 0.12
    assert out["dashboard"]["chart_result"]["config"]["source_evidence"]["single_source"] is True


def test_confirmed_relation_join_adds_source_evidence(monkeypatch):
    _patch_no_langgraph(monkeypatch)
    monkeypatch.setattr(filebrowser_ai_sql_runtime, "run_filebrowser_ai_sql_runtime", lambda *_args, **_kwargs: _fake_fb_result())
    monkeypatch.setattr(flowi_multisource, "_lookup_prompt_knowledge", lambda prompt: ([], set(), set()))
    monkeypatch.setattr(
        flowi_multisource,
        "_load_schema_registry",
        lambda: {
            "relations": [
                {
                    "relation_id": "fab_inline_lot",
                    "status": "confirmed",
                    "left_source_id": "db_FAB",
                    "left_source_type": "db",
                    "left_label": "FAB",
                    "left_column": "lot_id",
                    "right_source_id": "db_INLINE",
                    "right_source_type": "db",
                    "right_label": "INLINE",
                    "right_column": "lot_id",
                    "canonical_key": "lot_id",
                }
            ],
            "column_catalog": [],
        },
    )

    def fake_schema(source):
        source.files = [Path("/tmp/fake.parquet")]
        if source.source_id == "db_FAB":
            source.columns = ["lot_id", "wafer_id", "IOFF"]
            source.dtypes = {"IOFF": "Float64", "wafer_id": "Int64", "lot_id": "String"}
        else:
            source.columns = ["lot_id", "INLINE_CD"]
            source.dtypes = {"INLINE_CD": "Float64", "lot_id": "String"}

    def fake_source_frame(source, filters):
        if source.source_id == "db_FAB":
            return pl.DataFrame([{"FAB.lot_id": "A1000", "FAB.wafer_id": 1, "FAB.IOFF": 0.12, "__join_lot_id": "A1000"}]), []
        return pl.DataFrame([{"INLINE.lot_id": "A1000", "INLINE.INLINE_CD": 22.5, "__join_lot_id": "A1000"}]), []

    monkeypatch.setattr(flowi_multisource, "_resolve_source_files", lambda source, product_hint="": ([Path("/tmp/fake.parquet")], []))
    monkeypatch.setattr(flowi_multisource, "_schema_for_source", fake_schema)
    monkeypatch.setattr(flowi_multisource, "_source_frame", fake_source_frame)
    monkeypatch.setattr(dashboard_agent_runtime, "run_dashboard_agent_runtime", lambda *_args, **_kwargs: {
        "ok": True,
        "status": "success",
        "run_id": "dash_join",
        "trace": [{"node_id": "render_spec", "status": "success", "warnings": []}],
        "chart_type": "scatter",
        "config": {"chart_type": "scatter", "x": "FAB.wafer_id", "y": "INLINE.INLINE_CD"},
        "chart_result": {
            "ok": True,
            "kind": "dashboard_scatter",
            "chart_type": "scatter",
            "points": [{"x": 1, "y": 22.5}],
            "total": 1,
            "config": {"chart_type": "scatter", "x": "FAB.wafer_id", "y": "INLINE.INLINE_CD"},
            "chart_config": {"chart_type": "scatter", "x": "FAB.wafer_id", "y": "INLINE.INLINE_CD"},
        },
        "warnings": [],
    })

    out = runtime.run_home_sql_join_dashboard_runtime(
        {"natural_language": "FAB INLINE join chart", "root": "FAB", "product": "PRODA"},
        username="tester",
    )

    evidence = out["dashboard"]["chart_result"]["config"]["source_evidence"]
    assert out["ok"] is True
    assert evidence["relation_ids"] == ["fab_inline_lot"]
    assert evidence["join_keys"] == ["lot_id"]
    assert set(evidence["source_ids"]) == {"db_FAB", "db_INLINE"}


def test_unconfirmed_relation_blocks_join(monkeypatch):
    _patch_no_langgraph(monkeypatch)
    monkeypatch.setattr(filebrowser_ai_sql_runtime, "run_filebrowser_ai_sql_runtime", lambda *_args, **_kwargs: _fake_fb_result())
    monkeypatch.setattr(flowi_multisource, "_lookup_prompt_knowledge", lambda prompt: ([], set(), set()))
    monkeypatch.setattr(
        flowi_multisource,
        "_load_schema_registry",
        lambda: {
            "relations": [
                {
                    "relation_id": "fab_inline_lot",
                    "status": "draft",
                    "left_source_id": "db_FAB",
                    "left_source_type": "db",
                    "left_label": "FAB",
                    "left_column": "lot_id",
                    "right_source_id": "db_INLINE",
                    "right_source_type": "db",
                    "right_label": "INLINE",
                    "right_column": "lot_id",
                    "canonical_key": "lot_id",
                }
            ],
            "column_catalog": [],
        },
    )
    monkeypatch.setattr(flowi_multisource, "_resolve_source_files", lambda source, product_hint="": ([Path("/tmp/fake.parquet")], []))
    monkeypatch.setattr(flowi_multisource, "_schema_for_source", lambda source: setattr(source, "columns", ["lot_id", "value"]))

    out = runtime.run_home_sql_join_dashboard_runtime(
        {"natural_language": "FAB INLINE join chart", "root": "FAB", "product": "PRODA"},
        username="tester",
    )

    assert out["ok"] is False
    assert out["status"] == "blocked"
    assert out["join_plan"]["blocked"] is True
    assert any("confirmed" in item for item in out["join_plan"]["missing_evidence"])
