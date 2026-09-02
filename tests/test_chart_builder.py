import polars as pl
import datetime as dt
import pytest
from fastapi import HTTPException

from core import audit
from core.chart_builder_definition import (
    ChartBuilderDefinitionError,
    format_chart_builder_definition,
    parse_chart_builder_definition,
)
from routers import filebrowser


ASSISTANT_CHART_CODE = """
Q1
TABLE = ET
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, value

Q2
TABLE = INLINE
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, split

JOIN q1 LEFT q2 ON root_lot_id, wafer_id

CHART
TYPE = scatter
X = wafer_id
Y = value
WIDTH = 1000
HEIGHT = 500

MAX_ROWS = 100
"""


def _assistant(instruction: str) -> dict:
    return filebrowser._chart_builder_assistant_plan(filebrowser.ChartBuilderAssistantReq(
        instruction=instruction,
        definition_code=ASSISTANT_CHART_CODE,
        columns=["root_lot_id", "wafer_id", "value", "split"],
    ))


def test_chart_assistant_applies_common_visual_tweaks_without_query_rerun():
    resized = _assistant("차트 높이와 넓이를 조금 키워줘")
    assert resized["chart"]["width"] == 1200
    assert resized["chart"]["height"] == 600
    assert resized["requires_rerun"] is False

    colored = _assistant("컬러를 파란색으로 바꿔줘")
    assert colored["chart"]["color"] == "custom"
    assert colored["chart"]["color_else"] == "blue"

    trellised = _assistant("split으로 trellis 바꿔줘")
    assert trellised["chart"]["trellis"] == "split"
    assert "TRELLIS = split" in trellised["canonical_code"]


def test_chart_assistant_sets_chart_type_axes_and_visibility_without_llm():
    changed = _assistant("scatter로 바꾸고 X축은 wafer_id, Y축은 value로. 범례 숨겨줘")

    assert changed["chart"]["type"] == "scatter"
    assert changed["chart"]["x"] == "wafer_id"
    assert changed["chart"]["y"] == "value"
    assert changed["chart"]["show_legend"] is False
    assert changed["llm"]["used"] is False


def test_chart_assistant_switches_y_axis_scale_without_query_rerun():
    changed = _assistant("Y축을 log scale로 바꿔줘")

    assert changed["chart"]["y_scale"] == "log"
    assert changed["requires_rerun"] is False
    assert "Y_SCALE = log" in changed["canonical_code"]


def test_chart_assistant_recommends_wafer_map_from_shot_columns_without_llm():
    result = filebrowser._chart_builder_assistant_plan(filebrowser.ChartBuilderAssistantReq(
        instruction="기본 차트 자동 추천해줘",
        definition_code=ASSISTANT_CHART_CODE,
        columns=["root_lot_id", "wafer_id", "shot_x", "shot_y", "value"],
    ))

    assert result["chart"]["type"] == "wafer_map"
    assert result["chart"]["x"] == "shot_x"
    assert result["chart"]["y"] == "value"
    assert result["llm"]["used"] is False


def test_chart_assistant_join_change_is_validated_and_requests_rerun():
    changed = _assistant("첫 JOIN을 inner로 바꿔줘")

    assert changed["joins"][0]["how"] == "inner"
    assert changed["requires_rerun"] is True
    assert changed["execution_target"] == "operating_api"
    assert "JOIN q1 INNER q2" in changed["canonical_code"]


def test_chart_assistant_llm_receives_automatic_type_conversion_contract(monkeypatch):
    from core import llm_adapter

    captured = {}
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def complete_json(prompt, *, system, **kwargs):
        captured["prompt"] = prompt
        captured["system"] = system
        return {"ok": True, "obj": {"message": "변경 없음", "operations": []}}

    monkeypatch.setattr(llm_adapter, "complete_json", complete_json)
    operations, message, info = filebrowser._chart_assistant_llm_operations(
        "문자열 wafer와 tkout_time 컬러링이 맞는지 확인해줘",
        parse_chart_builder_definition(ASSISTANT_CHART_CODE),
        ["root_lot_id", "wafer_id", "tkout_time", "value"],
    )

    assert operations == []
    assert message == "변경 없음"
    assert info["used"] is True
    assert "Runtime filters and JOIN keys already normalize" in captured["system"]
    payload = __import__("json").loads(captured["prompt"])
    contract = payload["type_conversion_contract"]
    assert "do not add CAST" in contract["runtime_filters"]
    assert "tkout_time WITHIN N DAYS" in contract["color_rules"]
    assert "TRY_CAST" in contract["manual_sql"]


