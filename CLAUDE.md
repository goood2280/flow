# CLAUDE.md — flow

Claude Code 세션이 시작될 때 자동으로 읽는 파일이다. **이 파일 하나가 에이전트 진입점이다.** 별도의 `AGENTS.md`나 `TODO.md`는 두지 않는다.

## 이 앱이 무엇인가

`flow`는 반도체 개발·pilot 단계의 공정 데이터, 실험 plan, 이슈, 인폼, 회의를 **lot/wafer 축으로 이어 보는 사내 업무 플랫폼**이다. FastAPI + React, 단일 저장소.

목표는 기능을 많이 모으는 게 아니라 *"무슨 일이 있었고 지금 뭘 해야 하는지"*를 빠르게 판단하게 하는 것이다. 판단 기준은 [docs/PRODUCT_PHILOSOPHY.md](docs/PRODUCT_PHILOSOPHY.md)를 따른다. 요약하면:

- 모든 주요 화면은 `product`, `root_lot_id`, `lot_id`, `wafer_id`로 이어져야 한다.
- 차트만 보여주지 않는다. 누가 어떤 판단을 했고 어떤 후속 조치가 생겼는지 남긴다.
- 환경마다 경로·컬럼·포맷이 다르다. 내부는 canonical 이름으로 두고 차이는 adapter/profile이 흡수한다.
- AI는 보조 수단이다. 매핑·편집·분석·공유에는 항상 수동 fallback이 있어야 한다.
- 설명이 긴 화면보다, 반복 업무를 빠르게 처리하는 밀도 있는 화면이 맞다.

## 현재 미션 (2026-07)

1. **SplitTable 성능·캐시 운영** — 검색 지연 단축, RAM 캐시 예산과 축출, 운영/개발 2서버 워커 분산, OOM 방어. 최근 릴리스(v9.5.3x) 대부분이 여기에 있다.
2. **사내 반입·안정성** — soft landing, preflight, `_build_setup.py` → `setup.py` 배포 파이프라인, 읽기전용/공유 root 환경에서의 견고성.

> `docs/AGENT_FLOW_CONTEXT.md`에 남아 있는 "Agent 탭 LangGraph 재설계" 미션은 낡았다. Agent/semantic layer는 유지보수 대상이지 현재 우선순위가 아니다.

## 실행

`.claude/launch.json`에 등록되어 있어 preview 도구로 바로 띄울 수 있다 (`flow-backend` 8080, `flow-frontend` 5173).

