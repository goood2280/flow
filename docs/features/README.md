# Feature Docs

이 디렉터리는 Flow의 화면/기능별 현재 책임, 코드 진입점, 검증 포인트를 짧게 정리한다. 세부 목표 비교는 [../FEATURE_GOALS.md](../FEATURE_GOALS.md)를 함께 본다.

인덱스는 `frontend/src/config.js`의 탭 목록과 일치해야 한다. 새 탭을 붙이면 여기에 행을 추가한다.

## Index

| 탭 | 기능 | 문서 | 주요 코드 |
|---|---|---|---|
| `home` | Home, Messages | [home-messages.md](home-messages.md) | `frontend/src/pages/My_Home.jsx`, `backend/routers/home.py`, `backend/routers/messages.py` |
| `filebrowser` | FileBrowser | [filebrowser.md](filebrowser.md) | `frontend/src/pages/My_FileBrowser.jsx`, `backend/routers/filebrowser.py` |
| `splittable` | SplitTable | [splittable.md](splittable.md) | `frontend/src/pages/My_SplitTable.jsx`, `backend/routers/splittable.py` |
| `ramcache` | 캐시 관리 | [cache-management.md](cache-management.md) | `frontend/src/pages/My_RamCache.jsx`, `backend/routers/splittable.py`, `backend/core/cache_settings.py` |
| `inform` | Inform Log | [inform.md](inform.md) | `frontend/src/pages/My_Inform.jsx`, `backend/routers/informs.py` |
| `dashboard` | Dashboard | [dashboard.md](dashboard.md) | `frontend/src/pages/My_Dashboard.jsx`, `backend/routers/dashboard.py` |
| `tracker` | ET 추적 (Tracker) | [tracker.md](tracker.md) | `frontend/src/pages/My_Tracker.jsx`, `backend/routers/tracker.py` |
| `ettime` | ET 측정시간 | [et-time.md](et-time.md) | `frontend/src/pages/My_EtTime.jsx`, `backend/routers/et_time.py` |
| `reformatize` | ET 다운로드 | [reformatize.md](reformatize.md) | `frontend/src/pages/My_Reformatize.jsx`, `backend/routers/reformatize.py`, `backend/routers/reformatter.py` |
| `teg` | TEG 위치 조회 | [teg-map.md](teg-map.md) | `frontend/src/pages/My_TegMap.jsx`, `frontend/src/pages/TegCheck.jsx`, `backend/routers/teg_map.py` |
| `valve` | 매칭알람 | [valve-alerts.md](valve-alerts.md) | `frontend/src/pages/My_ValveAlerts.jsx`, `backend/routers/valve_alerts.py` |
| `meeting`, `calendar` | Meeting, Calendar | [meeting-calendar.md](meeting-calendar.md) | `frontend/src/pages/My_Meeting.jsx`, `frontend/src/pages/My_Calendar.jsx` |
| — | Diagnosis, Knowledge, RAG | [diagnosis-knowledge.md](diagnosis-knowledge.md) | `backend/routers/knowledge.py` |
| `tablemap` | TableMap | [diagnosis-knowledge.md](diagnosis-knowledge.md) | `frontend/src/pages/My_TableMap.jsx` |
| `admin` | Admin, Groups, Monitor | [admin.md](admin.md) | `frontend/src/pages/My_Admin.jsx`, `backend/routers/admin.py` |
| `diagnosis` | Agent | [flowi-agent.md](flowi-agent.md) | `frontend/src/pages/My_Diagnosis.jsx`, `frontend/src/components/agent/LlmTab.jsx`, `backend/routers/agent.py` |
| — | Dashboard Agent source orchestration | [home_sql_join_dashboard.md](home_sql_join_dashboard.md) | `backend/core/flowi_units/home_sql_join_dashboard_runtime.py`, `backend/core/flowi_units/dashboard_agent_runtime.py` |
| — | Agent Semantic Layer | [agent-semantic-layer.md](agent-semantic-layer.md) | `backend/routers/agent.py`, `backend/app_v2/modules/semantic_lexicon/`, `backend/app_v2/modules/semantic_learning/` |
| — | Knowledge Layer (지식 카드) | [knowledge-layer.md](knowledge-layer.md) | `backend/core/knowledge_cards.py`, `backend/core/knowledge_seed_cards/` |

## 문서가 없는 영역

아래 라우터는 아직 전용 문서가 없다. 손대게 되면 문서를 함께 만든다.

`auth`/`session_api` (로그인·세션), `dbmap`, `s3_ingest`, `sql_workspace`, `skills`, `mail_groups`, `flowi_learning`, `home_agent`, `aipd_bridge`

## Update Rule

- 새 기능을 붙이기 전에 해당 문서의 "Owns"와 "Does Not Own"을 확인한다.
- 코드 진입점이 바뀌면 같은 변경에서 기능 문서도 고친다.
- 새 탭을 추가하면 위 Index에 행을 추가한다 — `config.js`와 이 표가 갈라지면 다음 세션이 화면을 못 찾는다.
- 과거 긴 가이드와 분석 전문은 `archive/docs_reorg_2026_05_08/`에 보관한다.
