# 차트 보드 퇴역 백업 (2026-07-12)

대시보드가 "WIP × Split 현황" 단일 화면으로 개편되면서 기존 **차트 보드**
(저장 차트 그리드 · 차트 에디터 · 차트 추가 라이브러리 · 확대 모달 · 새로고침 설정 기어 ·
FabProgressPanel/TrendAlertPanel 등 미장착 패널 포함) UI 를 제거했다.

- `My_Dashboard.full.jsx` — 제거 직전의 `frontend/src/pages/My_Dashboard.jsx` 전체 원본 (3,351줄).
  WipSplitPanel(신규 화면)과 차트 보드가 모두 들어 있던 마지막 버전.
- 복원 방법: 이 파일을 `frontend/src/pages/My_Dashboard.jsx` 로 되돌리고
  `npm run build` 하면 탭(WIP 현황 / 차트 보드) 구성으로 돌아간다.

## 백엔드는 남아 있음

`/api/dashboard/charts|snapshots|charts/save|refresh|preview|columns` 등 차트 보드용
API 와 스냅샷 스케줄러(`routers/dashboard.py`)는 삭제하지 않았다 — UI 만 제거된 상태.
완전 퇴역 시 백엔드 정리는 별도 작업으로 진행할 것.

## setup 번들 제외

`archive/` 는 `_build_setup.py` 의 INCLUDE_DIRS 에 없고, EXCLUDE_PARTS 에도
`archive`/`reference`/`backup` 세그먼트가 등록되어 있어 setup.py 산출물에
포함되지 않는다.
