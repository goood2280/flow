"""ET/INLINE 차트 집계(agg) 확장 — median/avg/p90/p10/max/shot 파서·expr·SQL 회귀."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "backend"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import polars as pl  # noqa: E402
from routers import llm  # noqa: E402


def test_agg_parser_tokens():
    f = llm._flowi_chart_agg_from_prompt
    assert f("shot 으로 그려줘") == "shot"
    assert f("샷으로 보여줘") == "shot"
    assert f("전체 측정 point 다 찍어") == "shot"
    assert f("P90 으로") == "p90"
    assert f("p10 으로 그려") == "p10"
    assert f("최대값으로") == "max"
    assert f("평균으로") == "avg"
    assert f("중앙값 기준") == "median"
    assert f("그냥 그려줘") == "median"           # 기본값
    assert f("그냥 그려줘", default="") == ""      # scatter 미명시 감지


def test_agg_polars_expr_values():
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 100]
    df = pl.DataFrame({"g": ["w"] * 10, "metric_value": [float(v) for v in vals]})

    def agg(name):
        return df.lazy().group_by("g").agg(
            llm._flowi_agg_polars_expr(name, "metric_value").alias("y")
        ).collect()["y"][0]

    assert agg("median") == 5.5
    assert agg("avg") == 14.5
    assert agg("max") == 100.0
    assert round(agg("p90"), 1) == 18.1
    assert round(agg("p10"), 1) == 1.9
    # 미지 이름은 median 으로 안전 폴백
    assert agg("bogus") == 5.5


def test_agg_duck_sql_fragments():
    assert llm._flowi_agg_duck_sql("median") == "MEDIAN(_metric_value)"
    assert llm._flowi_agg_duck_sql("avg") == "AVG(_metric_value)"
    assert llm._flowi_agg_duck_sql("max") == "MAX(_metric_value)"
    assert llm._flowi_agg_duck_sql("p90") == "QUANTILE_CONT(_metric_value, 0.9)"
    assert llm._flowi_agg_duck_sql("p10") == "QUANTILE_CONT(_metric_value, 0.1)"
    assert llm._flowi_agg_duck_sql("shot") == "MEDIAN(_metric_value)"  # shot 은 grain 분기, agg 아님


def test_agg_label():
    assert llm._flowi_agg_label("p90") == "P90"
    assert llm._flowi_agg_label("shot") == "shot(all)"
    assert llm._flowi_agg_label("median") == "median"


def test_scatter_source_pair_and_defaults():
    # 소스 우선순위 [INLINE, ET, VM] — 낮은 인덱스가 x 슬롯.
    assert llm._flowi_scatter_source_pair("INLINE 3.0 VTN 이랑 VM 4.0 GATE_OX scatter") == ("INLINE", "VM")
    assert llm._flowi_scatter_source_pair("ET VTH 이랑 VM GATE_OX corr") == ("ET", "VM")
    assert llm._flowi_scatter_source_pair("INLINE CD 랑 ET IOFF scatter") == ("INLINE", "ET")
    # 소스 1개면 None
    assert llm._flowi_scatter_source_pair("VM VTH 만 보여줘") is None
    # VM/INLINE 기본 avg, ET 기본 median
    assert llm._flowi_source_default_agg("VM") == "avg"
    assert llm._flowi_source_default_agg("INLINE") == "avg"
    assert llm._flowi_source_default_agg("ET") == "median"


def test_scatter_slot_agg_respects_admin_then_source_default():
    # 관리자 scatter 설정은 INLINE/ET 키만 — VM 은 항상 소스 기본(avg).
    defaults = {"inline_agg": "median", "et_agg": "p90"}
    assert llm._flowi_scatter_slot_agg("INLINE", defaults) == "median"
    assert llm._flowi_scatter_slot_agg("ET", defaults) == "p90"
    assert llm._flowi_scatter_slot_agg("VM", defaults) == "avg"
    assert llm._flowi_scatter_slot_agg("VM", {}) == "avg"


def test_provenance_sql_reflects_agg():
    # ET provenance SQL 이 선택 집계를 반영해야 raw data 공유가 정확하다.
    base = {"source_type": "ET", "item_id": "IOFF", "product": "PRODA"}
    assert "MEDIAN(value)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "median"})
    assert "QUANTILE_CONT(value, 0.9)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "p90"})
    assert "MAX(value)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "max"})
    shot_sql = llm._flowi_dashboard_sql_from_config({**base, "aggregation": "shot"})
    assert "value AS y" in shot_sql and "GROUP BY" not in shot_sql


def test_provenance_sql_reflects_agg_inline():
    # INLINE 도 대칭 — 기본 avg, 선택 시 median/p90/max/shot 반영.
    base = {"source_type": "INLINE", "item_id": "CD", "product": "PRODA"}
    assert "AVG(value)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "avg"})
    assert "MEDIAN(value)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "median"})
    assert "QUANTILE_CONT(value, 0.9)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "p90"})
    shot_sql = llm._flowi_dashboard_sql_from_config({**base, "aggregation": "shot"})
    assert "value AS y" in shot_sql and "GROUP BY" not in shot_sql


def test_provenance_sql_reflects_agg_vm():
    # VM trend provenance — 기본 avg, FROM VM, 집계 선택 반영.
    base = {"source_type": "VM", "item_id": "3.0 VTN", "product": "PRODA"}
    avg_sql = llm._flowi_dashboard_sql_from_config({**base, "aggregation": "avg"})
    assert "AVG(value)" in avg_sql and "FROM VM" in avg_sql
    assert "QUANTILE_CONT(value, 0.9)" in llm._flowi_dashboard_sql_from_config({**base, "aggregation": "p90"})
    # aggregation 미지정이면 VM 기본은 avg
    assert "AVG(value)" in llm._flowi_dashboard_sql_from_config({**base})
