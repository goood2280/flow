# ASSIGNMENT — 담당자 현황 + 대화 로그

> 작업을 잡을 때: 아래 표에서 `todo` 항목 찾기 → `owner` 열에 본인 기입 (claude/codex) → 항목 파일에서 상태 변경 → [`CHECKLIST.md`](./CHECKLIST.md) 업데이트
> 완료했을 때: 이 파일 하단 "완료 로그" 섹션에 한 줄 기입

---

## 현재 담당 매트릭스

### v9.0.3 Hotfix (H1~H6)

| ID | 제목 | 상태 | 담당 후보 | 현재 owner | 시작일 | 메모 |
|---|---|---|---|---|---|---|
| H1 | ML 탭 PLANNED → BETA | todo | either | — | — | FE 위주, 반일 |
| H2 | Dashboard 팔레트 통일 | done | either | codex | 2026-04-24 | chartPalette 공통화 완료 |
| H3 | SplitTable 내부용어 은닉 | done | either | codex | 2026-04-24 | 기본/고급 분리 + 용어 정리 완료 |
| H4 | 사이드바 애매 탭 4개 정리 | todo | either | — | — | FE + BE admin |
| H5 | PRODA 중복 근본 차단 | todo | either | — | — | BE 위주 + cron |
| H6 | Home "3가지 질문" 섹션 | todo | either | — | — | FE 위주 + 신규 엔드포인트 |

### v9.1 Feature (F1~F3)

| ID | 제목 | 상태 | 담당 후보 | 현재 owner | 시작일 | 메모 |
|---|---|---|---|---|---|---|
| F1 | UXKit 4페이지 실투입 | in_progress (codex) | claude | codex | 2026-04-24 | Dashboard H2 완료, Inform/Admin 1차 이관 진행 |
| F2 | SplitTable 3,480줄 → 4파일 분할 | todo | claude | — | — | dev-splittable 단독 · F3 선행 필수 |
| F3 | pytest 100 케이스 도입 | todo | claude / codex | — | — | eval-lead + dev-lead 공동 |

**v9.1 상속 항목 (본 표 외 · 기존 로드맵)**:

| 원 ID | 제목 | 상태 | 담당 후보 | 현재 owner | 메모 |
|---|---|---|---|---|---|
| 1.10 | Meeting 이슈 가져오기 확장 | todo | either | — | 1주 · docs/v9_roadmap.md 원전 |
| 1.11 | Tracker 카테고리 Monitor/Analysis | todo | either | — | 2주 · docs/v9_roadmap.md 원전 |
| 1.12 | 3분 온보딩 투어 | todo | either | — | 1주 · H6 선행 |

### v9.2 Platform (P1~P6)

| ID | 제목 | 상태 | 담당 후보 | 현재 owner | 시작일 | 메모 |
|---|---|---|---|---|---|---|
| P1 | GitHub Actions CI | todo | either | — | — | GitHub 권한 시 human-required |
| P2 | 구조화 로깅 + request_id | todo | either | — | — | BE middleware |
| P3 | SQLite 세션 저장소 | todo | either | — | — | 배포 시 human-required |
| P4 | Prometheus/Grafana PoC | todo | either | — | — | BE + Docker |
| P5 | RBAC row-level (제품 ACL) | todo | claude | — | — | dev-lead + eval-lead 공동 · 보안 재감사 게이트 |
| P6 | Secret 암호화 + dep 감사 | todo | either | — | — | BE + CI |

### v9.3+ Long-term (L1~L9)

| ID | 제목 | 상태 | 담당 후보 | 현재 owner | 시작일 | 메모 |
|---|---|---|---|---|---|---|
| L1 | SPC 페이지 | todo | claude | — | — | 도메인 UAT human-required |
| L2 | DVC 방향성 뱃지 | todo | claude | — | — | dvc-curator 자문 |
| L3 | 인과 매트릭스 | todo | claude | — | — | causal-analyst 자문 · 도메인 |
| L4 | ET Time 분석 heatmap | todo | claude | — | — | dev-ettime |
| L5 | 모바일 뷰 PWA | todo | claude | — | — | 배포 human-required |
| L6 | SSO (SAML + OIDC) | todo | either | — | — | 사내 IdP 협의 human-required |
| L7 | i18n 한/영 인프라 | todo | either | — | — | F1 후 진행 권장 |
| L8 | 유저 가이드 10편 + 비디오 10편 | todo | either | — | — | 비디오 human-required |
| L9 | 멀티테넌시 (SaaS) | todo | human-required / claude | — | — | 설계 사람 결정 필수 |

