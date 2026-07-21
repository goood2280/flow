# FAB Datalake Long-format 스키마 (v8.8.29~)

## 배경

기존 ML_TABLE_PROD*.parquet 은 wide format 이었다:
```
| product | root_lot_id | wafer_id | INLINE_CD_GATE_MEAN | INLINE_CD_GATE_STD | INLINE_CD_SPACER_MEAN | ... |
```
이 방식은 **컬럼이 새 item 마다 늘어나므로** datalake 가 감당할 수 없다. 실제 사내 FAB 환경에서는 long format 이 표준:

```
| item_id         | subitem_id | lot_id | wafer_id | value | time                |
| CD_GATE_MEAN    | shot_01    | A0001  | 17       |  22.3 | 2026-04-23T08:00:00 |
| CD_GATE_MEAN    | shot_02    | A0001  | 17       |  22.4 | 2026-04-23T08:00:00 |
| CD_SPACER_MEAN  | shot_01    | A0001  | 17       |  11.9 | 2026-04-23T08:00:00 |
...
```

## 세 가지 원천 (테이블)

### 1. FAB — 공정 이력 (장비/레시피/챔버 등)

경로: `/DB/1.RAWDATA_DB_FAB/<PROD>/date=YYYYMMDD/part_*.parquet`

| 컬럼         | 타입   | 설명                                         |
| ------------ | ------ | -------------------------------------------- |
| product      | String | 제품 코드 (예: PRODA)                        |
| lot_id       | String | fab lot id (예: A0001A.1_V1)                 |
| root_lot_id  | String | root lot id (예: A0001)                      |
| wafer_id     | Int64  | wafer 순번                                   |
| step_id      | String | 공정 step id                                 |
| item_id      | String | 속성 이름 (예: EQP, PPID, CHAMBER, SLOT)      |
| subitem_id   | String | 세부 구분자 (예: chamber_no, slot_no) — 없으면 "" |
| value        | String | 측정/기록 값                                 |
| time         | String | ISO 8601 타임스탬프                          |

**예시**:
| product | lot_id    | wafer_id | step_id | item_id | subitem_id | value      | time                |
|---------|-----------|----------|---------|---------|------------|------------|---------------------|
| PRODA   | A0001A.1  | 17       | GATE    | EQP     |            | GATE_EQ_01 | 2026-04-23T08:00:00 |
| PRODA   | A0001A.1  | 17       | GATE    | PPID    |            | PPID_GATE_007 | 2026-04-23T08:00:00 |
| PRODA   | A0001A.1  | 17       | GATE    | CHAMBER | chamber_1  | CH_A       | 2026-04-23T08:00:00 |

### 2. INLINE — 계측 (CD, TOX, OVL, METAL_RES 등)

경로: `/DB/1.RAWDATA_DB_INLINE/<PROD>/date=YYYYMMDD/part_*.parquet`

| 컬럼         | 타입    | 설명                                      |
| ------------ | ------- | ----------------------------------------- |
| product      | String  |                                           |
| lot_id       | String  |                                           |
| root_lot_id  | String  |                                           |
| wafer_id     | Int64   |                                           |
| item_id      | String  | 측정 항목 (예: CD_GATE, TOX_M1, OVL_X)   |
| subitem_id   | String  | shot 번호 (예: shot_01 ~ shot_17)         |
| value        | Float64 | 측정값                                    |
| time         | String  | 측정 시각                                 |

**집계 룰 (wafer 단위 요약)**: 여러 shot 을 wafer 단위로 평균/표준편차로 집계할 때:
- `INLINE_{item_id}_MEAN` = mean(value) by (item_id, wafer_id)
- `INLINE_{item_id}_STD`  = std(value) by (item_id, wafer_id)

### 3. ET — 웨이퍼 electrical test (die-level)

경로: `/DB/1.RAWDATA_DB_ET/<PROD>/date=YYYYMMDD/part_*.parquet`

INLINE 과 달리 shot 을 **2D 좌표로 직접 저장**:

| 컬럼         | 타입    | 설명                                      |
| ------------ | ------- | ----------------------------------------- |
| product      | String  |                                           |
| lot_id       | String  |                                           |
| root_lot_id  | String  |                                           |
| wafer_id     | Int64   |                                           |
| item_id      | String  | 파라미터 (예: VT, IDSAT, ROFF)            |
| shot_x       | Int64   | x 좌표                                    |
| shot_y       | Int64   | y 좌표                                    |
| value        | Float64 |                                           |
| time         | String  |                                           |

**집계 룰**:
- `ET_{item_id}_MEAN` / `_STD` by (item_id, wafer_id)
- die-level wafer map 은 (shot_x, shot_y, value) 그대로 heatmap 렌더.

## 무한 성장 대응

새 item (예: `CD_VIA_TOP_MEAN`) 이 추가돼도 **스키마 변경 없음** — row 로 추가된다.
기존 컨슈머 (Dashboard / SplitTable / ML) 는 다음 중 선택:
1. **Pivot 뷰**: 필요한 item 집합을 wide 로 돌려서 사용. `item_id IN (...) → pivot(wafer_id × item_id → value)`.
2. **Scan 뷰**: 원본 long table 을 그대로 쓰며 `item_id == "X"` 필터링.

SplitTable 의 KNOB/MASK/INLINE/FAB/VM prefix 는 **wide pivot 후의 컬럼명 규약** 으로 해석한다:
- `KNOB_GATE_PPID` → FAB 에서 `step_id=GATE AND item_id=PPID`
- `INLINE_CD_GATE_MEAN` → INLINE 에서 `item_id=CD_GATE` 의 wafer 평균
- `ET_VT_MEAN` → ET 에서 `item_id=VT` 의 wafer 평균

## 마이그레이션 경로

### Phase 1 (v8.8.29, 현재): 공존
- 새 long-format 파일을 `/DB/1.RAWDATA_DB_FAB_LONG/`, `/INLINE_LONG/`, `/ET_LONG/` 에 나란히 생성.
- 기존 wide hive(`1.RAWDATA_DB_FAB/<PROD>/`) 는 유지 — SplitTable override 가 계속 작동.
- BE 에 pivot adapter (`core/long_pivot.py`) 를 추가해 long → wide 변환 함수 제공.

### Phase 2 (v8.9.x): long 이 primary
- SplitTable `_scan_fab_source` 를 long path 에서 읽어 pivot → wide 로 사용.
- ML_TABLE_PROD*.parquet 빌드 파이프라인도 long 기반 pivot 으로 전환.
- 구 wide hive 제거.

### Phase 3 (v9.0): schema-free 대시보드
- Dashboard X/Y 가 `(source, item_id, subitem_id, agg)` 튜플로 item 선택.
- SplitTable 이 prefix hard-code 없이 item registry 조회.

## 생성 도구

`scripts/gen_long_sample.py` — 소량의 샘플 long-format parquet 을 생성해 BE adapter 를 테스트/데모한다. 기존 wide 데이터와 컬럼 이름/값이 호환되도록 매핑.
