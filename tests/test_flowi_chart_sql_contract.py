import inspect
from pathlib import Path

from routers import llm
from routers import dashboard


def test_trend_time_prefers_tkout_time():
    column, question = llm._flowi_trend_time_column(
        "CD trend 보여줘", ["event_time", "tkout_time", "value"], "ET"
    )

    assert column == "tkout_time"
    assert question is None


def test_trend_time_requires_choice_when_tkout_is_missing():
    column, question = llm._flowi_trend_time_column(
        "CD trend 보여줘", ["measure_time", "event_time", "value"], "INLINE"
    )

    assert column == ""
    assert question["missing"] == ["time_column"]
    assert [row["time_column"] for row in question["table"]["rows"]] == ["measure_time", "event_time"]


def test_trend_payload_requests_sql_chart_columns_for_et_shots():
    payload = llm._flowi_dashboard_source_runtime_payload(
        "PRODA ET shot trend 그려줘", "PRODA", 20
    )

    assert payload["root"] == "ET"
    assert payload["preferred_selected_columns"] == [
        "root_lot_id", "wafer_id", "tkout_time", "value", "chip_x_pos", "chip_y_pos"
    ]
    assert "Default aggregation is INLINE AVG and ET MEDIAN" in payload["query_rules"]


def test_inline_sql_excludes_summary_subitems_and_keeps_shot_id():
    sql = llm._flowi_dashboard_sql_from_config({
        "source_type": "INLINE",
        "item_id": "CD1",
        "aggregation": "shot",
        "x_col": "measure_time",
    })

    assert "subitem_id, measure_time, value AS y" in sql
    assert "'AVG'" in sql and "'Q3'" in sql


def test_et_shot_sql_contains_physical_coordinates():
    sql = llm._flowi_dashboard_sql_from_config({
        "source_type": "ET",
        "item_id": "IOFF",
        "aggregation": "shot",
    })

    assert "chip_x_pos, chip_y_pos, tkout_time, value AS y" in sql


def test_wafer_map_sql_returns_chart_source_rows():
    sql = llm._flowi_dashboard_sql_from_config({
        "chart_type": "wafer_map",
        "source_type": "ET",
        "item_id": "IOFF",
        "coord_x": "chip_x_pos",
        "coord_y": "chip_y_pos",
    })

    assert sql.startswith("SELECT root_lot_id, wafer_id, chip_x_pos, chip_y_pos, value FROM ET")


def test_korean_splittable_show_prompt_uses_inline_view_path():
    assert llm._flowi_explicit_splittable_view_prompt("A1003 스플릿테이블 보여줘") is True


def test_explicit_splittable_root_runs_data_tool_before_navigation(monkeypatch):
    expected = {"handled": True, "type": "split_view", "split_view": {"rows": [{"parameter": "KNOB_A"}]}}
    monkeypatch.setattr(llm, "_handle_flowi_splittable_context_followup", lambda *args, **kwargs: {"handled": False})
    monkeypatch.setattr(llm, "_handle_wafer_split_at_step", lambda *args, **kwargs: expected)

    actual = llm._handle_explicit_splittable_view_fast_path(
        "A1003 스플릿테이블 보여줘",
        "",
        12,
        {"splittable"},
    )

    assert actual is expected


def test_plain_dashboard_prompt_means_current_wip_dashboard():
    assert llm._flowi_dashboard_wip_prompt("대시보드 보여줘") is True
    assert llm._flowi_dashboard_wip_prompt("show the WIP dashboard") is True
    assert llm._flowi_dashboard_wip_prompt("INLINE IOFF trend dashboard 보여줘") is False


def test_wip_dashboard_defaults_to_step_desc_number_axis():
    summary_axis = inspect.signature(dashboard.wip_split_summary).parameters["axis"].default
    lots_axis = inspect.signature(dashboard.wip_split_lots).parameters["axis"].default
    assert summary_axis.default == "step_desc"
    assert lots_axis.default == "step_desc"

    source = (
        Path(__file__).parents[1]
        / "frontend"
        / "src"
        / "features"
        / "dashboard"
        / "My_Dashboard.jsx"
    ).read_text(encoding="utf-8")
    assert 'const [axis, setAxis] = useState("step_desc")' in source
    assert 'q.set("axis", a || "step_desc")' in source


def test_wip_dashboard_tool_returns_inline_chart(monkeypatch):
    def fake_wip_split_summary(**kwargs):
        assert kwargs["product"] == "PRODA"
        assert kwargs["bin_size"] == 30000
        return {
            "product": "PRODA",
            "bins": [{"bin": 0, "label": "0", "total": 3, "splits": {"A": 3}}],
            "split_values": ["A"],
            "unassigned_label": "(unassigned)",
            "total_wafers": 3,
            "matched_wafers": 3,
            "axis": "step_id",
            "bin_size": 30000,
            "split_col": "KNOB_TEST",
            "generated_at": "2026-08-10T00:00:00",
        }

    monkeypatch.setattr(dashboard, "wip_split_summary", fake_wip_split_summary)
    tool = llm._handle_flowi_dashboard_wip_view(
        "PRODA 대시보드 보여줘",
        "PRODA",
        {"username": "tester", "role": "user"},
    )

    assert tool["handled"] is True
    assert tool["type"] == "chart"
    assert tool["chart_result"]["kind"] == "dashboard_wip_split"
    assert tool["chart_result"]["total_wafers"] == 3
    assert tool["navigate"]["auto"] is False


def test_non_admin_flowi_response_keeps_inline_chart_and_navigation():
    result = {
        "answer": "chart",
        "tool": {
            "type": "chart",
            "chart_result": {"kind": "dashboard_wip_split", "bins": []},
            "navigate": {"tab": "dashboard", "auto": False},
        },
    }

    public = llm._flowi_home_response_for_role(result, {"role": "user"})

    assert public["tool"]["chart_result"]["kind"] == "dashboard_wip_split"
    assert public["tool"]["navigate"]["tab"] == "dashboard"


def test_flowi_deadline_defaults_to_nearly_ten_minutes(monkeypatch):
    monkeypatch.delenv("FLOW_FLOWI_CHAT_DEADLINE_S", raising=False)
    assert llm._flowi_chat_deadline_s() == 570.0
