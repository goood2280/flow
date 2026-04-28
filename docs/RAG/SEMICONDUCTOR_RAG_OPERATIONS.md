# Semiconductor RAG Operations

Flow-i의 RAG는 사내 반도체 지식을 LLM 프롬프트에 길게 매번 붙이는 구조가 아니라, Flow 서버가 관리하는 구조화 파일에 저장한 뒤 RCA/차트/파일 준비 단위기능이 필요한 만큼 꺼내 쓰는 구조다.

## 저장 계층

| 계층 | 위치 | 용도 | 덮어쓰기 정책 |
|---|---|---|---|
| 코드 기본 seed | `backend/core/semiconductor_rca_seed_knowledge.json` | GAA 기준 기본 RCA 카드, causal edge, seed case | Git/setup.py로 갱신 |
| 설치 seed | `data/flow-data/semiconductor/seed_knowledge/semiconductor_rca_seed_knowledge.json` | 새 설치에서 바로 쓰는 기본 지식 | 없을 때만 생성, 있으면 보존 |
| RAG 추가 지식 | `data/flow-data/semiconductor/custom_knowledge.jsonl` | 심층리서치, 사내 판단, RCA 카드 draft | append-only |
| 유저/엔지니어 prior | `data/flow-data/semiconductor/engineer_knowledge.jsonl` | 사용자별 업무 성향, 담당 module, 자주 보는 판단 기준 | append-only |
| reformatter | `data/flow-data/reformatter/<product>.json` | real item alias/정규화 rule | admin apply만 |
| 제품/TEG YAML | `data/flow-data/product_config/products.yaml` | wafer layout, TEG 좌표, 제품 설정 | admin apply만 |

`FLOW_DATA_ROOT`가 설정되어 있으면 `data/flow-data` 대신 그 경로 아래에 저장된다.

## 가장 쉬운 입력 방법

Home Flow-i 또는 진단/RCA 탭에서 아래처럼 시작한다.

```text
[flow-i RAG Update] GAA short Lg에서 DIBL과 SS가 같이 증가하고 Vth roll-off가 나빠지면
channel release CD, inner spacer recess, RMG/WFM shift를 후보로 본다.
단일 DIBL만으로 root cause를 확정하지 말고, short/long Lg 분리와 gate/channel CD correlation을 확인한다.
```

이 입력은 `custom_knowledge.jsonl`에 저장된다. Admin이 입력하면 public 지식으로, 일반 user가 입력하면 private 지식으로 저장된다.

RAG 지식 변경은 일반 대화와 분리한다. 일반 user는 반드시 프롬프트 앞에 `[flow-i update]` 또는 `[flow-i RAG Update]`를 붙여야 저장된다. 마커가 없는 질문은 조회/진단/차트 요청으로만 처리되고 RAG 파일을 수정하지 않는다. Admin은 관리 화면/API에서 명시적으로 저장할 수 있지만, 운영상 동일한 마커를 붙이는 것을 권장한다.

## 어떤 내용을 넣어야 하나

RCA 품질을 높이는 지식은 단순한 문장보다 아래 필드를 포함할수록 좋다.

| 필드 | 예 |
|---|---|
| symptom | `DIBL increase`, `SS increase`, `CA_RC_KELVIN increase`, `SRAM Vmin tail` |
| item 조건 | source type, unit, test structure, polarity, Lg, bias condition |
| electrical mechanism | 전기적으로 왜 metric이 움직이는지 |
| structural cause | 실제 구조/CD/계면/막질/defect 후보 |
| process root cause | module, step, recipe, chamber, QTIME 후보 |
| supporting evidence | 같이 움직이면 강한 근거가 되는 metric/Inline/wafer map |
| contradicting evidence | 이 조건이면 이 원인이 아닐 수 있는 반증 |
| missing data | 판단 전에 더 필요한 DB/File/차트 |
| recommended checks | 다음 확인 차트와 필터 |

## 권장 프롬프트 템플릿

### 1. RCA 카드 초안

```text
[flow-i RAG Update]
title: GAA short-Lg electrostatic degradation
symptom: DIBL 증가, SS 증가, Vth roll-off 악화
items: DIBL, SS, VTH_ROLLOFF
module: GAA_CHANNEL_RELEASE, INNER_SPACER, RMG_WFM
electrical mechanism: gate control 약화로 drain coupling과 subthreshold slope가 나빠짐
structural cause: effective gate length 감소, channel release profile shift, inner spacer asymmetry
process root cause: channel release etch drift, inner spacer recess drift, RMG/WFM thickness shift
supporting evidence: short Lg에서만 강함, gate/channel CD와 correlation, long Lg Vth는 상대적으로 안정
contradicting evidence: long Lg Vth만 global shift, wafer-local defect map
missing data: Lg split, polarity split, Inline gate/channel CD, wafer map
recommended checks: DIBL vs SS scatter, GATE_CD vs DIBL, Vth roll-off trend
```

### 2. 사내 심층리서치 요약

```text
[flow-i RAG Update]
source: internal deep research 2026-04 GAA contact module
summary: CA_CD shrink가 CA_RC_KELVIN과 chain resistance tail을 같이 키우는 사례가 있었음.
guardrail: CA_RS raw name만으로 sheet/contact를 판단하지 말고 unit/test_structure를 먼저 확인한다.
evidence: Kelvin/TLM이면 contact resistance 후보, ohm/sq Rsheet 구조면 sheet resistance 후보.
recommended checks: CA_CD vs CA_RC_KELVIN scatter, CA_CHAIN_R wafer map, CA etch/preclean route split.
```

