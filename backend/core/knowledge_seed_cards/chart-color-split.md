---
term: 차트 색·조건 분리
kind: concept
aliases: [컬러 나눠, 색으로 구분, eqp별, chamber별, 챔버별, 랏 빼줘, 이후만]
trigger_terms: [컬러 나눠, 색으로, eqp_chamber, 챔버별, 빼줘, 제외, 이후, 이후만]
answered_by: dashboard
sources:
  - file: ML_TABLE_{PRODUCT}.parquet
    role: split_base
  - file: 1.RAWDATA_DB_FAB
    role: fab_db
related: [chart-playbook, commonality, knob-naming]
status: active
---
유의차 판단을 위한 색/그룹 분리와 필터 (SplitTable·차트 공통).

**색/그룹 기준(color_by / group_by)** — "○○ 별로 색 나눠줘":
- **KNOB / MASK 별**: ML_TABLE 의 KNOB_*/MASK_* 값 기준 (예: "3.0 VTN knob 으로 컬러").
- **eqp 별**: 특정 function_step 의 설비(장비) 기준 — 그 step 을 어느 eqp 로 지났는지로 분리.
- **eqp_chamber 별**: eqp 아래 chamber 까지 세분 — 챔버 유의차 판단용. func_step 을 함께 지정하면 그 step 기준.
- lot / wafer / product 별도 가능.

**필터**:
- lot 제외: "○○ 랏 빼줘 / 제외" — 해당 lot 을 데이터에서 뺀다.
- 기간: "언제 이후만 / ○○ 이후" — tkout_time/FAB 시간 기준 그 시점 이후만 남긴다.

이런 분리·필터의 목적은 대개 commonality(무엇이 달라 유의차가 나는가) 판단이다 — commonality 카드 참조.
