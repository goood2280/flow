---
doc_id: knowledge_vault_overview
kind: ontology
title: Knowledge Vault Overview
summary: Flow Knowledge Vault skeleton: raw events, wiki pages, graph, and search index.
actor: hol
created_at: 2026-05-07T23:14:47+09:00
updated_at: 2026-05-07T23:14:47+09:00
tags: ["knowledge", "ontology", "system"]
---

# Knowledge Vault Overview

## Purpose

Knowledge Vault keeps immutable operational events under raw/, human-readable pages under wiki/, and deterministic relationships under graph/.

## Canonical identity

- product
- root_lot_id
- wafer_id
- LOT_WF = root_lot_id + '_' + wafer_id

## Extension points

Company-specific rules should live in domain packs, matching tables, and templates rather than core code.
