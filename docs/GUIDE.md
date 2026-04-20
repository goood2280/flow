# FabCanvas.ai — Operator Guide

> 페이지별 "언제 쓰는가 + 기본 흐름" · 운영자 체크리스트 · 흔한 문제.
> 관련 문서: `ARCHITECTURE.md`(시스템) · `DOMAIN.md`(공정 지식) · `../FabCanvas_domain.txt`(전체 원본).

## 페이지별 용도

### Home (`/`)
- **언제**: 세션 시작. 최신 changelog 확인, unread 공지/답장 팝업 확인.
- **흐름**: 진입 즉시 카드/changelog 가 렌더(로딩 게이트 없음) → version.json 은 비동기 수신.

### FileBrowser (`/filebrowser`)
- **언제**: 원본 CSV/parquet 가 실제로 올라왔는지, root 경로가 제대로 잡혔는지 확인.
- **흐름**: DB roots 트리 → 파일 더블클릭 프리뷰. S3 sync 버튼으로 단일 프로필 기준 동기화.

### TableMap (`/tablemap`)
- **언제**: 테이블 관계/그룹을 시각적으로 등록·수정, 작은 lookup 테이블 편집.
- **흐름**: 노드 그래프에서 관계 드래그 → 하단 엑셀 시트에서 셀 편집(Tab/Enter 이동) → Save 시 `<db_root>/<name>.csv` 로 퍼시스트.

### SplitTable (`/splittable`)
- **언제**: 실험 plan 작성, 제품/PPID/knob/feature 조합 정의.
- **흐름**: 상단 드롭다운(출처: `data/Base/_uniques.json`) → root_lot × wafer sticky 그리드에서 값 입력.

### Dashboard (`/dashboard`)
- **언제**: 운영 KPI · SPC 스타일 트렌드 · 박스플롯을 한 화면에 조합.
- **흐름**: Chart Config 에서 x_col/y_expr 지정 → Advanced → exclude_null · 장비_챔버 컬러링. auto-refresh 주기는 우하단 admin 톱니에서 조절.

### ETTime (`/ettime`)
- **언제**: ET(전기 테스트) 결과 배치가 언제 들어왔는지 추적, 파라미터별 타임라인.
- **흐름**: 제품 선택 → 파라미터(Rc/Ion/Vth 등) → 시간축. DVC 룰 방향성이 주석으로 자동 표시.

### ML (`/ml`)
- **언제**: Y 값(예: Ioff, Rc) 영향 feature 순위를 TabICL/XGBoost/LightGBM 로 뽑고 SHAP 로 설명.
- **흐름**: target 선택 → 학습 → SHAP importance → area 별 그룹핑 결과 확인(신뢰도 등급 자동 부여).

### Tracker (`/tracker`)
- **언제**: 개발 이슈 · 사고 · 실험 action item 관리.
- **흐름**: 이슈 생성 → 카테고리(색상 지정) → List/Gantt 양방향. Gantt 제목 클릭 = 상세 뷰.

### Messages (`/messages`)
- **언제**: Admin ↔ User 1:1 문의, 전체 공지.
- **흐름**: Inbox → 스레드 클릭 → 답장. 공지는 admin 전용 생성.

### Admin (`/admin`)
- **언제**: 유저 승인/탭 권한, AWS profile, data roots override, 알림/메시지 inbox.
- **흐름**: 탭 전환만으로 즉시 반응(keep-mounted). 변경 후 Save → toast 확인.

## 운영자 체크리스트

### 새 분기 시작
- [ ] `data/Base/_uniques.json` 에 신규 제품/PPID 반영 확인 → SplitTable 드롭다운 갱신
- [ ] `data/Base/matching_step.csv` 에 신규 step_id ↔ func_step ↔ area 3열 매핑
- [ ] 어댑터 위자드로 신규 parquet 스키마 스캔 → 역할 매핑 저장
- [ ] Dashboard auto-refresh 주기를 분기 트래픽에 맞게 admin 톱니에서 조정

### ET 배치 수신
- [ ] ETTime 페이지에서 해당 배치 타임스탬프 표시 확인
- [ ] Dashboard 주요 파라미터(Rc, Ion, Ioff, Vth) 트렌드 급변 여부 확인
- [ ] DVC 룰 경고 발생 시 Tracker 에 이슈 생성(카테고리: ET-Alert)