def test_saved_chart_names_are_unique_and_searchable(tmp_path, monkeypatch):
    monkeypatch.setattr(filebrowser, "_chart_builder_history_path", lambda: tmp_path / "chart_history.jsonl")
    request = filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(id="q1", root="ET", product="PRODA", sql="SELECT shot_x, value")],
        chart={"type": "scatter", "x": "shot_x", "y": "value"},
        chart_name="VTH 산포",
    )
    result = {
        "sources": [{"id": "q1", "root": "ET", "product": "PRODA", "row_count": 2}],
        "joined": {"row_count": 2},
        "warnings": [],
    }

    first = filebrowser._record_chart_builder_history(username="engineer", req=request, result=result)
    second = filebrowser._record_chart_builder_history(username="engineer", req=request, result=result)

    assert first["history_id"] != second["history_id"]
    assert first["name"] == "VTH 산포"
    assert second["name"] == "VTH 산포 (2)"
    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "engineer"})
    search = filebrowser.chart_builder_history(object(), limit=100, q=second["history_id"])
    assert [row["name"] for row in search["history"]] == ["VTH 산포 (2)"]


def test_chart_history_returns_all_pins_before_recent_500_and_manager_can_unpin(tmp_path, monkeypatch):
    history_path = tmp_path / "chart_builder_history.jsonl"
    monkeypatch.setattr(filebrowser, "_chart_builder_history_path", lambda: history_path)
    for index in range(505):
        filebrowser.jsonl_append(history_path, {
            "event": "history",
            "history_id": f"chart_{index:03d}",
            "name": f"Chart {index:03d}",
            "timestamp": f"2026-08-01T00:{index // 60:02d}:{index % 60:02d}+00:00",
            "username": "engineer",
            "definition_code": ASSISTANT_CHART_CODE,
            "chart": {"type": "scatter", "x": "wafer_id", "y": "value"},
            "sources": [],
        }, max_lines=None)

    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "admin", "role": "admin"})
    pinned = filebrowser.chart_builder_history_pin(
        "chart_000", filebrowser.ChartBuilderPinReq(pinned=True), object(),
    )["chart"]
    assert pinned["pinned"] is True

    monkeypatch.setattr(filebrowser, "_require_filebrowser_user", lambda request: {"username": "admin", "role": "admin"})
    payload = filebrowser.chart_builder_history(object(), limit=500, q="")
    assert len(payload["history"]) == 501
    assert payload["history"][0]["history_id"] == "chart_000"
    assert payload["history"][0]["pinned"] is True
    assert payload["history"][1]["history_id"] == "chart_504"
    assert payload["pinned_count"] == 1
    assert payload["recent_count"] == 500

    unpinned = filebrowser.chart_builder_history_pin(
        "chart_000", filebrowser.ChartBuilderPinReq(pinned=False), object(),
    )["chart"]
    assert unpinned["pinned"] is False
    payload = filebrowser.chart_builder_history(object(), limit=500, q="")
    assert len(payload["history"]) == 500
    assert all(row["history_id"] != "chart_000" for row in payload["history"])


def test_chart_history_pin_requires_chartbuilder_manager(tmp_path, monkeypatch):
    history_path = tmp_path / "chart_builder_history.jsonl"
    monkeypatch.setattr(filebrowser, "_chart_builder_history_path", lambda: history_path)
    filebrowser.jsonl_append(history_path, {
        "event": "history", "history_id": "chart_one", "name": "One",
        "definition_code": ASSISTANT_CHART_CODE,
    }, max_lines=None)
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "viewer", "role": "user"})

    with pytest.raises(HTTPException) as exc_info:
        filebrowser.chart_builder_history_pin(
            "chart_one", filebrowser.ChartBuilderPinReq(pinned=True), object(),
        )

    assert exc_info.value.status_code == 403


