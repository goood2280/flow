# FabCanvas.ai — Domain Slice (2nm GAA Nanosheet)

> 전체 도메인 원본은 `../FabCanvas_domain.txt`. 본 문서는 에이전트가 spot-reference 용으로 쓰는 **요약 슬라이스**.
> 근거 수준: 학계 공개 기반(IEEE/VLSI/IEDM 류). 사내 기밀 공정 파라미터는 포함하지 않음.

## 1. 2nm GAA 공정 흐름 — 모듈 · Area 태그 · 대표 Step

FEOL → MOL → BEOL 순(공정 거리순). area 태그는 `data/Base/matching_step.csv` 의 area 컬럼 값과 정합.

| 순서 | 모듈 | area 태그 | 대표 step (학계 공개 기반) |
| --- | --- | --- | --- |
| 1 | Shallow Trench Isolation | STI | STI etch, liner ox, HDP/HARP fill, CMP |
| 2 | Well / VT Implant | WELL_VT | N-well / P-well implant, VT adjust implant |
| 3 | Gate Patterning (dummy, nanosheet release) | PC | Nanosheet stack epi, dummy gate patterning, inner spacer, NS release |
| 4 | Gate (HKMG) | GATE | Interfacial ox, High-K(HfO₂) ALD, work-function metal, gate fill, CMP |
| 5 | Spacer | SPACER | Main spacer dep/etch, offset spacer |
| 6 | S/D Epitaxy | SD_EPI | SiP / SiGe:B selective epi, cap |
| 7 | Middle of Line | MOL | TS/TD contact, silicide, CA/CB via, contact CMP |
| 8 | BEOL (Metal 1 … Metal n) | BEOL_M1 … BEOL_Mn | Via etch, Cu/Co dual damascene, barrier/seed, ECP, CMP (per metal layer) |

참고: 모듈 구분과 용어는 GAA Nanosheet 공정에 대한 학계 공개 자료에 기반. 구체적인 레시피/차원은 포함하지 않는다.

## 2. DVC 파라미터 방향성 테이블

단일 출처: `data/Base/dvc_rulebook.csv` (`param, direction, unit, spec_lo, spec_hi, target, note`).

| param | direction | 해석 |
| --- | --- | --- |
| Rc | lower_is_better | 접촉저항. 낮을수록 소자 성능 향상 |
| Rch | target_centered | 채널저항. 목표값 대비 편차가 중요 |
| ACint | lower_is_better | 기생 인터커넥트 커패시턴스. 낮을수록 속도 개선 |
| AChw | context_dependent | 핫와이어 AC. 구조/목적에 따라 해석 다름 |
| Vth (n/p) | target_centered | 문턱전압. 설계 타겟 대비 편차가 중요 |
| Ion (n/p) | higher_is_better | 온전류. 높을수록 구동력 좋음 |
| Ioff (n/p) | lower_is_better | 오프전류. 낮을수록 누설 적음 |
| lkg | lower_is_better | 누설전류. 낮을수록 좋음 |

활용: SPC 에서 방향성 위배 추세가 감지되면 자동 경고. ML 영향도 + 방향성 조합으로 "악화 원인 후보" 해석 주석이 붙는다.

## 3. 인과 방향성 매트릭스 (핵심 규칙)

| 원칙 | 강도 |
| --- | --- |
| 앞 공정 step → 직후 step | 강함 |
| 앞 공정 step → 2~3 단계 뒤 | 중간(감소) |
| 뒤 공정 → 앞 공정 | 거의 없음(역방향) |
| 형상 전사(shape transfer) 경로 존재 | 거리 멀어도 영향 가능 |

대표적 "형상 전사" 예외:

- **PC poly removal → BEOL metal fill**: Gate 영역에서 남은 프로파일이 훨씬 뒤 metal fill 때 전사.
- 따라서 `PC → far BEOL` 은 원칙상 약함이지만, 전사 경로가 명시되면 중간으로 승격.

영향도 샘플(도메인 원본 [7] 축약):

| Source → Target | 영향도 | 근거 |
| --- | --- | --- |
| PC → Gate | 강함 | 직전 공정 |
| Gate → MOL Contact | 강함 | 형상 전사 |
| Gate → BEOL M1 | 중간 | 간접 전사 |
| PC → SD Epi | 중간 | 2단계 앞 |
| PC → far BEOL | 약함 | 거리 멀고 전사 경로 불분명 |
| BEOL → Gate | 거의 없음 | 역방향 |
| BEOL → PC | 거의 없음 | 역방향 |
| S/D Epi → MOL | 강함 | 직전 공정 |
| MOL → BEOL M1 | 강함 | 직전 공정 |

신뢰도 등급 매핑:

- **높음**: 같은 area · 직전 area · 형상 전사 경로 명시
- **중간**: 2~3 단계 앞 · 간접 영향 가능
- **낮음**: 거리 멀고 전사 경로 없음
- **의심**: 역방향(뒤 → 앞)

ML 후처리 예: Y=PCCA lkg(PC area) 에 대해 X=far BEOL step 이 잡히면 `BEOL → PC = 거의없음` → "의심" 플래그. 반면 Gate step 이 원인이면 `Gate → PC 인접` → "높음".

## 4. 측정 · SPC 카테고리

| 카테고리 | 단위 | 타이밍 | 주 사용 페이지 |
| --- | --- | --- | --- |
| Inline 측정 | wafer / shot | 공정 중간 (CD/OCD/thickness/overlay 등) | Dashboard, ML |
| ET (Electrical Test) | wafer 레벨 | FEOL/MOL 후 | ETTime, Dashboard |
| EDS (Electrical Die Sort) | die 레벨 | 공정 후반 | Dashboard (die-level), Wafer Map |

SPC(개발 단계용) 기본 뷰:

- Trend % change
- Historic High/Low
- Spec Out(룰북 spec_lo/spec_hi 기반)
- 박스플롯: median · P10 · P90
- 장비_챔버 단위 컬러링(15색 팔레트) — 챔버만 구분하는 것은 의미 없음, `(장비, 챔버)` 조합이 최소 단위.

Wafer Map: `data/DB/wafer_maps/*.json` 은 WM-811K 류 공개 패턴에서 shape 만 차용한 합성 데이터. 패턴 라벨링/갤러리 기능과 연계.

## 5. 매칭 · 어댑터 계층 요지

- **step_id ↔ func_step ↔ area**: `data/Base/matching_step.csv` 3열. product 열은 step_id 자체가 제품별로 달라 불필요.
- **Relation Hint 3-tier**: `exact → alias → substring`. 타입 불일치(str vs int)는 join 시 자동 캐스팅.
- **Dedup**: 모든 컬럼 완전 동일 행만 자동 제거. 부분 중복은 사용자 판단.
- **어댑터 위자드 흐름**: S3 parquet 등록 → 자동 컬럼 스캔 → 타입 추론 → 역할 매핑 UI → 저장 후 재사용.

## 6. 아키텍처 원칙 (요약)

- AI 없이 100% 독립 동작 — 수동 매핑/분석/분류 경로가 반드시 있어야 한다.
- AI 는 편의 기능. 실패 시 수동 fallback.
- 플러그인/어댑터 패턴으로 구현체 교체 가능.
- UX: 탭 전환 즉각 반응, 서버 쿼리/ML 처리만 로딩 표시, keep-mounted 탭, stale-while-revalidate 캐시, virtual scroll.

더 자세한 맥락(소프트랜딩 전략, 로드맵, UX 원칙 전문)은 `../FabCanvas_domain.txt` [5][10][11][12] 참조.
