# FabCanvas.ai — Architecture (v8.3.0)

> 에이전트가 작업 시 긴 원본을 다시 파싱하지 않고 **필요한 섹션만** 참조하기 위한 요약.
> 도메인 원본: `../FabCanvas_domain.txt` · 릴리즈 로그: `../VERSION.json`.

## 1. 모듈 구조

| 레이어 | 스택 | 위치 | 비고 |
| --- | --- | --- | --- |
| Frontend | Vite + React 18 (SPA) | `frontend/src/` | `npm run build` → `frontend/dist/` 로 산출, backend 가 정적 서빙 |
| Backend | FastAPI + Polars | `backend/app.py` | `uvicorn app:app --host 0.0.0.0 --port 8080`. 라우터는 `backend/routers/*.py` 를 glob 동적 로딩 |
| Core | 순수 Python | `backend/core/` | `paths.py`, `roots.py`, `matching.py`, `domain.py`, `s3_sync.py`, `session.py`, `notify.py` |
| Data (raw) | CSV, Hive-flat | `data/DB/` | `FAB / INLINE / ET / EDS / LOTS` 테이블, `product=<P>` 파티션. `wafer_maps/*.json` 포함 |
| Data (base) | CSV + Parquet | `data/Base/` | 룰북(`dvc_rulebook.csv`), 매칭 테이블, `_uniques.json` 카탈로그, wafer-level 피처 parquet 2종 |

제약: 웹서버 메모리 제한, GPU 없음. 외부망 차단 · 사내망 전용. AI 없이 100% 독립 동작 가능해야 함.

## 2. 데이터 흐름 (도메인 [4] 요약)

```
DataLake  →  쿼리 VM (~100GB RAM)  →  S3 parquet  →  FabCanvas 웹서버
                (ETL · 추출)           (write)         (주기적 read)
```

- API 연동이 아닌 **S3 파일 기반 느슨한 결합**.
- ETL 은 VM 단에서 처리, FabCanvas 는 정제된 데이터만 소비.
- 데모(repo `data/`)와 사내(S3 mount)가 동일 코드베이스로 돌도록 roots chain 으로 분기.

## 3. 어댑터 레이어 — `core/roots.py`

데이터 루트 3종(`db_root`, `base_root`, `wafer_map_root`)의 해석 순서(first match wins):

1. 신 env: `FABCANVAS_DB_ROOT` / `FABCANVAS_BASE_ROOT` / `FABCANVAS_WAFER_MAP_ROOT`
2. Legacy env: `HOL_DB_ROOT` (DB only — Base 는 v8.3 split 이후라 legacy 없음)
3. `data/admin_settings.json` → `data_roots.{db|base|wafer_map}` (Admin UI 로 런타임 수정 가능)
4. Prod 자동 감지: `/config/work/sharedworkspace/DB` (`+ /Base`)
5. Repo 기본값: `<PROJECT_ROOT>/data/DB` (`+ /data/Base`)

`wafer_map_root` 만 특수 — 전 tier 에서 미설정 시 `<db_root>/wafer_maps` 로 폴백.
`snapshot()` 이 `/admin` 진단 UI 에 roots dict 를 노출.

Import 규약: `paths → roots` 단방향. `roots.py` 는 `core.paths` 를 import 하지 않음 (부팅 순환 방지).

## 4. 페이지 ↔ dev-* 에이전트 매핑

| Page (`frontend/src/pages/`) | 에이전트 | 주된 기능 |
| --- | --- | --- |
| `My_Home.jsx` | dev-lead 직속 | 랜딩 · changelog · 세션당 1회 unread 팝업 |
| `My_Dashboard.jsx` | dev-dashboard | 차트 config · exclude_null · admin auto-refresh 주기 |
| `My_TableMap.jsx` | dev-tablemap | 엑셀 스타일 편집 · 노드 그래프 · CSV 저장 |
| `My_SplitTable.jsx` | dev-spc (SPC plan 계열) | `_uniques.json` 드롭다운 · root_lot × wafer sticky |
| `My_ETTime.jsx` | dev-ettime | ET 타임라인 · 배치 수신 리포팅 |
| `My_ML.jsx` | dev-ml | TabICL / XGBoost / LightGBM · SHAP |
| `My_FileBrowser.jsx` | dev-filebrowser | DB roots · parquet 카운트 · S3 sync |
| `My_Message.jsx` | dev-messages | 1:1 스레드 + 공지사항 |
| `My_Tracker.jsx` | dev-tracker | 이슈 · 카테고리 색상 · Gantt |
| `My_Monitor.jsx` | dev-admin | 헬스 · 경로 로그 · AWS profile |
| `My_Admin.jsx` | dev-admin | 유저 · 알림 · 메시지 inbox · roots 설정 |
| `My_Login.jsx` / `My_DevGuide.jsx` | dev-lead | 로그인 타이핑 애니 · 문서 |

Wafer Map 뷰어는 `dev-wafer-map` 담당(현재 `My_ML` 과 `My_Dashboard` 에서 재사용).

## 5. OmniHarness Hook

- 에이전트는 결정이 막히면 `POST /api/questions`(OmniHarness backend, 포트 8082)로 `{agent, raw, context?}` 를 발행 → Questions 탭에 렌더 → 사용자 답변은 `POST /api/questions/{qid}/answer` 로 비동기 수신. FabCanvas 는 로그 태그(`logger name "holweb"` v8.3 이후에도 유지)로만 출처를 식별하고 OmniHarness 는 agent 필드로 라우팅한다. 상세 규약은 `AGENT_QUESTIONS.md`.

## 6. 빌드 · 배포