def test_chart_builder_runtime_recent_days_filters_raw_source(tmp_path, monkeypatch):
    source = tmp_path / "recent.parquet"
    today = dt.datetime.now(dt.timezone.utc).date()
    pl.DataFrame({
        "tkout_time": [str(today - dt.timedelta(days=2)), str(today - dt.timedelta(days=30))],
        "value": [1.0, 2.0],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="q1",
            root="ET",
            product="P",
            sql="SELECT tkout_time, value",
            runtime_recent_days=7,
            runtime_date_column="tkout_time",
        )],
        save_history=False,
    ), object())

    assert result["joined"]["row_count"] == 1
    assert result["joined"]["rows"][0]["value"] == 1.0


def test_chart_builder_runtime_filters_cast_string_time_and_numeric_wafer(tmp_path, monkeypatch):
    """VARCHAR time and numeric wafer keys use the same safe comparison contract."""
    source = tmp_path / "typed_runtime_filters.parquet"
    today = dt.datetime.now(dt.timezone.utc).date()
    pl.DataFrame({
        "root_lot_id": ["A1234", "A1234", "A1234"],
        "wafer_id": [1, 2, 1],
        "tkout_time": [str(today - dt.timedelta(days=2)), str(today - dt.timedelta(days=2)), "not-a-date"],
        "value": [11.0, 12.0, 99.0],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="q1",
            root="ET",
            product="P",
            sql="SELECT root_lot_id, wafer_id, tkout_time, value",
            runtime_recent_days=7,
            runtime_date_column="tkout_time",
            runtime_lot_wafer_pairs=[{"root_lot_id": "a1234", "wafer_id": "W1"}],
        )],
        save_history=False,
    ), object())

    assert result["joined"]["row_count"] == 1
    assert str(result["joined"]["rows"][0]["wafer_id"]) == "1"
    assert result["joined"]["rows"][0]["value"] == 11.0


def test_definition_time_window_filters_the_raw_source_and_survives_a_round_trip(tmp_path, monkeypatch):
    """저장되는 코드가 시간 창을 지닌다 — Template Report 는 이 코드를 그대로 다시 실행한다."""
    source = tmp_path / "recent.parquet"
    today = dt.datetime.now(dt.timezone.utc).date()
    pl.DataFrame({
        "tkout_time": [str(today - dt.timedelta(days=2)), str(today - dt.timedelta(days=30))],
        "value": [1.0, 2.0],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    parsed = parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = P
SQL = SELECT tkout_time, value
RECENT_DAYS = 7
""")

    assert parsed["sources"][0]["runtime_recent_days"] == 7
    # DATE_COLUMN 을 안 적으면 tkout_time 이고, 그 사실이 저장 코드에 남아야 한다.
    assert parsed["sources"][0]["runtime_date_column"] == "tkout_time"
    assert "RECENT_DAYS = 7\nDATE_COLUMN = tkout_time" in parsed["canonical_code"]
    assert parse_chart_builder_definition(parsed["canonical_code"])["sources"][0]["runtime_recent_days"] == 7

    result = filebrowser.chart_builder_run(
        filebrowser.ChartBuilderRunReq(sources=parsed["sources"], save_history=False), object()
    )

    assert result["joined"]["row_count"] == 1
    assert result["joined"]["rows"][0]["value"] == 1.0


def test_definition_round_trips_linked_root_lot_and_wafer_filters():
    parsed = parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = P
SQL = SELECT root_lot_id, wafer_id, value
ROOT_LOTS = A1234, A5678
WAFERS = 1, W2
""")

    source = parsed["sources"][0]
    assert source["runtime_root_lot_ids"] == ["A1234", "A5678"]
    assert source["runtime_wafer_ids"] == ["1", "W2"]
    assert "ROOT_LOTS = A1234, A5678" in parsed["canonical_code"]
    assert parse_chart_builder_definition(parsed["canonical_code"])["sources"][0]["runtime_wafer_ids"] == ["1", "W2"]


def test_chart_builder_runtime_root_lot_and_wafer_filters_push_down(tmp_path, monkeypatch):
    source = tmp_path / "linked_filters.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1234", "A1234", "A5678"],
        "wafer_id": ["W1", "#2", "2"],
        "value": [1.0, 2.0, 3.0],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="q1", root="ET", product="P",
            sql="SELECT root_lot_id, wafer_id, value",
            runtime_root_lot_ids=["A1234"], runtime_wafer_ids=["W2"],
        )],
        save_history=False,
    ), object())

    assert result["joined"]["row_count"] == 1
    assert result["joined"]["rows"][0]["value"] == 2.0


