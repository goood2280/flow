# Knowledge Layer — 단일 지식 레이어 (지식 카드)

Flow-i 의 도메인 지식을 **카드 하나의 포맷**으로 수렴시키는 레이어.
코어: `backend/core/knowledge_cards.py`. 흩어져 있던 저장소(fewshots, file_docs,
wiki, source_catalog)는 카드 뷰(어댑터)로 읽기 시점에 통일된다 — 마이그레이션 없음.

## 카드 4종과 우선순위

| origin | 위치 | 성격 | 배포 |
|---|---|---|---|
| **local** | `data_root/knowledge/cards_local/*.md` | 사내 티칭/교정 (살아있는 지식) | data_root — setup.py 가 건드리지 않음, 재배포에도 보존 |
| **seed** | `backend/core/knowledge_seed_cards/*.md` | **구조 지식** (값 무관 — "무엇을 어디서 찾는가") | 코드 트리 — setup.py 번들로 사내 배포 |
| **generated** | 런타임 생성 (저장 안 함) | 로컬 `ppid_knob.csv` 룰북에서 feature(split/knob)별 자동 생성 | 환경이 바뀌면 그 환경 CSV 로 자동 재생성 |
| **adapter** | 기존 `flowi_file_docs.json` / `flowi_fewshots.json` | 기존 티칭 자산의 카드 뷰 (읽기 전용) | 기존 그대로 |

같은 term 이면 **local > seed > generated > adapter**. 즉 사내에서 시드 카드가
틀린 걸 발견하면 `teach_card()`(또는 cards_local 에 같은 term 파일 작성)로 덮는다.

## 카드 포맷

마크다운 + 미니 frontmatter (yaml 의존 없음):

```markdown
---
term: 3.0 VTN                # 필수 — 조회 키
kind: split-knob             # rulebook | data-source | concept | playbook | glossary ...
aliases: [VTN, KNOB_3.0_VTN] # 같은 뜻의 다른 표기
trigger_terms: [split 구성]   # term 이 아니어도 이 카드를 소환할 표현
answered_by: ppid_knob       # 담당 Unit AI key (registry 기준) — 기능 선택에 사용
sources:                     # 근거 파일 — 답변 출처 표기에 사용
  - file: ppid_knob.csv
    role: rulebook
related: [ppid-knob-rulebook]
status: active               # active | todo(미노출 틀) | disabled
---
본문 (자유 마크다운, LLM 프롬프트에 압축 주입됨 — 짧고 사실 위주로)
```

- 영문 term/alias 는 단어 경계 매칭(대소문자 무시, 공백↔`_` 허용), 한글은 부분 문자열 매칭.
- `status: todo` 카드는 조회에서 제외 — 채운 뒤 `active` 로 바꾸면 즉시 사용된다.

## 소비 지점 (배선)

| 지점 | 파일 | 효과 |
|---|---|---|
| unit dispatch 순서 | `routers/llm.py` `_run_flowi_chat` (unit_only 구성 직후) | `reorder_units()` — 카드 `answered_by` 가 지목한 유닛을 앞으로 (지식 기반 기능 선택) |
| 결과 지식 첨부 | `routers/llm.py` `attach_term_knowledge` | 매칭 카드 → `retrieved_knowledge` + `tool.knowledge_sources` (답변 출처) |
| LLM 폴백 프롬프트 | `routers/llm.py` (unhandled 폴백 프롬프트) | `prompt_block()` — GPT OSS 등에 컴팩트 지식 블록 주입 |
| 오케스트레이터 planner | `core/home_orchestrator.py` `_plan_with_llm` | 도구 카탈로그 앞에 지식 카드 섹션 + "담당 유닛 우선" 지시 |
| 오케스트레이터 휴리스틱 | `core/home_orchestrator.py` `_plan_from_heuristic` | 카드가 지목한 도구 점수 +2.0 부스트 |

## 관리자 채움 명령 (flow-i 채팅, admin 전용)

todo 카드를 연결된 LLM(사내 GPT OSS 120B 등)이 초안으로 채우는 HITL 루프.
초안(draft)은 **승인 전까지 조회/프롬프트에 노출되지 않는다**.

