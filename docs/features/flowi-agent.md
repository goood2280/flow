# Flow-i Agent

Flow-i Agent는 사용자의 자연어 요청을 앱 기능의 read/action workflow로 연결한다.

## Owns

- feature intent routing
- FileBrowser/SplitTable/Inform/Tracker/Dashboard 등 앱 기능 질의
- RAG/knowledge lookup과 답변 근거 구성
- app 내부 action 후보 제안

## Does Not Own

- 일반 사용자 prompt에서 source code 변경
- raw DB 파일 직접 수정
- 관리자 확인 없는 destructive operation
- feature별 업무 규칙의 단독 판단

## Code Entrypoints

| Layer | Path |
|---|---|
| LLM router | `backend/routers/llm.py` |
| Agent router | `backend/routers/agent.py` |
| Knowledge router | `backend/routers/knowledge.py` |
| Feature prompts | `data/flow-data/flowi_agent_features/` |
| User notes | `data/flow-data/flowi_users/` |
| Entry docs | `data/flow-data/flowi_agent_entrypoints.md` |

## Guardrails

- 불명확한 product, lot, wafer, module은 action 전에 확인한다.
- feature docs의 책임 경계를 우선한다.
- raw DB write, code mutation, admin 설정 변경은 명시적 확인과 권한을 요구한다.
- 답변에는 사용자가 확인할 수 있는 app link나 파일/컬럼 근거를 남긴다.

## Agent Wiki

Agent 탭의 Wiki 운영은 Karpathy LLM Wiki 패턴을 앱 전체 문서 구조가 아니라 에이전트 지식 운영 계층으로 적용한다.

- Raw source는 `data/flow-data/knowledge/raw/sources/` 아래 append-only로 저장하며 원본 DB/Fab 파일은 수정하지 않는다.
- Maintained wiki page는 `data/flow-data/knowledge/wiki/agent_wiki/` 아래 markdown으로 저장한다.
- Wiki page frontmatter의 최소 필드는 `doc_id`, `kind=agent_wiki`, `title`, `summary`, `source_ids`, `updated_at`, `tags`이다.
- Search entrypoint는 `data/flow-data/knowledge/index/wiki_index.json`이며 Agent 탭 검색은 `agent_wiki` kind를 우선한다.
- Chronological 운영 기록은 `data/flow-data/knowledge/index/wiki_log.jsonl`에 append-only로 남긴다.
- Lint는 broken `[[wiki_link]]`, missing source, orphan page, stale summary, contradiction 후보를 점검한다.
- Source 등록, ingest commit, lint는 admin 또는 `diagnosis`/`knowledge` page admin만 수행한다. 읽기와 preview는 로그인 사용자가 수행할 수 있다.

## Verify

```bash
git diff --check
python scripts/smoke_test.py
```
