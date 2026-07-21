# F5 — ET Report 완전 구현 (Lot Scoreboard · Gantt · 검색 · 다운로드 이력)

> 작성: 2026-04-24 / 대상: `frontend/src/pages/My_ETTime.jsx` (또는 신규 My_ETReport.jsx)
> 규모: 대형 (FE 대수술 + BE API 5종 추가 + storage + admin 로그)
> 우선순위: p1 (사용자 즉시 요구)

---

## 1. 사용자 원문 요구 (2026-04-24)

> "ET Report는 갈길이 아직 먼듯 LOT이 측정되면 어느스텝에 어떤 step_seq로 찍히거나 했는지에 대해서 일목요연하게 나오고 그 랏 누르면 Lot의 scoreboard와 이상있는 값들에 대한 summar 나오고 총시간 step_seq별 시간 나오고 간트도 보이고 하면 좋겠음. 그리고 그 랏 옆에 레포트 생성한거 붙어있고 LOT별 검색가능해서 다운로드 되게 하고 이 다운로드 기록도 다 남아서 admin은 볼 수 있어야하고"

## 2. 현재 한계

- `/api/ettime/report` 는 step_seq 리스트만 얇게 리턴
- Lot 단위 drill-down 이 없음
- Scoreboard/이상값 summary 없음
- Gantt 없음
- 다운로드 기능 없음
- admin 다운로드 로그 없음

## 3. 화면 구성 (신규 또는 확장)

### 3.1 상단: Lot 검색·필터
- 검색창: root_lot_id / fab_lot_id / product
- 기간 필터: 최근 7일 / 30일 / 커스텀
- 결과 정렬: 최근 측정 순

### 3.2 리스트: Lot row (일목요연)
| 컬럼 | 내용 |
|---|---|
| root_lot_id | 클릭 시 3.3 drill-down |
| product | |
| 측정 step 목록 | step_id 1, step_id 2, ... (칩) |
| step_seq 범위 | min~max |
| 최종 측정 시각 | |
| 총 point | point 수 합계 |
| 이상값 카운트 | spec-out 파라미터 수 (경고 색) |
| 리포트 | [PDF 다운로드] [CSV 다운로드] 버튼 |

### 3.3 Drill-down (Lot 클릭 시 또는 side panel)
- **Scoreboard**: 측정 파라미터별 pass/warn/fail 비율 (색 gradient)
- **이상값 Summary**: spec-out top 10 list (param · value · spec · Δ)
- **총 시간**: 첫 측정 ~ 마지막 측정
- **step_seq 별 시간 bar**: step_seq 1~N 각 소요시간 → 병목 판별
- **Gantt**: step × 시간 축 (step_seq 순서대로 왼→오, 각 seq 의 duration 표시)

### 3.4 리포트 생성
- Lot row 의 [PDF 다운로드] 클릭 시:
  - BE 에서 PDF 생성 (Scoreboard + 이상값 summary + Gantt 캡처)
  - 파일명: `ET_Report_<root_lot_id>_<YYYYMMDD>.pdf`
  - 다운로드 로그 자동 기록 (user, lot, time, file_size)
- [CSV 다운로드]: ET raw row 전체

### 3.5 Admin 다운로드 이력
- Admin 페이지에 "ET Report Downloads" 서브탭
- 컬럼: 시각 · 사용자 · Lot · 파일유형 · 파일크기
- 최근 500건 + 기간 필터

## 4. BE 엔드포인트

| Endpoint | 목적 |
|---|---|
| `GET /api/ettime/lots?search=&days=30` | Lot 리스트 (3.2) |
| `GET /api/ettime/lot/{root_lot_id}` | Scoreboard + 이상값 + 시간 + gantt_points (3.3) |
| `POST /api/ettime/report/pdf` | PDF 생성 + 다운로드 로그 기록 |
| `POST /api/ettime/report/csv` | CSV 생성 + 다운로드 로그 기록 |
| `GET /api/admin/ettime/download-log?limit=500` | admin 다운로드 이력 |

## 5. PDF 생성

- backend 에 reportlab 또는 weasyprint (이미 있다면 재사용)
- 템플릿: `backend/templates/et_report.html` → weasyprint
- 또는 matplotlib figure → PDF 이미지 조합

## 6. DoD

- [ ] Lot 리스트 검색/필터 동작, 100+ lot 1s 내 응답
- [ ] Lot 클릭 → scoreboard + 이상값 + gantt drill-down 렌더
- [ ] PDF 다운로드 성공 (파일명 정합)
- [ ] CSV 다운로드 성공
- [ ] 다운로드 시 `admin_settings.ettime_download_log` 에 row append
- [ ] Admin 페이지에 ET Report Downloads 서브탭 노출
- [ ] smoke: /api/ettime/lots 200, /api/ettime/lot/{id} 200

## 7. 분할 권고

- F5.1: Lot 리스트 + 검색 (2일)
- F5.2: Drill-down scoreboard + 이상값 summary (2일)
- F5.3: Gantt + step_seq 시간 시각화 (2일)
- F5.4: PDF/CSV 생성 + 다운로드 로그 + admin 탭 (3일)
