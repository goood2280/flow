# CHECKLIST — 원페이지 진행률

> 한 눈에 상태 확인용. 항목 완료 시 `[ ]` → `[x]` 로 변경.
> 작업 중은 `[~]` (tilde). 차단은 `[!]`.
> 상세 스펙은 각 항목 파일 (`01_hotfix_v9_0_3.md` 등) 참조.

---

## 전체 진행률: 2 / 24 (8%)

```
v9.0.3 Hotfix      [ ] [x] [x] [ ] [ ] [ ]      2/6  (33%)
v9.1 Feature       [~] [ ] [ ]                  0/3  (0%)
v9.2 Platform      [ ] [ ] [ ] [ ] [ ] [ ]      0/6  (0%)
v9.3+ Long-term    [ ] [ ] [ ] [ ] [ ] [ ] [ ] [ ] [ ]  0/9  (0%)
```

---

## v9.0.3 Hotfix (2/6) — 목표 점수 7.2

- [ ] **H1** ML 탭 PLANNED → BETA (반일)
- [x] **H2** Dashboard 팔레트 통일 (UXKit.chartPalette) (반일)
- [x] **H3** SplitTable 내부용어 은닉 (고급 탭) (1일)
- [ ] **H4** 사이드바 애매 탭 4개 정리 (WaferLayout/Messages/TableMap/DevGuide) (1일)
- [ ] **H5** PRODA 중복 근본 차단 (dedup + 새벽 cron) (1일)
- [ ] **H6** Home "3가지 질문" 섹션 (1일)

**릴리즈 게이트**:
- [ ] smoke 27 → 42 케이스 확장
- [ ] 사내 실DB 회귀 수동 검증 pass
- [ ] CHANGELOG_v9.0.3.md 작성
- [ ] eval-lead 게이트 통과

---

## v9.1 Feature (0/3) — 목표 점수 7.5

- [~] **F1** UXKit 4페이지 실투입 (377 hex → 40 이하) (3주)
  - [ ] F1-a My_Inform (117 hex, 1주)
  - [~] F1-b My_Dashboard (82 hex, 1주, a 병렬)
  - [~] F1-c My_Admin (89 hex, 1주, a 병렬)
  - [ ] F1-d My_SplitTable (89 hex, 3주, F2 와 통합)
- [ ] **F2** SplitTable 3,480줄 → 4파일 분할 (3주)
  - [ ] index.jsx shell (400줄)
  - [ ] LotTable.jsx (1,200줄)
  - [ ] PlanPanel.jsx (900줄)
  - [ ] NotesDrawer.jsx (600줄)
  - [ ] _helpers.js (380줄)
- [ ] **F3** pytest 100 케이스 도입 (2주)
  - [ ] auth/ (10 케이스)
  - [ ] tracker/ (15 케이스)
  - [ ] inform/ (20 케이스)
  - [ ] splittable/ (15 케이스)
  - [ ] meeting/ (10 케이스)
  - [ ] admin/ (15 케이스)
  - [ ] dashboard/ (15 케이스)

**v9.1 상속 (원 ID 유지)**:
- [ ] 1.10 Meeting 이슈 가져오기 확장 (1주)
- [ ] 1.11 Tracker 카테고리 Monitor/Analysis (2주)
- [ ] 1.12 3분 온보딩 투어 (1주)

**릴리즈 게이트**:
- [ ] pytest 100/100 + smoke 42/42 pass
- [ ] bundle size 증가 15% 이내
- [ ] ux-reviewer 통과
- [ ] SplitTable paste 세트 ping-pong 수동 검증
- [ ] CHANGELOG_v9.1.md 작성

---

## v9.2 Platform (0/6) — 목표 점수 8.0

- [ ] **P1** GitHub Actions CI (3일)
- [ ] **P2** 구조화 로깅 + request_id (1주)
- [ ] **P3** SQLite 세션 저장소 (1주)
- [ ] **P4** Prometheus/Grafana PoC (2주)
- [ ] **P5** RBAC row-level 제품 ACL (2주)
- [ ] **P6** Secret 암호화 + dep 감사 (1주)

**릴리즈 게이트**:
- [ ] CI 최근 10 PR green
- [ ] 동시사용자 100명 로드 테스트 pass
- [ ] Grafana 3 대시보드 live
- [ ] 보안 재감사 high 0건
- [ ] 의존성 감사 0 high
- [ ] CHANGELOG_v9.2.md 작성

---

## v9.3+ Long-term (0/9) — 목표 점수 8.5

**Q1 권장 (v9.3)**:
- [ ] **L1** SPC 페이지 5뷰 + WE Rule 1~4 (1개월)
- [ ] **L2** DVC 방향성 뱃지 (2주)

**Q2 권장 (v9.4)**:
- [ ] **L7** i18n 한/영 인프라 (2주)
- [ ] **L8** 유저 가이드 10편 + 비디오 10편 (2주)

**Q3 권장 (v9.5)**:
- [ ] **L5** 모바일 PWA (Tracker bell + Inform + Meeting) (1개월)
- [ ] **L6** SSO (SAML + OIDC) (3주)

**Q4 권장 (v9.6+)**:
- [ ] **L3** 인과 매트릭스 (공정 방향성 등급) (4주)
- [ ] **L4** ET Time 분석 heatmap (3주)

**2027~**:
- [ ] **L9** 멀티테넌시 (SaaS org 격리) (3개월)

---

## 진행률 대시보드

### 릴리즈별

| 릴리즈 | 완료 | 총 | % | 시점 |
|---|---|---|---|---|
| v9.0.3 Hotfix | 2 | 6 | 33% | +2주 |
| v9.1 Feature | 0 | 3 | 0% | +6주 |
| v9.1 상속 | 0 | 3 | 0% | +6주 |
| v9.2 Platform | 0 | 6 | 0% | +3개월 |
| v9.3+ Long-term | 0 | 9 | 0% | +6개월~ |
| **전체** | **2** | **24** | **8%** | — |

### 영역별

| 영역 | 해당 항목 | 완료 / 총 |
|---|---|---|
| FE UI/UX | H1, H2, H3, H4, H6, F1 | 2 / 6 |
| SplitTable | H3, F1-d, F2 | 0 / 3 |
| 테스트/CI | F3, P1 | 0 / 2 |
| BE 인프라 | H5, P2, P3, P4 | 0 / 4 |
| 보안 | P5, P6, L6 | 0 / 3 |
| 도메인 | L1, L2, L3, L4 | 0 / 4 |
| 모바일/SaaS | L5, L9 | 0 / 2 |
| 문서/i18n | L7, L8, 1.12 | 0 / 3 |

---

## 상태 범례

| 표기 | 의미 |
|---|---|
| `[ ]` | 미착수 (todo) |
| `[~]` | 작업 중 (in_progress) |
| `[x]` | 완료 (done, DoD 충족) |
| `[!]` | 차단 (blocked, ASSIGNMENT.md 사유 참조) |

---

## 업데이트 룰

1. 항목 시작 시 `[ ]` → `[~]`
2. 완료 시 `[~]` → `[x]` 및 `ASSIGNMENT.md` "완료 로그" 에 한 줄 append
3. 차단 시 `[~]` → `[!]` 및 `ASSIGNMENT.md` "차단 사유" 에 기입
4. 상단 진행률 숫자도 함께 업데이트 (수동, ASCII bar 포함)

---

*최종 수정*: 2026-04-24 (초기 생성)
*허브*: [`README.md`](./README.md)
*담당*: [`ASSIGNMENT.md`](./ASSIGNMENT.md)
