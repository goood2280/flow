# Dashboard

Dashboard는 저장된 chart와 snapshot을 통해 운영 상태를 빠르게 보는 chart-only 화면이다.

## Owns

- KPI, trend, SPC성 chart
- chart session과 dashboard snapshot
- 일반 chart 종류와 Inform preset을 한 곳에서 추가하는 chart library
- Flow-i/LLM dashboard chart draft 생성과 사용자의 저장 확인 경계

## Does Not Own

- raw data 파일 탐색
- plan 편집
- issue lifecycle의 원본 상태 변경
- Home Flow-i Agent 화면 연계
- FAB progress와 alert watch 화면 책임. 기존 API는 호환용으로 유지하지만 Dashboard 기본 UI에서는 호출하지 않는다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Dashboard.jsx` |
| Backend router | `backend/routers/dashboard.py` |
| Snapshot data | `data/flow-data/dashboard_snapshots.json` |
| Chart sessions | `data/flow-data/dashboard_chart_sessions/` |

## Guardrails

- raw data 탐색은 FileBrowser로 넘긴다.
- plan/actual 편집은 SplitTable로 넘긴다.
- 자동 refresh와 snapshot은 사용자에게 상태가 보여야 한다.
- Dashboard 진입 시 `/api/dashboard/fab-progress`, `/api/dashboard/summary`, `/api/dashboard/trend-alerts`를 호출하지 않는다.
- `+ 차트 추가`는 일반 chart type, Inform preset, AI draft 생성의 단일 진입점이다.
- Flow-i/LLM은 chart draft 또는 chart session을 만들고, 실제 저장은 사용자의 `저장` 또는 명시적 확인 이후 `/api/dashboard/charts/save`로 수행한다.
- Flow-i unit action은 `dashboard.chart.llm.draft`로 노출하며 Home에서는 chart draft/config와 inline preview를 먼저 보여준다. 여러 DB/단일파일을 거친 chart draft는 `core.flowi_multisource`가 실제 source rows와 confirmed `schema_relations` join plan을 만든 뒤 chart config의 `source_evidence`에 `source_ids`, `relation_ids`, `join_keys`, `selected_columns`, `sql_plan`을 보존하고 Dashboard 편집 화면에 노출한다.
- Home Flow-i의 ET 단일 metric trend는 `tkout_time` X축 scatter, `lot_wf` 기준 median 집계가 기본이다. 직전 trend chart session이 있으면 KNOB 컬러링/값 제외와 1차 fitting line/R² 후속 요청은 같은 product/metric/lot scope를 재사용한다.
- Home Flow-i chart session은 표시된 point/group raw row를 보관하며, 사용자가 raw data CSV를 요청하면 FileBrowser 다운로드 제한을 통과한 경우에만 `/api/llm/flowi/chart-session/raw-data.csv`로 내려받게 한다.
- Home Flow-i chart result와 Dashboard scatter/trend render는 Plotly를 우선 사용하고, 기존 SVG renderer는 비대상 chart type과 fallback 경로로 유지한다.
- Shared chart 설정(chart defaults, saved chart CRUD, manual snapshot refresh)은 `dashboard` page manager 이상만 쓴다. 개인 layout 저장은 기존처럼 current user 기준이다.

## Verify

```bash
git diff --check
cd frontend && npm run build
python scripts/smoke_test.py
python scripts/flowi_chart_scenario_check.py
```
