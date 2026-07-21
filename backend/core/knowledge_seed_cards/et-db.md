---
term: ET DB
kind: data-source
aliases: [ET, Electrical Test, 이티]
trigger_terms: [ET 값, ET 데이터]
sources:
  - file: ET DB
    role: et_db
  - file: reformatter/{vehicle}_reformatter.csv
    role: et_reformat
related: [reformatter, chip-radius-teg, ml-table-knob]
status: active
---
ET 는 **Electrical Test data** — wafer 의 전기적 특성 측정값이다.

- **FAB 과 다르다**: FAB(FAB_* 컬럼)은 공정 진행 이력이고, ET 값은 **ET DB 에 별도로** 있다. ML_TABLE 의 FAB_* 컬럼을 ET 로 오해하지 말 것.
- 현재 들어있는 ET 항목은 performance 류보다 **HOL 관점 — systematic 불량을 잡는 TEG** 류가 중심이고, **일부 SRAM, vramp, DVC 등도 있다**. 구체 항목 종류는 보안상 시드에 없다 — 사내 채움 카드(ET 항목 종류) 참조.
- ET raw 는 reformatter({vehicle}_reformatter.csv)를 거쳐 엔지니어 기준값으로 변환된다. raw 를 직접 보는 일은 드물고, 이상이 있을 때 확인하는 정도.
- ET 기반 자동 리포팅 시스템은 **auto report 프로젝트**로 구현되어 있다 — ET 의 구조/활용을 깊게 이해하려면 그쪽을 참조.
