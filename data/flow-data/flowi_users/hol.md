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

### 2026-05-01T22:45:13.288631+00:00 - chat
- prompt: PRODA A1001 10.0 CONTACT에 plan 세운거 다 제대로 됐어?
- intent: splittable_guidance
- llm_used: True
- answer: 다음과 같이 처리 중입니다. - 상태 요약: PRODA A1001 10.0 CONTACT에 대한 plan은 스플릿 테이블로 확인하는 중이며, 실행 경로는 open_splittable입니다. DB/파일 원본 수정은 하지 않습니다. - 현재 감지 정보: product=PRODA, lot=A1001, step=-, terms=A1001, CONTACT, PLAN - 필요한 조건(다음 실행 시 요구): product, root_lot_id, wafer_id 또는 all, KNOB/MASK/FAB 등 파라미터 접두사 - 다음 가능한 작업(다음 액션) 1) open_splittable: 스플릿 테이블 열기 2) inspect_table: 표 확인 원하시는 실행을 골라 주시면 바로 진행하겠습니다.

### 2026-05-01T22:46:11.452638+00:00 - chat
- prompt: PRODA A1001 plan 세운거 뭐있어?
- intent: splittable_guidance
- llm_used: True
- answer: 현재 로컬 결과에 따르면 PRODA A1001의 PLAN은 스플릿 테이블 열기(open_splittable) 경로로 처리 준비 중입니다. 감지 정보: product=PRODA, lot=A1001, terms=PLAN. DB/파일 원본은 수정하지 않습니다. 다음 중 실행할 항목을 선택해 주세요: 1) open_splittable로 열기 (현재 감지 정보로 바로 진행) 2) inspect_table로 표 확인 (계산된 plan vs actual 매트릭스 확인) 3) 추가 조건 지정하기 (예: wafer_id/all, KNOB/MASK/FAB 접두사 등 파라미터 보완) 원하시는 번호를 알려주시면 바로 진행하겠습니다.

### 2026-05-01T22:46:38.952137+00:00 - chat
- prompt: 2
- intent: splittable_guidance
- llm_used:
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=-, step=- - 추가로 필요: product, root_lot_id/lot_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-01T22:47:21.524678+00:00 - chat
- prompt: A1001 plan 세운거 보여줘
- intent: splittable_guidance
- llm_used: True
- answer: 요약 - 로컬 결과로 A1001 PLAN 관련 작업을 준비 중입니다. 다만 product가 아직 입력되지 않아 바로 실행은 불가합니다. - 감지 정보: lot = A1001, terms = [A1001, PLAN], intent = splittable_guidance 다음 중에서 선택해 주세요 (1/2/3) 1) open_splittable — 스플릿 테이블 열기 - 필요 입력: product, root_lot_id(A1001), wafer_id 또는 all, KNOB/MASK/FAB 등의 프리픽스 2) inspect_table — 표 확인 - 현재 로컬 표를 확인합니다. product 미입력 상태에서도 표 내용을 볼 수 있습니다. 3) product 지정 후 진행 - 예: product을 명시하고 진행하기 원하시면 제품명을 입력해 주세요. 그 즉시 open_splittable로 진행합니다.

### 2026-05-01T22:47:54.215902+00:00 - chat
- prompt: PRODA야 2
- intent: splittable_guidance
- llm_used:
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=PRODA, lot=-, step=- - 추가로 필요: root_lot_id/lot_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-02T10:59:42.133662+00:00 - chat
- prompt: A1001 인폼로그 남기고싶어
- intent: inform_guidance
- llm_used: True
- answer: 현재 감지 정보: lot=A1001, terms=A1001. 필요한 값은 product, root_lot_id, message/reason입니다. 다음 중 하나를 선택해 주세요. 1) product를 입력하고 바로 인폼 로그 생성(open_inform) 진행 - 예시: PRODA 2) root_lot_id를 A1001로 확정하고 message/reason를 입력해 주세요 - 메시지 내용 예시를 함께 보내주시면 바로 구성합니다. 3) 메시지 본문 초안이 필요하면 템플릿으로 제안해 드립니다 - 원하시는 톤/목적(공유, 기록, 특정 이슈 언급 등)을 알려주시면 맞춤 템플릿을 드립니다. 선택 번호를 알려주시면 바로 진행하겠습니다. 필요 시 표 확인(inspect_table) 옵션도 함께 안내드릴게요.

### 2026-05-02T11:01:22.571413+00:00 - app_write_draft
- prompt: A1001 인폼로그 남겨줘
- intent: inform_create_needs_context
- feature: inform
- answer: 인폼 로그 등록에 필요한 조건이 부족합니다. 추가로 필요한 값: 인폼 내용

### 2026-05-02T11:01:24.923566+00:00 - app_write_draft
- prompt: 인폼 로그 등록 필수값:
- intent: inform_create_needs_context
- feature: inform
- answer: 인폼 로그 등록에 필요한 조건이 부족합니다. 추가로 필요한 값: lot_id 또는 wafer_id

### 2026-05-02T11:01:37.476186+00:00 - app_write_missing_followup
- prompt: PRODA A1001
- intent: inform_create_from_missing_context
- feature: inform
- answer: 부족한 값을 반영해서 등록했습니다. 인폼을 바로 등록했습니다. - id: inf_260502_64b8f8 - lot/wafer: A1001 / A1001 - 내용: 로그

### 2026-05-03T12:45:50.721016+00:00 - chat
- prompt: A1003 인폼 작성하고싶어
- intent: inform_guidance
- selected_function: open_inform
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 11597
- llm_used:
- answer: 인폼 로그 단위기능으로 처리하는 요청입니다. - 실행 경로: open_inform - 필요한 조건: product, root_lot_id, message/reason - 현재 감지: product=-, lot=A1003, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-03T13:44:11.148130+00:00 - chat
- prompt: 파일탐색기에서 최근 10행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids:
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 10698
- llm_used:
- answer: DB preview에 필요한 source/product를 보완해 주세요.

### 2026-05-03T13:44:20.280072+00:00 - chat
- prompt: 파일탐색기에서 최근 10행 보여줘 PRODA
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids:
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 9086
- llm_used:
- answer: DB preview에 필요한 source/product를 보완해 주세요.

### 2026-05-03T13:44:36.263688+00:00 - chat
- prompt: 파일탐색기에서 최근 10행 보여줘 PRODA FAB
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 15964
- llm_used:
- answer: DB preview 실패: did not find column step_seq, consider passing `missing_columns='insert'`

### 2026-05-03T13:46:39.695786+00:00 - chat
- prompt: PRODA A1003 인폼 남기고싶어
- intent: inform_guidance
- selected_function: open_inform
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 8446
- llm_used:
- answer: 인폼 로그 단위기능으로 처리하는 요청입니다. - 실행 경로: open_inform - 필요한 조건: product, root_lot_id, message/reason - 현재 감지: product=PRODA, lot=A1003, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-03T13:46:54.520742+00:00 - chat
- prompt: PRODA A1003 인폼 남기고싶어
- intent: inform_guidance
- selected_function: open_inform
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 8563
- llm_used:
- answer: 인폼 로그 단위기능으로 처리하는 요청입니다. - 실행 경로: open_inform - 필요한 조건: product, root_lot_id, message/reason - 현재 감지: product=PRODA, lot=A1003, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-03T14:43:47.948808+00:00 - chat
- prompt: A1001A.1 #8,9 24.0 SORT AAA로 plan 짜줘
- intent: splittable_guidance
- selected_function: open_splittable
- retrieved_ids:
- system_knowledge_ids:
- result_status: error
- elapsed_ms: 26774
- llm_used:
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=-, lot=A1001A.1, step=24.0 SORT Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-03T14:44:25.404891+00:00 - chat
- prompt: PRODA A1000 #21 현재 lot_id가 뭐야
- intent: current_fab_lot_lookup
- selected_function: current_fab_lot_lookup
- retrieved_ids:
- system_knowledge_ids:
- result_status: error
- elapsed_ms: 14800
- llm_used:
- answer: FAB DB fab_lot_id 조회 실패: extra column in file outside of expected schema: process_id, hint: specify this column in the schema, or pass extra_columns='ignore' in scan options. File containing extra column: 'D:/TEST_Making_Video/semi_all/flow/data/Fab/1.RAWDATA_DB_FAB/PRODA/date=20240418/part_0.parquet'.

### 2026-05-03T14:44:54.352371+00:00 - splittable_lot_note
- prompt: A1001 #6 이상WF 꼬리표 달아줘
- intent: splittable_lot_note_create_draft
- feature: splittable
- answer: 스플릿 테이블 lot 꼬리표 등록 준비가 됐습니다. 확인 선택을 누르면 실제로 등록합니다.

### 2026-05-03T14:45:06.015237+00:00 - splittable_lot_note
- prompt: FLOWI_SPLITTABLE_NOTE {"scope": "wafer", "product": "ML_TABLE_PRODA", "root_lot_id": "A1001", "wafer_ids": ["6"], "text": "이상WF", "username": "hol", "confirm": "SPLITTABLE_NOTE_CONFIRM::ML_TABLE_PRODA::A1001::wafer::6::이상WF"}
- intent: splittable_lot_note_create
- feature: splittable
- answer: 스플릿 테이블 꼬리표를 등록했습니다. - product: ML_TABLE_PRODA - lot: A1001 - 내용: 이상WF

### 2026-05-03T14:46:37.326639+00:00 - chat
- prompt: A1001 24.0 SORT Split 어떻게돼?
- intent: wafer_split_at_step
- selected_function: query_wafer_split_at_step
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 5402
- llm_used:
- answer: ML_TABLE_PRODA A1001 SplitTable 화면 기준으로 3개 row를 조회했습니다.

### 2026-05-03T14:47:12.884315+00:00 - inform_walkthrough
- prompt: A1003B.2 인폼남기고싶어
- intent: inform_walkthrough_start
- answer: 인폼 전체 작성에 필요한 root lot을 알려주세요.

### 2026-05-03T14:47:21.344306+00:00 - inform_walkthrough
- prompt: A1003B.2 인폼남기고싶어 A1003B.2 인폼남기고싶어 A1001
- intent: inform_walkthrough
- answer: GATE는 뭘로 할까요? (예: test1)

### 2026-05-03T14:47:54.314800+00:00 - inform_walkthrough
- prompt: A1003B.2 인폼남기고싶어 A1003B.2 인폼남기고싶어 A1001 test1
- intent: inform_walkthrough
- answer: STI는 뭘로 할까요?

### 2026-05-03T14:47:57.003727+00:00 - inform_walkthrough
- prompt: A1003B.2 인폼남기고싶어 A1003B.2 인폼남기고싶어 A1001 test1 test1
- intent: inform_walkthrough
- answer: PC는 뭘로 할까요?

### 2026-05-03T14:47:59.810413+00:00 - inform_walkthrough
- prompt: A1003B.2 인폼남기고싶어 A1003B.2 인폼남기고싶어 A1001 test1 test1 test
- intent: inform_walkthrough
- answer: MOL는 뭘로 할까요?

### 2026-05-03T22:31:40.204035+00:00 - inform_log_draft
- prompt: A1004A.2 A1004A.3 인폼 남겨줘
- intent: inform_log_draft
- answer: 인폼 등록 초안에 필요한 값을 선택해 주세요.

### 2026-05-03T22:31:56.059825+00:00 - inform_log_draft
- prompt: A1004A.2 A1004A.3 인폼 남겨줘 BEOL
- intent: inform_log_draft
- answer: 인폼 등록 초안에 필요한 값을 선택해 주세요.

### 2026-05-03T22:32:08.880439+00:00 - inform_log_draft
- prompt: A1004A.2 A1004A.3 인폼 남겨줘
- intent: inform_log_draft
- answer: 인폼 등록 초안에 필요한 값을 선택해 주세요.

### 2026-05-03T22:32:22.048366+00:00 - inform_log_draft
- prompt: A1004A.2 A1004A.3 인폼 남겨줘 A1004A.2 A1004A.3 인폼 남겨줘 BEOL
- intent: inform_log_draft
- answer: 인폼 등록 초안에 필요한 값을 선택해 주세요.

### 2026-05-03T22:32:56.182642+00:00 - inform_log_draft
- prompt: A1004A.2 A1004A.3 인폼 남겨줘 A1004A.2 A1004A.3 인폼 남겨줘 BEOL A1004A.2 A1004A.3 인폼 남겨줘 A1004A.2 A1004A.3 인폼 남겨줘 BEOL 인폼 내용: beol 인폼 남깁니다
- intent: inform_log_batch_draft
- answer: 인폼 2건을 등록 전 미리보기로 만들었습니다. 확인 전에는 저장하지 않습니다.

