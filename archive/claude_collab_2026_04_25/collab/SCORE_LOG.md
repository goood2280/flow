# flow v9 종합 점수 로그

> **목표**: 종합 ≥ 9.0 달성까지 Claude ↔ Codex handoff 루프 반복
> **기준일**: 2026-04-24
> **현재 버전**: v9.0.2

---

## 점수 축 정의 (9축 + 종합)

| 축 | 설명 |
|---|---|
| 기능 | 엔지니어가 매일 쓸 값이 실제로 들어있는가 |
| 도메인 | 반도체 fab/ET/INLINE/KNOB 개념과 정합하는가 |
| UX | 일관성·직관성·정보위계·첫인상 |
| 성능 | 대용량(30~60GB parquet) 처리 시간/메모리 |
| 안정성 | 회귀 방어선(smoke·pytest·CI) |
| 관측 | 로깅·메트릭·알림 허브 성숙도 |
| 보안 | 인증·세션·권한·비밀 관리 |
| 확장 | multi-worker / SSO / 멀티테넌시 |
| 문서 | 개발자·사용자 가이드·아키 문서 |

종합 = 9축 단순 평균.

---

## 점수 추적표

| 시점 | 버전 | 기능 | 도메인 | UX | 성능 | 안정성 | 관측 | 보안 | 확장 | 문서 | **종합** | 완료 handoff |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-04-23 | v8.8.31 | 8.0 | 7.0 | 7.0 | 6.0 | 4.0 | 5.0 | 6.0 | 3.0 | 5.0 | **6.5** | — |
| 2026-04-24 | v9.0.2 | 8.5 | 8.5 | 6.5 | 6.5 | 5.0 | 6.0 | 7.0 | 3.0 | 6.0 | **7.00** | 0 |
| 2026-04-24 15:17 | v9.0.2+H2 | 8.5 | 8.5 | 6.55 | 6.5 | 5.0 | 6.0 | 7.0 | 3.0 | 6.0 | **7.01** | 1 |
| 2026-04-24 15:20 | v9.0.2+H2+H3 | 8.5 | 8.5 | 6.65 | 6.5 | 5.0 | 6.0 | 7.0 | 3.0 | 6.0 | **7.02** | 2 |

---

## 루프 이력

### 2026-04-24T18:59:03+09:00 — handoff_v9_F4 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 5/8 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T18:56:03+09:00 — handoff_v9_H16 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 10/11 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T18:54:33+09:00 — handoff_v9_H13 auto-verified
- status: archived
- smoke: 0 PASS / 0 FAIL
- acceptance: 16/16 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T17:39:03+09:00 — handoff_v9_H15 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 8/8 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T17:35:33+09:00 — handoff_v9_H12 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 12/13 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24 17:35 — 사용자 요구 (QA 자동 루프) → H16
- **H16**: user/admin 페르소나 E2E + 차트 스펙 검증 + edge case + ux-reviewer 주기 호출
- daemon 에 QA scheduler 연동 (archive N=3 마다 e2e_qa.py, N=5 마다 ux-reviewer)
- 이상 발견 시 'qa_issue' kind handoff 자동 생성 + Admin 에 'QA 리포트' 서브탭
- inbox open: ≈35

### 2026-04-24T17:29:03+09:00 — handoff_v9_H14 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 10/10 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24 17:26 — H14 tracker LOT_WF schema 확장
- `tracker/issues.json` LOT_WF row 스키마에 `current_step`, `current_step_seq`, `et_measured`, `et_last_seq`, `et_last_time`, `last_checked_at` nullable 필드 추가
- 신규 공통 helper: `backend/core/tracker_schema.py`
- 자동/수동 마이그레이션 경로 추가:
  - router import 시 기존 `tracker/issues.json` 자동 보정
  - `scripts/migrate_tracker_schema.py` idempotent 실행 + 백업/로그 생성
  - Admin `트래커 스키마 재마이그레이션` 버튼 추가
- 검증:
  - `python3 flow/scripts/migrate_tracker_schema.py` → changed=true, backup 생성
  - function smoke: `lot_check_all` 응답 row 에 `et_measured` 포함 확인
  - `cd flow/frontend && npm run build` 성공

