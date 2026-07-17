"""vehicle_reformatter — auto report 형식 CSV 파싱 + REAL/ADDP 계산 검증."""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

import core.vehicle_reformatter as vr  # noqa: E402
from core.vehicle_reformatter import (  # noqa: E402
    _f_ma_ovl_index, _f_ma_window, _f_ma_window_min,
    apply_addp_rows, find_vehicle_csv, load_vehicle_table, reformatize,
)

CSV_TEXT = """num,CATEGORY,ITEMID,ALIAS,ABSOLUTE,SCALE FACTOR,ADDP FORM,UNIT,SPECLOW,SPECHIGH,TARGET,REPORT ORDER,REPORT LOG SCALE,REPORT DIRECTION,CAT1,CAT2,PPT_ONLY,Comment
1,REAL,VTH,VTH_IDX,True,2.0,,V,0.1,0.4,0.25,1,False,BOTH,Tr,VTH,,
2,REAL,IDSAT,IDSAT_IDX,False,1.0,,uA,,,,2,False,BOTH,Drive,IDSAT,,
3,ADDP,,SUM_IDX,False,1.0,{VTH_IDX} + {IDSAT_IDX},V,,,,3,False,BOTH,Tr,VTH,,
4,ADDP,,CHAIN_IDX,False,1.0,"rmax({SUM_IDX}, {VTH_IDX})",V,,,,4,False,BOTH,Tr,VTH,,
5,REAL,NOPE,MISSING_IDX,False,1.0,,V,,,,5,False,BOTH,Tr,VTH,,
"""


@pytest.fixture()
def vehicle_csv(tmp_path: Path) -> Path:
    fp = tmp_path / "TESTPROD_reformatter.csv"
    fp.write_text(CSV_TEXT, encoding="utf-8")
    return fp


def _long_df() -> pl.DataFrame:
    # shot (1,1): VTH=-0.2, IDSAT=3 / shot (2,1): VTH=0.1, IDSAT=5
    return pl.DataFrame({
        "root_lot_id": ["L1"] * 4,
        "wafer_id": ["1"] * 4,
        "step_id": ["S1"] * 4,
        "shot_x": ["1", "1", "2", "2"],
        "shot_y": ["1", "1", "1", "1"],
        "item_id": ["VTH", "IDSAT", "VTH", "IDSAT"],
        "value": ["-0.2", "3", "0.1", "5"],   # read_source 는 전 컬럼 str
    })


def test_load_vehicle_table_parses_real_and_addp(vehicle_csv: Path):
    table = load_vehicle_table(vehicle_csv)
    assert [r["alias"] for r in table] == [
        "VTH_IDX", "IDSAT_IDX", "SUM_IDX", "CHAIN_IDX", "MISSING_IDX"]
    vth = table[0]
    assert vth["category"] == "real" and vth["absolute"] is True and vth["scale"] == 2.0
    assert vth["speclow"] == 0.1 and vth["spechigh"] == 0.4 and vth["target"] == 0.25
    addp = table[2]
    assert addp["category"] == "addp" and addp["addp_form"] == "{VTH_IDX} + {IDSAT_IDX}"


def test_find_vehicle_csv_exact_and_partial(vehicle_csv: Path):
    base = vehicle_csv.parent
    assert find_vehicle_csv(base, "TESTPROD") == vehicle_csv
    assert find_vehicle_csv(base, "testprod") == vehicle_csv     # 대소문자 무시
    assert find_vehicle_csv(base, "TEST") == vehicle_csv         # 부분 일치
    assert find_vehicle_csv(base, "OTHER") is None


def test_reformatize_real_addp_and_missing(vehicle_csv: Path):
    table = load_vehicle_table(vehicle_csv)
    wide, out_cols, errors = reformatize(_long_df(), table)

    assert wide.height == 2  # shot 2개
    # full wide 는 raw item 컬럼(VTH 등)도 유지, out_cols 는 key+alias 만
    assert "VTH" in wide.columns and "VTH" not in out_cols
    assert out_cols[-1] == "MISSING_IDX"
    row1 = wide.filter(pl.col("shot_x") == "1").to_dicts()[0]
    row2 = wide.filter(pl.col("shot_x") == "2").to_dicts()[0]

    # REAL: abs(-0.2 * 2.0) = 0.4 / abs 없이 scale 1.0
    assert row1["VTH_IDX"] == pytest.approx(0.4)
    assert row1["IDSAT_IDX"] == pytest.approx(3.0)
    # ADDP: alias 참조 합
    assert row1["SUM_IDX"] == pytest.approx(3.4)
    # ADDP → ADDP 재귀 + rmax → 행단위 max
    assert row1["CHAIN_IDX"] == pytest.approx(3.4)
    assert row2["CHAIN_IDX"] == pytest.approx(0.1 * 2.0 + 5.0)

    # 데이터에 없는 ITEMID → null 컬럼 + 에러 메시지
    assert row1["MISSING_IDX"] is None
    assert any("MISSING_IDX" in e for e in errors)
    # 정상 규칙은 에러 없음
    assert not any("SUM_IDX" in e for e in errors)


def test_reformatize_unresolvable_addp_reports_error(vehicle_csv: Path):
    table = load_vehicle_table(vehicle_csv)
    table.append({
        "num": "9", "category": "addp", "itemid": "", "alias": "BROKEN_IDX",
        "absolute": False, "scale": 1.0, "addp_form": "{NO_SUCH_COL} * 2",
        "unit": "", "speclow": None, "spechigh": None, "target": None,
        "report_order": 9.0, "cat1": "", "cat2": "",
    })
    wide, _out_cols, errors = reformatize(_long_df(), table)
    assert "BROKEN_IDX" in wide.columns
    assert wide["BROKEN_IDX"].null_count() == wide.height
    assert any("BROKEN_IDX" in e for e in errors)


