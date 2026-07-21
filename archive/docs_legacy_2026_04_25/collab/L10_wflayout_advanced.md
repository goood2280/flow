# L10 — WF Layout 고도화 (Shot · TEG · Radius · EDS/ET 매핑)

> 작성: 2026-04-24 / 대상 페이지: `frontend/src/pages/My_WaferLayout.jsx`
> 규모: 대형 (FE 대폭 + BE 신설 + ETTime/ML 연동). 2~4 sprint 예상
> 우선순위: p2 (장기 백로그 L10 로 승격)

---

## 1. 배경 · 현재 한계

현재 My_WaferLayout 은 wafer 시각화 수준이지 "shot 안에 TEG 가 어떻게 배치되고, edge shot 이 어떻게 구성되고, EDS 와 ET 의 좌표가 어떻게 매핑되는가" 를 다루지 못한다. 엔지니어가 실제로 하는 분석을 앱이 못 따라가는 가장 큰 영역.

## 2. 도메인 정의

| 개념 | 정의 | 단위 |
|---|---|---|
| Wafer | 300mm round, center/flat-notch 기준점 | mm |
| Shot (Reticle field) | 노광 단위, 보통 26×33 mm 내 die 여러 개 | mm |
| Die | shot 안의 chip 단위 | 개수 |
| TEG | Test Element Group, shot 내 고정 위치 테스트 패턴 | 좌표 |
| EDS shot | die-level 전기 sort 검사 (yield) | die 단위 |
| ET shot | TEG 위치에서만 전기 측정 (Rc/Ion/Ioff/Vth) | TEG 단위 |
| Edge shot | wafer radius 기준 edge_exclusion 밖 shot | — |
| Radius | wafer center 대비 거리 = sqrt((x-cx)² + (y-cy)²) | mm |

## 3. 목표 (엔지니어 관점 시나리오)

1. **Shot 내 TEG 위치 확인**: shot 을 클릭하면 TEG 배치(예: 북서/북동/정중앙/남서/남동 5개) 가 hover 로 보여야 함
2. **Edge shot 조합 시뮬레이션**: TEG 다중 선택 → "이 TEG 들만 쓰면 edge shot 이 어떻게 구성되냐" 프리뷰
3. **EDS ↔ ET 샷 매칭**: ET TEG 좌표에 가장 가까운 EDS die 를 자동 매핑, 양쪽 wafer map 에 선 연결 (하이라이트)
4. **ET Report radius 반영**: ET 측정값 테이블에 각 row 의 `radius_mm` 자동 컬럼 + radius bin (center/mid/edge) 태깅

## 4. 데이터 모델

```json
// shot_grid.json (per product)
{
  "product": "PRODA",
  "wafer_diameter_mm": 300,
  "edge_exclusion_mm": 3,
  "flat_notch": "notch",
  "shot_width_mm": 26,
  "shot_height_mm": 33,
  "shot_origin_offset_mm": [0, 0],
  "teg_definitions": [
    {"id": "NW", "dx_mm": -10, "dy_mm": 12, "label": "NW corner"},
    {"id": "NE", "dx_mm": 10,  "dy_mm": 12, "label": "NE corner"},
    {"id": "C",  "dx_mm": 0,   "dy_mm": 0,  "label": "Center"},
    {"id": "SW", "dx_mm": -10, "dy_mm": -12, "label": "SW corner"},
    {"id": "SE", "dx_mm": 10,  "dy_mm": -12, "label": "SE corner"}
  ]
}
```

## 5. BE 엔드포인트 (신설)

| Endpoint | 목적 |
|---|---|
| `GET /api/waferlayout/grid?product=` | shot_grid.json 조회 |
| `PUT /api/waferlayout/grid?product=` | admin 전용 upsert |
| `GET /api/waferlayout/teg-positions?product=&lot_wf=` | wafer 상 모든 TEG 좌표 + radius 계산 결과 |
| `GET /api/waferlayout/edge-shots?product=&teg_filter=` | 선택한 TEG 로 구성되는 edge shot 인덱스 리스트 |
| `POST /api/waferlayout/match-eds-et` | ET row 각각에 대해 nearest EDS die 매핑 (body: {product, lot_wf, et_rows}) |
| `POST /api/ettime/report` 확장 | 응답 row 에 `radius_mm`, `radius_bin` 컬럼 자동 추가 |

## 6. FE 변경 (My_WaferLayout.jsx)

- Canvas 렌더러 확장:
  - shot grid overlay (회색 선)
  - TEG dot (shot 당 N 개, hover label)
  - Radius 동심원 (center/mid/edge 3단)
  - Edge shot highlight (선택 TEG 기반 실시간 재계산)
- 좌측 패널:
  - TEG 체크박스 (multi-select)
  - Edge exclusion 슬라이더 (1~5mm)
  - "Show EDS↔ET link" 토글
- 우측 패널:
  - 선택된 edge shot 의 예상 die count / TEG coverage %
  - Radius histogram (hist by TEG)

## 7. DoD (Definition of Done)

- [ ] shot_grid.json 스키마 + admin CRUD UI 동작
- [ ] 5개 이상 product 에 대해 TEG 정의 저장됨
- [ ] `/api/waferlayout/teg-positions` 가 각 TEG 의 (x_mm, y_mm, radius_mm) 리턴
- [ ] edge shot 시뮬레이션: TEG 3개 선택 시 edge shot 개수/좌표 500ms 내 응답
- [ ] EDS↔ET 매칭: L2 distance 로 최근접 die 찾고 wafer map 에 선 overlay
- [ ] ET Report 표에 `radius_mm`, `radius_bin` 자동 컬럼 + 정렬/필터
- [ ] smoke: `/api/waferlayout/grid?product=PRODA` 200, `/edge-shots` 200

## 8. 분할 handoff 후보

이 항목은 L10 하나로는 너무 커서 codex 가 받으면 4개로 쪼개 진행 권고:
- **L10.1**: shot_grid 데이터 모델 + admin CRUD + 기본 렌더 (shot grid overlay)
- **L10.2**: TEG 정의 + wafer 상 좌표/radius 계산 BE
- **L10.3**: Edge shot 시뮬레이션 FE (TEG multi-select + 실시간 계산)
- **L10.4**: EDS↔ET 매칭 + ET Report radius 컬럼 + L11 연계

## 9. L11 연결 고리

L11(설명가능 ML) 이 positional feature 로 사용할 자원:
- `radius_mm` (continuous)
- `shot_col`, `shot_row` (grid)
- `teg_id` (categorical: NW/NE/C/SW/SE...)
- `is_edge` (bool)
- `edge_distance_mm` (wafer_r - radius)

즉 L10 완료 없이 L11 의 positional feature 는 무의미. **L10 선행 필수**.