### 2026-04-24T17:23:04+09:00 — handoff_v9_H10 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 6/6 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T17:04:33+09:00 — handoff_v9_H11 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 7/7 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T17:00:33+09:00 — handoff_v9_H9 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 5/5 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:57:33+09:00 — handoff_v9_H8 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 3/4 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:56:03+09:00 — handoff_v9_H7 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 3/6 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:51:36+09:00 — handoff_v9_F1 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 3/5 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:51:03+09:00 — handoff_v9_F1 auto-verified
- status: archived
- smoke: 30 PASS / 0 FAIL
- acceptance: 3/5 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:47:33+09:00 — handoff_v9_F1 auto-REOPEN
- reason: missing 5 file(s)
- spawned: handoff_v9_F1_reopen_20260424_164733.json

### 2026-04-24 16:50 — 사용자 추가 요구 (WF Layout 파라미터 시스템) → H13 (+ 16:55 보강)
- **H13**: chip_radius · shot_pitch · shot/teg/chip 크기 · offset · scribe lane 패턴
- 핵심: 파라미터만 주면 wafer/shot/die/TEG/scribe 자동 렌더. scribe lane full=teg_h, half=teg_h/2
- **보강**: 한 scribe lane 에 TEG **여러 개 수평 병렬 배치 가능** (같은 dy, 서로 다른 dx)
- 의존: H10 (shot 번호), H12 (TEG 편집)
- inbox open: 31 → 32

### 2026-04-24 16:45 — 사용자 추가 요구 (WF Layout TEG + Edge Shot) → H12
- **H12**: WF Layout 제품별 TEG 편집 UI + Edge Shot 후보 표시 (L10 실용 phase 1)
- 핵심: 완전 shot(wafer 전체 포함) 제외하고 선택 TEG 기반 edge 후보 하이라이트
- 의존: H10 (shot 번호 1,1 체계)
- inbox open: 30 → 31

### 2026-04-24 16:38 — 사용자 요구 8개 handoff 편입 (H7~H11, F4, F5)
- 사용자 원문 요구 6건(파일탐색기 신호등·대시보드 차트/Fab Progress·SplitTable Lot History·ET Report 확장·WF Map chip edge·Tracker LOT_WF monitor_prod) → handoff 8개로 분해
- **H7**: FileBrowser 신호등 아이콘 재설계 (원형/↓/↑ 단순화)
- **H8**: Dashboard 내부 용어(chart=) 숨김
- **H9**: SplitTable Final Only 에 'This Lot All History' 추가
- **H10**: WF Map chip_view edge 잘린 chip 빨강 + shot 번호 (1,1) 체계
- **H11**: Tracker LOT_WF 에 monitor_prod 컬럼 + 전체 일괄 조회 버튼
- **F4**: Dashboard 실차트 + Fab Progress 속도·정체 (spec: F4_dashboard_full.md)
- **F5**: ET Report 완전 구현 (Lot Scoreboard + Gantt + 다운로드 이력, spec: F5_et_report_full.md)
- inbox open: 22 → 30

### 2026-04-24T16:29:41+09:00 — handoff_v9_H5 auto-verified
- status: archived
- smoke: 29 PASS / 0 FAIL
- acceptance: 4/4 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:29:03+09:00 — handoff_v9_H5 auto-verified
- status: archived
- smoke: 29 PASS / 0 FAIL
- acceptance: 4/4 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:27:34+09:00 — handoff_v9_H6 auto-verified
- status: archived
- smoke: 29 PASS / 0 FAIL
- acceptance: 3/5 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:25:49+09:00 — handoff_v9_H5 auto-REOPEN
- reason: missing 1 file(s)
- spawned: handoff_v9_H5_reopen_20260424_162549.json

### 2026-04-24T16:24:49+09:00 — handoff_v9_H4 auto-verified
- status: archived
- smoke: 29 PASS / 0 FAIL
- acceptance: 5/5 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:23:49+09:00 — handoff_v9_H1 auto-verified
- status: archived
- smoke: 29 PASS / 0 FAIL
- acceptance: 3/4 OK (60% threshold)
- files: ok
- by: autonomy_daemon

### 2026-04-24T16:19:23+09:00 — handoff_v9_H4 auto-REOPEN
- reason: missing 1 file(s); smoke 1 fail
- spawned: handoff_v9_H4_reopen_20260424_161923.json

### 2026-04-24T16:18:23+09:00 — handoff_v9_H1 auto-REOPEN
- reason: smoke 1 fail
- spawned: handoff_v9_H1_reopen_20260424_161823.json

