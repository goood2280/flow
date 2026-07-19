---
term: vm_matching.csv
kind: rulebook
aliases: [VM 매칭, vm matching, 가상 step]
sources:
  - file: vm_matching.csv
    role: vm_matching
    location: FLOW_DB_ROOT 루트
related: [inline-matching]
status: active
---
vm_matching.csv 는 **main step 에서 측정되는 다양한 설비값들로 만들어진 가상(VM) step 의 매칭테이블**이다.

- **VM = Virtual Measurement/Metrology** — main step 의 설비 센서 데이터로 INLINE 측정값을 예측해 맞춰둔 값이다. 실제 측정 step 이 아니라 가상 step 값.
- step_id 에 따라 여러 item_id 가 있는 1:N 매칭 구조 — VM 항목을 wide form 으로 볼 때 기준이 된다.
- **주의**: 설비 parameter 로 만든 값이라 관리가 안 되면 실제 INLINE 측정과 다를 수 있다 — 절대값 단정보다 참고/교차 확인용으로 쓴다.
