---
term: ppid_knob.csv
kind: rulebook
aliases: [KNOB 룰북, knob 규칙, split 규칙]
trigger_terms: [split 구성, 스플릿 규칙, knob 분류, 어떤 knob]
answered_by: ppid_knob
sources:
  - file: ppid_knob.csv
    role: rulebook
    location: FLOW_DB_ROOT 루트 (미설정 시 data/Fab)
related: [split-question-playbook, vehicle-matching]
status: active
---
ppid_knob.csv 는 **FAB DB 의 ppid 를 엔지니어가 해석 가능한 split 이름으로 매칭해 둔 룰북**이다.

- 기능 단위 function_step(step_desc) 의 ppid 에 따라 규칙을 정한다. 단일 step 뿐 아니라 **복수의 step 을 묶어 하나의 split** 을 만들 수도 있다.
- 컬럼: feature_name(=split 이름, 예 "3.0 VTN"), function_step(step_desc), rule_order, operator, value(=ppid), category(=분류 결과 knob 이름).
- 한 feature 는 R1..Rn 순서 규칙 + RO(rest-of, 나머지) 로 구성된다. operator 는 현재 eq 만 사용.
- 복수 step split 의 표현: 한 행에는 step 이 하나씩 달리고, **rule_order(R#) 가 같은 행들은 AND 조건**으로 묶인다 (여러 step 의 ppid 조건을 동시에 만족해야 그 category 로 분류).
- "X split 이 어떻게 구성되어 있나" / "이 ppid 는 어떤 knob 인가" 류 질문은 **이 파일**을 근거로 답한다 (ppid_knob 유닛 담당).
- wafer 별 실제 배정 결과는 이 파일이 아니라 ML_TABLE 의 KNOB_* 컬럼에 있다 (ml-table-knob 카드 참조).
