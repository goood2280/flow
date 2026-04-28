# Flow v9.0.4 LLM 연결 및 앱 구조 보고서

- 작성일: 2026-04-29
- 기준 커밋: `58fc2f7`
- 대상 앱: `flow`
- 목적: Flow-i LLM 연결이 어떻게 답변을 만들고 화면에 표시하는지, 답변/메모리의 Markdown 구조, 진행 흐름, 앱 구성, 코드 규모, 기술 스택을 한 문서로 정리한다.

## 1. 요약

`flow`는 반도체 개발/pilot 단계의 DB 탐색, 대시보드, 스플릿 테이블, 이슈추적, 인폼 로그, 회의/액션아이템, 관리자 기능을 FastAPI backend와 React/Vite frontend로 연결한 웹 앱이다.

Flow-i는 홈 화면의 대화형 프롬프트로 동작한다. 사용자는 자연어로 요청하고, 서버는 먼저 권한과 단위기능을 확인한 뒤 가능한 요청은 로컬 deterministic handler로 처리한다. LLM은 결과를 다듬거나 애매한 요청을 확인 질문으로 바꾸는 역할을 하며, DB 값을 임의로 지어내는 구조가 아니다.

현재 LLM 연결은 관리자 설정 기반이다. 사용자는 별도 API key를 입력하지 않고, Admin 화면에서 설정된 provider, endpoint, model, token을 서버가 사용한다. OpenAI API, OpenAI 호환 API, generic API, 사내 playground 방식 모두를 수용하도록 adapter가 분리되어 있다.

## 2. 기술 스택

| 영역 | 사용 기술 | 역할 |
|---|---|---|
| Backend | Python, FastAPI, Uvicorn | API router, 인증/권한, 데이터 조회, 단위기능 실행 |
| Dataframe/DB | Polars, PyArrow, Parquet | 대용량 parquet preview, filter, join, aggregation |
| File/Runtime Store | JSON, JSONL, CSV, YAML | 사용자/권한/설정/활동로그/메모리/캐시 저장 |
| Office export | openpyxl, xlsxwriter, xlrd, pptxgenjs | XLSX/PPTX export 및 호환 |
| Monitoring | psutil | Admin monitor, CPU/memory 상태 |
| Frontend | React 18, Vite 5, JavaScript/JSX | SPA 화면, Flow-i chat UI, dashboard/table rendering |
| Styling | CSS variables, page-local inline styles | dark UI 일관성, page별 조밀한 운영 화면 |
| LLM | provider adapter | OpenAI, OpenAI-compatible, generic, playground endpoint 호출 |
| Packaging | generated `setup.py` | 자체 추출형 배포/업데이트 번들 |

주요 dependency 파일:

- `backend/requirements.txt`: FastAPI, Uvicorn, Polars, PyArrow, psutil, PyYAML, office 관련 패키지
- `frontend/package.json`: React, Vite, `@vitejs/plugin-react`
- root `package.json`: setup bundle 생성/보조 도구

## 3. 앱 구성

### 3.1 Backend 구성

Backend 진입 흐름은 다음과 같다.

1. root `app.py`가 shim 역할을 한다.
2. 실제 FastAPI 앱은 `backend/app.py`에서 구성된다.
3. 기능별 API는 `backend/routers/*`에 분리되어 있다.
4. 공통 경로/설정/데이터 helper는 `backend/core/*`에 위치한다.
5. frontend build 산출물은 FastAPI static route로 제공된다.

주요 backend 관심 파일:

| 파일 | 역할 |
|---|---|
| `backend/app.py` | FastAPI 앱 생성, router mount, static frontend serving |
| `backend/routers/llm.py` | Flow-i 상태, 연결 확인, chat, 단위기능 처리 |
| `backend/core/llm_adapter.py` | provider별 LLM request/response adapter |
| `backend/core/paths.py` | data root, runtime 경로 |
| `backend/core/security.py` | 현재 사용자 및 권한 확인 |
| `backend/routers/admin.py` | Admin settings, LLM 설정 저장 |
| `backend/routers/splittable.py` | SplitTable, FAB lot cache, note, issue link |
| `backend/routers/tracker.py` | issue, comment/reply, ET lot cache, notification |
| `backend/routers/filebrowser.py` | DB/files preview, SQL/filter, sample load |
| `backend/routers/dashboard.py` | chart, snapshot, lazy filter/join projection |

### 3.2 Frontend 구성

