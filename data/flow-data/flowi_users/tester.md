# Flowi User Context: tester

- Created: 2026-05-30T14:26:46.677772+00:00
- Updated: 2026-05-30T14:26:46.677772+00:00

## User Notes
<!-- FLOWI_USER_NOTES_START -->

<!-- FLOWI_USER_NOTES_END -->

## Activity Log

### 2026-05-30T14:26:46.690062+00:00 - chat
- prompt: 안녕
- intent: filebrowser_guidance
- selected_function: open_filebrowser
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 5271
- llm_used: 
- answer: 파일 탐색기 단위기능으로 처리하는 요청입니다. - 실행 경로: open_filebrowser - 필요한 조건: source/root, product or file, optional SQL/filter - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-30T14:26:51.773486+00:00 - chat
- prompt: ㅎㅎ
- intent: filebrowser_guidance
- selected_function: open_filebrowser
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 4302
- llm_used: 
- answer: 파일 탐색기 단위기능으로 처리하는 요청입니다. - 실행 경로: open_filebrowser - 필요한 조건: source/root, product or file, optional SQL/filter - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-30T14:26:56.840914+00:00 - chat
- prompt: 도움말
- intent: filebrowser_guidance
- selected_function: open_filebrowser
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 4625
- llm_used: 
- answer: 파일 탐색기 단위기능으로 처리하는 요청입니다. - 실행 경로: open_filebrowser - 필요한 조건: source/root, product or file, optional SQL/filter - 현재 감지: product=-, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-30T14:27:00.057933+00:00 - chat
- prompt: PRODA 차트 그려줘
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 2612
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: - - 감지 metric 후보: - - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 아래 선택지에서 먼저 확인할 범위를 골라주세요.

### 2026-05-31T07:49:50.150506+00:00 - chat
- prompt: A1001 스플릿테이블 보여줘
- intent: splittable_view
- selected_function: query_splittable_view
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3895
- llm_used: 
- answer: ML_TABLE_PRODA A1001 SplitTable KNOB,MASK,FAB 기준으로 72개 row를 조회했습니다.

### 2026-05-31T07:50:58.699715+00:00 - chat
- prompt: 이번 분기 운영 전략을 어떻게 잡으면 좋을지 자유롭게 조언해줘
- intent: filebrowser_guidance
- selected_function: open_filebrowser
- retrieved_ids: 
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1985
- llm_used: 
- answer: 파일 탐색기 단위기능으로 처리하는 요청입니다. - 실행 경로: open_filebrowser - 필요한 조건: source/root, product or file, optional SQL/filter - 현재 감지: product=ML_TABLE_PRODA, lot=-, step=- Flowi는 조회/요약/표시만 수행하고 DB/Files 원본은 수정하지 않습니다.

### 2026-05-31T14:17:43.038565+00:00 - chat
- prompt: A1001 #3 지금 어디에 있어?
- intent: fab_current_location_lookup
- selected_function: query_current_location
- retrieved_ids: ['column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1052
- llm_used: 
- answer: A1001 #3 현재 위치는 step_id=AA100150 입니다. 최신 tkout_time: 2024-04-24T10:09:00.

### 2026-05-31T14:25:01.220720+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_scatter_plan
- selected_function: build_metric_scatter
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: missing
- elapsed_ms: 1063
- llm_used: 
- answer: 차트/상관 분석 단위기능으로 처리할 요청입니다. Flowi는 metric 이름을 지어내지 않고 schema catalog와 실제 DB row로만 차트를 만듭니다. - 감지 source: FAB - 감지 metric 후보: LKG, IOFF - 기본 집계: INLINE avg, ET median - 기본은 WF Agg입니다. shot/die/map을 명시한 경우에만 shot 단위 매칭을 시도합니다. 조건은 충분하지만 실제 차트 계산에 실패했습니다. 아래 계획과 오류를 확인해주세요.

### 2026-05-31T14:26:09.622762+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1010
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-05-31T14:26:21.591027+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 918
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-05-31T14:27:28.764583+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 865
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-05-31T14:30:36.384625+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 851
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-06-08T06:12:32.022510+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 689
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-06-08T07:12:11.213974+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 687
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-06-08T07:57:21.205499+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 686
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T14:41:42.368814+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 712
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T14:43:55.418034+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 706
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T15:05:27.349196+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 736
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T23:18:00.024137+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 791
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T23:33:16.326085+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 701
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T23:35:25.714677+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 705
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-02T23:52:47.365408+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 704
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-03T04:31:13.206207+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 721
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-03T05:23:25.211136+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 749
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-03T06:40:24.425679+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 716
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-12T22:41:43.276034+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1668
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-13T14:39:08.582744+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1100
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-14T22:39:49.681437+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1209
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-14T22:45:56.538187+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1026
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-15T14:20:12.346511+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 3104
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-15T14:39:38.738675+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 976
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-15T15:07:47.069865+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1019
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-15T23:03:14.003980+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1859
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-18T16:21:23.135318+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2269
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-18T17:28:22.351239+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 694
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-19T00:58:13.595264+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2327
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-19T01:06:03.105433+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 1922
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-19T01:19:17.031546+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 2170
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1

### 2026-07-19T01:37:39.304586+00:00 - chat
- prompt: PRODA FAB IOFF chart
- intent: dashboard_source_chart_runtime
- selected_function: dashboard.source_chart_runtime
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 683
- llm_used: 
- answer: Dashboard Agent source orchestration으로 차트를 생성했습니다. - source: db_FAB - selected columns: wafer_id, IOFF - join: single source - rows: 1
