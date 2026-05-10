# Agent Guide for Flow

Flow 작업의 공식 에이전트 진입점이다. 현재 기준은 이 파일과 `TODO.md`다.

## Mission

현재 미션은 **Agent 탭이 Inform Log / SplitTable / FileBrowser 를 driver로 호출해 매끄럽게 동작하도록 만드는 것**이다.

- Agent는 사용자의 자연어 prompt를 받아 위 세 기능의 unit action으로 라우팅한다.
- prompt → orchestrator → feature subagent → unit_action → API/handler → result 흐름이 한 페이지에서 모두 보여야 한다.
- Diagnosis 단독 화면은 현재 우선순위가 아니다 (제거하지는 않고 비중만 축소).

## Role Split

본 진입점을 사용하는 에이전트는 두 종류다. 작업이 어느 쪽에 속하는지 먼저 확인한다.

- **Claude 세션 (이 가이드)** — 진입점/문서/TODO/평가 보고서/Agent ↔ feature 명세 유지. 코드 자체 변경은 하지 않는다.
- **Codex CLI 세션** — 실제 frontend/backend 코드 변경 (Agent 페이지 UX 보강, backend trace 보강, feature handler 견고화 등). 본 가이드와 feature md를 spec으로 사용한다.

`TODO.md`의 `Now` 항목은 `(Claude)` / `(Codex)` 접두로 책임을 명시한다.

## Version Note

버전 표기는 **파일/문서의 수정 시각(mtime)** 기준으로 본다.

- 새 docs 작성 시 `v9.0.x` 같은 명시 버전을 적지 않는다.
- `VERSION.json`의 `release_notes` history는 역사 기록으로 보존한다.
- 최종 수정 시각 확인은 `git log -1 --format=%ai <file>` 또는 파일 mtime을 사용.

## Start Here

1. Read `README.md` for setup, run commands, and repo overview.
2. Read `TODO.md` and update the checklist for the current work (Claude vs Codex 분리 확인).
3. Read `docs/REVIEW.md` (있을 때) for the latest page evaluation snapshot.
4. Read `docs/README.md` for the current documentation map.
5. Read the matching feature doc under `docs/features/` before editing that feature. Agent ↔ feature 작업이면 `docs/features/flowi-agent.md`를 먼저 본다.
6. Use `docs/DEVELOPMENT.md` for scope, validation, data rules, and refactor rules.

## Working Rules

- Run `git status --short` before edits and preserve unrelated user changes.
- Keep `TODO.md` current: put active work in `Now` (with `(Claude)` 또는 `(Codex)` 표기), follow-ups in `Next`, completed work in `Done`.
- Treat `docs/features/` plus `docs/DEVELOPMENT.md` as the current implementation guide.
- Treat `archive/`, runtime logs, generated task specs, and moved legacy docs as historical unless the user explicitly asks.
- Keep Flow-i as an app-action router. It may query and guide app workflows, but normal user prompts must not mutate source code or raw DB files.
- Runtime/user data under `data/flow-data/`, operational DB roots, sessions, uploads, cache, and logs must not be overwritten by code or setup changes.
- Feature 코드 수정은 Codex CLI 세션에서 진행한다. Claude 세션은 `docs/`, `AGENTS.md`, `TODO.md`, `README.md`, `VERSION.json` 등 진입점/문서/메타만 직접 편집한다.
- 새 docs에 `v9.0.x` 같은 명시 버전을 적지 않는다 (위 Version Note 참조).

## Validation

Doc-only changes (Claude 세션이 주로 사용):

```bash
git diff --check
```

General code changes (Codex 세션):

```bash
git diff --check
cd frontend && npm run build
python3 scripts/smoke_test.py
```

Backend tests:

```bash
python3 -m pytest tests
```

Internal deployment/preflight:

```bash
python3 scripts/preflight_internal.py --write-probe
```
