---
doc_id: proda_a1001_w07_wafer_signal
kind: wafer
title: PRODA A1001 W07 wafer 이상 신호
summary: W07 wafer에서 DIBL 증가와 SS 악화가 같이 관찰될 때 확인할 예시 신호 지식입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:07+09:00
updated_at: 2026-05-15T07:47:07+09:00
product: PRODA
root_lot_id: A1001
wafer_id: W07
tags: ["PRODA", "A1001", "W07", "wafer", "DIBL", "SS"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_dibl_ss_rca_issue", "ml_table_proda.wafer_id", "ml_table_proda.lot_wf"]
relations: {"proda_dibl_ss_rca_issue": "supports_issue", "ml_table_proda.wafer_id": "uses_key", "ml_table_proda.lot_wf": "uses_key"}
---

# PRODA A1001 W07 wafer 이상 신호

## 신호 기준

W07 wafer는 예시 데이터에서 DIBL과 SS가 동시에 나빠지는 case를 표현합니다.

## 해석 메모

- DIBL만 단독 상승하면 short channel 후보를 먼저 봅니다.
- SS와 같이 악화되면 interface 또는 gate stack 계열 issue도 함께 봅니다.
- wafer_id만으로는 lot context가 부족하므로 LOT_WF를 함께 사용합니다.
