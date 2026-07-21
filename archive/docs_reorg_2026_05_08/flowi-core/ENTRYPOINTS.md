# Flow App Entrypoints

Flow 앱의 동작 진입점만 추린 문서다. 전체 구조가 필요하면 `docs/ARCHITECTURE.md`를 보되, 작업 시작 기준은 이 파일을 우선한다.

## Runtime

- 앱 루트 실행: `app.py`
- 실제 FastAPI 조립: `backend/app.py`
- router 자동 로드: `backend/app_v2/runtime/router_loader.py`
- startup/background service: `backend/app_v2/runtime/startup.py`
- 인증/권한 middleware: `backend/app_v2/runtime/security.py`
- 경로 기준: `backend/core/paths.py`

실행 명령:

```bash
cd flow
uvicorn app:app --host 0.0.0.0 --port 8080
```

프론트 빌드:

```bash
cd flow/frontend
npm run build
```

## Frontend Shell

- shell composition: `frontend/src/App.jsx`
- page registry: `frontend/src/app/pageRegistry.jsx`
- page state/hook: `frontend/src/app/useFlowShell.js`
- API helper: `frontend/src/lib/api.js`

주요 page key:

| Key | Page |
|---|---|
| `home` | `frontend/src/pages/My_Home.jsx` |
| `filebrowser` | `frontend/src/pages/My_FileBrowser.jsx` |
| `splittable` | `frontend/src/pages/My_SplitTable.jsx` |
| `inform` | `frontend/src/pages/My_Inform.jsx` |

## Flowi / LLM

- API router: `backend/routers/llm.py`
- Flowi entry guide: `data/flow-data/flowi_agent_entrypoints.md`
- Flowi feature guides: `data/flow-data/flowi_agent_features/*.md`
- activity log: `data/flow-data/flowi_activity.jsonl`
- feedback log: `data/flow-data/flowi_feedback.jsonl`
- user profile notes: `data/flow-data/flowi_users/*.md`

Flowi는 앱 기능을 고르는 라우터다. 원본 DB를 수정하거나 코드를 변경하는 권한 경로가 아니다.

## Core Data Roots

| Path | Role |
|---|---|
| `data/Fab/` | local DB root, parquet/CSV/rulebook |
| `data/flow-data/` | runtime data root, users/settings/informs/cache |
| `FLOW_DB_ROOT` | operational DB root override |
| `FLOW_DATA_ROOT` | operational runtime data override |

## Validation

문서만 바꿨으면:

```bash
cd flow
git diff --check
```

프론트 동작을 바꿨으면:

```bash
cd flow/frontend
npm run build
```

백엔드/API를 바꿨으면:

```bash
cd flow
python -m pytest tests
```
