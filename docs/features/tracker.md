# Tracker

Tracker는 개발 이슈, lot watch, 분석 액션을 생성부터 종료까지 추적한다.

## Owns

- issue status, priority, category, group visibility
- Gantt, comment/reply, image attachment
- issue 단위 mail/watch 설정
- ET source와 lot/wafer watch 상태
- calendar/meeting action으로 push되는 업무 상태
- 새 이슈 lot 입력 표의 product, lot_id(fab_lot_id), wafer_id, purpose, comment 관리
- Monitor lot summary: 입력된 `lot_id` 1행을 유지하고 현재 Qty, wafer 압축 라벨, step/status metadata를 붙인다.

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

## Verify

```bash
git diff --check
python -m pytest tests/test_tracker_app_v2.py
```