Frontend는 React/Vite SPA이다. page registry에서 각 화면을 연결하고, 화면별 JSX 파일이 UI와 API 호출을 담당한다.

주요 frontend 관심 파일:

| 파일 | 역할 |
|---|---|
| `frontend/src/App.jsx` | 앱 shell, navigation, route/page rendering |
| `frontend/src/pageRegistry.js` | page id와 component 연결 |
| `frontend/src/pages/My_Home.jsx` | 홈, Flow-i START, 대화형 prompt, result/table/chart |
| `frontend/src/pages/My_Admin.jsx` | Admin LLM 설정, test, monitor |
| `frontend/src/pages/My_FileBrowser.jsx` | 파일탐색기, sample 기본 로드, SQL 결과 |
| `frontend/src/pages/My_Dashboard.jsx` | chart dashboard, scatter/fitting/filter UI |
| `frontend/src/pages/My_SplitTable.jsx` | SplitTable, FAB cache 수동 scan, related issue |
| `frontend/src/pages/My_Tracker.jsx` | 이슈추적, 댓글/대댓글, 삭제/알림 |

### 3.3 Runtime 데이터

Runtime 데이터는 기본적으로 `data/flow-data` 아래에 저장된다. 이 영역은 사용자/운영 데이터이므로 git source와 분리해서 관리하는 전제이다.

| 경로 | 역할 |
|---|---|
| `data/flow-data/admin_settings.json` | Admin 설정, LLM 설정 포함 |
| `data/flow-data/users.csv` | 사용자 계정/권한 기반 데이터 |
| `data/flow-data/flowi_activity.jsonl` | Flow-i 사용자 요청/피드백 활동 로그 |
| `data/flow-data/flowi_users/{username}.md` | 사용자별 Flow-i 메모리 |
| `data/flow-data/splittable/match_cache/` | FAB root_lot_id/fab_lot_id cache |
| `data/flow-data/tracker/et_lot_cache/` | ET analysis root/fab lot cache |

## 4. LLM 연결 흐름

### 4.1 Admin 설정

Admin의 LLM 탭에서 다음 값이 저장된다.

| 설정 | 의미 |
|---|---|
| `enabled` | Flow-i LLM 사용 여부 |
| `provider` | `openai`, `openai_compatible`, `generic`, `playground` |
| `api_url` | API endpoint 또는 `/v1` base URL |
| `model` | 사용할 모델명 |
| `mode` | generic provider에서만 request body에 포함되는 실행 mode |
| `admin_token` | 서버가 보관하는 credential |
| `auth_mode` | `bearer`, `dep_ticket`, `none` |
| `format` | response format. 현재 OpenAI style response 중심 |
| `timeout_s` | request timeout |

OpenAI/OpenAI-compatible provider에서는 `mode`를 request body에 넣지 않는다. OpenAI API가 `mode` parameter를 받지 않기 때문에 이 처리가 없으면 `Unknown parameter: 'mode'` 오류가 발생한다.

### 4.2 홈 화면 상태 확인

홈 화면 Flow-i는 초기 진입 시 `/api/llm/status`를 호출한다.

응답에는 다음 성격의 정보가 포함된다.

| 필드 | 의미 |
|---|---|
| `active` | LLM 기능 활성화 여부 |
| `model` | 화면 우하단에 표시할 연결 모델명 |
| `provider` | OpenAI, compatible, generic 등 provider |
| `allowed_features` | 현재 사용자가 Flow-i로 실행 가능한 단위기능 |
| `unit_actions` | 서버가 노출하는 단위기능 목록 |

홈 prompt에는 별도 안내 문구를 넣지 않고, 우하단에 연결된 모델명과 남은 context 추정치만 표시한다.

### 4.3 START 연결 확인

사용자가 START를 누르면 frontend가 `/api/llm/flowi/verify`를 호출한다.

연결 확인 기준:

1. 서버가 LLM에 짧은 확인 prompt를 보낸다.
2. LLM 응답에 `확인완료`가 들어 있으면 UI 상태를 `연결`로 바꾼다.
3. 호출 실패 또는 기대 문자열 미포함이면 `연결끊김`으로 표시한다.

이 확인은 단순 설정 존재 여부가 아니라 실제 model endpoint가 응답하는지를 보는 동작이다.

### 4.4 대화 요청

사용자가 Enter로 prompt를 전송하면 frontend는 `/api/llm/flowi/chat`에 다음 내용을 보낸다.

