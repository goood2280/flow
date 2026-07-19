---
term: FAB DB
kind: data-source
aliases: [FAB raw, 1.RAWDATA_DB_FAB, lot 진행 DB, 진행 이력, 설비 이력]
trigger_terms: [현재 step, 지금 어느 step, 어느 장비, 진행 이력, tkout]
answered_by: step_lookup
sources:
  - file: 1.RAWDATA_DB_FAB
    role: fab_db
related: [ppid, knob-naming, ml-table-knob, et-db]
status: active
---
FAB DB 는 **lot 공정 진행 이력의 원천**이다 (ET DB 와 다름 — ET 는 전기적 측정, FAB 은 진행 이력).

- 주요 컬럼: lot_id, root_lot_id, wafer_id, step_id, equipment(eqp), tkout_time(공정 track-out 측정시각).
- "A1002 지금 어느 step 이야", "이 lot 어느 장비 지났어", "ppid raw 확인" 류 질문의 원천.
- 단, **"현재 step/장비" 질문의 실제 답변은 파생 캐시**에서 나온다: data/Fab/cache/lot_progress_latest_lot_by_root_wafer.parquet (core.lot_progress_cache.lot_progress_snapshot). raw DB 전수 스캔이 아니라 이 캐시를 먼저 본다.
- tkout_time 은 차트 x축(시간축)으로 자주 쓴다 — ET Index trend 를 tkout_time 기준으로 그리는 게 대표 패턴 (chart-playbook 참조).