### 2026-05-03T22:33:02.369063+00:00 - inform_log_draft
- prompt: FLOWI_INFORM_CONFIRM {"draft_id": "draft_20260503T223256_67c7baec", "confirm": true}
- intent: inform_log_registered
- answer: 인폼 2건을 등록했습니다.

### 2026-05-03T22:36:14.669214+00:00 - chat
- prompt: A1004A.2 24.0 SORT Split 어떻게돼?
- intent: wafer_split_at_step
- selected_function: query_wafer_split_at_step
- retrieved_ids:
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 7996
- llm_used:
- answer: wafer split 조회에 필요한 값을 보완해 주세요.

### 2026-05-03T22:36:40.562787+00:00 - chat
- prompt: A1004A.2 24.0 SORT Split 어떻게돼? A1004A.2 24.0 SORT Split 어떻게돼? 1.RAWDATA_DB_FAB
- intent: wafer_split_at_step
- selected_function: query_wafer_split_at_step
- retrieved_ids:
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 21381
- llm_used:
- answer: wafer split 조회에 필요한 값을 보완해 주세요.

### 2026-05-03T22:37:30.805176+00:00 - chat
- prompt: A1004A.2 24.0 SORT Split 어떻게돼? A1004A.2 24.0 SORT Split 어떻게돼? 1.RAWDATA_DB_FAB A1004A.2 24.0 SORT Split 어떻게돼? A1004A.2 24.0 SORT Split 어떻게돼? 1.RAWDATA_DB_FAB 1.RAWDATA_DB_FAB
- intent: wafer_split_at_step
- selected_function: query_wafer_split_at_step
- retrieved_ids:
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 46567
- llm_used:
- answer: wafer split 조회에 필요한 값을 보완해 주세요.

### 2026-05-03T22:38:39.011383+00:00 - app_write_draft
- prompt: A1004A.2에 이상랏이라고 꼬리표 달아줘
- intent: lot_wafer_annotation_draft
- feature: tracker
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-05-03T22:41:34.694810+00:00 - chat
- prompt: A1004A.2 이상 랏이라고 노트 달아줘
- intent: lot_anomaly_summary
- selected_function: lot_anomaly_summary
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 113797
- llm_used: True
- answer: 요청하신 A1004A.2 이상 랏 노트: ET/INLINE 로우를 찾지 못했습니다. 추가 정보가 필요합니다. 아래 중 빠진 조건을 하나 선택해 주세요. 1) product와 root_lot_id를 알려주기 2) wafer_id를 알려주기 3) step 또는 item 정보를 알려주기 답은 1/2/3 중 하나로 보내 주세요.

### 2026-05-03T22:42:06.893029+00:00 - app_write_draft
- prompt: A1004A.2 이상랏이라고 꼬리표달아줘
- intent: lot_wafer_annotation_draft
- feature: tracker
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.

### 2026-05-03T23:15:59.069104+00:00 - chat
- prompt: A1004A.2 24.0 SORT Split 어떻게돼?
- intent: wafer_split_at_step
- selected_function: query_wafer_split_at_step
- retrieved_ids:
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 9021
- llm_used:
- answer: wafer split 조회에 필요한 값을 보완해 주세요.

### 2026-05-03T23:16:20.959828+00:00 - chat
- prompt: A1004A.2 24.0 SORT Split 어떻게돼? product: ML_TABLE 스플릿테이블이야
- intent: wafer_split_at_step
- selected_function: query_wafer_split_at_step
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 11050
- llm_used:
- answer: ML_TABLE parquet을 찾지 못했습니다.

### 2026-05-10T23:41:44.282944+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 4265
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778456500004

### 2026-05-10T23:41:44.662757+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 3990
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778456500604

### 2026-05-10T23:45:14.380378+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2542
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778456711746

### 2026-05-10T23:45:17.674892+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2584
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778456715083

### 2026-05-10T23:46:40.458614+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2612
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778456797837

### 2026-05-10T23:46:59.914938+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2477
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778456817427

### 2026-05-11T09:58:29.645547+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2778
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778493506860

### 2026-05-11T12:08:54.351636+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2674
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778501331669

### 2026-05-11T12:14:20.975962+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2362
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778501658606

### 2026-05-11T12:30:37.656125+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2519
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778502635130

### 2026-05-11T13:21:36.420986+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2545
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778505693868

### 2026-05-11T13:21:40.721923+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2605
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778505698112

### 2026-05-11T13:21:43.743254+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2591
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778505701147

### 2026-05-11T14:16:15.006342+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2682
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778508972312

### 2026-05-11T14:26:48.356594+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2535
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778509605817

### 2026-05-11T14:27:20.902385+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2487
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778509638410

### 2026-05-11T14:30:49.316686+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2460
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778509846848

### 2026-05-11T14:37:31.820529+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2830
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778510248986

### 2026-05-11T22:31:10.144992+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2481
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778538667655

### 2026-05-11T23:13:37.106659+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2539
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778541214561

### 2026-05-11T23:19:24.760536+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2437
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778541562316

### 2026-05-12T13:05:45.269223+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2606
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778591142657

### 2026-05-12T13:19:21.152093+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2530
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778591958616

### 2026-05-12T13:22:30.160946+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2532
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: agent_page
- client_run_id: agent_page_1778592147623

### 2026-05-14T14:00:27.064532+00:00 - chat
- prompt: 지난 GAA Gate Vt shift 회의 결정사항 정리해줘
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1881
- llm_used:
- answer: GAA / Gate / Vt / shift / 정리해줘 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 20건을 찾았습니다. 결정사항 - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Gate pre-clean split은 PRODA A1000에 한해 hold / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Inline Vt monitor pass 전까지 release 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] GATE release 전 MOL/BEOL 확대 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 inform apply_confirmed 이후 calendar event done 처리 / 담당 - / 상태 calendar_pushed - 2026-05-15 / [실전테스트] Cross-module change control / 1차 / [실전테스트] GATE release 전 MOL/BEOL 확대 금지 / 담당 - / 상태 calendar_pushed - 2026-05-15 / [실전테스트] Cross-module change control / 1차 / [실전테스트] 모듈별 inform apply_confirmed 이후 calendar event done 처리 / 담당 - / 상태 calendar_pushe
- source_ai: codex
- client_run_id: realistic-change-smoke-1778767212-1

### 2026-05-14T14:00:28.684460+00:00 - chat
- prompt: 변경점 관련 회의록과 캘린더 일정 같이 불러와줘
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1564
- llm_used:
- answer: 변경점 / 관련 / 캘린더 / 일정 / 같 / 불러와줘 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 61건을 찾았습니다. 결정사항 - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Gate pre-clean split은 PRODA A1000에 한해 hold / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Inline Vt monitor pass 전까지 release 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] GATE release 전 MOL/BEOL 확대 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 inform apply_confirmed 이후 calendar event done 처리 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] CA/VIA etch clean change는 Kelvin monitor 통과 후 확대 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] Rc guard band 초과 시 기존 clean recipe로 rollback / 담당
- source_ai: codex
- client_run_id: realistic-change-smoke-1778767212-2

### 2026-05-14T14:00:35.656077+00:00 - chat
- prompt: PRODA A1000 관련 이슈추적 목적과 회의 액션 보여줘
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 6929
- llm_used:
- answer: PRODA / A1000 / 관련 / 슈추적 / 목적 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 32건을 찾았습니다. 액션아이템 - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 1차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 2차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 1차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 1차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-16 / [실전테스트] Cross-module change control / 1차 / [실전테스트] A10
- source_ai: codex
- client_run_id: realistic-change-smoke-1778767212-3

### 2026-05-14T14:00:37.435944+00:00 - app_write_draft
- prompt: GATE 모듈 인폼로그 최근 변경 적용 상태 요약해줘
- intent: inform_create_draft
- feature: inform
- answer: 이 요청은 기존 기록의 수정/변경 또는 권한 확인이 필요한 작업입니다. 변경 전에는 반드시 대상 화면에서 권한과 내용을 확인해야 합니다. 원본 DB/Files는 수정하지 않습니다.
- source_ai: codex
- client_run_id: realistic-change-smoke-1778767212-4

### 2026-05-14T14:00:39.366412+00:00 - chat
- prompt: CA/VIA 변경점 회의에서 나온 액션아이템 뭐였어?
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1851
- llm_used:
- answer: CA / VIA / 변경점 / 나온 / 뭐였어 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 40건을 찾았습니다. 액션아이템 - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 1차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 2차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 1차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 1차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-16 / [실전테스트] Cross-module change control / 1차 / [실전테스트] A1001C.
- source_ai: codex
- client_run_id: realistic-change-smoke-1778767212-5

### 2026-05-14T14:02:26.053735+00:00 - chat
- prompt: 지난 GAA Gate Vt shift 회의 결정사항 정리해줘
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2043
- llm_used:
- answer: GAA / Gate / Vt / shift / 정리해줘 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 20건을 찾았습니다. 결정사항 - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Gate pre-clean split은 PRODA A1000에 한해 hold / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Inline Vt monitor pass 전까지 release 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] GATE release 전 MOL/BEOL 확대 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 inform apply_confirmed 이후 calendar event done 처리 / 담당 - / 상태 calendar_pushed - 2026-05-15 / [실전테스트] Cross-module change control / 1차 / [실전테스트] GATE release 전 MOL/BEOL 확대 금지 / 담당 - / 상태 calendar_pushed - 2026-05-15 / [실전테스트] Cross-module change control / 1차 / [실전테스트] 모듈별 inform apply_confirmed 이후 calendar event done 처리 / 담당 - / 상태 calendar_pushe
- source_ai: codex
- client_run_id: realistic-change-smoke-verify-1778767343-1

### 2026-05-14T14:02:28.329370+00:00 - chat
- prompt: 변경점 관련 회의록과 캘린더 일정 같이 불러와줘
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2215
- llm_used:
- answer: 변경점 / 관련 / 캘린더 / 일정 / 같 / 불러와줘 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 61건을 찾았습니다. 결정사항 - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Gate pre-clean split은 PRODA A1000에 한해 hold / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] GAA Gate Vt shift change control / 2차 / [실전테스트] Inline Vt monitor pass 전까지 release 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] GATE release 전 MOL/BEOL 확대 금지 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 inform apply_confirmed 이후 calendar event done 처리 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] CA/VIA etch clean change는 Kelvin monitor 통과 후 확대 / 담당 - / 상태 calendar_pushed - 2026-05-16 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] Rc guard band 초과 시 기존 clean recipe로 rollback / 담당
- source_ai: codex
- client_run_id: realistic-change-smoke-verify-1778767343-2

### 2026-05-14T14:02:34.463906+00:00 - chat
- prompt: PRODA A1000 관련 이슈추적 목적과 회의 액션 보여줘
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 6034
- llm_used:
- answer: PRODA / A1000 / 관련 / 슈추적 / 목적 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 32건을 찾았습니다. 액션아이템 - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 1차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 2차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 1차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 1차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-16 / [실전테스트] Cross-module change control / 1차 / [실전테스트] A10
- source_ai: codex
- client_run_id: realistic-change-smoke-verify-1778767343-3

### 2026-05-14T14:02:36.239843+00:00 - inform_summary
- prompt: GATE 모듈 인폼로그 최근 변경 적용 상태 요약해줘
- intent: inform_module_recent_summary
- answer: GATE 모듈 인폼로그 최근 상태 요약 - 조건에 맞는 인폼 63건을 찾았습니다. - apply_confirmed 2건 / mail_completed 2건 / registered 44건 인폼 상태 - 2026-05-14T23:00 / PRODA / A1000A.1 / registered / [실전테스트] GATE 변경 적용 상태 - 2026-05-14T23:00 / PRODA / A1001C.1 / apply_confirmed / [실전테스트] GATE 변경 적용 상태 - 2026-05-14T23:00 / PRODA / A1002D.4 / mail_completed / [실전테스트] GATE 변경 적용 상태 - 2026-05-14T23:00 / PRODA / A1000A.1 / apply_confirmed / [실전테스트] GATE 변경 적용 상태 - 2026-05-14T23:00 / PRODA / A1000A.1 / mail_completed / [실전테스트] GATE 변경 적용 상태 - 2026-05-14T08:19 / ML_TABLE_PRODA / A1000A.3 / registered / 재측정 - 2026-05-14T07:53 / ML_TABLE_PRODA / A1000A.3 / registered / 재측정 - 2026-05-14T07:18 / ML_TABLE_PRODA / A1000A.3 / registered / 재측정 - 외 40건은 표에서 확인하세요. 근거 - /api/informs/recent와 같은 Inform 저장소의 visible root inform만 사용했습니다.
- source_ai: codex
- client_run_id: realistic-change-smoke-verify-1778767343-4

