---
term: Vehicle_matching.csv
kind: rulebook
aliases: [vehicle 매칭, step_desc 매칭, function step 매칭]
trigger_terms: [vehicle, 비히클]
sources:
  - file: Vehicle_matching.csv
    role: step_matching
    location: FLOW_DB_ROOT 루트
related: [ppid-knob-rulebook, step-matching]
status: active
---
Vehicle_matching.csv 는 **step_id 를 function_step(step_desc) 으로 매칭해 둔 마스터 테이블**이다.

- step_id 구조: 영문자 2자 + 숫자 6자리 (+α 접미). 제품(vehicle)별로 step_id 는 다르다.
- **vehicle 과 product 는 같은 개념**이다 (용어만 다름 — reformatter 등 vehicle 단위 파일은 제품 단위 파일로 보면 된다).
- 목적: 제품마다 다른 step_id 를 **기능이 같은 step(function_step) 끼리 묶기** 위함.
- ppid_knob.csv 의 step_desc(공용 정의)가 "어느 제품의 어느 step_id 에 적용되는가"는 이 파일을 거쳐야 정확하다.
