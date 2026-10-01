# Flow 동작 구조 (ARCHITECTURE)

Flow 가 **어떻게 돌아가는지**(프로세스·요청·캐시·작업·에이전트)를 설명한다. 코드를 고치거나 개선 계획을 세우기 전에
이 문서로 전체 그림을 잡는다.

| 문서 | 역할 |
|---|---|
| `AGENTS.md` | 규칙·금지사항의 정본(진입점) |
| `README.md` | 설치·운영 절차, 환경변수 |
| **이 문서** | 동작 원리와 지켜야 할 불변식 |
| `docs/CODEMAP.md` | 탭·기능 ↔ 파일 위치, 수정 레시피 |
| `docs/PLAN.md` | 개선 계획(무엇을 어떤 순서로, 사내 조건에 따라 어떻게 조절할지) |

숫자는 코드 기본값이다. 실제 값은 현장 환경변수·⚙ 설정·감지된 하드웨어로 달라진다. 문서와 코드가 다르면 코드가 맞다.

## 1. 한 장 요약

```
scripts/flow_server.py (감시기: 재시작·/health 멈춤 감지·메모리 90% 재시작)
  └─ python -m uvicorn app:app   ← API 프로세스는 1개 (RAM 캐시를 복제하지 않기 위해)
       ├─ 미들웨어: 인증 → 자원 가드(레인·503) → 진행 요청 추적 → gzip(작업 스레드에서 압축)
       ├─ backend/routers/*  (router_loader 가 dict 응답을 작업 스레드에서 JSON bytes 로 감쌈)
       ├─ backend/core/*     계산·캐시·저장
       ├─ 백그라운드 스레드: 워치독, 예열, 스케줄러(소유자 1곳만), 스캔 게이트 워커
       └─ 자식 프로세스(spawn): ET 계산, KNOB 예열, reformatize 다운로드, Auto report 생성
            └─ 결과는 파일로 넘긴다(Arrow IPC·디스크 캐시). 부모 GIL 을 잡지 않는다.

FLOW_DB_ROOT (= base_root)   원천 parquet/csv(읽기 전용) + 파생 캐시(cache/ 아래)
FLOW_DATA_ROOT               사용자 기록·설정·로그·대화(sqlite)
사내 LLM (선택)               core/llm_adapter.py — 없어도 규칙만으로 동작해야 한다
```

## 2. 실행 환경과 자원 예산

- 운영: Windows VM 1대, 8 논리 코어·128GB(현장 기준). 개발 worker 로 넘기던 구조는 2026-09-30 폐지 — 모든 작업이 이 1대에서 돈다.
- `core/runtime_limits.py` 가 호스트를 보고 프로파일을 고른다: 64GB·8코어 이상이면 `large`.
  large 기본: Polars·DuckDB 스레드 = 코어 수, 프로세스 상한 ≈ 총량×0.78, 캐시 풀 = 총량×0.6×0.8(128GB 면 약 61GB).
  이전 CPU 가드·소형 프로파일·활성 캐시 제한은 기동 로그와 `scripts/check_flow_performance.py`에서 진단하며 자동 삭제하지 않는다.
- **GIL**: 파이썬 코드는 프로세스당 사실상 1코어다. 여러 코어를 쓰는 것은 Polars/DuckDB 내부 계산뿐이다.
  행 단위 파이썬 루프는 전체 서버를 느리게 한다 → 선택·집계는 Polars 로, 파이썬 객체는 줄어든 결과에만 만든다.
  오래 도는 파이썬 계산은 자식 프로세스로 보내고 결과는 파일로 받는다(`core/splittable_prewarm_process.py` 패턴).
- Windows 특성: 읽는 쪽이 파일을 잡고 있으면 `os.replace` 가 실패한다 → `core/file_transaction.replace_file()`.
  메모리는 working set 이 아니라 private bytes(커밋)로 잰다. 커밋 한도(RAM+pagefile)는 기동 로그에 나온다.

## 3. 요청 경로

1. `AuthMiddleware` — `X-Session-Token` 검증. 권한은 `users.csv` 역할·탭과 `core/auth.py`.
2. `ResourceGuardMiddleware`(`app_v2/runtime/resource_guard.py`)
   - **essential** 경로(SplitTable 조회·후보, 파일 보기)는 메모리/CPU 가드로 거절하지 않는다. `/view` 는 라우터가 cold 계산 구간에서만 자체 세마포어를 잡는다.
   - **heavy** 경로는 동시성 레인 + 메모리/CPU 가드. 막히면 503 과 함께 워치독 긴급 축출을 요청한다.
3. 라우터 → core. 사용자 요청 시각은 `core/request_priority.py` 에 남아, 백그라운드 작업이 사용자에게 양보하는 기준이 된다.
4. 응답: dict 는 작업 스레드에서 orjson 으로 직렬화(`core/json_fast.py`), gzip 도 작업 스레드. 이벤트 루프를 막지 않는 것이 원칙이다.

