---
term: PPID
kind: concept
aliases: [피피아이디]
trigger_terms: [ppid 분류]
answered_by: ppid_knob
sources:
  - file: ppid_knob.csv
    role: rulebook
related: [ppid-knob-rulebook]
status: active
---
PPID 는 장비 레시피 식별자다. SplitTable 셀에 들어가는 raw 값이며, ppid_knob.csv 의 value 열과 대응한다.

- "이 PPID 가 어떤 knob/split 인가" → ppid_knob.csv 의 operator(eq) 규칙으로 value→category 분류 (ppid_knob 유닛 담당).
- 룰북에 없는 PPID 는 미분류(RO/알람 대상)로 취급한다.
