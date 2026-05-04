# 사용자 매뉴얼 인덱스

> 문서 버전: 2026-05-03  
> 이 문서는 6개 기능 가이드의 진입점입니다.

## 지금 바로 보는 Quick Start 5분

1. 먼저 인폼/관리 권한을 기준으로 현재 계정이 접근 가능한 페이지를 확인한다.
2. [인폼 가이드](./user_guide_inform.md)에서 `인폼/매트릭스/로그` 탭 구조를 빠르게 훑는다.
3. [SplitTable 가이드](./user_guide_splittable.md)에서 작업할 LOT와 소스(ML_TABLE_*)를 선택한다.
4. [파일 탐색기 가이드](./user_guide_filebrowser.md)로 데이터 소스 경로를 확인한다.
5. [대시보드 가이드](./user_guide_dashboard.md)로 핵심 KPI를 시각화한다.
6. [에이전트 가이드](./user_guide_agent.md)로 반복 작업을 자동화한다.
7. [Admin 가이드](./user_guide_admin.md)로 권한/카탈로그 변경은 안전하게 반영한다.

## 가이드 목록 (1줄 요약 + 링크)

- [user_guide_inform.md](./user_guide_inform.md):  
  인폼 등록/조회/매트릭스/로그/메일 정책까지 포함한 3탭 운영 매뉴얼.
- [user_guide_splittable.md](./user_guide_splittable.md):  
  SplitTable의 제품/lot/컬럼/소스/노트/저장 흐름과 디버그 절차 설명.
- [user_guide_filebrowser.md](./user_guide_filebrowser.md):  
  파일 탐색, SQL preview, Hive/parquet 확인, 업로드/동기화 운영 가이드.
- [user_guide_dashboard.md](./user_guide_dashboard.md):  
  차트 생성(16종) 및 인폼 KPI 12종, 공개범위/멀티축 운영 매뉴얼.
- [user_guide_agent.md](./user_guide_agent.md):  
  자연어 에이전트 사용, 30+ 시나리오 카탈로그, 멀티턴/스레드 동작 가이드.
- [user_guide_admin.md](./user_guide_admin.md):  
  권한 매트릭스와 카탈로그, 모듈 순서, 로그·메일·백업 관리용 관리자 가이드.

## 상황별 추천 경로

- 신규 인폼 학습이 필요하다면 → 인폼 가이드 → 대시보드 연동 4장에서 상태 분포 KPI 점검
- 랏 기반 데이터 분석이 필요하다면 → SplitTable 가이드의 제품/lot/컬럼 단계 → 매트릭스 탭 비교
- 파일 원본 이슈가 의심된다면 → 파일 탐색기 가이드의 SQL 미리보기/파케이 검증 → 파일 동기화 확인
- 반복 반복질의를 줄이고 싶다면 → 에이전트 가이드의 follow_up_inputs/시나리오 사용
- 권한 관련 이슈가 있다면 → admin 가이드의 권한 매트릭스와 감사로그 확인

## 권장 학습 순서

1. 인폼
2. SplitTable
3. 파일 탐색기
4. 대시보드
5. 에이전트
6. Admin

## 검색어 추천

- 인폼 상태별 미해결 건: `인폼`, `상태`, `모듈`
- SplitTable 오버라이드: `split`, `ML_TABLE`, `override`
- SQL 미리보기 오류: `filebrowser`, `apply_sql_like`
- 대시보드 성능 이슈: `chart`, `multi y`, `heatmap`
- 에이전트 clarifying: `agent`, `clarification`, `slot`
- 관리자 변경 추적: `audit`, `권한`, `카탈로그`

## 연계 체크리스트

- 권한 확인 후 각 가이드를 실습한다.
- 변경 직후 로그에서 반영 여부를 확인한다.
- 중요한 작업은 팀 스레드로 공유한다.
- 인폼/대시보드/에이전트 흐름에서 동일 lot 기준으로 결과를 맞춘다.
- 월말에는 admin 백업을 점검한다.

## 문서 유지 정책

각 가이드는 task #15~18 완료 후 실제 동작과 동일성 재검증이 필요합니다.
운영 정책 변경이 있으면 변경 로그를 남겨 각 문서의 해당 섹션을 동기화하세요.

최종 검증: code reference 와 현재 동작 일치 여부 확인 필요