### ML 리런
- [ ] ML 페이지에서 target 선택 → 학습 성공 로그 확인
- [ ] SHAP importance 상위 step 이 area 매트릭스상 "신뢰도 높음/중간" 인지 확인
- [ ] "의심(역방향)" 플래그가 붙은 feature 는 리포트에서 제외하거나 사유 주석

### 사고 조사
- [ ] Tracker 에 이슈 등록(카테고리 + 우선순위 색상)
- [ ] 영향 받은 lot/wafer 를 SplitTable 로 범위 지정
- [ ] ML 로 원인 후보 feature 도출 → DOMAIN.md 인과 매트릭스로 필터링
- [ ] 결론 이슈 코멘트에 근거 링크(Dashboard 차트 URL + ML run id) 첨부

## 흔한 문제

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| 페이지 새로고침 시 404(SPA 경로) | 백엔드가 구 dist 참조 중 | `npm run build` 후 `uvicorn` 재시작 |
| TableMap/SplitTable 매칭 미스 | `matching_step.csv` 3-tier(exact/alias/substring) 불충분 | Admin 어댑터 위자드에서 alias/substring 추가 저장 |
| FileBrowser/Dashboard 데이터 미표시 | data roots 미해석 | `/admin` → Monitor 탭에서 `db_root`/`base_root` 확인 → env 또는 `admin_settings.json > data_roots` 수정 |
| Dashboard 차트에 (null) 남음 | exclude_null 꺼짐 | Chart Config → Advanced → exclude_null 체크 |
| 종↔Messages unread 배지 불일치 | poll 캐시 | 브라우저 탭 전환 또는 `hol:notif-refresh` 이벤트 대기(최대 30~45초) |
| AWS sync 실패 (endpoint_url 누락) | profile 미설정 | Admin AWS Config 에 default profile 등록 → FileBrowser 재시도 |
| ML 결과가 물리적으로 말이 안 됨 | 통계적 허위 상관 | DOMAIN.md 인과 매트릭스 확인 후 "역방향/거리 멀고 전사 없음" 플래그 필터 적용 |

## 빠른 명령어

```bash
# 서버 기동 (개발)
cd backend && uvicorn app:app --host 0.0.0.0 --port 8080

# 프론트 빌드
cd frontend && npm run build

# 재시작 (prod)
pkill -f "uvicorn app:app" || true
cd backend && uvicorn app:app --host 0.0.0.0 --port 8080 &
# 브라우저 Ctrl+Shift+R
```

초기 관리자: `hol / hol12345!` (브랜드 교체 후에도 유지).

## 코드 규칙 (핵심만)

### Python / 백엔드
- Polars 버전 편차 대비 `_STR = getattr(pl, "Utf8", None) or getattr(pl, "String", pl.Object)` 가드.
- Cast 는 **per-column** (bulk `df.cast()` 금지).
- JSON 저장은 `core.utils.save_json` / `load_json` 경유 (atomic write + indent 통일).
- 시스템 자원 조회는 `/proc/*` + cgroup (psutil 금지).

### JSX / 프론트엔드
- `App.jsx` 는 **static import** 만 (Vite lazy import 불안정).
- 네트워크 호출은 `src/lib/api.js` 의 `sf` / `postJson` (HTML 에러 페이지 방어).
- JSX ternary else 가 multi-child 면 `<>...</>` fragment.

### CSS / 테마
- Accent: `#f97316` (orange-500) / `#ea580c` (orange-600).
- Dark bg `#1a1a1a`, light bg `#fafafa`, 헤더는 monospace (JetBrains Mono).

## 버그 리포트 템플릿

```
FabCanvas.ai v8.3.x 수정 요청.

[재현]
1. ...
2. ...
[기대 동작] ...
[실제 동작] ...
[스크린샷/로그] ...
```

v8.1.5 당시의 setup_v8 파트 교체 / update_vXXX.py 배포 흐름은 [`reference/v8_1_5_UPDATE_GUIDE.md`](reference/v8_1_5_UPDATE_GUIDE.md) · [`reference/v8_1_5_WEB_GUIDE.md`](reference/v8_1_5_WEB_GUIDE.md) 에 히스토리로 남아있다 (현행 배포와는 다름).
