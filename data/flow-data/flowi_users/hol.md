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

### 2026-04-29T12:48:06.820245+00:00 - blocked_write_request
- prompt: DB root에 sample.csv 파일 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.

### 2026-04-29T12:49:55.234739+00:00 - blocked_write_request
- prompt: DB root에 sample.csv 파일 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.

### 2026-04-29T13:08:14.700075+00:00 - blocked_write_request
- prompt: DB root에 sample.csv 파일 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.

### 2026-04-29T13:12:10.336364+00:00 - blocked_write_request
- prompt: DB root에 sample.csv 파일 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.

### 2026-04-29T14:18:22.593985+00:00 - chat
- prompt: PRODA A1000 1.0_STI 어떤 Knob으로 진행했어?
- intent: knob_fastest_lot
- llm_used: 
- answer: KNOB_1.0 STI 값을 가진 lot 중 FAB 최신 step 기준으로 가장 앞선 후보를 계산했습니다. Top: A1000 / -.

### 2026-04-29T14:18:39.615588+00:00 - feedback
- rating: up
- intent: knob_fastest_lot
- tags: correct
- needs_review: 
- golden_candidate: 
- note: 
- prompt: PRODA A1000 1.0_STI 어떤 Knob으로 진행했어?

### 2026-04-29T14:18:41.270054+00:00 - feedback
- rating: up
- intent: knob_fastest_lot
- tags: correct
- needs_review: 
- golden_candidate: 
- note: 
- prompt: PRODA A1000 1.0_STI 어떤 Knob으로 진행했어?

### 2026-04-29T14:28:18.706373+00:00 - chat
- prompt: B1002는 제품이 뭐야? 현재 fab_lot_id 어떻게 분류되어있어?
- intent: et_wafer_median
- llm_used: 
- answer: ET 레포트 단위기능으로 처리하는 요청입니다. - 실행 경로: query_et - 필요한 조건: product, root_lot_id or lot_id, step_id, item_id - 현재 감지: product=-, lot=B1002, step=B1002 - 추가로 필요: product Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T14:28:47.180153+00:00 - feedback
- rating: down
- intent: et_wafer_median
- tags: output_issue, wrong_workflow
- needs_review: True
- golden_candidate: 
- note: ET Report를 원한게 아님 FAB DB에서 봐야함
- prompt: B1002는 제품이 뭐야? 현재 fab_lot_id 어떻게 분류되어있어?

### 2026-04-29T21:21:55.176405+00:00 - chat
- prompt: B1002는 제품이 뭐야? 현재 fab_lot_id 어떻게 분류되어있어?
- intent: et_wafer_median
- llm_used: 
- answer: ET 레포트 단위기능으로 처리하는 요청입니다. - 실행 경로: query_et - 필요한 조건: product, root_lot_id or lot_id, step_id, item_id - 현재 감지: product=-, lot=B1002, step=B1002 - 추가로 필요: product Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:32:29.746556+00:00 - app_write_draft
- prompt: PRODA A1002 aaa1 커스텀 세트로 PEMS 인폼 등록해줘 GATE 모듈에다가 내용은 그냥 GATE 모듈 인폼이라고 적어줘
- intent: inform_create_draft
- feature: inform
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:32:51.456972+00:00 - chat
- prompt: 인폼 로그에서 이 요청을 처리할 화면을 열어줘
- intent: inform_guidance
- llm_used: 
- answer: 인폼 로그 단위기능으로 처리하는 요청입니다. - 실행 경로: open_inform - 필요한 조건: product, root_lot_id, message/reason - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:33:59.512294+00:00 - app_write_draft
- prompt: ㅁㄴㅇㅁ 회의에 2차 회의 5월 5일 오후 2시에 잡아주고 1차아젠다는 네모의꿈 2차 아젠다는 세모의 꿈으로 추가해줘
- intent: meeting_write_draft
- feature: meeting
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:34:32.827043+00:00 - chat
- prompt: 회의관리에서 이 요청을 처리할 화면을 열어줘
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:35:59.643133+00:00 - chat
- prompt: 이런회의 라고 회의 추가해서 매주 화요일 2시에 할거야 카테고리는 아무거나 해도돼
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:36:36.300785+00:00 - app_write_draft
- prompt: 회의하나 네모의꿈으로 매주 수요일에 생성해줘
- intent: meeting_write_draft
- feature: meeting
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:36:50.436296+00:00 - chat
- prompt: 회의관리에서 이 요청을 처리할 화면을 열어줘
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:44:11.005154+00:00 - chat
- prompt: 회의 네모의꿈이라고 매주 수요일 2시에 진행하는 회의 만들어주세요
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:45:13.886721+00:00 - chat
- prompt: 네모의 꿈이라고 매주 수요일 오후2시에 진행하는 회의하나 만들어주세요
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:46:30.899610+00:00 - chat
- prompt: 이슈추적 네모의 꿈이라고 만들고 랏 PRODB B1025B.1 등록해주세요 모니터링용이야
- intent: db_table_lookup
- llm_used: 
- answer: 실제 ML_TABLE parquet에서 조건을 적용해 표로 조회했습니다. 25개 row를 표시합니다.

