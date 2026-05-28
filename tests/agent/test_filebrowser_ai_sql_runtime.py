from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core import auth as auth_core  # noqa: E402
from core import llm_adapter, utils  # noqa: E402
from core.flowi_units.filebrowser_ai_sql_runtime import (  # noqa: E402
    COLUMN_DRAFT_SYSTEM_PROMPT,
    FILTER_DRAFT_SYSTEM_PROMPT,
    filebrowser_ai_sql_graph,
)
from routers import agent, filebrowser  # noqa: E402


class _State:
    def __init__(self, user: dict):
        self.user = user


class _Request:
    headers = {}

    def __init__(self, username: str = "tester", role: str = "user"):
        self.state = _State({"username": username, "role": role})


class _DummyPaths:
    def __init__(self, root: Path):
        self.base_root = root
        self.db_root = root
        self.data_root = root / "flow-data"
        self.upload_dir = self.data_root / "uploads"
        self.cache_dir = self.data_root / "cache"
        self.db_cache_dir = self.data_root / "cache"
        self.log_dir = self.data_root / "logs"
        self.activity_log = self.log_dir / "activity.jsonl"
        self.download_log = self.log_dir / "downloads.jsonl"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


def _install_filebrowser_fixture(monkeypatch, tmp_path: Path) -> Path:
    paths = _DummyPaths(tmp_path)
    monkeypatch.setattr(filebrowser, "PATHS", paths)
    monkeypatch.setattr(utils, "PATHS", paths)
    monkeypatch.setattr(auth_core, "current_user", lambda _request: {"username": "tester", "role": "user"})
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "user"})

    product_dir = tmp_path / "FAB" / "PRODA"
    product_dir.mkdir(parents=True)
    rows = []
    for idx in range(12):
        rows.append({
            "root_lot_id": "A1000" if idx < 8 else "B2000",
            "wafer_id": idx + 1,
            "item_id": "IOFF" if idx % 2 == 0 else "VTH",
            "value": round(idx * 0.1, 3),
            "value_text": "bad" if idx == 9 else str(idx),
            "step_id": "S1",
        })
    fp = product_dir / "part.parquet"
    pl.DataFrame(rows).write_parquet(fp)
    return fp


def _patch_llm(monkeypatch, calls: list[dict], *, bad_filter: bool = False):
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_complete_json(ask, *, system="", timeout=0, max_retries=0, schema=None):
        payload = json.loads(ask)
        calls.append({"system": system, "payload": payload})
        if "display columns" in system:
            selected = ["missing_column", "value"] if bad_filter else ["root_lot_id", "wafer_id", "value"]
            return {"ok": True, "obj": {"selected_columns": selected, "notes": "column call"}}
        sql = "SELECT * FROM source" if bad_filter else "root_lot_id = 'A1000'"
        return {
            "ok": True,
            "obj": {
                "sql": sql,
                "resolved_columns": ["root_lot_id"],
                "resolved_values": ["A1000"],
                "notes": "filter call",
            },
        }

    monkeypatch.setattr(llm_adapter, "complete_json", fake_complete_json)


def test_filebrowser_ai_sql_graph_shape_and_catalog(monkeypatch):
    monkeypatch.setattr(agent, "current_user", lambda _request: {"username": "tester", "role": "user"})

    graph_payload = filebrowser_ai_sql_graph()
    graph = graph_payload["nodes"]
    assert [node["id"] for node in graph] == [
        "context_sample",
        "semantic_layer",
        "filter_draft",
        "column_draft",
        "merge",
        "preview_apply",
    ]
    assert "state_design" in graph_payload
    assert graph_payload["state_design"]["request"]["producer"] == "runtime"
    assert graph_payload["state_design"]["filter"]["producer"] == "filter_draft"
    assert graph_payload["state_design"]["columns_result"]["producer"] == "column_draft"
    for node in graph:
        assert node["persona"]
        assert isinstance(node["state_io"]["reads"], list)
        assert isinstance(node["state_io"]["writes"], list)
        assert node["answer_attach_rule"]
    prompts = {node["id"]: node["prompt"]["system"] for node in graph}
    assert prompts["filter_draft"] == FILTER_DRAFT_SYSTEM_PROMPT
    assert prompts["column_draft"] == COLUMN_DRAFT_SYSTEM_PROMPT
    catalog = agent.unit_ai_catalog(_Request())
    assert catalog["ok"] is True
    assert [unit["key"] for unit in catalog["units"]] == [
        "filebrowser_ai_sql",
        "inform_registration",
        "change_management",
        "dashboard_agent",
        "home_sql_join_dashboard",
    ]


