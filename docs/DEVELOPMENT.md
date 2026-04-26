# Development

이 문서는 사람이든 AI든 `flow`를 수정할 때 지켜야 할 기준이다.

## 작업 전

수정 전에 아래를 먼저 정한다.

- 바꾸려는 사용자 흐름
- 읽고 쓰는 데이터 파일
- 영향받는 API endpoint
- 필요한 권한
- 실패 시 UI에 보여줄 진단
- 검증할 smoke/test 항목

이 답이 없으면 UI부터 붙이지 않는다.

## 작업 크기

한 번의 변경은 아래 중 하나로 제한한다.

- API 호출 분리
- service/repository 도입
- 모달 또는 패널 컴포넌트 추출
- 한 저장 파일의 atomic write 전환
- 한 smoke/pytest 케이스 추가
- 작은 버그 수정

피해야 할 변경:

- 기능 추가와 대규모 리팩터링을 같은 변경에 섞기
- `App.jsx`와 여러 대형 페이지를 동시에 수정하기
- 라우팅, 권한, 저장 구조, 디자인을 한 번에 바꾸기

## Backend Rules

- 앱 기동/운영 wiring은 `backend/app_v2/runtime`에 둔다.
- 새 I/O는 라우터에 넣지 않는다.
- `load_json`, `save_json`, `open`, `pl.scan_*`를 새 라우터 코드에 직접 추가하지 않는다.
- `backend/app_v2/modules/<feature>`에 `domain`, `repository`, `service`를 만든다.
- 라우터는 service를 호출하고 HTTP shape만 맞춘다.
- 공통 저장은 `app_v2.shared.json_store.JsonFileStore`를 우선 사용한다.
- 권한 체크는 라우터 초입에서 명확히 한다.

## Frontend Rules

- 전역 shell 상태(auth/theme/tab/notification)는 `src/app/useFlowShell.js`에서 관리한다.
- 페이지 등록은 `src/app/pageRegistry.jsx`에만 추가한다.
- 새 API 호출은 `fetch()` 대신 `src/lib/api.js`의 `sf`, `postJson`, `dl`을 사용한다.
- 페이지 파일이 커지면 새 기능 추가 전에 component/hook을 추출한다.
- 기능 전용 UI는 `features/<feature>/components`에 둔다.
- 공통 UI만 `shared` 또는 `components/UXKit.jsx`로 올린다.
- 페이지는 데이터 로드, 모달 내부 상태, 큰 table renderer를 모두 들고 있으면 안 된다.

## AI Editing Rules

- AI에게는 먼저 이 5개 문서만 읽게 한다.
- 과거 archive 문서는 히스토리 확인이 필요할 때만 읽는다.
- 요청은 "어느 feature, 어느 파일 범위, 어떤 검증"까지 좁힌다.
- Claude/Codex handoff loop, inbox/outbox, daemon 방식은 사용하지 않는다.
- AI가 새 문서를 만들기 전에 기존 5개 문서에 흡수 가능한지 확인한다.

## Test And Verify

기본 확인:

```bash
python scripts/smoke_test.py
cd frontend && npm run build
```

백엔드 단위 테스트가 필요한 경우:

```bash
python -m pytest tests
```

서버 실행:

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

## Next Refactor Targets

1. `Inform`
   - product contacts API/hook
   - mail preview/send modal
   - SplitTable embed builder

2. `SplitTable`
   - notes repository/service
   - rulebook repository/service
   - product scan adapter

3. `Dashboard`
   - direct fetch 제거
   - chart config repository
   - snapshot scheduler service

4. `Admin`
   - QA panel, backup panel, activity panel, category manager 분리

## Definition Of Done

- 기존 API 응답 shape가 유지된다.
- 권한 경계가 유지된다.
- 실패 케이스가 UI 또는 API detail로 설명된다.
- smoke/build/test 중 필요한 검증을 실행했다.
- 새 구조가 문서 5개 중 관련 문서에 반영됐다.