### 3. Real item/TEG 해석

```text
[flow-i RAG Update]
PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르다.
alias화할 때 geometry_dimension, gate pitch, cell height, coordinate discriminator를 보존해야 한다.
14x14는 contact array size, 13x13은 reduced array, 12x12는 tighter DOE variant로 본다.
```

### 4. 유저별 업무 prior

진단/RCA 탭의 `Engineer Prior Knowledge`에 넣는다.

```text
module: CA_MOL_CONTACT
use_case: daily contact resistance excursion triage
prior_knowledge: CA_RC_KELVIN과 CA_CHAIN_R이 같이 움직이고 CA_CD가 줄면 CA etch/preclean을 먼저 본다.
tags: CA, contact, Kelvin, chain, CD
```

## LLM으로 구조화할 때 흐름

1. 사용자가 `[flow-i RAG Update]`로 자연어 지식을 입력한다.
2. 서버가 raw item 후보, canonical item 후보, discriminator, schema type을 추출한다.
3. `custom_knowledge.jsonl`에 원문과 structured JSON을 같이 append한다.
4. RCA 실행 시 `item_master`, 기본 seed pack, `custom_knowledge`, `engineer_knowledge`를 함께 검색한다.
5. Flow-i는 DB/File source profile과 whitelist tool 결과만 근거로 답한다.

중요한 점은 LLM이 DB 값을 만들거나 SQL을 직접 생성하지 않는다는 것이다. LLM은 “어떤 지식을 어떤 구조로 저장할지”와 “어떤 단위기능을 호출할지”를 돕고, 실제 조회/수정은 서버 tool이 수행한다.

## 직접 JSONL로 넣는 방법

대량 지식은 Admin이 검토한 뒤 `custom_knowledge.jsonl`에 append할 수 있다. 한 줄이 하나의 JSON object다.

```json
{"kind":"knowledge_card","visibility":"public","title":"GAA short Lg DIBL/SS RCA","source":"internal_research","product":"","module":"GAA_CHANNEL_RELEASE","items":["DIBL","SS","VTH_ROLLOFF"],"tags":["GAA","short_Lg","electrostatic"],"content":"DIBL and SS increase on short Lg requires electrostatic RCA checks.","structured_json":{"symptom_items":["DIBL","SS","VTH_ROLLOFF"],"trigger_terms":["GAA","short Lg","DIBL","SS"],"electrical_mechanism":"Worse gate electrostatic control increases drain coupling and subthreshold slope.","structural_causes":["effective gate length reduction","inner spacer asymmetry","channel release profile shift"],"process_root_causes":["channel release etch drift","inner spacer recess drift","RMG/WFM shift"],"supporting_evidence":["short Lg only","DIBL and SS move together","gate/channel CD correlation"],"contradicting_evidence":["long Lg global Vth shift only","local defect map"],"missing_data":["Lg split","polarity split","Inline gate/channel CD"],"recommended_checks":["DIBL vs SS scatter","GATE_CD vs DIBL","Vth roll-off trend"],"confidence_base":0.64}}
```

직접 편집할 때는 기존 파일을 덮어쓰지 말고 append만 한다. 잘못 넣은 지식은 삭제보다 새 row로 `disabled`, `superseded_by`, `quality_note`를 남기는 편이 추적에 유리하다.

## Admin 파일 반영이 필요한 지식

아래는 RAG Update만으로 끝내지 않고 Admin apply가 필요하다.

| 작업 | Flow-i 입력 | 실제 반영 |
|---|---|---|
| real item alias rule | `PRODA PC-CB-M1 Chain item들을 reformatter alias 후보로 정리해줘` | `data/flow-data/reformatter/<product>.json` |
| TEG 좌표/YAML | `이 파일의 TEG 좌표를 product YAML에 넣을 후보로 만들어줘` | `data/flow-data/product_config/products.yaml` |
| Files 수정/삭제 | `FLOWI_FILE_OP {...}` 확인 구조 | DB/Files root 안에서 admin만 |

일반 user는 DB/File 원본 수정 권한이 없다. 단, 인폼/이슈/회의처럼 앱 단위기능 권한으로 허용된 업무 데이터 생성은 가능하다.

## 검증 루틴

새 지식을 넣은 뒤 아래 질문으로 확인한다.

```text
GAA short Lg에서 DIBL과 SS가 증가했고 Vth roll-off도 나빠졌어. 원인 후보와 확인 차트 보여줘.
```

기대 결과:

- interpreted item meanings에 `DIBL`, `SS`, `VTH_ROLLOFF`가 나온다.
- top hypotheses가 electrical mechanism, structural cause, process root cause를 분리한다.
- missing data에 Lg/polarity/Inline CD/wafer map 같은 확인 항목이 나온다.
- 단일 metric으로 단일 root cause를 확정하지 않는다.

품질이 낮으면 Home Flow-i 답변의 피드백에서 `wrong_data_source`, `missed_clarification`, `hallucination`, `aggregation_error`, `key_matching_error` 같은 태그를 남긴다. Admin은 이 피드백을 golden case로 승격해 다음 평가 기준으로 쓸 수 있다.
