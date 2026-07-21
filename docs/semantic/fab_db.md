# Semantic Source: fab_db

## Source Contract

- ID: `fab_db`
- Role: FAB raw parquet and latest lot progress evidence
- Path pattern: `FLOW_DB_ROOT/1.RAWDATA_DB_FAB/<product>/**/*.parquet`
- Primary columns: `lot_id`, `root_lot_id`, `wafer_id`, `step_id`, `equipment`, `tkout_time`
- Related question IDs: `Q3`

## Owner And Writes

The source is owned by DB operations, FileBrowser, and lot-progress cache jobs.
The Agent semantic layer treats the raw parquet as read-only. Cache refreshes
and source-data updates must happen through their owner jobs or feature APIs.

## Agent Use

Use this source for current FAB location, latest step, equipment, and lot
progress questions. The resolver can use it as a source-context priority when
the selected FileBrowser root is FAB.