```bash
cd frontend && npm install && npm run build
```

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8080
```

기본 관리자 계정은 `hol`이며 `FLOW_ADMIN_PW`가 없으면 로컬 기본값으로 시드된다 ([backend/app_v2/runtime/startup.py](backend/app_v2/runtime/startup.py)).

## 코드 지도

| 작업 | 먼저 볼 곳 |
|---|---|
| 탭/페이지 추가 | `frontend/src/config.js`, `frontend/src/app/pageRegistry.jsx` |
| 페이지 로직 | `frontend/src/pages/My_*.jsx` |
| API | `backend/routers/<feature>.py` |
| 업무 규칙·저장 | `backend/app_v2/modules/<feature>/` (domain/repository/service) 또는 기존 `backend/core/` |
| 기동/백그라운드 스케줄러 wiring | `backend/app_v2/runtime/startup.py` |
| root/path 문제 | `backend/core/roots.py`, `backend/core/paths.py` |
| 서버 역할(운영/개발 워커) | `backend/core/worker_dispatch.py` |
| 캐시 예산·설정 | `backend/core/cache_settings.py` |
| 배포 번들 | `_build_setup.py` → `setup.py` (생성물) |

프런트 API 호출은 `frontend/src/lib/api.js` helper를 우선 쓴다.

## 절대 규칙

**데이터**
- `data/`, `flow-data/`, `Fab/`, `DB/`, `Base/`, `wafer_maps/`는 **운영 데이터 루트다.** 코드나 setup 변경으로 덮어쓰지 않는다.
- 로컬 drive 경로를 backend/frontend/docs/setup에 하드코딩하지 않는다. 항상 `FLOW_DATA_ROOT` / `FLOW_DB_ROOT` / `FLOW_WAFER_MAP_ROOT`를 거친다.
- 실데이터, 계정, 세션 토큰, private export를 Git에 넣지 않는다.

**변경 크기**
- 한 번의 변경 = 한 사용자 흐름, 한 API 계약, 한 컴포넌트/훅 추출, 한 저장 파일 개선, 한 버그 수정. 그 이상 섞지 않는다.
- 대형 페이지와 라우터를 한 번에 갈아엎지 않는다.
- 이유 없이 endpoint 응답 shape를 깨지 않는다.
- 권한 체크는 라우터 초입에서 명확히 한다.

**대형 파일 — 통째로 읽지 말 것**

아래는 grep으로 해당 함수/엔드포인트만 찾아 들어간다. 전체를 읽으면 컨텍스트만 태운다.

| 파일 | 줄 수 |
|---|---:|
| `setup.py` | 43,726 (생성물 — 직접 편집 금지) |
| `backend/routers/llm.py` | 24,906 |
| `backend/routers/splittable.py` | 15,346 |
| `backend/routers/filebrowser.py` | 11,730 |
| `backend/routers/informs.py` | 5,828 |
| `frontend/src/pages/My_Inform.jsx` | 5,075 |

## 검증

문서만 고쳤을 때:

```bash
git diff --check
```

코드를 고쳤을 때:

```bash
cd frontend && npm run build
```

```bash
python scripts/smoke_test.py
```

```bash
python -m pytest tests
```

사내 반입/업데이트 전:

```bash
python scripts/preflight_internal.py --write-probe
```

> **테스트 기준선: 1,022 통과 / 45 실패** (2026-07-26). 45개는 v9.4.x 시점 기대값을 그대로 확인하는 알려진 실패이며 목록과 이유는 [tests/README.md](tests/README.md)에 있다. **이 숫자가 늘어나면 새 회귀다.** 전체 실행은 7~8분 걸린다.

## 배포

GitHub `origin/main`이 배포 기준이며 **full source**를 담는다. `agent/portable-flow-setup`은 installer-only 사이드 브랜치다.

푸시 전에는 변경이 작아도 항상 번들을 재생성한다:

```bash
python _build_setup.py
```

```bash
python setup.py version
```

`VERSION.json`의 `version`을 올리고 `release_notes`에 항목을 추가한다. 릴리스 노트는 이 저장소에서 가장 신뢰할 수 있는 변경 이력이다 — 무엇이 왜 바뀌었는지 알아야 할 때 여기부터 본다. 상세 절차는 [docs/GITHUB_MAIN_PUSH.md](docs/GITHUB_MAIN_PUSH.md).

## 문서

| 문서 | 언제 |
|---|---|
| [docs/README.md](docs/README.md) | 전체 문서 지도 |
| [docs/features/](docs/features/) | 화면/기능별 책임과 진입점 — 기능 작업 전 필수 |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 수정 기준, 진입점 표, 리팩터 규칙 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | backend/frontend/data 책임 경계 |
| [docs/SPLITTABLE_SCALING.md](docs/SPLITTABLE_SCALING.md) | SplitTable 캐시 계층과 성능 |
| [docs/WORKER_DISPATCH.md](docs/WORKER_DISPATCH.md) | 운영/개발 2서버 분산 |
| [docs/SOFT_LANDING_INTERNAL.md](docs/SOFT_LANDING_INTERNAL.md) | 사내 반입, root 보존, preflight |
| [docs/permission_matrix.md](docs/permission_matrix.md) | 탭·기능별 권한 |

화면/API ownership이 바뀌면 **같은 변경에서** `docs/features/<feature>.md`도 고친다.

`archive/`, runtime 로그, 생성된 task spec은 historical reference다. 사용자가 명시하지 않으면 현재 구현 가이드로 쓰지 않는다.

## 알려진 함정

작업 전에 알고 있어야 실수하지 않는 것들:

- **`git status`가 항상 dirty하다.** `__pycache__`(1,293개), `frontend/node_modules`(12,001개), `data/flow-data`(6,450개)가 `.gitignore`에 있음에도 이미 추적 중이라 무효 상태다. `git add -A`는 운영 데이터와 캐시를 함께 커밋한다 — 경로를 지정해 스테이징한다.
- **`bb0737b5`("Reduce repo to README.md and setup.py only", 2026-07-20)가 지운 파일이 더 있을 수 있다.** 소스는 복구됐지만 `tests/` 92개, `backend/scheduler.py`, `.gitattributes`, `AGENTS.md`, `TODO.md`, `frontend/package-lock.json`은 복구가 누락됐었다(2026-07-26에 앞의 셋을 되살림). 뭔가 "원래 있었는데 없다" 싶으면 `git show bb0737b5^:<path>`로 확인한다.
- **`backend/app_v2/runtime/startup.py`의 스케줄러 기동 실패는 warning 로그로만 삼켜진다.** 모듈이 통째로 없어도 앱은 정상 부팅하고 그 기능만 조용히 죽는다 — `product dedup scheduler`가 그렇게 6일간 멈춰 있었다. 스케줄러를 건드리면 로그에서 `init failed`를 확인한다.
- **README의 "두 파일만 배포합니다"는 `origin/main` 얘기가 아니다.** installer-only 브랜치를 설명한 문장이다.
- **개발 PC(Windows)에서는 `python`을 쓴다.** `python3`는 Windows 앱 실행 별칭 스텁이라 동작하지 않는다. 문서 일부(`docs/APP_MAINTENANCE_REPORT.md` 등)의 `python3` 명령은 Linux 사내 서버 기준이다.
- **버전 표기는 mtime 기준으로 본다.** 새 문서에 `v9.x` 같은 고정 버전을 적지 않는다. 최종 수정은 `git log -1 --format=%ai <file>`로 확인한다.
- 서버 역할은 startup 1회 판정이다. 관리자 탭에서 역할을 바꿔도 재시작 전에는 완전히 적용되지 않는다.
