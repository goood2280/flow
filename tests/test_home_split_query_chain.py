import pytest
import polars as pl

from core import data_chat, split_lead_tracker as source, product_semantics, llm_adapter, flowi_turn
from routers import splittable


@pytest.fixture
def split_data(monkeypatch, tmp_path):
    path = tmp_path / "ML_TABLE_PRODB0.parquet"
    pl.DataFrame({"ROOT_LOT_ID": ["AZAAA", "AZAAA", "AZBBB"], "LOT_ID": ["AZAAA.1", "AZAAA.2", "AZBBB.1"],
        "WAFER_ID": ["01", "02", "03"], "KNOB_eSD A": ["AAA", "AAA", "AAAX"], "KNOB_eSD B": ["BBB", "CCC", "DDD"]}).write_parquet(path)
    monkeypatch.setattr(source, "find_ml_table_path", lambda p: path if p == "PRODB0" else None)
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODB0", "PRODC"])
    monkeypatch.setattr(data_chat, "split_table_product", lambda p: "ML_TABLE_" + p)
    monkeypatch.setattr(splittable, "list_products", lambda: {"products": [{"name": "ML_TABLE_PRODB0"}, {"name": "ML_TABLE_PRODC"}]})
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *a: [])
    monkeypatch.setattr(source, "describe_step", lambda sid, p: {"step_desc": {"CS100": "10.0 PHOTO", "CS200": "20.0 PHOTO", "CS900": "90.0 PHOTO"}[sid]})
    wip = [{"product": "PRODB0", "root_lot_id": "AZAAA", "lot_id": "AZAAA.1", "wafer_id": "1", "step_id": "CS100"},
           {"product": "PRODB0", "root_lot_id": "AZAAA", "lot_id": "AZAAA.2", "wafer_id": "2", "step_id": "CS200"},
           {"product": "PRODB0", "root_lot_id": "AZBBB", "lot_id": "AZBBB.1", "wafer_id": "3", "step_id": "CS900"}]
    monkeypatch.setattr(source, "read_lot_progress_cache", lambda **k: {"items": wip})
    return path, wip


def test_exact_three_question_chain(split_data):
    first = data_chat.execute("PRODB0 eSD가 들어간 split 보여줘", {}, None)
    assert first["tool"]["action"] == "splittable.columns"
    assert [row["Split 항목"] for row in first["tool"]["table"]["rows"]] == ["KNOB_eSD A", "KNOB_eSD B"]
    second = data_chat.execute("eSD A Split 종류 뭐가 있어?", first["context"], None)
    assert second["tool"]["action"] == "splittable.values"
    assert {row["Split 조건"] for row in second["tool"]["table"]["rows"]} == {"AAA", "AAAX"}
    third = data_chat.execute("eSD Split AAA 조건 가장 선행랏이 뭐야?", second["context"], None)
    assert third["ok"], third
    assert third["tool"]["context"]["split_col"] == "KNOB_eSD A"
    assert third["tool"]["lead_lot"]["lot_id"] == "AZAAA.2"
    assert third["tool"]["lead_lot"]["wafers"] == ["2"]
    assert len(third["tool"]["table"]["rows"]) == 2


def test_ambiguous_column_then_value_choices_are_pending(split_data):
    first = data_chat.execute("PRODB0 eSD 선행랏이 뭐야?", {}, None)
    assert first["tool"]["clarification"]["kind"] == "split_column"
    assert flowi_turn._status(first) == "needs_input"
    second = data_chat.execute("KNOB_eSD A", first["context"], None)
    assert second["tool"]["clarification"]["kind"] == "split_value"
    assert flowi_turn._status(second) == "needs_input"
    third = data_chat.execute("AAA", second["context"], None)
    assert third["tool"]["lead_lot"]["lot_id"] == "AZAAA.2"
    assert "pending_split_choice" not in third["context"]
    assert flowi_turn._status(third) == "completed"


def test_exact_value_never_falls_back_to_substring(split_data):
    assert not source.find_split_leading_lot("PRODB0", "KNOB_eSD A", "AA")["ok"]


def test_unknown_step_order_does_not_guess_lead(split_data, monkeypatch):
    monkeypatch.setattr(source, "describe_step", lambda *a: {"step_desc": "unmapped"})
    out = source.find_split_leading_lot("PRODB0", "KNOB_eSD A", "AAA")
    assert not out["ok"]
    assert "공정 순서" in out["error"]


def test_product_switch_does_not_reuse_selected_column(split_data):
    first = data_chat.execute("PRODB0 eSD A Split 종류 뭐가 있어?", {}, None)
    second = data_chat.execute("PRODC eSD Split AAA 선행랏", first["context"], None)
    assert not second["ok"]
    assert "split_query" not in second["context"]