```json
{
  "prompt": "A1003 STI 인폼 작성해줘",
  "product": "",
  "max_rows": 12,
  "context": {
    "type": "home_flowi_chat",
    "limit_chars": 12000,
    "remaining_chars": 10430,
    "messages": [
      {"role": "user", "prompt": "..."},
      {"role": "assistant", "text": "...", "intent": "..."}
    ]
  }
}
```

Frontend는 최근 대화 8개를 context로 보낸다. 우하단의 `ctx` 값은 대화 context 여유량을 사용자가 볼 수 있게 표시하는 추정치이다.

### 4.5 Backend 처리 순서

`backend/routers/llm.py`의 `_run_flowi_chat` 처리 흐름은 다음과 같다.

1. 현재 사용자를 확인한다.
2. 사용자의 Flow-i 단위기능 권한을 계산한다.
3. prompt에서 의도를 추정한다.
4. DB/File 원본 수정 요청이면 일반 user는 차단한다.
5. Admin file operation은 `FLOWI_FILE_OP` 구조화 명령이 있을 때만 처리한다.
6. SplitTable, Inform, DB lookup, chart 등 가능한 단위기능은 로컬 handler에서 처리한다.
7. 결과가 불명확하면 선택지 기반 확인 질문을 만든다.
8. LLM이 켜져 있으면 결과 설명을 보완하거나 답변 문장을 정리한다.
9. 활동 로그와 사용자 메모리를 갱신한다.
10. frontend가 그대로 렌더링할 수 있는 JSON 결과를 반환한다.

중요 원칙:

- DB에서 가져온 값과 table/chart 결과는 로컬 계산 결과를 기준으로 한다.
- LLM은 수치/테이블을 만들어내는 주체가 아니라, 검색/처리된 결과를 설명하는 보조 역할이다.
- 애매하면 바로 실행하지 않고 질문과 선택지를 반환한다.

### 4.6 Adapter request 흐름

`backend/core/llm_adapter.py`는 provider별 차이를 흡수한다.

| 단계 | 내용 |
|---|---|
| 설정 병합 | `admin_settings.json`의 `llm` block을 default와 병합 |
| endpoint 정리 | OpenAI `/v1` base URL이면 chat completions endpoint로 변환 |
| header 구성 | `Authorization: Bearer`, `x-dep-ticket`, 또는 no auth |
| body 구성 | provider별로 model/messages/prompt/mode 구성 |
| HTTP 호출 | timeout 내 POST 호출 |
| 응답 추출 | OpenAI choices, output_text, text, response 등 여러 형태 대응 |
| 반환 | `{ok, text, error}` 형태로 normalize |

## 5. 답변 및 Markdown 구조

### 5.1 API 응답 구조

Flow-i chat 응답은 UI가 그대로 사용할 수 있도록 구조화된다.

```json
{
  "ok": true,
  "active": true,
  "user": "hol",
  "answer": "요청 내용을 처리했습니다.",
  "intent": "inform_create",
  "tool": "inform",
  "llm": {"used": true, "model": "gpt-5-nano", "provider": "openai"},
  "allowed_features": ["inform", "splittable", "tracker"],
  "choices": [],
  "table": null,
  "chart": null
}
```

Frontend의 `FlowiResult`는 `answer`를 `white-space: pre-wrap`로 표시한다. 따라서 서버가 주는 줄바꿈과 간단한 Markdown 형태 문장이 화면에서 자연스럽게 유지된다.

### 5.2 선택지 질문 구조

요청이 불명확하면 바로 실행하지 않고 선택지를 반환한다.

```json
{
  "answer": "제품을 확인해야 합니다. 어떤 제품인가요?",
  "choices": [
    {
      "label": "1",
      "title": "PRODA",
      "recommended": true,
      "description": "최근 입력과 가장 가까운 제품",
      "prompt": "제품은 PRODA야"
    },
    {
      "label": "2",
      "title": "직접 입력",
      "recommended": false,
      "description": "제품명을 직접 알려주세요",
      "prompt": ""
    }
  ]
}
```

UI는 recommended 선택지를 먼저 보여주며, 사용자는 긴 문장을 다시 입력하지 않고 선택지를 눌러 대화를 이어갈 수 있다.

### 5.3 Table 결과 구조

DB 조회, 파일탐색기 SQL 결과, SplitTable/Issue lookup 결과는 table object로 내려온다.

