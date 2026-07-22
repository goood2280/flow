"""ET 다운로드의 재귀 ADDP 출력 컬럼 회귀 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from core.vehicle_reformatter import reformatize, resolve_needed_items  # noqa: E402
from routers.reformatize import _resolve_output_cols  # noqa: E402


def _rule(alias: str, category: str, *, itemid: str = "", form: str = "", order: int = 1) -> dict:
    return {
        "num": str(order), "category": category, "itemid": itemid, "alias": alias,
        "absolute": False, "scale": 1.0, "addp_form": form, "unit": "",
        "speclow": None, "spechigh": None, "target": None, "report_order": float(order),
        "cat1": "", "cat2": "",
    }


def _long_df() -> pl.DataFrame:
    # log10(y) = -x² + 2x - 1 의 세 점. MA_Window의 auto-report 호환
    # 오목 예외 처리로 WINDOW/WINDOW_new가 모두 유한한 값이 된다.
    return pl.DataFrame({
        "root_lot_id": ["L1"] * 3,
        "wafer_id": ["1"] * 3,
        "step_id": ["S1"] * 3,
        "shot_x": ["1"] * 3,
        "shot_y": ["1"] * 3,
        "item_id": ["RAW_A", "RAW_B", "RAW_C"],
        "value": [0.1, 1.0, 0.1],
    })


def test_recursive_addp_keeps_window_and_real_raw_values():
    table = [
        _rule("A", "real", itemid="RAW_A", order=1),
        _rule("B", "real", itemid="RAW_B", order=2),
        _rule("C", "real", itemid="RAW_C", order=3),
        _rule("WINDOW", "addp", form="MA_Window([0,1,2], {A}, {B}, {C}, 1, 10)", order=4),
        _rule("FINAL", "addp", form="{WINDOW_new} + {A}", order=5),
    ]

    resolved, raw_ids = resolve_needed_items(table, ["FINAL"])
    assert [r["alias"] for r in resolved] == ["A", "B", "C", "WINDOW", "FINAL"]
    assert raw_ids == {"RAW_A", "RAW_B", "RAW_C"}

    wide, out_cols, errors = reformatize(_long_df(), table, selected_aliases=["FINAL"])
    assert errors == []
    assert {"WINDOW", "WINDOW_new", "FINAL"}.issubset(wide.columns)
    assert wide["FINAL"].null_count() == 0

    # 선택한 최종 ADDP, 그 기반 WINDOW, 재귀 의존 alias와 RAW ITEMID가
    # 조회와 CSV가 공통으로 사용하는 결과 열에 모두 포함되어야 한다.
    cols = _resolve_output_cols(out_cols, table, ["FINAL"], wide)
    assert {"A", "B", "C", "WINDOW", "WINDOW_new", "FINAL"}.issubset(cols)
    assert {"RAW_A", "RAW_B", "RAW_C"}.issubset(cols)


def test_all_items_selection_also_keeps_real_raw_values():
    table = [
        _rule("A", "real", itemid="RAW_A", order=1),
        _rule("TOTAL", "addp", form="{A} * 2", order=2),
    ]
    wide, out_cols, errors = reformatize(_long_df().filter(pl.col("item_id") == "RAW_A"), table)
    assert errors == []

    # items=[]은 전체 선택이며, raw ITEMID를 생략해서는 안 된다.
    cols = _resolve_output_cols(out_cols, table, [], wide)
    assert {"A", "TOTAL", "RAW_A"}.issubset(cols)
