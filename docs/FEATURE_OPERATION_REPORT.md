# Flow 주요 기능 동작 보고서

작성 2026-07-26 · 기준 v9.5.39

대상: 스플릿 테이블, 캐시 관리, 파일탐색기, 대시보드, TEG 위치 조회, ET 다운로드.

각 기능이 **무엇을 읽고, 어떻게 계산하고, 무엇을 저장하는지**를 실제 코드 기준으로 정리한다. 기능별 책임 경계와 세부 규칙은 [features/](features/) 아래 문서를 본다.

---

## 0. 전체 구조

모든 화면은 **두 개의 루트**에서 데이터를 얻는다.

| 루트 | env | 내용 | 성격 |
|---|---|---|---|
| DB 루트 | `FLOW_DB_ROOT` (없으면 `data/Fab/`) | 원천 parquet/CSV, 룰북, 캐시 | 읽기 전용 원천 |
| 데이터 루트 | `FLOW_DATA_ROOT` (없으면 `data/flow-data/`) | 사용자 작성물, 설정, 세션, 로그 | 앱이 쓰는 운영 상태 |

이 분리가 사내 반입의 핵심이다. 코드는 통째로 교체해도 되고, 데이터는 코드 디렉터리 밖 공유 경로에 꽂는다.

**서버는 두 대**로 나눈다. 운영 API 서버(사용자 요청 + RAM 캐시 소유)와 개발 worker 서버(무거운 공유 캐시 생성). 역할은 기동 시 1회 판정하며 `FLOW_SERVER_ROLE` > `server_role.json` > 자동 순으로 결정한다. worker는 메일·S3·스케줄러 같은 외부 연동을 띄우지 않는다 — 두 서버가 같은 데이터 루트를 보기 때문에 중복 발송을 막아야 한다.

**공유 기준선이 하나 있다.** `{DB루트}/cache/lot_progress_latest_lot_by_root_wafer.parquet` — root/wafer별 최신 `lot_id`다. 파일탐색기가 생성·갱신을 소유하고, 스플릿테이블·인폼·트래커·대시보드·Flow-i의 "현재 위치" 질의가 전부 이걸 읽는다. 즉 **"지금 이 lot이 어디까지 갔나"의 단일 출처**다.

---

## 1. 파일탐색기 — 데이터가 있는지 확인하는 입구

### 하는 일

DB 루트를 탐색해 parquet/CSV의 스키마와 샘플을 보고, 다음 작업 대상을 고른다. 읽기 전용 SQL과 다운로드까지가 범위이고, 분석 판단은 하지 않는다.

### 동작 — 2단계 로드

DB 제품/root parquet을 처음 열면 한 번에 다 읽지 않는다.

1. `meta_only=true` — 스키마만 즉시 그린다. 화면엔 "샘플 행 불러오는 중…"이 뜬다.
2. 백그라운드로 **최신 date 파티션 한정** 샘플을 받아 교체한다 (DB 제품 500행).

SQL·정렬·집계가 붙은 조회와 페이지 이동은 단일 요청(`meta_only=false`)으로 간다. 응답이 순서가 꼬이면 요청 시퀀스 가드가 늦게 온 응답을 버린다.

최신 파티션을 찾을 때 `iter_latest_partition_files`는 **과거 `date=` 형제 폴더에 아예 들어가지 않는** walk를 쓴다. 수년치 파티션이 쌓인 DB에서 목록 조회가 멈추지 않는 이유다. preview 캐시 서명은 TTL 30초의 stale-while-revalidate이라, 새 파티션 반영이 최대 30초 늦을 수 있다.

### 표시 한도

| 대상 | 한도 | 이유 |
|---|---|---|
| 화면 표시 행 | 100행 (DB 기본 preview만 500행) | 브라우저 보호 |
| 화면 표시 열 | 기본 100개 | 5,000열 wide schema 대응 |
| 컬럼 검색 | `/api/filebrowser/columns/search` 서버 조회 | 전체 스키마를 내려보내지 않으려고 |
| CSV 다운로드 | `csv_download_max_bytes` 주 제한 (상한 500,000행 / 100MB) | 화면 100행 제한과 **별개** |