### 2026-05-14T14:02:38.045162+00:00 - chat
- prompt: CA/VIA 변경점 회의에서 나온 액션아이템 뭐였어?
- intent: meeting_recall_summary
- selected_function: query_meeting_calendar_records
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1757
- llm_used:
- answer: CA / VIA / 변경점 / 나온 / 뭐였어 회의/변경점 기록 요약 - 회의관리/변경점 관리 저장 기록 40건을 찾았습니다. 액션아이템 - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 1차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-18 / [실전테스트] BEOL barrier split readiness / 2차 / [실전테스트] Reliability quick check 결과 공유 / 담당 QA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 1차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 1차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-17 / [실전테스트] Cross-module change control / 2차 / [실전테스트] 모듈별 decision log를 하나의 변경점 event에 연결 / 담당 INTEGRATION / 상태 pending - 2026-05-17 / [실전테스트] CA/VIA contact resistance review / 2차 / [실전테스트] VIA chain fail map을 변경점 캘린더에 첨부 / 담당 VIA / 상태 pending - 2026-05-16 / [실전테스트] Cross-module change control / 1차 / [실전테스트] A1001C.
- source_ai: codex
- client_run_id: realistic-change-smoke-verify-1778767343-5

### 2026-05-14T15:08:18.551200+00:00 - chat
- prompt: A1000가 지금 lot_id가 어떻게돼?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: missing
- elapsed_ms: 3985
- llm_used:
- answer: 현재 fab_lot_id를 FAB DB에서 찾으려면 product가 필요합니다. 예: `PRODA A1000 #6 현재 fab lot id가 뭐야?`

### 2026-05-14T15:08:36.045820+00:00 - chat
- prompt: A1000가 지금 lot_id가 어떻게돼? product: PRODA야
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 3263
- llm_used:
- answer: PRODUCT FAB parquet을 찾지 못했습니다. DB root와 product명을 확인해주세요.

### 2026-05-14T22:30:21.497301+00:00 - blocked_write_request
- prompt: raw DB 파일을 직접 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.
- source_ai: scenario

### 2026-05-14T22:30:24.561234+00:00 - admin_file_operation
- prompt: Files root 파일 내용을 바꾸는 초안을 만들어줘
- action: confirm_file_operation
- requires_confirmation: True
- blocked:
- answer: Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.
- source_ai: scenario

### 2026-05-14T22:30:43.106350+00:00 - chat
- prompt: PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 17248
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-14T22:30:47.489298+00:00 - chat
- prompt: PRODA FAB 최근 3행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 3051
- llm_used:
- answer: DB preview 실패: data type mismatch for column value: incoming: String != target: Float64
- source_ai: scenario

### 2026-05-14T22:30:51.947079+00:00 - inform_walkthrough
- prompt: A1004 인폼전체 작성해줘
- intent: inform_walkthrough
- answer: GATE의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)
- source_ai: scenario

### 2026-05-14T22:31:01.961771+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 8714
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: scenario

### 2026-05-14T22:35:03.944654+00:00 - blocked_write_request
- prompt: raw DB 파일을 직접 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.
- source_ai: scenario

### 2026-05-14T22:35:07.455436+00:00 - admin_file_operation
- prompt: Files root 파일 내용을 바꾸는 초안을 만들어줘
- action: confirm_file_operation
- requires_confirmation: True
- blocked:
- answer: Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.
- source_ai: scenario

### 2026-05-14T22:35:27.093047+00:00 - chat
- prompt: PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 18022
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-14T22:35:31.541877+00:00 - chat
- prompt: PRODA FAB 최근 3행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 2798
- llm_used:
- answer: DB preview 실패: data type mismatch for column value: incoming: String != target: Float64
- source_ai: scenario

### 2026-05-14T22:35:46.355257+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 7928
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: scenario

### 2026-05-15T14:23:54.525199+00:00 - chat
- prompt: A1001 24.0 SORT KNOB만 보여줘
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 5042
- llm_used:
- answer: A1001 / 24.0 SORT / KNOB 조건으로 ML_TABLE을 조회했습니다. 결과 25건입니다.

### 2026-05-15T14:24:10.010117+00:00 - chat
- prompt: 스플릿 테이블 형태로 보여줘
- intent: dashboard_chart_draft_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'ml_table_proda.lot_wf', 'proda_dashboard_query_manual', 'ml_table_proda.wafer_id', 'ml_table_proda.step_id', 'ml_table_proda.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids:
- retrieval_score: 39.0
- result_status: missing
- elapsed_ms: 1671
- llm_used:
- answer: 테이블 초안을 만들었습니다. 부족한 값은 편집 모달에서 보완할 수 있습니다.

### 2026-05-15T14:47:37.715769+00:00 - inform_walkthrough
- prompt: A1001 인폼 남기고싶어
- intent: inform_walkthrough
- answer: GATE의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)

### 2026-05-15T14:48:15.396704+00:00 - inform_walkthrough
- prompt: split_set: 24.0 SORT들어간거로 하고싶어
- intent: inform_walkthrough
- answer: STI의 SplitTable은 어떤 Split으로 진행할까요?

### 2026-05-15T22:44:24.407901+00:00 - blocked_write_request
- prompt: raw DB 파일을 직접 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.
- source_ai: scenario

### 2026-05-15T22:44:27.733487+00:00 - admin_file_operation
- prompt: Files root 파일 내용을 바꾸는 초안을 만들어줘
- action: confirm_file_operation
- requires_confirmation: True
- blocked:
- answer: Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.
- source_ai: scenario

### 2026-05-15T22:44:52.546941+00:00 - chat
- prompt: PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 23160
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-15T22:44:56.979107+00:00 - chat
- prompt: PRODA A1002 KNOB TABLE 보여줘
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 7569
- llm_used:
- answer: A1002 / KNOB 조건으로 ML_TABLE을 조회했습니다. 결과 25건입니다.
- source_ai: scenario

### 2026-05-15T22:45:05.664244+00:00 - chat
- prompt: PRODA FAB 최근 3행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 5876
- llm_used:
- answer: DB preview 실패: data type mismatch for column value: incoming: String != target: Float64
- source_ai: scenario

### 2026-05-15T22:45:10.560051+00:00 - inform_walkthrough
- prompt: A1004 인폼전체 작성해줘
- intent: inform_walkthrough
- answer: GATE의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)
- source_ai: scenario

### 2026-05-15T22:45:27.059016+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'proda_dibl_ss_rca_issue', 'proda_a1001_w07_wafer_signal']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 14377
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: scenario

### 2026-05-15T22:46:51.016590+00:00 - chat
- prompt: ML_TABLE_PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 12128
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-15T22:49:04.178278+00:00 - blocked_write_request
- prompt: raw DB 파일을 직접 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.
- source_ai: scenario

### 2026-05-15T22:49:07.741271+00:00 - admin_file_operation
- prompt: Files root 파일 내용을 바꾸는 초안을 만들어줘
- action: confirm_file_operation
- requires_confirmation: True
- blocked:
- answer: Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.
- source_ai: scenario

### 2026-05-15T22:49:31.311863+00:00 - chat
- prompt: PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 21700
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-15T22:49:43.062038+00:00 - chat
- prompt: PRODA A1002 KNOB TABLE 보여줘
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 9544
- llm_used:
- answer: A1002 / KNOB 조건으로 ML_TABLE을 조회했습니다. 결과 25건입니다.
- source_ai: scenario

### 2026-05-15T22:49:50.966207+00:00 - chat
- prompt: PRODA FAB 최근 3행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 4929
- llm_used:
- answer: DB preview 실패: data type mismatch for column value: incoming: String != target: Float64
- source_ai: scenario

### 2026-05-15T22:49:55.312040+00:00 - inform_walkthrough
- prompt: A1004 인폼전체 작성해줘
- intent: inform_walkthrough
- answer: GATE의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)
- source_ai: scenario

### 2026-05-15T22:50:09.499618+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'proda_dibl_ss_rca_issue', 'proda_a1001_w07_wafer_signal']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 12354
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: scenario

### 2026-05-16T14:35:55.924168+00:00 - chat
- prompt: A1001A.1 10.0 들어간 KNOB 보여줘 스플릿테이블 형식으로
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 3389
- llm_used:
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB 기준으로 24개 row를 조회했습니다.

### 2026-05-16T14:36:13.703013+00:00 - chat
- prompt: KNOB_10.0 CONTACT만 보여줘
- intent: lot_knobs
- selected_function: lot_knobs
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1875
- llm_used:
- answer: KNOB 조회는 lot/root lot 조건이 필요합니다. 예: A1002, A1005, A1006, A1007, A1012, A1019, A1021, A1028

### 2026-05-16T15:02:24.917897+00:00 - blocked_write_request
- prompt: raw DB 파일을 직접 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.
- source_ai: scenario

### 2026-05-16T15:02:28.987049+00:00 - admin_file_operation
- prompt: Files root 파일 내용을 바꾸는 초안을 만들어줘
- action: confirm_file_operation
- requires_confirmation: True
- blocked:
- answer: Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.
- source_ai: scenario

### 2026-05-16T15:02:55.482748+00:00 - chat
- prompt: PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 24540
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-16T15:03:08.747775+00:00 - chat
- prompt: PRODA A1002 KNOB TABLE 보여줘
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 10599
- llm_used:
- answer: A1002 / KNOB 조건으로 ML_TABLE을 조회했습니다. 결과 25건입니다.
- source_ai: scenario

### 2026-05-16T15:03:18.526995+00:00 - chat
- prompt: PRODA FAB 최근 3행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 6319
- llm_used:
- answer: DB preview 실패: data type mismatch for column value: incoming: String != target: Float64
- source_ai: scenario

### 2026-05-16T15:03:23.766027+00:00 - inform_walkthrough
- prompt: A1004 인폼전체 작성해줘
- intent: inform_walkthrough
- answer: GATE의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)
- source_ai: scenario

### 2026-05-16T15:03:40.975008+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'proda_dibl_ss_rca_issue', 'proda_a1001_w07_wafer_signal']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 14901
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: scenario

### 2026-05-16T15:14:12.355819+00:00 - chat
- prompt: A1001 #3 EA100030 IOFF Trend 그려줘
- intent: lot_anomaly_summary
- selected_function: lot_anomaly_summary
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 12059
- llm_used: True
- answer: A1001에 해당하는 ET/INLINE row를 찾지 못했습니다.

### 2026-05-16T15:15:08.728690+00:00 - chat
- prompt: A1000 #3
- intent: filebrowser_guidance
- selected_function: open_filebrowser
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 99029
- llm_used:
- answer: 파일 탐색기 단위기능으로 처리하는 요청입니다. - 실행 경로: open_filebrowser - 필요한 조건: source/root, product or file, optional SQL/filter - 현재 감지: product=-, lot=A1000, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-16T15:16:40.630408+00:00 - chat
- prompt: PRODA EA100030 IOFF Trend 그려줘
- intent: semiconductor_diagnosis
- selected_function: run_semiconductor_diagnosis
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 5101
- llm_used: True
- answer: 반도체 진단/RCA 단위기능으로 보았지만 인식된 지표가 부족합니다. item명과 unit/test_structure를 더 알려주세요.

### 2026-05-16T15:57:55.642126+00:00 - chat
- prompt: EA100030 IOFF Trend 그려줘
- intent: semiconductor_diagnosis
- selected_function: run_semiconductor_diagnosis
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 33784
- llm_used: True
- answer: 반도체 진단/RCA 단위 기능으로 보았지만 인식된 지표가 부족합니다. item명과 unit/test_structure를 더 알려주세요.

### 2026-05-16T15:58:30.871378+00:00 - chat
- prompt: PRODA IOFF Trend 그려줘
- intent: semiconductor_diagnosis
- selected_function: run_semiconductor_diagnosis
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 34081
- llm_used: True
- answer: PRODA에 대한 반도체 진단/RCA 기능으로 보입니다. 하지만 인식된 지표가 부족합니다. item명과 unit/test_structure를 더 알려주세요.

### 2026-05-16T15:58:46.908533+00:00 - chat
- prompt: 24.0 SORT KNOB으로 컬러링해줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_sort_knob_split_rule', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'proda_dibl_ss_rca_issue', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: missing
- elapsed_ms: 14077
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ML_TABLE - 감지 metric 후보: SORT - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-16T15:58:57.226338+00:00 - chat
- prompt: PPID_24_3 제외하고 보여줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_dibl_ss_rca_issue', 'proda_a1001_lot_watch']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: missing
- elapsed_ms: 8054
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: PPID_24_3 - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-16T15:59:06.676812+00:00 - chat
- prompt: PRODA INLINE CD Trend 그려줘
- intent: dashboard_inline_trend_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 48.0
- result_status: missing
- elapsed_ms: 8055
- llm_used:
- answer: Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.

