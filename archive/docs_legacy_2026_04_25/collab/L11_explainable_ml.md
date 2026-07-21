# L11 — 설명가능 ML (Positional · Step-aware · SHAP 기반)

> 작성: 2026-04-24 / 대상 페이지: `frontend/src/pages/My_ML.jsx`
> 규모: 대형 (FE 확장 + BE 파이프라인 신설). 2~3 sprint 예상
> 우선순위: p2 (장기 백로그 L11 로 승격)
> **선행**: L10 (WF Layout 고도화) 가 먼저 완료되어야 positional feature 가 정확함

---

## 1. 배경 · 현재 한계

현재 `/api/ml/*` 는 TabICL/XGBoost/LightGBM 을 돌리고 SHAP 결과를 뷰로 보여주지만,

1. **Positional feature 부재**: die/TEG 좌표, radius, edge 여부가 feature 에 들어가지 않음 → "edge 에서 왜 Ioff 가 튀나?" 같은 질문에 답 못함
2. **Step 순서 반영 부재**: step_id 는 카테고리일 뿐, 공정 **순서** 나 **직전 step 과의 gap** 이 feature 가 아님 → 인과 방향 추론 빈약
3. **설명 시각화 얇음**: SHAP bar 는 있으나 wafer map 에 overlay 안 됨, interaction 뷰 없음

반도체 엔지니어가 ML 결과를 실제 의사결정에 쓸 수준이 되려면 "왜 이렇게 예측하는가" 를 **공간(wafer 위)** 과 **시간(공정 순서)** 양 축으로 설명해야 한다.

## 2. Feature 확장

| 분류 | feature | 원천 | 비고 |
|---|---|---|---|
| Positional | `radius_mm`, `shot_col`, `shot_row`, `teg_id`, `is_edge`, `edge_distance_mm` | L10 API | 핵심 신규 |
| Temporal | `step_seq`, `step_id`, `days_since_prev_step`, `step_order_in_flow` | core/lot_step.py | step_seq 는 이미 있음 |
| Categorical | `EQP_CHAMBER`, `DVC`, `recipe_id` | FAB long | 기존 + SHAP 친화 인코딩 |
| Measurement | Rc, Rch, Ioff, Ion, Vth, lkg 등 | ET long | 기존 |
| Interaction | `step_seq × radius_bin`, `chamber × edge`, `teg_id × step_id` | 파생 | SHAP interaction 대상 |

## 3. 모델 · 파이프라인

- **모델**: XGBoost (GPU) + LightGBM (CPU), TabICL 은 small sample 보조
- **타겟**:
  - 회귀: ET 파라미터 실측값 (예: `Ioff_V1`)
  - 분류: spec-in / spec-out binary (DVC 방향성 기준 dvc-curator 자문)
- **CV**: GroupKFold by `root_lot_id` (lot 누수 방지)
- **Hyperparam**: Optuna 50 trial (learning_rate, max_depth, reg_alpha)

## 4. 설명 레이어

### 4.1 전역 설명
- SHAP Beeswarm: top 30 feature
- SHAP Dependence: `radius_mm` 축에 Ioff 기여도 (edge 에서 튀는 지점 찾기)
- Feature interaction heatmap: `step_seq × teg_id` SHAP 평균

### 4.2 인스턴스 설명
- SHAP Waterfall: 특정 row 예측이 왜 그 값이 됐는지
- Counter-factual: "이 row 의 DVC 를 반대로 바꾸면 예측이 어떻게 변하나"

### 4.3 공간 설명 (신규 — 핵심)
- **Wafer map SHAP overlay**: die/TEG 별 SHAP 값을 radius-aware 색상으로 wafer 캔버스에 겹쳐 그림
- Edge/Center contribution 비교 bar
- Shot 단위 SHAP 집계 (shot 평균)

### 4.4 시간 설명 (신규)
- Step-wise contribution bar: step_seq 1~N 순서대로 누적 SHAP
- 인과 방향성 (causal-analyst 자문): "앞 공정 → 뒤 공정 강함, 반대 약함" 원칙을 엔진 레벨로 강제

## 5. BE 엔드포인트 (확장)

| Endpoint | 변경 |
|---|---|
| `POST /api/ml/run` | body 에 `feature_groups=["positional","temporal","interaction"]` 추가, L10 API 내부 호출로 positional 자동 보강 |
| `GET /api/ml/explain/{run_id}` | 기존 SHAP bar 외 wafer_overlay / step_contrib / interaction_heatmap 응답 섹션 추가 |
| `POST /api/ml/counterfactual` | 신설. row + perturbation spec → 예측 변화 |

## 6. FE 변경 (My_ML.jsx)

- 상단 feature group 체크박스 (Positional / Temporal / Interaction)
- 결과 탭 분리:
  - Summary (기존)
  - Wafer SHAP (신규, canvas overlay)
  - Step Contribution (신규, horizontal bar)
  - Interaction (신규, heatmap)
  - Counterfactual (신규, per-row)
- 각 탭 "엔지니어에게 읽어주는 한국어 해설" 박스 (LLM assisted, short)

## 7. DoD

- [ ] L10 API 호출로 positional feature 6종 자동 포함
- [ ] step_seq 기반 순서 feature 2종 (`step_seq`, `days_since_prev_step`) 포함
- [ ] GroupKFold by root_lot_id 적용, CV metric 기록
- [ ] SHAP wafer overlay 가 edge 의 Ioff 이상 기여 케이스에서 시각적으로 확인 가능
- [ ] Counter-factual API 동작 + FE tab 표시
- [ ] causal-analyst 자문: 공정 방향성 규칙이 feature importance ranking 에 soft penalty 로 반영
- [ ] smoke: `/api/ml/run` 200, `/api/ml/explain/{id}` 응답에 `wafer_overlay`/`step_contrib` 키 존재

## 8. 분할 handoff 후보

- **L11.1**: Feature 확장 (positional + temporal 통합, L10 API 소비)
- **L11.2**: 설명 레이어 BE (wafer overlay / step_contrib / interaction / counterfactual)
- **L11.3**: FE 5 탭 (Summary + Wafer SHAP + Step + Interaction + Counterfactual)
- **L11.4**: LLM 해설 + causal soft-penalty 튜닝

## 9. 성공 지표

- 엔지니어가 ML 결과 보고 "edge 에서 chamber-X 가 Ioff 를 0.3σ 만큼 끌어올린다" 같은 문장을 30 초 안에 도출 가능
- 같은 실험에 대해 TabICL 과 XGBoost 의 SHAP top 10 이 70% 이상 겹침 (reproducibility)
- v9.0.2 ML 만족도 설문 기준 6.0 → 8.5 목표
