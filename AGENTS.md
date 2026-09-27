# Flow contributor instructions

## Model delegation and token budget

- The user authorizes lower-model subagents for simple, bounded Flow work. Keep the main agent responsible for design decisions, S0/history invariants, cache correctness, permissions, concurrency, and final review.
- Delegate independent file inventories, reference checks, small UI/copy edits, and focused verification to `gpt-5.6-luna` at low or medium reasoning when that model is available. Use `gpt-5.6-sol` for a bounded implementation that needs more reasoning. If unavailable, choose an available inexpensive coding model; do not silently change the main task model.
- Give each subagent only its goal, relevant paths, constraints, and acceptance checks. The main agent inspects delegated diffs and runs the relevant checks before completion.
- Read only relevant file sections. Avoid full-repository dumps, repeated unchanged polling, and tests unrelated to the changed behavior.

## Deployment contract

- `python _build_setup.py` rebuilds `frontend/dist` and the self-contained `setup.py`; the installer is the deployment. A source change that is not rebuilt is not deployed.
- The retired Flow-i agent runtime (`FLOWI_EXCLUDE_*` in `_build_setup.py`) is not shipped. The active home agent is `backend/routers/data_chat.py` + `backend/core/data_chat*.py`.
- `config/` and data files are seed-only. Every new setting needs its default in code.
- Never `git add -A` (runtime data and caches live in the tree). The GitHub repository is public: no internal documents, data, or reports.

## Server roles

- Production API: 5 cores / 30 GB. Development worker: 5 cores / 16 GB. Both see the same `FLOW_DB_ROOT` and `FLOW_DATA_ROOT`.
- Heavy work goes through `core.worker_dispatch.run_heavy()` with a local fallback. Production must remain fully functional when the worker is offline or overloaded; the worker only reduces production load.
- Budgets are derived from the detected host (`core/runtime_limits.py`, `core/cache_budget.py`); do not hard-code machine sizes.

## UI and design system

- Colors, radii, and spacing come from `frontend/src/styles/tokens.css`. `components.css`, `layouts.css`, and `utilities.css` must not contain raw colors, `!important`, or style-attribute selectors (`npm run design:check`).
- Work pages share the Carbon-like page layer (`flow-connected-page`): square 2-4 px corners, 1 px neutral borders, no drop shadows, 32 px controls. Use the accent color for the primary action and state, not for panel borders.
- Charts render through `FlowPlotlyChart` and size themselves from `lib/chartLayout.js`; do not pass fixed heights. New Plotly trace types must be registered in `lib/plotlyCustom.js`.
- When a Flow UI asks users to enter or paste row-and-column data, use `components/SpreadsheetPasteGrid.jsx`: direct multi-cell paste from Excel and Google Sheets, about 10 visible rows with an internal scrollbar and sticky headers, per-cell editing, row numbers, and clear validation. Textareas remain for prose, code, SQL, formulas, and one-dimensional input.

## Verification

- Frontend: `cd frontend && npm run check` (design check, feature boundaries, production build).
- Backend: run the `pytest` modules that cover the changed behavior.
