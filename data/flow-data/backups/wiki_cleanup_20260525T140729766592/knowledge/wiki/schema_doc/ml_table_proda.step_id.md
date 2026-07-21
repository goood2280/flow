---
doc_id: ml_table_proda.step_id
kind: schema_doc
title: ML_TABLE_PRODA · step_id
summary: step_id는 공정 또는 측정 단계 식별자로 split/knob 비교 필터에 사용됩니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:09+09:00
updated_at: 2026-05-15T07:47:09+09:00
product: PRODA
tags: ["schema", "ML_TABLE_PRODA", "step_id", "function_step"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_sort_knob_split_rule", "proda_dashboard_query_manual"]
relations: {"proda_sort_knob_split_rule": "filters_split", "proda_dashboard_query_manual": "filters_chart"}
relation_id: ML_TABLE_PRODA
column_refs: ["ML_TABLE_PRODA.step_id"]
---

# ML_TABLE_PRODA · step_id

## column 의미

step_id는 trend를 공정 또는 측정 단계별로 나눌 때 사용하는 기준입니다.

## 사용 원칙

knob 변경과 metric 변동이 같은 step에서 발생했는지 먼저 확인하고, function_step alias가 있으면 함께 표시합니다.
