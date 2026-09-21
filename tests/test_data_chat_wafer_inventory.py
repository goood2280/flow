"""Product choice -> original membership query -> grounded wafer IDs/counts."""
import pytest

from core import data_chat, data_chat_features, llm_adapter, lot_progress_cache
from core.latest_lot_cache_format import FORMAT_COLUMN, FORMAT_VERSION
from routers import lot_progress


@pytest.fixture
def planner(monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA", "REAL_BETA"])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    calls = []
    def execute(action, params, request):
        calls.append((action, params))
        return {"feature": "lot_progress", "message": "실제 조회 결과", "table": {"rows": []}}
    monkeypatch.setattr(data_chat_features, "execute_feature", execute)
    return calls


@pytest.mark.parametrize("question", ["AZAAA.1 안에 wafer 뭐 있어", "AZAAA.1 몇번 몇번 장있어", "AZAAA.1 웨이퍼 번호 알려줘"])
def test_lot_membership_pauses_then_resumes_without_sibling_lots(planner, question):
    first = data_chat.execute(question, {}, None)
    assert first["tool"]["missing"] == ["product"]
    assert first["tool"]["clarification"]["options"] == [
        {"label": name, "value": name} for name in ["REAL_ALPHA", "REAL_BETA"]]
    assert planner == []
    second = data_chat.execute("REAL_BETA", first["context"], None)
    assert planner == [("lot_progress.wafers", {"product": "REAL_BETA", "lot_id": "AZAAA.1"})]
    assert "pending_product_prompt" not in second["context"]


def test_registered_product_shaped_like_lot_is_product(planner, monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["AZAAA.1", "REAL_BETA"])
    data_chat.execute("AZAAA.1로 분류되어있는 wafer가 뭐가 있어", {}, None)
    assert planner == [("lot_progress.wafers", {"product": "AZAAA.1"})]


def test_explicit_product_inventory_clears_previous_lot(planner):
    previous = {"product": "REAL_ALPHA", "confirmed_product": "REAL_ALPHA", "lot_id": "OLD.1",
                "root_lot_id": "OLD", "params": {"lot_id": "OLD.1", "wafer_id": "7", "limit": 1}}
    out = data_chat.execute("REAL_ALPHA wafer 목록 보여줘", previous, None)
    assert planner == [("lot_progress.wafers", {"product": "REAL_ALPHA"})]
    assert not out["context"].get("lot_id")
    assert not out["context"].get("root_lot_id")


def test_choices_are_capped_and_other_product_can_resume(planner, monkeypatch):
    products = [f"REAL_{i}" for i in range(8)]
    monkeypatch.setattr(data_chat, "available_product_names", lambda: products)
    first = data_chat.execute("wafer 몇 장 있어", {}, None)
    clarification = first["tool"]["clarification"]
    assert len(clarification["options"]) == 5
    assert clarification["allow_other"] is True
    data_chat.execute(products[-1], first["context"], None)
    assert planner == [("lot_progress.wafers", {"product": products[-1]})]


def test_invalid_other_product_keeps_original_membership_question(planner):
    first = data_chat.execute("AZAAA.1 wafer 뭐 있어", {}, None)
    retry = data_chat.execute("제품 UNKNOWN", first["context"], None)
    assert retry["context"]["pending_product_prompt"] == "AZAAA.1 wafer 뭐 있어"
    data_chat.execute("REAL_ALPHA", retry["context"], None)
    assert planner == [("lot_progress.wafers", {"product": "REAL_ALPHA", "lot_id": "AZAAA.1"})]


@pytest.mark.parametrize("question", ["REAL_ALPHA wafer map 보여줘", "REAL_ALPHA 수율 웨이퍼 목록", "REAL_ALPHA 스플릿 wafer 1~5 변경", "REAL_ALPHA wafer 어디 있어"])
def test_inventory_does_not_capture_other_features(question):
    assert not data_chat._is_wafer_inventory(question)


@pytest.fixture
def wafer_cache(monkeypatch, tmp_path):
    import polars as pl
    path = tmp_path / "current.parquet"
    rows = [
        {"product": "REAL_ALPHA", "root_lot_id": "AZAAA", "lot_id": "AZAAA.1", "wafer_id": "01"},
        {"product": "ML_TABLE_REAL_ALPHA", "root_lot_id": "AZAAA", "lot_id": "AZAAA.1", "wafer_id": "1"},
        {"product": "REAL_ALPHA", "root_lot_id": "AZAAA", "lot_id": "AZAAA.1", "wafer_id": "03"},
        {"product": "REAL_ALPHA", "root_lot_id": "AZAAA", "lot_id": "AZAAA.2", "wafer_id": "02"},
        {"product": "REAL_ALPHA", "root_lot_id": "BZBBB", "lot_id": "BZBBB.1", "wafer_id": "01"},
        {"product": "REAL_BETA", "root_lot_id": "AZAAA", "lot_id": "AZAAA.1", "wafer_id": "04"},
    ]
    pl.DataFrame([{**row, FORMAT_COLUMN: FORMAT_VERSION} for row in rows]).write_parquet(path)
    monkeypatch.setattr(lot_progress_cache, "filebrowser_cache_parquet_file", lambda: path)
    return path


def test_inventory_counts_unique_root_wafer_before_limiting(wafer_cache):
    all_rows = lot_progress_cache.canonical_wafer_inventory(product="REAL_ALPHA", limit=2)
    assert all_rows["total"] == 4
    assert len(all_rows["items"]) == 2
    assert all_rows["truncated"] is True
    lot = lot_progress_cache.canonical_wafer_inventory(product="REAL_ALPHA", lot_id="AZAAA.1")
    assert lot["total"] == 2
    assert [r["wafer_id"] for r in lot["items"]] == ["1", "3"]
    root = lot_progress_cache.canonical_wafer_inventory(product="REAL_ALPHA", root_lot_id="AZAAA")
    assert root["total"] == 3


def test_inventory_uses_authenticated_route_and_actual_numbers(wafer_cache, monkeypatch):
    request = object()
    authenticated = []
    monkeypatch.setattr(lot_progress, "current_user", lambda req: authenticated.append(req))
    tool = data_chat_features.execute_feature("lot_progress.wafers", {"product": "REAL_ALPHA", "lot_id": "AZAAA.1"}, request)
    assert authenticated == [request]
    assert tool["table"]["total"] == 2
    assert "총 2장" in tool["message"]
    assert "1, 3번" in tool["message"]
    assert "AZAAA.2" not in tool["message"]


def test_chat_choice_to_real_cache_result(wafer_cache, monkeypatch):
    monkeypatch.setattr(data_chat, "available_product_names", lambda: ["REAL_ALPHA", "REAL_BETA"])
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)
    monkeypatch.setattr(lot_progress, "current_user", lambda req: {"username": "tester"})
    first = data_chat.execute("AZAAA.1 안에 wafer가 뭐 있어", {}, object())
    result = data_chat.execute("REAL_ALPHA", first["context"], object())
    assert result["ok"] is True
    assert "총 2장" in result["reply"] and "1, 3번" in result["reply"]
    assert "Lot: AZAAA.1" in result["reply"]
    assert "Left 5" not in result["reply"]
    assert result["tool"]["execution_trace"]["provenance"] == "tool_result"


def test_inventory_unavailable_is_not_zero_wafers(wafer_cache):
    wafer_cache.unlink()
    with pytest.raises(ValueError, match="캐시가 없습니다"):
        lot_progress_cache.canonical_wafer_inventory(product="REAL_ALPHA")


def test_inventory_rejects_legacy_cache(wafer_cache):
    import polars as pl
    frame = pl.read_parquet(wafer_cache).with_columns(pl.lit(FORMAT_VERSION - 1).alias(FORMAT_COLUMN))
    frame.write_parquet(wafer_cache)
    with pytest.raises(ValueError, match="오래"):
        lot_progress_cache.canonical_wafer_inventory(product="REAL_ALPHA")
