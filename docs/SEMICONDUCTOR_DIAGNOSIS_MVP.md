# Semiconductor Diagnosis MVP

Flow의 `진단/RCA` 탭은 ET/Inline/VM/QTIME/EDS 같은 반도체 데이터를 LLM이 직접 SQL로 만지지 않고, 서버의 허용된 단위기능만 호출해서 해석하도록 만든 MVP다.

## 구조

```text
User / Flow-i prompt
  -> feature extractor
  -> item semantics resolver
  -> knowledge card RAG
  -> graph causal DB
  -> historical case DB
  -> eval guardrails
  -> structured RCA JSON + chart specs
```

현재 구현은 실제 DB/Files parquet·csv를 먼저 샘플링하고, 읽을 수 있는 데이터가 없을 때만 `mock_in_memory` seed로 fallback한다. 실제 DB 연결은 같은 API 경계 안에서 DuckDB/Postgres/pgvector로 교체하면 된다.

## API

| API | 역할 |
|---|---|
| `GET /api/items/search?q=` | item_master 검색 |
| `POST /api/items/resolve` | raw item을 canonical 의미로 해석 |
| `POST /api/data/query-et` | whitelisted ET 조회 |
| `POST /api/data/query-inline` | whitelisted Inline 조회 |
| `POST /api/analytics/trend` | trend 집계 |
| `POST /api/analytics/correlation` | lot_wf 기준 상관 분석 |
| `POST /api/charts/spec` | chart spec 생성 |
| `POST /api/diagnosis/run` | deterministic RCA JSON 생성 |
| `GET /api/diagnosis/{id}` | 저장된 diagnosis run 조회 |
| `POST /api/llm/chat` | 진단 전용 mock chat adapter |
| `POST /api/semiconductor/knowledge/update-prompt` | `[flow-i RAG Update]` 지식 append |
| `POST /api/semiconductor/dataset/sample` | DB 또는 Files 단일 parquet/csv 샘플 |
| `POST /api/semiconductor/dataset/profile` | DB 또는 Files source의 schema/grain/join key/aggregation 프로파일 |
| `POST /api/semiconductor/reformatter/propose` | real item alias/reformatter 후보 생성 |
| `POST /api/semiconductor/reformatter/apply` | admin 전용 reformatter 적용 |
| `POST /api/semiconductor/teg/propose` | TEG 좌표 YAML 후보 생성 |
| `POST /api/semiconductor/teg/apply` | admin 전용 product YAML 적용 |

## 저장 위치

| 종류 | 위치 | setup.py 포함 | 재설치 보존 |
|---|---|---:|---:|
| 기본 item/card/graph/case/use case seed | `backend/core/semiconductor_knowledge.py` | 예 | 코드로 재생성 |
| 기본 GAA RCA 지식 pack | `backend/core/semiconductor_rca_seed_knowledge.json` | 예 | `flow-data`에 없을 때만 복사 |
| 설치된 기본 RCA 지식 pack | `data/flow-data/semiconductor/seed_knowledge/semiconductor_rca_seed_knowledge.json` | 아니오 | 예 |
| 진단 실행 기록 | `data/flow-data/semiconductor/diagnosis_runs.jsonl` | 아니오 | 예 |
| 엔지니어 사전지식/업무 성향 | `data/flow-data/semiconductor/engineer_knowledge.jsonl` | 아니오 | 예 |
| GPT 심층리서치/RAG 추가 지식 | `data/flow-data/semiconductor/custom_knowledge.jsonl` | 아니오 | 예 |
| 제품 reformatter rule | `data/flow-data/reformatter/<product>.json` | 아니오 | 예 |
| 제품 YAML/TEG layout | `data/flow-data/product_config/products.yaml` | 아니오 | 예 |

운영에서 `FLOW_DATA_ROOT`를 쓰면 위 `data/flow-data`는 해당 경로 아래로 바뀐다. setup.py는 `flow-data`, `DB`, `Base`를 번들에 넣지 않고 덮어쓰지 않는다. 단, 기본 GAA RCA 지식 pack은 새 설치에서 바로 RCA가 동작하도록 `flow-data/semiconductor/seed_knowledge/`에 파일이 없을 때만 생성한다. 이미 존재하면 보존한다.