---

## 상태 값 정의

| 값 | 의미 |
|---|---|
| `todo` | 미착수 |
| `in_progress (claude)` | claude 작업 중 |
| `in_progress (codex)` | codex 작업 중 |
| `in_progress (claude+codex)` | 공동 작업 (드물게, 명시 필요) |
| `blocked` | 차단 (이유는 owner 메모에 기입) |
| `done` | 완료 · DoD 충족 · eval-lead 검증 |

---

## 대화 섹션 (claude ↔ codex)

> 상의 · 질문 · 충돌 해결 · 설계 결정 등을 이 섹션에 남깁니다.
> 형식: `[YYYY-MM-DD HH:mm owner] 메시지`
> 오래된 스레드는 주기적으로 아래 "아카이브" 로 이동.

### 현재 스레드

```
[2026-04-24 오후 claude]
[Initial] collab 폴더 구조 생성 완료.
README.md 부터 읽어주세요. 작업 잡으실 때 이 표에 owner 표시 부탁드립니다.

H1~H6 은 모두 반일~1일짜리 독립 항목이라 각자 집어도 무방합니다.
F1~F3 은 F3 (pytest) 가 다른 F/P 의 선행 재료이니 F3 우선 합의 필요.
P 는 v9.1 완료 후 진입이므로 당장 급한 것 없음.
L 은 분기별 1~2건 선택 방식 — 지금 일단 메뉴만 정리.

codex 측 자유롭게 의견 부탁합니다.
```

### 아카이브 (완결된 스레드)

_현재 비어 있음_

---

## 결정 로그

> 설계 · 우선순위 등 중요 결정을 여기에 한 줄로 요약 (날짜 · 결정 · 근거).

| 날짜 | 결정 | 근거 | 결정자 |
|---|---|---|---|
| 2026-04-24 | H1~H6 순서를 Quick Win 매트릭스 기준 (ML→팔레트→용어→애매탭→PRODA→Home) 으로 배치 | `_archive/v9_improvement_plan.md` §2 우선순위 매트릭스 | orchestrator |
| 2026-04-24 | F1~F3 를 v9.1 대형 3건으로 확정, 상속 3건 (1.10/1.11/1.12) 은 별도 sprint | 스펙 요구사항 (H6+F3+P6+L9=24) | orchestrator |
| 2026-04-24 | P5 RBAC 를 P 최종 단계로 배치 (P6 후가 아닌 P6 전) | 보안 재감사 게이트가 배포 직전 필요 | orchestrator |
| 2026-04-24 | L1~L9 분기별 선택 방식 채택 (일괄 스케줄 X) | 도메인 UAT 복수 필요 · 리소스 분산 | orchestrator |

---

## 완료 로그

> 항목 완료 시 한 줄 추가: `[YYYY-MM-DD ID owner] 커밋 해시 / PR#nnn / 메모`

_현재 비어 있음. 첫 완료 항목부터 여기에 append._

- [2026-04-24 H2 codex] dashboard chartPalette 공통화 완료 / `npm run build` pass
- [2026-04-24 H3 codex] SplitTable 내부용어 은닉 완료 / `npm run build` pass

---

## 차단 (Blocked) 사유

> 항목이 blocked 상태일 때 이유와 해결 필요 조건을 명시.

_현재 차단 항목 없음._

---

## 스펙 변경 / 추가 요청

> 계획표 자체에 대한 수정 요청은 여기에 기록 → orchestrator 가 반영.

_현재 요청 없음._

---

*문서 위치*: `D:\TEST_Making_Video\semi_all\flow\docs\collab\ASSIGNMENT.md`
*최종 수정*: 2026-04-24 (collab 구조 생성 시점)
