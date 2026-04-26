# Flow Data Policy

This repository keeps the small demo/seed dataset needed to run the Flow app locally.

Tracked by default:
- `data/Fab/`: compact sample parquet/csv files for FileBrowser, SplitTable, Dashboard, ET Report, and ML demos.
- `data/flow-data/`: app seed/config JSON/YAML such as dashboard charts, product config, reformatter rules, table maps, and demo content.

Ignored by default:
- `data/flow-data/_backups/`: generated startup/runtime backups.
- `data/flow-data/logs/`: append-only runtime logs.
- `data/flow-data/sessions/`: session/token state.
- `data/flow-data/dbmap/archive/`: generated table-map history snapshots.
- `data/flow-data/calendar/`, `meetings/`, `messages/`, `notifications/`: local collaboration/runtime state.
- `data/flow-data/tracker/issues.json`, `tracker/images/`, `et_reports/`: generated issue/report content and attachments.
- `data/flow-data/users.json`: local account runtime state; `users.csv` remains the seed file.

Do not put real production raw data, credentials, session tokens, or private user exports in Git. If the dataset grows beyond a small demo size, keep the code in Git and move raw data to object storage, a mounted volume, DVC, or Git LFS.
