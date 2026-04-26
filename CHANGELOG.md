## v9.0.4 — 2026-04-26

Tracker/ET/TableMap/WF Layout 쪽 운영 큐를 정리한 릴리스.

- **Tracker 메일/Analysis 정리** — 이슈 단위 메일 설정, 수신 그룹/템플릿/미리보기, 2MB 초과 시 본문+go/flow 안내, category 필수 안내, Analysis ET DB 연결/lot 검색 흐름을 보강했다.
- **ET Report 재구성** — 제품/lot 검색 → 측정 package → `step_seq(XXpt)` breakdown → reformatter index page → scoreboard/PPTX 흐름으로 단순화했다. PPTX는 index당 한 페이지에 Statistical Table, Box Table, WF Map, Trend, Radius, Cumulative Plot을 담는다.
- **TableMap Product/YAML 관리** — `_backups` product page 노출을 막고, Product Connection 숨김/복원과 단일 `product_config/products.yaml` block 추가/삭제를 지원한다.
- **WF Layout 정리** — WF 상단에는 TEG 전체 overlay를 제거하고 Shot Sample에서만 선택/검색 TEG를 표시한다. Chip View는 칩이 어느 shot에 속하는지 표로 보여주고 CSV 다운로드를 제공한다.
- **Dashboard/Meeting/Inform 보강** — Dashboard 섹션별 공개 범위를 admin 설정으로 제어하고, Meeting issue import는 글/이미지를 같이 가져오며, Inform SplitTable snapshot은 SplitTable 표시 구조와 맞췄다.

## v9.0.3 — 2026-04-25

Inform log 에서 SplitTable 기준 lot/fab_lot 표시가 빠지던 문제를 바로잡은 핫픽스.

- **root_lot_id 보존** — 인폼 저장/조회 시 `lot_id[:5]`로 강제 축약하지 않고, SplitTable 스냅샷 또는 DB 원본 `root_lot_id`를 그대로 보존한다.
- **fab_lot_id 표시 복원** — 저장된 SplitTable 스냅샷의 `header_groups` / `wafer_fab_list`에서 실제 `fab_lot_id`를 읽어 인폼 카드와 이력 타임라인에 함께 표시한다.
- **검색 범위 확장** — 인폼 타임라인 Lot 검색이 `root_lot_id`, `lot_id`, `fab_lot_id_at_save`, 스냅샷 안의 `fab_lot_id`까지 모두 찾도록 보강했다.
- **SplitTable 자동기록 보강** — plan 변경 자동 인폼 로그가 cell key의 root/wafer를 복원하고 SplitTable 기준 `fab_lot_id`를 같이 저장한다.

## v9.0.2 — 2026-04-24

ET 리포팅을 `lot-step` 기준으로 재정의하고, Reformatter/KNOB lineage/Inform/SplitTable를 운영형 흐름에 맞게 정리했다.

- **ET 리포팅 lot-step 정렬** — raw flat ET DB(`1.RAWDATA_DB_ET/<PRODUCT>/<PRODUCT>_YYYY-MM-DD.parquet`)를 우선 사용하고, `root_lot_id / fab_lot_id / step_id` 기준으로 package를 집계. `step_seq` 조합과 item별 point 수를 Recent ET Package에서 바로 보여준다.
- **엔지니어 이름 ↔ 시스템 step_id 브리지** — `M1DC`, `M2DC`, `function_step`, `canonical_step`, `area` 같은 엔지니어 용어를 실제 `step_id` 집합으로 풀어 검색과 후속 분석에 재사용하게 정리했다.
- **Reformatter 확장 + ET 예시 추가** — `python_expr`, `shot_formula`를 추가해 raw item의 abs/scale 보정과 shot 단위 index 계산을 지원한다. ET 제품 예시 rule 파일(`PRODUCT_A0/A1/B`)도 함께 넣었다.
- **SplitTable/KNOB 메타 강화** — `step_matching.csv`에 `module` 컬럼을 추가하고, KNOB은 function_step 아래 실제 step_id와 module을 함께 보여준다. 값이 비어 있어도 metadata에 정의된 parameter는 계속 유지된다.
- **Inform 등록 흐름 경량화** — lot 입력만으로 거대한 ML_TABLE 스냅샷을 붙이지 않고, `CUSTOM` 컬럼을 고른 뒤 검색했을 때만 SplitTable 스냅샷을 첨부하게 바꿨다. 제품 목록은 FAB DB 제품을 자동 흡수한다.
- **대시보드 안정화** — 내부 스크롤 구조를 정리해 무한 아래 스크롤 버그를 제거하고, KNOB lineage briefing에 earliest step, function_step, module, step_id를 같이 보여준다.

## v9.0.1 — 2026-04-23

SplitTable root↔fab 연결, 인폼 기본 흐름, 회의록 메일 본문 자동화를 정리한 안정화 패치.

- **SplitTable root_lot_id ↔ fab_lot_id 연결 fix** — join/coalesce 구조와 lot candidate 흐름을 안정화해 FAB 연결이 빠져도 root 기준 작업이 끊기지 않도록 보강했다.
- **인폼 사유/스냅샷 흐름 정리** — 신규 인폼 작성 시 기본 reason과 스냅샷/메일 본문 흐름을 일관되게 맞췄다.
- **회의록 메일 minutes 본문 자동 포함** — 메일 본문이 비어 있으면 공동 작성 minutes.body를 자동으로 사용한다.

## v9.0.0 — 2026-04-23

운영형 구조 정리, 보안 보강, 관측성/트래커 강화가 묶인 메이저 롤업.

- **운영형 기반 정리** — parquet 성능 최적화, SplitTable CUSTOM 구조 정리, 인폼 st_view 보존, UXKit, 안정성 플레이북을 도입했다.
- **관측성/알림/트래커 강화** — notify 허브, FAB/ET auto-step, lot watch, final history, drift 경고를 추가해 lot 진행관리와 협업 흐름을 운영형으로 끌어올렸다.
- **보안/세션 정책 보강** — tracker/filebrowser/splittable/dbmap 권한 검증을 정리하고 idle 6h + absolute 24h 세션 정책을 적용했다.
