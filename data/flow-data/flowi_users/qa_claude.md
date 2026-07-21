# Flowi User Context: qa_claude

- Created: 2026-07-19T02:07:30.830099+00:00
- Updated: 2026-07-19T02:07:30.830099+00:00

## User Notes
<!-- FLOWI_USER_NOTES_START -->

<!-- FLOWI_USER_NOTES_END -->

## Activity Log

### 2026-07-19T02:07:30.839065+00:00 - chat
- prompt: PRODA IOFF spec 0.95 이하 spec out map 그려줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:wafer map', 'card:차트 질문 플레이북']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 9523
- llm_used: 
- answer: PRODA ET IOFF spec(high 0.95) 기준 spec out map을 그렸습니다. shot median 기준 out 69/117 — 빨간색=spec out, 회색=in spec. 좌표=shot_x/shot_y.

### 2026-07-19T03:37:20.867837+00:00 - chat
- prompt: PRODA 3.0 VTN ET trend 그려줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:ET Index', 'card:3.0 VTN', 'card:차트 질문 플레이북', 'card:ET DB']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 30189
- llm_used: 
- answer: PRODA VTH ET Trend 차트를 그렸습니다. tkout_time을 x축으로 하는 scatter 플롯이며, ET는 lot_wf별 median 값 기준입니다. 혹시 VTN이 아니라 VTH가 맞으신가요?

### 2026-07-19T03:38:04.637388+00:00 - chat
- prompt: PRODA 7.0 SPACER INLINE trend 그려줘 grain: lot_wf
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:7.0 SPACER', 'card:inline_matching.csv', 'card:차트 질문 플레이북']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 3977
- llm_used: 
- answer: PRODA 7.0 SPACER INLINE에서 어떤 측정값 (metric)의 트렌드를 보고 싶으신가요? (예: CD, 두께 등)

### 2026-07-19T03:38:53.455523+00:00 - chat
- prompt: PRODA INLINE 7.0 SPACER trend 그려줘
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:7.0 SPACER', 'card:inline_matching.csv', 'card:차트 질문 플레이북']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 19463
- llm_used: 
- answer: PRODA INLINE 7.0 SPACER의 어떤 INLINE grain (예: 특정 측정값, CD, Overlay 등)에 대한 트렌드를 확인하고 싶으신가요?

### 2026-07-19T03:39:28.618883+00:00 - chat
- prompt: lot_wf avg 그레인으로 그려줘
- intent: react_ask_user
- selected_function: ask_user
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'card:차트 질문 플레이북']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 10687
- llm_used: 
- answer: '그레인'이 어떤 데이터 소스(ET, INLINE, VM, EDS, FAB 등)의 어떤 측정값(metric)을 의미하는지 알려주세요.

### 2026-07-19T03:45:18.288959+00:00 - chat
- prompt: PRODA A1000 스플릿 KNOB 조건 보여줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:split 질문 플레이북']
- system_knowledge_ids: 
- result_status: success
- elapsed_ms: 18948
- llm_used: True
- answer: PRODA A1000의 KNOB 구성은 custom set 기준으로 다음과 같습니다: - custom_set_1: #1, #5, #9, #13, #17, #21, #25 웨이퍼에 적용되며, 1.0 STI=PPID_01_1_S1, 2.0 WELL=PPID_02_2_S1, 3.0 VTN=PPID_03_3_S1, 4.0 GATE_OX=PPID_04_0_S1, 5.0 PC=PPID_05_1_S1, 6.0 LDD=PPID_06_2_S1, 7.0 SPACER=PPID_07_3_S1, 8.0 SD_EPI=PPID_08_0_S1 입니다. - custom_set_2: 1.0 STI=PPID_01_1_S2, 2.0 WELL=PPID_02_2_S2, 3.0 VTN=PPID_03_3_S2 등입니다. (자세한 내용은 관찰 기록 참조)

### 2026-07-19T03:45:58.779838+00:00 - chat
- prompt: PRODA A1000 스플릿을 qaset custom으로 보여줘
- intent: react_orchestration
- selected_function: react_loop
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:split 질문 플레이북']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 17934
- llm_used: 
- answer: [SplitTable View 조회] ET parquet을 찾지 못했습니다.

### 2026-07-19T03:46:48.023578+00:00 - chat
- prompt: PRODA A1000 스플릿 qaset custom 컬럼 조건 보여줘
- intent: filebrowser_sql_llm_draft
- selected_function: filebrowser.sql.llm.draft
- retrieved_ids: ['column:ML_TABLE_PRODA.lot_wf', 'column:ML_TABLE_PRODA.root_lot_id', 'column:ML_TABLE_PRODA.step_id', 'card:split 질문 플레이북']
- system_knowledge_ids: 
- result_status: error
- elapsed_ms: 8025
- llm_used: 
- answer: ET/PRODA 기준 FileBrowser SQL 초안을 만들고 preview 0행을 확인했습니다.

### 2026-07-19T03:47:24.013628+00:00 - splittable_plan
- prompt: PRODA A1000 wafer 1~5에 3.0 VTN knob plan PPID_03_3_S1 넣어줘
- intent: splittable_plan_confirm
- feature: splittable
- answer: SplitTable plan 저장 준비가 됐습니다. 확인 선택을 누르면 실제 plan 저장소에 반영합니다.

