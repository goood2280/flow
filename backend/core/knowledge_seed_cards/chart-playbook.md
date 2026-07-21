---
term: 차트 질문 플레이북
kind: playbook
aliases: [차트, 그래프, 그려줘, trend, scatter, 산점도, 상관, correlation, 시각화, plot]
trigger_terms: [그려, 차트, 그래프, 추세, 컬러, 색깔로]
answered_by: dashboard
sources:
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
  - file: reformatter/{vehicle}_reformatter.csv
    role: et_reformat
related: [et-index-chart, chart-agg-defaults, chart-color-split, chart-raw-data, commonality]
status: active
---
flow-i 는 자연어 요청을 DuckDB/Polars 쿼리로 실행해 차트를 즉시 그린다 (dashboard 계열 handler + home_sql_join_dashboard 멀티소스 JOIN 유닛).

대표 차트 유형:
- **trend**: y(측정 index)를 시간축에 — x 기본은 tkout_time(DC 측정시각) 또는 FAB 시간(ML_TABLE 내 시간 컬럼).
- **scatter/correlation**: ET Index vs INLINE 등 두 항목의 상관. 양쪽에 다 있는 wafer/shot 만 교집합으로 남겨 계산.
- **box / group_metric**: 조건(knob/eqp)별 분포 비교 — 유의차 판단용.

요청에 자주 붙는 옵션 (각각 전용 카드 참조):
- 집계 방법(ET median 기본, INLINE avg 기본, shot=전체, P90/P10/max) → chart-agg-defaults.
- 색/그룹 분리(knob·eqp·eqp_chamber 별) → chart-color-split.
- lot 제외("○○ 랏 빼줘"), 기간("언제 이후만") 필터 → chart-color-split 하단.
- 차트 raw data 공유/다운로드 → chart-raw-data.

원칙: 규칙=ppid_knob.csv, split 실적=ML_TABLE, ET 변환값=reformatter(ET Index) — 근거 소스를 답변에 명시한다.
이상 여부는 flow-i 가 단정하지 않는다 — **차트를 그려 엔지니어가 보고 판단하게 하는 것**이 목적이다 (spec 은 개발단 기준이라 참고용, et-representative 참조).
