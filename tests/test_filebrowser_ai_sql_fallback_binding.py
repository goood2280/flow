"""FileBrowser AI SQL rule fallback (LLM 없음): 값 하나는 한 컬럼에만 묶인다.

item_id 절이 가져간 값(VTH)을 wafer_id/lot_id 가 다시 쓰면 0행이 나온다.
'웨이퍼별'/'랏별'은 집계 group-by 힌트이지 필터가 아니다.
'A1000 랏'처럼 랏 토큰이 별칭 앞에 오면 root_lot_id 로 묶는다.
"""
import pytest

from routers import filebrowser as fb

COLS = [
    "root_lot_id", "lot_id", "wafer_id", "step_id", "step_seq", "flat",
    "tkin_time", "tkout_time", "item_id", "value",
]


@pytest.mark.parametrize("prompt, expected", [
    ("웨이퍼별 VTH 평균 보여줘", "item_id = 'VTH'"),
    ("VTH 항목의 웨이퍼별 평균", "item_id = 'VTH'"),
    ("랏별 VTH 평균", "item_id = 'VTH'"),
    ("A1000 랏 VTH 값 큰 순서로", "root_lot_id = 'A1000' AND item_id = 'VTH'"),
    ("A1000 랏 VTH 보여줘", "root_lot_id = 'A1000' AND item_id = 'VTH'"),
    ("A1000 에서 VTH 만 보여줘", "root_lot_id = 'A1000' AND item_id = 'VTH'"),
])
def test_item_value_is_not_rebound_to_wafer_or_lot(prompt, expected):
    assert fb._fallback_ai_sql(prompt, COLS) == expected


def test_explicit_lot_value_after_alias_still_binds_lot_id():
    sql = fb._fallback_ai_sql("랏 A1000.1 VTH 값", COLS)
    assert set(sql.split(" AND ")) == {"lot_id = 'A1000.1'", "item_id = 'VTH'"}


def test_wafer_filter_with_number_is_kept_when_not_group_by():
    sql = fb._fallback_ai_sql("웨이퍼 3 VTH 값", COLS)
    assert "wafer_id = 3" in sql and "item_id = 'VTH'" in sql


def test_group_by_only_mention_detection():
    assert fb._fallback_group_by_only_mention("웨이퍼별 VTH 평균", "wafer_id")
    assert fb._fallback_group_by_only_mention("VTH avg by wafer", "wafer_id")
    assert not fb._fallback_group_by_only_mention("웨이퍼 3 VTH", "wafer_id")
    assert not fb._fallback_group_by_only_mention("VTH 평균", "wafer_id")


@pytest.fixture
def no_llm(monkeypatch):
    from core import llm_adapter
    monkeypatch.setattr(llm_adapter, "is_available", lambda: False)


def _draft(prompt):
    return fb._draft_filebrowser_ai_sql(natural_language=prompt, columns=COLS)


def test_draft_wafer_group_avg_without_llm(no_llm):
    out = _draft("웨이퍼별 VTH 평균 보여줘")
    assert out["ok"] and out["fallback"]
    assert out["sql"] == "item_id = 'VTH'"
    agg = out["aggregate"]
    assert (agg["function"], agg["column"], agg["group_by"]) == ("avg", "value", ["wafer_id"])


def test_draft_lot_token_and_value_sort_without_llm(no_llm):
    out = _draft("A1000 랏 VTH 값 큰 순서로")
    assert out["ok"] and out["fallback"]
    assert out["sql"] == "root_lot_id = 'A1000' AND item_id = 'VTH'"
    assert (out["sort"]["column"], out["sort"]["direction"]) == ("value", "desc")
