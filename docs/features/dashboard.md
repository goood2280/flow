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

## Verify

```bash
git diff --check
cd frontend && npm run build
python scripts/smoke_test.py
```
