---
doc_id: dashboard_chart_generation_rules
kind: agent_wiki
title: Dashboard Chart Generation Rules
summary: Flow-i dashboard chart requests must use Agent Wiki interpretation rules, then verify columns and rows against DB/Files before returning a chart.
actor: system
created_at: 2026-05-13T00:00:00+09:00
updated_at: 2026-05-13T00:00:00+09:00
tags: ["dashboard", "chart", "flowi", "trend", "scatter", "INLINE", "ET", "lot_wf", "tkout_time"]
schema_type: agent_llm_wiki_page_v1
---

# Dashboard Chart Generation Rules

## Summary

Flow-i uses these rules as interpretation guidance for dashboard chart requests. The rules do not authorize invented columns, items, joins, or values. Every chart still has to be verified against actual DB/Files schema and rows before Flow-i returns a saveable chart_config.

## Maintained Notes

- Trend means a scatter chart with x_col=tkout_time.
- INLINE defaults to aggregation=avg by grain=lot_wf.
- ET defaults to aggregation=median by grain=lot_wf.
- lot_wf is root_lot_id + "_" + wafer_id.
- If an INLINE source lacks a physical lot_wf column, derive lot_wf from root_lot_id and wafer_id when both source columns exist.
- Process or module expressions such as 16.0 VIA2 should be interpreted first as INLINE item_id candidates.
- The wiki rule is an interpretation rule only. Flow-i must verify that the referenced source_type, columns, item_id values, and rows exist in DB/Files.

## Expected Flow-i Defaults

- source_type=INLINE, trend: chart_type=scatter, x_col=tkout_time, grain=lot_wf, aggregation=avg.
- source_type=ET, metric scatter: grain=lot_wf, aggregation=median.
- Cross-source INLINE and ET scatter joins should use verified lot_wf keys unless shot/die grain is explicitly requested and available.
