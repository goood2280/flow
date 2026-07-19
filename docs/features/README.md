# Feature Docs

이 디렉터리는 Flow의 화면/기능별 현재 책임, 코드 진입점, 검증 포인트를 짧게 정리한다. 세부 목표 비교는 [../FEATURE_GOALS.md](../FEATURE_GOALS.md)를 함께 본다.

## Index

| 기능 | 문서 | 주요 코드 |
|---|---|---|
| Home, Messages | [home-messages.md](home-messages.md) | `frontend/src/pages/My_Home.jsx`, `backend/routers/home.py`, `backend/routers/messages.py` |
| FileBrowser | [filebrowser.md](filebrowser.md) | `frontend/src/pages/My_FileBrowser.jsx`, `backend/routers/filebrowser.py` |
| SplitTable | [splittable.md](splittable.md) | `frontend/src/pages/My_SplitTable.jsx`, `backend/routers/splittable.py` |
| Inform Log | [inform.md](inform.md) | `frontend/src/pages/My_Inform.jsx`, `backend/routers/informs.py` |
| Dashboard | [dashboard.md](dashboard.md) | `frontend/src/pages/My_Dashboard.jsx`, `backend/routers/dashboard.py` |
| Tracker | [tracker.md](tracker.md) | `frontend/src/pages/My_Tracker.jsx`, `backend/routers/tracker.py` |
| Meeting, Calendar | [meeting-calendar.md](meeting-calendar.md) | `frontend/src/pages/My_Meeting.jsx`, `frontend/src/pages/My_Calendar.jsx` |
| Diagnosis, Knowledge, RAG | [diagnosis-knowledge.md](diagnosis-knowledge.md) | `backend/routers/knowledge.py` |
| TableMap | [diagnosis-knowledge.md](diagnosis-knowledge.md) | `frontend/src/pages/My_TableMap.jsx` |
| Admin, Groups, Monitor | [admin.md](admin.md) | `frontend/src/pages/My_Admin.jsx`, `backend/routers/admin.py` |
| Agent | [flowi-agent.md](flowi-agent.md) | `frontend/src/pages/My_Diagnosis.jsx`, `frontend/src/components/agent/LlmTab.jsx`, `backend/routers/agent.py` |
| Dashboard Agent source orchestration | [home_sql_join_dashboard.md](home_sql_join_dashboard.md) | `backend/core/flowi_units/home_sql_join_dashboard_runtime.py`, `backend/core/flowi_units/dashboard_agent_runtime.py` |
| Agent Semantic Layer | [agent-semantic-layer.md](agent-semantic-layer.md) | `backend/routers/agent.py`, `backend/app_v2/modules/semantic_lexicon/`, `backend/app_v2/modules/semantic_learning/` |
| Knowledge Layer (지식 카드) | [knowledge-layer.md](knowledge-layer.md) | `backend/core/knowledge_cards.py`, `backend/core/knowledge_seed_cards/` |

## Update Rule

- 새 기능을 붙이기 전에 해당 문서의 "Owns"와 "Does Not Own"을 확인한다.
- 코드 진입점이 바뀌면 같은 변경에서 기능 문서도 고친다.
- 과거 긴 가이드와 분석 전문은 `archive/docs_reorg_2026_05_08/`에 보관한다.