def test_filebrowser_ai_sql_runtime_separates_filter_and_column_llm(monkeypatch, tmp_path):
    _install_filebrowser_fixture(monkeypatch, tmp_path)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)

    out = agent.filebrowser_ai_sql_runtime_run(
        agent.FileBrowserAiSqlRuntimeRunReq(
            natural_language="A1000 value만 보여줘",
            scope="db_product",
            root="FAB",
            product="PRODA",
        ),
        _Request(),
    )

    assert out["ok"] is True
    trace_ids = [row["node_id"] for row in out["trace"]]
    assert trace_ids[:2] == ["context_sample", "semantic_layer"]
    assert set(trace_ids[2:4]) == {"filter_draft", "column_draft"}
    assert trace_ids[4:] == ["merge", "preview_apply"]
    assert len(calls) == 2
    systems = [call["system"] for call in calls]
    assert FILTER_DRAFT_SYSTEM_PROMPT in systems
    assert COLUMN_DRAFT_SYSTEM_PROMPT in systems
    assert any("read-only WHERE" in s for s in systems)
    assert any("display columns" in s for s in systems)
    graph_prompts = {node["id"]: node["prompt"]["system"] for node in out["graph"]["nodes"]}
    assert graph_prompts["filter_draft"] == FILTER_DRAFT_SYSTEM_PROMPT
    assert graph_prompts["column_draft"] == COLUMN_DRAFT_SYSTEM_PROMPT
    assert all(call["payload"]["sample_rows"] == [] for call in calls)
    assert all(call["payload"]["sample_profile"]["sampling_policy"]["row_dump_in_prompt"] is False for call in calls)
    assert out["filter"]["sql"] == "root_lot_id = 'A1000'"
    assert out["columns"]["selected_columns"] == ["root_lot_id", "wafer_id", "value"]
    assert out["preview"]["columns"] == ["root_lot_id", "wafer_id", "value"]
    assert out["preview"]["rows"] == []
    assert out["preview"]["rows_returned"] > 0
    assert out["preview"]["applied_sql"] == (
        "SELECT root_lot_id, wafer_id, value WHERE root_lot_id = 'A1000'"
    )
    assert out["preview"]["applied_where_sql"] == "root_lot_id = 'A1000'"
    assert out["preview"]["applied_select_cols"] == ["root_lot_id", "wafer_id", "value"]
    history_rows = [
        json.loads(line)
        for line in filebrowser._filebrowser_ai_sql_history_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert history_rows[-1]["source"] == "agent_test_prompt"
    assert history_rows[-1]["username"] == "tester"
    assert history_rows[-1]["timestamp"]
    assert history_rows[-1]["display_sql"] == out["merged"]["display_sql"]
    assert history_rows[-1]["preview_summary"]["rows_returned"] > 0
    assert "rows" not in history_rows[-1]["preview_summary"]
    assert history_rows[-1]["trace_summary"][0]["node_id"] == "context_sample"


def test_filebrowser_ai_sql_runtime_applies_cast_filter(monkeypatch, tmp_path):
    _install_filebrowser_fixture(monkeypatch, tmp_path)
    calls: list[dict] = []
    monkeypatch.setattr(llm_adapter, "is_available", lambda: True)

    def fake_complete_json(ask, *, system="", timeout=0, max_retries=0, schema=None):
        payload = json.loads(ask)
        calls.append({"system": system, "payload": payload})
        if "display columns" in system:
            return {"ok": True, "obj": {"selected_columns": ["root_lot_id", "value_text"]}}
        return {
            "ok": True,
            "obj": {
                "sql": "CAST(value_text AS DOUBLE) >= 10",
                "resolved_columns": ["value_text"],
                "resolved_values": ["10"],
            },
        }

    monkeypatch.setattr(llm_adapter, "complete_json", fake_complete_json)

    out = agent.filebrowser_ai_sql_runtime_run(
        agent.FileBrowserAiSqlRuntimeRunReq(
            natural_language="문자열 value_text를 숫자로 봐서 10 이상만",
            scope="db_product",
            root="FAB",
            product="PRODA",
        ),
        _Request(),
    )

    assert out["ok"] is True
    assert out["filter"]["sql"] == "TRY_CAST(value_text AS DOUBLE) >= 10"
    assert out["merged"]["where_sql"] == "TRY_CAST(value_text AS DOUBLE) >= 10"
    assert out["preview"]["applied_where_sql"] == "TRY_CAST(value_text AS DOUBLE) >= 10"
    assert out["preview"]["columns"] == ["root_lot_id", "value_text"]
    assert out["preview"]["rows_returned"] > 0
    filter_call = next(call for call in calls if "read-only WHERE" in call["system"])
    assert "CAST(column AS DOUBLE" in filter_call["system"]
    assert filter_call["payload"]["supported_where_casts"]["examples"][0] == "CAST(value AS DOUBLE) >= 10"


def test_filebrowser_ai_sql_runtime_warns_on_invalid_sql_and_unknown_columns(monkeypatch, tmp_path):
    _install_filebrowser_fixture(monkeypatch, tmp_path)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls, bad_filter=True)

    out = agent.filebrowser_ai_sql_runtime_run(
        agent.FileBrowserAiSqlRuntimeRunReq(
            natural_language="전체 중 value 컬럼만",
            scope="db_product",
            root="FAB",
            product="PRODA",
        ),
        _Request(),
    )

    filter_warnings = " / ".join(out["filter"]["warnings"])
    column_warnings = " / ".join(out["columns"]["warnings"])
    assert "filter_draft SQL rejected" in filter_warnings
    assert out["filter"]["sql"] == ""
    assert "unknown column removed: missing_column" in column_warnings
    assert out["columns"]["selected_columns"] == ["value"]
    assert out["preview"]["columns"] == ["value"]


