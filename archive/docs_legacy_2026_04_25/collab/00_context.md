# 00. 배경 — flow v9 현재 상태와 목표

**기준일**: 2026-04-24
**현재 버전**: v9.0.2
**참고 원전**: `docs/FLOW_APP_ASSESSMENT_2026_04_24.md`, `docs/flow_maturity_2026_04_23.md`, `docs/v9_roadmap.md`

---

## 한 줄 요약

flow 는 **기능은 포화** 수준이나, **UX 일관성 · 자동 테스트 · 내부 용어 노출** 3축에서 아쉽다.
이 셋을 먼저 고치면 종합 7.0 → 7.5 로 올라가고, 이후 **플랫폼화** (관측성 · 멀티 워커 · SSO) 로 8.0 까지 도달한다.

---

## 현재 점수 (v9.0.2)

| 측면 | 점수 | 진단 |
|---|---|---|
| 기능 커버리지 | 8.0 | Tracker/Inform/SplitTable/Meeting 등 주축 기능 모두 탑재 |
| 도메인 정합성 | 7.5 | KNOB/MASK/FAB/ET/INLINE/VM 소스 일체화, long-format adapter 완성 |
| **UX 일관성** | **6.5** | UXKit.jsx 존재하나 pages/*.jsx 에서 import 0건 · hex hardcoded 377건 |
| 성능 | 6.5 | 30GB 검증 완료, SplitTable parquet streaming OK |
| **안정성 (회귀)** | **4.0** | smoke 27 만 · pytest 없음 · 리팩터링 공포 |
| 운영 관측성 | 5.0 | psutil 15s 폴링만 · request_id 없음 · latency 지표 부재 |
| 보안 | 6.0 | 감사 17 finding 중 high 4 fix 완료. 평문 admin_settings.json 잔존 |
| 확장성 (SaaS) | 3.5 | single-tenant · 세션 파일 기반 (멀티워커 불가) |
| 문서화 | 5.0 | CHANGELOG/매뉴얼 PDF 상세, 엔지니어용 step-by-step 부재 |
| **종합** | **7.0** | 사내 pilot 적합, SaaS 미흡 |

---

## 직전 수준점검 주요 지적 (10대 이슈)

`docs/FLOW_APP_ASSESSMENT_2026_04_24.md` 에서 식별된 이슈:

1. **UXKit 미투입** — 표준 문서 있으나 페이지 0건에서 실제 import
2. **자동 테스트 9%** — tests/ 디렉토리 자체 없음
3. **ML 탭 모순** — config.js 에서 PLANNED 인데 /api/ml/* 8개 정상 동작
4. **Home 가치 제안 부재** — 기능 카드 나열형, "어디서 해결?" 판단 불가
5. **SplitTable 비대화** — 3,480줄, state 40+, useEffect 22 — 리팩터 1순위
6. **SplitTable 용어 노출** — override-debug, long-items, fab-roots 가 UI 라벨
7. **PRODA 중복 재발** — v9.0.0 one-shot dedup 후에도 신규 등록 시 발생
8. **애매 탭 4건** — WaferLayout, Messages, TableMap, DevGuide 포지션 불명
9. **팔레트 파편** — Dashboard COLORS(15) + PASTEL(15) 두 팔레트 혼재
10. **세션 파일 기반** — sessions/tokens.json 단일파일 · 멀티워커 경합

추가 지표:
- **화면별 직관성 평균**: 3.2/5 (목표 4.0+)
- **애매 기능**: 5건 (위 8번 + ML status 모순)

---

## 목표 로드맵

### v9.0.3 — 핫픽스 (+2주, 목표 7.2)

**성격**: 반일~1일짜리, 단일 PR 크기, 대형 리팩터 없음.
**범위**: 즉시 체감 개선 6건 (H1~H6).

### v9.1 — 메이저 (+6주, 목표 7.5)

**성격**: 1~2주 단위 feature branch.
**핵심 3**:
- F1 UXKit 4페이지 실투입 (377 hex → UXKit primitives)
- F2 SplitTable 3,480줄 → 4파일 분할
- F3 pytest 100 케이스 도입 (커버리지 9% → 55%)

**관련 v9.1 기존 로드맵 흡수** (v9.1 전체 스프린트 내):
- Meeting 이슈 가져오기 대확장
- Tracker 카테고리 대확장 (Monitor/Analysis)
- 3분 온보딩 투어

### v9.2 — 플랫폼화 (+3개월, 목표 8.0)

**성격**: 인프라·관측성·멀티워커·보안.
**핵심 6**:
- P1 GitHub Actions CI
- P2 구조화 로깅 + request_id
- P3 SQLite 세션 저장소
- P4 Prometheus/Grafana PoC
- P5 RBAC row-level (제품별 ACL)
- P6 Secret 암호화 + dep 감사

### v9.3+ — 장기 (+6개월~, 목표 8.5)

**성격**: 분기별 1~2건 선택.
**장기 9**:
- L1 SPC 페이지 · L2 DVC 방향성 · L3 인과 매트릭스 · L4 ET Time
- L5 모바일 · L6 SSO · L7 i18n · L8 가이드 · L9 멀티테넌시

---

## 핵심 숫자

| 지표 | 현재 | v9.1 목표 | v9.2 목표 |
|---|---|---|---|
| pages/*.jsx hex hardcoded | 377건 | 40 이하 | 20 이하 |
| 테스트 케이스 수 | smoke 27 | pytest 100 + smoke 42 | pytest 200 |
| 테스트 커버리지 | ~9% | 55% | 75% |
| SplitTable 파일 길이 | 3,480줄 (단일) | 4파일 평균 700줄 | 동일 |
| 동시사용자 검증 | 20명 | 30명 | 100명 |
| 사이드바 탭 수 (일반 유저) | 15개 | 11개 (4개 admin 이동) | 동일 |
| request 추적 | 없음 | request_id middleware | 분산 추적 |
| CI | 수동 smoke | — | GitHub Actions |

---

## 가정 / 제약

1. **사용자 기반**: v9.0.3~v9.2 동안 사내 pilot 10~30명 유지.
2. **워크플로**: feature branch + PR 표준 (main-guard v8.8.13+ 준수).
3. **smoke_test.py 유지**: pytest 도입 후에도 빠른 sanity 용도 보조 유지.
4. **릴리즈 게이트**: eval-lead 가 최종 검증. dev-verifier 직접 호출 금지.
5. **실데이터 접근**: 각 항목별 DoD 에 명시된 수동 검증은 사내 실DB (1.RAWDATA_DB_*) 상에서만 진행 가능한 경우 human-required 플래그.

---

## 리스크 5가지

| R# | 리스크 | 완화 |
|---|---|---|
| R1 | UXKit 4페이지 migration 중 regression → 실유저 피드백 폭주 | feature branch + pytest 필수 게이트 (F3 선행) |
| R2 | SplitTable 3,480줄 분할 시 paste 세트 시나리오 깨짐 | parity test 10 케이스 선행 (F2 DoD) |
| R3 | SQLite 세션 전환 시 기존 로그인 전부 invalidate → 재로그인 폭풍 | migration 시 tokens.json 병렬 읽기 기간 2주 (P3 DoD) |
| R4 | Prometheus 계측 누락 → 부분 metric | 주요 20 endpoint 체크리스트 (P4 DoD) |
| R5 | RBAC row-level 중 일부 쿼리 누락 → 권한 우회 | eval-lead 보안 재감사 게이트 (P5 DoD) |

---

## 소유권 (Ownership)

| 항목 군 | Primary | Secondary | Reviewer |
|---|---|---|---|
| UXKit (H2, H3, F1) | dev-lead (dev-uxkit) | ux-reviewer | eval-lead |
| 테스트 (F3, P1) | eval-lead (qa) | dev-lead | mgmt-lead |
| 페이지 분할 (F2) | dev-lead (dev-splittable) | eval-lead | orchestrator |
| 인프라 (P2, P3, P4) | dev-lead (dev-adapter/infra) | eval-lead | orchestrator |
| 보안 (P5, P6) | dev-lead + eval-lead (audit) | mgmt-lead | orchestrator |
| 도메인 (L1, L2, L3) | dev-lead (dev-dvc, dev-causal) | 도메인 엔지니어 UAT | eval-lead |
| 문서/온보딩 (L8) | mgmt-lead (reporter) | dev-lead | orchestrator |

---

## 다음 단계

1. `ASSIGNMENT.md` 에서 `todo` 상태 항목 확인
2. 담당자 표시 (claude / codex / either / human-required)
3. 항목 파일(`01~04_*.md`) 에서 상세 DoD 확인
4. 작업 시작 시 상태를 `in_progress (owner)` 로 변경
5. 완료 시 `CHECKLIST.md` 에 체크 + 항목 상태 `done`

---

*상세 스펙 원문은 [`_archive/v9_improvement_plan.md`](./_archive/v9_improvement_plan.md) 참조.*
