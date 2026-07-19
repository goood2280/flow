---
term: inline_matching.csv
kind: rulebook
aliases: [INLINE 매칭, inline matching]
trigger_terms: [inline 항목, 인라인, INLINE]
sources:
  - file: inline_matching.csv
    role: inline_matching
    location: FLOW_DB_ROOT 루트
related: [vehicle-matching, vm-matching]
status: active
---
inline_matching.csv 는 **INLINE 측정 항목을 wide form 으로 가져오기 위한 매칭테이블**이다.

- 컬럼: step_id 와 해당 측정 step 에서 보는 item_id. 한 측정 step_id 에 **여러 item_id 가 측정될 수 있다** (1:N).
- INLINE 컬럼을 SplitTable/조회 화면에 wide 로 펼칠 때 이 매칭을 기준으로 한다.
- **INLINE 은 샘플링 측정이 기본** — 측정 안 된 wafer 는 빈칸으로 나온다. 보간으로 채우고 싶은 수요는 있으나 split/설비 조건 차이 때문에 아직 하지 않는다. split 비교 시 빈칸을 결측 그대로 다룰 것.