### 2026-07-19T03:48:54.337985+00:00 - split_nav
- prompt: FLOWI_SPLITTABLE_PLAN {"product": "ML_TABLE_PRODA", "root_lot_id": "A1000", "knob": "KNOB_3.0 VTN", "plans": {"A1000|1|KNOB_3.0 VTN": "5"}, "assignments": [{"wafers": ["1"], "value": "5", "label": "wafer 1"}], "summary": ["wafer 1: 5 (1wf)"], "invalid_wafers": [], "username": "qa_claude", "knob_candidates": ["KNOB_3.0 VTN"], "source": "splittable.view", "confirm": "SPLITTABLE_PLAN_CONFIRM::ML_TABLE_PRODA|A1000|KNOB_3.0 VTN|1"}
- intent: split_nav
- feature: splittable
- answer: ML_TABLE_PRODA A1000 SplitTable KNOB 기준으로 28개 row를 조회했습니다. 아래는 조회된 스플릿 데이터입니다. 전체 화면은 SplitTable 열기 버튼을 사용하세요.

### 2026-07-19T03:52:41.438889+00:00 - splittable_plan
- prompt: PRODA A1000 wafer 1~5에 3.0 VTN knob plan PPID_03_3_S1 넣어줘
- intent: splittable_plan_confirm
- feature: splittable
- answer: SplitTable plan 저장 준비가 됐습니다. 확인 선택을 누르면 실제 plan 저장소에 반영합니다.

### 2026-07-19T03:53:11.789573+00:00 - splittable_plan
- prompt: FLOWI_SPLITTABLE_PLAN {"product": "ML_TABLE_PRODA", "root_lot_id": "A1000", "knob": "KNOB_3.0 VTN", "plans": {"A1000|1|KNOB_3.0 VTN": "PPID_03_3_S1"}, "assignments": [{"wafers": ["1", "2", "3", "4", "5"], "value": "PPID_03_3_S1", "label": "wafer 1~5"}], "summary": ["wafer 1~5: PPID_03_3_S1 (1wf)"], "invalid_wafers": [], "username": "qa_claude", "knob_candidates": ["KNOB_3.0 VTN"], "source": "splittable.view", "confirm": "SPLITTABLE_PLAN_CONFIRM::ML_TABLE_PRODA|A1000|KNOB_3.0 VTN|1"}
- intent: splittable_plan_saved
- feature: splittable
- answer: SplitTable plan을 저장했습니다. - product: ML_TABLE_PRODA - lot: A1000 - KNOB: KNOB_3.0 VTN - 저장 cell: 1

### 2026-07-19T03:53:44.043175+00:00 - inform_log_draft
- prompt: PRODA A1000 랏 PC 모듈로 인폼 등록해줘. 사유: QA 확인, note: spec 점검 완료
- intent: inform_log_draft
- answer: 인폼 등록 초안에 필요한 값을 선택해 주세요.

### 2026-07-19T03:54:18.586789+00:00 - inform_log_draft
- prompt: PRODA A1000 랏 PC 모듈로 인폼 등록해줘. 사유: QA 확인, note: spec 점검 완료 split_set: test1
- intent: inform_log_draft
- answer: 인폼 1건을 등록 전 미리보기로 만들었습니다. 확인 전에는 저장하지 않습니다.

### 2026-07-19T03:54:40.739131+00:00 - inform_log_draft
- prompt: FLOWI_INFORM_CONFIRM {"draft_id": "draft_20260719T035418_84428909", "confirm": true}
- intent: inform_log_registered
- answer: 인폼 1건을 등록했습니다. 메일 발송은 아래에서 별도 확인한 경우에만 진행합니다.

### 2026-07-19T03:55:07.587723+00:00 - inform_mail_preview
- prompt: FLOWI_INFORM_MAIL {"inform_ids": ["inf_260719_8b44de"], "confirm": true}
- intent: inform_module_mail_preview
- answer: 메일 미리보기에 필요한 값을 선택해 주세요.

### 2026-07-19T03:56:57.829622+00:00 - inform_mail_confirm
- prompt: FLOWI_INFORM_MAIL {"inform_ids": ["inf_260719_8b44de"], "confirm": true} FLOWI_INFORM_MAIL {"inform_ids": ["inf_260719_8b44de"], "confirm": true}
- intent: inform_mail_failed
- answer: 1건은 보내지 못했습니다: inf_260719_8b44de: 수신자 이메일이 없습니다 (유저 email 또는 group 을 먼저 설정하세요).

### 2026-07-19T03:57:39.941684+00:00 - inform_mail_confirm
- prompt: FLOWI_INFORM_MAIL {"inform_ids": ["inf_260719_8b44de"], "confirm": true} FLOWI_INFORM_MAIL {"inform_ids": ["inf_260719_8b44de"], "confirm": true}
- intent: inform_mail_sent
- answer: 인폼 메일 1건을 발송했습니다 (dry-run 구성 검증).
