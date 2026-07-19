---
term: mask.csv
kind: rulebook
aliases: [MASK, mask 버전, mask_version, reticle, 레티클]
trigger_terms: [mask 버전, reticle, 마스크 버전]
answered_by: ppid_knob
sources:
  - file: mask.csv
    role: mask_matching
    location: FLOW_DB_ROOT 루트
related: [ppid-knob-rulebook, ml-table-knob, knob-naming, fab-db]
status: active
---
mask.csv 는 **FAB 공정 이력의 reticle_id 를 엔지니어가 이해하기 좋은 마스크 버전으로 바꾼 매칭 테이블**이다.

- raw 는 FAB DB 의 reticle_id 이고, 이를 사람이 읽는 mask 버전 이름으로 매핑한다 (ppid_knob.csv 가 ppid→split 이름으로 바꾸는 것과 같은 성격의 룰북).
- ML_TABLE 의 MASK_* 컬럼(wafer 별 mask 배정)이 이 매핑을 반영한다 (ml-table-knob 참조).
- "이 wafer 의 mask 버전이 뭐야", "MASK_ 항목은 어디서 나온 값이야", "reticle 바뀌었어?" 류 질문의 근거 파일.