다운로드를 화면 제한에 묶지 않는 게 의도된 설계다. 보는 것과 받는 것은 목적이 다르다.

### AI SQL

연결된 LLM이 read-only SQL 필터, `ORDER BY`, 집계, 선택 컬럼 초안을 만들고 화면에서 즉시 preview까지 실행한다. Agent 탭의 `filebrowser_ai_sql` unit은 소유권을 가져간 게 아니라 `context_sample → semantic_layer → filter_draft → column_draft → merge → preview_apply` 흐름을 보여주는 wrapper이며, 검증과 적용은 파일탐색기 helper를 그대로 재사용한다.

`select_cols`가 비면 identity 컬럼만 반환하고, `*` 전체 요청은 차단하며, 없는 컬럼은 `code=unknown_column` 400으로 돌려준다.

### S3 동기화

파일 목록의 신호등은 `status-by-target?include_local=0` 빠른 응답으로 먼저 뜨고, 이후 idle/5분 주기로 `include_local=1`을 불러 freshness 텍스트만 보강한다. 로컬 스캔도 최신 `date=` 하나만 내려가는 pruned walk이고 TTL 300초 stale-while-revalidate로 서빙된다.

권한이 3단으로 갈린다: 일반 사용자는 신호등만, page manager는 항목·이력 조회와 수동 실행·중지, 항목 CRUD와 AWS credential은 global admin 전용.

---

## 2. 스플릿 테이블 — 계획과 실제를 맞추는 작업대

`product + root_lot + wafer` 축으로 plan과 actual을 비교하고 편집한다. 앱에서 가장 무거운 화면이고, 최근 릴리스 대부분이 여기 성능에 쓰였다.

### 데이터가 오는 길

원천은 제품별 `ML_TABLE_<PRODUCT>.parquet`이다. 이건 **long 포맷**(행 = 측정 항목)인데 화면은 **행렬**(행 = 항목, 열 = wafer)이 필요하다. 매번 transpose하면 감당이 안 되므로 캐시 계층을 쌓았다.

### 캐시 5단 — 위에서부터 히트하면 아래로 안 내려간다

| 단 | 위치 | 담는 것 | 무효화 |
|---|---|---|---|
| ① view payload 캐시 | 프로세스 메모리 | 조회 조건별 최종 JSON | plan/tag/룰북/설정/원본 mtime 변경 |
| ② root lot RAM 캐시 | 프로세스 메모리 | root 단위 피벗 결과 | 기본 3GB 예산, 30분 스케줄러 예열 |
| ③ 제품 원본 RAM 캐시 | 프로세스 메모리 | ML_TABLE 원본 | 기본 3GB, 선택적 (끄기 권장) |
| ④ 사전 피벗 캐시 | `{DB}/cache/split_table/<제품>/<root>.parquet` | root별 행렬 parquet | 원본이 새로우면 재빌드 큐잉 |
| ⑤ 원본 raw scan | `ML_TABLE_*.parquet` | 전부 | — |

**핵심은 ④다.** 사전 피벗 캐시가 있으면 원본 스캔과 transpose를 통째로 우회하고, parquet의 행렬 구조를 그대로 읽어 JSON으로 매핑한다. 0.1초 내외로 끝난다.

캐시가 없거나 낡으면 이번 응답은 **기존 캐시로 즉시 내보내고** 재빌드를 백그라운드에 큐잉한다(제품당 single-flight + 5분 쿨다운). 캐시가 아예 없으면 원본에 root/wafer/prefix 필터를 걸어 raw fallback 결과를 그 요청 안에서 만들어 준다.

재빌드는 기존 파일을 **지우지 않는다.** root 단위로 tmp→replace 원자 교체만 하므로 빌드 중에도 조회가 계속 서빙된다. 빌드가 실패하면 이전 캐시가 그대로 남는다.

### KNOB 특별 취급 (v9.5.38)

실제로 압도적으로 많이 보는 게 `prefix=KNOB`이라, 피벗 캐시를 만들 때 **KNOB 컬럼 + 앵커만 담은 좁은 사이드카**를 `<제품>/knob/<root>.parquet`에 같이 쓴다. KNOB 단독 조회만 이걸 읽는다 (4,000→2,000컬럼 기준 검색당 약 18ms 절감).

