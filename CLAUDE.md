# Claude Guide for Flow

Start here for Flow app work:

1. Read `docs/flowi-core/README.md`.
2. Read `docs/flowi-core/TODO.md`.
3. For implementation, open only the matching feature doc first:
   - FileBrowser: `docs/flowi-core/FILEBROWSER.md`
   - SplitTable: `docs/flowi-core/SPLITTABLE.md`
   - Inform Log: `docs/flowi-core/INFORM_LOG.md`
4. Use `docs/flowi-core/ENTRYPOINTS.md` for run/build/API entrypoints.

Rules:

- Treat `docs/flowi-core/TODO.md` as the only active TODO list.
- Do not treat `.codex_task_*_spec.txt`, archive notes, or runtime logs as current source of truth unless the user explicitly asks.
- Before edits, run `git status --short` and preserve unrelated user changes.
- Keep Flowi as an app-action router. It can query and guide FileBrowser/SplitTable/Inform Log workflows, but it must not mutate source code or raw DB files from normal user prompts.
- Default validation:

```bash
git diff --check
cd frontend && npm run build
python scripts/smoke_test.py
```

For doc-only changes, `git diff --check` is enough.
