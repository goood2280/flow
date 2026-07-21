---
doc_id: proda_dashboard_query_manual
kind: manual
title: PRODA dashboard query 운영 규칙
summary: Flow-i와 dashboard에서 PRODA lot/wafer trend를 조회할 때 쓰는 예시 질의 규칙입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:08+09:00
updated_at: 2026-05-15T07:47:08+09:00
product: PRODA
tags: ["PRODA", "dashboard", "Flow-i", "manual", "LOT_WF"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["ml_table_proda.lot_wf", "ml_table_proda.step_id", "proda_hold_release_decision"]
relations: {"ml_table_proda.lot_wf": "uses_key", "ml_table_proda.step_id": "filters_by", "proda_hold_release_decision": "supports_decision"}
---

# PRODA dashboard query 운영 규칙

## 조회 규칙

PRODA 이상 신호를 볼 때는 root_lot_id, wafer_id, LOT_WF를 모두 검색 가능한 키로 남깁니다.

## Flow-i 예시 질문

- PRODA A1001 W07 DIBL SS trend 보여줘
- PRODA LOT_WF 기준으로 SORT split 비교해줘
- A1001 step_id별 DIBL 평균을 dashboard로 그려줘
