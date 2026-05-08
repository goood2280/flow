# Dashboard

Dashboard는 chart, fab progress, alert watch, snapshot을 통해 운영 상태를 빠르게 보는 화면이다.

## Owns

- KPI, trend, SPC성 chart, fab progress
- alert watch, admin section visibility
- chart session과 dashboard snapshot
- Inform/Tracker 등 다른 기능의 요약 widget

## Does Not Own

- raw data 파일 탐색
- plan 편집
- issue lifecycle의 원본 상태 변경

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
- admin section visibility는 권한 경계를 유지한다.

## Verify

```bash
git diff --check
cd frontend && npm run build
python scripts/smoke_test.py
```