## 4. 캐시 계층 (SplitTable 중심)

조회는 위에서부터 찾고, 없으면 **원천을 동기 스캔하지 않고** 빌드를 큐에 넣은 뒤 즉시 응답한다(준비 중 표시).

| 계층 | 형태 | 위치 |
|---|---|---|
| 조회 결과(view payload) RAM | 최근 15%는 dict, 나머지는 orjson bytes | `70_pivot_fab_and_view.part.py` `_VIEW_CACHE` |
| 조회 결과 디스크 | zlib 압축 JSON(재시작 뒤 복원, 예열 자식이 채움) | `db_root/cache/split_table_view_payload/` |
| root/제품 RAM | Polars DataFrame | `ml_table_lookup`, splittable product RAM |
| 파생 캐시 4종 | lookup(root 파티션), pivot(root 1개씩), WIP latest-lot, FAB latest 인덱스 | `db_root/cache/…` |

- 신선도: 캐시 항목마다 hard 서명(입력 파일·사용자 편집)과 soft 서명(파생 캐시). hard 가 다르면 버리고, soft 만 다르면 옛 결과를 주고 백그라운드에서 다시 계산한다.
- 무효화: 제품 단위(`_clear_split_view_cache_product`). 다시 만든 캐시가 실제로 바뀌지 않았으면 무효화하지 않는다(`cache_builder.last_build_changes()`).
- 예산: `core/cache_budget.py` 가 캐시 풀을 캐시별 지분으로 나눈다. 넘치면 LRU 축출.

## 5. 무거운 작업과 스케줄러

- 모든 무거운 작업은 `core/heavy_jobs.run_heavy(kind, fn, …)`: 메모리 admission → (필요 시) 사용자 조용함 대기 → 슬롯 → 실행 → 메모리 반환.
- 슬롯(`core/scan_gate.py`):
  - **공용 슬롯 1칸** — 백그라운드 캐시 빌드·스캔(FAB 매칭 검사, 예약·수동 스캔 등). 대기열은 FIFO, 협조적 취소.
  - **조회 레인**(large, 기본 2칸) — 조회 전제 캐시(lookup·pivot·FAB 인덱스·WIP). 백그라운드 스캔 뒤에 줄 서지 않는다.
  - **Auto report 레인**(large, 1칸) — PPT 생성 하위 프로세스(최대 6시간)가 캐시 슬롯을 잡지 않는다.
- 기동: `app_v2/runtime/startup.py`. 프로세스 로컬 서비스(워치독·예열)와, 공유 데이터를 쓰는 스케줄러(소유자 선출 `core/background_owner.py` 가 lease 를 쥔 프로세스만)로 나뉜다.
- 스케줄러는 20개 안팎이 각자 타이머로 돈다(FAB 매칭, 자동 제품 캐싱, S0 스냅샷, 백업, 운영 점검 …). 같은 원천을 여러 곳이 따로 본다 — 통합은 `docs/PLAN.md` P2.

## 6. 메모리 보호

- `core/memory_watchdog.py`: 15초마다 프로세스 메모리 %. warn → 로그, critical → 축출 비용이 낮은 캐시부터 safe 까지 축출.
- `core/gc_tuning.py`: 캐시가 수십 GB 의 파이썬 객체라 전체 GC 가 수 초 정지를 만든다. 평소에는 젊은 세대 수집 + `gc.freeze()` 만,
  전체 수집은 사용자가 없을 때나 위기 때만. 주기·작업 경로에서 `gc.collect()` 를 직접 부르지 않는다(`core/memory_trim.trim()` 사용).
- DuckDB 는 연결마다 `memory_limit`·`temp_directory`(`core/duckdb_engine.configure_connection`). 기본은 RAM 80%/연결이라 반드시 적용.

## 7. 홈 에이전트(데이터 챗) 흐름

```
POST /api/home-agent/orchestrate  (routers/data_chat.py)
  ├─ 사용자별 질문 한도(core/flowi_quota.py; 되묻기 답은 무료)
  ├─ 대화 상태(core/chat_conversations.py, sqlite) → context 정리, 본인 피드백·스킬 주입
  └─ core/flowi_turn.execute
       ├─ 한 메시지를 질문 여러 개로 나눔(최대 4개), 턴당 LLM 호출 최대 6회
       └─ 질문마다: flowi_routing.resolve(관리자 질문 해석 규칙 — 문장 전체 일치 시 경로 고정)
            └─ core/data_chat.execute  ← 고정 순서의 규칙 처리기(먼저 잡는 쪽이 처리)
                 1 의미 별칭 관리(관리자) → 2 리포트 → 3 표시 중 차트 모양 수정
                 4 파일 차트·대시보드·POR·ET 차트·ET → 5 스플릿 조회 대기 답
                 6 제품 확인(없으면 되묻기) → 7 웨이퍼맵·ML 차트·INLINE 차트·INLINE·ETA·스플릿 조회
                 8 제품 위키 → 9 용어 연결(여러 개면 번호 선택) → 10 스플릿 변경·Split lead
                 11 TEG → 12 표시 중 표의 인라인 차트
                 13 _feature_plan: 웨이퍼 목록 규칙 → 정규식 규칙 계획 → (없으면) 계획 LLM
                 14 선택된 읽기 전용 작업 실행(data_chat_features.ACTIONS) 또는 일반 조회(_execute_data)
```

