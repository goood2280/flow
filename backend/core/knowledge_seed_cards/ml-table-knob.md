---
term: ML_TABLE
kind: data-source
aliases: [KNOB 컬럼, MASK 컬럼, wafer split]
trigger_terms: [knob 배정, wafer 별 knob, 스플릿 실적]
answered_by: split_nav
sources:
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
    location: FLOW_DB_ROOT 루트
related: [ppid-knob-rulebook, root-lot-product]
status: active
---
wafer 별 **실제 split/knob 배정 결과**는 제품별 ML_TABLE_{PRODUCT}.parquet 에 있다.

- 키 컬럼: PRODUCT, ROOT_LOT_ID, (FAB_)LOT_ID, WAFER_ID.
- split 값 컬럼: KNOB_* (knob 배정), MASK_* (mask 배정). 컬럼 이름 뒤쪽이 feature 이름과 대응한다 (예: KNOB_3.0_VTN ↔ feature "3.0 VTN").
- 프리픽스 = 출처 DB: FAB_/MASK_/KNOB_ 는 FAB DB, INLINE_ 은 Inline DB, VM_ 은 VM DB 출신. KNOB_ 는 FAB DB 출신 중 plan 관리 대상이라 별도 분리된 것 (knob-naming 카드 참조).
- "A1002 의 knob 이 뭐야" 처럼 lot/wafer 기준 질문은 이 파일 기준. "규칙이 뭐야" 는 ppid_knob.csv 기준 — 두 소스를 혼동하지 말 것.
