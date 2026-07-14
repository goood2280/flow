# Admin

Admin은 사용자, 권한, 그룹, root, 백업, 메일/API/LLM 설정, 모니터링을 관리한다.

## Owns

- user/group/permission 관리
- data root, S3, mail, LLM, backup, monitor 설정
- `LLM 설정` 탭 — LLM 연결 상태/프로필/에이전틱 토글 UI host (v9.2.x에서 Agent 탭에서 이관, 컴포넌트는 `frontend/src/components/agent/LlmTab.jsx`)
- `Flow-i 학습` 탭 — 하위 섹션 3개: `용어사전 (Semantic layer)`(`frontend/src/components/agent/SemanticLayerPanel.jsx`, `/api/agent/semantic/*`), `few-shot 용어`, `파일 설명`(`/api/flowi-learning/*`)
- Base CSV 편집기와 product YAML 관리
- 관리자 공지와 운영 설정

## Does Not Own

- 일반 사용자의 업무 화면 상태 변경
- raw DB 파일 임의 수정
- 사용자 대신 issue/inform 업무를 생성하는 기능
- Inform 전용 설정 화면. Inform module/reason/template/contact 설정은 Inform Log PageGear가 소유한다.
- **캐시 운영 패널** — Admin은 SplitTable 매칭 캐시나 Tracker Analysis 캐시를 소유하지 않는다. FileBrowser가 LOT 진행 최신 캐시의 상태/수동 갱신 진입점을 제공한다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Admin.jsx` |
| Admin router | `backend/routers/admin.py` |
| Groups router | `backend/routers/groups.py` |
| Monitor router | `backend/routers/monitor.py` |
| Catalog router | `backend/routers/catalog.py` |
| Product config | `data/flow-data/product_config/` |

## Guardrails

- 권한 없는 사용자가 설정을 읽거나 바꾸지 못해야 한다.
- 운영 설정은 사용자 기능과 섞지 않는다.
- 변경 전후 진단과 rollback 후보를 보여준다.
- root/path 변경은 `docs/SOFT_LANDING_INTERNAL.md`와 preflight 기준을 따른다.
- Page manager 위임 키는 canonical page id만 저장한다: `filebrowser`, `dashboard`, `splittable`, `tracker`, `inform`, `meeting`, `calendar`, `tablemap`, `groups`, `messages`, `devguide`, `diagnosis`.
- Legacy alias는 읽을 때만 흡수한다: `informs -> inform`, `meetings -> meeting`, `dbmap -> tablemap`.
- Shared 설정, catalog, rulebook, cache, credential, wiki/schema write는 global admin 또는 해당 page manager 이상만 허용한다.
- 비밀번호 reset/bulk-create API 응답과 audit log에는 임시/기본 비밀번호를 남기지 않는다.
- 비밀번호 reset/forgot 메일은 Admin 메일 설정의 `from_addr`, `status_code`, `domain`을 따르고 첨부 없이 전송한다.

## Delegated Write Rules

| Area | Canonical page id | Delegated write scope |
|---|---|---|
| FileBrowser | `filebrowser` | S3 ingest/AWS config, FileBrowser settings, base-file edit/rollback/delete, cache settings/refresh/cleanup |
| Dashboard | `dashboard` | chart defaults, saved chart CRUD, snapshot refresh |
| SplitTable | `splittable` | source config, rulebook/schema, prefixes, precision, paste sets, custom sets, match cache refresh |
| Inform | `inform` | Inform Log PageGear의 module/reason/template/contact 설정 위임 |
| Calendar | `calendar` | shared categories/settings |
| Tracker | `tracker` | shared categories, scheduler, DB sources, lot progress cache |
| TableMap | `tablemap` | DB map/table/product config writes |
| Agent/Knowledge | `diagnosis` | wiki/schema shared writes and ontology rebuild/save |

## Verify

```bash
git diff --check
python3 scripts/preflight_internal.py --write-probe
```