### 2026-04-29T21:48:09.907226+00:00 - chat
- prompt: 네모의 꿈이라고 매주수요일 오후2시에 진행하는 회의하나 만들어주세요
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:48:38.134206+00:00 - chat
- prompt: 네모의 꿈이라고 매주 수요일 오후2시에 진행하는 회의하나 만들어주세요
- intent: meeting_guidance
- llm_used: 
- answer: 회의관리 단위기능으로 처리하는 요청입니다. - 실행 경로: open_meeting - 필요한 조건: meeting topic, participants, action items - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:49:30.780659+00:00 - app_write_draft
- prompt: TEST1 이슈 하나 등록해줘 PRODA A1004 모니터링할거야
- intent: tracker_issue_create_draft
- feature: tracker
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:49:48.631477+00:00 - chat
- prompt: 이슈 추적에서 이 요청을 처리할 화면을 열어줘
- intent: tracker_guidance
- llm_used: 
- answer: 이슈 추적 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tracker - 필요한 조건: issue title, product/lot/wafer, owner/status - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:50:22.555329+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 모니터링하는거 TEST2 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_issue_create_draft
- feature: tracker
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:50:36.475176+00:00 - chat
- prompt: 이슈 추적에서 이 요청을 처리할 화면을 열어줘
- intent: tracker_guidance
- llm_used: 
- answer: 이슈 추적 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tracker - 필요한 조건: issue title, product/lot/wafer, owner/status - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:50:46.966628+00:00 - chat
- prompt: 이슈 추적에서 이 요청을 처리할 화면을 열어줘
- intent: tracker_guidance
- llm_used: True
- answer: 요청에 따라 이슈 추적 화면을 엽니다. - 실행 경로: open_tracker - 필요한 조건: issue title, product/lot/wafer, owner/status - 현재 감지: product=-, lot=-, step=- - 파일/DB 수정은 수행하지 않습니다. 다음 단계(가능 여부): tracker 탭 열기(이슈 추적 열기), 표 확인(inspect_table).

### 2026-04-29T21:51:08.966342+00:00 - chat
- prompt: 등록까지 된거야?
- intent: splittable_guidance
- llm_used: 
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=-, step=- - 추가로 필요: product, root_lot_id/lot_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:52:19.639703+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 모니터링하는거 TEST2 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_issue_create_draft
- feature: tracker
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:52:32.591692+00:00 - chat
- prompt: 이슈 추적에서 이 요청을 처리할 화면을 열어줘
- intent: tracker_guidance
- llm_used: True
- answer: 다음 중에서 선택해 진행합니다. 1) 이슈 추적 화면 열기 (open_tracker) - 실행 경로: open_tracker - 필요한 조건: issue title, product/lot/wafer, owner/status - 현재 감지: product=-, lot=-, step=- - 비고: Flowi는 조회/표시만 수행하며 DB/Files 수정은 하지 않습니다. 2) 표 확인 (inspect_table) - 실행 경로: inspect_table - 설명: Flowi_action_plan 표를 홈 화면에서 확인 선택지 중 하나를 알려주시면 바로 진행합니다.

