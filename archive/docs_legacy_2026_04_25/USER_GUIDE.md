# flow 사용 가이드 — v9.0.0

**flow** 는 반도체 개발·pilot 단계의 공정 데이터를 단일 웹에서 탐색·추적·공유·회의하기 위한 도구입니다.  아래는 실제 일하는 흐름과 각 도구의 역할을 **한 장**으로 정리한 가이드입니다.

---

## 0. 용어 정리

| 용어 | 의미 |
|---|---|
| **root_lot_id** | 실험 단위 묶음 (보통 5자 영숫자, 예: `A0001`) |
| **lot_id / fab_lot_id** | 실제 라인 투입 lot (예: `A0001A.1_V1`) — root_lot_id 앞 5자를 공유 |
| **wafer_id** | 1~25 번 wafer 번호 |
| **step_id** | 공정 step 식별자 (보통 대문자 2자 + 숫자 6자 + 숫자 6자 포맷, 예: `AB123456789012`) — 뒤 6자리 숫자가 진행도 |
| **step_seq** | 같은 step_id 내의 측정 순서 |
| **flat** | ET 측정에서 wafer 의 notch 방향 (구분자) |
| **PRODA / PRODB** | 제품명.  내부에 `ML_TABLE_PRODA` 로 저장되기도 하지만 **같은 제품** — v9.0.0 부터는 UI/응답 전부 `PRODA` 로 통일됩니다 |

---

## 1. 데이터 소스 구조

### 1.1 DB 루트 (읽기 전용, 원천)
```
/DB/1.RAWDATA_DB_FAB/<PROD>/date=YYYYMMDD/*.parquet   ← 공정 이력 (장비/레시피/챔버)
/DB/1.RAWDATA_DB_INLINE/<PROD>/date=YYYYMMDD/*.parquet ← 인라인 계측 (CD/TOX/OVL 등)
/DB/1.RAWDATA_DB_ET/<PROD>/date=YYYYMMDD/*.parquet     ← 웨이퍼 ET (VT/IDSAT/ROFF 등 die-level)
```
long-format: `item_id / subitem_id / lot_id / wafer_id / value / time` (+ ET 는 shot_x/shot_y, step_seq, flat)

### 1.2 Base 루트 (룰북/매핑, 편집 가능)
```
/Base/ML_TABLE_<PROD>.parquet   ← pivot 완료된 분석 wide 테이블
/Base/knob_ppid.csv             ← KNOB_* 컬럼 → function_step + step_id 매핑
/Base/inline_matching.csv       ← INLINE_* 컬럼 → step_id + item_desc 매핑
/Base/vm_matching.csv           ← VM_* 컬럼 → step_id + step_desc 매핑
/Base/step_matching.csv         ← function_step → step_id 리스트 확장
/Base/_uniques.json             ← wafer/lot/product unique 레지스트리
/Base/features_*.parquet        ← 집계된 wafer 단위 features
```

### 1.3 Matching Table / Rulebook 동작 원리

**KNOB_\<feature\>** 컬럼 (예: `KNOB_GATE_PPID`)
1. `knob_ppid.csv` 에서 `feature_name == GATE_PPID` 행 조회
2. 행의 `function_step` 값들을 수집 (하나 이상, 예: `GATE_PATTERN + PC_ETCH`)
3. 각 `function_step` 마다 `step_matching.csv` 에서 실제 `step_id` 목록 확장
4. SplitTable row 서브라벨: `#{rule_order} {function_step} → [step_id_A, step_id_B, ...]` 로 표시
5. `function_step` 이 2+ 개면 `+` 로 연결되어 한 KNOB 컬럼에 관여한 여러 step 을 모두 노출

**INLINE_\<item\>** 컬럼 (예: `INLINE_CD_GATE_MEAN`)
1. `inline_matching.csv` 에서 `item_id == CD_GATE` 행 조회
2. 해당 `step_id` 하나 + `item_desc` 를 sub-label 로 표시 (🔬 INLINE · desc · step_id pill)
3. 등록 안 된 경우 `step 미등록` dashed pill + 원본 item_id 표시 → Rulebook 탭에서 "Auto-infer" 실행 (아래 참조)

**VM_\<feature\>** 컬럼 — INLINE 과 동일하지만 `vm_matching.csv` 기준, 색상 보라(🤖).

**FAB_\<feature\>** — knob_ppid.csv 에 매칭되면 KNOB 과 동일 구조, 안 되면 🏭 FAB + step 미등록 pill.