| 명령 | 동작 |
|---|---|
| `지식 현황` | 상태별/출처별 카드 수, todo/draft 목록, 답변 대기 질문 수 |
| `지식 채움 수행` | ① **환경 실조사**(db_root 파일 목록, 매칭 CSV 컬럼 헤더, ML_TABLE 제품/KNOB 컬럼)를 증거로 수집 → ② todo 카드(최대 4장/회)를 LLM 이 초안 작성(draft 저장) → ③ 증거로 확인 못 한 것은 "## 남은 질문" 으로 관리자에게 되물음(질문 큐 저장) |
| `지식 질문` | 답변 대기 질문 목록 (`knowledge/fill_questions.json`) |
| `지식 답변: <term> <답>` | 답을 해당 카드 본문 "## 사용자 답변" 에 병합, 질문 1건 소진. term 은 앞부분 최장 일치로 인식 |
| `지식 보기: <term>` | 초안/카드 본문 확인 |
| `지식 승인: <term>` | draft → active (즉시 사용 시작) |
| `지식 반려: <term>` | 로컬 초안 삭제 — 시드 todo 틀이 복원되어 다시 채울 수 있음 |

구현: `routers/llm.py` `_handle_knowledge_card_admin` (티칭 핸들러와 같은 최우선
결정 신호 단계), `core/knowledge_cards.py` `fill_todo_cards / set_status / status_summary`.

## 운영 (집 → 사내)

1. **집(선주입)**: `knowledge_seed_cards/` 에 구조 지식 카드를 작성/보강한다.
   값(실제 제품명, PPID)은 넣지 않는다 — 값은 generated/local 이 담당.
2. `python setup.py` 로 배포 — 시드 카드가 소스 번들에 포함된다 (`backend/core` 전체 수집).
3. **사내(축적)**: 사내 ppid_knob.csv 기준 generated 카드 자동 생성. 기존 "기억해:" /
   "파일 설명:" 티칭은 어댑터로 즉시 카드 뷰에 합류. 교정·심화 지식은
   `knowledge_cards.teach_card()` → cards_local.
4. 재배포 시: seed 는 갱신, cards_local 은 보존, generated 는 재생성 — 충돌 없음.

## 채움표 — 남은 todo 카드 (사내 채움용)

KNOB 네이밍/lot 체계/ET·INLINE·VM 구조/reformatter/commonality 등 구조 지식은
2026-07-19 사용자 답변으로 시드에 채워졌다. 2차 인터뷰(같은 날)로 ET 대표값 규칙
(`et-representative`), 일반 은어(`fab-terms` — flier/excursion/물량/자재), INLINE 항목
종류(`inline-item-naming`), 웨이퍼 Zone(`wafer-zone`), wafer 매수/scrap, EVT 접미·세대
문자, reformatter 공통 스키마/spec 열까지 채워 활성화했다. 사내 고유 은어는 fab-terms
카드에 계속 축적한다. 3차 인터뷰(같은 날)로 7장 추가: `shot`(shot/chip/TEG/DUT 계층),
`eqp-chamber`(eqp→chamber 분해, PM 이력 부재), `rework`(photo rework/EC step),
`por`(POR=기준, 2매 이상·최다 진행 휴리스틱), `corr-interpretation`(R², 밑둥/cloud 이상),
`wafer-map`(값 컬러링·spec out map, chip_x_adj 변환), `yield-scope`(수율 데이터 부재).
이후 `process-terms`(etch/depo/litho 등 일반 공정용어 — 값 무관이라 시드 active 번들) 추가.
남은 todo:

| 카드 | 핵심 질문 |
|---|---|
| `todo-product-naming.md` (제품 상세) | 실제 제품 코드/별칭 목록, 제품별 특이사항 — 보안상 시드 제외, 사내 local 채움 |
| `todo-et-items.md` (ET 항목 종류) | HOL 관점 TEG 항목 목록/이름 규칙 — 보안상 시드 제외, 사내 채움 |
| `todo-process-stage.md` (공정 구간) | 제품별 step_id 구간 ↔ 공정 단계(PC/RMG/MOL/BEOL) 매핑 — "지식 채움 수행" 시 Vehicle_matching.csv step_desc 를 `domain.classify_process_area` 로 분류한 **구간 자동 추정이 환경 증거로 제공**되고, 관리자가 확인/교정해 채운다 |

채우는 법: 파일에 답을 쓰고 `status: todo` → `active`, 또는 사내에서
"지식 채움 수행" / "지식 답변:" 명령 사용. 새 지식은 파일 1개 = 카드 1장.

## 테스트

`tests/test_knowledge_cards.py` — 파서 왕복, 병합 우선순위, 경계/한글 매칭,
todo 제외, 생성 카드, unit 힌트/재정렬, prompt_block, 티칭 저장/삭제 (hermetic).
