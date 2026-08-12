import polars as pl
import datetime as dt
import pytest

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


def test_chart_assistant_join_change_is_validated_and_requests_rerun():
    changed = _assistant("첫 JOIN을 inner로 바꿔줘")

    assert changed["joins"][0]["how"] == "inner"
    assert changed["requires_rerun"] is True
    assert changed["execution_target"] == "operating_api"
    assert "JOIN q1 INNER q2" in changed["canonical_code"]


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
WIDTH = 1280
HEIGHT = 720
MAX_ROWS = 300
""")

    assert parsed["sources"][0]["apply_reformatter"] is True
    assert parsed["sources"][0]["reformatter_items"] == "VTH_INDEX, VTH_DELTA"
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
        "width": 1280,
        "height": 720,
    }
    assert "REFORMATTER = true" in parsed["canonical_code"]
    assert "CHART\nTYPE = scatter" in parsed["canonical_code"]
    assert "WIDTH = 1280\nHEIGHT = 720" in parsed["canonical_code"]


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
