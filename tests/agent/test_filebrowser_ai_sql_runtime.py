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
from core.flowi_units.filebrowser_ai_sql_runtime import filebrowser_ai_sql_graph  # noqa: E402
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

    graph = filebrowser_ai_sql_graph()["nodes"]
    assert [node["id"] for node in graph] == [
        "context_sample",
        "semantic_layer",
        "filter_draft",
        "column_draft",
        "merge",
        "preview_apply",
    ]
    catalog = agent.unit_ai_catalog(_Request())
    assert catalog["ok"] is True
    assert [unit["key"] for unit in catalog["units"]] == ["filebrowser_ai_sql"]


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
    assert [row["node_id"] for row in out["trace"]] == [
        "context_sample",
        "semantic_layer",
        "filter_draft",
        "column_draft",
        "merge",
        "preview_apply",
    ]
    assert len(calls) == 2
    assert "read-only WHERE" in calls[0]["system"]
    assert "display columns" in calls[1]["system"]
    assert len(calls[0]["payload"]["sample_rows"]) == 10
    assert len(calls[1]["payload"]["sample_rows"]) == 10
    assert out["filter"]["sql"] == "root_lot_id = 'A1000'"
    assert out["columns"]["selected_columns"] == ["root_lot_id", "wafer_id", "value"]
    assert out["preview"]["columns"] == ["root_lot_id", "wafer_id", "value"]
    assert out["preview"]["rows"]


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
    assert out["preview"]["rows"]