def test_chart_builder_derived_column_can_be_filtered_and_keeps_only_visible_helpers(tmp_path, monkeypatch):
    source = tmp_path / "derived_filter.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1234", "A1234", "B5678"],
        "wafer_id": ["1", "2", "1"],
        "purpose": ["KEEP", "DROP", "KEEP"],
        "value": [11.0, 12.0, 21.0],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="q1", root="ET", product="P", sql="SELECT value",
            derived_columns=[{"name": "lot_wafer", "columns": ["root_lot_id", "wafer_id"], "separator": "_"}],
            runtime_filters=[
                {"column": "purpose", "operator": "in", "values": ["KEEP"]},
                {"column": "lot_wafer", "operator": "equals", "values": ["B5678_1"]},
            ],
        )],
        save_history=False,
    ), object())

    assert result["joined"]["columns"] == ["value", "lot_wafer"]
    assert result["joined"]["rows"] == [{"value": 21.0, "lot_wafer": "B5678_1"}]


def test_chart_builder_linked_color_table_filters_exact_lot_wafer_pairs(tmp_path, monkeypatch):
    source = tmp_path / "linked_pair_filters.parquet"
    pl.DataFrame({
        "root_lot_id": ["A1234", "A1234", "B5678", "B5678"],
        "wafer_id": ["W1", "W2", "W1", "#2"],
        "value": [11.0, 12.0, 21.0, 22.0],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="q1", root="ET", product="P",
            sql="SELECT root_lot_id, wafer_id, value",
            runtime_root_lot_ids=["A1234", "B5678"],
            runtime_wafer_ids=["1", "2"],
            runtime_lot_wafer_pairs=[
                {"root_lot_id": "A1234", "wafer_id": "1"},
                {"root_lot_id": "B5678", "wafer_id": "2"},
            ],
        )],
        save_history=False,
    ), object())

    assert [(row["root_lot_id"], row["value"]) for row in result["joined"]["rows"]] == [
        ("A1234", 11.0), ("B5678", 22.0),
    ]


def test_definition_rejects_a_date_column_without_a_window():
    """DATE_COLUMN 만 적으면 아무 조건도 안 걸린다 — 조용히 흘리지 않는다."""
    try:
        parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = P