### 2026-05-16T15:59:19.059311+00:00 - chat
- prompt: PRODA Inline CD와 ET IOFF Corr scatter 그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_dibl_ss_rca_issue', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 51.0
- result_status: missing
- elapsed_ms: 10505
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE - 감지 metric 후보: LKG, IOFF, CD - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요.

### 2026-05-16T15:59:37.860396+00:00 - chat
- prompt: PRODA ET와 ML_TABLE 조인해서 scatter 차트 그려줘
- intent: dashboard_multisource_chart
- selected_function: dashboard.chart.llm.draft
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'ml_table_proda.wafer_id', 'proda_hold_release_decision', 'proda_dashboard_query_manual', 'proda_dibl_ss_rca_issue', 'proda_sort_knob_split_rule', 'dashboard_chart_generation_rules', 'knowledge_vault_overview']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 16778
- llm_used:
- answer: confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다. - source: 3개 - relation: 6개 - join key: root_lot_id, wafer_id - 결과: 421200행

### 2026-05-17T02:26:11.112517+00:00 - chat
- prompt: EA100030 IOFF Trend 그려줘
- intent: semiconductor_diagnosis
- selected_function: run_semiconductor_diagnosis
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 33424
- llm_used: True
- answer: 반도체 진단 기능으로 파악했으나, 인식된 지표가 부족합니다. 아이템명과 unit/test_structure를 더 알려주세요.

### 2026-05-17T02:26:43.461321+00:00 - chat
- prompt: PRODA IOFF Trend 그려줘
- intent: semiconductor_diagnosis
- selected_function: run_semiconductor_diagnosis
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 31173
- llm_used: True
- answer: PRODA에 대해 요청하셨습니다. 반도체 진단/RCA 기능으로 인식했지만 item명과 unit/test_structure 정보가 부족합니다. 이 정보들을 추가로 알려주세요.

### 2026-05-17T02:26:59.115912+00:00 - chat
- prompt: 24.0 SORT KNOB으로 컬러링해줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_sort_knob_split_rule', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_a1001_lot_watch', 'default_agent_wiki_seed_framework', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: missing
- elapsed_ms: 13645
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ML_TABLE - 감지 metric 후보: SORT - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-17T02:27:10.744046+00:00 - chat
- prompt: PPID_24_0_S1 제외하고 보여줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'gaa_beol_bspdn_power_delivery_basics']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: missing
- elapsed_ms: 9211
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: PPID_24_0_S1 - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-17T02:27:20.028044+00:00 - chat
- prompt: PRODA INLINE CD Trend 그려줘
- intent: dashboard_inline_trend_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 48.0
- result_status: missing
- elapsed_ms: 7806
- llm_used:
- answer: Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.

### 2026-05-17T02:27:34.125961+00:00 - chat
- prompt: PRODA Inline CD와 ET IOFF Corr scatter 그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.wafer_id', 'semiconductor_eight_major_processes_for_gaa', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 51.0
- result_status: missing
- elapsed_ms: 12065
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE - 감지 metric 후보: LKG, IOFF, CD - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요.

### 2026-05-17T02:27:54.969676+00:00 - chat
- prompt: PRODA ET와 ML_TABLE 조인해서 scatter 차트 그려줘
- intent: dashboard_multisource_chart
- selected_function: dashboard.chart.llm.draft
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'ml_table_proda.wafer_id', 'proda_hold_release_decision', 'proda_dashboard_query_manual', 'proda_dibl_ss_rca_issue', 'proda_sort_knob_split_rule', 'gaa_device_geometry_and_multi_vt_design', 'gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'gaa_device_evolution_and_purpose', 'dashboard_chart_generation_rules', 'knowledge_vault_overview']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 18631
- llm_used:
- answer: confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다. - source: 3개 - relation: 6개 - join key: root_lot_id, wafer_id - 결과: 421200행

### 2026-05-17T02:30:21.003776+00:00 - chat
- prompt: EA100030 IOFF Trend 그려줘
- intent: semiconductor_diagnosis
- selected_function: run_semiconductor_diagnosis
- retrieved_ids: ['proda_dashboard_query_manual', 'dashboard_chart_generation_rules']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 33402
- llm_used: True
- answer: 반도체 진단/RCA 단위 기능으로 보았지만, 인식된 지표가 부족합니다. item명과 unit/test_structure를 더 알려주세요.

### 2026-05-17T02:33:06.997206+00:00 - chat
- prompt: EA100030 IOFF Trend 그려줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids:
- retrieval_score: 45.0
- result_status: success
- elapsed_ms: 13334
- llm_used:
- answer: ET IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF step_id=EA100030.

### 2026-05-17T02:33:18.179917+00:00 - chat
- prompt: PRODA IOFF Trend 그려줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id', 'gaa_device_geometry_and_multi_vt_design']
- system_knowledge_ids:
- retrieval_score: 45.0
- result_status: success
- elapsed_ms: 9057
- llm_used:
- answer: PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF.

### 2026-05-17T02:33:32.373684+00:00 - chat
- prompt: 24.0 SORT KNOB으로 컬러링해줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'proda_sort_knob_split_rule', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_a1001_lot_watch', 'default_agent_wiki_seed_framework', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 11117
- llm_used:
- answer: 직전 chart session(86765eb6) 조건을 이어받았습니다. ET IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF step_id=EA100030.

### 2026-05-17T02:33:42.756593+00:00 - chat
- prompt: PPID_24_0_S3 제외하고 보여줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 8073
- llm_used:
- answer: 직전 chart session(ac5869b0) 조건을 이어받았습니다. ET IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF step_id=EA100030.

### 2026-05-17T02:33:54.226091+00:00 - chat
- prompt: PRODA INLINE CD Trend 그려줘
- intent: dashboard_inline_trend_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 48.0
- result_status: missing
- elapsed_ms: 9404
- llm_used:
- answer: Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.

### 2026-05-17T02:34:07.327940+00:00 - chat
- prompt: PRODA Inline CD와 ET IOFF Corr scatter 그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.wafer_id', 'semiconductor_eight_major_processes_for_gaa', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 51.0
- result_status: missing
- elapsed_ms: 11104
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE - 감지 metric 후보: LKG, IOFF, CD - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요.

### 2026-05-17T02:34:28.485957+00:00 - chat
- prompt: PRODA ET와 ML_TABLE 조인해서 scatter 차트 그려줘
- intent: dashboard_multisource_chart
- selected_function: dashboard.chart.llm.draft
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'ml_table_proda.wafer_id', 'proda_hold_release_decision', 'proda_dashboard_query_manual', 'proda_dibl_ss_rca_issue', 'proda_sort_knob_split_rule', 'gaa_device_geometry_and_multi_vt_design', 'gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'gaa_device_evolution_and_purpose', 'dashboard_chart_generation_rules', 'knowledge_vault_overview']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 19007
- llm_used:
- answer: confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다. - source: 3개 - relation: 6개 - join key: root_lot_id, wafer_id - 결과: 421200행

### 2026-05-17T02:35:32.481587+00:00 - chat
- prompt: PRODA EA100030 IOFF Trend 그려줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 45.0
- result_status: success
- elapsed_ms: 10790
- llm_used:
- answer: PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF step_id=EA100030.

### 2026-05-17T02:35:43.310726+00:00 - chat
- prompt: PRODA IOFF Trend 그려줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id', 'gaa_device_geometry_and_multi_vt_design']
- system_knowledge_ids:
- retrieval_score: 45.0
- result_status: success
- elapsed_ms: 8254
- llm_used:
- answer: PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF.

### 2026-05-17T02:35:58.163734+00:00 - chat
- prompt: 24.0 SORT KNOB으로 컬러링해줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'proda_sort_knob_split_rule', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_a1001_lot_watch', 'default_agent_wiki_seed_framework', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 11878
- llm_used:
- answer: 직전 chart session(95fa2403) 조건을 이어받았습니다. PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF step_id=EA100030. 24.0 SORT 기준으로 색상을 입혔고 KNOB가 없는 point는 회색으로 표시합니다.

### 2026-05-17T02:36:08.511785+00:00 - chat
- prompt: PPID_24_0_S2 제외하고 보여줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 7821
- llm_used:
- answer: 직전 chart session(98a14a2f) 조건을 이어받았습니다. PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF step_id=EA100030. 24.0 SORT 기준으로 색상을 입혔고 KNOB가 없는 point는 회색으로 표시합니다.

### 2026-05-17T02:36:18.725632+00:00 - chat
- prompt: PRODA INLINE CD Trend 그려줘
- intent: dashboard_inline_trend_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 48.0
- result_status: missing
- elapsed_ms: 7787
- llm_used:
- answer: Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.

### 2026-05-17T02:36:33.230147+00:00 - chat
- prompt: PRODA Inline CD와 ET IOFF Corr scatter 그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.wafer_id', 'semiconductor_eight_major_processes_for_gaa', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 51.0
- result_status: missing
- elapsed_ms: 12543
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: ET, INLINE - 감지 metric 후보: LKG, IOFF, CD - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요.

### 2026-05-17T02:36:52.500981+00:00 - chat
- prompt: PRODA ET와 ML_TABLE 조인해서 scatter 차트 그려줘
- intent: dashboard_multisource_chart
- selected_function: dashboard.chart.llm.draft
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'ml_table_proda.wafer_id', 'proda_hold_release_decision', 'proda_dashboard_query_manual', 'proda_dibl_ss_rca_issue', 'proda_sort_knob_split_rule', 'gaa_device_geometry_and_multi_vt_design', 'gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'gaa_device_evolution_and_purpose', 'dashboard_chart_generation_rules', 'knowledge_vault_overview']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 17070
- llm_used:
- answer: confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다. - source: 3개 - relation: 6개 - join key: root_lot_id, wafer_id - 결과: 421200행

### 2026-05-17T03:34:58.574972+00:00 - chat
- prompt: PRODA 16.0 VIA2 Inline trend 그려줘
- intent: dashboard_inline_trend_needs_grain
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 52.0
- result_status: missing
- elapsed_ms: 1315
- llm_used:
- answer: PRODA 16.0 VIA2은 INLINE item으로 해석했고 Trend x축은 tkout_time 기준입니다. INLINE grain을 선택해야 합니다.

### 2026-05-17T03:35:02.972204+00:00 - chat
- prompt: PRODA 16.0 VIA2 Inline trend 그려줘 PRODA 16.0 VIA2 Inline trend 그려줘 grain: lot_wf
- intent: dashboard_inline_trend_chart
- selected_function: query_inline_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'ml_table_proda.lot_wf', 'proda_dashboard_query_manual', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 64.0
- result_status: success
- elapsed_ms: 1844
- llm_used:
- answer: PRODA 16.0 VIA2 INLINE Trend를 tkout_time x축 scatter로 그렸습니다. INLINE은 lot_wf별 avg(value)를 시간별로 집계했습니다. 표시 point=500, item match=16.0 VIA2.

### 2026-05-17T03:35:21.087532+00:00 - chat
- prompt: PRODA 16.0 VIA2 Inline trend 그려줘 grain: PRODA 24.0 SORT로 컬러링해줘
- intent: dashboard_inline_trend_chart
- selected_function: query_inline_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'column:ML_TABLE_PRODA.step_id', 'proda_sort_knob_split_rule', 'default_agent_wiki_seed_framework', 'proda_hold_release_decision', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 53.0
- result_status: success
- elapsed_ms: 1726
- llm_used:
- answer: 직전 chart session(ad340b30) 조건을 이어받았습니다. PRODA 16.0 VIA2 INLINE Trend를 tkout_time x축 scatter로 그렸습니다. INLINE은 lot_wf별 avg(value)를 시간별로 집계했습니다. 표시 point=500, item match=16.0 VIA2.

### 2026-05-17T03:36:01.260221+00:00 - chat
- prompt: PRODA 16.0 VIA2 Inline trend 그려줘 grain: PRODA 16.0 VIA2와 IOFF Corr. Chart 그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'ml_table_proda.step_id', 'semiconductor_eight_major_processes_for_gaa', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 60.0
- result_status: missing
- elapsed_ms: 1694
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: INLINE - 감지 metric 후보: LKG, IOFF, VIA2, GRAIN, CORR. - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-17T03:36:09.637334+00:00 - chat
- prompt: PRODA 16.0 VIA2 Inline trend 그려줘 PRODA 16.0 VIA2 Inline trend 그려줘 grain: PRODA 16.0 VIA2와 IOFF Corr. Chart 그려줘 / INLINE 기준 left join으로 진행
- intent: dashboard_multisource_chart
- selected_function: dashboard.chart.llm.draft
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'ml_table_proda.wafer_id', 'proda_hold_release_decision', 'proda_dashboard_query_manual', 'proda_dibl_ss_rca_issue', 'proda_sort_knob_split_rule', 'dashboard_chart_generation_rules', 'default_agent_wiki_seed_framework', 'proda_a1001_lot_watch', 'semiconductor_eight_major_processes_for_gaa', 'gaa_device_evolution_and_purpose']
- system_knowledge_ids:
- retrieval_score: 31.0
- result_status: success
- elapsed_ms: 2700
- llm_used:
- answer: confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다. - source: 1개 - relation: 0개 - join key: - - 결과: 3725행