사이드카를 하위 폴더에 두는 이유가 있다 — lot 후보 목록이 캐시 폴더의 `*.parquet` stem으로 만들어지기 때문에, 같은 폴더에 두면 `A1000.KNOB` 같은 가짜 lot이 생긴다.

사이드카가 없거나 낡거나 앵커 컬럼이 빠지면 전체 파일로 폴백하므로 결과가 달라질 수 없다. 다만 컬럼 선택기 목록(`all_columns`)은 전체 피벗 스키마 기준으로 복원한다 — 안 그러면 KNOB 화면에서 커스텀 세트를 만들 때 INLINE/VM 컬럼이 사라진다.

관리자가 등록한 우선 lot × KNOB 조합은 30분 주기로 **미리 계산**해 둔다. 가장 흔한 검색이 cold 계산을 건너뛰고 캐시 HIT로 끝난다.

### 편집은 원본을 건드리지 않는다

이게 이 화면의 근본 규칙이다.

- plan → `data/flow-data/splittable/` 아래 별도 파일
- `TAG_*` 꼬리표 → `custom_tags.json`에만
- `MGMT_*` 관리 행 → `management_rows.json`에만

전부 조회 시점에 **overlay**로 얹힌다. view payload 캐시의 의존 시그니처에 이 파일들의 mtime이 들어 있어서 저장 직후 조회에 바로 반영된다. 원본 `ML_TABLE_*.parquet`과 FAB source에는 아무것도 쓰지 않는다.

### 룰북 4종

| 파일 | 계약 컬럼 | 만드는 행 이름 |
|---|---|---|
| `ppid_knob.csv` | `feature_name`, `rule_order`, `step_desc`, `operator`, `value`, `category` | KNOB 적용공정 정보 |
| `Vehicle_matching.csv` | `product`, `step_id`, `step_desc` | step_desc → step_id 확장 |
| `inline_matching.csv` | `product`, `step_id`, `item_id` | `INLINE_<item_id>` |
| `vm_matching.csv` | `step_desc`, `item_id` | `VM_<step_desc>_<item_id>` |

`ppid_knob.csv`는 **제품 없는 공용 룰북**이다. 같은 KNOB의 CSV row 전체가 `R1`, `R2`... 순으로 표시되고, 같은 `rule_order`에 여러 row가 있으면 하나의 AND 조건 묶음이 된다.

`Vehicle_matching.csv`의 `product` 셀은 `"PRODA, PRODB"`처럼 쉼표로 여러 제품을 적을 수 있다. 토큰 중 현재 제품과 맞는 row만 쓰며, `PRODA` 선택이 `PRODA0`/`PRODA1`을 끌어오지는 않는다.

### 표시 형식 3종

- **기본** — 모든 행/열 개별 칸
- **Split 체크** — split 값을 S0/S1... 행으로 분리해 어떤 ppid/split인지 보여준다. KNOB/MASK 비교용이라 INLINE/VM이 표시 대상이면 비활성화된다.
- **병합** — 왼쪽 칸과 같은 값이면 colSpan으로 합친다. 읽기 전용이며 편집 중에는 기본 형식으로 렌더한다.

XLSX 다운로드가 현재 표시 형식을 그대로 따른다. 병합 형식이면 실제 셀 병합으로 export한다.

### 불일치 알람

plan이 actual과 달라지면 **plan 작성자에게** `my_plan_actual_mismatch`를 1회 발행한다. 저장 시점에 이미 다르면 즉시, 이후 DB 갱신으로 `/view`에서 새 mismatch가 관측되면 백그라운드 큐가 같은 조합을 dedupe해서 발행한다. `source-config`의 지정 팀에게도 함께 가며, 팀 수신자는 사용자별 key로 중복을 막는다.

`/view`의 첫 응답은 matrix와 mismatch 카운트를 먼저 주고, 관련 이슈는 `/related-issues` 후속 호출로 붙인다.

### 성능 관측

응답에 `runtime_profile`(`total_ms`, `collect_ms`, `matrix_ms`, `overlay_ms`, `root_cache_hit`, `knob_sidecar`)과 `view_cache`가 실려 온다. 느릴 때 **어느 단계가 느린지 화면에서 바로 본다.**

