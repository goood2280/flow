# Wafer Layout Input Guide

## 목적

Wafer / shot / chip / TEG 배치를 가장 단순하게 입력하면서도  
나중에 `ET radius plot`, `TEG proximity`, `shot agg`, `EDS chip 연결`까지 확장할 수 있게 한다.

## 권장 입력 단위

좌표 단위는 하나로 통일해야 한다.

- 권장: `um`
- 가능: `mm`

중요한 건 절대 단위가 아니라 **모든 값이 같은 단위**를 쓰는 것이다.

## 최소 입력 세트

### 1. Wafer 기준

- `wf_center_x`
- `wf_center_y`
- `wafer_radius`

보통 wafer center 는 `(0, 0)` 으로 두는 편이 가장 단순하다.

### 2. Shot lattice 기준

- `ref_shot_x`
- `ref_shot_y`
- `ref_shot_center_x`
- `ref_shot_center_y`
- `shot_pitch_x`
- `shot_pitch_y`
- `shot_size_x`
- `shot_size_y`

의미:

- `ref_shot_x`, `ref_shot_y`
  - shot index 기준 reference shot 번호
- `ref_shot_center_x`, `ref_shot_center_y`
  - 그 reference shot center 의 wafer absolute 좌표
- `shot_pitch_x`, `shot_pitch_y`
  - 인접 shot center 간 거리
- `shot_size_x`, `shot_size_y`
  - shot 실제 크기

즉 shot 배치는

`reference shot 1개 + pitch + size`

만 있으면 전부 복원된다.

## Chip 배치 입력

Chip 은 두 방식 중 하나로 받으면 된다.

### A. Grid 방식

- `chip_cols`
- `chip_rows`
- `chip_width`
- `chip_height`
- `chip_origin_mode`

이 방식은 shot 안 chip 이 규칙적일 때 가장 쉽다.

`chip_origin_mode`:

- `shot_lower_left`
  - shot 좌하단이 `(0,0)`
- `shot_center`
  - shot center 가 `(0,0)`

### B. Explicit layout 방식

나중에 irregular chip layout 이 필요하면 chip 별 좌표를 직접 받는다.

- `chip_name`
- `chip_x`
- `chip_y`

현재는 A 방식으로 시작하는 것이 맞다.

## TEG 입력 규약

### 기본 원칙

- TEG 좌표가 주어진 경우:
  - **그 좌표는 TEG lower-left** 로 본다.
- TEG 좌표가 따로 없고 shot-level만 있는 경우:
  - **그 TEG는 해당 shot center에 있다고 본다.**

즉 TEG representative point 규칙은 아래다.

1. `teg_x`, `teg_y` 있으면: lower-left representative
2. 없으면: shot center representative

이 규칙이 ET radius 계산의 기본이다.

## 가장 쉬운 입력 포맷

### Wafer / Shot / Chip

```json
{
  "unit": "um",
  "wafer": {
    "wf_center_x": 0,
    "wf_center_y": 0,
    "wafer_radius": 150000
  },
  "shot_layout": {
    "ref_shot_x": 0,
    "ref_shot_y": 0,
    "ref_shot_center_x": 0,
    "ref_shot_center_y": 0,
    "shot_pitch_x": 26000,
    "shot_pitch_y": 28000,
    "shot_size_x": 25000,
    "shot_size_y": 27000
  },
  "chip_layout": {
    "origin_mode": "shot_lower_left",
    "chip_cols": 6,
    "chip_rows": 4,
    "chip_width": 3600,
    "chip_height": 4800
  }
}
```

### TEG

```json
{
  "tegs": [
    { "name": "TEG_A", "teg_x": 2000, "teg_y": 3000 },
    { "name": "TEG_B", "teg_x": 18000, "teg_y": 2500 },
    { "name": "TEG_C" }
  ]
}
```

`TEG_C`처럼 좌표가 없으면 shot center representative 로 처리한다.

## ET radius 계산 규칙

ET에서 shot별 / TEG별 radius 를 계산할 때 기준은 `wf center`다.

### Shot radius

`radius_shot = distance(shot_center, wf_center)`

### TEG radius

- TEG 좌표 있음:
  - `radius_teg = distance(teg_lower_left_abs, wf_center)`
- TEG 좌표 없음:
  - `radius_teg = distance(shot_center_abs, wf_center)`

즉 TEG radius 는 항상 representative point 기준이다.

## ET Report 에 필요한 기본 결과물

report item 별로 기본적으로 아래가 들어가면 된다.

- `radius plot`
- `cumulative distribution`
- `basic statistical table`
- `trend`
- `box plot`

그리고 메일 본문에는 아래 scoreboard 가 필요하다.

- item별 전체 point 수
- spec out point 수
- out %
- worst point / worst wafer / worst lot

## 이상 탐지 기본 원칙

단순 1pt outlier 는 참고 수준이다.  
아래가 겹치면 더 강한 이상 신호로 봐야 한다.

1. 여러 pt 에서 spec out
2. wafer map 상 특정 방향 / ring / edge 경향
3. 최근 trend 에서 반복적으로 같은 pt / 같은 radius 영역 이상
4. 특정 shot / 특정 TEG family 에 집중

즉 report item은 무조건 reporting 하되,
reformatter 에 표시된 항목 전체를 한 번 훑고
`trend 대비 이상`, `map 경향`, `repeated abnormal pt`
를 추가 highlight 하는 구조가 맞다.

## Spec tuning 방향

초기에는 reformatter 에서 spec 을 준다.

나중에는 제품별로 아래를 보고 점진적으로 자율 tuning 할 수 있다.

- 최근 정상 lot 분포
- 제품/공정 revision 변화
- point mode별 분포 차이
- false alarm 빈도
- 실제 fail/yield impact

하지만 자동 tuning 은 곧바로 운영 spec 을 바꾸면 안 된다.

권장 단계:

1. `suggested_spec`
2. reviewer 승인
3. `active_spec` 반영

즉 `auto-suggest`, `human-approve`, `activate` 3단계가 맞다.
