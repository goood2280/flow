from types import SimpleNamespace

import polars as pl
import pytest

from core import data_chat, data_chat_inline as inline, llm_adapter, product_semantics


@pytest.fixture
def database(tmp_path, monkeypatch):
    rows = {
        "root_lot_id": ["AZSSS", "AZSSS", "AZZZZ"],
        "fab_lot_id": ["AZSSS.1", "AZSSS.2", "AZZZZ.1"],
        "wafer_id": [1, 2, 3],
        "INLINE_I1_avg": [10.0, 20.0, None],
        "INLINE_I2_avg": [11.0, 22.0, 33.0],
        "INLINE_I10_avg": [100.0, 200.0, 300.0],
        "FAB_ETCH_tkout_time": ["2026-09-01 10:00:00", "2026-09-02 10:00:00", None],
        "FAB_PHOTO_tkout_time": ["2026-08-01 10:00:00", None, "2026-08-03 10:00:00"],
    }
    path = tmp_path / "ML_TABLE_PRODA.parquet"
    pl.DataFrame(rows).write_parquet(path)
    monkeypatch.setattr(inline, "resolve_ml_table_file", lambda product: path if product == "PRODA" else None)
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["PRODA", "PRODB"])
    monkeypatch.setattr(data_chat, "available_product_catalog", lambda: [{"product": "PRODA", "split_table": "ML_TABLE_PRODA"}])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args, **kwargs: [])
    monkeypatch.setattr(inline.semantic_measure_catalog, "match_terms", lambda *args, **kwargs: [])
    monkeypatch.setattr(product_semantics, "PATHS", SimpleNamespace(db_root=tmp_path))
    tmp_path.joinpath("Inline_matching.csv").write_text(
        "product,step_id,item_id,item_desc\nPRODA,A100,I1,ABC CD\nPRODA,A200,I2,ABC TCD\nPRODB,B100,I10,ABC CD\n", encoding="utf-8")
    return path


def bind(monkeypatch, **extra):
    row = {"kind": "measurements", "source_type": "INLINE", "step_id": "A100", "item_id": "I1", "term": "ABC CD", "reference_id": "confirmed-id", **extra}
    monkeypatch.setattr(product_semantics, "resolve_terms", lambda *args, **kwargs: [row])


def test_semantic_direct_values_exact_fab_lot(database, monkeypatch):
    bind(monkeypatch)
    result = data_chat.execute("PRODA AZSSS.1 ABC CD Inline 보여줘", {}, None)
    assert result["ok"], result
    assert result["tool"]["table"]["rows"][0]["INLINE_I1_avg"] == 10
    assert len(result["tool"]["table"]["rows"]) == 1
    assert result["tool"]["query_scope"]["lot"] == "AZSSS.1"


def test_bare_semantic_measurement(database, monkeypatch):
    bind(monkeypatch)
    result = data_chat.execute("PRODA ABC CD 보여줘", {}, None)
    assert result["ok"]
    assert result["tool"]["action"] == "inline.values"
    assert len(result["tool"]["table"]["rows"]) == 2


def test_fuzzy_candidates_are_product_scoped_then_execute(database):
    first = data_chat.execute("PRODA AZSSS.1 ABC CDD Inline 보여줘", {}, None)
    options = first["tool"]["clarification"]["options"]
    assert first["ok"] is False
    assert all("I10" not in o["label"] for o in options)
    selected = next(o for o in options if "I1_avg" in o["label"])
    result = data_chat.execute(selected["value"], first["context"], None)
    assert result["ok"], result
    assert result["tool"]["table"]["rows"][0]["INLINE_I1_avg"] == 10
    assert "pending_inline" not in result["context"]


def test_trend_selects_time_and_filters_missing_pairs(database, monkeypatch):
    bind(monkeypatch)
    first = data_chat.execute("PRODA ABC CD Trend 보여줘", {}, None)
    assert first["tool"]["clarification"]["kind"] == "inline_time"
    result = data_chat.execute("FAB_PHOTO_tkout_time", first["context"], None)
    chart = result["tool"]["chart_result"]
    assert result["ok"]
    assert chart["chart_type"] == "scatter"
    assert len(chart["points"]) == 1
    assert chart["points"][0]["y"] == 10
    assert chart["x"] == "FAB_PHOTO_tkout_time"
    assert "pending_inline" not in result["context"]


def test_fuzzy_then_time_selection_keeps_measurement(database):
    first = data_chat.execute("PRODA ABC CDD Trend 보여줘", {}, None)
    second = data_chat.execute("1", first["context"], None)
    assert second["tool"]["clarification"]["kind"] == "inline_time"
    result = data_chat.execute("2", second["context"], None)
    assert result["ok"]
    assert result["tool"]["chart_result"]["y"] == "INLINE_I1_avg"


