# Semantic Source: rulebook

## Source Contract

- ID: `rulebook`
- Role: PPID and knob rulebook lookup
- Path pattern: `FLOW_DB_ROOT/ppid_knob.csv`
- Primary columns: `feature_name`, `function_step`, `rule_order`, `operator`, `value`, `category`
- Related question IDs: `Q1`

## Owner And Writes

The source is owned by the SplitTable rulebook and FileBrowser base-file paths.
The Agent semantic layer treats it as read-only. Updates must go through the
approved manager or admin save paths that already validate the CSV contract.

## Agent Use

Use this source when a prompt asks for PPID classification, knob category,
split rule meaning, or rulebook evidence tied to `feature_name`. The resolver
may surface it in `source_catalog_matches` and in unknown-term
`search_priority` rows.
