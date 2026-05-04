# Flowi Agent Entrypoints

가벼운 라우팅 인덱스다. 먼저 아래 3개 core 기능 중 하나로 분류하고, 고른 기능의 상세 가이드만 읽어 실행한다.

## Core

- filebrowser: 파일탐색기, parquet/csv, DB preview, schema/스키마, 컬럼, row 조회
- splittable: 스플릿테이블, SplitTable, plan, actual, KNOB, MASK, CUSTOM set, mismatch
- inform: 인폼로그, 인폼, 모듈 인폼, 공지/공유, 메일 preview, 등록 초안/확인

## Secondary

- dashboard: 차트, trend, 그래프, scatter, 상관, EQP/Chamber별
- tracker: 이슈, tracker, 모니터링, Analysis, 등록
- meeting: 회의, 미팅, 아젠다, 반복 회의
- calendar: 일정, 캘린더, 변경점, schedule
- ettime: ET median, elapsed, step/item/wf별 측정
- diagnosis: DIBL, VTH, SS, ION, IOFF, RCA, 원인 후보
- waferlayout: TEG, shot, die, wafer layout, edge
- tablemap: table map, relation, join path, 컬럼 관계

공통 slot 규칙은 서버의 workflow guide를 따른다. 특히 5자 mixed token은 root_lot_id, 점 suffix 또는 6자 이상 mixed lot은 fab_lot_id, step_id는 영문 2자+숫자 6자리만 인정한다.
