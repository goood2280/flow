# TEG 위치 조회 (teg_map)

탭 `teg` / 라벨 "TEG 위치 조회". chip layout과 Teg_location 파일로 WF MAP geometry를 fit하고 TEG 실좌표·radius를 계산한다. 하위 뷰로 **TEG Mapfile 체크**(`TegCheck.jsx`)를 포함한다.

## Owns

- WF MAP geometry fit과 TEG 좌표/radius 계산
- vehicle별 WF MAP 표시 (shot 격자 / 업로드 그림)
- vehicle 그림 업로드·삭제
- Mapfile 체크 대상 TEG 등록과 신호등 표시
- 설비 레시피 원문 검사 — 파싱, flat 변환, Teg_location 대조
- main overlay 반영/조회/삭제

## Geometry

Auto Report의 `My_Function._wafer_circle_params`와 **같은 수학**을 쓴다.

`Chip_Radius` r은 shot 센터 ↔ wafer 원점 거리(mm)이므로 shot 격자좌표 `(chip_x_adj, chip_y_adj)`와의 관계

```
r² = kx²·(x-cx)² + ky²·(y-cy)²
```

를 `r² = A·x² + B·y² + p·x + q·y + C`로 선형화해 최소자승 fit하면 `cx=-p/2A, cy=-q/2B, kx=√A, ky=√B`.

- shot 센터 실좌표(mm) = `((x-cx)·kx, (y-cy)·ky)`
- TEG 좌하단 실좌표 = shot 센터 + `(ebeam_x, ebeam_y)·scale`
- radius = 원점과의 유클리드 거리

## 입력 파일

파일탐색기 Files 위치(DB root) 기준. 상대경로는 `db_root` 기준으로 해석한다.

| 파일 | 필수 열 | 선택 열 |
|---|---|---|
| chip layout | `Mask`(vehicle), `chip_x_adj`, `chip_y_adj`, `Chip_Radius` | — |
| `Teg_location.csv` | `vehicle`, `teg`, `ebeam_x`, `ebeam_y` | `teg_w`/`teg_h`(없으면 설정 기본 사이즈), `top_cell`(teg의 다른 이름 — Mapfile 체크 완전 일치용), `direction`(H 기본 / V는 w·h 스왑) |

열 이름은 대소문자 무관. `ebeam_x`/`ebeam_y`는 shot 센터 기준 TEG 좌하단이며 TEG는 직사각형이다.

## 설정 저장소

DB root의 `teg_location/` 폴더 (파일탐색기 위치 안).

- `teg_location/teg_map.json` — 파일 경로, ebeam 배율, wafer 반경/최외곽, TEG 기본 사이즈, vehicle별 shot 표시 방식
  - `mode: grid` — shot 안 칩 배열. `cols×rows`, 칩 크기(`chip_w`/`chip_h` mm, 0=칸 자동), 간격(`gap_x`/`gap_y` mm). 칩 블록은 shot 센터 기준 좌우·상하 대칭 배치.
  - `mode: image` — 업로드된 vehicle 그림 표시
  - `mode: none`
- `teg_location/<vehicle>.<ext>` — vehicle별 그림 파일

## TEG Mapfile 체크

설비에서 복사한 레시피 원문을 `/inspect`로 보내 두 가지를 한다.

1. 전체 Pattern의 site 좌표를 작은 WF MAP 카드로 표시 (클릭 → 확대)
2. `#teg-map`의 module 좌표를 flat 변환(`Vertical(R)` = 반시계 90° 회전 원복)한 뒤, 정답지(Teg_location의 raw ebeam 값)와 대조

판정 표시: 🟢 일치 / 🟡 확인필요(ΔX·ΔY 각 3 이내) / 🔴 불일치 / 🟣 확장 / ⚪ 미등록.

신호등 정렬 순서는 빨강 → 미등록 → 노랑 → 초록. 오프셋(flat 기본·TEG별·회전 offset)은 ⚙️ 설정의 "TEG Mapfile 체크" 섹션에서 편집한다.

> 원문 좌표 원복은 PCHK 상대좌표 → ebeam 절대좌표로 되돌리는 계산이며, PCHK는 별도 TEG 행으로 다룬다. TEG offset은 H 관점(양수=차감)이고, 중복 TEG는 `ref_seq`로 구분한다.

## Does Not Own

- WF MAP 결함 패턴 분석 — 파일탐색기/대시보드
- ET 측정값 — [et-time.md](et-time.md)

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_TegMap.jsx` |
| Mapfile 체크 뷰 | `frontend/src/pages/TegCheck.jsx` |
| 공용 줌/팬 | `frontend/src/components/ZoomPanSvg.jsx` |
| Backend router | `backend/routers/teg_map.py` |
| Geometry/좌표 로직 | `backend/core/teg_map.py` |
| Mapfile 체크 로직 | `backend/core/teg_check.py` |

## API

| Method | Path | 용도 |
|---|---|---|
| GET·PUT | `/api/teg-map/config` | 설정 (파일 경로·배율·TEG 기본크기·vehicle 표시) |
| GET | `/api/teg-map/vehicles` | layout의 vehicle 목록 |
| GET·PUT | `/api/teg-map/check-targets` | Mapfile 체크 대상 TEG (쓰기: manager) |
| GET | `/api/teg-map/map` | WF MAP payload (geometry+shots+tegs+표시설정) |
| POST | `/api/teg-map/inspect` | 설비 원문 검사 |
| GET·POST·DELETE | `/api/teg-map/main-overlay[/apply]` | main overlay |
| GET | `/api/teg-map/radius` | TEG 좌하단 shot별 radius 표 |
| GET·POST·DELETE | `/api/teg-map/image` | vehicle 그림 (POST는 multipart `file`) |

**쓰기 권한: admin 또는 page manager(`teg`).**

## Guardrails

- geometry 수학은 Auto Report와 동일하게 유지한다. 한쪽만 바꾸면 좌표가 조용히 갈라진다.
- main overlay는 같은 그룹이 이미 있으면 `exists`로 알리고, UI 확인 후 `overwrite=True`로만 덮어쓴다. 무조건 덮어쓰지 않는다.
- 렌더 상한이 있다 (`MAX_CELLS` 400,000, 격자선은 6,000 초과 시 생략). 큰 vehicle에서 이 가드를 제거하면 브라우저가 멈춘다.
- 원문(`text`)이 비면 400으로 명확히 알린다.

## Verify

```bash
git diff --check
```

```bash
python -m pytest tests/test_teg_check.py
```

```bash
cd frontend && npm run build
```
