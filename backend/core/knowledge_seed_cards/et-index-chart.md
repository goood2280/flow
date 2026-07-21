---
term: ET Index
kind: concept
aliases: [ET index, ET 인덱스, ET 지표, ET 값 차트]
trigger_terms: [ET Index, ET 상관, ET trend, ET 그려]
answered_by: dashboard
sources:
  - file: reformatter/{vehicle}_reformatter.csv
    role: et_reformat
  - file: 1.RAWDATA_DB_FAB
    role: fab_db
related: [et-db, reformatter, chart-playbook, chart-agg-defaults]
status: active
---
ET Index 는 **reformatter 를 반영해 뽑은 ET 지표값**이다 (raw ET 가 아니라 ET Index 탭 산출값 기준).

대표 차트 패턴:
- **ET Index vs INLINE 상관** (Corr./relation chart): 두 소스를 wafer/shot 교집합으로 맞춰 scatter + 상관/피팅.
- **ET Index vs 시간축**: x = FAB 시간(ML_TABLE 내 시간 컬럼) 또는 tkout_time(DC 측정시각). trend 차트로 가장 많이 그린다.
- 집계 기본: ET 는 median, INLINE 은 avg — chart-agg-defaults 참조. "shot 으로 그려줘" 는 전체 측정 point 를 집계 없이 그린다.

Corr 은 INLINE/ET/VM 중 **임의 2소스 조합**을 지원한다 (INLINE↔ET, INLINE↔VM, ET↔VM). VM 은 wafer 단위(shot 없음)이며 여러 값이면 avg 기본. y 슬롯(주로 ET/VM)에 집계 override("median/P90 으로")가 적용된다.

Trend(tkout_time x축)는 **ET/INLINE/VM 각각 지원**한다. 소스별 기본 집계: ET=median, INLINE·VM=avg. "shot 으로/median/P90/max" 로 집계를 바꿀 수 있고, knob 으로 색 분리도 된다. (VM 은 wafer 단위라 shot 은 lot_wf 와 사실상 동일.)
