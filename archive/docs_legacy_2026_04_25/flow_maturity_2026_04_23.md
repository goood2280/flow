# flow 현재 수준 진단 (2026-04-23, v8.8.31 기준)

## TL;DR

**개발 pilot/POC 수준을 지나 사내 pilot 배포 가능 단계에 진입.** 핵심 페이지(파일탐색기·대시보드·SplitTable·이슈·인폼·회의·달력·TableMap·ML·Admin) 10개 모두 실제 사용 가능한 두께. 데이터 스키마가 사내 datalake 와 정합(long format primary). 인증·세션·감사·백업·메일 rate limit 등 운영 기본기는 들어가 있음. **단, 자동화된 테스트 커버리지 0, 대규모 동시사용자·권한 세분화·진정한 prod hardening 은 아직.** 짧게 말하면 **"사내 수십 명 pilot 용은 OK, SaaS 수준은 아님"**.

---

## 숫자로 본 현재

| 지표 | 값 |
|---|---|
| 버전 | v8.8.31 (v8.0.0 → v8.8.31 = 18 semver-bumped releases) |
| 최근 14일 커밋 | 40+ commits (v8.4~ 부터) |
| BE 라우터 모듈 | 22개 (`admin`, `auth`, `calendar`, `catalog`, `dashboard`, `dbmap`, `filebrowser`, `groups`, `informs`, `informs_extra`, `llm`, `mail_groups`, `meetings`, `messages`, `ml`, `monitor`, `reformatter`, `s3_ingest`, `session_api`, `splittable`, `tracker`) |
| BE core 모듈 | 18개 (`auth`/`session`/`paths`/`roots`/`audit`/`backup`/`mail`/`notify`/`sysmon`/`matching`/`product_config`/`s3_sync`/`domain`/`utils`/`reformatter`/`llm_adapter`/`long_pivot`) |
| BE 엔드포인트 | **약 298개** |
| BE 소스라인 | 20,632 lines |
| FE 페이지 | 15개 (`My_Admin`/`Calendar`/`Dashboard`/`DevGuide`/`FileBrowser`/`Home`/`Inform`/`Login`/`Meeting`/`Message`/`ML`/`Monitor`/`SplitTable`/`TableMap`/`Tracker`) |
| FE 컴포넌트 | 7개 (PageGear, Loading, …) |
| FE 소스라인 | 15,628 lines (pages + components + lib) |
| 자동 테스트 | **0 건** (pytest/vitest 파일 없음) |
| 감사 로그 호출 지점 | 82곳 |

---

## 레벨 1 — 제품 기능 (Feature fit)

### 핵심 10 페이지 (전부 실제 작동)
| 페이지 | 성숙도 | 비고 |
|---|---|---|
| 📂 파일탐색기 | ⭐⭐⭐⭐ | DB(hive)/Base 전환, SQL 필터, S3 양방향 sync 신호등, 컬럼 projection, head 200 첫 클릭. v8.8.31 부터 FAB/INLINE/ET 배지. |
| 📊 대시보드 | ⭐⭐⭐⭐ | scatter/line/bar/pie/hist/box/pareto/treemap/wafer_map/combo/heatmap/table/cross-table. LEFT JOIN, 자유 수식, fit deg 1-4, USL/LSL multi-spec. |
| 🗂 SplitTable | ⭐⭐⭐⭐ | LOT_WF 전치 뷰, plan vs actual, fab_lot_id 그룹 헤더, XLSX 컬러/병합/자연정렬/plan 테두리, KNOB/MASK/INLINE/VM/FAB/CUSTOM, hive override (fab_lot_id CI + long adapter). |
| 📋 트래커 | ⭐⭐⭐ | 이슈 CRUD + Gantt + 카테고리 + Lot/Wafer 태깅. updated_at stamp 추가. |
| 🔗 TableMap | ⭐⭐⭐ | 관계 그래프, 버전 30 + 롤백 + 감사, Relation 자동 매칭(CI), Base 파일 import. |
| 🧠 ML | ⭐⭐ | TabICL/XGBoost/LightGBM 트리거 + SHAP 뷰. 인과 매트릭스 필터는 초보 수준. |
| 📝 인폼 로그 | ⭐⭐⭐⭐⭐ | 최근 가장 많이 build-up. 스레드·모듈 권한·제품 담당자·SplitTable 임베드 스냅샷·메일 rate limit·Lot 타이핑 검색. |
| 🗓 회의관리 | ⭐⭐⭐⭐ | 차수·아젠다·회의록 공동작성(OT-lite)·결정/액션 선택적 달력 push·mail_group·고유 color auto. |
| 📅 변경점 달력 | ⭐⭐⭐⭐ | 회의 자동동기화, 카테고리→meeting color 연계, 공개 그룹. |
| ⚙️ Admin | ⭐⭐⭐⭐ | 14탭(사용자/권한/그룹/메일/백업/활동대시보드/모니터/데이터루트…). 페이지 admin 위임 매트릭스. |

