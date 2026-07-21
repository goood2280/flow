# ET Measurement DB

ET measurement sources are read-only Flow-i inputs for electrical-test value,
trend, and correlation workflows.

- Role: `et_db`
- Typical path: `FLOW_DB_ROOT/**/ET*/<product>/**/*.parquet`
- Expected keys: `product`, `root_lot_id`, `wafer_id`, `step_id`, `item_id`, `value`
- Optional spec fields: `target`, `spec_low`, `spec_high`
- Default aggregation: `median`

Semantic measurement terms can map user-facing names such as `PCCB Chain` to
`source_type=ET`, `product`, `step_id`, `item_id`, and spec metadata.
