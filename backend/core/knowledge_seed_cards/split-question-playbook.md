---
term: split 질문 플레이북
kind: playbook
aliases: [스플릿 질문, split 질문]
trigger_terms: [스플릿, split, 스플릿테이블, knob]
sources:
  - file: ppid_knob.csv
    role: rulebook
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
related: [ppid-knob-rulebook, ml-table-knob, splittable-page, root-lot-product, commonality]
status: active
---
split 관련 질문은 4유형으로 나눠 처리한다.

1. **화면 열기/조회** — "A1002 스플릿테이블 보여줘": root lot → product 확인(복수면 선택지) → SplitTable 딥링크 + 인라인 데이터. 담당: split_nav.
2. **특정 knob 만 보기** — "3.0 VTN, 4.0 GATE_OX 만": knob 이름은 **복수**로 추출해 합집합 필터. 요청 개수("2개")와 추출 개수가 다르면 누락을 명시한다.
3. **규칙 구성** — "3.0 VTN split 어떻게 구성돼": ppid_knob.csv 룰북(R1..Rn+RO) 나열. 담당: ppid_knob.
4. **역조회** — "knob 값 X 인 lot 찾아줘": ML_TABLE 의 KNOB_* 값 검색.

원칙: 규칙(정의)은 ppid_knob.csv, 실적(배정)은 ML_TABLE — 근거 파일을 답변에 명시한다.
