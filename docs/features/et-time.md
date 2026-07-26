# ET 측정시간 (et_time)

탭 `ettime` / 라벨 "ET 측정시간". ET raw DB에서 root lot별로 PGM(pt) 단위 측정 소요시간을 집계해 보여준다. auto report의 `f_et_test` 구조와 PGM(pt) 라벨 규칙을 그대로 따른다.

## Owns

- 제품 → root lot 선택과 측정시간 집계 표
- PGM(pt) 라벨 산출 — `step_seq(Npt)_중복차수`
- 측정시간 추세(trend) 조회
- 측정 패키지 단위 상세(measure)

## 업무 규칙

- 측정시간 = `tkout_time - tkin_time`.
- 같은 `(step_id, PGM(pt))` 조합이면 **wafer가 달라도 측정시간은 동일하다**는 전제로 그룹당 한 행으로 요약한다.
- Point(pt) = 같은 `(fab_lot_id, wafer_id, tkout_time)` 측정 패키지의 pivot 행 수 = 고유 `(chip_x_pos, chip_y_pos, subitem_id)` 조합 수.
- Duplicate_Count = `(DC_Split, temperature, flat_zone, fab_lot_id, wafer_id, step_seq, Point)` 그룹 안에서 `tkout_time` dense rank. auto report의 `DC_Split`은 step_id→DC layer 매핑이라 flow에서는 `step_id`로 대체하고, `temperature`는 auto report처럼 5 단위로 반올림한다.

### 라벨 접미사 — auto report와 의도적으로 다른 지점

- **auto report** ([Main.py:933](../../../auto%20report/Main.py)) 는 **항상** 접미사를 붙인다: `f"{step_seq}({Point}pt)_{Duplicate_Count}"` → 1회만 측정해도 `1(25pt)_1`.
- **flow** ([backend/routers/et_time.py:155](../../backend/routers/et_time.py)) 는 같은 `(step_id, step_seq, Point)` 조합에 **재측정이 있을 때만** 붙인다 → 1회 측정이면 `1(25pt)`, 재측정이 있으면 `1(25pt)_1`, `1(25pt)_2`.

라벨 **문자열을 키로 두 시스템을 대조하면 1회 측정 건이 어긋난다.** 이건 알려진 의도적 차이이며, 바꾸려면 양쪽을 같이 바꿔야 한다.

## 데이터 소스

`{FLOW_DB_ROOT}/1.RAWDATA_DB_ET/<PRODUCT>/**/*.parquet`

기대 컬럼은 auto report 기준이다: `fab_lot_id`, `lot_id`, `root_lot_id`, `wafer_id`, `process_id`, `part_id`, `step_id`, `step_seq`, `tkout_time`, `item_id`, `flat_zone`, `eqp_id`, `probe_card_id`, `chip_x_pos`, `chip_y_pos`, `subitem_id`, `et_value`, `temperature`, `total_site_cnt`.

flow 내부 샘플 DB의 `shot_x`/`shot_y`/`flat`/`value` 명칭은 fallback으로 흡수한다. `tkin_time`은 auto report 소스에는 없고 flow ET DB에 있을 때만 측정시간을 계산한다.

## Does Not Own

- ET 측정값 자체의 분석 — [splittable.md](splittable.md), 대시보드 소유
- ET index 추출/다운로드 — [reformatize.md](reformatize.md)
- ET 진행 추적과 일일 스캔 — [tracker.md](tracker.md)

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_EtTime.jsx` |
| Backend router | `backend/routers/et_time.py` |

## API

| Method | Path | 용도 |
|---|---|---|
| GET | `/api/et-time/products` | ET DB 제품 목록 |
| GET | `/api/et-time/lots` | 제품의 root lot 목록 |
| GET | `/api/et-time/trend` | 측정시간 추세 |
| GET | `/api/et-time/measure` | 측정 패키지 단위 상세 |

## Guardrails

- Point(pt)·Duplicate_Count 산출 규칙은 auto report `Main.py`와 **같아야 한다.** 여기서만 바꾸면 두 시스템의 결과가 조용히 갈라진다. 접미사 표기만 위에 적은 대로 의도적으로 다르다.
- `tkin_time`이 없는 소스에서는 측정시간을 만들어내지 말고 없음을 표시한다.
- 컬럼 명칭 fallback은 흡수 계층에서만 하고, 내부 로직은 canonical 이름으로 유지한다.

## Verify

```bash
git diff --check
```

```bash
cd frontend && npm run build
```
