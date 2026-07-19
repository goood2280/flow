---
term: SplitTable
kind: feature-page
aliases: [스플릿테이블, splittable, 스플릿 테이블]
answered_by: split_nav
sources:
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
  - file: ppid_knob.csv
    role: rulebook
related: [ml-table-knob, split-question-playbook]
status: active
---
SplitTable 화면은 root lot 기준으로 wafer×KNOB/MASK 매트릭스를 보여준다.

- 딥링크: /splittable?product={P}&root={ROOT} — flow-i 의 split_nav 유닛이 이 링크와 인라인 데이터를 함께 반환한다.
- 화면 상단 prefix 필터(KNOB/MASK/FAB/INLINE/VM)와 KNOB 매칭 규칙 모달(ppid_knob.csv + Vehicle_matching.csv)을 제공한다.
- "스플릿테이블 보여줘" 는 네비게이션 의도 → split_nav. "특정 knob 만" 은 knob 이름을 함께 추출해야 한다 (복수 가능).
