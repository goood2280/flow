# Feature Goals

이 문서는 각 페이지가 왜 존재하는지와 기능 추가 기준을 정리한다.

## Home

목표: 사용자가 로그인 직후 지금 볼 일과 최근 변경을 빠르게 파악한다.

추가 기준:
- 최근 변경, 알림, 다음 행동 추천만 둔다.
- 복잡한 설정이나 분석 기능은 넣지 않는다.

## FileBrowser

목표: DB/Base 루트의 파일 존재, 스키마, 미리보기, S3 동기화 상태를 확인한다.

추가 기준:
- 원천 데이터 진단과 preview에 집중한다.
- 분석 로직은 Dashboard/SplitTable로 넘긴다.

## SplitTable

목표: `product + lot + wafer` 기준으로 plan, actual, diff, notes, rulebook 매핑을 관리한다.

추가 기준:
- plan 저장, history, final value, drift를 명확히 유지한다.
- notes, rulebook, product scan은 service/repository로 분리한다.
- 화면은 matrix 작업대 역할에 집중한다.

## Dashboard

목표: SPC성 추세, KPI, fab progress, chart snapshot을 빠르게 확인한다.

추가 기준:
- 차트 설정과 계산을 분리한다.
- raw data 탐색은 FileBrowser로, plan 편집은 SplitTable로 넘긴다.
- 자동 refresh와 snapshot 상태가 보여야 한다.
- dashboard 권한이 있는 사용자가 Charts/FAB Progress/Alert Watch 중 볼 섹션을 admin gear에서 정할 수 있어야 한다.

## Tracker

목표: 개발 이슈, lot watch, 분석 액션을 생성부터 종료까지 추적한다.

추가 기준:
- 이슈는 상태, priority, category, group visibility가 명확해야 한다.
- category 지정은 필수이며 비어 있으면 저장/메일 전에 안내한다.
- lot/wafer watch는 FAB/ET source 의미가 분리되어야 한다.
- tracker 변경은 알림과 audit 후보가 된다.
- 메일 설정은 lot/wafer 행이 아니라 이슈 단위로 관리한다.
- Analysis는 ET DB에 연결되며 측정 상세는 `step_id` 아래 `step_seq(XXpt)` 단위로 보여준다.
- 신규 ET 측정 알림은 step/seq 조건과 stable delay 설정을 분리해서 관리한다.

## Inform

목표: 제품/lot/wafer 단위 이슈를 모듈 담당자에게 전달하고 후속 스레드로 남긴다.

추가 기준:
- product contacts, mail modal, SplitTable embed는 각각 분리한다.
- 첨부와 메일은 실패 가능성을 UI에 보여준다.
- 담당자/마감/상태가 운영 기록으로 남아야 한다.

## Meeting

목표: 회의, 아젠다, 결정사항, 액션아이템을 운영 데이터로 남긴다.

추가 기준:
- 회의록 작성과 calendar push는 분리된 service로 관리한다.
- 실시간 편집보다 기록 무결성과 충돌 방지가 우선이다.
- tracker issue import는 글과 이미지를 함께 가져온다.
- 메일 발송 시 이미지 용량이 과하면 아젠다 이미지는 제외하고 텍스트 요약과 링크를 남긴다.

## Calendar

목표: tracker, meeting action, decision의 날짜와 진행 상태를 한 곳에서 본다.

추가 기준:
- calendar 자체 입력과 외부 push 항목의 출처를 구분한다.
- 상태 변경은 원본 엔터티와 동기화되어야 한다.

## ET Report / ETTime

목표: ET 측정 패키지, step_seq, reformatter index, 측정 시점을 lot 단위로 추적한다.

추가 기준:
- `request_id` 또는 measurement package 개념을 우선한다.
- 단순 step_id만으로 ET를 해석하지 않는다.
- 제품/lot 검색에서 출발하고 lot 선택 후 scoreboard와 측정 시간을 보여준다.
- 측정량은 `seq1(60pt), seq2(20pt)`처럼 0pt를 제외한 실제 측정 step_seq별 point로 표시한다.
- 제품 reformatter에 설정된 index는 index당 한 페이지로 Statistical Table, Box Table, WF Map, Trend, Radius Plot, Cumulative Plot을 제공한다.

## WF Layout

목표: wafer/shot/chip 좌표 기반으로 공간 패턴을 확인한다.

추가 기준:
- layout registry와 측정 데이터는 분리한다.
- 좌표 변환은 명시적 mapping을 따른다.
- WF 상단은 wafer/shot/chip 범위를 우선하고 TEG 전체 overlay는 표시하지 않는다.
- TEG는 Shot Sample 안에서 선택/검색된 항목만 확인한다.
- Chip View는 각 chip이 속한 shot을 표로 보여주고 CSV로 내려받을 수 있어야 한다.

## TableMap

목표: 작은 lookup/base table과 관계를 관리한다.

추가 기준:
- 대용량 분석은 하지 않는다.
- relation hint와 CSV 편집 이력을 분명히 한다.
- Product Connection product page는 숨김/복원할 수 있어야 한다.
- 제품 설정 YAML은 단일 `product_config/products.yaml` 안에서 block 단위로 추가/삭제 관리한다.

## ML

목표: Y에 대한 feature 영향 후보를 뽑고, 도메인 방향성과 함께 해석한다.

추가 기준:
- 통계 결과는 원인으로 단정하지 않는다.
- source, step, area, direction 신뢰도를 함께 보여준다.

## Admin

목표: 사용자, 권한, 그룹, data root, 백업, 설정, 모니터링을 관리한다.

추가 기준:
- 운영 설정은 사용자 기능과 섞지 않는다.
- 탭 단위로 panel/component를 분리한다.
- 권한 없는 사용자가 설정을 읽거나 바꾸지 못해야 한다.