### 부가 기능
- Contact 허브(유저↔admin 1:1 문의 + 공지 배너)
- 세션 4시간 idle auto-logout (서버측 enforcement)
- 자동 백업 (data_root zip, 주기+예약)
- psutil 기반 시스템 모니터 + 유휴부하 farming

**평가**: semi fab 공정 엔지니어가 일상업무에 **수십 가지 관점**(파일·분석·플랜·이슈·회의·달력·모듈통보) 을 하나의 로컬 웹앱으로 커버. 경쟁 SaaS(Spotfire, Tableau + Jira + Confluence + Slack 조합) 를 얇게 대체.

---

## 레벨 2 — 도메인 적합성 (Fab fit)

### 강점
- **장기·단기 lot 체인** (`root_lot_id` + `fab_lot_id` + `wafer_id`) 규약 올바름
- **ML_TABLE wide ↔ long adapter** — v8.8.31 부터 사내 datalake 와 스키마 정합, `/override-debug` 로 join 재현 검증
- **공정영역 태깅** (STI/Well/PC/Gate/Spacer/S-D Epi/MOL/BEOL) 사전 구비
- **KNOB/MASK/INLINE/VM/FAB prefix** 규약이 전 페이지 일관
- **plan vs actual diff** 가 SplitTable 의 1급 시민 (컬러·화살표·pin)
- **die-level wafer map** (ET shot_x/shot_y) 원천 지원

### 약점
- **DVC 방향성**(Rc/Rch/Ioff/Ion/Vth/lkg 좋아지는/나빠지는) UI 로 노출 아직 없음
- **공정 인과** ML 결과에 방향성 등급(앞→뒤 강함, 뒤→앞 거의없음) 미반영
- **SPC 전용 페이지**(Trend/historic/spec-out/box/EQP_CHAMBER 컬러링) 아직 없음 (백로그)
- **ET time 전용 분석**(ET/EDS 시간대별 heatmap) 아직 없음 (백로그)

---

## 레벨 3 — 코드 품질·운영 (Ops fit)

### 있는 것
- **라우터/페이지 분리** 깔끔. BE 22 라우터 × core 18 — 단일 책임 유지
- **auth middleware** (`/api/*` 전체 401 강제, 예외 경로 명시)
- **세션 토큰** (idle logout + `/?t=` fallback for images)
- **감사 로그** 82곳 (auth/admin/tracker/inform/meeting/splittable)
- **자동 백업** scheduler + 예약 1회 백업
- **데이터 보존 L0~L6** 가드 (`_PROTECTED_BASENAMES`, 공유경로 자동보호)
- **에러바운더리** (TabBoundary Admin)
- **HTTP 405 방어** (SPA catchall 진단) · **401 방어** (세션만료 글로벌 이벤트)
- **rate limit** (인폼 메일 3단 throttle)
- **CI schema mismatch** (ROOT_LOT_ID ↔ root_lot_id CI align) 전 라우터

### 없는 것 (gap)
| 항목 | 현재 | 이상 |
|---|---|---|
| 자동 테스트 | pytest/vitest 없음 | 핵심 경로 smoke + regression 최소 20개 |
| CI/CD | 없음 | GitHub Actions로 lint + build + test |
| 타입 검증 | Python은 Pydantic만, TS 없음 | mypy strict + TS 전환 고려 |
| 구조화 로깅 | `logging.info` 단일 | JSON 로깅 + request_id 상관관계 |
| Metrics 수집 | psutil 15s 폴링만 | Prom-style counter (endpoint latency, error rate) |
| Database | JSON/CSV/parquet 파일 기반 | SQLite 로 승격? 또는 그대로 유지 (single-user) |
| 권한 세분화 | 페이지별 admin 위임까지만 | row-level (특정 제품/lot 만 보기) |
| Secret 관리 | `admin_settings.json` 평문 | 최소한 파일 mode 400 + 의도적 encrypt |
| Dep vulnerability | 수동 | `pip-audit` + `npm audit` 자동 |
| 동시 사용자 | 단일 uvicorn worker | gunicorn multi-worker + 세션 공유 |

