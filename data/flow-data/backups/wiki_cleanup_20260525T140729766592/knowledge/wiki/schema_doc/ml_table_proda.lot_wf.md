---
doc_id: ml_table_proda.lot_wf
kind: schema_doc
title: ML_TABLE_PRODA · LOT_WF
summary: LOT_WF는 root_lot_id와 wafer_id를 결합한 wafer-level 분석 키입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:09+09:00
updated_at: 2026-05-15T07:47:09+09:00
product: PRODA
tags: ["schema", "ML_TABLE_PRODA", "LOT_WF", "root_lot_id", "wafer_id"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["ml_table_proda.root_lot_id", "ml_table_proda.wafer_id", "proda_dashboard_query_manual"]
relations: {"ml_table_proda.root_lot_id": "combines", "ml_table_proda.wafer_id": "combines", "proda_dashboard_query_manual": "used_by"}
relation_id: ML_TABLE_PRODA
column_refs: ["ML_TABLE_PRODA.LOT_WF"]
---

# ML_TABLE_PRODA · LOT_WF

## column 의미

LOT_WF는 wafer-level trend와 RCA 증거를 연결하기 위한 복합 키입니다.

## 예시

A1001_W07처럼 root_lot_id와 wafer_id를 조합해 dashboard, Flow-i, 지식위키 검색에서 같은 wafer를 가리키게 합니다.
