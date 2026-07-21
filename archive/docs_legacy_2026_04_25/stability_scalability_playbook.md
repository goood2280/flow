# Stability & Scalability Playbook — flow v8.8.33

**작성:** 2026-04-23 · dev-lead 주도 · eval-lead/security-auditor 참고 · mgmt-lead 감수 반영 여지

본 playbook 은 사내 pilot 에서 **30~60GB 데이터레이크 + 수십 명 동시 접속** 구간을 넘어 SaaS 검증 구간으로 가기 전에 필요한 건전성·확장성 작업을 한 문서에 모아두기 위한 것이다. 각 항목은 "현재 상태 → 다음 한 단계 → 롱텀" 으로 기술한다. 숫자/경로/엔드포인트는 v8.8.33 기준.

---

## 0. 원칙

1. **회귀 방지 > 기능 추가.** smoke → CI → sentry 축이 기본 골격. 신규 기능 이전에 이 축이 먼저 존재해야 한다.
2. **단일 프로세스 가정을 명시.** 지금은 SQLite + 파일 세션 + 단일 uvicorn worker. 확장은 이 가정을 바꾸는 구체 단계로 정의한다.
3. **사내망 원칙 유지.** 외부 SaaS(Sentry 유사) 는 self-host 변형만 허용. 로컬 미러/에어갭 대응 가능해야 릴리즈 게이트 통과.
4. **측정 없이 최적화 금지.** 모든 perf 작업은 "현재 측정 → 목표치 → 측정 재검" 3단을 따른다.

---

## 1. 안정성 (Reliability)

### 1.1 Smoke Test (현 기준선)

- **현재:** `scripts/smoke_test.py` · stdlib urllib · 27항목 · <5초 · 의존성 0.
- **커버리지:** 서버 헬스, 로그인, Admin CRUD, FileBrowser roots/products/view, SplitTable override-debug/long-items, 인폼 CRUD round-trip, 회의 minutes-append, 트래커 updated_at+summary, 달력/그룹/메시지, 대시보드/TableMap/ML, 인증 방어 401 3건.
- **dev-verifier 선실행 의무화 (v8.8.33):** 모든 기능 검증 이전에 smoke 통과 확인. 실패 시 즉시 fail 보고.

**다음 한 단계 (v8.9.0 후보):**
- `smoke_test_ext.py` — 회귀 의심 케이스 누적 (50~100 항목). 실데이터 없이 가능한 계약(schema/field) 검증까지.
- `smoke_test --quick|--full` 토글. quick 은 30초 cap, full 은 5분 cap.
- 실패 시 마지막 N lines of `server.log` 를 자동 첨부해 리턴.

**롱텀:**
- pytest 로 마이그레이션 (단, stdlib-only 원칙 유지 희망 시 pytest 대신 unittest).
- 시나리오 기반 (pageflow): 로그인→인폼 작성→메일 preview→삭제 순.

### 1.2 CI 게이트 (제안)

- **현재:** 없음. 개발자가 로컬에서 수동 smoke + setup.py rebuild.
- **다음 한 단계:** GitHub Actions `.github/workflows/ci.yml` (private repo 동작) —
  1. push / PR 마다 실행
  2. `pip install -r backend/requirements.txt`
  3. uvicorn 서버 백그라운드 기동 (임시 포트) — 더미 `admin_settings.json` 사용
  4. `python scripts/smoke_test.py`
  5. `cd frontend && npm ci && npm run build`
  6. python AST parse 전체 `.py` (syntax 사전 차단)
- **롱텀:** self-hosted runner 로 전환 (사내망 원칙). 가능하면 `preview` 단계 추가 — PR merge 전 이미지 URL 발급.

### 1.3 구조화 로깅

