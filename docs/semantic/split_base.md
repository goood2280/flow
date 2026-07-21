# Semantic Source: split_base

## Source Contract

- ID: `split_base`
- Role: SplitTable base parquet and raw export source
- Path pattern: `FLOW_DB_ROOT/ML_TABLE_<product>.parquet`
- Primary columns: `root_lot_id`, `wafer_id`, `fab_lot_id`, `KNOB_*`, `INLINE_*`, `VM_*`
- Related question IDs: `Q4`

## Owner And Writes

The source is owned by SplitTable and FileBrowser. Agent reads are preview and
export only. Writes require the owning feature APIs and permissions.

## Agent Use

Use this source for SplitTable view context, wafer-level table questions, and
raw export requests. Raw export question `Q4` maps here because the existing
CSV download path reads `ML_TABLE_<product>.parquet`.
