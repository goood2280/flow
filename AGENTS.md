# Flow contributor instructions

## Model delegation and token budget

- The user authorizes lower-model subagents for simple, bounded Flow work. Keep the main agent responsible for design decisions, S0/history invariants, cache correctness, permissions, concurrency, and final review.
- Delegate independent file inventories, reference checks, small UI/copy edits, and focused verification to `gpt-5.6-luna` at low or medium reasoning when that model is available. Use `gpt-5.6-sol` for a bounded implementation that needs more reasoning. If unavailable, choose an available inexpensive coding model; do not silently change the main task model.
- Give each subagent only its goal, relevant paths, constraints, and acceptance checks. Prefer no history fork, one subagent per independent scope, and no recursive delegation. Do trivial one-line edits locally when delegation would cost more than the edit.
- The main agent must inspect delegated diffs and run the relevant checks before completion. Escalate failed or ambiguous work to the main agent instead of repeatedly spending tokens on the same failed approach.
- Read only relevant vault branches and file sections. Avoid full-repository dumps, duplicate investigations, repeated unchanged polling, and tests unrelated to the changed behavior.

## Spreadsheet-style tabular inputs

- When a Flow UI asks users to enter or paste a two-dimensional list or table, use an editable spreadsheet-style grid by default. Do not use a plain multiline textarea for tabular data.
- Support direct multi-cell paste from Microsoft Excel and Google Sheets. Pasting must work from the focused cell, preserve rows and columns, and accept a copied header row when the grid has named columns.
- Show about 10 editable data rows without expanding the page. Additional rows must remain available through an internal vertical scrollbar, with column headers kept visible.
- Keep direct per-cell editing, row numbers, clear validation feedback, and any domain preview that helps users verify values, such as a color swatch.
- Reuse `frontend/src/components/SpreadsheetPasteGrid.jsx` for this interaction unless the requested table needs behavior that the shared component cannot provide.
- Textareas remain appropriate for prose, code, SQL, formulas, or genuinely one-dimensional free-form input; this spreadsheet rule applies to structured row-and-column data.
