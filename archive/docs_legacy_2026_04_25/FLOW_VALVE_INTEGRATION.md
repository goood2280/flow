# Flow ↔ Valve Integration

## Summary

`flow`와 `valve`는 역할 분리가 잘 맞는다.

- `flow`: 공정 의미 계층, rulebook, reformatter, 제품별 process 설정, 운영 UI
- `valve`: 사내 DataLake query, chunk planning, parquet staging, S3 upload

즉 최종 구조는 아래가 맞다.

`flow`가 "무엇을 어떤 기준으로 뽑을지"를 관리하고  
`valve`가 "실제로 DB에서 어떻게 안전하게 뽑아 parquet/S3로 만들지"를 수행한다.

## Current Assessment

### 잘 맞는 점

1. `valve`는 이미 `LakeAPI -> Planner -> Executor -> S3Uploader`로 분리돼 있다.
2. `valve`의 `source_types.yaml`, `products.yaml`, `settings.json`은 외부 설정 기반이라 `flow`가 만든 설정을 읽기 쉽다.
3. `flow`는 이미 `reformatter`, `step/function/module`, `adapter`, `rulebook`, `orchestrator`가 있어 "무엇을 뽑아야 하는지"를 정의하기 좋다.
4. 둘 다 파일 기반 설정을 먼저 쓰고 있어서 사내 API 승인 없이도 운영 가능하다.

### 지금 바로 붙이면 위험한 점

1. 제품 설정이 `flow`와 `valve`에 이중 저장될 수 있다.
2. `valve` 문서에도 적혀 있듯 ET reformatter raw item 목록 자동 연동이 아직 없다.
3. `process_id`, `line_id`, `product_code` 같은 query 조건이 `valve/products.yaml`에만 있으면, 실제 분석 기준과 추출 기준이 어긋날 수 있다.
4. `step_id -> function_step -> module` 같은 의미 계층은 `flow`에 있고, `valve`는 아직 raw query 관점이라 그대로 두면 운영자가 이해하는 이름과 실제 추출 조건이 벌어진다.

## Recommended Contract

단일 소스는 `flow`로 둔다.

- `flow`가 관리
  - 제품별 `process_id`, `line_id`, `product_code`
  - 소스별 필수 컬럼
  - ET/INLINE reformatter raw item 목록
  - source별 shard 전략 힌트
  - step/function/module registry

- `valve`가 소비
  - query params template
  - source type registry
  - ET raw item prefilter
  - S3 target path

즉 `valve/config/*.yaml`을 사람이 직접 편집하는 구조보다,
`flow -> exported contract json/yaml -> valve import`
구조가 맞다.

## Integration Boundaries

### 1. Product Query Contract

`flow`가 아래를 제품별로 export 한다.

- `product`
- `source`
- `table`
- `params_template`
- `custom_col`
- `shard_hierarchy`
- `target_chunk_rows`
- `process_id`
- `line_id`
- `product_code`

이 계약은 사실상 `valve/config/products.yaml`의 상위 버전이다.

### 2. ET Reformatter Contract

ET는 `flow`의 reformatter를 기준으로 `valve`가 raw item filter를 자동 적용해야 한다.

필수 항목:

- `product`
- `report_variant`
- `point_mode`
- `report_audience`
- `rawitem_ids`
- `aliases`
- `use`
- `spec`
- `spec_order`
- `tracker_attach`

중요한 점:

- `valve`는 ET 전체 item을 다 뽑지 않는다.
- `flow`가 지정한 reformatter raw item 목록만 우선 추출한다.
- 나중에 수동 drill-down이 필요할 때만 추가 item query를 한다.

### 3. Process Registry Contract

제품별 query/분석 일관성을 위해 아래 계층을 export 한다.

- `product`
- `process_id`
- `line_id`
- `step_id`
- `function_step`
- `module`
- `step_class`
- `measure_domain`

이 계약이 있으면:

- `valve`는 process_id 기준으로 query 범위를 안정화할 수 있다.
- `flow`는 추출된 raw data를 의미 계층으로 바로 연결할 수 있다.