## GPT 심층리서치 결과 넣는 법

Flow-i 또는 진단/RCA 탭에서 아래처럼 입력한다.

```text
[flow-i RAG Update] PC-CB-M1 Chain item은 14x14, 13x13, 12x12 DOE TEG가 다르다.
alias화할 때 geometry_dimension, gate pitch, cell height, coordinate discriminator를 보존해야 한다.
```

이 입력은 seed 코드를 직접 수정하지 않고 `custom_knowledge.jsonl`에 append-only로 저장된다. Admin 입력은 public, 일반 user 입력은 private으로 저장된다.

심층리서치 결과를 긴 문서로 넣을 때는 다음 기준으로 나눠 넣는다.

- 일반 조사/논문/FA 요약: `[flow-i RAG Update]` 뒤에 핵심 item, module, mechanism, evidence, missing data를 적는다.
- RCA 카드로 승격할 내용: `원인`, `mechanism`, `supporting evidence`, `contradicting evidence`, `recommended checks`를 포함해서 입력한다.
- real item/TEG 해석: item명, geometry discriminator, gate pitch, cell height, coordinate, DOE 의미를 포함한다.
- 사내 엔지니어 판단/업무 성향: 진단/RCA 탭의 `Engineer Prior Knowledge`에 담당 module/use_case 기준으로 저장한다.

직접 파일로 넣어야 하는 경우에는 `data/flow-data/semiconductor/custom_knowledge.jsonl`에 JSONL append 형식으로 저장한다. 기본 구조는 아래와 같다.

```json
{"kind":"knowledge_card","visibility":"public","title":"GAA short Lg DIBL/SS RCA","module":"GAA_CHANNEL_RELEASE","items":["DIBL","SS"],"tags":["GAA","short_Lg"],"content":"요약","structured_json":{"symptom_items":["DIBL","SS"],"electrical_mechanism":"...","structural_causes":["..."],"process_root_causes":["..."],"supporting_evidence":["..."],"contradicting_evidence":["..."],"missing_data":["..."],"recommended_checks":["..."],"confidence_base":0.62}}
```

## DB / Files 데이터 소스

RCA와 구조화 작업은 둘 다 아래 source를 받는다.

```json
{"root":"ET","product":"PRODA"}
```

```json
{"source_type":"base_file","file":"EDS_PRODA.parquet","product":"PRODA"}
```

첫 번째는 `DB/<root>/<product>` 또는 hive `product=<product>` 구조를 읽는다. 두 번째는 Files 영역의 단일 parquet/csv를 읽는다. Flow-i 프롬프트에 `EDS_PRODA.parquet`처럼 파일명을 쓰면 파일 source로 우선 해석한다.

File Browser에서 DB 제품, root parquet, Base 단일파일을 연 뒤 `진단/RCA로` 버튼을 누르면 같은 source payload가 진단/RCA 탭으로 전달된다. 진단/RCA 탭은 즉시 `/api/semiconductor/dataset/profile`을 호출해서 다음을 보여준다.

- `suggested_source_type`: ET/INLINE/EDS/VM/QTIME/FAB 추정
- `metric_shape`: `long` (`item_id` + `value`) 또는 `wide` metric columns
- `grain`: `lot_wf`, `shot`, `die`, `lot`, `row`
- `join_keys`: `root_lot_id`, `fab_lot_id`, `wafer_id`, `lot_wf`, `shot_x/y`, `die_x/y` 등
- `default_aggregation`: source profile 기준 집계 규칙

따라서 ET/EDS/VM 등이 DB가 아니라 Files 단일 parquet/csv에 있어도, prompt에서 그 파일을 지정하거나 File Browser에서 넘기면 whitelisted query/reformatter/TEG tool이 같은 방식으로 샘플을 읽어 작업한다.

Home의 Flow-i에서도 프롬프트에 `ET_PRODA.parquet`, `EDS_PRODA.csv`처럼 실제 Files 파일명이 들어가면 같은 source resolver를 쓴다. 이때 응답 JSON과 공개 실행 trace에는 `data_source`와 `source_profile`이 포함되어, 사용자가 어떤 파일을 어떤 grain/shape/join key로 읽었는지 확인할 수 있다. 이 trace는 사고과정이 아니라 검증 가능한 실행 경로 요약이다.

