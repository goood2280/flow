# Claude Guide for Flow

Start here for Flow app work:

1. Read `README.md`.
2. Read `docs/README.md`.
3. Read `docs/features/README.md`.
4. Open the matching feature doc under `docs/features/` before editing that feature.
5. Use `docs/DEVELOPMENT.md` for scope, validation, and refactor rules.

Rules:

- Treat `docs/features/` plus `docs/DEVELOPMENT.md` as the current implementation guide.
- Do not treat `.codex_task_*_spec.txt`, archive notes, runtime logs, or moved legacy docs as current source of truth unless the user explicitly asks.
- Before edits, run `git status --short` and preserve unrelated user changes.
- Keep Flowi as an app-action router. It can query and guide FileBrowser, SplitTable, Inform Log, Dashboard, Tracker, and other app workflows, but it must not mutate source code or raw DB files from normal user prompts.
- Default validation:

```bash
git diff --check
cd frontend && npm run build
python scripts/smoke_test.py
```

For doc-only changes, `git diff --check` is enough.
