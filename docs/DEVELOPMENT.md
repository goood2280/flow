# Development

이 문서는 사람이든 AI든 `flow`를 수정할 때 지켜야 할 기준이다. 목표는 큰 화면/큰 라우터를 한 번에 갈아엎지 않고, 사용자 흐름 단위로 작게 고치고 검증하는 것이다.

## 수정 전 정리

작업을 시작하기 전에 아래를 먼저 적는다.

- 대상 화면 또는 API
- 바꾸려는 사용자 흐름
- 읽는 데이터 파일과 쓰는 데이터 파일
- 영향받는 API endpoint
- 필요한 권한/admin gate/group visibility
- 실패 시 UI/API에 보여줄 진단
- 실행할 검증 명령

이 답이 없으면 UI부터 붙이지 않는다.

## 진입점

| 작업 | 먼저 볼 파일 |
|---|---|
| 앱 실행/배포 | `README.md`, `docs/SOFT_LANDING_INTERNAL.md` |
| 탭/페이지 추가 | `frontend/src/config.js`, `frontend/src/app/pageRegistry.jsx` |
| 페이지 로직 수정 | `frontend/src/pages/My_*.jsx`, 필요한 경우 local subfolder |
| API 수정 | `backend/routers/<feature>.py` |
| 저장/업무 규칙 수정 | `backend/app_v2/modules/<feature>` 또는 기존 `backend/core` |
| root/path 문제 | `backend/core/roots.py`, `backend/core/paths.py`, `/runtime-roots.json` |
| smoke/preflight | `scripts/smoke_test.py`, `scripts/preflight_internal.py` |

## 작업 크기

한 번의 변경은 아래 중 하나로 제한한다.

- API 호출 분리
- service/repository 도입
- 모달 또는 패널 컴포넌트 추출
- 한 저장 파일의 atomic write 전환
- 한 smoke/pytest 케이스 추가
- 작은 버그 수정
- 문서와 코드의 불일치 정리

피해야 할 변경:

- 기능 추가와 대규모 리팩터링을 같은 변경에 섞기
- `App.jsx`와 여러 대형 페이지를 동시에 수정하기
- 라우팅, 권한, 저장 구조, 디자인을 한 번에 바꾸기
- 기존 API 응답 shape를 이유 없이 깨기

## Backend Rules

- 앱 기동/운영 wiring은 `backend/app_v2/runtime`에 둔다.
- 새 I/O를 라우터에 직접 많이 넣지 않는다.
- `load_json`, `save_json`, `open`, `pl.scan_*`를 새 라우터 코드에 바로 추가하지 않는다.
- 새 업무 로직은 가능하면 `backend/app_v2/modules/<feature>`에 `domain`, `repository`, `service`로 둔다.
- 라우터는 service를 호출하고 HTTP shape만 맞춘다.
- 공통 JSON 저장은 `app_v2.shared.json_store.JsonFileStore`를 우선 사용한다.
- 권한 체크는 라우터 초입에서 명확히 한다.
- `core/roots.py` resolver를 우회해서 특정 DB 경로를 하드코딩하지 않는다.
- `base_root`는 별도 root가 아니라 `db_root` alias임을 유지한다.

## Frontend Rules

- 전역 shell 상태(auth/theme/tab/notification/session)는 `src/app/useFlowShell.js`에서 관리한다.
- 페이지 등록은 `src/config.js`와 `src/app/pageRegistry.jsx`를 함께 확인한다.
- 새 API 호출은 `fetch()` 직접 호출보다 `src/lib/api.js`의 helper를 우선한다.
- 페이지 파일이 커지면 새 기능 추가 전에 component/hook/helper를 추출한다.
- 기능 전용 UI는 먼저 페이지 주변 local folder에 빼고, 여러 화면이 쓰면 shared/component로 올린다.
- 공통 UI만 `components/UXKit.jsx` 또는 `components/*`로 올린다.
- 페이지는 데이터 로드, 모달 내부 상태, 큰 table renderer를 모두 오래 들고 있으면 안 된다.

## Data Rules

- `data/Fab/`은 로컬 DB root seed다. 운영에서는 `FLOW_DB_ROOT` 또는 공유 기본 DB를 쓴다.
- `data/flow-data/`는 runtime/user state다. 코드 업데이트나 build가 덮어쓰면 안 된다.
- DuckDB는 parquet/csv 원본 위의 in-memory read-only query engine으로만 사용한다. 원본 DB 파일을 수정하거나 DuckDB database 파일로 변환하지 않는다.
- tracker, informs, meetings, calendar, messages, sessions, backups는 runtime 변동 파일이다.
- real production raw data, credentials, session token, private export는 Git에 넣지 않는다.
- 사내 반입 전후에는 `scripts/preflight_internal.py --write-probe`로 root와 data_root 보존을 확인한다.

## 문서 반영

기능 변경 후 문서가 필요한 경우:

- 실행/진입 경로가 바뀌면 `README.md`와 `docs/README.md`를 수정한다.
- 책임 경계나 폴더 구조가 바뀌면 `docs/ARCHITECTURE.md`를 수정한다.
- 개발자가 따라야 할 절차가 바뀌면 이 문서를 수정한다.
- 화면별 목표나 추가 기준이 바뀌면 `docs/FEATURE_GOALS.md`를 수정한다.
- 사내 포트/root/백업/preflight가 바뀌면 `docs/SOFT_LANDING_INTERNAL.md`를 수정한다.
- 긴 changelog는 `VERSION.json`에 두고 README에는 최신 요약만 둔다.

## Test And Verify

문서만 수정:

```bash
git diff --check
```

프론트엔드 영향:

```bash
cd frontend && npm run build
```

서버가 떠 있는 상태의 핵심 smoke:

```bash
python scripts/smoke_test.py
```

백엔드 단위 테스트:

```bash
python -m pytest tests
```

사내 반입/업데이트:

```bash
python3 scripts/preflight_internal.py --write-probe
```

서버 실행:

```bash
uvicorn app:app --host 0.0.0.0 --port 8080
```

## Next Refactor Targets

1. `Inform`: product contacts API/hook, mail preview/send modal, SplitTable embed builder
2. `SplitTable`: notes repository/service, rulebook repository/service, product scan adapter
3. `Dashboard`: direct fetch 제거, chart config repository, snapshot scheduler service
4. `Meeting`: mail/calendar push 분리
5. `Admin`: QA panel, backup panel, activity panel, category manager 분리

## Definition Of Done

- 사용자 흐름이 요청과 맞다.
- 기존 API 응답 shape와 권한 경계가 유지된다.
- 실패 케이스가 UI 또는 API detail로 설명된다.
- 필요한 smoke/build/test/preflight를 실행했다.
- 새 구조나 절차가 관련 문서에 반영됐다.