- Frontend: `cd frontend && npm run build` → `dist/` 산출물을 backend 가 `/assets` + SPA fallback 으로 서빙.
- Backend: `uvicorn app:app --host 0.0.0.0 --port 8080`. 부팅 시 `routers/` 를 glob 하여 `router` / `match_router` 를 자동 include.
- Seed: 첫 부팅 시 admin 계정 `hol / hol12345!` 생성(브랜드 교체 후에도 유지).
- Docker: 멀티스테이지 (dev/prod 분리). GitHub 기반 코드 관리. 상세는 도메인 원본 [11] 참조.

## 7. 백엔드 라우터 prefix 맵

모든 API 는 `/api/<name>/` prefix. `app.py` 가 `routers/*.py` 에서 `router` / `match_router` 객체를 자동 glob include.

| 파일 | Prefix | 역할 |
| --- | --- | --- |
| `auth.py` | `/api/auth` | 로그인 · 회원가입 · 사용자 |
| `admin.py` | `/api/admin` | 승인 · 권한 · 알림 · 전역 settings · roots override |
| `filebrowser.py` | `/api/filebrowser` | DB 트리 · parquet/CSV preview · SQL filter |
| `s3_ingest.py` | `/api/filebrowser` (shared) | S3 sync · AWS profile 관리 |
| `dashboard.py` | `/api/dashboard` | 차트 config CRUD · `compute_chart` · `exclude_null` |
| `splittable.py` | `/api/splittable` | Lot × Wafer plan 저장/조회 |
| `tracker.py` | `/api/tracker` | 이슈 · 카테고리 색상 · Gantt |
| `tablemap.py` → `dbmap.py` | `/api/tablemap` | 관계 그래프 · DB refs · 그룹 |
| `ml.py` | `/api/ml` | Process-Window ML · SHAP |
| `ettime.py` | `/api/ettime` | ET 타임라인 |
| `monitor.py` | `/api/monitor` | `/proc` + cgroup 기반 시스템 모니터 |
| `catalog.py` | `/api/catalog` | 제품/설비 카탈로그 |
| `reformatter.py` | `/api/reformatter` | 스키마 변환 규칙 |
| `messages.py` | `/api/messages` | 1:1 + 공지 (v8.2+) |

자세한 히스토리는 [`reference/v8_1_5_ARCHITECTURE.md`](reference/v8_1_5_ARCHITECTURE.md) §3 참조.

## 8. 데이터 처리 규칙

### 8.1 DB 디렉토리 포맷 3종
- **Hive-partitioned** — `1.RAWDATA~/<product>/date=YYYY-MM-DD/*.parquet` (시계열 대용량)
- **Root parquet** — DB 루트에 직접 `*.parquet` / `*.csv` (수천 컬럼, 단일 파일)
- **Flat directory** — `DB/<name>/*.parquet` (partition 없이, 소규모 lookup)

### 8.2 Polars 호환성 가드
```python
_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)
# Bulk cast 금지 — 컬럼별 cast
df = df.with_columns([pl.col(c).cast(_STR, strict=False) for c in str_cols])
# Multi-file scan 시 categorical 혼재 허용
pl.scan_parquet(paths, cast_options=pl.ScanCastOptions(categorical_to_string="allow"))
```

### 8.3 SplitTable plan key 포맷
`{lot_id}|{col_name}|{wafer_id}` — `wafer_id` 는 원본 문자열 유지 (zero-pad 변환 금지).

## 9. 주요 함정 (Gotchas)

| # | 함정 | 해결 |
| --- | --- | --- |
| 1 | Vite 는 lazy import 불안정 | `App.jsx` 는 static import 만 |
| 2 | `version.json` 을 static 로 두면 SPA catch-all 이 가로챔 | 반드시 API 라우트로 제공 |
| 3 | `safeFetch` 미사용 시 HTML 에러 페이지가 JSON parse 실패 유발 | `src/lib/api.js` 의 `sf` / `postJson` 사용 |
| 4 | Profile dropdown 클리핑 | `position:fixed` 로 탈출 |
| 5 | JSX ternary else-branch 에 multiple children | `<>...</>` fragment 필수 |
| 6 | `psutil` 사용 불가 (사내 규정) | `/proc/*` + cgroup 직접 파싱 |
| 7 | Categorical cast per-column 필수 | bulk `df.cast()` 금지 |
| 8 | Dashboard `exclude_null` default True (v8.1.5~) | 끄려면 Chart Config → Advanced |
| 9 | 차트 폴링 heartbeat 는 CPU burst 만 (memory burst 는 99% 가서 서버 reclaim) | `monitor.py` 참조 |
| 10 | `App.jsx` 는 parallel 에이전트 충돌 지점 | 동시 수정 피하고 orchestrator 경유 |

## 10. 신규 기능 추가 체크리스트

- [ ] `backend/routers/<new>.py` 에 `router = APIRouter(prefix="/api/<new>")`
- [ ] `frontend/src/pages/My_<New>.jsx` 추가 (static import)
- [ ] `src/config.js` 의 `TABS` 에 탭 항목 추가
- [ ] `App.jsx` 의 `PAGE_MAP` 에 컴포넌트 등록
- [ ] `FEATURE_VERSIONS` 에 버전 기록
- [ ] dev-* 에이전트 스펙 업데이트 (`.claude/agents/`)
- [ ] `ARCHITECTURE.md` §4 페이지 매핑 표 갱신

v8.1.5 당시의 setup_v8 11-part 분할 / update_vXXX 배포 흐름 등 레거시 릴리즈 패턴은 [`reference/v8_1_5_ARCHITECTURE.md`](reference/v8_1_5_ARCHITECTURE.md) §5, [`reference/v8_1_5_UPDATE_GUIDE.md`](reference/v8_1_5_UPDATE_GUIDE.md) 참조 (v8.2+ 현행 배포와는 다름).
