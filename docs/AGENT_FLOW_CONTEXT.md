# Flow Agent Context

이 문서는 Flow 프로젝트에만 해당하는 에이전트 맥락을 담는다. 일반 코딩 태도와 작업 방식은 루트 `AGENTS.md`를 따른다.

## Mission

현재 우선 미션은 **Agent 탭을 LangGraph/LangSmith-ready runtime surface로 재설계하고, FileBrowser AI SQL은 기존 FileBrowser 소유로 보존하는 것**이다.

- Agent는 자연어 목표를 semantic layer로 정규화하고 unit-agent graph 실행 상태를 SSE로 보여준다.
- `goal -> semantic_layer -> task_planner -> unit_agents -> conclusion` 흐름이 한 화면에서 보여야 한다.
- v9.2.x 재편(2026-07): Agent 탭 = `기능 카탈로그` / `실행 추적` / `Workflow 템플릿`. LLM 설정과 Semantic layer 편집기는 관리 페이지로 이관했다 (`LLM 설정` 탭, `Flow-i 학습 > 용어사전`).
- Diagnosis 단독 화면은 현재 우선순위가 아니다. 제거하지는 않고 비중만 낮춘다.

## Source Of Truth

- `AGENTS.md` - 에이전트 작업 태도와 일반 실행 원칙
- `TODO.md` - 현재 큐와 Claude/Codex 진행 주체
- `README.md` - 설치, 실행, repo overview
- `docs/README.md` - 문서 지도
- `docs/DEVELOPMENT.md` - scope, validation, data rules, refactor rules
- `docs/features/` - 기능별 책임, 계약, 코드 진입점
- `docs/features/agent-semantic-layer.md` - Agent semantic layer API, data-root, unit별 사용 규칙

`archive/`, runtime logs, generated task specs, moved legacy docs는 historical reference다. 사용자가 명시하지 않으면 현재 구현 가이드로 쓰지 않는다.

## Role And TODO

본 진입점을 사용하는 에이전트는 Claude 세션과 Codex CLI 세션이다. 둘 다 frontend/backend 코드, 문서, TODO, feature 명세, 빌드 산출물, `setup.py` 재생성까지 직접 변경할 수 있다.

- `TODO.md`의 `(Claude)` / `(Codex)` 접두는 권한 표시가 아니라 진행 주체 표시다.
- 의미 있는 변경을 시작하기 전에 `TODO.md`의 `Now`에 현재 작업을 표시한다.
- 다른 세션이 잡고 있는 파일이나 영역을 동시에 건드리지 않는다.

## Operating Loop

작업 시작 시:

1. `git status --short`로 작업트리를 확인한다.
2. `README.md`, `TODO.md`, `docs/README.md`, `docs/DEVELOPMENT.md`를 읽는다.
3. 해당 기능의 `docs/features/<feature>.md`를 읽는다.
4. Agent 관련 작업이면 `docs/features/flowi-agent.md`를 먼저 읽는다.

작업 종료 시:

1. 변경 목적과 성공 기준을 다시 확인한다.
2. 필요한 검증을 실행한다.
3. 완료한 항목은 `TODO.md`의 `Done`으로 옮기고, 남은 일은 `Next`에 구체적으로 적는다.

## Flow Engineering Rules

- 대형 페이지와 라우터를 한 번에 갈아엎지 않는다.
- 한 번의 변경은 한 사용자 흐름, 한 API 계약, 한 component/hook 추출, 한 저장 파일 개선, 한 bug fix로 제한한다.
- Flow-i는 app-action router다. 일반 prompt가 source code나 raw DB file을 직접 mutate하면 안 된다.
- 새 API 호출은 frontend `src/lib/api.js` helper를 우선 사용한다.
- 새 backend I/O는 가능하면 `backend/app_v2/modules/<feature>`의 service/repository로 둔다.
- 권한 체크는 라우터 초입에서 명확히 한다.
- endpoint shape를 이유 없이 깨지 않는다.
- 화면/API ownership이 바뀌면 같은 변경에서 `docs/features/<feature>.md`도 갱신한다.

## Data Rules

- GitHub에는 앱 코드와 문서만 둔다. `data/`, `flow-data/`, `Fab/`, `DB/`, `Base/`, `wafer_maps/`는 Git 추적 대상이 아닌 로컬/사내 운영 데이터 루트다.
- Runtime/user data under `data/flow-data/`, operational DB roots, sessions, uploads, cache, logs는 code/setup 변경으로 덮어쓰거나 Git에 추가하면 안 된다.
- `data/Fab/`은 env가 없을 때 쓰는 local DB fallback이다. 운영에서는 `FLOW_DB_ROOT` 또는 공유 기본 DB를 쓴다.
- 특정 로컬 drive path를 backend/frontend/docs/setup에 hardcode하지 않는다.
- real production raw data, credentials, session token, private export는 Git에 넣지 않는다.

## Version Note

버전 표기는 파일/문서의 수정 시각, 즉 mtime 기준으로 본다.

- 새 docs에 `v9.0.x` 같은 명시 버전을 적지 않는다.
- `VERSION.json.release_notes` history는 역사 기록으로 보존한다.
- 최종 수정 시각 확인은 `git log -1 --format=%ai <file>` 또는 파일 mtime을 사용한다.

## Validation

문서만 수정:

```bash
git diff --check
```

일반 코드 수정:

```bash
git diff --check
cd frontend && npm run build
python3 scripts/smoke_test.py
```

백엔드 테스트:

```bash
python3 -m pytest tests
```

사내 반입/업데이트:

```bash
python3 scripts/preflight_internal.py --write-probe
```

`setup.py` 또는 번들 산출물을 갱신했을 때:

```bash
python3 _build_setup.py
python3 setup.py version
```
