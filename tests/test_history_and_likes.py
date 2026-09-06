import json
import pytest
from pathlib import Path
from backend.routers import reformatize
from backend.routers import filebrowser


def test_reformatize_expression_format_and_hash():
    filters = {
        "lot_filter": "LOT123*",
        "step_filter": "1000",
        "days": 7,
    }
    expr = reformatize._format_reformatize_expression(
        "PRODA",
        ["VTH_P", "VTH_N"],
        filters,
        "median",
    )
    assert "PRODUCT = PRODA" in expr
    assert "ITEMS = VTH_N, VTH_P" in expr
    assert "PRODUCT = PRODA" in expr
    assert "ITEMS = VTH_N, VTH_P" in expr
    assert "RECENT_DAYS = 7" in expr
    assert "ROOT_LOTS = LOT123*" in expr
    assert "TABLE = et" in expr
    assert "REFORMATTER = true" in expr

    h1 = reformatize._reformatize_expression_hash("PRODA", ["VTH_P", "VTH_N"], filters, "median")
    h2 = reformatize._reformatize_expression_hash("PRODA", ["VTH_N", "VTH_P"], filters, "median")
    assert h1 == h2


def test_reformatize_history_lifecycle(tmp_path, monkeypatch):
    h_file = tmp_path / "reformatize_history.jsonl"
    p_file = tmp_path / "reformatize_pins.json"
    l_file = tmp_path / "reformatize_likes.json"

    monkeypatch.setattr(reformatize, "HISTORY_FILE", h_file)
    monkeypatch.setattr(reformatize, "PINS_FILE", p_file)
    monkeypatch.setattr(reformatize, "LIKES_FILE", l_file)

    # 1. Save entry
    entry1 = reformatize._save_or_increment_reformatize_history(
        "PRODA",
        ["VTH_N"],
        {"days": 3},
        "raw",
        "engineer_a",
    )
    assert entry1["history_id"].startswith("RH-")
    assert entry1["reuse_count"] == 1

    # 2. Increment reuse
    entry1_reuse = reformatize._save_or_increment_reformatize_history(
        "PRODA",
        ["VTH_N"],
        {"days": 3},
        "raw",
        "engineer_b",
    )
    assert entry1_reuse["history_id"] == entry1["history_id"]
    assert entry1_reuse["reuse_count"] == 2

    # 3. Add second entry
    entry2 = reformatize._save_or_increment_reformatize_history(
        "PRODB",
        ["LEAK_P"],
        {"days": 10},
        "median",
        "engineer_c",
    )
    assert entry2["history_id"] != entry1["history_id"]
    assert entry2["reuse_count"] == 1

    # Check visible history sorting: sorted by last activity descending (entry2 is most recent > entry1), and seq=1 for first search
    entries = reformatize._reformatize_visible_history_entries()
    assert entries[0]["history_id"] == entry2["history_id"]
    assert entries[0]["seq"] == 2
    assert entries[1]["history_id"] == entry1["history_id"]
    assert entries[1]["seq"] == 1

    # 4. Pin entry2
    reformatize._set_reformatize_pin(entry2["history_id"], pinned=True, username="admin")
    entries_pinned = reformatize._reformatize_visible_history_entries()
    # Pinned entry comes first regardless of reuse count
    assert entries_pinned[0]["history_id"] == entry2["history_id"]
    assert entries_pinned[0]["pinned"] is True

    exact = reformatize.reformatize_history(
        object(), limit=1, q="", history_id=entry1["history_id"],
        user={"username": "engineer"},
    )
    assert [row["history_id"] for row in exact["history"]] == [entry1["history_id"]]

    # 5. Reuse with specific history_id
    entry1_reuse_by_id = reformatize._save_or_increment_reformatize_history(
        "PRODA",
        ["VTH_N"],
        {"days": 3},
        "raw",
        "engineer_d",
        history_id=entry1["history_id"],
    )
    assert entry1_reuse_by_id["history_id"] == entry1["history_id"]
    assert entry1_reuse_by_id["reuse_count"] == 3
    assert entry1_reuse_by_id["status"] == "success"

    # 6. Record failed query
    entry_fail = reformatize._save_or_increment_reformatize_history(
        "PRODA",
        ["VTH_N"],
        {"lot_filter": "INVALID_LOT_XYZ"},
        "raw",
        "engineer_e",
        status="error",
        error_message="조건에 맞는 ET 데이터가 없습니다",
    )
    assert entry_fail["status"] == "error"
    assert entry_fail["error_message"] == "조건에 맞는 ET 데이터가 없습니다"
    all_entries = reformatize._reformatize_visible_history_entries()
    found_fail = next(e for e in all_entries if e["history_id"] == entry_fail["history_id"])
    assert found_fail["status"] == "error"
    assert found_fail["error_message"] == "조건에 맞는 ET 데이터가 없습니다"



def test_chart_builder_likes(tmp_path, monkeypatch):
    l_file = tmp_path / "chart_builder_likes.json"
    monkeypatch.setattr(filebrowser, "_chart_builder_likes_path", lambda: l_file)
    monkeypatch.setattr(filebrowser, "_chart_builder_history_entries", lambda: [
        {"history_id": "CB-test1", "name": "Test 1"},
        {"history_id": "CB-test2", "name": "Test 2"},
    ])

    # Like CB-test1
    res = filebrowser._toggle_chart_builder_like("CB-test1", username="user1", liked=True)
    assert res["liked"] is True
    assert res["likes_count"] == 1

    # Like CB-test1 with user2
    res2 = filebrowser._toggle_chart_builder_like("CB-test1", username="user2", liked=True)
    assert res2["liked"] is True
    assert res2["likes_count"] == 2

    # Unlike user1
    res3 = filebrowser._toggle_chart_builder_like("CB-test1", username="user1", liked=False)
    assert res3["liked"] is False
    assert res3["likes_count"] == 1