def test_filebrowser_ai_sql_runtime_preview_is_read_only(monkeypatch, tmp_path):
    source = _install_filebrowser_fixture(monkeypatch, tmp_path)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)
    before = source.read_bytes()

    out = agent.filebrowser_ai_sql_runtime_run(
        agent.FileBrowserAiSqlRuntimeRunReq(
            natural_language="A1000 value만 보여줘",
            scope="db_product",
            root="FAB",
            product="PRODA",
        ),
        _Request(),
    )

    assert out["ok"] is True
    assert source.read_bytes() == before
    assert out["preview"]["rows"] == []
    assert out["preview"]["rows_returned"] > 0


def test_parse_ai_sql_select_prefix_handles_common_forms():
    cols = ["root_lot_id", "wafer_id", "IOFF", "value"]
    parser = filebrowser._parse_ai_sql_select_prefix
    assert parser("SELECT IOFF, wafer_id WHERE root_lot_id = 'A1000'", cols) == (
        "root_lot_id = 'A1000'",
        ["IOFF", "wafer_id"],
    )
    assert parser("SELECT ioff, wafer_id", cols) == ("", ["IOFF", "wafer_id"])
    assert parser("SELECT * WHERE root_lot_id = 'A1000'", cols) == ("root_lot_id = 'A1000'", [])
    assert parser("root_lot_id = 'A1000'", cols) == ("root_lot_id = 'A1000'", [])
    assert parser("SELECT count(*) WHERE x", cols) == ("SELECT count(*) WHERE x", [])
    assert parser("SELECT IOFF, missing WHERE x", cols) == ("SELECT IOFF, missing WHERE x", [])
    assert parser("", cols) == ("", [])


def test_filebrowser_ai_sql_runtime_merge_emits_select_form(monkeypatch, tmp_path):
    _install_filebrowser_fixture(monkeypatch, tmp_path)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)

    out = agent.filebrowser_ai_sql_runtime_run(
        agent.FileBrowserAiSqlRuntimeRunReq(
            natural_language="A1000 value만 보여줘",
            scope="db_product",
            root="FAB",
            product="PRODA",
        ),
        _Request(),
    )

    assert out["ok"] is True
    assert out["merged"]["where_sql"] == "root_lot_id = 'A1000'"
    assert out["merged"]["selected_columns"] == ["root_lot_id", "wafer_id", "value"]
    assert out["merged"]["sql"] == (
        "SELECT root_lot_id, wafer_id, value WHERE root_lot_id = 'A1000'"
    )
    assert out["merged"]["display_sql"] == out["merged"]["sql"]
    assert out["preview"]["display_sql"] == out["merged"]["sql"]
    assert out["preview"]["columns"] == ["root_lot_id", "wafer_id", "value"]


def test_filebrowser_ai_sql_runtime_display_sql_includes_sort_intent(monkeypatch, tmp_path):
    _install_filebrowser_fixture(monkeypatch, tmp_path)
    calls: list[dict] = []
    _patch_llm(monkeypatch, calls)

    out = agent.filebrowser_ai_sql_runtime_run(
        agent.FileBrowserAiSqlRuntimeRunReq(
            natural_language="A1000 value 큰순서",
            scope="db_product",
            root="FAB",
            product="PRODA",
        ),
        _Request(),
    )

    assert out["ok"] is True
    assert out["merged"]["sort"] == {"column": "value", "direction": "desc", "nulls": "last"}
    assert out["merged"]["display_sql"].endswith("ORDER BY value DESC")
    assert out["preview"]["rows"] == []
    assert out["preview"]["rows_returned"] > 0
