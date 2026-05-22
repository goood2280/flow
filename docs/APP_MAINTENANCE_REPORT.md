# Flow App Maintenance Report

작성 기준: 현재 checkout의 코드/문서 구조와 runtime data 보존 규칙.

## 1. 목적과 이번 정리 범위

이 리포트는 Flow 앱을 사용자 경험, 유지보수성, 응답속도, 서버 안정성 관점에서 운영자가 바로 점검할 수 있도록 정리한다.

이번 1차 정리는 전면 재작성 대신 이미 문서화된 고빈도 병목을 닫는 방식으로 진행했다.

- LOT progress cache read path에 인메모리 인덱스를 추가했다.
- Tracker 최신 FAB step fallback과 Inform product 옵션이 cache parquet를 매번 직접 scan하지 않도록 바꿨다.
- light API 100ms 예산을 확인하는 probe 스크립트를 추가했다.
- 대형 SQL, 대용량 파일 preview/download, Flow-i LLM/다중 agent 작업은 100ms 완료 대상이 아니라 loading/status/trace를 보여주는 대상으로 분리한다.

## 2. 앱 구성

Flow는 FastAPI backend와 React/Vite frontend가 한 프로세스에서 동작하는 단일 웹 앱이다.

```text
Browser
  -> FastAPI :8080
     -> /api/*          backend/routers/*.py
     -> /version.json   VERSION.json
     -> /*              frontend/dist SPA
```

주요 디렉터리:

| 경로 | 책임 |
|---|---|
| `backend/app.py` | FastAPI app assembly, middleware, router loading, static serving |
| `backend/routers/` | 기능별 HTTP endpoint, 권한 확인, response shape |
| `backend/core/` | roots/path, cache, LLM adapter, knowledge, data helper |
| `backend/app_v2/runtime/` | auth middleware, router loader, resource guard, startup jobs |
| `backend/app_v2/modules/` | 점진 분리된 feature service/repository/domain |
| `frontend/src/App.jsx` | tab shell, global UI, error boundary |
| `frontend/src/app/` | page registry, shell state |
| `frontend/src/pages/My_*.jsx` | 기능별 page |
| `frontend/src/components/` | shared UI, Plotly chart, modal, toast |
| `data/Fab/` | local DB seed |
| `data/flow-data/` | runtime/user state. 코드 업데이트로 덮어쓰면 안 됨 |
| `scripts/` | smoke, scenario, preflight, performance probe |
| `docs/features/` | 기능별 현재 책임과 code entrypoint |

## 3. 현재 가능한 작업

| 화면/기능 | 사용자가 할 수 있는 작업 | 주요 backend |
|---|---|---|
| Home / Flow-i | 자연어로 SplitTable, FileBrowser, Inform, chart, wiki 근거 조회 | `backend/routers/llm.py`, `backend/routers/home.py` |
| FileBrowser | DB/file 탐색, 100행 preview, AI SQL draft, CSV download, LOT progress cache 운영 | `backend/routers/filebrowser.py`, `backend/core/lot_progress_cache.py` |
| SplitTable | 제품별 split/KNOB table 조회, plan 편집, note/history, cache 기반 lot 후보 | `backend/routers/splittable.py` |
| Dashboard | chart CRUD, Flow-i chart draft, Plotly trend/scatter 표시 | `backend/routers/dashboard.py` |
| Agent | goal -> semantic layer -> unit-agent graph -> SSE trace -> conclusion, LLM 연결 | `backend/routers/agent.py`, `backend/app_v2/modules/agent_runtime/` |
| Tracker | issue/lot 상태 추적, meeting/inform/wiki evidence 연결 | `backend/routers/tracker.py` |
| Inform | product/module/reason 기반 inform 작성, mail preview, split snapshot 첨부 | `backend/routers/informs.py` |
| Meeting / Calendar | 회의록, action/decision recall, calendar event 관리 | `backend/routers/meetings.py`, `backend/routers/calendar.py` |
| Knowledge Wiki | agent wiki, schema doc, graph, impact context 근거 관리 | `backend/routers/knowledge.py`, `backend/core/knowledge_vault.py` |
| Admin | 사용자/권한/root/settings/backup/LLM 연결 관리 | `backend/routers/admin.py` |

## 4. 응답속도 기준

Flow의 UX 기준은 두 종류로 나눈다.

| 분류 | 목표 | 예 |
|---|---:|---|
| Light endpoint | warm 상태 100ms 이하 | 목록, 설정, roots, chart list, tracker issue 5건, meeting list |
| Heavy endpoint | 즉시 완료 대신 loading/status/trace | 대형 SQL, 50GB parquet scan, CSV download, cache rebuild, Flow-i LLM/multi-agent |

운영 probe:

```bash
FLOW_BASE=http://127.0.0.1:8080 python3 scripts/latency_budget_probe.py
```

환경 변수:

| 변수 | 기본값 | 의미 |
|---|---:|---|
| `FLOW_LATENCY_BUDGET_MS` | `100` | light endpoint 예산 |
| `FLOW_LATENCY_TIMEOUT_SEC` | `10` | probe request timeout |
| `FLOW_USER` / `FLOW_PW` | `hol` / `hol12345!` | probe 로그인 계정 |

이번에 닫은 병목:

| 이전 상태 | 현재 상태 |
|---|---|
| `lookup_lot_progress`가 `items` list를 매 호출 선형 scan | `by_product`, `by_lot_id`, `by_root_lot_id`, `by_wafer_id`, `by_lot_wf` 인덱스 사용 |
| Tracker 최신 FAB step fallback이 cache parquet를 매번 `pl.scan_parquet` | `lot_progress_snapshot(refresh_if_missing=False)`로 메모리/JSON cache만 읽음 |
| Inform product 옵션이 cache parquet를 매번 `pl.scan_parquet` | `list_products()`가 기존 JSON/memory cache를 읽고 source refresh를 트리거하지 않음 |

