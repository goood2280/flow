# Flow App Assessment — 2026-04-24

## Summary Score

- UI/전문성: `7.2 / 10`
- 사용성: `7.4 / 10`
- 보안: `7.8 / 10`
- 운영성: `8.1 / 10`
- 확장성: `8.0 / 10`
- 테스트 자동화: `6.6 / 10`
- 데이터 현실성 반영: `8.4 / 10`

## Strengths

- 멀티팀 슈퍼앱 방향이 명확하다.
- 권한/그룹 가시성, 운영 데이터 분리 원칙이 이미 잡혀 있다.
- SplitTable, Tracker, Inform, Meeting, Dashboard, ML이 한 제품 안에서 연결될 기반이 있다.
- FAB/INLINE/ET/VM/ML_TABLE의 실제 현업형 특성이 점점 코드와 문서에 반영되고 있다.

## Gaps

1. UI/전문성
- Dashboard 카드 품질 편차가 크고 차트 레이아웃 일관성이 약했다.
- 운영 히스토리가 분석 화면까지 충분히 올라오지 않았다.

2. 사용성
- lot를 보고 있을 때 tracker/inform 이력을 한눈에 못 보던 구간이 있었다.
- ML은 기능은 많지만 `Inline_ET + KNOB`처럼 현업 질문형 분석이 바로 드러나지 않았다.

3. 보안
- tracker 상세 조회에 가시성 체크가 빠져 있었다.

4. 테스트
- 스모크 테스트는 있었지만 운영 연결과 ML 세부 흐름까지는 못 덮고 있었다.

## Applied Improvements

- SplitTable History에 tracker / inform 운영 기록 집계 추가
- tracker 상세 조회에 세션 + 그룹 가시성 적용
- ML에 `Inline_ET + KNOB` 요약 분석 탭 추가
- Dashboard 그리드 밀도, 카드 쉘, 크기 일관성 개선
- 스모크 테스트에 operational-history, inline_et_overview 추가

## Next Priority

1. ET Reporting system
- step_seq / request_id / 재의뢰 구분
- ET Time analysis
- wafer / shot pattern reporting

2. Grain bridge
- shot-chip layout registry
- EDS(chip) ↔ ET/INLINE(shot) ↔ wafer lineage

3. Optimization layer
- y에 대한 KNOB 조합 효과
- 상호작용, Pareto, 추천 split

4. Test hardening
- 권한 시나리오
- 실데이터 soft-landing profile 시나리오
- lineages across lot/wafer/shot/chip
