---
term: step_matching.csv
kind: rulebook
aliases: [step 매칭, function_step 매핑]
trigger_terms: [무슨 step, 어떤 공정, step_id]
answered_by: step_lookup
sources:
  - file: step_matching.csv
    role: step_matching
    location: FLOW_DB_ROOT 루트
related: [vehicle-matching]
status: active
---
step_id ↔ function_step 의 양방향 매칭은 step_matching.csv 가 단일 소스다.

- 컬럼: product, step_id, function_step. 제품별로 행이 존재한다.
- "AA100090 이 무슨 step 이야" / "SD_EPI 의 step_id 는" 류 질문은 step_lookup 유닛이 이 파일로 답한다.