- **현재:** `server.log` · FastAPI/uvicorn 기본 포맷. request_id 없음.
- **다음 한 단계:** `backend/core/logging.py` 신설 —
  - JSON line 포맷: `{ts, level, request_id, user, method, path, status, latency_ms}`
  - middleware 로 request_id 주입 (uuid4, X-Request-Id 헤더 노출)
  - 기존 `logger.warning/info` 호출은 그대로 유지 (extra 로 request_id 병합만)
- **롱텀:** 로그 회전(logrotate or python built-in TimedRotatingFileHandler), 7일 보관.

### 1.4 에러 집약

- **현재:** Admin 활동 로그(JSONL) 에 일부 이벤트만 기록. 런타임 예외는 `server.log` 에만.
- **다음 한 단계:** self-host 가능한 aggregator 도입 —
  - **옵션 A:** Sentry OSS (docker-compose) — 사내망 OK, 학습곡선 있음.
  - **옵션 B:** GlitchTip (Sentry API 호환, lighter) — 권장.
  - 통합 지점: FastAPI exception handler + frontend global error boundary.
- **롱텀:** alerting 룰 (error rate spike) → messages 라우터로 admin 에게 자동 DM.

### 1.5 백업 검증

- **현재:** `admin_settings.backup` 주기/예약/즉시 백업 구현 (v8.8.14). zip 산출물 저장.
- **다음 한 단계:** 주기적 자동 검증 워커 —
  1. 최신 backup zip 열어 manifest(파일 목록+sha256) 비교
  2. 임시 디렉토리에 일부 핵심 파일만 해제 후 JSON/CSV 무결성 확인
  3. 결과를 `admin_settings.backup.last_verify` 에 기록 (PASS / FAIL + 사유)
- **롱텀:** 3-2-1 원칙 (3 copies, 2 media, 1 offsite). offsite 는 별도 S3 bucket.

### 1.6 세션·권한 회귀 가드

- **현재:** admin-role-tester / user-role-tester + security-auditor 에이전트 패턴 존재. 릴리즈 전 호출.
- **다음 한 단계:** smoke_test 에 "권한 경계 금지" 케이스 10건 상시화 — `/api/admin/*` 을 일반 유저 토큰으로 호출 → 401/403 기대. 누락 시 릴리즈 블록.

---

## 2. 확장성 (Scalability)

### 2.1 멀티 워커

- **현재:** uvicorn 단일 프로세스. 세션은 파일 기반 (`core/session.py`).
- **병목:** 동시 30~50 유저부터 파일 락 병목 / Python GIL 경쟁.
- **다음 한 단계:**
  1. 세션 저장소를 SQLite (WAL 모드) 로 전환 — 기존 파일 API 유지한 래퍼 추가.
  2. gunicorn + uvicorn worker N 개 (N=CPU-1). 공유 저장소 경합 테스트.
  3. `admin_settings.json` 같은 쓰기 경합 경로는 file lock (`portalocker`) 적용.
- **롱텀:** Redis 세션 + Redis pubsub 기반 SSE (회의록 동시편집, messages) — 단, 사내망 Redis 확보 필요.

### 2.2 대용량 parquet (30~60GB)

- **현재 (v8.8.33):** `core/parquet_perf.py` 3종 헬퍼 —
  - `collect_streaming(lf)` — polars streaming engine
  - `prune_recent_partitions(files, days=30)` — `date=YYYYMMDD` 파티션 필터
  - meta 사이드카 캐시 (`.meta.json`) · `get_or_compute_meta(fp)`
  - `scan_parquet_perf(files, hive=True, recent_days=30)` 합성 헬퍼
- **통합:** `core/utils.lazy_read_source(recent_days=30)` + `/api/filebrowser/view` (SQL 에 date/time 필터 있거나 `all_partitions=1` 전달 시 pruning 생략) + `/api/filebrowser/parquet-meta`.
- **다음 한 단계:**
  1. SplitTable main source 스캔도 pruning 호출부 업그레이드 (현재 `splittable.py` 의 `scan_parquet([...])` 직호출 몇 곳).
  2. Dashboard `/chart/.../data` 의 group_by 도 `collect_streaming`.
  3. UI 에 "파티션 범위 자동: 최근 30일 · [전체 보기]" 토글 노출.
