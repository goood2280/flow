# Inline Measurement DB

Inline measurement sources are read-only Flow-i inputs for item trend and
wafer-level value lookup workflows.

- Role: `inline_db`
- Typical path: `FLOW_DB_ROOT/**/INLINE*/<product>/**/*.parquet`
- Expected keys: `product`, `root_lot_id`, `wafer_id`, `step_id`, `item_id`, `value`
- Optional spec fields: `target`, `spec_low`, `spec_high`
- Default aggregation: `avg`

Semantic measurement terms can map user-facing names such as `CA BCD` to
`source_type=INLINE`, `product`, `step_id`, `item_id`, and spec metadata.
