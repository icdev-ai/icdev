# Loop feedback — kanban_build

Corrections applied to this loop's output. Loaded into the loop's context on
every run by `tools/agent_runtime/loop_context.py`.

Keep this file short. It competes with the actual task for context, so prune a
correction once the behaviour it describes has been enforced by a gate or a test
— at that point the gate is the better teacher.

## Corrections

- 2026-07-28 (session): Editing a file under a mirror-enforced `tools/<pkg>`
  without updating its `icdev/` twin trips `test_mirror_drift_baseline` on
  **every** branch, not just yours. Mirror both, or reconcile before pushing.
  Note `mirror_parity.py --fix` resolves its repo root as `icdev/` and writes to
  `icdev/icdev/tools/...` — copy the file yourself instead.

- 2026-07-28 (session): Do not commit the output of
  `companion.py --sync --write` without reading it. It regenerates ~82 files as
  pure CRLF churn and **deletes** the hand-written "Standalone Agent Runtime"
  section from `AGENTS.md`. Revert what it touched that you did not intend.

- 2026-07-28 (session): Run repo scripts as `python -m tools.pkg.module` from
  the repo root, never `python tools/pkg/module.py`. Running by path puts the
  script's own directory on `sys.path[0]`, which lets a site-wide `.pth` from an
  unrelated checkout shadow the `tools` package — the traceback then points at
  another repo's files and the DB it opens is not the one you expect.

- 2026-07-28 (session): A guard implemented with `git grep` needs `--untracked`.
  Without it the search covers only tracked files, so a violation introduced in
  a newly-added module passes the guard until the moment it is committed.

- 2026-07-28 (session): Bash `> /tmp/x` and Python `open('/tmp/x')` are
  different files on Windows — MSYS temp versus `C:\tmp`. Neither errors, so a
  stale file can be read back as if it were fresh output. Use an absolute path
  under the session scratchpad and pass it to both sides.