---

## 레벨 4 — 경쟁력 (누가 쓸까)

### 사내 pilot (지금 실제 사용자가 있다면)
- **30~100명 공정 엔지니어** 가 하나의 localhost:8080 으로 수렴하기 좋음
- 각자 본인 업무 영역(module) 만 인폼 필터링, 회의록은 공동작성, SplitTable 로 plan 공유 — 이미 실 업무 대체 가능 수준
- **이미 검증**: v8.8.x 대부분이 실사용 피드백에서 나온 fix/feat

### SaaS 로 못 가는 이유
1. **multi-tenant 구조 없음** — 한 사내에만 맞춰짐 (data_root 단일)
2. **테스트 0** — 리팩터링 시 회귀 위험 매우 큼
3. **관측/알람** 부족 — 장애 났는지 admin 이 모름 (활동 로그는 있지만 경보 없음)
4. **도메인 락인** — 반도체 fab 용어가 deep (KNOB/MASK/INLINE 등) → 재사용성 낮음

### 상용 flow 대비
| 항목 | flow (v8.8.31) | Spotfire | Jira+Confluence |
|---|---|---|---|
| 반도체 lot/wafer 1급 지원 | ✅ | ⚠ 커스텀 필요 | ❌ |
| plan vs actual diff | ✅ | ❌ | ❌ |
| 공정 인과/방향성 | ⚠ 부분 | ❌ | ❌ |
| 동적 차트 | ✅ (외부 lib 제로) | ✅✅ | ❌ |
| 이슈 + 회의 + 달력 통합 | ✅ | ❌ | ✅ |
| SSO | ❌ | ✅ | ✅ |
| 모바일 | ⚠ 반응형 미흡 | ⚠ | ✅ |
| 오픈소스/Self-host | ✅ | 유료 | 유료 |

---

## 정성적 한 줄 평

> flow 는 이제 **"공정 엔지니어 한 팀이 실제로 쓸 수 있는 도구"** 단계. 18 릴리즈(v8.0→8.8.31) 동안 실사용 피드백 기반으로 daily 단위 iterate — 그 결과 기능 완성도가 Jira/Confluence 수준이지만, 동시에 테스트·관측·멀티테넌시 등 **"조직 전체가 의존하는 플랫폼"** 이 되기 위한 프로덕션 hardening 은 아직 얇다.

---

## 다음 6주 제안 (우선순위 순)

1. **smoke test 세트** — pytest 20개 (로그인/인폼 CRUD/SplitTable override/메일 rate limit)
2. **SPC 페이지 초벌** — v8.x 이래 백로그 1위
3. **DVC 방향성 뱃지** — 각 파라미터에 ↑좋음/↓나쁨 annotation (dvc-curator 에이전트 산출물 소비)
4. **ET time 분석** — 시간대별 heatmap (ettime 페이지)
5. **구조화 로깅** — request_id + 요청별 JSON line
6. **pip-audit + npm audit** — GitHub Actions 한 번만 돌려도 됨

---

## 사용자 관점 체감 수준 (점수)

| 측면 | 점수 (10점 만점) |
|---|---|
| **기능 커버리지** | 8/10 — 일상 업무 대부분 |
| **도메인 정합성** | 7/10 — long format 정합, DVC 방향성 약점 |
| **UX 일관성** | 7/10 — PageGear/톱니·pill/dot 통일 거의 완료 |
| **성능** | 6/10 — polars lazy 기반으로 꽤 빠르지만 대용량 scan 은 미검증 |
| **안정성 (회귀)** | 4/10 — 테스트 0, 실사용 피드백으로만 잡음 |
| **운영 관측성** | 5/10 — 감사 로그 + sysmon 있지만 알람/Prom 없음 |
| **보안** | 6/10 — auth/session/rate limit 있음, secret 관리·의존성 감사 부족 |
| **확장성 (SaaS)** | 3/10 — single-tenant 설계 |
| **문서화** | 5/10 — CHANGELOG 상세, 하지만 사용자 가이드 얇음 |
| **종합** | **6.5/10 — 사내 pilot 상용 수준, 플랫폼화는 아직** |
