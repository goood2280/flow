# Tracker (ET 추적)

탭 `tracker` / 라벨 "ET 추적". 개발 이슈, lot watch, 분석 액션을 생성부터 종료까지 추적한다.

## Owns

- issue status, priority, category, group visibility
- Gantt, comment/reply, image attachment
- issue 단위 mail/watch 설정
- ET source와 lot/wafer watch 상태
- calendar/meeting action으로 push되는 업무 상태
- 새 이슈 lot 입력 표의 product, lot_id(fab_lot_id), wafer_id, purpose, comment 관리
- Monitor lot summary: 입력된 `lot_id` 1행을 유지하고 현재 Qty, wafer 압축 라벨, step/status metadata를 붙인다.
- 명시된 lot 이상 / split 영향 키워드를 append-only KnowledgeEvent 후보로 남긴다.
- **ET 일일 스캔** — 하루 n회 지정 시각에 ET DB의 PGM(pt)을 감지해 `et_history`에 누적하고, 톱니바퀴 설정으로 일괄 메일을 보낸다 (`core/et_tracker.py`, `/api/tracker/et-scan/*`).

## Does Not Own

- ET 측정 package 상세 렌더링
- 회의록 본문 저장소
- inform thread의 원본 대화 기록

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Tracker.jsx` |
| Backend router | `backend/routers/tracker.py` |
| Module layer | `backend/app_v2/modules/tracker/` |
| ET 일일 스캔 | `backend/core/et_tracker.py` |
| 스케줄러 | `backend/core/tracker_scheduler.py` |
| Tracker data | `data/flow-data/tracker/` |
| Flow-i guide | `data/flow-data/flowi_agent_features/tracker.md` |

## Guardrails

- category가 비어 있으면 저장/메일 전에 안내한다.
- FAB/ET source 의미를 섞지 않는다.
- lot/wafer 행이 아니라 issue 단위로 mail 설정을 관리한다.
- purpose와 comment는 별도 lot row 필드로 보존한다.
- lot 후보는 full fab lot_id를 우선 보여주며 root 5자리로 잘라 저장하지 않는다.
- Monitor row는 wafer별 저장 행으로 확장하지 않는다. watcher/status cache는 row metadata나 별도 status cache로만 갱신한다.
- `/api/tracker/update`의 optional `lots`는 purpose/comment 편집을 허용하되 기존 watch/status 필드를 보존한다.
- 변경 사항은 알림과 audit 후보가 된다.
- KnowledgeEvent append는 best-effort다. append 실패가 issue/comment/lot 저장을 막지 않는다.
- Shared 설정(categories, scheduler, DB sources, ET lot cache refresh, lot watch polling)은 `tracker` page manager 이상만 쓴다. issue/comment 작성과 본인 업무 흐름은 current user 규칙을 유지한다.

## Verify

```bash
git diff --check
```

```bash
python -m pytest tests/test_tracker_category_source.py
```
