---
doc_id: proda_a1001_lot_watch
kind: lot
title: PRODA A1001 lot 관찰 기록
summary: A1001 lot에서 W07 wafer의 DIBL/SS 변동을 확인하고 SORT split 여부를 추적합니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:07+09:00
updated_at: 2026-05-15T07:47:07+09:00
product: PRODA
root_lot_id: A1001
tags: ["PRODA", "A1001", "lot", "DIBL", "demo"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_a1001_w07_wafer_signal", "proda_dibl_ss_rca_issue", "proda_sort_knob_split_rule"]
relations: {"proda_a1001_w07_wafer_signal": "has_wafer_signal", "proda_dibl_ss_rca_issue": "raises_issue", "proda_sort_knob_split_rule": "checks_split_rule"}
---

# PRODA A1001 lot 관찰 기록

## 관찰 내용

A1001 lot은 최근 dashboard trend에서 W07 중심으로 DIBL 상승과 SS 악화가 같이 보이는 예시 lot입니다.

## 확인 순서

1. LOT_WF 기준으로 wafer trend를 좁힙니다.
2. step_id와 function_step을 확인해 SORT 전후 구간을 분리합니다.
3. knob 변경 기록이 있으면 RCA issue 문서와 연결합니다.