```json
{
  "table": {
    "kind": "db_query",
    "title": "조회 결과",
    "placement": "below",
    "columns": ["root_lot_id", "fab_lot_id", "step_id", "func_step"],
    "rows": [
      {"root_lot_id": "A1003", "fab_lot_id": "F123", "step_id": "STI", "func_step": "PEMS"}
    ],
    "total": 1
  }
}
```

홈 화면은 이 table을 기존 단위기능에서 보는 표와 같은 결로 아래에 렌더링한다. 즉 prompt 답변과 실제 데이터 결과가 분리되지 않고 같은 대화 흐름 안에 붙는다.

### 5.4 Chart 결과 구조

chart 요청은 chart plan 또는 chart data로 반환된다.

예시 요청:

- `Inline 1.0 CD와 ET LKG Corr. 그려줘`
- `1차식 fitting line 그려줘`
- `ML_TABLE 제품 Knob 기준으로 컬러링 해줘`
- `특정 knob은 빼줘`

기본 처리 원칙:

| 데이터 | 기본 집계/매칭 |
|---|---|
| INLINE | `lot_wf(root_lot_id_wafer_id)` 기준 `avg` |
| ET | `lot_wf(root_lot_id_wafer_id)` 기준 `median` |
| Shot 단위 matching | 정확한 shot key가 있으면 shot 기준 연결 |
| ML_TABLE | knob/feature 기준 filter 또는 coloring |

LLM은 차트를 직접 그리는 대신 어떤 DB, column, join, filter, aggregation이 필요한지 확인한다. 실제 plot 데이터는 DB에서 가져온 값을 기준으로 만든다.

### 5.5 사용자 메모리 Markdown

사용자별 메모리는 `data/flow-data/flowi_users/{username}.md`에 저장된다.

기본 구조:

```markdown
# Flow-i user memory: hol

<!-- FLOWI_USER_NOTES_START -->
- 자주 보는 제품: PRODA
- 선호 업무: STI/PEMS inform 작성
<!-- FLOWI_USER_NOTES_END -->

## Activity

### 2026-04-29T10:22:31 - inform_create
- prompt: A1003 STI 인폼 작성해줘
- result: clarification_requested
```

이 파일은 유저별 경향을 쌓기 위한 lightweight memory이다. 현재 구조는 Markdown이라 사람이 읽고 정리하기 쉽고, 앱은 marker 사이의 notes와 activity를 이용해 다음 대화 context에 참고할 수 있다.

### 5.6 활동 로그 JSONL

전체 활동 로그는 `flowi_activity.jsonl`에 한 줄 JSON으로 쌓인다.

예상 필드:

| 필드 | 의미 |
|---|---|
| `ts` | 발생 시각 |
| `user` | 사용자 |
| `prompt` | 입력 prompt |
| `intent` | 추정 의도 |
| `tool` | 실행 단위기능 |
| `ok` | 성공 여부 |
| `feedback` | 사용자 피드백 |

이 로그는 여러 user의 업무 route와 반복 패턴을 나중에 분석하기 위한 기반이다.

## 6. 진행 흐름

Flow-i의 목표는 사용자가 기다리는 느낌을 줄이고, 가능한 요청은 단위기능 결과로 빠르게 보여주는 것이다.

일반 요청 흐름:

1. 사용자 prompt 입력
2. frontend가 최근 context와 함께 `/api/llm/flowi/chat` 호출
3. backend가 사용자/권한/의도 확인
4. cache 또는 parquet/JSON store에서 필요한 데이터 조회
5. 가능한 경우 단위기능 실행
6. 불명확하면 recommended 선택지를 포함한 확인 질문 반환
7. 확정되면 Inform, SplitTable, Tracker 등 단위기능 결과 생성
8. table/chart/answer를 같은 대화 카드 아래에 표시
9. feedback과 활동 로그를 기록

권한 원칙:

| 사용자 유형 | 가능한 작업 |
|---|---|
| 일반 user | 본인에게 허용된 단위기능 실행, 조회, Inform/Tracker 등 업무 기능 |
| 일반 user | DB/File 원본 직접 수정/삭제는 차단 |
| admin | Admin 권한과 구조화된 `FLOWI_FILE_OP` 결과가 있을 때 file 조작 가능 |
| admin | LLM 설정, cache scan 주기, 수동 scan, monitor 설정 가능 |

## 7. LLM과 에이전트의 차이