---

## 3. 캐시 관리 — 위 캐시들을 운영하는 콘솔

스플릿테이블이 만든 캐시를 사람이 보고 조절하는 화면이다. 캐시를 *만드는* 로직은 스플릿테이블이 소유하고, 이 화면은 **현황·예산·수동 실행·로그**를 담당한다.

### 예산 우선순위

```
env  >  cache_budget_settings.json (톱니바퀴 ⚙)  >  적응형 기본값
```

운영자가 env로 고정한 값은 UI 설정이 절대 덮지 못한다. 설정 파일은 `{데이터루트}/splittable/cache_budget_settings.json`에 있다.

여기에 **함정이 하나** 있다. 운영 서버와 개발 서버가 데이터 루트를 공유하므로 저장하면 두 서버에 동시 적용된다. 그래서 서버별로 달라야 하는 값(풀 비율, root RAM GB, 제품 원본 RAM 등)은 **전부 운영/개발 분리 키**로 저장한다.

### 화면 구성

- **전체 RAM 캐시 사용량** / **서버 메모리 종합** — 운영·개발 각각
- **제품별 현황** — root 수, MB, 우선 lot 적재율. 캐시에 아직 안 올라간 제품도 0으로 포함해서 보여준다(빠뜨리면 "없는 건지 안 올라간 건지" 구분이 안 된다)
- **주요 Lot** — purpose/comment는 엔지니어가 쓰고, 위치(step_id/step_desc)는 최신 진행 데이터에서 자동으로 채운다. 등록된 lot은 RAM 캐시에 우선 적재된다 (lot_id 앞 5자리 = root_lot_id)
- **전체 캐시** — 적재 내역 상세
- **수동 스캔** — 진행 단계, 실행 중 작업, 대기 큐, 피크 메모리를 실시간으로 표시
- **이벤트 로그** — 수동 스캔 / 예열 / 축출 / 워치독 / 캐시 적재 필터

### 로그 구조

인메모리 링 버퍼 200건 + 공유 JSONL(`{데이터루트}/logs/cache_events.jsonl`)의 이중 구조다. 운영/개발 로그를 같은 파일에 `origin` 필드로 구분해 넣어 한 화면에서 본다. **링 버퍼만 보고 "이력이 없다"고 판단하면 안 된다.**

### 메모리 압박 시 동작

캐시 작업은 사용자 요청에 양보하고(`yield_to_users`), 메모리 압박이면 중단한다. 서버 메모리가 부족하면 lot 위치(step) 조회를 건너뛰는데, 이때 화면에 **건너뛰었다는 사실을 표시**한다. 조용히 빈 값으로 두지 않는 게 규칙이다.

상태 표시도 "완료" / "건너뜀" / "실패"를 구분한다 — 셋을 뭉뚱그리면 운영자가 잘못된 결론을 낸다.

---

## 4. 대시보드 — 저장된 차트로 상태를 보는 화면

chart-only 화면이다. raw 데이터 탐색은 파일탐색기로, plan 편집은 스플릿테이블로 넘긴다.

### 동작

진입 시 `/fab-progress`, `/summary`, `/trend-alerts`를 **호출하지 않는다.** 저장된 차트와 스냅샷만 그린다. (기존 API는 호환용으로 남아 있지만 기본 UI가 쓰지 않는다.)

차트 종류는 line / bar / scatter / box / pie / heatmap / table / SPC / wafer map. Home Flow-i 결과와 scatter·trend는 Plotly를 우선 쓰고, 기존 SVG 렌더러는 비대상 타입과 fallback 경로로 유지한다.

### 차트 추가

`+ 차트 추가`가 세 가지의 단일 진입점이다: 일반 chart type, Inform preset, AI draft 생성.

데이터 소스 목록에는 LOT 진행 최신 캐시가 있으면 `Cache/LOT latest`로 뜬다. `root_parquet` read-only 경로를 그대로 쓰므로 차트·테이블·조인 입력이 되지만, **대시보드가 이 캐시를 갱신하지는 않는다** — 생성은 파일탐색기 소유다.

### LLM 경계

