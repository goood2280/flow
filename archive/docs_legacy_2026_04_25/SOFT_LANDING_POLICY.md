# flow Soft-Landing Policy

이 문서는 테스트 데이터와 사내 실데이터의 차이를 앱이 어떻게 흡수해야 하는지에 대한 기준을 정리한다.

## 1. 왜 필요한가

현업에서는 아래 차이가 자주 발생한다.

- 폴더 경로가 다름
- 파일 이름 규칙이 다름
- 같은 의미의 컬럼인데 이름이 다름
- 대소문자만 다름
- wide/long 포맷이 다름
- 어떤 환경에는 컬럼이 있고 어떤 환경에는 없음

이 차이를 코드에 하드코딩하면, 테스트 환경에서는 되는데 실환경에서 바로 깨진다.

## 2. 원칙

- 앱은 가능한 한 canonical 이름으로 내부 로직을 유지한다.
- 실제 데이터와의 차이는 adapter/profile 계층에서 흡수한다.
- 경로와 컬럼명은 운영 중에도 조정 가능해야 한다.
- "못 찾음"으로 바로 죽기보다, 후보/매칭 로그/대체 경로를 보여주고 soft fail 한다.

## 3. 다뤄야 하는 soft-landing 대상

### 3.1 경로

- DB root
- Base root
- wafer map root
- 특정 제품별 루트
- 특정 팀 전용 파일 위치

### 3.2 파일명

- `ML_TABLE_PRODA.parquet`
- `ml_table_proda.parquet`
- `ML_TABLE_PRODA_v2.parquet`

같은 의미라면 alias 또는 패턴으로 찾을 수 있어야 한다.

### 3.3 컬럼명

예:

- `root_lot_id`
- `ROOT_LOT_ID`
- `rootLotId`

내부에서는 하나의 canonical 이름으로 취급해야 한다.

### 3.4 스키마 형태

- FAB/INLINE wide vs long
- 시각 컬럼 이름 다름 (`time`, `created_at`, `tkout_time`)
- lot/wafer join key 이름 다름

## 4. 권장 구현 방식

### 4.1 Canonical schema

코드 안에서는 아래처럼 canonical 이름을 쓴다.

- `product`
- `root_lot_id`
- `lot_id`
- `fab_lot_id`
- `wafer_id`
- `step_id`
- `step_seq`
- `tkin_time`
- `tkout_time`
- `time`
- `eqp`
- `ppid`
- `chamber`
- `item_id`
- `subitem_id`
- `shot_x`
- `shot_y`
- `value`
- `request_id`
- `map_id`

### 4.2 Adapter profile

환경별 차이는 profile 로 정의한다.

예:

```json
{
  "default": {
    "roots": {
      "db": ["/config/work/sharedworkspace/DB", "./data/Fab"]
    },
    "column_aliases": {
      "root_lot_id": ["ROOT_LOT_ID", "rootLotId"],
      "wafer_id": ["WAFER_ID", "waferId"]
    }
  }
}
```

### 4.3 Resolution order

권장 순서는 아래와 같다.

1. 정확히 일치하는 경로/파일/컬럼
2. 대소문자 무시 매칭
3. alias 매칭
4. 패턴/휴리스틱 매칭
5. 사용자 설정 override
6. 그래도 실패하면 후보 목록과 진단 메시지 반환

## 5. 운영 UX 기준

운영 화면에서는 아래가 가능해야 한다.

- 현재 해석된 경로 보기
- 경로 override 저장
- 컬럼 alias profile 보기
- 어떤 컬럼이 어떤 canonical 이름으로 매핑되었는지 보기
- 실패 시 "왜 실패했는지"와 "찾아본 후보들" 보기

즉, 실패를 숨기지 말고 진단 가능하게 노출해야 한다.

## 6. 현재 flow에서의 적용 방향

이미 일부는 구현되어 있다.

- `db_root`, `base_root` 는 runtime override 가능
- 일부 컬럼은 case-insensitive 로 찾음
- SplitTable/FAB 쪽은 long/wide soft landing 이 들어가 있음

앞으로는 이를 공통 adapter 계층으로 올려야 한다.

실데이터 기준 source model은 별도 문서와 설정 파일로 관리한다.

- 문서: `docs/REAL_DATA_SOURCE_MODEL.md`
- 설정: `data/holweb-data/adapters/source_models.json`

핵심 기준은 아래다.

- FAB: `run/event` 성격의 long table
- INLINE: `subitem_id` 기반 shot table
- ET: `(shot_x, shot_y)` + `step_seq` + package 구분이 있는 long table
- INLINE 과 ET 는 직접 join 하지 않고 matching table 을 통해 연결

### 우선순위

1. 공통 column alias resolver
2. 환경별 adapter profile 저장소
3. 화면에서 경로/alias 조정 UI
4. 주요 기능(FileBrowser, SplitTable, Inform, Dashboard)에 공통 적용

## 7. 한 줄 결론

실데이터 연동을 버티는 앱은 "정답 스키마를 강제하는 앱"이 아니라, "차이를 흡수하면서 내부에서는 일관성을 유지하는 앱"이어야 한다.
