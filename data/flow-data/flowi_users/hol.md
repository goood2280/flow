# Flowi User Context: hol

- Created: 2026-04-28T15:13:52.182135+00:00
- Updated: 2026-04-28T15:13:52.182135+00:00

## User Notes
<!-- FLOWI_USER_NOTES_START -->

<!-- FLOWI_USER_NOTES_END -->

## Activity Log

### 2026-04-28T15:13:52.190130+00:00 - chat
- prompt: 연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.
- intent: splittable_guidance
- llm_used: 
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=-, step=- - 추가로 필요: product, root_lot_id/lot_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-28T15:14:11.069581+00:00 - chat
- prompt: 연결 확인입니다. 정상 수신했다면 확인완료 라고만 답하세요.
- intent: splittable_guidance
- llm_used: 
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=-, step=- - 추가로 필요: product, root_lot_id/lot_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-28T15:21:29.919746+00:00 - chat
- prompt: B1000 VM 1.0_STI 값이 몇이야?
- intent: db_table_lookup
- llm_used: 
- answer: 실제 ML_TABLE parquet에서 조건을 적용해 표로 조회했습니다. 25개 row를 표시합니다. 조회 컬럼: KNOB_1.0 STI, INLINE_1.0 STI, MASK_1.0 STI, FAB_1.0 STI, VM_1.0 STI, QTIME_1.0 STI, VM_2.0 WELL, VM_3.0 VTN.

### 2026-04-28T15:22:41.074581+00:00 - chat
- prompt: A1003 제품명이 뭐야?
- intent: splittable_guidance
- llm_used: 
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=A1003, step=A1003 - 추가로 필요: product Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-28T15:23:11.633677+00:00 - chat
- prompt: A1003 제품명이 뭐야?
- intent: splittable_guidance
- llm_used: 
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=A1003, step=A1003 - 추가로 필요: product Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-28T15:24:31.526896+00:00 - chat
- prompt: A0001 VTH GATE_CD Corr. chart 그려줘
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: VTH, CD, A0001, GATE_CD, CORR. - 기본 집계: INLINE avg, ET median - shot/die key가 양쪽에 있으면 shot 단위로 먼저 매칭하고, 없을 때 lot_wf로 내려갑니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-04-28T15:25:02.820855+00:00 - chat
- prompt: A0001 VTH GATE_CD Corr. chart 그려줘 / schema 후보 먼저 확인
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: VTH, CD, A0001, GATE_CD, CORR., SCHEMA - 기본 집계: INLINE avg, ET median - shot/die key가 양쪽에 있으면 shot 단위로 먼저 매칭하고, 없을 때 lot_wf로 내려갑니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-04-28T15:25:45.743514+00:00 - chat
- prompt: A0001 VTH GATE_CD Corr. chart 그려줘 / schema 후보 먼저 확인 / schema 후보 먼저 확인
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: VTH, CD, A0001, GATE_CD, CORR., SCHEMA - 기본 집계: INLINE avg, ET median - shot/die key가 양쪽에 있으면 shot 단위로 먼저 매칭하고, 없을 때 lot_wf로 내려갑니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-04-28T15:30:07.915645+00:00 - chat
- prompt: proda knob PPID_24_2을 가지고 있는 가장 빠른 랏이 어디에 있어?
- intent: knob_fastest_lot
- llm_used: 
- answer: KNOB_1.0 STI 값을 가진 lot 중 FAB 최신 step 기준으로 가장 앞선 후보를 계산했습니다. Top: A1000 / -.
