# Admin

Admin은 사용자, 권한, 그룹, root, 백업, 메일/API/LLM 설정, 모니터링을 관리한다.

## Owns

- user/group/permission 관리
- data root, S3, mail, LLM, backup, monitor 설정
- Base CSV 편집기와 product YAML 관리
- 관리자 공지와 운영 설정

## Does Not Own

- 일반 사용자의 업무 화면 상태 변경
- raw DB 파일 임의 수정
- 사용자 대신 issue/inform 업무를 생성하는 기능
- **캐시 운영 패널** — Admin은 SplitTable 매칭 캐시나 Tracker Analysis ET 캐시를 소유하지 않는다. FileBrowser가 LOT 진행 최신 캐시의 상태/수동 갱신 진입점을 제공한다.

## Code Entrypoints

| Layer | Path |
|---|---|
| Frontend page | `frontend/src/pages/My_Admin.jsx` |
| Admin router | `backend/routers/admin.py` |
| Groups router | `backend/routers/groups.py` |
| Monitor router | `backend/routers/monitor.py` |
| Catalog router | `backend/routers/catalog.py` |
| Product config | `data/flow-data/product_config/` |

## Guardrails

- 권한 없는 사용자가 설정을 읽거나 바꾸지 못해야 한다.
- 운영 설정은 사용자 기능과 섞지 않는다.
- 변경 전후 진단과 rollback 후보를 보여준다.
- root/path 변경은 `docs/SOFT_LANDING_INTERNAL.md`와 preflight 기준을 따른다.

## Verify

```bash
git diff --check
python3 scripts/preflight_internal.py --write-probe
```
