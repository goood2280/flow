# Semantic Source: step_matching

## Source Contract

- ID: `step_matching`
- Role: step ID and function step matching
- Primary path pattern: `FLOW_DB_ROOT/Vehicle_matching.csv`
- Fallback path pattern: `FLOW_DB_ROOT/step_matching.csv`
- Primary columns: `product`, `step_id`, `function_step`, `step_desc`
- Related question IDs: `Q2`

## Owner And Writes

The source is owned by SplitTable matching-table and FileBrowser base-file
paths. The Agent semantic layer treats it as read-only. CSV updates require the
existing owner save and review path.

## Agent Use

Use this source when a prompt mentions `step_id`, `function_step`, step
description, vehicle matching, or process step aliases. Deterministic unit
lookup can answer direct step matching questions without LLM routing.
