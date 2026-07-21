---
term: commonality
kind: playbook
aliases: [커먼널리티, 커머낼리티, 공통성 분석]
trigger_terms: [유의차]
related: [split-question-playbook, ml-table-knob]
status: active
---
**commonality 분석** — 유의차가 보이는 lot 에 대해 "무엇이 달랐나"를 찾는 정형 질문.

- 전형적 질문: "이 랏에 유의차가 보이는데 어떤 조건이 차이나?", "inline 이나 다른 소스에서 차이가 보이나?"
- 확인 순서: ① split/knob 조건 차이 (ML_TABLE KNOB_*) → ② 공정 진행 이력 차이 (FAB_*, 설비) → ③ INLINE/VM 측정 차이.
- 함께 자주 나오는 질문 유형: "이 랏 목적이 뭐야?" (lot 목적), "해당 step 어떤 조건으로 진행했어?" (step 진행 조건 조회).