### 2026-04-29T21:52:53.455197+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 모니터링하는거 TEST2 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_issue_create_draft
- feature: tracker
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:53:06.605866+00:00 - chat
- prompt: 이슈 추적에서 이 요청을 처리할 화면을 열어줘
- intent: tracker_guidance
- llm_used: True
- answer: 다음 중 처리 화면을 열 수 있습니다. 선택해 주세요. 1) 이슈 추적 열기 (open_tracker) - 실행 경로: open_tracker - 필요한 조건: issue title, product/lot/wafer, owner/status - 현재 감지: product=-, lot=-, step=- - 비고: DB/Files 수정 없이 조회/표시만 수행 2) 표 확인 (inspect_table) - 실행 경로: inspect_table - 설명: Flowi_action_plan 표를 홈 화면에서 확인 - 비고: DB/Files 수정 없음 선택 번호를 알려주시면 바로 진행하겠습니다.

### 2026-04-29T21:54:30.394651+00:00 - app_write_draft
- prompt: Lot: A1003 aaa1 커스텀셋으로 BEOL 모듈 인폼로그 등록해줘
- intent: inform_create_draft
- feature: inform
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:54:44.228975+00:00 - chat
- prompt: 인폼 로그에서 이 요청을 처리할 화면을 열어줘
- intent: inform_guidance
- llm_used: 
- answer: 인폼 로그 단위기능으로 처리하는 요청입니다. - 실행 경로: open_inform - 필요한 조건: product, root_lot_id, message/reason - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:55:05.738218+00:00 - app_write_draft
- prompt: Lot: A1003 인폼로그 aaa1 커스텀셋으로 등록해줘
- intent: inform_create_draft
- feature: inform
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:55:18.836053+00:00 - chat
- prompt: 인폼 로그에서 이 요청을 처리할 화면을 열어줘
- intent: inform_guidance
- llm_used: 
- answer: 인폼 로그 단위기능으로 처리하는 요청입니다. - 실행 경로: open_inform - 필요한 조건: product, root_lot_id, message/reason - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:56:23.494540+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 모니터링하는거 TEST2 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_issue_create_draft
- feature: tracker
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:56:41.826941+00:00 - chat
- prompt: 이슈 추적에서 이 요청을 처리할 화면을 열어줘
- intent: tracker_guidance
- llm_used: 
- answer: 이슈 추적 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tracker - 필요한 조건: issue title, product/lot/wafer, owner/status - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T21:57:11.368788+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 모니터링하는거 TEST2 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_issue_create_draft
- feature: tracker
- answer: 이 요청은 조회가 아니라 앱 내부 기록 생성/수정 작업입니다. 이번 단계에서는 바로 실행하지 않고 초안과 확인 플로우가 필요하다고 표시합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T21:58:12.381783+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 모니터링하는거 TEST2 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_create
- feature: tracker
- answer: 이슈를 바로 등록했습니다. - id: ISS-260430-33BE - category: Monitor - title: TEST2

### 2026-04-29T21:59:07.405806+00:00 - app_write_draft
- prompt: 이슈 PRODA A1004 Analysis 하는거 TEST3 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_create
- feature: tracker
- answer: 이슈를 바로 등록했습니다. - id: ISS-260430-8782 - category: Monitor - title: TEST3

### 2026-04-29T22:00:39.156154+00:00 - app_write_draft
- prompt: 이슈 PRODA A1005 Analysis 하는거 TEST3 이름으로 등록해줘 내용은 ㅁㅁㅁ 적어줘
- intent: tracker_create
- feature: tracker
- answer: 이슈를 바로 등록했습니다. - id: ISS-260430-9FBE - category: Monitor - title: TEST3

