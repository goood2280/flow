# Wafer Geometry Model

## 목적
`ET(shot)`와 `EDS(chip)`를 공간적으로 연결하려면 단순 `shot_x, shot_y`만으로는 부족하다.  
이 문서는 `wafer center`, `ref shot center`, `shot size`, `chip layout`, `TEG relative coord`를 명시적으로 정의해서
나중에 `ET radius ↔ chip yield` 인사이트를 안정적으로 만들기 위한 기준이다.

## 핵심 판단
- 지금 단계의 기본 분석 단위는 여전히 `shot`이다.
- 하지만 좌표계는 지금부터 분리해서 잡아야 한다.
- 당장은 `TEG DUT` 전체 좌표 대신 `TEG lower-left`를 대표점으로 본다.
- EDS는 필요할 때만 `chip`까지 내려가고, 기본은 `shot agg`로 본다.

## 좌표계
앱에서 최소 4개 좌표를 구분한다.

1. `wafer absolute`
- wafer center를 `(wf_center_x, wf_center_y)`로 두는 절대 좌표계
- shot / chip / TEG의 최종 비교 좌표는 여기에 맞춘다

2. `ref shot lattice`
- reference shot `(ref_shot_x, ref_shot_y)`와 그 shot center 절대좌표
- 다른 shot center는 shot pitch로 계산한다

3. `shot local`
- shot 내부 좌표
- 2가지 모드 허용
  - `shot_lower_left`: shot 좌하단이 `(0,0)`
  - `shot_center`: shot center가 `(0,0)`

4. `TEG local`
- 각 TEG lower-left의 shot 상대 좌표
- 현재는 이 점을 TEG representative point로 사용

## 권장 엔티티

### 1. wafer_geometry_registry
- 목적: 제품별 wafer/shot 전역 좌표계 정의
- 권장 컬럼
  - `product`
  - `wf_center_x`
  - `wf_center_y`
  - `ref_shot_x`
  - `ref_shot_y`
  - `ref_shot_center_x`
  - `ref_shot_center_y`
  - `shot_pitch_x`
  - `shot_pitch_y`
  - `shot_size_x`
  - `shot_size_y`
  - `coord_unit`
  - `valid_from`
  - `valid_to`
  - `note`

### 2. shot_layout_registry
- 목적: shot 안 chip layout 정의
- 권장 컬럼
  - `product`
  - `layout_id`
  - `chip_id`
  - `chip_x`
  - `chip_y`
  - `coord_mode`
  - `is_reference`
  - `valid_from`
  - `valid_to`

### 3. teg_layout_registry
- 목적: shot 안 TEG 위치 정의
- 권장 컬럼
  - `product`
  - `step_id`
  - `step_seq`
  - `item_id`
  - `teg_id`
  - `teg_ll_x`
  - `teg_ll_y`
  - `coord_mode`
  - `valid_from`
  - `valid_to`
  - `note`

### 4. eds_chip_layout_registry
- 목적: chip-level EDS를 shot/chip absolute coordinate로 변환
- 권장 컬럼
  - `product`
  - `layout_id`
  - `chip_id`
  - `shot_x`
  - `shot_y`
  - `chip_x`
  - `chip_y`
  - `bin_group`
  - `is_edge`

## 계산 규칙

### shot center
`shot_center_abs = ref_shot_center_abs + (shot_index - ref_shot_index) * shot_pitch`

### TEG representative point
- 현재는 `TEG lower-left`
- shot local 좌표를 shot absolute 좌표로 변환

### radius
- `radius = sqrt((abs_x - wf_center_x)^2 + (abs_y - wf_center_y)^2)`
- ET/TEG/chip 모두 같은 wafer absolute 좌표계에서 계산

## 분석 단계

### Stage 1. current
- ET를 shot 기준으로 agg
- EDS도 shot 기준으로 agg
- shot radius별 yield / ET trend를 본다

### Stage 2. near-term
- TEG 대표점 radius 계산
- 특정 ET item의 TEG radius와 근처 chip yield를 연결
- ring / quadrant / radial gradient 분석

### Stage 3. advanced
- chip absolute coordinate로 완전 연결
- `distance(chip, TEG)` 기반 local neighborhood yield
- edge / center / ring / asymmetric hotspot 분석

## 앱 적용 방향

1. `ET Report + ET Time`
- step_seq별 duration만이 아니라, 추후 TEG radius 분포도 같이 표시

2. `Dashboard`
- shot-level wafer map
- radius bucket별 ET / EDS box plot
- quadrant별 yield 비교

3. `ML`
- `radius`, `ring`, `quadrant`, `edge_flag`를 feature로 사용
- KNOB × radius interaction 확인

## Shot / Chip 번호 체계
- usable shot만 대상으로 ET shot 번호를 다시 매긴다
- 기준:
  - 좌상단 = `1,1`
  - 오른쪽으로 갈수록 `x` 증가
  - 아래로 갈수록 `y` 증가
- chip은 EDS 기본 단위이므로 shot body 전체를 균등 grid로 채운다
- wafer edge exclusion에 걸리는 chip은 `usable chip` 집계에서 제외한다

## TEG number
- `teg_no`는 반드시 별도 컬럼으로 관리한다
- ET 측정 데이터의 TEG number와 직접 연결되는 키로 사용한다
- 화면에서는 TEG가 너무 많아질 수 있으므로 기본 전체 표시보다 `검색 / 선택 기반 강조`가 적합하다

## 지금 추가된 코드
- [wafer_geometry.py](/mnt/d/TEST_Making_Video/semi_all/flow/backend/core/wafer_geometry.py:1)
  - shot absolute center 계산
  - TEG / chip absolute coordinate 계산
  - radius / angle 계산
  - chip -> shot radius aggregate helper

## 실무 권장
- 좌표계는 데이터가 들어오기 전부터 문서와 registry로 고정한다
- ET/EDS join을 서두르지 말고 먼저 `absolute coordinate`를 신뢰 가능하게 만든다
- 초기엔 `shot agg` 중심으로 가고, `TEG representative point`를 중간 브리지로 쓰는 게 가장 안전하다