현재 Flow-i 구조는 단순히 LLM API를 연결한 채팅창이 아니라, 제한된 단위기능을 호출해 업무를 이어가는 `tool-using task agent`에 가깝다. 다만 모든 파일/DB를 자율적으로 탐색하고 임의 행동을 반복하는 완전 자율 에이전트가 아니라, 권한과 기능 목록이 고정된 업무형 에이전트로 보는 것이 정확하다.

| 구분 | LLM | 에이전트 |
|---|---|---|
| 기본 역할 | 입력을 보고 답변 text 생성 | 목표를 이해하고 tool을 호출해 작업 진행 |
| 상태 | 기본적으로 stateless | context, memory, 진행 상태를 함께 관리 |
| 실행 능력 | 직접 DB/File/API를 조작하지 못함 | 허용된 단위기능/API/tool을 호출 가능 |
| 반복 흐름 | 1회 prompt -> 1회 answer 중심 | 질문 -> 실행 -> 결과 관찰 -> 다음 행동 |
| 권한 | 모델 자체에는 앱 권한 개념 없음 | 사용자/admin 권한으로 실행 범위를 제한 |
| 정확도 | 지식과 prompt에 의존 | DB/cache/tool 결과를 근거로 답변 가능 |

Flow-i가 에이전트라고 볼 수 있는 이유:

- 이전 대화 context를 이어 받아 후속 질문을 처리한다.
- 사용자별 Markdown memory와 JSONL activity log를 참고할 수 있다.
- Inform, SplitTable, Tracker, FileBrowser, Dashboard 같은 단위기능을 호출한다.
- 애매한 요청은 선택지 기반으로 되묻고, 답변을 받은 뒤 계속 진행한다.
- 일반 user와 admin의 실행 권한을 다르게 적용한다.
- LLM 결과만 믿지 않고 DB/cache/handler 결과를 화면에 table/chart로 표시한다.

반대로 Flow-i가 아직 완전 자율 에이전트가 아닌 이유:

- 무제한 tool 탐색이나 임의 파일 조작을 허용하지 않는다.
- 사용자가 승인하지 않은 destructive action은 실행하지 않는다.
- long-running multi-step plan을 background에서 계속 자율 실행하는 구조는 아니다.
- 업무 안정성을 위해 단위기능 route 안에서만 실행하도록 제한한다.

### 7.1 Markdown memory와 context의 역할

Markdown memory가 질문을 이어가게 만드는 본질은 아니다. Markdown은 사용자별 메모리를 사람이 읽기 좋은 형태로 저장하기 위한 포맷이다.

질문이 이어지는 실제 구조는 다음과 같다.

1. frontend가 최근 대화 일부를 `context.messages`로 서버에 보낸다.
2. backend가 사용자별 memory/activity에서 필요한 힌트를 읽는다.
3. 현재 prompt, 최근 대화, 사용자 경향, 권한, 가능한 단위기능을 함께 판단한다.
4. LLM을 쓸 경우 이 context를 system/user prompt에 포함해 호출한다.
5. 서버는 결과를 저장하고 다음 요청에서 다시 일부를 넣어준다.

즉 LLM 자체가 이전 요청을 자동으로 기억하는 것이 아니다. 매 요청마다 앱이 필요한 context와 memory를 다시 연결해 주기 때문에 “대화가 이어지는 것처럼” 동작한다.

## 8. 정확도 원칙

Flow-i가 지켜야 하는 원칙은 다음과 같다.

- DB에 없는 값을 만들어내지 않는다.
- 실제 table/chart는 parquet, JSON, cache, 또는 단위기능 handler 결과에서 만든다.
- `root_lot_id`, `fab_lot_id`, `lot_wf`, `step_id`, `func_step` 같은 key는 명시적 매칭 기준을 사용한다.
- INLINE은 기본적으로 wafer key 기준 평균, ET는 중앙값으로 요약한다.
- 정확한 shot matching key가 있으면 shot 단위로 연결한다.
- ambiguous request는 질문과 선택지로 되묻는다.
- 사용자별 memory는 다음 대화의 힌트이지, DB 사실을 대체하지 않는다.

## 9. 현재 반영된 주요 기능

