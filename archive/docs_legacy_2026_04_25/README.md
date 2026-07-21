# FabCanvas.ai — Docs Index

세 종의 요약 슬라이스 + 에이전트용 질문 규약. 긴 원본(`../domain_sources_2026_04_26/FabCanvas_domain.txt`, `../VERSION.json`)을 매번 다시 파싱하지 않고, 필요한 섹션만 spot-reference 하도록 관리한다.

## 문서 구성

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — 모듈 구조(FastAPI+Polars / Vite+React), DataLake→S3→웹서버 흐름, `core/roots.py` 우선순위 체인, 페이지↔dev-* 에이전트 매핑, OmniHarness 훅, 라우터 prefix 맵, 데이터 처리 규칙, Gotchas, 신규 기능 체크리스트.
- **[ARCHITECTURE_NEXT.md](ARCHITECTURE_NEXT.md)** — 현재 구조의 운영상 한계, 목표 아키텍처, `router -> service -> repository -> domain` 전환 전략, 파일 저장소 동시성/백그라운드 작업/운영 데이터 분리 방안.
- **[ARCHITECTURE_USER_GUIDE.md](ARCHITECTURE_USER_GUIDE.md)** — 비개발자/초급 개발자용 설명 문서. FastAPI, React, Polars, Postgres, 스캐폴딩, 레이어 분리 이유와 실무식 구조를 쉬운 언어로 설명.
- **[SOFT_LANDING_POLICY.md](SOFT_LANDING_POLICY.md)** — 테스트 데이터와 실데이터의 경로/파일명/컬럼명 차이를 앱에서 어떻게 흡수할지에 대한 기준. adapter/profile, alias, case-insensitive 매칭, 운영 UX 원칙 정리.
- **[REAL_DATA_SOURCE_MODEL.md](REAL_DATA_SOURCE_MODEL.md)** — 실제 fab/inline/ET 원천을 어떤 canonical schema 로 볼지 정리한 문서. `step_seq`, `subitem_id`, `shot_x/shot_y`, ET package key, INLINE↔ET matching table 기준 포함.
- **[FLOW_PROCESS_ANALYTICS_ROADMAP.md](FLOW_PROCESS_ANALYTICS_ROADMAP.md)** — lot/wafer/shot/chip grain 연결, ET reporting, Inline_ET + KNOB 분석, FAB/VM→INLINE/ET→EDS 통합 분석 시스템의 단계별 구축 로드맵.
- **[WAFER_GEOMETRY_MODEL.md](WAFER_GEOMETRY_MODEL.md)** — wafer center / ref shot center / shot size / chip layout / TEG representative point 를 어떻게 모델링할지 정리. shot agg에서 TEG/chip proximity 분석으로 올라가기 위한 기준 문서.
- **[WAFER_LAYOUT_INPUT_GUIDE.md](WAFER_LAYOUT_INPUT_GUIDE.md)** — wafer/shot/chip/TEG 좌표를 어떤 포맷으로 주면 가장 쉽게 그릴 수 있는지, TEG lower-left vs shot center fallback, ET radius/report 기본 구성까지 정리한 입력 가이드.
- **[AGENT_ORCHESTRATION.md](AGENT_ORCHESTRATION.md)** — 파일 기반 현재 운영에서 시작해, 나중에 사내 API + 약한 내부 모델 + JSON action payload로 확장하는 agentic orchestration 구조. 각 에이전트의 역할 문서 링크 포함.
- **[INTERNAL_API_INTEGRATION.md](INTERNAL_API_INTEGRATION.md)** — 사내 API가 들어왔을 때 source contract 검증, step 자동 분류, 사람 확인이 필요한 위험 조건을 어떻게 다룰지 정리한 문서.
- **[FLOW_VALVE_INTEGRATION.md](FLOW_VALVE_INTEGRATION.md)** — `flow`와 `valve`를 어떻게 분리하고 붙일지, ET reformatter/product process setting을 어느 쪽이 소유하고 어떤 계약 파일로 넘겨야 하는지 정리한 문서.
- **[UX_MIGRATION_PLAN.md](UX_MIGRATION_PLAN.md)** — `ux_standard.md`와 `UXKit.jsx`를 실제 페이지에 어떻게 연결할지, Dashboard → Admin → Inform → SplitTable 순서의 UI 정리 계획과 완료 기준.
- **[CLAUDE_CODEX_COLLAB.md](CLAUDE_CODEX_COLLAB.md)** — Claude와 Codex가 같은 워크스페이스에서 handoff JSON으로 협업하는 규약. 리뷰→구현→검증 흐름과 메시지 포맷 정리.
- **[GUIDE.md](GUIDE.md)** — 페이지별 "언제 쓰는가" + 운영자 체크리스트(분기 시작 · ET 배치 · ML 리런 · 사고 조사) + 흔한 문제 해결 + 빠른 명령어/코드 규칙/버그 리포트 템플릿.
- **[DOMAIN.md](DOMAIN.md)** — 2nm GAA 공정 흐름/area 태그, DVC 방향성 테이블, 인과 매트릭스 핵심 규칙, SPC/측정 카테고리. 학계 공개 수준의 축약본.
- **[AGENT_QUESTIONS.md](AGENT_QUESTIONS.md)** — 에이전트가 사용자 결정을 기다릴 때 OmniHarness Questions 탭으로 라우팅하는 POST 규약(비동기 모델).