source profile이 `AUTO`, `row grain`, join key 없음, profile 실패처럼 모호하면 Flow-i는 진단을 바로 실행하지 않는다. 대신 아래처럼 1/2/3 선택지를 반환하고, 사용자가 선택한 다음 턴에 `source_type=ET` 같은 확인값을 source filter로 실어 실행한다.

```text
1. ET/WAT parametric (Recommended)
2. INLINE metrology
3. EDS wafer sort
```

## Real Item / TEG / Reformatter

비슷한 raw item을 같은 alias로 묶기 전에 아래 discriminator를 먼저 분리한다.

| 구분 | 예 |
|---|---|
| geometry dimension | `14x14`, `13x13`, `12x12` |
| layer/module | `PC`, `CB`, `M1`, `CA`, `Gate` |
| structure | `Chain`, `Kelvin`, `TLM`, `sheet`, `comb` |
| device/layout | gate pitch, cell height, fin/sheet width |
| coordinate | shot-local TEG x/y, die x/y, wafer absolute |
| condition | bias, polarity, macro, bin, test temperature |

`/api/semiconductor/reformatter/propose`는 후보 rule만 만든다. `source`를 주면 DB/Files 샘플의 `item_id` 컬럼 또는 wide metric columns를 보고 후보를 만든다. 실제 저장은 admin이 `/api/semiconductor/reformatter/apply`를 호출할 때 `data/flow-data/reformatter/<product>.json`에 반영된다.

TEG 좌표는 `label/name/id`, `dx_mm/x`, `dy_mm/y` 컬럼을 가진 JSON row 또는 DB/Files dataset source로 넣는다. 적용 시 `data/flow-data/product_config/products.yaml`의 `wafer_layout.teg_definitions`에 들어간다.

## Source Type Profile

새 DB가 들어오면 먼저 source profile을 잡는다.

| source | 기본 grain | 기본 aggregation | 핵심 guardrail |
|---|---|---|---|
| FAB | lot/wafer/step/time | 최신 step/time | route sequence 없이 step 문자열 순서로 판단 금지 |
| INLINE | lot_wf 또는 shot/position | lot_wf avg | 좌표가 있으면 shot match 우선 |
| ET | lot_wf/item/step/point | lot_wf median | polarity, short/long Lg 혼합 금지 |
| VM | lot_wf/macro/condition/bin | macro별 median/fail-rate | ET/Inline 근거 없이 공정 원인 확정 금지 |
| QTIME | lot/wafer/from_step/to_step | duration median/p95 | 측정 이전 window인지 확인 |
| EDS | wafer/die/bin/condition | yield/fail-rate | spatial pattern을 먼저 보존 |

## RCA Guardrails

- raw item 이름만 보고 의미를 추론하지 않는다.
- `CA_RS`는 unit/test_structure가 Rsheet를 지지할 때만 sheet resistance로 본다.
- Kelvin/TLM/contact/chain이면 contact resistance 후보로 본다.
- 단일 metric으로 단일 root cause를 확정하지 않는다.
- RCA 출력은 symptom, electrical mechanism, structural cause, process root cause, supporting evidence, contradicting evidence, missing data, checks, confidence를 분리한다.
- LLM이 SQL을 만들지 않는다. 모든 조회/수정은 backend whitelist tool을 통한다.

## Sample Prompts

```text
GAA nFET short Lg에서 DIBL과 SS가 증가했고 CA_RS도 올랐어. 원인 후보와 확인 차트 보여줘.
```

```text
[flow-i RAG Update] SRAM Vmin fail은 reticle edge에서만 높고, 같은 lot의 Vth sigma와 GATE_CD sigma가 같이 증가했다.
```

```text
PRODA PC-CB-M1 Chain 14x14/13x13/12x12 item들을 reformatter alias 후보로 정리해줘.
```

```json
[
  {"name":"TEG_TOP","x":13.6,"y":29.6,"width":1.2,"height":0.6},
  {"name":"TEG_RIGHT","x":27.6,"y":14.6}
]
```
