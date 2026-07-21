# QA User E2E Report — flow v9 handoffs H1~H15

**날짜:** 2026-04-24  
**테스터 페르소나:** 일반 유저 (qa_user, role=user, tabs=filebrowser,dashboard,splittable)  
**서버:** http://localhost:8080 (v9.0.2, 시작 시각 15:57)  
**세션:** qa_user 생성 → approve → login (admin API로 준비)

---

## 체크리스트 및 판정

| # | Handoff | 검증 항목 | 기대 | 실제 | pass/fail |
|---|---------|-----------|------|------|-----------|
| 1 | H1 | ML BETA — /api/ml/config 정상 응답 | 200 + available_models | 200, correlation/tabpfn/tabicl/random_forest | **pass** |
| 2 | H1 | ML BETA — config.js featureMap.ML.status=beta | FE 정의 존재 | frontend/src/config.js line 23: status:"beta", badge:"BETA" | **pass** (FE) |
| 3 | H1 | ML BETA — 서버측 /api/ml/status, /api/ml/jobs | 200 | 404 (엔드포인트 없음 — config.js 기반 FE-only 판정) | **N/A** |
| 4 | H2 | Dashboard palette — /api/dashboard/charts 팔레트 필드 | palette/color key 포함 | API에 palette 필드 없음, FE UXKit.chartPalette 로만 구현 | **fail** (BE 미저장) |
| 5 | H3 | SplitTable 용어 — splittable_terms_ko.md 존재 | docs/ 에 파일 | docs/splittable_terms_ko.md 존재 (1147 bytes) | **pass** |
| 6 | H3 | SplitTable 용어 — /api/splittable/terms 엔드포인트 | 200 | 404 | **fail** |
| 7 | H4 | 사이드바 4탭 — Messages /api/messages/unread | 200 | 200 (thread_unread, notice_unread 포함) | **pass** |
| 8 | H4 | 사이드바 4탭 — WaferLayout /api/waferlayout/* | 200 | **404 전부** (서버 미로드) | **fail** |
| 9 | H4 | 사이드바 4탭 — TableMap /api/dbmap/config | 200 | 200 (nodes, relations) | **pass** |
| 10 | H5 | PRODA dedup — /api/informs/products 중복 없음 | 중복 0 | PRODA/PRODB 중복 없음 | **pass** |
| 11 | H5 | PRODA dedup — 중복 추가 시 403 반환 | 409 또는 403 | 403 admin only (일반유저 write 차단 → 정책 변경) | **pass** (의도적 차단) |
| 12 | H6 | Home 3가지 질문 — /api/home/summary | 200 + suggested_actions 3개 | **404** (서버 미로드) | **fail** |
| 13 | H7 | FileBrowser 신호등 — /api/s3ingest/health | 200 + light/download_light/upload_light | 200, light=none (S3 미설정 상태 정상) | **pass** |
| 14 | H7 | FileBrowser 신호등 — roots에 signal 필드 | roots에 signal | 없음 (S3StatusLight는 /api/s3ingest/health 독립 호출) | **pass** (설계 확인) |
| 15 | H8 | Dashboard chart= 숨김 — 저장 chart에 chart_type 없음 | FE 숨김 | BE는 chart_type 저장, FE 표시 여부는 코드 레벨 검증 필요 | **partial** |
| 16 | H9 | SplitTable lot_all — history_mode=lot_all 인식 | 별도 row 세트 | 200, history_mode 응답 키 없음, final/lot_all 행수 동일(7) | **fail** |
| 17 | H10 | WF Map shot(1,1) — /api/waferlayout/* | 200 | **404** (서버 미로드) | **fail** |
| 18 | H11 | Tracker monitor_prod — lot 객체에 monitor_prod | 필드 존재 | **MISSING** (issues.json 데이터에도 없음) | **fail** |
| 19 | H12 | WF Layout TEG 편집 — /api/waferlayout/* | 200 | **404** (서버 미로드) | **fail** |
| 20 | H14 | Tracker et_measured — lot 객체에 et_measured | 필드 존재 | 존재함 (값 None이나 스키마 OK) | **pass** |
| 21 | H15 | Home release-notes — /api/home/release-notes | 200 + recent>=11 | **404** (서버 미로드) | **fail** |
| 22 | H15 | release_notes.json 파일 존재 | data_root에 파일 | 파일 없음 (/config/work/sharedworkspace/holweb-data/) | **fail** |

---

## 발견 이슈 — severity 별

### CRITICAL

**[BUG-01] home + waferlayout 라우터 서버 미로드**

- severity: critical
- 영향 handoff: H4(WaferLayout), H6(Home 3가지 질문), H10(WF Map), H12(TEG 편집), H15(release-notes)
- 원인: 서버 시작(15:57) 이후에 파일 생성됨
  - `home.py` mtime = 2026-04-24 17:35
  - `waferlayout.py` mtime = 2026-04-24 17:33
  - 서버 loaded list에 두 라우터 모두 없음
- 재현: `GET /api/home/summary` → 404, `GET /api/waferlayout/edge-shots` → 404
- 해결: uvicorn 서버 재시작 필요

**[BUG-02] ETTime /api/ettime/report 500 Internal Server Error**

- severity: critical (전 제품 발생)
- 영향: PRODA, PRODB 모두 500
- 원인: polars ColumnNotFoundError — `request_id` 컬럼 없음
  - 실제 컬럼: product, root_lot_id, lot_id, wafer_id, item_id, shot_x, shot_y, value
  - ET 데이터 스키마가 request_id를 기대하는 코드와 불일치
- 재현: `GET /api/ettime/report?product=PRODA` → 500

### HIGH

**[BUG-03] H9 SplitTable history_mode=lot_all — history_mode 미반영**

- severity: high
- 내용: `?history_mode=lot_all` 요청 시 응답에 `history_mode` 키 없음, final/all/lot_all 모두 동일한 7행 반환
- 코드: `backend/routers/splittable.py:2906` history_mode 인식하지만 응답에 포함 안 됨 (line 3139)
- 재현: `GET /api/splittable/view?product=ML_TABLE_PRODA&root_lot_id=A0001&history_mode=lot_all` → 200, rows=7, history_mode 없음

**[BUG-04] H11 Tracker monitor_prod 컬럼 미적용**

- severity: high
- 내용: issues.json의 lot 객체에 monitor_prod 필드 없음
- 영향: handoff H11 spec인 "LOT_WF 테이블 monitor_prod 컬럼 추가"가 데이터에 미반영
- 재현: `GET /api/tracker/issue?issue_id=ISS-260424-8E42` → lots[0] 키 목록에 monitor_prod 없음

**[BUG-05] H15 release_notes.json 파일 미생성**

- severity: high
- 내용: handoff archive → release_notes.json 자동 생성이 동작하지 않음
- 파일 경로: `/config/work/sharedworkspace/holweb-data/release_notes.json` 없음
- 영향: /api/home/release-notes 가 로드되어도 empty 반환

### MEDIUM

**[BUG-06] /api/admin/settings 일반 유저 접근 허용 (부분 노출)**

- severity: medium
- 내용: 일반 유저도 GET 200 반환 (`dashboard_refresh_minutes`, `dashboard_bg_refresh_minutes` 2개 필드)
- admin에서만 보이는 추가 필드: llm, backup, mail, data_roots
- 판단: 민감 정보 없는 UI 설정값이라 즉각 위험은 낮으나 admin 전용 path가 403 아닌 200을 반환하는 점은 설계 의도 재확인 필요
- security-auditor 교차 확인 권고

**[BUG-07] /api/admin/notify-rules 일반 유저 200 응답**

- severity: medium
- 내용: 일반 유저도 GET 200, rules/catalog 키 반환
- 재현: `GET /api/admin/notify-rules` with user token → 200

**[BUG-08] H2 Dashboard palette — BE 미저장**

- severity: medium
- 내용: chart save 시 `palette` 필드 저장 안 됨, 조회 시 palette 키 없음
- 영향: UXKit.chartPalette는 FE 상수로 존재하나 사용자 차트별 palette 선택 유지 불가
- 재현: `POST /api/dashboard/charts/save` with palette field → 저장 후 조회시 palette 키 없음

### LOW

**[INFO-01] H8 chart_type — API에는 노출, FE 숨김 여부 코드 레벨 확인 필요**

- FE-only 이슈 (dev-verifier 범위)
- BE: chart_type 저장/반환 정상
- H8 의도(title에 "chart=scatter" 텍스트 미표시)는 FE My_Dashboard.jsx 검증 필요

**[INFO-02] tabs 정책 — API 레벨 미적용 (FE 라우팅만)**

- 일반 유저(tabs=filebrowser,dashboard,splittable)라도 tracker/informs API에 직접 curl 호출 시 200
- tabs 제한은 FE 라우팅만 동작, BE는 모든 인증된 사용자에게 허용
- 보안 위험 낮음(특별 권한 없음), 설계 의도 확인 필요

---

## latency 측정

| endpoint | latency |
|---------|---------|
| POST /api/auth/login | ~2140ms |
| GET /api/ml/config | ~2040ms |
| GET /api/filebrowser/roots | ~2018ms |
| GET /api/splittable/products | ~2033ms |
| GET /api/splittable/view (with data) | ~2081ms |
| GET /api/tracker/issues | ~2060ms |
| GET /api/tracker/lot-step | ~2741ms |
| GET /api/s3ingest/health | ~2065ms |
| GET /api/dashboard/charts | ~2051ms |
| POST /api/dashboard/charts/save | ~2150ms |

> 모든 응답이 2~3초 수준 — 반도체 데이터 조회치고 느린 편. 특히 tracker/lot-step(2741ms), login(2140ms) 주의.

---

## 권한 경계 체크 요약

| 경로 | 일반 유저 결과 | 기대 | 판정 |
|-----|-------------|------|------|
| GET /api/admin/users | 403 | 403 | pass |
| GET /api/admin/settings | 200 (일부 필드) | 403 | fail (의도 불명) |
| GET /api/admin/activity/summary | 403 | 403 | pass |
| GET /api/admin/page-admins | 403 | 403 | pass |
| GET /api/admin/notify-rules | 200 | 403 | fail |
| GET /api/messages/admin/threads?admin=hol | 403 (not owner) | 403 | pass |
| GET /api/tracker/issues (tab 미허용) | 200 | FE 제한만 | info |
| No token → /api/* | 401 | 401 | pass |

---

## 총평

**이 사용자가 실제로 쓸 만한가?**

현재 상태로는 **제한적으로 사용 가능**. 핵심 이유:

1. **서버 재시작 필요**: home 탭(진입 3가지 질문, release-notes)과 WaferLayout 전체가 404. 서버를 재시작하면 즉시 해결.
2. **ETTime 500**: 공정 엔지니어의 핵심 분석 도구가 전 제품 500 오류. 즉각 수정 필요.
3. **SplitTable lot_all 미작동**: history_mode=lot_all 파라미터가 서버에서 수신되지만 실제 별도 이력 조회로 동작하지 않음.
4. 기본 기능(FileBrowser 조회, SplitTable 최종값 조회, Tracker 이슈 목록, Dashboard 차트 저장)은 정상.
5. S3 신호등(H7)은 /api/s3ingest/health 기반으로 정상 동작(미설정 = none 상태 표시).

**서버 재시작 + ETTime 버그 수정 후** 일반 사용자 기본 플로우(FileBrowser → SplitTable → Tracker 이슈) 는 사용 가능 수준.
