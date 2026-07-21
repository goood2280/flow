# Home And Messages

Home은 로그인 직후 필요한 상태와 최근 변경을 보여주고, Messages는 사용자-admin 문의와 공지를 앱 내부 기록으로 남긴다.

## Owns

- version, contact bell, notice banner
- Flow-i prompt entry와 LLM 연결 상태
- Home 안에서 Flow-i 표/차트/회의 요약/SQL 초안 결과를 인라인 확인하는 원샷 응답
- raw chain-of-thought가 아닌 공개 실행 로그(해석 로그 / 근거 흐름)
- Home Flow-i few-shot workflow catalog 조회와 admin 편집
- 사용자-admin 1:1 문의
- admin notice, read state, bell 동기화

## Does Not Own

- 복잡한 설정
- 대용량 분석
- feature별 업무 상태의 원본 저장

## Code Entrypoints

| Layer | Path |
|---|---|
| Home page | `frontend/src/pages/My_Home.jsx` |
| App shell | `frontend/src/App.jsx` |
| Shell state | `frontend/src/app/useFlowShell.js` |
| Home router | `backend/routers/home.py` |
| Flow-i chat/workflow API | `backend/routers/llm.py` |
| Flow-i workflow catalog | `backend/core/flowi_workflow_catalog.py`, `backend/core/flowi_workflow_defaults.json` seed + generated variants |
| Messages router | `backend/routers/messages.py` |
| Notification data | `data/flow-data/notifications/` |
| Messages data | `data/flow-data/messages/` |

## Guardrails

- 공지와 알림은 업무 화면을 가리지 않는다.
- Messages는 운영 설정 변경이 아니라 문의/공지 기록에 집중한다.
- read state와 bell count는 서로 어긋나지 않아야 한다.
- Flow-i 결과는 Home card 안에서 answer와 table/chart/preview 같은 실제 결과물을 우선 보여준다. warnings, evidence trace, action log, runtime/debug 정보는 보존하되 기본 접힘 상태의 실행 정보 영역에 둔다.
- 공개 trace는 입력 해석, 사용한 기능 AI, endpoint/payload 요약, rows/source/warnings/fallback 상태만 보여주며 모델 사고과정 원문은 표시하지 않는다.
- Home Flow-i 응답은 `answer`와 별도로 공개 `action_log`를 내려준다. `action_log.summary`는 사용자용 사고과정 요약, `action_log.timeline`은 `semantic_layer -> task_planner -> unit_agents -> conclusion` 단계별 실행 로그, `action_log.final_answer`는 `answer`와 같은 최종답변이다.
- `/api/llm/flowi/workflows` catalog는 few-shot 예시와 prompt cache에 쓰는 runtime workflow 목록이다. 각 workflow는 예시 prompt와 함께 derived `question_template`, slot 목록, source role, public `orchestration` 단계를 제공해 Flow-i planner와 Agent 화면이 같은 template/runbook을 참고한다. GET은 로그인 사용자에게 열고, draft/save/merge-defaults는 admin만 수행한다.
- LLM polish는 raw reasoning을 요청하지 않고 `[생각요약]`과 `[최종답변]` 공개 형식만 파싱한다. deterministic 결과도 기존 public trace에서 `action_log`를 생성한다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