Flow-i/LLM은 chart **draft**나 chart session까지만 만든다. 실제 저장은 사용자가 `저장`을 누르거나 명시적으로 확인한 뒤 `/charts/save`로 간다. AI가 대시보드를 마음대로 바꾸지 않는다.

여러 DB/단일 파일을 거친 draft는 `core.flowi_multisource`가 실제 source rows와 확인된 `schema_relations` join plan을 만든 뒤, chart config의 `source_evidence`에 `source_ids`, `relation_ids`, `join_keys`, `selected_columns`, `sql_plan`을 보존한다. **차트가 어떤 근거로 만들어졌는지 편집 화면에서 확인할 수 있다.**

저장 위치는 `dashboard_snapshots.json`과 `dashboard_chart_sessions/`다.

---

## 5. TEG 위치 조회 — 좌표 계산과 Mapfile 검증

chip layout과 Teg_location 파일로 WF MAP geometry를 fit하고 TEG 실좌표·radius를 계산한다.

### Geometry — Auto Report와 같은 수학

`Chip_Radius` r은 shot 센터와 wafer 원점 사이 거리(mm)다. shot 격자좌표 `(x, y)`와의 관계

```
r² = kx²·(x-cx)² + ky²·(y-cy)²
```

를 `r² = A·x² + B·y² + p·x + q·y + C` 로 선형화해 최소자승 fit하면

```
cx = -p/2A,  cy = -q/2B,  kx = √A,  ky = √B
```

여기서:
- shot 센터 실좌표(mm) = `((x-cx)·kx, (y-cy)·ky)`
- TEG 좌하단 실좌표 = shot 센터 + `(ebeam_x, ebeam_y)·scale`
- radius = 원점과의 유클리드 거리

**Auto Report의 `My_Function._wafer_circle_params`와 같은 수학을 쓴다.** 한쪽만 바꾸면 두 시스템 좌표가 조용히 갈라진다.

### 입력

| 파일 | 필수 | 선택 |
|---|---|---|
| chip layout | `Mask`(vehicle), `chip_x_adj`, `chip_y_adj`, `Chip_Radius` | — |
| `Teg_location.csv` | `vehicle`, `teg`, `ebeam_x`, `ebeam_y` | `teg_w`/`teg_h`, `top_cell`(다른 이름), `direction`(H 기본 / V는 w·h 스왑) |

열 이름은 대소문자 무관. 설정과 vehicle 그림은 DB 루트의 `teg_location/` 폴더에 둔다 — 파일탐색기에서 보이는 위치 안이다.

shot 표시는 3가지: `none` / `image`(업로드 그림) / `grid`(shot 안 칩 배열 — cols×rows, 칩 크기, 간격, shot 센터 기준 대칭 배치).

### Mapfile 체크

설비에서 복사한 레시피 원문을 붙여넣으면:

1. 전체 Pattern의 site 좌표를 작은 WF MAP 카드로 한 번에 표시 (클릭하면 확대)
2. `#teg-map`의 module 좌표를 **flat 변환**(`Vertical(R)` = 반시계 90° 회전 원복)한 뒤, 정답지(Teg_location의 raw ebeam 값)와 대조

판정: 🟢 일치 / 🟡 확인필요(ΔX·ΔY 각 3 이내) / 🔴 불일치 / 🟣 확장 / ⚪ 미등록. 신호등 정렬은 빨강 → 미등록 → 노랑 → 초록 순으로, **문제부터 위에 온다.**

좌표 원복은 PCHK 상대좌표를 ebeam 절대좌표로 되돌리는 계산이고, PCHK는 별도 TEG 행으로 다룬다. TEG offset은 H 관점(양수 = 차감)이며 중복 TEG는 `ref_seq`로 구분한다.

### 렌더 가드

`MAX_CELLS` 400,000 셀 상한이 있고, 격자선은 6,000을 넘으면 생략한다. 큰 vehicle에서 이 가드를 빼면 브라우저가 멈춘다.

쓰기 권한은 admin 또는 page manager(`teg`).

---

## 6. ET 다운로드 — 규칙으로 index를 뽑아내는 화면

auto report의 reformatize 흐름을 flow 화면으로 옮긴 것이다. 제품을 고르면 vehicle reformatter 규칙으로 **shot 단위 index 값**을 계산해 페이지 단위로 보여주고 CSV로 내려준다.

