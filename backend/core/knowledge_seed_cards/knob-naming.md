---
term: KNOB 네이밍 규칙
kind: concept
aliases: [knob 이름, feature 이름, 스플릿 조건]
sources:
  - file: ppid_knob.csv
    role: rulebook
related: [ppid-knob-rulebook, ml-table-knob]
status: active
---
split/knob feature 이름과 컬럼 프리픽스의 규칙.

- feature 이름은 "3.0 VTN" 과 **비슷한 구조** (숫자 + 공정 명칭). 앞 숫자(3.0, 4.0 …)는 보통 **photo layer 단위**로 끊어져 있다.
- 컬럼 프리픽스는 **어느 DB 에서 나왔는지**가 기본: FAB_, MASK_, KNOB_ 는 모두 FAB DB 출신, INLINE_ 은 Inline DB, VM_ 은 VM DB 출신이다.
- KNOB_ 는 FAB DB 출신이지만 **사용 빈도가 높고 plan 등으로 따로 관리**되어 별도 프리픽스로 분리해 둔 것.
- 엔지니어 구어: knob 을 보통 "스플릿", "스플릿 조건", "조건", "실험" 이라고 부른다 — 이런 표현의 질문은 split/knob 질문으로 해석한다.
