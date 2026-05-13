# Home And Messages

Home은 로그인 직후 필요한 상태와 최근 변경을 보여주고, Messages는 사용자-admin 문의와 공지를 앱 내부 기록으로 남긴다.

## Owns

- version, contact bell, notice banner
- Flow-i prompt entry와 LLM 연결 상태
- Home 안에서 Flow-i 표/차트/회의 요약/SQL 초안 결과를 인라인 확인하는 원샷 응답
- raw chain-of-thought가 아닌 공개 실행 로그(해석 로그 / 근거 흐름)
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
| Messages router | `backend/routers/messages.py` |
| Notification data | `data/flow-data/notifications/` |
| Messages data | `data/flow-data/messages/` |

## Guardrails

- 공지와 알림은 업무 화면을 가리지 않는다.
- Messages는 운영 설정 변경이 아니라 문의/공지 기록에 집중한다.
- read state와 bell count는 서로 어긋나지 않아야 한다.
- Flow-i 결과는 가능하면 Home card 안에서 answer, table/chart/preview, warnings, evidence trace를 같이 보여주고 화면 이동 버튼은 보조 동작으로 둔다.
- 공개 trace는 입력 해석, 사용한 기능 AI, endpoint/payload 요약, rows/source/warnings/fallback 상태만 보여주며 모델 사고과정 원문은 표시하지 않는다.

## Verify

```bash
git diff --check
cd frontend && npm run build
```