- 계획 LLM(`_feature_plan`): 도구 스키마·제품 목록·참고자료·대화·질문을 JSON 으로 보내고 `{action, params}` 를 받는다.
  action 은 enum 으로 제한(게이트웨이가 지원하면 json_schema strict, 아니면 json_object, 아니면 프롬프트 JSON — `llm_adapter._complete_structured`).
  프롬프트는 고정 내용이 앞, 질문이 맨 뒤(서버 prefix cache 재사용). 참고자료는 관련도 순 글자 예산(`core/llm_prompt_budget.py`).
- 불변식: LLM 출력은 사용자 의도의 증거가 아니다(제품은 확인된 것만), 읽기 전용 작업만 고른다, 변경·승인은 별도 메시지·명시 승인.
- 사람에게 되묻기(HITL): 응답의 `tool.missing`·`tool.clarification`·후보 목록을 화면(`features/home/InterpretationPanel.jsx` `hitlSections`)이
  버튼으로 그린다. 대기 상태는 `pending_*` context 키 — `data_chat.execute` 첫 줄의 허용 키 목록에 있어야 다음 턴까지 남는다.
- 학습 신호: 성공 질문(`core/chat_prompts.py`, 되묻기·후속 질문 제외), 👍👎·교정(`core/chat_feedback.py`, 교정은 개인 전용,
  다른 사용자에게는 서로 다른 3명 이상 합의만), 관리자 질문 해석 규칙(`core/flowi_routing.py`). 되묻기 답은 학습하지 않는다(사용자 결정).
- LLM 이 없거나 실패하면 규칙 경로만으로 답하고, 필요한 조건을 되묻는다.

## 8. 프런트엔드

React 18 + Vite SPA. 탭 목록은 `frontend/src/app/pageManifest.jsx`, 화면은 `frontend/src/features/<기능>/`. 모든 API 호출은
`lib/api.js` 가 세션 토큰을 붙인다. 디자인 토큰·검사(`npm run check`)는 AGENTS.md **UI and design system**.

## 9. 배포

`python _build_setup.py` → `frontend/dist` 재빌드 + 자기추출 `setup.py`(소스 전체 번들). 현장에서 `python setup.py` 가 추출 →
의존성 설치 → 프런트 빌드(가능하면) → **라이브러리 점검 표**(`check-deps`, `install_check.json`) 순으로 돈다.
묶음 pip 실패·누락은 개별 재시도하고, 최종 표는 앞 단계 실패 뒤에도 출력한다. 필수 패키지 미설치·버전 부족·import 실패는
기본 설치에서도 실패 종료한다. 설치 명령 실패와 실제 사용 가능 상태는 JSON에 따로 남긴다.
프런트는 소스·lock 지문 및 모든 dist 자산 해시가 일치하면 strict에서도 npm 없이 재사용하고, 재빌드 시 `npm ci`를 쓴다.
기본 추출은 `docs/`·`tests/`·`AGENTS.md` 를 풀지 않는다 — 코드 에이전트가 쓸 설치 폴더는 `extract --all`.
새 backend 모듈은 `backend/app.py` `_REQUIRED_BUNDLED_BACKEND_SOURCES`, 새 최상위 파일은 `_build_setup.py` 포함 목록 두 곳에 넣는다.

## 10. 고칠 때 먼저 확인할 불변식

- API 프로세스 1개·감시기 기동. 여러 워커로 늘리지 않는다.
- 조회 경로는 원천을 동기 스캔하지 않는다. 캐시가 없으면 빌드를 큐에 넣고 즉시 응답.
- 무거운 작업은 `run_heavy` 를 거친다. 긴 비캐시 작업을 캐시 슬롯에 넣지 않는다.
- 이벤트 루프에서 큰 직렬화·파일 I/O 를 하지 않는다.
- 새 설정은 코드에 기본값을 둔다(`config/`·데이터 파일은 시드일 뿐).
- LLM 은 선택 사항이다. LLM 이 없을 때의 동작을 먼저 설계한다.
