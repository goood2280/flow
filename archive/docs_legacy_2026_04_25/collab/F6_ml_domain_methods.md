# F6 — ML 분석 플랫폼: 반도체 도메인 기법 6종 통합

> 작성: 2026-04-24 / 대상: `frontend/src/pages/My_ML.jsx` + BE 대규모 확장
> 규모: 초대형 (sub 6건 분할 필수)
> 우선순위: p1 (사용자 핵심 요구)
> **GPU 무사용 원칙** — TabICL / XGBoost CPU / LightGBM / 전통 통계 기법

---

## 1. 현재 한계

- `ML_TABLE_<prod>` 는 **wafer 1개 기준 agg form** — shot/chip 위치 정보 손실
- DVC-ET 와 YLD 가 분리 — 서로 관계 해석 약함
- KNOB split 의 **개별 gain 누적 분리** 안 됨
- Inline 값이 단일 숫자로 flatten — 공간/시간적 패턴 잃음
- 전통 반도체 YLD 분석법 (defect density, Pareto, layer baseline) 없음

## 2. 데이터 계층 재설계 (Foundation)

### 2.1 Long format 4종 (parquet)

| 테이블 | 키 | 값 | 단위 |
|---|---|---|---|
| `et_long.parquet` | (lot, wf, shot_r, shot_c, teg_id, step_seq, param) | value | TEG |
| `inline_long.parquet` | (lot, wf, shot_r, shot_c, die_r, die_c, step_seq, param) | value | chip or shot |
| `yld_long.parquet` | (lot, wf, die_r, die_c, bin_result, defect_codes) | pass/fail | die |
| `knob_long.parquet` | (lot, wf, step_id, knob_id, value) | float | wf |

### 2.2 Feature Frame (분석 직전 자동 join)

```
features(lot, wf, shot_r, shot_c) =
    KNOB_wf + INLINE_agg_shot + ET_teg + YLD_die_agg_shot
    + radius_mm, edge_distance, is_edge (H12/H13 연계)
    + step_seq 순서 feature
```

## 3. 6가지 분석 기법

### 3.1 방안 A — **Knob-Split Gain 누적 (DVC-ET 최적)**

**목적**: 각 KNOB 이 DVC 에 미치는 영향 분리

**흐름**:
```
1. Clean lot (reference) 파라미터 기준값 확보
2. KNOB split lot 각각에 대해:
     Δ_param = split_value - clean_value
3. TabICL / XGBoost 로 KNOB → Δ_param 학습
4. SHAP 로 각 KNOB 의 누적 gain 분해
5. 시각화: KNOB 별 forest plot (95% CI)
```

**왜 이게 맞나**: DVC 는 연속값 + 이상치에도 정보 있음 → tree 기반 잘 먹힘. Δ 사용으로 lot-level bias 제거.

**CPU 비용**: XGBoost 500 trees, 100K row → ~30초.

**예시 결과**:
```
KNOB_ChannelDope  ->  Vth  -0.05V (±0.01, n=42)  [significant]
KNOB_GateOx       ->  Ioff +15%   (±3%,  n=42)   [significant]
KNOB_Anneal       ->  Rc   +0.8Ω  (±2.1, n=28)   [noise]
```

### 3.2 방안 B — **YLD Robust 분석 (EDS, 전통 통합)**

**목적**: Defect 여러 원인 겹친 YLD 를 robust 하게 해석

**흐름**:
```
1. Defect sort: top 5 defect code 별로 분리
2. 각 defect 제외 시 baseline YLD 추정 (virtual removal)
3. Layer 별 defect density 학습 (공정 step → defect count)
4. Pareto 80/20 시각화
5. "이 조건에서 YLD 가 95~97% 가능성" 범위 예측 (quantile regression)
```

**Robust 포인트**:
- Outlier lot 제거 대신 **Huber loss** 사용
- Trimmed mean (10% cut) 으로 baseline
- Per-layer defect 는 별도 모델 → 교란 억제

**예시**:
```
Layer LITHO_G4:
  baseline_defect = 0.8/cm² (±0.2)
  current lot     = 2.3/cm² → 이상
  expected YLD    = 89~92% (85% confidence)
  traditional yield = D0-Poisson model: 90.1%
```

### 3.3 방안 C — **Inline Binning + Median Shift**

**목적**: KNOB → Inline → ET/YLD 중간 경로 탐색, 비모수적

**흐름**:
```
1. Inline param (예: gate_CD) 을 quantile 10 bin 으로 분할
2. 각 bin 에서 ET median / YLD median 계산
3. Δmedian_bin = bin 끝 - 시작
4. |Δmedian| / σ_total > 0.5 이면 "영향 있음"
5. 시각화: bin × ET/YLD heatmap
```

