---
doc_id: proda_operating_overview
kind: product
title: PRODA 운영 개요
summary: PRODA 제품의 lot/wafer 추적, SORT split, RCA 확인 흐름을 묶는 예시 운영 지식입니다.
actor: codex_demo_seed
created_at: 2026-05-15T07:47:07+09:00
updated_at: 2026-05-15T07:47:07+09:00
product: PRODA
tags: ["PRODA", "운영", "제품", "demo"]
schema_type: demo_operational_knowledge_v1
seed_batch: knowledge_graph_demo_2026_05_15
related_doc_ids: ["proda_a1001_lot_watch", "ml_table_proda.root_lot_id", "ml_table_proda.lot_wf"]
relations: {"proda_a1001_lot_watch": "has_watch_lot", "ml_table_proda.root_lot_id": "uses_key", "ml_table_proda.lot_wf": "uses_key"}
---

# PRODA 운영 개요

## 사용 목적

PRODA는 예시 제품으로, Flow 지식위키 graph가 product, lot, wafer, schema column, issue 문서를 함께 연결하는지 확인하기 위한 기준 노드입니다.

## 운영 포인트

- lot 단위로 root_lot_id를 우선 확인합니다.
- wafer 단위 분석에서는 LOT_WF와 wafer_id를 함께 기록합니다.
- SORT split 또는 knob 변경이 있으면 decision 문서와 issue 문서를 같이 남깁니다.
