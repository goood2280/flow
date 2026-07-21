# 03. Platform v9.2 (P1~P6)

**시점**: +3개월 / **점수 목표**: 7.5 → 8.0 / **항목 수**: 6 (각 3일~2주)
**성격**: 인프라 · 관측성 · 멀티워커 · 보안. 사내 pilot 50명+ 대응.
**추천 진행 순서**: P1 (CI) → P6 (Secret) → P2 (로깅) → P3 (SQLite) → P4 (Prometheus) → P5 (RBAC)
**전제**: v9.1 완료 상태 (pytest 100 + UXKit 4페이지 + SplitTable 분할)

---

## P1. GitHub Actions CI 도입

- **상태**: todo
- **담당 후보**: either (GitHub 권한 필요 시 human-required)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\.github\workflows\ci.yml` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\.github\workflows\build.yml` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\scripts\ci_notify.py` (신규, 실패 알림)
- **변경 내용**:
  - `ci.yml`:
    - Trigger: `pull_request` + `push` to `main`/`release/*`
    - Jobs: `lint` (ruff), `test` (pytest -n 4), `build` (npm run build), `audit` (pip-audit + npm audit --audit-level=high)
    - Matrix: python 3.10 / 3.11 / 3.12
  - `build.yml`:
    - On release tag → setup.py 빌드 → GitHub Release 첨부 자동화
  - `scripts/ci_notify.py`:
    - CI red 시 webhook 호출 (mgmt-lead 관리 Slack/webhook URL)
- **완료 조건 (DoD)**:
  - [ ] 첫 PR → CI green (lint + test + build + audit 모두 pass)
  - [ ] 일부러 broken commit (pytest assertion 깨뜨리기) → CI red + 알림 도착
  - [ ] main branch protection 룰: CI 성공 필수
  - [ ] `docs/ci.md` 간단 가이드 (실행 / 디버깅)
- **의존성**:
  - F3 (pytest 100) 필수 선행 — CI 실행 대상
- **예상 공수**: 3일 (initial)
- **리스크**:
  - 안 하면: multi-worker 전환 시 회귀 폭주
  - 하다가: CI 과다 지연 (>10분) → pytest-xdist 4 worker + cache 필수

---

## P2. 구조화 로깅 + request_id

- **상태**: todo
- **담당 후보**: either (BE 위주)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\logging.py` (신규 또는 교체)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\middleware.py` (신규, RequestIDMiddleware)
  - `D:\TEST_Making_Video\semi_all\flow\backend\main.py` (middleware 등록)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\audit.py` (audit_log 테이블에 request_id 컬럼 추가)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\admin.py` (`/api/admin/logs?request_id=` 엔드포인트)
  - `D:\TEST_Making_Video\semi_all\flow\tests\infra\test_request_id.py` (신규)
- **변경 내용**:
  - `core/logging.py` — JSON line formatter:
    ```json
    {"ts": "2026-04-24T10:00:00Z", "level": "INFO", "request_id": "uuid", "user_id": "uid", "endpoint": "/api/...", "msg": "..."}
    ```
  - `core/middleware.py` `RequestIDMiddleware` — 모든 요청에 `X-Request-ID` uuid 부여 (클라이언트가 제공 시 그대로, 없으면 신규). context var 로 전파.
  - `audit_log` 테이블 migration: `ALTER TABLE audit_log ADD COLUMN request_id TEXT` (기존 row 는 null).
  - `/api/admin/logs?request_id=xxx` — 해당 request_id 로 audit_log + stdout 로그 파일 grep 결과 병합.
- **완료 조건 (DoD)**:
  - [ ] pytest: `test_request_id_propagates.py` — 1 요청 → audit_log + stdout 동일 uuid
  - [ ] 수동: 회의 save 실패 시 admin 로그 검색 → 단일 request_id 로 전체 chain 확인
  - [ ] `X-Request-ID` 응답 헤더에 포함
  - [ ] 기존 `logging.info(f"...")` 호출부 50% 이상 구조화 format 로 변환
- **의존성**: F3 (pytest) 선행
- **예상 공수**: 1주
- **리스크**:
  - 안 하면: 장애 대응 시간 30분+ 지속
  - 하다가: context var 전파 누락 시 log 에 request_id=null → middleware 단위 pytest 로 방어

---

## P3. SQLite 세션 저장소

- **상태**: todo
- **담당 후보**: either (BE 위주, 단 배포 시 human-required — 기존 세션 invalidate 가능)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\session.py` (교체)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\session_migrate.py` (신규, tokens.json import)
  - `D:\TEST_Making_Video\semi_all\flow\backend\admin_settings.py` (`session_backend` 추가)
  - `D:\TEST_Making_Video\semi_all\flow\tests\auth\test_session_multiworker.py` (신규)
- **변경 내용**:
  - `core/session.py` — SQLite 백엔드:
    ```sql
    CREATE TABLE sessions(
      token TEXT PRIMARY KEY,
      user_id TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      last_seen INTEGER NOT NULL,
      expires_at INTEGER NOT NULL,
      ua TEXT,
      ip TEXT
    );
    CREATE INDEX ix_sessions_user ON sessions(user_id);
    CREATE INDEX ix_sessions_expires ON sessions(expires_at);
    ```
    - `WAL` 모드 + `timeout=5` + prepared statement
  - `core/session_migrate.py` — 기존 `sessions/tokens.json` 자동 import + `.bak` 보관
  - `admin_settings.session_backend` ∈ {`file`, `sqlite`} (default `sqlite`). `file` 모드는 2주 유지 후 deprecate.
- **완료 조건 (DoD)**:
  - [ ] pytest: `test_session_multiworker.py` — 10 worker 동시 login 경합 없음
  - [ ] 수동: gunicorn `-w 4` 로 띄우고 5명 동시 로그인 OK
  - [ ] 수동: 기존 tokens.json 자동 import → 로그인 유지
  - [ ] Admin UI 에서 `session_backend` 토글 가능 (비상 시 file 로 복귀)
- **의존성**: F3 (pytest) 선행
- **예상 공수**: 1주
- **리스크**:
  - 안 하면: 동시사용자 50+ 에서 로그인 산발 실패
  - 하다가: 전환 시점 전체 세션 invalidate → 병렬 읽기 기간 2주 유지 필수 (R3 완화책)

---

## P4. Prometheus/Grafana PoC

- **상태**: todo
- **담당 후보**: either (BE + Docker)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\metrics.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\main.py` (instrumentator 통합)
  - `D:\TEST_Making_Video\semi_all\flow\requirements.txt` (prometheus-fastapi-instrumentator 추가)
  - `D:\TEST_Making_Video\semi_all\flow\docker\grafana\docker-compose.yml` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docker\grafana\dashboards\requests.json` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docker\grafana\dashboards\errors.json` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docker\grafana\dashboards\resources.json` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\docs\observability.md` (신규)
- **변경 내용**:
  - `core/metrics.py` 주요 counter:
    - `flow_request_total{method, endpoint, status}` (Counter)
    - `flow_request_latency_seconds{endpoint}` (Histogram)
    - `flow_error_total{type}` (Counter; type ∈ {override, ml_training, meeting_save, session, ...})
  - `/metrics` endpoint 노출 (Prometheus format)
  - `docker-compose.yml` — Prometheus + Grafana + node-exporter PoC
  - Grafana dashboards 3개 JSON import: requests / errors / resources
  - GlitchTip self-host 옵션 (v9.2 후반 이어감, 본 항목은 Prometheus만 완료 기준)
- **완료 조건 (DoD)**:
  - [ ] `curl /metrics` 200 + 유효 Prometheus format
  - [ ] Grafana 3개 대시보드 live 데이터 표시
  - [ ] 주요 20 endpoint 체크리스트 (R4 완화) — 모두 metric 계측
  - [ ] `docs/observability.md` 운영 가이드 (접속/디버깅/알림 룰)
  - [ ] 의도된 에러 발생 → `flow_error_total{type="override"}` 증가 확인
- **의존성**: P2 (로깅) 선행 권장 (request_id 로 metric ↔ log 상관)
- **예상 공수**: 2주 (instrumentation + Grafana PoC)
- **리스크**:
  - 안 하면: plateau 유지, 조직 의존 단계 진입 불가
  - 하다가: instrumentation 누락 → 부분 metric. **완화**: 체크리스트 20 endpoint 사전 정의 + pytest 1 endpoint 당 1 assertion

---

## P5. RBAC 정교화 (row-level, 제품별 ACL)

- **상태**: todo
- **담당 후보**: claude (dev-lead + eval-lead 공동, 보안 재감사 필수)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\acl.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\tracker.py` (acl 적용)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\inform.py` (acl 적용)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\splittable.py` (acl 적용)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\meeting.py` (acl 적용)
  - `D:\TEST_Making_Video\semi_all\flow\backend\routers\admin.py` (product_acl CRUD)
  - `D:\TEST_Making_Video\semi_all\flow\frontend\src\pages\My_Admin.jsx` (ACL 매트릭스 UI)
  - `D:\TEST_Making_Video\semi_all\flow\tests\admin\test_product_acl_filters.py` (신규)
- **변경 내용**:
  - `admin_settings.product_acl` — `{product_id: [user_ids / group_ids]}` 구조.
  - `core/acl.py` `_filter_by_product_acl(user, rows)` 헬퍼:
    - 제품 ACL 존재 & 유저 미포함 → 필터 아웃
    - 제품 ACL 미설정 → 통과 (soft-landing, 현재 동작 유지)
  - 4 라우터 모두 목록/단건/검색 엔드포인트에 적용
  - Admin UI — 제품 × 유저(그룹) 매트릭스 체크박스
- **완료 조건 (DoD)**:
  - [ ] pytest: `test_product_acl_filters.py` — A 유저가 B 제품 조회 시 403 또는 empty
  - [ ] 수동: Admin 이 제품 ACL 설정 → 일반 유저 사이드바 제품 목록에서 사라짐
  - [ ] 수동: ACL 미설정 제품은 기존처럼 공개 (soft-landing)
  - [ ] eval-lead 보안 재감사 pass (쿼리 누락 없음, R5 완화)
  - [ ] 4 라우터 모두 목록/단건/검색 엔드포인트에 acl 적용
- **의존성**:
  - F3 (pytest) + P2 (로깅 request_id) 선행
  - 보안 감사 (eval-lead) 게이트 통과 필수
- **예상 공수**: 2주
- **리스크**:
  - 안 하면: 보안 감사 finding + 조직 확장 어려움
  - 하다가: 쿼리 누락 → 권한 우회. **완화**: 각 라우터 pytest 1 endpoint 당 1 ACL assertion 필수

---

## P6. Secret 관리 + 의존성 감사

- **상태**: todo
- **담당 후보**: either (BE + CI)
- **변경 파일**:
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\secrets.py` (신규)
  - `D:\TEST_Making_Video\semi_all\flow\backend\core\config.py` (admin_settings.enc 로딩)
  - `D:\TEST_Making_Video\semi_all\flow\scripts\encrypt_admin_settings.py` (신규, 마이그레이션 tool)
  - `D:\TEST_Making_Video\semi_all\flow\.github\workflows\ci.yml` (P1 확장 — pip-audit + npm audit)
  - `D:\TEST_Making_Video\semi_all\flow\docs\security.md` (업데이트)
- **변경 내용**:
  - AES-256 암호화: `admin_settings.json` → `admin_settings.enc` + `.key` (chmod 400)
  - Key 출처: `FLOW_SECRET_KEY` env var > first-run prompt (fallback)
  - `scripts/encrypt_admin_settings.py` — 기존 평문 파일을 .enc 로 1회 변환 (백업은 `.json.bak`)
  - CI 에 `pip-audit` + `npm audit --audit-level=high` — high 이상 finding 시 CI red
  - `docs/security.md` — secret 운영 가이드 (key rotation, 복구, 긴급 대응)
- **완료 조건 (DoD)**:
  - [ ] `admin_settings.enc` 은 openssl 없이 읽을 수 없음 (hex dump 검증)
  - [ ] `pip-audit` 0 high finding
  - [ ] `npm audit --audit-level=high` 0 finding
  - [ ] Key rotation 시나리오 문서화 + 스크립트 `scripts/rotate_secret.py` 제공
  - [ ] 로그 파일에 평문 secret 유출 없음 (pytest assertion)
- **의존성**: P1 (CI) 선행 — audit 실행 환경
- **예상 공수**: 1주
- **리스크**:
  - 안 하면: 파일 leak 시 전체 유저 유출 + CVE 장기 미대응
  - 하다가: 암호화 key 분실 시 복구 불가. **완화**: `.key` 는 반드시 chmod 400 + 백업 3중화 (admin HW · LastPass · 사내 금고)

---

## v9.2 릴리즈 게이트 (eval-lead 검증)

P1~P6 완료 후:

- [ ] 6개 항목 DoD 충족
- [ ] CI 100% green (latest 10 PR)
- [ ] 동시사용자 100명 로드 테스트 pass (gunicorn -w 4)
- [ ] Prometheus/Grafana 3 대시보드 live
- [ ] 보안 재감사 (eval-lead) finding high 0건
- [ ] 의존성 감사 0 high
- [ ] CHANGELOG_v9.2.md 작성
- [ ] docs/observability.md + docs/security.md 업데이트

목표 점수: **7.5 → 8.0** (UX 0.3↑, 성능 0.7↑, 안정성 1.0↑, 관측성 1.5↑, 보안 1.2↑, 확장성 1.0↑)

---

## 참고

- 원본 스펙: [`_archive/v9_improvement_plan.md`](./_archive/v9_improvement_plan.md) §1.13~1.18
- 스프린트 상세: `_archive/v9_improvement_plan.md` §6.4 (Sprint 7-10)
- 리스크 원전: `_archive/v9_improvement_plan.md` §5.1 (R3/R4/R5)
- 다음 단계: [`04_longterm_v9_3plus.md`](./04_longterm_v9_3plus.md) (L1~L9 장기)