**왜 이게 맞나**: 비선형 관계 잡기 좋음, 분포 가정 없음. **CPU 수초**.

**예시**:
```
gate_CD bin (5.2 ~ 5.4 nm):   Vth shift 가장 큼
gate_CD bin (5.4 ~ 5.6 nm):   YLD 95→89%
→ "gate_CD 5.5 부근 경계"
```

### 3.4 방안 D — **Wf-level Corr × R² 스크리닝**

**목적**: 대량 파라미터 중 영향성 높은 것만 빠르게 골라내기

**흐름**:
```
1. wf aggregate: 모든 inline/KNOB → wf 단위 mean/std/max/min
2. 각 feature 와 target (ET 파라미터 or YLD) Pearson corr
3. R² = corr² > 0.3 인 것만 선정
4. 선정된 feature 에 대해서만 방안 A/C 돌림 (2-step)
```

**왜**: 100+ 파라미터 중 영향 없는 80% 는 빼버려야 해석 가능. **CPU 1초**.

**예시**: 120개 inline 중 R²>0.3 인 22개만 A 방안에 투입.

### 3.5 방안 E — **Step Grouping Feature**

**목적**: "특정 step 들을 묶어서" — 엔지니어 직관을 feature 로

**흐름**:
```
1. rulebook 으로 step_id 그룹 정의:
   "spacer_module" = [SP_ETCH, SP_STRIP, SP_CLEAN]
   "gate_stack"    = [GATE_OX, POLY_DEP, GATE_ETCH]
2. 그룹별 aggregate (mean, sum, max_delta)
3. 그룹 feature 를 다른 방안에 투입
4. SHAP 결과도 "그룹 기여도" 로 해석
```

**UI**: admin 이 rulebook 설정 → 모든 ML run 에 자동 적용

### 3.7 방안 G — **KNOB Path Optimization (Multi-Objective)** ⭐ 사용자 핵심

**목적**: "가장 좋은 KNOB 조합 찾기" — 성능·수율·둘 다 최적점

**흐름**:
```
1. Surrogate model 학습: KNOB → (Y_perf, Y_yield) (XGBoost/LightGBM regression)
2. 탐색 알고리즘 (CPU):
   - Optuna TPE (Bayesian like) — 20~100 trial
   - scikit-optimize gp_minimize
   - 또는 grid search (KNOB 차원 낮을 때)
3. 목표 옵션:
   (a) Single: maximize Y_perf
   (b) Single: maximize Y_yield
   (c) Multi-Objective: Pareto front (perf × yield)
4. 출력:
   - 추천 Top-5 KNOB 조합 + 예측 Y (95% CI)
   - Pareto plot (yield vs perf)
   - "현재 lot 대비 Δ" counterfactual
   - 추천 레시피 텍스트 ("ChannelDope 4.2→4.4, Anneal 15s→17s")
```

**왜 CPU OK**: Optuna / sklearn CPU 완전 지원. KNOB 10~20 차원이면 TPE 100 trial 로 충분.

**예시 출력**:
```
현재 운영점: Y_perf=0.82, Y_yield=91%
추천 Top-3:
  #1  ChannelDope+0.15, Anneal+2s   → Y_perf=0.87, Y_yield=93% (CI ±2%)
  #2  GateOx-0.3Å, SpacerWidth+1nm  → Y_perf=0.91, Y_yield=89% (perf 강조)
  #3  (conservative) Anneal+1s only → Y_perf=0.84, Y_yield=95% (yield 강조)

Pareto frontier: 점 3개 중 선택 가능
```

**검증**: 실제 lot 돌려서 예측 오차 측정 (후속)

### 3.6 방안 F — **Position-Aware (shot/chip/edge)**

**목적**: L10 / L11 과 연동. edge vs center 구분 + radius 영향

**흐름**:
```
1. 각 측정 row 에 radius_mm / is_edge 자동 부여 (L10 API)
2. radius bin 3 (center/mid/edge) × 분석기법 cross-product
3. "edge 에서만 Ioff 튀는 chamber-X" 식 분리
4. Wafer map SHAP overlay (L11 연계)
```

## 4. UI 통합 (My_ML.jsx 확장)

### 4.1 상단: 분석 종류 선택
```
[ ] A. KNOB Gain (DVC-ET)
[ ] B. YLD Robust (EDS)
[ ] C. Inline Binning
[ ] D. Corr R² 스크리닝
[ ] E. Step Grouping
[ ] F. Position-Aware
```
체크박스 다중선택 → 파이프라인 조합.