<!--
Claude 가 매 verify 사이클마다 아래에 append.
포맷:
### YYYY-MM-DD HH:MM — handoff_v9_<ID> verified
- status: archived / reopened
- smoke: N/N PASS
- acceptance: N/N OK
- score delta: (이전) → (이후)
- note: ...
-->

### 2026-04-24 15:17 — handoff_v9_H2 verified
- status: **archived**
- method: manual Claude review (FE-only, smoke irrelevant)
- acceptance: 3/4 OK (PDF 캡처 대조는 사용자 수동 몫)
- 확인 근거:
  - grep `const COLORS|PASTEL` in My_Dashboard.jsx → 0 matches
  - UXKit.jsx:26 `export const chartPalette` 존재
  - My_Dashboard.jsx:4 import + L18~19 `SERIES=chartPalette.series / PASTELS=chartPalette.pastel`
- 부산물: handoff 의 `linked_files[0]` 경로 틀림 (`frontend/src/UXKit.jsx` → 실제 `frontend/src/components/UXKit.jsx`). archive 버전에 정정 주석
- 부산물: `claude_verify.py` subprocess 가 Windows cp949 로 smoke 읽다 크래시 → `encoding="utf-8"` 추가로 패치 완료
- score delta: UX 6.5 → 6.55 (+0.05), 종합 7.00 → 7.01

### 2026-04-24 15:55 — L10/L11 신규 백로그 편입
- 사용자 도메인 요구로 신규 대형 handoff 2개 추가:
  - **L10**: WF Layout 고도화 (shot·TEG·radius·EDS/ET 매핑) — spec_doc: `docs/collab/L10_wflayout_advanced.md`
  - **L11**: 설명가능 ML (positional·step-aware·SHAP 공간/시간) — spec_doc: `docs/collab/L11_explainable_ml.md`
- 의존: L11 은 L10 선행 필수 (positional feature 가 L10 API 소비)
- 각각 4개 sub-handoff 로 분할 권고 (codex 가 구현 시 쪼개서 진행)
- inbox open: 25 → 27

### 2026-04-24 15:20 — handoff_v9_H3 verified
- status: **archived**
- method: manual Claude review (FE+docs, smoke irrelevant)
- acceptance: 4/4 OK
- 확인 근거:
  - grep `override-debug|long-items|fab-roots` in My_SplitTable.jsx → 0 matches
  - `docs/splittable_terms_ko.md` 존재 + 15 엔트리 (요구 10+)
  - codex verification 에 "기본/고급 탭 분리 + 라벨 한국어 치환 + npm run build 통과" 기록
- score delta: UX 6.55 → 6.65 (+0.1), 종합 7.01 → 7.02

### 2026-04-24 15:17 — inbox 정리
- archive 로 이동: `handoff_20260424_144500_001` (codex bootstrap 샘플), `loop_20260424_150441_001` (비표준 chip view 루프)
- 이유: 공식 프로토콜 시작 전 codex 자체 테스트 산출물. inbox 청결 유지

---

## Break 조건

1. 종합 점수 **≥ 9.0** 도달 → 루프 정상 종료
2. 완료 handoff **≥ 24** 인데도 종합 **< 9.0** → 추가 백로그 생성 필요 (`docs/collab/` 에 05_gap_analysis.md 신설)
3. 연속 **3 회** reopen → 해당 handoff 격리 후 사용자 에스컬레이션

---

## 예상 점수 상승 시나리오 (docs/collab/_archive 기반)

| 단계 | 완료 항목 | 예상 종합 |
|---|---|---|
| v9.0.2 현재 | — | 7.0 |
| v9.0.3 핫픽스 (H1~H6) | Quick Win 6 | 7.2 |
| v9.1 피처 (F1~F3) | UXKit 투입 + SplitTable 분할 + pytest | 7.5 |
| v9.2 플랫폼화 (P1~P6) | CI + 로깅 + SQLite + Prom + RBAC + 비밀암호화 | 8.0 |
| v9.3+ 장기 (L1~L3 선택) | SPC + DVC + 모바일 초벌 | 8.5+ |
| 미정 보강 | 온보딩 투어 + i18n + 인과 매트릭스 | **9.0** |

---

_마지막 업데이트: 2026-04-24 Claude_