| 영역 | 내용 |
|---|---|
| Flow-i Home | Enter 전송, 대화 context 유지, START 실제 연결 확인, 모델명/ctx 표시 |
| LLM Adapter | OpenAI `mode` 오류 방지, provider별 request body 분리 |
| SplitTable | FAB root/fab lot cache, 수동 scan, 적용 공정 정보 default unchecked, related issue 표시 |
| Tracker | Monitor column 정리, ET lot cache, 댓글/대댓글, 작성자/admin 삭제, 알림 |
| FileBrowser | sample load 버튼 없이 기본 샘플 로드 |
| Dashboard | `n.toFixed is not a function` 방지, lazy filter/join projection |
| Inform | SplitTable knob plan 자동 Inform 생성 방지 |
| Admin | LLM 설정, monitor 보도블럭 갈기, cache 주기/수동 scan 계열 설정 |
| Docs/Setup | README v9.0.4 정리, generated `setup.py` 번들 갱신 |

## 10. 코드 규모

아래 지표는 보고서 작성 직전 기준이며, `data/`, `frontend/dist/`, `node_modules/`, `.git/`, `archive/`는 제외했다. generated `setup.py`는 실제 소스와 성격이 달라 별도 분리했다.

| 구분 | 파일 수 | 줄 수 | 문자 수 |
|---|---:|---:|---:|
| Backend Python | 80 | 43,132 | 1,671,917 |
| Frontend source | 33 | 23,580 | 1,493,633 |
| Tests | 20 | 2,713 | 97,100 |
| Scripts | 18 | 3,977 | 153,142 |
| Docs Markdown | 11 | 2,205 | 56,124 |
| Config/text | 9 | 1,981 | 68,120 |
| Other source | 10 | 292 | 8,518 |
| Primary total | 181 | 77,880 | 3,548,554 |
| Generated setup.py | 1 | 18,174 | 1,448,259 |

확장자별 파일 수:

| 확장자 | 파일 수 |
|---|---:|
| `.py` | 122 |
| `.jsx` | 27 |
| `.md` | 11 |
| `.json` | 7 |
| `.js` | 7 |
| no extension | 3 |
| `.txt` | 2 |
| `.html` | 1 |
| `.svg` | 1 |
| `.css` | 1 |

## 11. 운영 메모

1. LLM은 선택 기능이다. 연결이 끊겨도 로컬 단위기능은 계속 동작해야 한다.
2. OpenAI 테스트 시 provider는 `openai`, endpoint는 `https://api.openai.com/v1`, model은 Admin 화면 기본값 또는 테스트 모델명으로 설정한다.
3. OpenAI provider에서는 `mode`를 보내지 않아야 한다.
4. 사내 API가 OpenAI compatible이면 provider를 `openai_compatible`로 두고 endpoint/model/token만 바꿔 테스트한다.
5. 일반 user가 파일/DB를 직접 수정하는 프롬프트를 보내면 차단하고, 대신 가능한 단위기능 route를 제안한다.
6. Admin file edit은 안전 확인 문구가 포함된 `FLOWI_FILE_OP` 구조로 제한한다.
7. 대용량 INLINE/ET/ML_TABLE 대응은 cache, lazy filter, column projection, aggregation 기준을 먼저 적용하고 LLM은 그 결과를 설명하는 방식으로 유지한다.

## 12. 주요 코드 위치

| 주제 | 위치 |
|---|---|
| Flow-i status/chat/verify API | `backend/routers/llm.py` |
| `_run_flowi_chat` main flow | `backend/routers/llm.py` |
| `FLOWI_FILE_OP` parsing | `backend/routers/llm.py` |
| LLM provider adapter | `backend/core/llm_adapter.py` |
| OpenAI/generic body 분기 | `backend/core/llm_adapter.py` |
| Home Flow-i UI | `frontend/src/pages/My_Home.jsx` |
| Flow-i table/chart rendering | `frontend/src/pages/My_Home.jsx` |
| Admin LLM 설정 UI | `frontend/src/pages/My_Admin.jsx` |
| README 현재 버전 | `README.md` |
| 자체 추출 번들 | `setup.py` |

## 13. 결론

현재 Flow-i는 단순 챗봇이 아니라, 앱의 단위기능을 자연어로 호출하는 얇은 orchestration layer로 설계되어 있다. 핵심 데이터는 DB/cache/handler에서 가져오고, LLM은 연결 확인, 의도 보조, 답변 정리, 애매한 요청의 확인 질문 생성에 사용된다.

이 구조를 유지하면 OpenAI 저가 모델, OpenAI-compatible 사내 모델, playground API를 같은 UI에서 교체 테스트할 수 있다. 또한 user/admin 권한 경계를 유지하면서도 Inform 생성, SplitTable 조회, Tracker issue 확인, DB table 결과 표시, chart plan 같은 업무 흐름을 대화 안에서 이어갈 수 있다.
