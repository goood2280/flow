# FabCanvas.ai — Docs Index

세 종의 요약 슬라이스 + 에이전트용 질문 규약. 긴 원본(`../FabCanvas_domain.txt`, `../VERSION.json`)을 매번 다시 파싱하지 않고, 필요한 섹션만 spot-reference 하도록 관리한다.

## 문서 구성

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — 모듈 구조(FastAPI+Polars / Vite+React), DataLake→S3→웹서버 흐름, `core/roots.py` 우선순위 체인, 페이지↔dev-* 에이전트 매핑, OmniHarness 훅, 라우터 prefix 맵, 데이터 처리 규칙, Gotchas, 신규 기능 체크리스트.
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

dev-*/eval-*/mgmt-lead/reporter/domain-researcher 는 작업 전 자기 범위의 문서를 먼저 읽는다 — dev-* 는 주로 ARCHITECTURE 의 페이지 매핑 표와 GUIDE 의 흐름 섹션, causal-analyst/process-tagger/dvc-curator 는 DOMAIN 의 매트릭스/방향성 표, reporter 는 세 문서 전체를 "사용자 언어 번역"의 소스로 삼는다. 원본 `FabCanvas_domain.txt` 와 `VERSION.json` 은 편집 대상이 아니며, 본 docs 만 변경 범위다.
