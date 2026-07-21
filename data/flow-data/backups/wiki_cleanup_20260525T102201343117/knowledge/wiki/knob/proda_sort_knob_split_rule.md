---
doc_id: proda_sort_knob_split_rule
kind: knob
title: PRODA SORT knob split 확인 규칙
summary: SORT split, knob, step_id를 함께 확인해 DIBL/SS 변동과 연결하는 예시 규칙입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:07+09:00
updated_at: 2026-05-15T07:47:07+09:00
product: PRODA
tags: ["PRODA", "SORT", "knob", "split", "step_id"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_a1001_lot_watch", "proda_hold_release_decision", "ml_table_proda.step_id"]
relations: {"proda_a1001_lot_watch": "applies_to", "proda_hold_release_decision": "informs_decision", "ml_table_proda.step_id": "uses_key"}
---

# PRODA SORT knob split 확인 규칙

## 확인 규칙

SORT knob 변경이 있는 lot은 split group, step_id, function_step을 함께 확인합니다.

## 운영 메모

- split A/B 간 DIBL 평균과 SS 평균을 같이 비교합니다.
- LOT_WF 단위 chart를 먼저 보고, root_lot_id aggregate로 다시 확인합니다.
- knob 변경이 임시 조치인지 release 조건인지 decision 문서에 남깁니다.