- **롱텀:**
  1. Arrow Flight / DuckDB 비교 PoC — 읽기 전용 aggregation 만 DuckDB 로 라우팅.
  2. parquet 의 `column_statistics.min/max` 를 meta 에 추가 — dashboard 후보 컬럼 drop-down 에 활용.

### 2.3 S3 / 원격 스토리지

- **현재:** `backend/core/s3_sync.py` + `s3_ingest.py` 라우터 · endpoint_url override · 양방향 sync.
- **다음 한 단계:** read-through cache 패턴 —
  - S3 원격 parquet 을 로컬에 최초 접근 시 복제 (size + etag 검증) · 이후 로컬 hit.
  - 로컬 용량 cap (기본 20GB) 도달 시 LRU 제거.
  - `core/s3_cache.py` 별도 모듈. filebrowser 의 `_scan_product` 등에서 투명하게 호출.
- **롱텀:** 제품별/팀별 데이터 루트 격리 — `admin_settings.data_roots.<team>` 다중 root, 페이지·유저 별 권한 매트릭스.

### 2.4 long-format 기반 schema-free 대시보드

- **현재:** `core/long_pivot.py` + `_LONG` parquet primary (v8.8.31). `item_id` 레지스트리 진입 단계.
- **다음 한 단계:**
  1. `admin_settings.item_registry` 에 item_id ↔ (display_name, unit, scale, area) 매핑 저장.
  2. 대시보드 "컬럼 선택" UI 가 wide 컬럼명 대신 item_id 레지스트리를 참조.
  3. 새 item 이 로그에 나타나면 Admin 에게 "미등록 item_id: …" 알림.
- **롱텀:** SPC 트렌드 페이지도 long-format primary — 제품별 schema 이종 대응.

### 2.5 페이지별 데이터 루트 오버라이드

- **현재:** 전역 `db_root` 하나 · `admin_settings.data_roots` 로 선택적 오버라이드 가능.
- **다음 한 단계:** 페이지/팀별 루트 스위치 —
  - `admin_settings.page_roots.{informs, splittable, dashboard}` 각각 root 지정 가능.
  - FileBrowser 에서는 모든 루트 병합 뷰 유지.
- **롱텀:** multi-tenant — 유저가 속한 `team` 에 따라 데이터 경계 자동. PRODA 제품담당자는 PRODA 데이터만.

### 2.6 대량 다운로드 백그라운드화

- **현재:** `/download-csv`, `/download-xlsx` 동기 처리 — 수만 rows 요청 시 request timeout 위험.
- **다음 한 단계:**
  1. 10만 rows 이상 요청은 자동으로 job 큐 등록.
  2. job 상태 조회 (`/jobs/<id>`) 와 완료 시 `messages` 알림.
  3. 실제 파일은 `data/downloads/<user>/<jobid>.xlsx` 경로.
- **롱텀:** RQ / Celery 같은 external worker 도입 vs. 사내 제약상 file-polling worker 유지. 결정 대기.

---

## 3. 운영 관측성 (Operability)

### 3.1 Admin 활동 대시보드 (v8.8.14 기반)

- **현재:** `/api/admin/activity/summary` + `/features` · 1/7/30/90 일 탭 · top user/action/day.
- **다음 한 단계:** 이벤트 분류 카탈로그 (`core/activity_taxonomy.py`) — action prefix 를 고정 매핑 (`inform:*`, `meeting:*`, `tracker:*` 등) 해 Admin 이 보기 쉽게 묶기.

### 3.2 유저 워치 알림