SQL = SELECT tkout_time, value
DATE_COLUMN = tkout_time
""")
    except ChartBuilderDefinitionError as exc:
        assert "RECENT_DAYS" in str(exc)
    else:
        raise AssertionError("DATE_COLUMN 단독 사용은 오류여야 한다")


def test_chart_builder_runs_two_read_only_queries_and_joins(tmp_path, monkeypatch):
    left = tmp_path / "left.parquet"
    right = tmp_path / "right.parquet"
    pl.DataFrame({
        "root_lot_id": ["A", "B"],
        "wafer_id": ["1", "2"],
        "tkout_time": ["2026-01-01", "2026-01-02"],
        "value": [1.5, 2.5],
    }).write_parquet(left)
    pl.DataFrame({
        "root_lot_id": ["A", "C"],
        "wafer_id": [1, 3],
        "split": ["S1", "S2"],
        "value": [10.0, 30.0],
    }).write_parquet(right)

    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [left] if root == "ET" else [right])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(filebrowser, "_chart_builder_history_path", lambda: tmp_path / "chart_history.jsonl")
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    req = filebrowser.ChartBuilderRunReq(
        sources=[
            filebrowser.ChartBuilderSourceReq(id="et", root="ET", product="P", sql="SELECT root_lot_id, wafer_id, tkout_time, value"),
            filebrowser.ChartBuilderSourceReq(id="inline", root="INLINE", product="P", sql="SELECT root_lot_id, wafer_id, split, value"),
        ],
        joins=[filebrowser.ChartBuilderJoinReq(left="et", right="inline", left_on="root_lot_id, wafer_id", right_on="root_lot_id, wafer_id", how="inner")],
        max_rows=100,
    )

    result = filebrowser.chart_builder_run(req, object())

    assert result["ok"] is True
    assert result["joined"]["row_count"] == 1
    assert result["joined"]["rows"][0]["root_lot_id"] == "A"
    assert result["joined"]["rows"][0]["inline__value"] == 10.0
    assert result["joins"][0]["left_on"] == ["root_lot_id", "wafer_id"]
    assert result["sources"][0]["sql"].startswith("SELECT root_lot_id, wafer_id, tkout_time, value")
    history = filebrowser.jsonl_read(tmp_path / "chart_history.jsonl")
    assert history[0]["username"] == "tester"
    assert history[0]["row_count"] == 1
    assert "JOIN et INNER inline ON root_lot_id, wafer_id" in history[0]["definition_code"]


def test_chart_builder_enriches_inline_subitems_with_authoritative_teg_coordinates(tmp_path, monkeypatch):
    source = tmp_path / "inline.parquet"
    pl.DataFrame({
        "root_lot_id": ["A", "A"],
        "wafer_id": ["1", "1"],
        "step_id": ["STEP1", "STEP1"],
        "item_id": ["CD1", "CD1"],
        "subitem_id": ["SITE_1", "UNMAPPED"],
        "shot_x": [999, 998],
        "shot_y": [999, 998],
        "value": [10.5, 20.5],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(filebrowser.inline_coordinates, "load_matching_rules", lambda *args, **kwargs: [{
        "product": "P", "step_id": "STEP1", "item_id": "CD1",
        "matching_table": "MAP_A", "available": True, "vehicle": "VH_P", "shot_count": 1,
    }])
    monkeypatch.setattr(filebrowser.inline_coordinates, "load_coordinate_mapping", lambda *args, **kwargs: {
        "configured": True,
        "configured_tables": ["MAP_A"],
        "missing_tables": [],
        "rows": [{
            "product": "p", "step_id": "step1", "item_id": "cd1", "subitem_id": "site_1",
            "shot_x": -2.0, "shot_y": 3.0, "matching_table": "MAP_A",
        }],
    })

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="inline", root="INLINE", product="P",
            # The mapping keys are deliberately omitted. ChartBuilder must read
            # them internally, attach coordinates, then hide those helper cols.
            sql="SELECT root_lot_id, wafer_id, shot_x, shot_y, value",
        )],
        save_history=False,
    ), object())

    rows = result["joined"]["rows"]
    assert rows[0]["shot_x"] == -2.0
    assert rows[0]["shot_y"] == 3.0
    assert rows[0]["raw_inline_shot_x"] == 999
    assert rows[0]["raw_inline_shot_y"] == 999
    assert rows[0]["inline_map_name"] == "MAP_A"
    assert rows[0]["inline_vehicle"] == "VH_P"
    assert rows[1]["shot_x"] is None
    assert "step_id" not in result["joined"]["columns"]
    mapping = result["sources"][0]["inline_coordinate_mapping"]
    assert mapping["applied"] is True
    assert mapping["matched_rows"] == 1
    assert mapping["unmatched_rows"] == 1
    assert mapping["match_rate"] == 50.0
    assert mapping["vehicles"] == ["VH_P"]


def test_chart_builder_accepts_a_single_trend_query(tmp_path, monkeypatch):
    source = tmp_path / "trend.parquet"
    pl.DataFrame({"tkout_time": ["2026-01-02", "2026-01-01"], "item_id": ["OTHER", "VTH"], "value": [4.1, 3.2]}).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(filebrowser, "_chart_builder_history_path", lambda: tmp_path / "chart_history.jsonl")
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(id="trend", root="ET", product="P", sql="SELECT tkout_time, value WHERE item_id = 'VTH' ORDER BY tkout_time")],
        joins=[],
    ), object())

    assert result["joined"]["row_count"] == 1
    assert result["joined"]["source_ids"] == ["trend"]
    assert result["sources"][0]["sql"].endswith("ORDER BY tkout_time ASC")
    assert result["max_rows"] == 10000


def test_chart_builder_uses_full_shot_yield_as_a_virtual_corr_source(tmp_path, monkeypatch):
    from core import yield_map

    frame = pl.DataFrame({
        "product": ["P", "P"], "root_lot_id": ["A", "A"], "lot_id": ["A", "A"],
        "wafer_id": ["1", "1"], "shot_x": [0, 1], "shot_y": [0, 0],
        "shot_yield": [75.0, 100.0], "good_die": [3, 4], "total_die": [4, 4],
        "expected_die": [4, 4], "is_full_shot": [True, True],
    })
    monkeypatch.setattr(yield_map, "shot_yield_frame", lambda product: frame)
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "tester"})
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="yield", root="YIELD_SHOT", product="P",
            sql="SELECT root_lot_id, wafer_id, shot_x, shot_y, shot_yield WHERE shot_yield >= 80",
        )],
        chart={"type": "scatter", "x": "shot_yield", "y": "other_value"},
        save_history=False,
    ), object())

    assert result["joined"]["row_count"] == 1
    assert result["joined"]["rows"][0]["shot_yield"] == 100.0
    assert result["sources"][0]["grain"] == "full_shot"
    assert result["sources"][0]["virtual_source"] is True


def test_chart_builder_definition_parses_multiline_sql_and_join():
    parsed = parse_chart_builder_definition("""