## Practical Operating Model

### Phase 1

`flow`가 계약 파일을 생성하고, `valve`는 그 파일을 읽어 query 설정을 갱신한다.

- 장점: 가장 단순하고 승인 절차가 없다.
- 단점: 완전 실시간은 아니다.

### Phase 2

`flow`가 S3 또는 shared workspace에 계약 파일을 저장하고, `valve`가 주기적으로 reload 한다.

- 장점: 반자동 운영 가능
- 권장 주기: 5~15분

### Phase 3

사내 API가 열리면 `valve`가 `flow`의 exported contract API를 읽거나,
반대로 `flow`가 `valve`에 job enqueue JSON을 직접 POST 한다.

## What Should Be Owned By Flow

아래는 `flow`가 single source of truth 여야 한다.

1. ET reformatter
2. 제품별 process_id / line_id / product_code
3. step/function/module registry
4. knob rulebook
5. source adapter / alias 정책

이유는 이 값들이 단순 query 설정이 아니라 운영 의미와 직접 연결되기 때문이다.

## What Should Be Owned By Valve

아래는 `valve`가 소유하는 게 맞다.

1. query retry / timeout / rate limit
2. chunk planning / sharding
3. staging parquet merge
4. S3 upload
5. query failure monitor / probe / retry agent

즉 `valve`는 추출 엔진이고, `flow`는 의미/운영 플랫폼이다.

## Immediate Next Steps

1. `flow`에서 `valve`가 읽을 export contract 파일 포맷을 고정
2. ET reformatter raw item 목록 export 추가
3. 제품별 process setting export 추가
4. `valve`가 contract를 읽어 `products.yaml`을 대체하거나 merge 하는 importer 추가

## Immediate Sync Rule

운영 기준으로 아래 파일들은 저장 즉시 S3에 올라가야 한다.

- `data/holweb-data/reformatter/*.json`
- `data/holweb-data/product_config/*.yaml`
- `data/Fab/step_matching.csv`
- `data/Fab/matching_step.csv`
- `data/Fab/knob_ppid.csv`
- `data/Fab/inline_matching.csv`
- `data/Fab/vm_matching.csv`
- 필요 시 `mask.csv`, `inline_item_map.csv`, `inline_step_match.csv`, `inline_subitem_pos.csv`

이 파일들은 배치 업로드가 아니라 `save -> atomic local write -> immediate s3 push`가 맞다.

이유:

1. `valve`가 다음 query 실행 때 바로 최신 계약을 읽어야 한다.
2. 엔지니어가 rulebook/reformatter를 수정한 뒤 구버전 설정으로 추출되면 운영 사고가 난다.
3. 제품별 process setting 역시 `flow`에서 수정 즉시 `valve`가 소비 가능한 상태가 되어야 한다.

## Valve Alert Pullback

`valve`가 `config_sync` fallback / S3 invalid / local corrupt / missing config 같은 상태를
S3 mirror 또는 shared workspace JSONL 로 남기면, `flow`는 그 파일을 주기적으로 읽어
admin bell notify 를 보내는 구조가 맞다.

권장 이벤트 포맷:

```json
{
  "ts": 1776991200,
  "source": "valve.config_sync",
  "kind": "config_fallback_last_good",
  "severity": "warn",
  "title": "products.yaml — last_good 로 fallback 완료",
  "meta": {
    "name": "products.yaml",
    "error": "..."
  }
}
```

현재 `flow`는 이 형식의 JSONL 을 polling 하는 `valve_watch` 스케줄러를 기준으로 두고 있다.
경로는 `data/holweb-data/valve_watch.json` 에서 바꿀 수 있게 두는 방향이 맞다.

## Decision

현재 평가:

- 구조 적합도: 높음
- 구현 난이도: 중간
- 운영 리스크: 중간

핵심 리스크는 기술이 아니라 설정 이중화다.

따라서 `flow`를 기준 설정원으로 두고 `valve`는 그것을 소비하는 구조로 가면,
말한 방향대로 유기적으로 연결될 가능성이 높다.