- **현재:** messages 라우터 기본 CRUD 만. 이벤트 기반 알림 없음.
- **다음 한 단계 (v8.8.33 ~ v8.9.0 목표, 사용자 요청):**
  - `admin_settings.notify_rules` (유저별):
    - `my_plan_changed`: 내 plan 이 누군가에 의해 변경 → 메시지 + bell
    - `my_meeting_minutes_added`: 내가 만든 회의에 누가 minutes append
    - `my_tracker_comment`: 내 이슈에 댓글
    - `my_tracker_status_changed`: 내 이슈 상태 변경
    - `tracker_step_reached`: 특정 step_id 도달 (트래커 Lot 등록 시 설정)
  - 각 라우터의 변경점 hook 에 `core/notify.py` 의 `emit_event(event_type, actor, target_user, payload)` 호출 추가.
  - bell + home unread 팝업과 연동.
- **롱텀:** 메일 발송도 선택 가능 (`deliver: ["bell", "mail"]`).

### 3.3 시스템 모니터

- **현재:** `/api/admin/sysmon` · CPU/메모리/디스크 % · (FE 필드명 v8.8.28 정합 완료).
- **다음 한 단계:** parquet cache 적중률, smoke 최근 PASS 수, backup last_verify 결과까지 한 카드에.

---

## 4. 보안 (Security)

### 4.1 권한 누수 점검

- **현재:** security-auditor 에이전트 릴리즈 전 호출. admin 라우터 데코레이터 수기 점검.
- **다음 한 단계:** 라우터 스캐너 — `backend/routers/*.py` 의 `@router.*` 데코레이터를 AST 로 파싱, 권한 가드가 없는 `/admin/*` 경로를 감지해 리포트.

### 4.2 의존성 고정

- **현재:** `backend/requirements.txt` 버전 고정. `package-lock.json` 존재.
- **다음 한 단계:** `pip-audit` / `npm audit --production` 자동화 (CI 단계). 사내 미러 기반이라도 로컬 DB 참조 가능.

### 4.3 비밀 관리

- **현재:** `admin_settings.json` 평문 저장 (내부 자격증명 없음, 주로 UI 설정). S3 profile 은 `~/.aws/credentials` 참조.
- **다음 한 단계:** 민감 설정은 환경변수 or OS keyring 참조 필수화 (코드에서 하드코드 차단).

---

## 5. 릴리즈 게이트 (권장)

모든 릴리즈는 아래 체크리스트 통과가 조건:

- [ ] smoke_test.py PASS (27/27 이상)
- [ ] frontend build PASS (vite)
- [ ] security-auditor 릴리즈 감사 OK (critical=0, high≤1 + 추적 이슈 번호)
- [ ] backup 검증 워커 최근 7일 내 PASS
- [ ] VERSION.json / CHANGELOG 업데이트
- [ ] setup.py 재빌드 + 바이트 수 기록
- [ ] 구조 변경이 있으면 `docs/stability_scalability_playbook.md` 본 문서 해당 섹션 업데이트

---

## 6. 이월·공백 (2026-04-23 기준)

- [1.2 CI] GitHub Actions yml 초안 미작성.
- [1.3 logging] JSON line 포맷 middleware 미작성.
- [1.4 에러 집약] GlitchTip 선택 시 docker-compose 템플릿 필요.
- [2.1 멀티 워커] SQLite 세션 마이그레이션 plan 필요.
- [2.2 parquet] SplitTable / Dashboard 호출부의 streaming 업그레이드 2차 작업 남음.
- [2.3 S3 cache] `core/s3_cache.py` 미구현.
- [3.2 notify] `core/notify.py` 의 `emit_event` 훅 삽입이 각 라우터 별로 필요 — v8.8.33 후속 개발.

---

*담당: dev-lead (구현) · eval-lead (게이트 운영) · mgmt-lead (릴리즈 요약). 갱신 주기: 릴리즈 단위 + 이월·공백 섹션은 변경 즉시.*
