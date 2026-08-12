---
term: 공정 구간
kind: rulebook
aliases: [process stage, 공정 단계, 공정 영역, FEOL, MOL 구간, BEOL 구간, RMG 구간, PC 구간]
trigger_terms: [어디까지가, 어디부터, 무슨 구간, 어느 구간, 앞단, 뒷단]
answered_by: step_lookup
sources:
  - file: Vehicle_matching.csv
    role: step_matching
    location: FLOW_DB_ROOT 루트
  - file: step_matching.csv
    role: step_matching
    location: FLOW_DB_ROOT 루트
related: [step-matching, vehicle-matching, process-terms]
status: todo
---
제품(vehicle)별로 **step_id 가 어느 공정 단계(PC / RMG / MOL / BEOL 등)에 속하는지**의
구간 매핑. "AA123456 은 BEOL 이야?", "PC 는 어디까지야?" 류 질문의 근거가 된다.
step_desc 로 대략 추정 가능하지만(환경 증거의 자동 추정 참조), 확정 구간은 사내 채움.

질문 (관리자 채움용 — "지식 채움 수행" / "지식 답변:" 으로 채운다):

1. 이 fab 에서 쓰는 공정 단계 구분과 순서는? (예: PC → RMG → MOL → BEOL — FEOL/Gate 등
   다른 명칭을 쓰면 함께, 각 단계의 의미 한 줄씩)
2. 제품별로 각 단계의 step_id 시작~끝 구간은? (제품 1개당: 제품명, 단계, 시작 step_id,
   끝 step_id. 환경 증거의 step_desc 기반 자동 추정 구간이 있으면 맞는지 확인/교정)
3. 단계 경계에 걸치거나 예외인 step 은? (계측/검사 step, photo rework EC step 등은
   어느 단계로 세는지)
4. RMG 는 어떤 step 명칭/step_desc 키워드로 식별되나? (예: RMG, HKMG, METAL GATE,
   REPLACEMENT GATE 등 — 실제 step_desc 표기 기준)
5. 구간이 제품(세대)마다 다른가, vehicle 계열이 같으면 동일한가? (신규 제품이 들어올 때
   구간을 어떻게 정하는지)
