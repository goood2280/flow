---
doc_id: ml_table_proda.wafer_id
kind: schema_doc
title: ML_TABLE_PRODA · wafer_id
summary: wafer_id는 lot 안의 wafer 단위 식별자이며 LOT_WF 구성에 사용됩니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:08+09:00
updated_at: 2026-05-15T07:47:08+09:00
product: PRODA
tags: ["schema", "ML_TABLE_PRODA", "wafer_id", "LOT_WF"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_a1001_w07_wafer_signal", "ml_table_proda.lot_wf"]
relations: {"proda_a1001_w07_wafer_signal": "identifies", "ml_table_proda.lot_wf": "builds_key"}
relation_id: ML_TABLE_PRODA
column_refs: ["ML_TABLE_PRODA.wafer_id"]
---

# ML_TABLE_PRODA · wafer_id

## column 의미

wafer_id는 root_lot_id 내부 wafer 번호를 나타냅니다. 단독으로 쓰면 제품 또는 lot context가 빠질 수 있습니다.

## 사용 원칙

LOT_WF 또는 root_lot_id와 함께 사용해 trend, split, RCA 근거를 연결합니다.