## Reference (과거 시점 상세)

v8.1.5 ("Options") 시점에 확정된 상세 문서 3종은 [`reference/`](reference/) 아래에 **원본 그대로** 보존 — HOL 브랜드명 포함 역사적 기록. 현재 docs 는 compact 유지 목적이므로 세부(파트 분할, 배포 스크립트, 당시 페이지 상세)가 필요하면 여기서 참조:

- [`reference/v8_1_5_ARCHITECTURE.md`](reference/v8_1_5_ARCHITECTURE.md) — 라우터 표 · 페이지 표 · setup_v8 11-part 구조 · Gotchas 전체.
- [`reference/v8_1_5_UPDATE_GUIDE.md`](reference/v8_1_5_UPDATE_GUIDE.md) — Claude 와 `update_vXXX.py` 배포 흐름 · 파트 교체 매핑 · 긴급 롤백.
- [`reference/v8_1_5_WEB_GUIDE.md`](reference/v8_1_5_WEB_GUIDE.md) — 기능 현황 · 변경 히스토리 · 코드 규칙 · 대화 운영 규칙.

v8.2+ 현행 배포 흐름(docker 멀티스테이지 · Github 기반)은 위 3종과 다르다 — 히스토리 참조용.

## 2nm GAA dummy data 위치

- `../data/DB/` — Hive-flat raw (`FAB / INLINE / ET / EDS / LOTS` 파티션 + `wafer_maps/*.json`). 스키마는 `../data/DB/README_GAA2N.md`.
- `../data/Base/` — 룰북(`dvc_rulebook.csv`), 매칭 테이블(`matching_step.csv`, `inline_*`), `_uniques.json` 카탈로그, wafer-level 피처 parquet 2종. 스키마는 `../data/Base/README_GAA2N.md`.
- 모두 합성 데이터(WM-811K 계열 shape 참조). 사내 실측치 아님.

## 에이전트 사용 가이드

dev-*/eval-*/mgmt-lead/reporter/domain-researcher 는 작업 전 자기 범위의 문서를 먼저 읽는다 — dev-* 는 주로 ARCHITECTURE 의 페이지 매핑 표와 GUIDE 의 흐름 섹션, causal-analyst/process-tagger/dvc-curator 는 DOMAIN 의 매트릭스/방향성 표, reporter 는 세 문서 전체를 "사용자 언어 번역"의 소스로 삼는다. 원본 `domain_sources_2026_04_26/FabCanvas_domain.txt` 와 `VERSION.json` 은 편집 대상이 아니며, 본 docs 만 변경 범위다.
