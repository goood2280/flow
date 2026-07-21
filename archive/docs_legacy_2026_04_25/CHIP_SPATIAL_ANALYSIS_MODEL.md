# Chip Spatial Analysis Model

## 목적
지금까지는 `ET(shot)`와 `EDS(chip)`를 직접 잇기 어렵기 때문에
- ET는 `shot agg`
- EDS는 `wafer agg`
- 둘을 `wafer-level corr`
로 먼저 보는 경우가 많았다.

앞으로는 여기서 더 내려가서
- `ET TEG / shot 위치`
- `chip yield / fail bin / bin group`
- `radius / ring / quadrant / edge`
- `ML feature`
를 같이 다룰 수 있도록 기준을 고정한다.

## 왜 필요한가
- WF 평균으로는 분명히 보이는데 shot/chip으로 내려가면 안 맞는 경우가 있다.
- 반대로 wafer 평균에선 묻히지만 특정 ring, 특정 edge shot, 특정 TEG 주변에서만 강하게 보이는 현상도 있다.
- 따라서 `coarse grain`과 `fine grain`을 둘 다 가져가야 한다.

## 분석 grain

### 1. wafer grain
- key: `root_lot_id + wafer_id`
- 용도:
  - 빠른 trend
  - ML_TABLE, KNOB, lot-level 운영판단
  - coarse corr

### 2. shot grain
- key: `root_lot_id + wafer_id + shot_x + shot_y`
- 용도:
  - ET / INLINE map
  - ring / edge / quadrant 비교
  - TEG representative point 연결

### 3. chip grain
- key: `root_lot_id + wafer_id + shot_x + shot_y + chip_id`
- 용도:
  - EDS fail bin / bin group
  - local neighborhood yield
  - TEG proximity 기반 상세 분석

## 기본 원칙
- ET는 여전히 물리적으로 `shot`에서 찍힌다.
- EDS는 `chip`이 기본 단위다.
- 그래서 기본 브리지는 `shot -> chip`이다.
- 지금처럼 shot으로 퉁쳐서 먼저 보는 화면은 계속 유지한다.
- 다만 이후 ML/리포트는 chip-level도 내려갈 수 있게 설계한다.

## 권장 bridge

### 1. shot_layout_registry
- 제품별 usable shot 좌표계
- ET와 동일한 shot index 체계 사용
- 좌상단 `1,1`, 오른쪽 증가, 아래 증가

### 2. chip_layout_registry
- 제품별 shot 내부 chip 배치
- chip은 shot body 전체를 grid로 채움
- edge exclusion 적용 시 usable chip 판정 가능

### 3. teg_layout_registry
- `teg_no`, `teg_name`, `teg_ll_x`, `teg_ll_y`
- TEG lower-left를 representative point로 사용
- ET의 TEG number와 직접 연결

### 4. eds_bin_registry
- `bin_no`, `bin_group`, `bin_name`, `pass_fail`
- chip-level 수율을 bin group으로 묶기 위한 기준

## 분석 단계

### Stage 1. current safe mode
- ET shot agg
- EDS wafer agg
- wafer-level corr / trend / report

### Stage 2. practical bridge
- ET shot ↔ chip shot join
- chip yield를 shot으로 다시 agg
- `ET shot metric ↔ shot yield` 비교

### Stage 3. spatial detail
- TEG representative radius
- selected TEG neighborhood chip yield
- edge / ring / quadrant / directional asymmetry

### Stage 4. ML
- feature:
  - `shot_radius`
  - `ring_bucket`
  - `quadrant`
  - `edge_flag`
  - `teg_no`
  - `local_bin_fail_rate`
- target:
  - yield
  - fail bin ratio
  - ET performance / DVC

## 중요한 해석 규칙
- finer grain이 항상 더 잘 맞는 것은 아니다.
- shot/chip으로 내려갔을 때 노이즈가 커질 수 있다.
- 따라서 아래 3개를 같이 본다.

1. `wafer-level consistency`
- 전체 wafer 평균으로 같은 경향이 반복되는지

2. `shot-level spatial consistency`
- 특정 radius/ring/quadrant에서 반복되는지

3. `chip-level local consistency`
- 특정 TEG 주변 chip fail이 계속 같은 방향인지

즉,
- coarse에서만 보이는지
- fine에서만 보이는지
- coarse/fine 둘 다 같은지
를 같이 판단해야 한다.

## ET와 EDS를 바로 안 붙이는 이유
- ET는 shot 대표값일 뿐이며, shot 안 chip마다 응답이 다를 수 있다.
- EDS는 chip마다 fail mechanism이 다르다.
- 그래서 1차는 `shot agg yield`, 2차는 `TEG neighborhood yield`, 3차는 `chip local`로 내려가는 게 안전하다.

## 앱 적용

### Dashboard
- `ET shot metric vs shot yield`
- `radius bucket별 yield`
- `quadrant box plot`
- `TEG number color`

### ET Report
- 선택 TEG별 radius plot
- TEG별 주변 chip yield summary
- abnormal TEG repeated shot highlight

### ML
- `KNOB + spatial feature + ET + EDS`
- 어떤 knob이 특정 ring / edge / local fail과 결합되는지 확인

## 결론
- 지금까지의 `WF 단위 corr`는 계속 필요하다.
- 하지만 앞으로는 `shot bridge`, `TEG bridge`, `chip local`을 단계적으로 추가해야 한다.
- 그래야 “WF 평균으로는 맞는데 왜 실제 불량/수율은 안 맞지?”를 더 잘 설명할 수 있다.