### 2026-05-17T03:36:52.847173+00:00 - chat
- prompt: PRODA A1001A.2에 plan 세운거 뭐가있어?
- intent: splittable_guidance
- selected_function: open_splittable
- retrieved_ids: ['gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 797
- llm_used:
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=PRODA, lot=A1001A.2, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-17T03:37:10.877980+00:00 - chat
- prompt: PRODA A1001A.2에 plan 세운거 뭐가있어? 스플릿테이블로 보여줘 해당행만
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1282
- llm_used:
- answer: 이전 조건을 이어받아 SplitTable 형태로 다시 표시했습니다. ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-17T03:37:38.714425+00:00 - chat
- prompt: KNOB 스플릿 테이블만 보여줘
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1365
- llm_used:
- answer: 이전 조건을 이어받아 SplitTable 형태로 다시 표시했습니다. ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-17T03:37:48.704178+00:00 - chat
- prompt: KNOB만 보여줘야해
- intent: lot_knobs
- selected_function: lot_knobs
- retrieved_ids:
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 1966
- llm_used:
- answer: KNOB 조회는 lot/root lot 조건이 필요합니다. 예: A1008, A1009, A1010, A1011, A1014, A1018, A1020, A1024

### 2026-05-17T03:49:38.522290+00:00 - chat
- prompt: PRODA A1001 IOFF value Trend 차트 그려줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'proda_a1001_lot_watch', 'proda_a1001_w07_wafer_signal', 'proda_hold_release_decision', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 46.0
- result_status: success
- elapsed_ms: 5563
- llm_used:
- answer: PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=225, item match=IOFF.

### 2026-05-17T03:49:59.972858+00:00 - chat
- prompt: 24.0 SORT KNOB에 따라서 컬러링 해줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'proda_sort_knob_split_rule', 'gaa_device_geometry_and_multi_vt_design', 'ml_table_proda.wafer_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_a1001_lot_watch', 'gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 3428
- llm_used:
- answer: 직전 chart session(3736b3e7) 조건을 이어받았습니다. PRODA IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=225, item match=IOFF. 24.0 SORT 기준으로 색상을 입혔고 KNOB가 없는 point는 회색으로 표시합니다.

### 2026-05-17T03:50:21.416911+00:00 - chat
- prompt: shot으로 다그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'gaa_device_geometry_and_multi_vt_design', 'ml_table_proda.wafer_id', 'proda_dibl_ss_rca_issue', 'default_agent_wiki_seed_framework', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 43.0
- result_status: missing
- elapsed_ms: 1188
- llm_used:
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: SHOT - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-17T03:50:31.695893+00:00 - chat
- prompt: shot으로 다그려줘 / ET 기준 left join으로 진행
- intent: dashboard_multisource_chart
- selected_function: dashboard.chart.llm.draft
- retrieved_ids: ['default_agent_wiki_seed_framework', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'gaa_device_geometry_and_multi_vt_design', 'gaa_nanosheet_process_flow_and_failure_modes', 'semiconductor_eight_major_processes_for_gaa', 'gaa_beol_bspdn_power_delivery_basics', 'gaa_device_evolution_and_purpose', 'dashboard_chart_generation_rules', 'knowledge_vault_overview', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf']
- system_knowledge_ids:
- retrieval_score: 17.0
- result_status: success
- elapsed_ms: 2134
- llm_used:
- answer: confirmed schema relation 기준으로 실제 source를 읽어 결과를 만들었습니다. - source: 3개 - relation: 6개 - join key: root_lot_id, wafer_id - 결과: 421200행

### 2026-05-17T03:50:52.168364+00:00 - chat
- prompt: 위에 Trend 그린거 원본 raw data 제공해줘
- intent: dashboard_inline_trend_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['dashboard_chart_generation_rules', 'proda_dashboard_query_manual', 'ml_table_proda.lot_wf', 'ml_table_proda.wafer_id', 'proda_a1001_lot_watch', 'gaa_device_geometry_and_multi_vt_design', 'knowledge_vault_overview', 'default_agent_wiki_seed_framework']
- system_knowledge_ids:
- retrieval_score: 45.0
- result_status: missing
- elapsed_ms: 1847
- llm_used:
- answer: Trend로 그릴 INLINE item을 찾지 못했습니다. item명을 더 정확히 알려주세요.

### 2026-05-17T06:31:15.639441+00:00 - chat
- prompt: A1000 IOFF Trend 그려줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'gaa_device_evolution_and_purpose', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids:
- retrieval_score: 45.0
- result_status: success
- elapsed_ms: 7399
- llm_used:
- answer: ET IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF.

### 2026-05-17T06:31:32.772700+00:00 - chat
- prompt: 24.0 SORT KNOB으로 컬러링넣어줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'proda_sort_knob_split_rule', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_a1001_lot_watch', 'default_agent_wiki_seed_framework']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 3195
- llm_used:
- answer: 직전 chart session(c448adef) 조건을 이어받았습니다. ET IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF.

### 2026-05-17T06:31:51.299313+00:00 - chat
- prompt: KNOB으로 컬러링넣어줘
- intent: dashboard_et_trend_chart
- selected_function: query_et_trend_scatter_chart
- retrieved_ids: ['dashboard_chart_generation_rules', 'gaa_device_geometry_and_multi_vt_design', 'proda_dashboard_query_manual', 'gaa_nanosheet_process_flow_and_failure_modes', 'ml_table_proda.lot_wf', 'gaa_device_evolution_and_purpose', 'default_agent_wiki_seed_framework', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids:
- retrieval_score: 42.0
- result_status: success
- elapsed_ms: 2608
- llm_used:
- answer: 직전 chart session(234660ce) 조건을 이어받았습니다. ET IOFF ET Trend를 tkout_time x축 scatter로 그렸습니다. ET는 lot_wf별 median(value) 기준입니다. 표시 point=500, item match=IOFF.

### 2026-05-17T06:34:25.373398+00:00 - chat
- prompt: raw data 제공해줘
- intent: dashboard_chart_raw_data
- selected_function: export_chart_raw_data
- retrieved_ids: ['knowledge_vault_overview', 'default_agent_wiki_seed_framework']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 50532
- llm_used:
- answer: 직전 chart session(f44784c5)의 raw data를 CSV로 내려받을 수 있습니다. FileBrowser 제한 기준: 500/500,000행, 최대 100,000,000 bytes.

### 2026-05-17T11:50:29.743292+00:00 - blocked_write_request
- prompt: raw DB 파일을 직접 삭제해줘
- answer: DB 루트 원본은 admin도 Flow-i에서 수정할 수 없습니다. 수정/등록은 파일탐색기 수정 권한이 있는 사용자만 Files 영역 단일파일에 대해 확인 후 실행됩니다.
- source_ai: scenario

### 2026-05-17T11:50:33.120883+00:00 - admin_file_operation
- prompt: Files root 파일 내용을 바꾸는 초안을 만들어줘
- action: confirm_file_operation
- requires_confirmation: True
- blocked:
- answer: Files 단일파일 작업은 구조화된 확인 명령이 필요합니다. DB 루트는 admin도 수정할 수 없습니다.
- source_ai: scenario

### 2026-05-17T11:50:55.339597+00:00 - chat
- prompt: PRODA A1002 24.0 SORT KNOB 구성이 어떻게돼?
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'proda_a1001_lot_watch', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 20450
- llm_used:
- answer: A1002 KNOB 구성은 custom set 기준으로 보는 것이 가장 좋습니다. 25 wafer row를 2개 custom set으로 묶었습니다. - custom_set_1: 24.0 SORT=PPID_24_2 / #1, #2, #3 - custom_set_2: 24.0 SORT=(empty) / #4, #5, #6, #7, #8, #9, #10, #11, #12, #13, #14, #15, #16, #17, #18, #19, #20, #21, #22, #23, #24, #25
- source_ai: scenario

### 2026-05-17T11:51:06.856469+00:00 - chat
- prompt: PRODA A1002 KNOB TABLE 보여줘
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 9110
- llm_used:
- answer: A1002 / KNOB 조건으로 ML_TABLE을 조회했습니다. 결과 25건입니다.
- source_ai: scenario

### 2026-05-17T11:51:14.353377+00:00 - chat
- prompt: PRODA FAB 최근 3행 보여줘
- intent: filebrowser_data_preview
- selected_function: preview_filebrowser_data
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 4789
- llm_used:
- answer: DB preview 실패: data type mismatch for column value: incoming: String != target: Float64
- source_ai: scenario

### 2026-05-17T11:51:18.868853+00:00 - inform_walkthrough
- prompt: A1004 인폼전체 작성해줘
- intent: inform_walkthrough
- answer: GATE의 SplitTable은 어떤 Split으로 진행할까요? (예: test1)
- source_ai: scenario

### 2026-05-17T11:51:34.026707+00:00 - chat
- prompt: PRODA A1000 #6 현재 fab lot id가 뭐야?
- intent: current_fab_lot_lookup
- selected_function: query_current_fab_lot_from_fab_db
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id', 'gaa_device_evolution_and_purpose', 'default_agent_wiki_seed_framework', 'proda_dibl_ss_rca_issue']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 13163
- llm_used:
- answer: PRODA A1000 wafer #6의 현재 fab_lot_id는 `A1000A.3`입니다. 기준 row: step_id=EA100030, time=2024-04-27T18:20:00. 기준 SQL: root_lot_id/wafer_id 조건 후 tkout_time 최신 row.
- source_ai: scenario

### 2026-05-17T12:33:37.472368+00:00 - chat
- prompt: [실전테스트] PRODA 24.0 SORT INLINE_AAA MTS 기준 변경점 이력과 현재 anchor item 보여줘
- intent: knowledge_impact_context
- selected_function: knowledge.impact_context.lookup
- retrieved_ids: ['flowi_live_aaa_0ec01543_anchor_registry', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 1.0
- result_status: success
- elapsed_ms: 13286
- llm_used:
- answer: 확인된 운영 지식 1건: [실전테스트] PRODA INLINE_AAA anchor registry MTS 변경 후보: 1건 (24.0 SORT / INLINE_AAA 기준 변경: 2.0 -> 3.5) Anchor item 현재: 24.0 SORT / INLINE_AAA Anchor item 이력: INLINE_AAA(-~현재) / INLINE_OLD(2026-01-01~2026-02-01) -> INLINE_AAA / INLINE_AAA(2026-02-01~현재) 근거: wiki flowi_live_aaa_0ec01543_anchor_registry / event evt_20260517213324_c8108bd3, evt_20260517213323_2a6cda19, evt_20260517213323_6316c367
- source_ai: live_knowledge_smoke

### 2026-05-17T12:36:26.481177+00:00 - chat
- prompt: [실전테스트] PRODA 24.0 SORT INLINE_AAA MTS 기준 변경점 이력과 현재 anchor item 보여줘
- intent: knowledge_impact_context
- selected_function: knowledge.impact_context.lookup
- retrieved_ids: ['flowi_live_aaa_0ec01543_anchor_registry', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.step_id', 'proda_sort_knob_split_rule', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id']
- system_knowledge_ids:
- retrieval_score: 1.0
- result_status: success
- elapsed_ms: 12137
- llm_used:
- answer: 확인된 운영 지식 1건: [실전테스트] PRODA INLINE_AAA anchor registry MTS 변경 후보: 1건 (24.0 SORT / INLINE_AAA 기준 변경: 2.0 -> 3.5) Anchor item 현재: 24.0 SORT / INLINE_AAA Anchor item 이력: INLINE_OLD(2026-01-01~2026-02-01) -> INLINE_AAA / INLINE_AAA(2026-02-01~현재) 근거: wiki flowi_live_aaa_0ec01543_anchor_registry / event evt_20260517213324_c8108bd3, evt_20260517213323_2a6cda19, evt_20260517213323_6316c367
- source_ai: live_knowledge_smoke_rerun

### 2026-05-17T13:39:05.578236+00:00 - chat
- prompt: PRODA A1001 KNOB 어떤게 있어 리스트 형태로 보여줘
- intent: lot_knobs
- selected_function: query_lot_knobs_from_ml_table
- retrieved_ids: ['ml_table_proda.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'ml_table_proda.lot_wf']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 13041
- llm_used:
- answer: A1001 / KNOB 조건으로 ML_TABLE을 조회했습니다. 결과 25건입니다.

### 2026-05-17T13:40:16.801920+00:00 - chat
- prompt: A1001 #18의 스플릿 테이블만 보여줘
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['gaa_device_geometry_and_multi_vt_design', 'semiconductor_eight_major_processes_for_gaa', 'proda_hold_release_decision', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids:
- result_status: success
- elapsed_ms: 12912
- llm_used:
- answer: 이전 조건을 이어받아 SplitTable 형태로 다시 표시했습니다. ML_TABLE_PRODA A1001 SplitTable KNOB 기준으로 24개 row를 조회했습니다.

### 2026-05-17T13:40:45.763699+00:00 - inform_log_draft
- prompt: A1001에 대해서 인폼남기고싶어
- intent: inform_log_draft
- answer: 인폼 등록 초안에 필요한 값을 선택해 주세요.

### 2026-05-17T13:41:10.354650+00:00 - inform_summary
- prompt: A1001에 대해서 인폼남기고싶어 module: BEOL
- intent: inform_lot_module_summary
- answer: A1001 인폼 모듈 현황입니다. - 등록 모듈: 7/10 - 완료 모듈: 2 - 미완료 모듈: 5 - 미등록 모듈: 3 - 미완료: PC, MOL, BEOL, (미지정), INTEGRATION - 미등록: Spacer, Well, 기타

### 2026-05-23T07:40:01.168174+00:00 - chat
- prompt: A1000 21 PRDA AA100590
- intent: fab_progress_lookup
- selected_function: query_fab_progress
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 16447
- llm_used: 
- answer: A1000 현재 위치는 step_id=EA100030 입니다. 최신 시간: 2024-04-24T11:20:00.

### 2026-05-23T07:40:19.985225+00:00 - chat
- prompt: PRDA A1000 #21 AA100590는 어떤 eqp chamber 로 진행했어?
- intent: wafer_split_at_step
- selected_function: clarify_product
- retrieved_ids: ['flowi_live_aaa_0ec01543_anchor_registry', 'ml_table_proda.lot_wf', 'ml_table_proda.step_id', 'proda_hold_release_decision']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 7448
- llm_used: 
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 선택해주세요.

### 2026-05-23T08:48:39.204748+00:00 - chat
- prompt: PRODA A1000 #21 lot id 알고싶어
- intent: et_wafer_median
- selected_function: query_et
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.wafer_id', 'ml_table_proda.lot_wf', 'ml_table_proda.root_lot_id', 'ml_table_proda.step_id']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 6000
- llm_used: 
- answer: ET 레포트 단위기능으로 처리하는 요청입니다. - 실행 경로: query_et - 필요한 조건: product, root_lot_id or lot_id, step_id, item_id - 현재 감지: product=PRODA, lot=A1000, step=- - 추가로 필요: step_id Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-26T13:37:34.759147+00:00 - inform_summary
- prompt: A1002A.1 인폼내용 요약해줘
- intent: inform_lot_module_summary
- answer: A1002 인폼 모듈 현황입니다. - 등록 모듈: 6/9 - 완료 모듈: 0 - 미완료 모듈: 6 - 미등록 모듈: 3 - 미완료: GATE, STI, PC, MOL, BEOL, ET - 미등록: Spacer, Well, 기타

### 2026-05-27T14:26:48.050672+00:00 - inform_summary
- prompt: A1001A.2 인폼내용 요약해줘
- intent: inform_lot_module_summary
- answer: A1001 인폼 모듈 현황입니다. - 등록 모듈: 7/10 - 완료 모듈: 2 - 미완료 모듈: 5 - 미등록 모듈: 3 - 미완료: PC, MOL, BEOL, (미지정), INTEGRATION - 미등록: Spacer, Well, 기타

### 2026-05-28T12:42:41.216705+00:00 - chat
- prompt: FAB Data에서 A1000 step_id 30들어간거 보여줘
- intent: fab_progress_lookup
- selected_function: query_fab_progress
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 8055
- llm_used: 
- answer: A1000 현재 위치는 step_id=EA100030 입니다. 최신 시간: 2024-04-24T11:20:00.

### 2026-05-28T12:42:54.333585+00:00 - chat
- prompt: 테이블로 보여줘
- intent: dashboard_chart_draft_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 1879
- llm_used: 
- answer: 테이블 초안을 만들었습니다. 부족한 값은 편집 모달에서 보완할 수 있습니다.

### 2026-05-28T12:43:03.954016+00:00 - chat
- prompt: 테이블로 보여줘 product: 테이블로 보여줘 FAB
- intent: dashboard_chart_draft_needs_context
- selected_function: collect_required_fields
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 2477
- llm_used: 
- answer: FAB 테이블 초안을 만들었습니다. 부족한 값은 편집 모달에서 보완할 수 있습니다.

### 2026-05-28T12:43:52.589610+00:00 - chat
- prompt: A1000 split table 6.0 LDD 값 구성 보여줘
- intent: wafer_split_at_step
- selected_function: clarify_product
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 6720
- llm_used: 
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 선택해주세요.

### 2026-05-30T13:38:17.370768+00:00 - chat
- prompt: M1_LITHO는 step_id가 뭐야?
- intent: step_mapping_lookup
- selected_function: query_step_mapping_lookup
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2396
- llm_used: 
- answer: 5.0 PC은 ppid_knob.csv에서 PC_LITHO, CONTACT_LITHO, M1_LITHO, M2_LITHO로 해석됐고, step_matching.csv 기준 step_id는 PRODA: AA100240, AA100440, AA100100, AA100300, AA100500, AA100140, AA100340, AA100540, AA100170, AA100370, AA100570; PRODB: AB100040, AB100240, AB100440, AB100100, AB100300, AB100500, AB100140, AB100340, AB100540, AB100170, AB100370, AB100570 입니다. 근거: ppid_knob.csv -> step_matching.csv

### 2026-05-30T13:38:49.970504+00:00 - chat
- prompt: AA100340는 무슨 step이야?
- intent: tablemap_guidance
- selected_function: open_tablemap
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1561
- llm_used: 
- answer: 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=-, lot=-, step=AA100340 Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-30T13:40:27.188562+00:00 - chat
- prompt: AA100220은 무슨 step이야?
- intent: tablemap_guidance
- selected_function: open_tablemap
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1603
- llm_used: 
- answer: 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=-, lot=-, step=AA100220 Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-30T14:23:09.922855+00:00 - chat
- prompt: AA100250는 무슨 step이야?
- intent: tablemap_guidance
- selected_function: open_tablemap
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2151
- llm_used: 
- answer: 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=-, lot=-, step=AA100250 Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-31T00:25:45.422935+00:00 - chat
- prompt: A1001은 스플릿테이블
- intent: wafer_split_at_step
- selected_function: clarify_product
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 12460
- llm_used: 
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 선택해주세요.

### 2026-05-31T00:26:18.878738+00:00 - chat
- prompt: A1001 스플릿테이블
- intent: splittable_context_followup
- selected_function: clarify_product
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 32196
- llm_used: True
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 선택해주세요.

### 2026-05-31T08:31:32.352337+00:00 - semantic_measurement_lookup
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: semantic_measurement_lookup
- answer: PRODA A1001 CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T08:39:30.511779+00:00 - semantic_measurement_lookup
- prompt: PRODA A1001 ?????? ???
- intent: semantic_measurement_lookup
- answer: PRODA A1001 CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T08:40:03.170601+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-31T08:40:35.009325+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-31T08:51:36.680891+00:00 - splittable_view
- prompt: A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-05-31T08:51:38.177435+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-31T10:50:06.776114+00:00 - splittable_view
- prompt: A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-05-31T10:50:09.966302+00:00 - semantic_measurement_lookup
- prompt: product: PRODA
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T10:50:22.915967+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-31T10:59:59.672539+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB 기준으로 25개 row를 조회했습니다.

### 2026-05-31T11:02:06.892791+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB 기준으로 25개 row를 조회했습니다.

### 2026-05-31T11:07:04.526688+00:00 - splittable_view
- prompt: PRODA A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB 기준으로 25개 row를 조회했습니다.

### 2026-05-31T11:54:45.513901+00:00 - splittable_view
- prompt: A1002 1.0 STI Split(or Knob) 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-05-31T11:54:48.048606+00:00 - semantic_measurement_lookup
- prompt: product: PRODA
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T12:10:10.802049+00:00 - chat
- prompt: 1.6.0 LDD Knob ??? ? ???????
- intent: lot_knobs
- selected_function: lot_knobs
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 6892
- llm_used: 
- answer: KNOB 조회는 lot/root lot 조건이 필요합니다. 예: A1002, A1006, A1008, A1013, A1015, A1017, A1018, A1024

### 2026-05-31T12:11:15.605793+00:00 - chat
- prompt: 1.6.0 LDD Knob ??? ? ???????
- intent: lot_knobs
- selected_function: lot_knobs
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3902
- llm_used: 
- answer: KNOB 조회는 lot/root lot 조건이 필요합니다. 예: A1000, A1002, A1006, A1008, A1021, A1024, A1025, A1028

### 2026-05-31T12:14:01.637262+00:00 - chat
- prompt: 1.6.0 LDD Knob 어떻게 룰 구성되어있어?
- intent: knob_rulebook_lookup
- selected_function: query_knob_rulebook_rows
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3028
- llm_used: 
- answer: 조건에 맞는 KNOB rulebook 행을 찾지 못했습니다.

### 2026-05-31T12:25:39.073273+00:00 - splittable_view
- prompt: A1002 1.0 STI Split(or Knob) 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-05-31T12:25:40.654739+00:00 - splittable_view
- prompt: A1002 1.0 STI Split(or Knob) 보여줘 product: PRODA
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1002 1.0 STI ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다.

### 2026-05-31T12:33:32.077738+00:00 - wafer_split_at_step
- prompt: A1002 1.0 STI Split(or Knob) 보여줘 product: A1002 1.0 STI Split(or Knob) 보여줘
- intent: wafer_split_at_step
- feature: splittable
- answer: ML_TABLE parquet을 찾지 못했습니다.

### 2026-05-31T12:33:33.832212+00:00 - splittable_view
- prompt: A1002 1.0 STI Split(or Knob) 보여줘 product: PRODA
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1002 1.0 STI ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다.

### 2026-05-31T12:33:57.718424+00:00 - chat
- prompt: A1002 1.0 STI Split(or Knob) ë³´ì¬ì¤
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3988
- llm_used: 
- answer: ML_TABLE_PRODA A1002 1.0 STI ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다.

### 2026-05-31T12:33:58.364372+00:00 - semantic_measurement_lookup
- prompt: PRODA
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T12:34:20.081550+00:00 - chat
- prompt: A1002 1.0 STI Split(or Knob) ë³´ì¬ì¤
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 4032
- llm_used: 
- answer: ML_TABLE_PRODA A1002 1.0 STI ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다.

### 2026-05-31T12:34:20.855894+00:00 - semantic_measurement_lookup
- prompt: PRODA
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T12:36:24.420233+00:00 - splittable_view
- prompt: A1002 1.0 STI Split(or Knob) 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-05-31T12:36:25.853969+00:00 - splittable_view
- prompt: A1002 1.0 STI Split(or Knob) 보여줘 product: PRODA
- intent: splittable_view
- feature: splittable
- answer: ML_TABLE_PRODA A1002 1.0 STI ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다.

### 2026-05-31T13:33:33.710436+00:00 - semantic_measurement_lookup
- prompt: Inline 15.0 M2의 trend를 그려줘
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T13:33:50.244963+00:00 - semantic_measurement_lookup
- prompt: Inline 15.0 M2의 trend를 그려줘
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T13:33:59.984960+00:00 - semantic_measurement_lookup
- prompt: Inline 15.0 M2의 trend chart를 그려줘
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T13:36:46.871616+00:00 - semantic_measurement_lookup
- prompt: Inline 15.0 M2의 trend chart를 그려줘
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T13:53:16.582899+00:00 - semantic_measurement_lookup
- prompt: Inline 15.0 M2의 trend chart를 그려줘
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T13:54:23.637355+00:00 - semantic_measurement_lookup
- prompt: Inline 15.0 M2의 trend chart를 그려줘
- intent: semantic_measurement_lookup
- answer: PRODA - CA BCD 값을 INLINE source에서 wafer별 avg로 조회했습니다. 결과 0건.

### 2026-05-31T13:56:42.068016+00:00 - chat
- prompt: 3. A1001 #3 지금 어디에 있어?
- intent: fab_progress_lookup
- selected_function: query_fab_progress
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 7096
- llm_used: 
- answer: A1001 현재 위치는 step_id=AA100150 입니다. 최신 시간: 2024-04-24T10:09:00.

### 2026-05-31T13:56:48.503354+00:00 - chat
- prompt: A1001 #3 지금 어디에 있어?
- intent: fab_progress_lookup
- selected_function: query_fab_progress
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 6088
- llm_used: 
- answer: A1001 현재 위치는 step_id=AA100150 입니다. 최신 시간: 2024-04-24T10:09:00.

### 2026-06-15T06:07:20.060751+00:00 - splittable_view
- prompt: A1001 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-07-02T23:01:25.427746+00:00 - skill_catalog
- prompt: 쓸 수 있는 스킬 알려줘
- intent: skill_catalog
- feature: skills
- skill_key: 
- answer: 사용 가능한 공유 스킬입니다. "<스킬 제목> 스킬 실행" 형태로 입력하면 바로 실행합니다. - 스모크 공유 조회 (SQL)

### 2026-07-02T23:01:25.879675+00:00 - inform_summary
- prompt: 스모크 공유 조회 스킬 실행해줘
- intent: inform_lot_module_summary_needs_context
- answer: Lot별 인폼 모듈 현황을 보려면 lot_id 또는 root_lot_id가 필요합니다.

### 2026-07-02T23:02:26.684716+00:00 - skill_run
- prompt: 스모크 공유 조회 스킬 실행해줘 쓸 수 있는 스킬 알려줘
- intent: skill_run
- feature: skills
- skill_key: flow_smoke_shared_skill
- answer: 공유 스킬 '스모크 공유 조회' 실행 결과 — 2행 (미리보기 2행) a | b 1 | x 2 | y

### 2026-07-02T23:02:27.218847+00:00 - skill_run
- prompt: 스모크 공유 조회 스킬 실행해줘 스모크 공유 조회 스킬 실행해줘
- intent: skill_run
- feature: skills
- skill_key: flow_smoke_shared_skill
- answer: 공유 스킬 '스모크 공유 조회' 실행 결과 — 2행 (미리보기 2행) a | b 1 | x 2 | y

### 2026-07-02T23:03:07.115946+00:00 - skill_run
- prompt: 스모크 공유 조회 스킬 실행해줘 쓸 수 있는 스킬 알려줘
- intent: skill_run
- feature: skills
- skill_key: flow_smoke_shared_skill
- answer: 공유 스킬 '스모크 공유 조회' 실행 결과 — 2행 (미리보기 2행) a | b 1 | x 2 | y

### 2026-07-02T23:03:07.853218+00:00 - skill_run
- prompt: 스모크 공유 조회 스킬 실행해줘 스모크 공유 조회 스킬 실행해줘
- intent: skill_run
- feature: skills
- skill_key: flow_smoke_shared_skill
- answer: 공유 스킬 '스모크 공유 조회' 실행 결과 — 2행 (미리보기 2행) a | b 1 | x 2 | y

### 2026-07-02T23:04:21.200511+00:00 - skill_catalog
- prompt: 쓸 수 있는 스킬 알려줘
- intent: skill_catalog
- feature: skills
- skill_key: 
- answer: 사용 가능한 공유 스킬입니다. "<스킬 제목> 스킬 실행" 형태로 입력하면 바로 실행합니다. - 스모크 공유 조회 (SQL)

### 2026-07-02T23:04:21.573564+00:00 - skill_run
- prompt: 스모크 공유 조회 스킬 실행해줘
- intent: skill_run
- feature: skills
- skill_key: flow_smoke_shared_skill
- answer: 공유 스킬 '스모크 공유 조회' 실행 결과 — 2행 (미리보기 2행) a | b 1 | x 2 | y

### 2026-07-02T23:29:47.753543+00:00 - chat
- prompt: AA100100는 무슨 step이야
- intent: step_lookup
- selected_function: id_to_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 8911
- llm_used: True
- answer: AA100100는 PRODA의 CONTACT_LITHO step입니다.

### 2026-07-02T23:29:54.497629+00:00 - chat
- prompt: AA100160는 무슨 step이야
- intent: step_lookup
- selected_function: id_to_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 6452
- llm_used: True
- answer: AA100160는 PRODA의 VIA1_FORMATION step입니다.

### 2026-07-02T23:29:59.527533+00:00 - chat
- prompt: SD_EPI step_id 관련 파일 어디에 있어
- intent: step_lookup
- selected_function: step_to_id
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3789
- llm_used: True
- answer: SD_EPI의 step_id는 PRODA에 AA100090, AA100290, AA100490이 있으며, PRODB에는 AB100090, AB100290, AB100490이 있습니다.

### 2026-07-02T23:29:59.942122+00:00 - fewshot_teach
- prompt: 기억해: ZQ123456는 TEST_FUNCTION_STEP 공정
- intent: fewshot_teach
- feature: fewshot
- answer: 기억했습니다: ZQ123456 → TEST_FUNCTION_STEP 공정 다음부터 이 용어 질문에 바로 답합니다. 수정은 같은 형식으로 다시, 삭제는 "잊어줘: ZQ123456".

### 2026-07-02T23:30:06.282929+00:00 - chat
- prompt: ZQ123456 무슨 공정이야
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 6128
- llm_used: True
- answer: ZQ123456: TEST_FUNCTION_STEP 공정 (사용자 학습 데이터 — hol 님이 가르쳐준 매핑, 1회 사용)

### 2026-07-02T23:30:11.871072+00:00 - chat
- prompt: ZW654321 무슨 공정이야
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 5348
- llm_used: True
- answer: ZW654321: CORRECTED_STEP_X (사용자 학습 데이터 — hol 님이 가르쳐준 매핑, 1회 사용)

### 2026-07-02T23:30:12.182421+00:00 - fewshot_forget
- prompt: 잊어줘: ZQ123456
- intent: fewshot_forget
- feature: fewshot
- answer: 'ZQ123456' 학습 데이터를 삭제했습니다.

### 2026-07-02T23:30:12.438803+00:00 - fewshot_forget
- prompt: 잊어줘: ZW654321
- intent: fewshot_forget
- feature: fewshot
- answer: 'ZW654321' 학습 데이터를 삭제했습니다.

### 2026-07-02T23:31:20.442129+00:00 - chat
- prompt: AA100100는 무슨 step이야
- intent: step_lookup
- selected_function: id_to_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 6428
- llm_used: True
- answer: AA100100는 PRODA의 CONTACT_LITHO step입니다.

### 2026-07-02T23:31:26.016931+00:00 - chat
- prompt: AA100160EC2는 무슨 step이야
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 5340
- llm_used: True
- answer: 'AA100160EC2'에 대한 정확한 step 정보는 찾을 수 없었습니다. 유사한 step ID인 'AA100160'은 PRODA의 'VIA1_FORMATION'입니다. 정확한 정보를 알고 계시다면 "기억해: AA100160EC2는 <답>" 형태로 알려주세요. 다음부터 바로 안내해 드리겠습니다.

### 2026-07-02T23:31:30.605994+00:00 - chat
- prompt: SD_EPI step_id 관련 파일 어디에 있어
- intent: step_lookup
- selected_function: step_to_id
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 4359
- llm_used: True
- answer: SD_EPI의 step_id는 PRODA: AA100090, AA100290, AA100490 이고 PRODB: AB100090, AB100290, AB100490 입니다. 'SD_EPI' 관련 파일은 ppid_knob.csv에 있으며, 2행의 데이터(feature_name, function_step 열)를 포함하고 있습니다.

### 2026-07-02T23:31:31.002937+00:00 - fewshot_teach
- prompt: 기억해: ZQ123456는 TEST_FUNCTION_STEP 공정
- intent: fewshot_teach
- feature: fewshot
- answer: 기억했습니다: ZQ123456 → TEST_FUNCTION_STEP 공정 다음부터 이 용어 질문에 바로 답합니다. 수정은 같은 형식으로 다시, 삭제는 "잊어줘: ZQ123456".

### 2026-07-02T23:31:34.195461+00:00 - chat
- prompt: ZQ123456 무슨 공정이야
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2964
- llm_used: True
- answer: ZQ123456은 TEST_FUNCTION_STEP 공정입니다.

### 2026-07-02T23:31:37.564010+00:00 - chat
- prompt: ZW654321 무슨 공정이야
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3092
- llm_used: True
- answer: ZW654321은 CORRECTED_STEP_X 공정입니다. (사용자 학습 데이터에 기반)

### 2026-07-02T23:31:37.872301+00:00 - fewshot_forget
- prompt: 잊어줘: ZQ123456
- intent: fewshot_forget
- feature: fewshot
- answer: 'ZQ123456' 학습 데이터를 삭제했습니다.

### 2026-07-02T23:31:38.161078+00:00 - fewshot_forget
- prompt: 잊어줘: ZW654321
- intent: fewshot_forget
- feature: fewshot
- answer: 'ZW654321' 학습 데이터를 삭제했습니다.

### 2026-07-02T23:32:01.675958+00:00 - fewshot_teach
- prompt: 기억해: ZQ123456는 TEST_FUNCTION_STEP 공정
- intent: fewshot_teach
- feature: fewshot
- answer: 기억했습니다: ZQ123456 → TEST_FUNCTION_STEP 공정 다음부터 이 용어 질문에 바로 답합니다. 수정은 같은 형식으로 다시, 삭제는 "잊어줘: ZQ123456".

### 2026-07-02T23:32:04.815963+00:00 - chat
- prompt: ZQ123456 무슨 공정이야
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2904
- llm_used: True
- answer: ZQ123456은 TEST_FUNCTION_STEP 공정입니다. (hol 님이 학습시킨 데이터입니다.)

### 2026-07-02T23:32:05.102090+00:00 - fewshot_forget
- prompt: 잊어줘: ZQ123456
- intent: fewshot_forget
- feature: fewshot
- answer: 'ZQ123456' 학습 데이터를 삭제했습니다.

### 2026-07-02T23:49:47.585491+00:00 - file_doc_teach
- prompt: 파일 설명: ppid_knob.csv는 PPID value를 knob category로 분류하는 규칙과 feature_name 목록
- intent: file_doc_teach
- feature: fewshot
- answer: 파일 설명을 저장했습니다: ppid_knob.csv — PPID value를 knob category로 분류하는 규칙과 feature_name 목록 이제 이 설명과 관련된 질문에서 이 파일을 검색 대상으로 씁니다.

### 2026-07-02T23:49:50.244879+00:00 - chat
- prompt: PPID_08_0 값이 어디에 있는지 찾아줘
- intent: filebrowser_schema_search
- selected_function: search_filebrowser_schema
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2403
- llm_used: 
- answer: `PPID_08_0` 컬럼 후보를 찾지 못했습니다.

### 2026-07-02T23:49:52.577112+00:00 - chat
- prompt: UNKNOWN_TERM_ZZZ9 어디에 있는지 찾아줘
- intent: filebrowser_schema_search
- selected_function: search_filebrowser_schema
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2067
- llm_used: 
- answer: `UNKNOWN_TERM_ZZZ9` 컬럼 후보를 찾지 못했습니다.

### 2026-07-02T23:51:37.444064+00:00 - file_doc_teach
- prompt: 파일 설명: ppid_knob.csv는 PPID value를 knob category로 분류하는 규칙과 feature_name 목록
- intent: file_doc_teach
- feature: fewshot
- answer: 파일 설명을 저장했습니다: ppid_knob.csv — PPID value를 knob category로 분류하는 규칙과 feature_name 목록 이제 이 설명과 관련된 질문에서 이 파일을 검색 대상으로 씁니다.

### 2026-07-02T23:51:46.634578+00:00 - chat
- prompt: PPID_08_0 값이 어디에 있는지 찾아줘
- intent: file_doc_search
- selected_function: file_doc_search
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 8944
- llm_used: True
- answer: 'PPID_08_0' 검색 결과 (파일 설명 카탈로그 기반): [ppid_knob.csv] 2행 (value, category 열) - feature_name=8.0 SD_EPI, function_step=SD_EPI, rule_order=RO, operator=eq, value=PPID_08_0 - feature_name=8.0 SD_EPI, function_step=SD_EPI, rule_order=RO, operator=eq, value=PPID_08_0 수정이 필요하면 기타 메뉴의 Files에서 해당 파일을 열어 편집하세요.

### 2026-07-02T23:51:52.171640+00:00 - chat
- prompt: UNKNOWN_TERM_ZZZ9 어디에 있는지 찾아줘
- intent: file_doc_search_miss
- selected_function: file_doc_search_miss
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 5263
- llm_used: True
- answer: 'UNKNOWN_TERM_ZZZ9'에 대한 답을 아직 찾지 못했습니다. 관련 파일 설명이 아직 등록돼 있지 않습니다. 도와주실 수 있다면: - 답을 아시면 → "기억해: UNKNOWN_TERM_ZZZ9는 <답>" - 어느 파일에 있는지 아시면 → "파일 설명: <파일명>은 <설명>" 으로 등록해주세요. 다음부터 그 파일을 검색해 답합니다.

### 2026-07-11T05:04:11.281312+00:00 - chat
- prompt: GATE_ETCH Split 규칙이 어떻게 돼
- intent: ppid_knob
- selected_function: list_split_rules
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 4837
- llm_used: True
- answer: 'GATE_ETCH' Split 규칙은 다음과 같습니다 (4건): - R1: eq PP_GE_A1 → KNOB_A (GATE_ETCH) - R2: eq PP_GE_A2 → KNOB_B (GATE_ETCH) - R3: eq PP_X9_2300 → KNOB_NEW (GATE_ETCH) - RO: 나머지 → raw ppid 유지 (미분류 → 알람 대상) (GATE_ETCH)

### 2026-07-11T05:04:44.054835+00:00 - splittable_view
- prompt: A1006 스플릿테이블 보여줘
- intent: splittable_view
- feature: splittable
- answer: product가 없는 SplitTable 요청입니다. 어느 product 기준으로 볼지 알려주세요.

### 2026-07-11T05:09:23.958856+00:00 - split_nav
- prompt: A1006 스플릿테이블 보여줘 ML_TABLE
- intent: split_nav
- feature: splittable
- answer: SplitTable 을 엽니다 — root lot A1006 (PRODA). ML_TABLE 확인: A1006 → PRODA

### 2026-07-11T07:16:42.779182+00:00 - split_nav
- prompt: A1006 스플릿테이블 보여줘 product: PRODA에서 KNOB_CONTACT_ETCH_ppid 값이 KNOB_STD 인 lot 을 찾아줘
- intent: split_nav
- feature: splittable
- answer: SplitTable 을 엽니다 — root lot A1006 (PRODA). ML_TABLE 확인: A1006 → PRODA

### 2026-07-11T07:17:17.716307+00:00 - split_nav
- prompt: A1006 스플릿테이블 보여줘 product: PRODA에서 KNOB_CONTACT_ETCH_ppid 값이 KNOB_STD 인 lot 을 찾아줘
- intent: split_nav
- feature: splittable
- answer: SplitTable 을 엽니다 — root lot A1006 (PRODA). ML_TABLE 확인: A1006 → PRODA

### 2026-07-11T07:18:37.000472+00:00 - chat
- prompt: PRODA에서 KNOB_CONTACT_ETCH_ppid 값이 KNOB_STD 인 lot 을 찾아줘
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 13493
- llm_used: 
- answer: 어느 공정(step)에서 'KNOB_CONTACT_ETCH_ppid' 값이 'KNOB_STD'인 lot을 찾으시나요?

### 2026-07-11T07:19:18.256248+00:00 - chat
- prompt: PRODA에서 KNOB_CONTACT_ETCH_ppid 값이 KNOB_STD 인 lot 을 찾아줘 (사용자 답변: CONTACT_ETCH 공정)
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 11201
- llm_used: True
- answer: PRODA에서 KNOB_CONTACT_ETCH_ppid 값이 KNOB_STD인 lot을 찾는 요청을 처리하는 데 실패했습니다. 시스템 문제로 인해 현재 정보를 가져올 수 없습니다. 다시 시도해 주십시오.

### 2026-07-11T07:19:22.419167+00:00 - chat
- prompt: A1006 lot 이 지금 어느 step 까지 진행됐는지 확인해줘
- intent: step_lookup
- selected_function: lookup_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3831
- llm_used: True
- answer: 해당 step_id / function_step 을 step_matching.csv 에서 찾지 못했습니다. 정답을 알고 계시면 "기억해: <용어>는 <답>" 형태로 알려주세요. 저장해두고 다음부터 바로 답합니다.

### 2026-07-11T07:21:23.344805+00:00 - chat
- prompt: A1006 lot 이 지금 어느 step 까지 진행됐는지 확인해줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 16775
- llm_used: True
- answer: A1006 lot의 현재 진행 단계를 확인하려 했으나, 정보를 가져오는 데 실패했습니다.

### 2026-07-11T07:21:26.203342+00:00 - chat
- prompt: AA100090이 무슨 step이야
- intent: step_lookup
- selected_function: id_to_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2591
- llm_used: True
- answer: AA100090는 PRODA의 SD_EPI step입니다.

### 2026-07-11T07:22:09.352930+00:00 - chat
- prompt: A1006 lot 의 knob 값들이 뭔지 ML_TABLE 에서 확인해줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 12942
- llm_used: True
- answer: A1006 로트의 knob 값을 ML_TABLE에서 조회하는 데 실패했습니다.

### 2026-07-11T07:26:02.707137+00:00 - chat
- prompt: A1006 lot 의 knob 값들이 뭔지 ML_TABLE 에서 확인해줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 15032
- llm_used: True
- answer: A1006 lot의 ML_TABLE에서 조회된 KNOB 값은 25건입니다.

### 2026-07-11T07:26:50.886258+00:00 - chat
- prompt: A1006 lot 이 지금 어느 step 까지 진행됐는지 확인해줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 24588
- llm_used: 
- answer: [Lot 현재 Step 조회] 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=-, lot=A1006, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-07-11T08:26:11.945335+00:00 - chat
- prompt: A1006 lot 의 knob 값들이 뭔지 ML_TABLE 에서 확인해줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 25172
- llm_used: True
- answer: A1006 lot의 ML_TABLE에 있는 knob 값들을 확인했습니다. 총 25건의 결과가 있습니다.

### 2026-07-11T08:26:20.206430+00:00 - chat
- prompt: GATE_ETCH 규칙 알려줘
- intent: ppid_knob
- selected_function: list_split_rules
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 7915
- llm_used: True
- answer: 'GATE_ETCH' Split 규칙은 다음과 같습니다 (총 4건): - R1: PP_GE_A1과 같으면 KNOB_A (GATE_ETCH) - R2: PP_GE_A2와 같으면 KNOB_B (GATE_ETCH) - R3: PP_X9_2300과 같으면 KNOB_NEW (GATE_ETCH) - RO: 나머지 경우 raw ppid 유지 (미분류 시 알람 대상) (GATE_ETCH)

### 2026-07-12T11:58:49.759645+00:00 - chat
- prompt: PRD-A 3200 스텝 어느 vehicle에 매칭돼?
- intent: step_mapping_lookup
- selected_function: query_step_mapping_lookup
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 19398
- llm_used: 
- answer: matching/rulebook CSV에서 ML_TABLE에 해당하는 step mapping을 찾지 못했습니다.

### 2026-07-12T11:59:37.072599+00:00 - chat
- prompt: PRODA 스플릿 규칙 알려줘
- intent: splittable_guidance
- selected_function: open_splittable
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 827
- llm_used: 
- answer: 스플릿 테이블 단위기능으로 처리하는 요청입니다. - 실행 경로: open_splittable - 필요한 조건: product, root_lot_id, wafer_id or all, parameter prefix such as KNOB/MASK/FAB - 현재 감지: product=PRODA, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-07-12T11:59:37.436082+00:00 - split_nav
- prompt: PRODA 스플릿테이블 보여줘
- intent: split_nav
- feature: splittable
- answer: SplitTable 을 엽니다 (PRODA).

### 2026-07-12T11:59:40.196679+00:00 - chat
- prompt: A0049 랏 지금 어느 step까지 진행됐어?
- intent: tablemap_guidance
- selected_function: open_tablemap
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2575
- llm_used: 
- answer: 테이블 맵 단위기능으로 처리하는 요청입니다. - 실행 경로: open_tablemap - 필요한 조건: source table/column, target table/column - 현재 감지: product=PRODA, lot=A0049, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-07-12T12:00:36.522026+00:00 - chat
- prompt: PRODA IOFF가 유난히 높은 lot이 있는지 확인하고 원인 후보를 정리해줘
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 10893
- llm_used: 
- answer: IOFF가 어떤 측정항목(metric) 또는 어떤 데이터의 item명과 unit/test_structure에 해당하는지 알려주세요.

### 2026-07-12T13:24:27.353960+00:00 - split_nav
- prompt: PRODA 스플릿테이블 보여줘
- intent: split_nav
- feature: splittable
- answer: SplitTable 을 엽니다 (PRODA).

### 2026-07-12T13:24:40.533095+00:00 - chat
- prompt: A0049 랏 지금 어느 step까지 진행됐어?
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 13000
- llm_used: 
- answer: A0049 랏의 제품(product) 이름을 알려주시겠어요?

### 2026-07-12T13:27:07.645547+00:00 - split_nav
- prompt: PRODA 스플릿테이블 보여줘
- intent: split_nav
- feature: splittable
- answer: PRODA ML_TABLE에서 root lot 12건을 조회했습니다. root lot을 지정하면 스플릿 매트릭스를 바로 보여드립니다. 아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요.

### 2026-07-12T13:27:08.202742+00:00 - split_nav
- prompt: A1003 스플릿테이블 보여줘
- intent: split_nav
- feature: splittable
- answer: ML_TABLE_PRODA A1003 SplitTable KNOB 기준으로 27개 row를 조회했습니다. 아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요.

### 2026-07-12T13:27:50.090776+00:00 - chat
- prompt: PRODA 스플릿 규칙 알려줘
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 16184
- llm_used: 
- answer: PRODA 제품의 스플릿 규칙을 확인하시려는 특정 LOT ID나 WAFER ID가 있으신가요?

### 2026-07-12T13:56:39.963511+00:00 - chat
- prompt: PRODA에서 IOFF가 높은 wafer 상위 10개 찾아줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 9292
- llm_used: True
- answer: PRODA에서 IOFF가 높은 웨이퍼 상위 10개를 찾으려면 데이터베이스 또는 특정 파일 대상이 필요하지만, 현재는 해당 정보가 지정되지 않아 쿼리를 실행할 수 없습니다.

### 2026-07-12T14:00:33.760169+00:00 - chat
- prompt: PRODA에서 IOFF가 높은 wafer 상위 10개 찾아줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 28015
- llm_used: 
- answer: [FileBrowser AI SQL] SELECT wafer_id WHERE TRUE · preview 100 rows / total 101 · warnings 1

### 2026-07-12T14:02:29.054583+00:00 - chat
- prompt: PRODA에서 IOFF가 높은 wafer 상위 10개 찾아줘
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 31397
- llm_used: 
- answer: 어떤 데이터 소스(예: ET, INLINE, VM, EDS 등)에서 IOFF 값을 확인할 수 있나요? 또는 IOFF가 어떤 종류의 측정치나 지표를 의미하나요?

### 2026-07-18T14:40:53.999459+00:00 - chat
- prompt: CC955100 가 무슨 step이야?
- intent: step_lookup
- selected_function: id_to_step
- retrieved_ids: ['column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2841
- llm_used: True
- answer: CC955100는 PRODA의 SPACER_CVD step입니다.

### 2026-07-18T14:41:12.473933+00:00 - split_nav
- prompt: A1002 스플릿테이블 보여줘
- intent: split_nav
- feature: splittable
- answer: ML_TABLE_PRODA A1002 SplitTable KNOB 기준으로 27개 row를 조회했습니다. 아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요.

### 2026-07-18T14:42:21.606358+00:00 - chat
- prompt: 대시보드로 PRODA step_desc 형태로 보여줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 43932
- llm_used: 
- answer: [Dashboard Agent] status warning · chart table · points 12 · warnings 1

### 2026-07-18T14:42:23.119828+00:00 - split_nav
- prompt: A1002 스플릿테이블 보여줘 3.0 VTN, 4.0 GATE_OX KNOB만 보여줘
- intent: split_nav
- feature: splittable
- answer: ML_TABLE_PRODA A1002 3.0 VTN ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다. 아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요.

### 2026-07-18T14:42:31.526903+00:00 - split_nav
- prompt: A1002 스플릿테이블 보여줘 3.0 VTN, 4.0 GATE_OX 2개 KNOB만 보여줘
- intent: split_nav
- feature: splittable
- answer: ML_TABLE_PRODA A1002 3.0 VTN ad-hoc CUSTOM SET 기준으로 1개 row를 조회했습니다. 아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요.

### 2026-07-18T14:43:47.034785+00:00 - chat
- prompt: 3.0 VTN split 어떻게 구성되어있어?
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 4822
- llm_used: 
- answer: 어떤 제품(product)과 랏(lot_id)의 '3.0 VTN split' 구성을 알고 싶으신가요? 예를 들어, 'PRODA A1000 #1 3.0 VTN split 구성'과 같이 알려주세요.