Q1
TABLE = INLINE
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, value
      WHERE item_id = 'CD1'

Q2 | TABLE=ET | PRODUCT=PRODA | SQL=SELECT root_lot_id, wafer_id, value
JOIN q1 LEFT q2 ON root_lot_id, wafer_id
MAX_ROWS = 500
""")

    assert [source["id"] for source in parsed["sources"]] == ["q1", "q2"]
    assert parsed["sources"][0]["root"] == "INLINE"
    assert parsed["sources"][0]["sql"].endswith("WHERE item_id = 'CD1'")
    assert parsed["joins"] == [{
        "left": "q1",
        "right": "q2",
        "left_on": "root_lot_id, wafer_id",
        "right_on": "root_lot_id, wafer_id",
        "how": "left",
    }]
    assert parsed["max_rows"] == 500


def test_chart_builder_definition_round_trips_named_queries_and_mapped_join_keys():
    code = format_chart_builder_definition(
        sources=[
            {"id": "inline", "root": "INLINE", "product": "P", "sql": "SELECT lot_id, wf_id"},
            {"id": "et", "root": "ET", "product": "P", "sql": "SELECT root_lot_id, wafer_id"},
        ],
        joins=[{"left": "inline", "right": "et", "left_on": "lot_id, wf_id", "right_on": "root_lot_id, wafer_id", "how": "inner"}],
        max_rows=250,
    )

    parsed = parse_chart_builder_definition(code)

    assert [source["id"] for source in parsed["sources"]] == ["inline", "et"]
    assert parsed["joins"][0]["left_on"] == "lot_id, wf_id"
    assert parsed["joins"][0]["right_on"] == "root_lot_id, wafer_id"
    assert parsed["joins"][0]["how"] == "inner"
    assert parsed["max_rows"] == 250


def test_chart_builder_definition_round_trips_derived_columns_and_value_filters():
    parsed = parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = P
SQL = SELECT root_lot_id, wafer_id, purpose, value
DERIVE = lot_wafer | columns=root_lot_id,wafer_id | separator=_
DERIVE = purpose_wafer = purpose + "-" + wafer_id
FILTER = lot_wafer | operator=in | values=A1234_1, B5678_2
FILTER = purpose | operator=not_contains | values=MONITOR
""")

    source = parsed["sources"][0]
    assert source["derived_columns"] == [
        {"name": "lot_wafer", "columns": ["root_lot_id", "wafer_id"], "separator": "_"},
        {"name": "purpose_wafer", "columns": ["purpose", "wafer_id"], "separator": "-"},
    ]
    assert source["runtime_filters"] == [
        {"column": "lot_wafer", "operator": "in", "values": ["A1234_1", "B5678_2"]},
        {"column": "purpose", "operator": "not_contains", "values": ["MONITOR"]},
    ]
    reparsed = parse_chart_builder_definition(parsed["canonical_code"])["sources"][0]
    assert reparsed["derived_columns"] == source["derived_columns"]
    assert reparsed["runtime_filters"] == source["runtime_filters"]


def test_chart_builder_definition_round_trips_chart_custom_color_and_reformatter():
    parsed = parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = PRODA
SQL = SELECT root_lot_id, wafer_id, tkout_time, VTH_INDEX
REFORMATTER = true
ITEMS = VTH_INDEX, VTH_DELTA

