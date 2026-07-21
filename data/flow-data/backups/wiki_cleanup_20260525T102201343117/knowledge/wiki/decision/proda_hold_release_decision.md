---
doc_id: proda_hold_release_decision
kind: decision
title: PRODA A1001 hold/release 판정
summary: A1001 lot의 재측정과 split 비교 결과를 근거로 hold 또는 release를 판정하는 예시 decision입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:08+09:00
updated_at: 2026-05-15T07:47:08+09:00
product: PRODA
root_lot_id: A1001
tags: ["PRODA", "decision", "hold", "release", "A1001"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_dibl_ss_rca_issue", "proda_dashboard_query_manual"]
relations: {"proda_dibl_ss_rca_issue": "addresses_issue", "proda_dashboard_query_manual": "requires_check"}
---

# PRODA A1001 hold/release 판정

## 판정 기준

hold/release 판정은 단일 wafer 이상 여부가 아니라 split 간 차이와 재측정 복귀 여부를 함께 봅니다.

## 예시 판정

- W07만 일시적으로 튀고 재측정에서 control limit 안으로 복귀하면 conditional release 후보입니다.
- lot 전체 평균이 같이 이동하면 hold 후 공정 조건 확인을 우선합니다.