def test_apply_addp_rows_test_items_and_autoreport_funcs(vehicle_csv: Path):
    """관리자 수식 테스트 경로: 기존 alias + raw item 참조, auto report 호환 함수."""
    table = load_vehicle_table(vehicle_csv)
    wide, _out_cols, _errors = reformatize(_long_df(), table)
    wide2, errors = apply_addp_rows(wide, [
        {"alias": "T_RAW", "addp_form": "{VTH} * 10"},                 # raw item 참조
        {"alias": "T_LOG", "addp_form": "LOG(10, {IDSAT_IDX})"},       # auto report LOG(base, x)
        {"alias": "T_POW", "addp_form": "power({IDSAT_IDX}, 2)"},
        {"alias": "T_CHAIN", "addp_form": "rmax({T_RAW}, {T_POW})"},   # 테스트 항목끼리 참조
        {"alias": "T_BAD", "addp_form": "__import__('os')"},           # 차단되어야 함
    ])
    row1 = wide2.filter(pl.col("shot_x") == "1").to_dicts()[0]
    assert row1["T_RAW"] == pytest.approx(-2.0)          # raw VTH=-0.2 (abs/scale 미적용)
    assert row1["T_LOG"] == pytest.approx(0.47712, abs=1e-4)  # log10(abs(3.0))
    assert row1["T_POW"] == pytest.approx(9.0)
    assert row1["T_CHAIN"] == pytest.approx(9.0)
    assert row1["T_BAD"] is None
    assert any("T_BAD" in e for e in errors)
    assert not any(a in e for a in ("T_RAW", "T_LOG", "T_POW", "T_CHAIN") for e in errors)


def test_ma_window_convex_fit():
    """볼록 2차식: log10(y) = x^2 - 1, spec=1(log 0) → margin ±1, window 2."""
    args = ([-1, 0, 1], 1.0, 0.1, 1.0, 1.0, 10)
    assert _f_ma_window(*args) == pytest.approx(2.0, abs=1e-6)
    assert _f_ma_ovl_index(*args) == pytest.approx(0.0, abs=1e-6)
    assert _f_ma_window_min(*args) == pytest.approx(1.0, abs=1e-6)


def test_ma_window_concave_uses_compliance():
    """오목 2차식(a2<0)은 auto report 예외처리대로 ±compliance."""
    # log10(y) = -(x^2) - 0.5 → 오목
    ys = [10 ** (-(x * x) - 0.5) for x in (-1, 0, 1)]
    assert _f_ma_window([-1, 0, 1], *ys, 1.0, 7) == pytest.approx(14.0, abs=1e-6)


def test_manual_functions_hook_and_rowwise_eval(tmp_path, monkeypatch, vehicle_csv: Path):
    """manual_functions.py 훅 함수 + 내장 MA_Window 를 ADDP form 에서 행 단위 호출."""
    hook = tmp_path / "manual_functions.py"
    hook.write_text(
        "def my_ratio(a, b):\n"
        "    \"\"\"a/b test manual fn\"\"\"\n"
        "    return None if not b else a / b\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vr, "_MANUAL_FILE", hook)
    monkeypatch.setitem(vr._MANUAL_CACHE, "sig", ())

    table = load_vehicle_table(vehicle_csv)
    wide, _out_cols, _errors = reformatize(_long_df(), table)
    wide2, errors = apply_addp_rows(wide, [
        {"alias": "T_MANUAL", "addp_form": "my_ratio({IDSAT_IDX}, {VTH_IDX})"},
        {"alias": "T_MIXED", "addp_form": "abs(my_ratio({VTH_IDX}, {IDSAT_IDX})) * 2"},
    ])
    assert errors == []
    row1 = wide2.filter(pl.col("shot_x") == "1").to_dicts()[0]
    # shot1: VTH_IDX=0.4, IDSAT_IDX=3.0
    assert row1["T_MANUAL"] == pytest.approx(3.0 / 0.4)
    assert row1["T_MIXED"] == pytest.approx((0.4 / 3.0) * 2)
    # help 목록에 파일 함수가 노출된다
    names = [h["name"] for h in vr.rowwise_function_help()]
    assert any(n.startswith("my_ratio") for n in names)


def test_apply_addp_rows_group_std_avg():
    """std/avg 는 wafer 단위(root_lot·wafer·tkout_time) groupby transform."""
    df = pl.DataFrame({
        "root_lot_id": ["L1"] * 4,
        "wafer_id": ["1", "1", "2", "2"],
        "tkout_time": ["t"] * 4,
        "shot_x": ["1", "2", "1", "2"],
        "V": [1.0, 3.0, 10.0, 10.0],
    })
    out, errors = apply_addp_rows(df, [
        {"alias": "V_AVG", "addp_form": "avg({V})"},
        {"alias": "V_STD", "addp_form": "std({V})"},
    ])
    assert errors == []
    w1 = out.filter(pl.col("wafer_id") == "1")
    w2 = out.filter(pl.col("wafer_id") == "2")
    assert w1["V_AVG"].to_list() == [2.0, 2.0]
    assert w2["V_AVG"].to_list() == [10.0, 10.0]
    assert w1["V_STD"][0] == pytest.approx(2.0 ** 0.5)   # 표본 std
    assert w2["V_STD"][0] == pytest.approx(0.0)