### 2026-04-29T22:03:50.906851+00:00 - chat
- prompt: PRODA CD_GATE EQP/Chamber별로 박스차트 그려줘
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: CD, CD_GATE, EQP, CHAMBER - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-04-29T22:04:09.415768+00:00 - chat
- prompt: PRODA CD_GATE EQP/Chamber별로 박스차트 그려줘 / schema 후보 먼저 확인
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: CD, CD_GATE, EQP, CHAMBER, SCHEMA - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-04-29T22:08:20.043246+00:00 - chat
- prompt: PRODA GATE_CD Trend 그려줘
- intent: dashboard_guidance
- llm_used: 
- answer: 대시보드 단위기능으로 처리하는 요청입니다. - 실행 경로: open_dashboard - 필요한 조건: source, x/y column, join key, optional fit/color/filter - 현재 감지: product=PRODA, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T22:09:13.996479+00:00 - chat
- prompt: PRODA0 CD_SPACER Trend 그려줘
- intent: dashboard_guidance
- llm_used: 
- answer: 대시보드 단위기능으로 처리하는 요청입니다. - 실행 경로: open_dashboard - 필요한 조건: source, x/y column, join key, optional fit/color/filter - 현재 감지: product=PRODA0, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T22:11:46.549501+00:00 - app_write_draft
- prompt: PRODA LOT A1001에 이상한랏이라고 꼬리표 달아줘
- intent: lot_wafer_annotation_draft
- feature: tracker
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T22:12:12.329188+00:00 - chat
- prompt: PRODA LOT A1001에 이상한랏이라고 노트 달아줘
- intent: lot_anomaly_summary
- llm_used: 
- answer: A1001에 해당하는 ET/INLINE row를 찾지 못했습니다.

### 2026-04-29T22:22:33.506887+00:00 - app_write_draft
- prompt: 스플릿 테이블 A1001 랏에 이상한 랏이라고 꼬리표 달아줘
- intent: splittable_plan_update_draft
- feature: splittable
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T22:23:06.736372+00:00 - app_write_draft
- prompt: A1001에 스플릿 테이블 꼬리표 이상한 랏이라고 달아줘
- intent: splittable_plan_update_draft
- feature: splittable
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T22:26:16.195294+00:00 - app_write_draft
- prompt: A1001에 이상한 랏 꼬리표 달아줘
- intent: lot_wafer_annotation_draft
- feature: tracker
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-04-29T23:04:55.778901+00:00 - blocked_write_request
- prompt: DB root에 sample.csv 파일 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.

### 2026-04-29T23:05:08.172058+00:00 - chat
- prompt: PRODA Inline CD와 ET LKG Corr scatter 그리고 1차식 fitting line 그려줘
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE - 감지 metric 후보: LKG, CD - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 실제 DB 기준 scatter를 계산했습니다. n=375, corr=-0.272273.

### 2026-04-29T23:05:20.410674+00:00 - chat
- prompt: PRODA CD_GATE box plot 그려줘
- intent: dashboard_box_chart
- llm_used: 
- answer: PRODA CD_GATE INLINE 분포를 root_lot_id별 box plot으로 그렸습니다. group=12, item match=CD_GATE.

### 2026-04-29T23:05:32.672096+00:00 - chat
- prompt: PRODA ET VTH WF map 그려줘
- intent: dashboard_wafer_map_chart
- llm_used: 
- answer: PRODA ET VTH을 shot_x/shot_y 기준 median으로 집계해 WF map을 그렸습니다. points=5, item match=VTH.

### 2026-04-29T23:05:45.021377+00:00 - chat
- prompt: PRODA Inline CD와 ET LKG Corr scatter KNOB_SPLIT B 제외하고 컬러링
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE, ML_TABLE - 감지 metric 후보: LKG, CD, KNOB_SPLIT - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 실제 DB 기준 scatter를 계산했습니다. n=375, corr=-0.272273.

### 2026-04-29T23:05:57.146759+00:00 - chat
- prompt: 테이블맵 relation에서 inline item과 knob 연결 보여줘
- intent: tablemap_guidance
- llm_used: 
- answer: 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=PRODA, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T23:08:04.556107+00:00 - blocked_write_request
- prompt: DB root에 sample.csv 파일 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.

