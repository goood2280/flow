---
doc_id: proda_dibl_ss_rca_issue
kind: issue
title: PRODA DIBL SS RCA 후보
summary: DIBL 증가와 SS 악화가 동시 발생할 때 확인할 RCA 후보와 증거 연결 방식입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:07+09:00
updated_at: 2026-05-15T07:47:07+09:00
product: PRODA
root_lot_id: A1001
wafer_id: W07
tags: ["PRODA", "RCA", "DIBL", "SS", "issue"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_a1001_w07_wafer_signal", "proda_sort_knob_split_rule", "proda_hold_release_decision"]
relations: {"proda_a1001_w07_wafer_signal": "observed_in", "proda_sort_knob_split_rule": "uses_rule", "proda_hold_release_decision": "resolved_by"}
---

# PRODA DIBL SS RCA 후보

## RCA 후보

DIBL 증가와 SS 악화가 같이 보이면 단일 metric 이상으로 확정하지 않고 다음 후보를 분리합니다.

- gate electrostatic control 약화
- interface trap 증가
- SORT split 조건 차이
- 특정 wafer edge 영향

## 증거 연결

증거는 chart, LOT_WF key, step_id, knob 변경 기록을 같이 묶어야 합니다.
