---
term: 차트 raw data 공유
kind: concept
aliases: [raw data 공유, 원본 데이터, 데이터 내려받기, csv 다운로드, 이 데이터 줘]
trigger_terms: [raw data, 원본 데이터, 데이터 공유, 다운로드, 내려받, csv로]
answered_by: dashboard
related: [chart-playbook, chart-agg-defaults]
status: active
---
차트를 만든 **원본 데이터를 공유/다운로드**하는 요청 — 분석 흐름의 필수 후속.

- "이 차트 raw data 공유해줘" = 방금 그린 차트의 계산 전/후 데이터를 CSV 로 제공.
- 집계 전 raw(shot/wafer 단위)와 집계 후(차트 point) 를 구분해 줄 수 있다 — 어떤 집계(median/avg 등)를 거쳤는지 함께 명시.
- 어떤 쿼리/필터로 만든 데이터인지(provenance: 소스 파일, lot 필터, 기간, 집계)도 함께 답한다.
- 차트 세션 컨텍스트를 이어받아 "그거 데이터 줘" 같은 후속에도 대응한다.