### 1.4 Auto-infer — 매칭 테이블 비어있을 때

SplitTable Rulebook 탭에서 `infer-step-mapping` 실행 (관리자 또는 splittable page_admin 전용):
- **원리**: FAB long 과 INLINE/VM long 을 `(lot_id, wafer_id)` 기준 `join_asof(on=time, backward)` → 각 INLINE.item_id 직전의 FAB.step_id 를 winner 로 판정
- 결과가 `inline_matching.csv` / `vm_matching.csv` 에 upsert (수동 편집 행은 보존)
- 한 번 돌리면 SplitTable 의 모든 INLINE/VM row 에 step_id pill 이 자동으로 붙음

---

## 2. 일 flow — 실제 작업 순서

### Step 1 — 파일 탐색기 (`📂`)
- DB 루트에서 FAB/INLINE/ET parquet 을 바로 탐색.
- 제품을 한 번 선택하면 DB 루트만 바꿔도 자동으로 같은 제품 view 로 이동.
- 대용량 (30~60GB) 은 기본 최근 30일 파티션만 scan (URL 에 `all_partitions=1` 또는 SQL 에 `date=...` 필터 넣으면 전체).

### Step 2 — 스플릿 테이블 (`📊`)
- 제품 + root_lot_id (또는 fab_lot_id) 선택 → parameter × wafer 매트릭스 렌더.
- **CUSTOM 모드** = 내가 보고 싶은 parameter 만 뽑아서 보는 뷰:
  1. `CUSTOM` chip 클릭
  2. 컬럼 검색 / 체크 (root_lot_id/wafer_id 같은 기본 식별자는 자동 제외됨)
  3. `Search` 누르면 저장 없이도 그 체크 상태대로 결과 표시
  4. 같은 조합을 나중에도 쓸 거면 이름 입력 + `Save` → 공용 CUSTOM set 으로 공유
- **Plan** = 내가 이 셀에 이렇게 바뀌었으면 한다 하는 값 (KNOB/MASK/FAB 컬럼 전용):
  - 셀 클릭 → 목표값 입력 → Confirm → 오렌지 화살표로 `기존 → 목표` 표시
  - history 에서 누가 언제 뭐로 바꿨는지 전체 로그
  - `Final Only` 탭 = 셀별 최종값 + drift 경고 (같은 셀에 값 2번 이상 다르게 set, 또는 여러 사용자가 set)
- **노트** = 셀/행/wafer 범위에 메모 (💬 배지). lot 전역·parameter 전역 스코프도 지원.

### Step 3 — 이슈 추적 (`📌`)
**카테고리가 곧 일하는 방식**:
- **Monitor** (source=fab, lot 단위) — lot 진행 모니터링.  특정 step 을 넘으면 알림.
- **Analysis** (source=et, wafer 단위) — ET 측정 이력 추적.  새 측정 찍히면 알림.

**Lot/Wafer 행별 watch 설정**:
1. 이슈 생성 → 카테고리 Monitor 또는 Analysis 선택
2. Lot/Wafer 행에 `root_lot_id` (5자) 또는 `lot_id` (긴 형식) 입력 + wafer 번호
3. 저장 후 이슈 상세 들어가면 각 행에 자동으로 `FAB/ET` 배지가 카테고리 따라 붙음:
   - **FAB 모드** (Monitor): `target step` 입력 (예: `AB123456789012`).  DB 업데이트 시 `이 lot 최신 step 이 target 이상 + 앞 prefix+head 동일` 이면 fire
   - **ET 모드** (Analysis): target 없음.  새 측정 패키지 (step_id/step_seq(Npt) 조합) 가 추가로 찍히면 fire
4. `✉ 메일` 체크박스 → 알림 + 메일 동시 발송
5. 시스템이 **30분 주기** 로 폴링해서 조건 충족 시 알림/메일 발송 (실시간은 부하 큼)

### Step 4 — 회의관리 (`👥`)
- 회의 생성 → action item → 달력 auto-sync
- 회의록 공동작성 (SSE 실시간 + rev 충돌 방지)
- 이슈 가져오기 → 이슈 본문 + 캡처 + LOT_WF 리스트 + FAB/ET 최신 정보 자동 삽입 (v9.1 로드맵)