현재 probe에서 남은 병목:

| endpoint | 관측 | 다음 조치 |
|---|---|---|
| `/api/knowledge/wiki?limit=10` | 약 300ms | Wiki index/graph read path cache 또는 heavy/readiness 분류 검토 |
| `/api/meetings/list` | 간헐 120ms | list payload 축소 또는 mtime 기반 cache 검토 |

## 5. 서버 안정성 기준

현재 서버 안정성 장치는 아래처럼 구성되어 있다.

| 장치 | 위치 | 역할 |
|---|---|---|
| runtime thread 제한 | `backend/core/runtime_limits.py` | Polars/Rayon/PyArrow/BLAS thread 수 기본 제한 |
| memory soft guard | `backend/app_v2/runtime/resource_guard.py` | heavy API 동시성 제한, memory high 상태에서 503/429 반환 |
| heavy background opt-in | `backend/app_v2/runtime/startup.py` | 대형 DB scanner scheduler를 기본 비활성화 |
| sample-first FileBrowser | `backend/routers/filebrowser.py`, `frontend/src/pages/My_FileBrowser.jsx` | 대형 parquet를 첫 화면에서 전체 scan하지 않음 |
| lock/log 기반 cache refresh | `backend/core/lot_progress_cache.py` | 중복 cache build 방지, refresh 결과 기록 |
| auth middleware | `backend/app_v2/runtime/security.py` | `/api/*` session token gate |

운영자가 서버 꺼짐/메모리 급증을 볼 때 우선 확인할 것:

1. `/api/system/stats`에서 CPU/memory를 확인한다.
2. `/api/filebrowser/cache/status` 또는 `/api/lot-progress/status`에서 cache refresh가 running인지 확인한다.
3. heavy endpoint가 429/503을 냈다면 `Retry-After`를 따르고 동시에 실행 중인 scan/download를 줄인다.
4. `FLOW_ENABLE_HEAVY_BACKGROUND_JOBS`, `FLOW_ENABLE_TRACKER_ET_LOT_CACHE`, `FLOW_ENABLE_SPLITTABLE_MATCH_CACHE`가 켜져 있는지 확인한다.
5. 운영 data root는 삭제하지 말고, `scripts/preflight_internal.py --write-probe`로 root 보존을 먼저 확인한다.

## 6. 유지보수 원칙

작업 전:

```bash
git status --short --branch
sed -n '1,220p' docs/AGENT_FLOW_CONTEXT.md
sed -n '1,220p' docs/DEVELOPMENT.md
```

수정 시:

- 새 backend I/O는 router에 길게 넣지 말고 `backend/app_v2/modules/<feature>` 또는 기존 `backend/core` helper를 우선 사용한다.
- 새 frontend API 호출은 `frontend/src/lib/api.js` helper를 우선 사용한다.
- runtime/user data인 `data/flow-data/`는 코드 변경과 함께 정리하지 않는다.
- 기능 ownership이 바뀌면 `docs/features/<feature>.md`를 같은 변경에서 갱신한다.
- 대형 라우터/페이지는 한 번에 갈아엎지 말고 한 사용자 흐름, 한 endpoint, 한 panel 단위로 나눈다.

검증 기준:

```bash
git diff --check
python3 -m pytest tests/test_lot_progress_cache.py -q
python3 -m pytest tests/test_filebrowser_sql.py tests/test_splittable_lot_candidates.py tests/inform -q
cd frontend && npm run build
python3 scripts/smoke_test.py
FLOW_BASE=http://127.0.0.1:8080 python3 scripts/latency_budget_probe.py
```

`setup.py` 또는 bundled docs/source를 바꿨다면:

```bash
python3 _build_setup.py
python3 setup.py version
```

## 7. 남은 리스크와 다음 개선 후보

아래는 이번 1차 정리 후에도 남는 현실적인 리스크다.

| 리스크 | 다음 조치 |
|---|---|
| `backend/routers/llm.py`와 `backend/routers/filebrowser.py`가 여전히 크다 | Flow-i unit action handler, FileBrowser SQL/cache service를 feature module로 단계 추출 |
| cold cache가 없는 첫 요청은 빈 readiness 또는 refresh 경로로 갈 수 있다 | startup warm-up 결과를 `/api/lot-progress/status`에 더 명확히 노출 |
| 100ms는 환경 의존적이다 | `scripts/latency_budget_probe.py`를 warm server에서 정기 실행하고 회귀 endpoint를 feature TODO로 올림 |
| runtime data dirty가 검증 중 계속 생긴다 | source commit과 runtime artifact를 분리하고, 필요 시 runtime cleanup은 별도 작업으로 수행 |
| full test/smoke는 로컬 서버와 data root 상태에 의존한다 | 단위 테스트 + build + live smoke 결과를 각각 별도로 기록 |

## 8. 운영 체크리스트

배포/반입 전 최소 확인:

1. `git status --short --branch`로 source 변경과 runtime data churn을 분리한다.
2. `python3 -m pytest tests -q` 또는 영향 범위 pytest를 실행한다.
3. `cd frontend && npm run build`를 실행한다.
4. 서버를 띄운 뒤 `python3 scripts/smoke_test.py`를 실행한다.
5. warm 상태에서 `python3 scripts/latency_budget_probe.py`를 실행한다.
6. `python3 _build_setup.py`와 `python3 setup.py version`으로 bundle metadata를 갱신한다.
7. GitHub push는 사용자가 요청한 경우에만 수행한다.