def test_product_prompt_restores_bare_measurement(database, monkeypatch):
    bind(monkeypatch)
    first = data_chat.execute("ABC CD 보여줘", {}, None)
    assert first["tool"]["clarification"]["kind"] == "product"
    result = data_chat.execute("PRODA", first["context"], None)
    assert result["ok"]
    assert result["tool"]["action"] == "inline.values"


def test_changed_schema_does_not_query_stale_choice(database):
    first = data_chat.execute("PRODA ABC CDD Inline 보여줘", {}, None)
    pl.read_parquet(database).drop("INLINE_I1_avg").write_parquet(database)
    result = data_chat.execute("1", first["context"], None)
    assert result["tool"]["error"] == "inline_changed"


def test_product_change_clears_old_measurement(database):
    first = data_chat.execute("PRODA ABC CDD Inline 보여줘", {}, None)
    result = data_chat.execute("PRODB ABC CD Inline 보여줘", first["context"], None)
    assert result["tool"]["error"] == "ml_table_missing"
    assert "pending_inline" not in result["context"]


def test_invalid_choice_and_cancel(database):
    first = data_chat.execute("PRODA ABC CDD Inline 보여줘", {}, None)
    invalid = data_chat.execute("99", first["context"], None)
    assert invalid["tool"]["missing"] == ["inline_measure"]
    result = data_chat.execute("취소", invalid["context"], None)
    assert "pending_inline" not in result["context"]


def test_no_fab_column_requires_scope_confirmation(database, monkeypatch):
    bind(monkeypatch)
    pl.read_parquet(database).drop("fab_lot_id").write_parquet(database)
    first = data_chat.execute("PRODA AZSSS.1 ABC CD Inline 보여줘", {}, None)
    assert first["tool"]["clarification"]["kind"] == "inline_lot_scope"
    result = data_chat.execute("1", first["context"], None)
    assert result["ok"]
    assert len(result["tool"]["table"]["rows"]) == 2
    assert result["tool"]["query_scope"]["lot"] == "AZSSS"


def test_catalog_binding_and_descriptive_column(database, monkeypatch):
    pl.read_parquet(database).rename({"INLINE_I1_avg": "INLINE_ABC_CD"}).write_parquet(database)
    monkeypatch.setattr(inline.semantic_measure_catalog, "match_terms", lambda *args, **kwargs: [
        {"product": "PRODA", "source_type": "INLINE", "term": "ABC CD", "item_id": "I1", "step_id": "A100"}])
    result = data_chat.execute("PRODA ABC CD Inline 보여줘", {}, None)
    assert result["ok"]
    assert result["tool"]["query_scope"]["column"] == "INLINE_ABC_CD"


def test_truncation_is_explicit(database, monkeypatch):
    bind(monkeypatch)
    monkeypatch.setattr(inline, "LIMIT", 1)
    first = data_chat.execute("PRODA ABC CD Trend 보여줘", {}, None)
    result = data_chat.execute("1", first["context"], None)
    assert result["tool"]["table"]["truncated"]
    assert result["tool"]["chart_result"]["points"][0]["y"] == 20


def test_empty_time_pairs(database, monkeypatch):
    bind(monkeypatch)
    pl.read_parquet(database).with_columns(pl.lit(None).cast(pl.String).alias("FAB_PHOTO_tkout_time")).write_parquet(database)
    first = data_chat.execute("PRODA ABC CD Trend 보여줘", {}, None)
    result = data_chat.execute("2", first["context"], None)
    assert result["tool"]["chart_result"]["points"] == []


def test_new_request_replaces_pending_measurement(database):
    first = data_chat.execute("PRODA ABC CDD Inline 보여줘", {}, None)
    result = data_chat.execute("PRODA ABC TCD 보여줘", first["context"], None)
    assert result["context"]["pending_inline"]["query"]["term"] == "abc tcd"


def test_trend_followup_reuses_selected_measurement(database, monkeypatch):
    bind(monkeypatch)
    first = data_chat.execute("PRODA ABC CD 보여줘", {}, None)
    second = data_chat.execute("Trend 보여줘", first["context"], None)
    assert second["tool"]["clarification"]["kind"] == "inline_time"


def test_invalid_timestamp_values_are_omitted(database, monkeypatch):
    bind(monkeypatch)
    pl.read_parquet(database).with_columns(pl.lit("not a date").alias("FAB_PHOTO_tkout_time")).write_parquet(database)
    first = data_chat.execute("PRODA ABC CD Trend 보여줘", {}, None)
    result = data_chat.execute("2", first["context"], None)
    assert result["tool"]["chart_result"]["points"] == []


def test_stale_semantic_binding_offers_mapping_fallback(database, monkeypatch):
    bind(monkeypatch, item_id="GONE", term="OLD CD")
    result = data_chat.execute("PRODA ABC CD Inline 보여줘", {}, None)
    assert result["tool"]["clarification"]["kind"] == "inline_measure"