### 4.2 결과 탭
- **Summary**: 선택된 방안별 핵심 1줄
- **Forest Plot** (A): KNOB 별 CI
- **Pareto** (B): defect 기여도
- **Bin Heatmap** (C): inline × target
- **R² 랭킹** (D): 영향 인자 표
- **Group Bar** (E): 그룹별 SHAP
- **Wafer Map** (F): SHAP overlay

### 4.3 한 줄 해설 박스 (엔지니어용)
```
"ch.2 에서 gate_CD 5.5nm 부근 경계. Edge 에서만 Ioff +0.3σ. 
 Spacer 모듈 기여도 45% — Anneal 시간 확인 권장."
```

## 5. BE 엔드포인트

| Endpoint | 목적 |
|---|---|
| `POST /api/ml/build-feature-frame` | 4 long table join → feature frame (캐시) |
| `POST /api/ml/run/knob-gain` | 방안 A |
| `POST /api/ml/run/yld-robust` | 방안 B |
| `POST /api/ml/run/inline-binning` | 방안 C |
| `POST /api/ml/run/corr-screen` | 방안 D |
| `POST /api/ml/step-groups` | 방안 E rulebook CRUD |
| `POST /api/ml/run/position-aware` | 방안 F |
| `GET /api/ml/explain/{run_id}` | SHAP + plot data |

## 6. 선행 준비

- **shot/chip 좌표**: H13 (WF Layout 파라미터 시스템) 완료 필수
- **TEG 좌표**: H12 완료 필요
- **Long format parquet**: 데이터 ETL 파이프라인 신설
- **CPU 파이프라인**: XGBoost, LightGBM, TabICL, statsmodels

## 7. 분할 권고 (7 sub-handoff)

| Sub | 제목 | 공수 |
|---|---|---|
| F6.1 | 데이터 계층 (4 long table ETL + feature frame) | 1주 |
| F6.2 | 방안 A KNOB Gain + TabICL 인프라 | 1주 |
| F6.3 | 방안 B YLD Robust + 전통 yield model (D0-Poisson, Pareto) | 1주 |
| F6.4 | 방안 C+D Inline binning + Corr R² 스크리닝 | 4일 |
| F6.5 | 방안 E Step Grouping rulebook | 3일 |
| F6.6 | 방안 F Position-Aware + wafer SHAP overlay | 4일 |
| **F6.7** | **방안 G KNOB Path Optimization (Optuna/Pareto)** ⭐ | 1주 |

## 8. DoD 최소 집합

- [ ] 4 long parquet 스키마 BE 준수
- [ ] 6 분석 방안 각각 POST endpoint + JSON 응답
- [ ] My_ML 에 분석 종류 선택 UI
- [ ] 각 방안 결과 시각화 1개씩
- [ ] CPU-only 동작 (GPU dep 없음)
- [ ] 해설 박스 LLM or rule 기반 문장 생성
- [ ] smoke: /api/ml/run/knob-gain POST 200

## 9. 성공 기준 — 사용자 핵심 두 가지

### 9.1 원인 규명 (Root Cause)
엔지니어 질문: "이 lot 의 YLD 왜 떨어졌지?" → 10분 내 답 도출
- D 로 후보 3개 feature 선별
- A 로 KNOB 기여도 분해
- B 로 defect 메커니즘 확인
- F 로 edge/center 패턴 확인
- 결론: "spacer 모듈 anneal + edge chamber-2 복합 원인"

### 9.2 Y 예측 + Optimal Path (방안 G)
엔지니어 질문: "다음 실험 어떤 KNOB 으로 가야 yield + perf 좋아지지?"
- 현재 운영점 surrogate 예측: Y_perf, Y_yield (CI 포함)
- Pareto front 탐색 → 추천 Top-3 조합
- 선택 옵션:
  - "성능 우선" → perf 최대
  - "수율 우선" → yield 최대
  - "균형" → Pareto front 중간
- counterfactual: "ChannelDope +0.15 면 perf +5%p, yield +2%p"

### 9.3 End-to-End 통합 시나리오
```
[원인 규명]                    [예측]
lot A 가 yield 떨어짐     →    lot B 예상 yield 몇%?
  D → A → B → F                 surrogate + CI
  "chamber-2 + anneal 이슈"     "92%±2%, edge 영향"
                                        ↓
                              [최적화 G]
                          "Anneal +2s + ChannelDope +0.15
                           → 94±1.5% 예상"
                           (Pareto top)
```

F6 완성 시 엔지니어는 **한 페이지에서 원인 → 예측 → 최적화** 까지 일관 흐름.