CHART
TYPE = scatter
X = tkout_time
Y = VTH_INDEX
COLOR = custom
COLOR_RULE = root_lot_id = 'AAAAA' AND wafer_id = 'BB' THEN red
COLOR_RULE = root_lot_id = 'AAAAA' AND wafer_id = 'B1' THEN blue
COLOR_ELSE = gray
HIGHLIGHT = true
SHOW_LEGEND = false
WIDTH = 1280
HEIGHT = 720
MAX_ROWS = 300
""")

    assert parsed["sources"][0]["apply_reformatter"] is True
    assert parsed["sources"][0]["reformatter_items"] == "VTH_INDEX, VTH_DELTA"
    assert parsed["sources"][0]["runtime_lot_wafer_pairs"] == [
        {"root_lot_id": "AAAAA", "wafer_id": "BB"},
        {"root_lot_id": "AAAAA", "wafer_id": "B1"},
    ]
    assert parsed["chart"] == {
        "type": "scatter",
        "x": "tkout_time",
        "y": "VTH_INDEX",
        "color": "custom",
        "color_rules": [
            "root_lot_id = 'AAAAA' AND wafer_id = 'BB' THEN red",
            "root_lot_id = 'AAAAA' AND wafer_id = 'B1' THEN blue",
        ],
        "color_else": "gray",
        "highlight": True,
        "show_legend": False,
        "width": 1280,
        "height": 720,
    }
    assert "REFORMATTER = true" in parsed["canonical_code"]
    assert "CHART\nTYPE = scatter" in parsed["canonical_code"]
    assert "SHOW_LEGEND = false" in parsed["canonical_code"]
    assert "WIDTH = 1280\nHEIGHT = 720" in parsed["canonical_code"]
    assert parse_chart_builder_definition(parsed["canonical_code"])["sources"][0]["runtime_lot_wafer_pairs"] == parsed["sources"][0]["runtime_lot_wafer_pairs"]


def test_chart_builder_definition_preserves_tkout_time_within_color_rules():
    """시간 강조 규칙은 저장 코드가 소유하며 Template Report가 같은 코드로 재실행한다."""
    parsed = parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = PRODA
SQL = SELECT tkout_time, VTH_INDEX
RECENT_DAYS = 30
DATE_COLUMN = tkout_time

CHART
TYPE = line
X = tkout_time
Y = VTH_INDEX
COLOR = custom
COLOR_RULE = tkout_time WITHIN 3 DAYS THEN #dc2626
COLOR_RULE = tkout_time WITHIN 7 DAYS THEN #f59e0b
COLOR_ELSE = #cbd5e1

MAX_ROWS = 10000
""")

    assert parsed["chart"]["color_rules"] == [
        "tkout_time WITHIN 3 DAYS THEN #dc2626",
        "tkout_time WITHIN 7 DAYS THEN #f59e0b",
    ]
    assert parsed["sources"][0]["runtime_lot_wafer_pairs"] == []
    assert "COLOR_RULE = tkout_time WITHIN 7 DAYS THEN #f59e0b" in parsed["canonical_code"]
    assert parse_chart_builder_definition(parsed["canonical_code"])["chart"] == parsed["chart"]


def test_chart_builder_definition_rejects_invalid_within_color_rule():
    with pytest.raises(ChartBuilderDefinitionError, match="WITHIN 날짜"):
        parse_chart_builder_definition("""
Q1
TABLE = ET
PRODUCT = PRODA
SQL = SELECT tkout_time, value

CHART
TYPE = scatter
X = tkout_time
Y = value
COLOR_RULE = tkout_time WITHIN 0 DAYS THEN red
""")


def test_chart_builder_definition_accepts_chart_size_shorthand():
    parsed = parse_chart_builder_definition("""
Q1 | TABLE=ET | PRODUCT=PRODA | SQL=SELECT wafer_id, value
CHART | TYPE=scatter | X=wafer_id | Y=value | SIZE=900x500
MAX_ROWS = 10
""")

    assert parsed["chart"]["width"] == 900
    assert parsed["chart"]["height"] == 500
    assert "WIDTH = 900\nHEIGHT = 500" in parsed["canonical_code"]


def test_chart_builder_definition_round_trips_log_y_scale_and_positive_range():
    parsed = parse_chart_builder_definition("""
Q1 | TABLE=ET | PRODUCT=PRODA | SQL=SELECT wafer_id, value
CHART | TYPE=scatter | X=wafer_id | Y=value | Y_SCALE=log | Y_MIN=0.1 | Y_MAX=100
""")

    assert parsed["chart"]["y_scale"] == "log"
    assert parsed["chart"]["y_min"] == 0.1
    assert parsed["chart"]["y_max"] == 100.0
    assert parse_chart_builder_definition(parsed["canonical_code"])["chart"] == parsed["chart"]

    with pytest.raises(ChartBuilderDefinitionError, match="0보다 커야"):
        parse_chart_builder_definition("""
Q1 | TABLE=ET | PRODUCT=PRODA | SQL=SELECT wafer_id, value
CHART | TYPE=scatter | X=wafer_id | Y=value | Y_SCALE=log | Y_MIN=0 | Y_MAX=100
""")


