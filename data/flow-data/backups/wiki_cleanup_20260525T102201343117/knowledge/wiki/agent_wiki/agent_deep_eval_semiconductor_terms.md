---
doc_id: agent_deep_eval_semiconductor_terms
kind: agent_wiki
title: [deep-eval] Agent semiconductor term and join rules
summary: step_id/function_step/lot_wf/KNOB multi-source join 검증용 운영 지식
actor: codex_deep_eval
created_at: 2026-05-24T20:51:13+09:00
updated_at: 2026-05-24T20:51:13+09:00
tags: ["agent", "deep-eval", "semantic", "lot_wf", "knob", "multi-db"]
relation_id: flow_agent_deep_eval
column_refs: ["step_map_db.step_id", "step_map_db.function_step", "fab_db.lot_wf", "split_db.knob", "et_db.value"]
join_keys: ["step_id", "lot_wf"]
source: scripts/flowi_agent_deep_eval.py
---

# [deep-eval] Agent semiconductor term and join rules

Flow-i Agent deep eval operational terms.

- step_id is the raw process step key. Map it through step_map_db to function_step.
- function_step is the user-facing process family such as CONTACT, PHOTO, ETCH, SORT.
- lot_wf is the wafer-level key used to join fab_db, split_db, et_db, tracker_db, and inform_db.
- KNOB/PPID terms come from SplitTable and should filter split_db before returning lot_wf lists.
- Multi-DB raw answers must preserve source columns, join keys, and row counts.
