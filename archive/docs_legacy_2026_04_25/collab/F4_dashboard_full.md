# F4 — Dashboard 실제 차트 + Fab Progress 속도·정체 가시화

> 작성: 2026-04-24 / 대상: `frontend/src/pages/My_Dashboard.jsx`
> 규모: 중형 (FE 재구성 + BE 지표 API 확장)
> 우선순위: p1 (사용자 즉시 요구)

---

## 1. 사용자 원문 요구 (2026-04-24)

> "대시보드에 내가 제시했던 차트들이 하나도 없음 (...) Fab progress Lot별 진행 차트 보이는거는 좋은데 빠른지 느린지 전혀 파악이 안됨 지금 7주일 평균 30일 평균 TAT DPML 이런게 얼마나 되고 이 랏은 어떻게 진행중이다가 파악이 안됨 그리고 오래 멈춰있거나 진행이 안되는 자재에 대해서는 확인이 가능해야함"

## 2. 문제 정리

1. **사용자 제시 차트 미구현** — 과거 논의된 차트 목록이 실제 페이지에 없음 (도메인 요구)
2. **Fab Progress 속도 판단 불가** — 현재 Lot 진행 막대만 있고 "빠른가/느린가" 신호 없음
3. **기준 지표 부재** — 전사 7일/30일 평균 TAT·DPML 없음
4. **정체 자재 미가시화** — 오래 멈춘 lot 이 이상하게 빠진 채 진행되는 lot 속에 섞임

## 3. 필요한 차트 (사용자 제시 추정치 — 사용자 확정 필요)

| ID | 차트 | 목적 |
|---|---|---|
| trend_et | ET 파라미터 일별 trend (Ioff/Ion/Vth/Rc) | 공정 드리프트 감지 |
| knob_mix | KNOB split 비율 pie | 실험 balance 확인 |
| distribution | 특정 파라미터 분포 histogram | outlier 분포 감지 |
| yield_trend | wafer/lot yield 일별 | 품질 트렌드 |
| fab_progress | Lot 별 진행 bar + 속도 뱃지 | 스피드·정체 판단 |
| stuck_lots | 정체 자재 리스트 | 개입 대상 발견 |

*(사용자가 원본 차트 목록 알려주면 더 정확히 반영. 일단 위 6개로 초안 착수)*

## 4. Fab Progress 고도화 스펙

### 4.1 상단 KPI 카드 (신규)
- 7일 평균 TAT (hour)
- 30일 평균 TAT
- 7일 DPML (Defects Per Million Lot) — 아니면 단순 문제 lot 비율
- 30일 DPML
- 현재 재공 lot 수 (in-progress)
- 정체 lot 수 (last_step 변경 ≥ 24h)

### 4.2 Lot 별 row
| 컬럼 | 내용 |
|---|---|
| root_lot_id | 클릭 시 SplitTable drill-down |
| 진행률 % | 현재 step_seq / 예상 total step_seq |
| 현재 step | step_id + step_seq |
| TAT | 시작~현재 (hour) |
| 속도 뱃지 | 🟢 빠름 / ⚪ 평균 / 🟠 느림 / 🔴 정체 |
| last_event | 마지막 step 이동 시각 |
| 정체 시간 | now - last_event (h) |

### 4.3 속도 판정 로직
- 빠름: 현재 lot 의 TAT/progress 가 30일 평균 대비 -20% 이상
- 평균: ±20% 이내
- 느림: 평균 대비 +20% 초과
- 정체: last_event 이 24h 이상 변화 없음

### 4.4 필터
- 상단 토글: [전체] [정체만] [느림+정체] [빠름만]
- 제품 필터 (기존)
- 기간 필터 (7d/30d/90d)

## 5. BE 엔드포인트

| Endpoint | 변경 |
|---|---|
| `GET /api/dashboard/summary` | 7일/30일 TAT·DPML KPI |
| `GET /api/dashboard/fab-progress` | Lot 별 진행 + 속도 판정 + 정체 시간 |
| `GET /api/dashboard/stuck-lots?hours=24` | 정체 자재 전용 리스트 |
| `GET /api/dashboard/charts/{id}` | chart_id 별 데이터 (trend_et/knob_mix 등) |

## 6. DoD

- [ ] 상단 KPI 카드 6개 (TAT 7d/30d, DPML 7d/30d, 재공 lot, 정체 lot)
- [ ] Lot 별 row 에 속도 뱃지 (🟢⚪🟠🔴)
- [ ] 정체 lot 필터 토글 동작
- [ ] 최소 4개 차트 (trend_et, knob_mix, distribution, yield_trend) 실제 데이터 표시
- [ ] H8 적용 — 'chart=' 같은 내부 문자열 일반 view 에 노출 없음
- [ ] npm run build 통과
- [ ] smoke: /api/dashboard/summary 200

## 7. 분할 권고

codex 가 한 번에 모두 하기 어려우면:
- F4.1: KPI 카드 + fab-progress 속도 판정 (3일)
- F4.2: 차트 4개 실제 데이터 연결 (2일)
- F4.3: 정체 자재 전용 뷰 (1일)