### 규칙 소스

`{데이터루트}/reformatter/<vehicle>_reformatter.csv`

- **REAL** — raw `ITEMID`, abs 여부, scale factor
- **ADDP** — ADDP Form과 참조 컬럼

`/items`는 **데이터를 전혀 읽지 않고 규칙 CSV만 파싱**한다. index 선택 UI가 가벼운 이유다.

### 제품 탐색

ET 측정시간 탭과 **같은 탐색**(`core.lot_step.db_product_candidates`)을 쓴다. 제품 폴더, hive 파티션(`product=`), 플랫 파일명, parquet의 `product` 컬럼 스캔까지 흡수하고, 이게 실패할 때만 폴더 나열로 폴백한다.

> 사내 DB에서 제품이 안 잡히던 문제는 단순 폴더 나열을 이 루트 해석으로 교체해 해결했다(2026-07-21). 폴더 구조만 가정하는 코드로 되돌리면 재발한다.

### 실행 흐름

```
제품 선택 → /items 로 REAL/ADDP 항목 표시 → index 선택
   → POST /run  (계산 후 offset/limit 페이지 반환)
   → GET /download  (전체 결과 CSV + downloads.jsonl 기록)
```

매칭되는 vehicle CSV가 없으면 400으로 명확히 알린다. 빈 결과로 조용히 넘어가지 않는다.

### 관리자 전용 수식 테스트

`/formula-help`, `/test`, `/test/download`는 `require_admin` 게이트 뒤에 있다. **임의 수식을 평가하는 경로**라 권한을 낮추면 안 된다. 새 ADDP 수식을 배포 전에 미리 돌려보는 용도다.

### 규칙 편집은 별도

이 화면은 규칙을 **사용**만 한다. 규칙 등록·편집은 `backend/routers/reformatter.py`가 소유하고, 거기서 등록한 제품별 JSON 규칙을 파일탐색기 다운로드·대시보드 차트·ML 학습이 **모두 공유**한다.

---

## 7. 여섯 화면을 관통하는 것

### 캐시는 항상 "낡아도 응답을 준다"

스플릿테이블 피벗, 파일탐색기 preview, S3 신호등이 전부 같은 패턴이다.

```
캐시 fresh?  → 즉시 반환
캐시 stale?  → 낡은 값 즉시 반환 + 백그라운드 재계산 큐잉
캐시 없음?   → 가드 여유 있으면 원본 계산, 아니면 큐잉 사실을 응답에 표시
```

사용자를 기다리게 하는 대신 **낡았다는 사실을 알려주고** 다음 조회에서 최신을 준다.

### 원본에 쓰지 않는다

plan, TAG, 관리 행, TEG 설정, reformatter 규칙 — 전부 별도 저장소에 두고 조회 시점에 overlay한다. 원천 parquet과 FAB source는 읽기 전용이다. 배포(`setup.py`)에도 6중 가드가 걸려 있어 코드 교체가 운영 데이터를 건드릴 수 없다.

### 실패를 숨기지 않는다

메모리 부족으로 step 조회를 건너뛰면 화면에 표시하고, 캐시가 큐잉만 됐으면 `queued=true`와 사유를 응답에 싣고, 상태는 "완료"·"건너뜀"·"실패"를 구분한다.

> 다만 **예외가 하나 있다.** 기동 시 스케줄러 등록 실패는 warning 로그로만 삼켜진다 ([backend/app_v2/runtime/startup.py](../backend/app_v2/runtime/startup.py)). 실제로 `backend/scheduler.py`가 유실됐을 때 제품 dedup이 6일간 조용히 멈춰 있었고 아무 화면에도 표시되지 않았다. 스케줄러를 건드릴 때는 기동 로그의 `init failed`를 직접 확인해야 한다. (v9.5.39에서 해당 파일은 복구했다.)

### 권한은 3단

`일반 사용자` → `page manager(화면별)` → `admin`. 공유 설정 변경은 page manager 이상, 파괴적이거나 credential을 다루는 동작은 admin 전용이다. 화면별 매트릭스는 [permission_matrix.md](permission_matrix.md)에 있다.