### Step 5 — 인폼 로그 (`📝`)
- 제품·Lot·모듈·사유 + 본문 + SplitTable CUSTOM 스냅샷 자동 첨부
- 제품 담당자 + 메일 그룹에 자동 메일 (2MB 제한, 초과 시 제목+본문+표 요약만)
- 답글 스레드 / 수정 이력 타임라인

### Step 6 — 대시보드 (`📈`)
- 차트 Config CRUD — X/Y 축, 색상 그룹핑, left joins
- 자동 refresh (글로벌 주기 + per-chart override)

---

## 3. 관리자 작업 (톱니 ⚙)

### 3.1 Matching table 구축 순서 (최초 세팅)

1. **`knob_ppid.csv`** 작성 — feature_name / function_step / rule_order / ppid / operator 컬럼
2. **`step_matching.csv`** 작성 — function_step → step_id 리스트
3. **`inline_matching.csv`** — item_id / step_id / item_desc / product
4. **`vm_matching.csv`** — feature_name / step_id / step_desc / product
5. 파일 Base 루트에 올림 → SplitTable Rulebook 탭에서 확인
6. 비어있는 step 이 많으면 → Rulebook 탭의 **Auto-infer** 버튼으로 FAB↔INLINE/VM 시간 join 자동 채움

### 3.2 유저·권한 관리

- **승인** — 회원가입 → admin 이 승인해야 로그인 가능
- **탭 권한** — 사용자별로 보여줄 탭을 매트릭스로 지정
- **페이지 위임 admin** — 특정 페이지(informs/splittable/tablemap/tracker) 관리 권한을 일반 유저에게 위임
- **그룹** — 공용 메일 그룹 + 인폼/회의 공개 범위로 사용
- **카테고리 편집** (Tracker 톱니) — Monitor/Analysis 외에 추가 카테고리 정의.  각 카테고리에 source (fab/et) + max_issues_per_user + mail_group_ids + auto_close_step_id (v9.1)

### 3.3 알림 구독 룰

유저가 자기 톱니에서 8가지 이벤트별 on/off 설정:
- `my_plan_changed` — 내 plan 이 누군가에 의해 바뀜
- `my_meeting_minutes_added` — 내 회의에 회의록 추가
- `my_tracker_comment` — 내 이슈에 댓글
- `my_tracker_status_changed` — 내 이슈 상태 변경
- `tracker_step_reached` — 내가 watch 건 Lot 이 target step 넘음
- `my_inform_comment` — 내 인폼에 댓글
- 등

### 3.4 세션 정책

- idle 6시간 → 자동 로그아웃
- absolute 24시간 → 아무리 활동해도 24h 뒤 재로그인
- 로그인 위치 다중 기기 허용

---

## 4. 자주 하는 실수 / FAQ

- **Q. PRODA 가 드롭다운에 두 번 뜹니다 → v9.0.0 부터는 `ML_TABLE_PRODA` 와 `PRODA` 를 canonical 로 합쳐 1개로 보입니다.  이전에 두 이름으로 기록된 인폼은 원천 레코드 그대로지만 UI 에서는 합쳐 노출.
- **Q. SplitTable 에서 INLINE row 에 step_id 가 안 보여요 → `inline_matching.csv` 에 해당 item 이 없기 때문입니다.  Rulebook 탭 > Auto-infer 실행.
- **Q. Lot watch 건 알림이 안 와요 → (a) 카테고리가 Monitor 인지 Analysis 인지 확인 (b) FAB 모드면 `target step_id` 가 입력됐는지 (c) 메일도 원하면 `✉ 메일` 체크 (d) 스케줄러는 30분 주기라 즉시가 아님.
- **Q. CUSTOM set 저장을 깜빡했어요 → v9.0.0 부터는 save 없이 체크만 하고 Search 눌러도 바로 반영됩니다.  같은 조합을 재사용할 때만 이름 붙여서 save.

---

## 5. 버전 · 문서 인덱스

- **현재 버전:** v9.0.0 (2026-04-23)
- **CHANGELOG:** [CHANGELOG.md](../CHANGELOG.md)
- **v9.x 로드맵:** [docs/v9_roadmap.md](v9_roadmap.md)
- **FAB datalake 스키마:** [docs/fab_datalake_schema.md](fab_datalake_schema.md)
- **UX 표준:** [docs/ux_standard.md](ux_standard.md)
- **안정성/확장성 playbook:** [docs/stability_scalability_playbook.md](stability_scalability_playbook.md)
- **성숙도 진단:** [docs/flow_maturity_2026_04_23.md](flow_maturity_2026_04_23.md)
