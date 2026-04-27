# Flow Data Policy

이 저장소의 `data/`는 로컬 실행에 필요한 작은 demo/seed 데이터와 runtime 상태의 기본 위치다. 운영에서는 `FLOW_DB_ROOT`, `FLOW_DATA_ROOT`로 코드 밖 공유 경로를 쓰는 것을 기본으로 한다.

## Roots

| 경로 | 의미 |
|---|---|
| `data/Fab/` | 로컬 DB root. FAB/INLINE/ET parquet, rulebook CSV, `ML_TABLE_*.parquet` |
| `data/flow-data/` | 로컬 data root. 사용자, 설정, tracker, informs, meetings, calendar, backup 상태 |

`base_root`는 별도 디렉터리가 아니라 `db_root`와 같은 경로를 반환하는 호환 alias다.

## Track By Default

- `data/Fab/`: 작게 유지된 demo parquet/csv와 matching/rulebook 파일
- `data/flow-data/`: seed/config JSON/YAML. 예: dashboard charts, dbmap config, product config, reformatter rules, source config

## Ignore By Default

런타임에 계속 바뀌거나 크기가 커질 수 있는 파일은 Git에 넣지 않는다.

- `data/Fab/_backups/`
- `data/flow-data/_backups/`
- `data/flow-data/logs/`
- `data/flow-data/sessions/`
- `data/flow-data/calendar/`, `meetings/`, `messages/`, `notifications/`
- `data/flow-data/dbmap/archive/`
- `data/flow-data/tracker/issues.json`, `tracker/images/`, `tracker/scheduler_status.json`
- `data/flow-data/et_reports/`
- `data/flow-data/splittable/*.json` except `source_config.json`
- `data/flow-data/users.json`

`users.csv`는 seed 파일로 남기고, 실제 계정 상태는 운영 data root에서 보호한다.

## 운영 원칙

- real production raw data, credentials, session tokens, private user exports는 Git에 넣지 않는다.
- dataset이 demo 크기를 넘으면 code는 Git에 두고 raw data는 object storage, mounted volume, DVC, Git LFS 중 하나로 분리한다.
- 사내 업데이트 전후에는 `python3 scripts/preflight_internal.py --write-probe`로 `db_root`, `base_root`, `data_root`를 확인한다.
- 코드 업데이트, `setup.py`, frontend build는 `FLOW_DATA_ROOT`를 삭제하거나 덮어쓰면 안 된다.