### 2026-04-29T23:08:04.865839+00:00 - chat
- prompt: PRODA Inline CD와 ET LKG Corr scatter 그리고 1차식 fitting line 그려줘
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE - 감지 metric 후보: LKG, CD - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 실제 DB 기준 scatter를 계산했습니다. n=375, corr=-0.272273.

### 2026-04-29T23:08:05.035095+00:00 - chat
- prompt: PRODA CD_GATE box plot 그려줘
- intent: dashboard_box_chart
- llm_used: 
- answer: PRODA CD_GATE INLINE 분포를 root_lot_id별 box plot으로 그렸습니다. group=12, item match=CD_GATE.

### 2026-04-29T23:08:05.251688+00:00 - chat
- prompt: PRODA ET VTH WF map 그려줘
- intent: dashboard_wafer_map_chart
- llm_used: 
- answer: PRODA ET VTH을 shot_x/shot_y 기준 median으로 집계해 WF map을 그렸습니다. points=5, item match=VTH.

### 2026-04-29T23:08:05.543780+00:00 - chat
- prompt: PRODA Inline CD와 ET LKG Corr scatter KNOB_SPLIT B 제외하고 컬러링
- intent: dashboard_scatter_plan
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE, ML_TABLE - 감지 metric 후보: LKG, CD, KNOB_SPLIT - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 실제 DB 기준 scatter를 계산했습니다. n=375, corr=-0.272273.

### 2026-04-29T23:08:05.635343+00:00 - chat
- prompt: 테이블맵 relation에서 inline item과 knob 연결 보여줘
- intent: tablemap_guidance
- llm_used: 
- answer: 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=PRODA, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-04-29T23:09:34.712184+00:00 - chat
- prompt: ㅁㅁㅁ 회의 결정사항 보여줘
- intent: meeting_recall_summary
- llm_used: 
- answer: ㅁㅁㅁ 기준 회의 기록 3건을 날짜별로 정리했습니다. 회의관리/변경점 관리의 저장된 기록만 사용했습니다.

### 2026-04-29T23:10:54.092063+00:00 - chat
- prompt: ㅁㅁㅁ 회의 2차 날짜랑 시간이 어떻게돼? 아젠다는?
- intent: meeting_recall_summary
- llm_used: True
- answer: - 2차 회의 날짜: 2026-05-20 - 시간: 현재 로컬 데이터에 시간 정보 없음(확인 필요) - 아젠다: TEST, ㅁㄴㅇ 참고: 자세한 목록을 보려면 회의관리 열기/표 확인을 선택해 주세요.

### 2026-04-29T23:11:17.653650+00:00 - chat
- prompt: 2차 회의 회의록 정리해줘
- intent: meeting_recall_summary
- llm_used: 
- answer: 2차 / 정리해줘 기준 회의 기록 3건을 날짜별로 정리했습니다. 회의관리/변경점 관리의 저장된 기록만 사용했습니다.

### 2026-05-01T00:57:07.111183+00:00 - chat
- prompt: A1000 #1000 1.0 STI plan이 뭐야?
- intent: splittable_guidance
- llm_used: True
- answer: 현재 감지: lot A1000, product 미확정. 스플릿 테이블 진행을 위해 아래 3가지 중 하나를 선택해 주세요. 1) product를 PRODA로 지정, lot=A1000, wafer은 전체(all)로 확인 2) product를 지정하지 않고, lot=A1000을 모든 product에서 검색 3) 특정 product 이름을 알려주시면 그 product로 A1000 로트를 확인 선택지 번호를 말씀해 주시면 바로 open_splittable로 진행합니다. DB/파일 수정 없이 조회/표시만 수행합니다.

### 2026-05-01T03:20:24.088065+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: et_wafer_median
- llm_used: 
- answer: ET 레포트 단위기능으로 처리하는 요청입니다. - 실행 경로: query_et - 필요한 조건: product, root_lot_id or lot_id, step_id, item_id - 현재 감지: product=PRODA, lot=A1000, step=- - 추가로 필요: step_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.