def test_chart_builder_et_reformatter_uses_download_engine(tmp_path, monkeypatch):
    from routers import reformatize

    captured = {}

    def fake_run(req, user):
        captured["product"] = req.product
        captured["items"] = req.items
        captured["username"] = user["username"]
        return {
            "rows": [
                {"root_lot_id": "A", "wafer_id": "1", "VTH_INDEX": 1.25},
                {"root_lot_id": "B", "wafer_id": "2", "VTH_INDEX": 2.5},
            ],
            "columns": ["root_lot_id", "wafer_id", "VTH_INDEX"],
            "index_columns": ["root_lot_id", "wafer_id"],
            "total_rows": 2,
            "vehicle_csv": "PRODA.csv",
            "rule_errors": [],
        }

    monkeypatch.setattr(reformatize, "run", fake_run)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda **kwargs: (_ for _ in ()).throw(AssertionError("raw source must not be used")))
    monkeypatch.setattr(filebrowser, "current_user", lambda request: {"username": "engineer", "role": "user"})
    monkeypatch.setattr(filebrowser, "_chart_builder_history_path", lambda: tmp_path / "chart_history.jsonl")
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: None)

    result = filebrowser.chart_builder_run(filebrowser.ChartBuilderRunReq(
        sources=[filebrowser.ChartBuilderSourceReq(
            id="et",
            root="ET",
            product="PRODA",
            sql="SELECT root_lot_id, wafer_id, VTH_INDEX WHERE VTH_INDEX > 2",
            apply_reformatter=True,
            reformatter_items="VTH_INDEX",
        )],
        chart={"type": "scatter", "x": "wafer_id", "y": "VTH_INDEX", "highlight": True},
    ), object())

    assert captured == {"product": "PRODA", "items": ["VTH_INDEX"], "username": "engineer"}
    assert result["sources"][0]["apply_reformatter"] is True
    assert result["sources"][0]["vehicle_csv"] == "PRODA.csv"
    assert result["joined"]["row_count"] == 1
    assert result["joined"]["rows"][0]["VTH_INDEX"] == 2.5
    history = filebrowser.jsonl_read(tmp_path / "chart_history.jsonl")
    assert history[0]["chart"]["highlight"] is True
    assert "ITEMS = VTH_INDEX" in history[0]["definition_code"]


def test_chart_builder_split_table_schema_search_uses_virtual_source(tmp_path, monkeypatch):
    source = tmp_path / "split.parquet"
    pl.DataFrame({
        "ROOT_LOT_ID": ["A"],
        "WAFER_ID": [1],
        "KNOB_1.0 STI": ["PPID_01_1"],
        "FAB_10.0 TOOL": ["EQP_01"],
    }).write_parquet(source)
    monkeypatch.setattr(filebrowser, "source_data_files", lambda root, product: [source])

    schema, source_size = filebrowser._schema_for_product_source("SPLITTABLE", "ML_TABLE_P")

    assert list(schema) == ["ROOT_LOT_ID", "WAFER_ID", "KNOB_1.0 STI", "FAB_10.0 TOOL"]
    assert source_size == source.stat().st_size


def test_chart_builder_radius_layout_matches_product_mask(tmp_path, monkeypatch):
    pl.DataFrame({
        "Mask": ["VH_PRODA", "VH_PRODA", "VH_PRODB"],
        "chip_x_adj": [1, 2, 1],
        "chip_y_adj": [3, 4, 2],
        "Chip_Radius": [120.5, 99.25, 88.0],
    }).write_csv(tmp_path / "Chip_Radius.csv")
    monkeypatch.setattr(filebrowser, "_db_root", lambda: tmp_path)

    result = filebrowser._chart_builder_radius_layout("PRODA")

    assert result["mask"] == "VH_PRODA"
    assert result["row_count"] == 2
    assert result["rows"][0]["radius"] in {99.25, 120.5}
