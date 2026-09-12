# Does an epic CLAIM this task id? Surveyed, then armed to `report` (rem-hyg-03/04)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.identity_survey --json            # every id, machine-readable
python -m tools.kanban.identity_survey                   # per-card table + headline rates
python -m tools.kanban.identity_survey --env-file /path/to/.env   # running from a worktree
```

KANBAN_IDENTITY_CHECK=enforce|report|off, default `report` — same shape as
KANBAN_LANDED_CHECK, read by tools/kanban/task_identity.py::mode and consulted by
create_tasks BEFORE any insert, so a refusal cannot half-land a batch. The default is
what the survey SUPPORTS, not arming left half-done. Measured 2026-08-16 on the live
board (3,244 rows): refusing every unclaimed id = 35.17%. The NARROWING is the finding:
789 of the 1,119 no_card rows are opaque machine ids — `task-<hex>` is what the
dashboard's own create-task API and `awareness/suggested_card_writer` generate — so
refusing them refuses routine work, the exact defect the PreToolUse survey found.
Exempting them (`is_enforceable`, the ONE predicate the survey's NARROWED column and
the seeder's refusal both call) gives 10.85% lifetime and 15.81% over 30d — ten times
the rate this file already calls refusing routine work, so `enforce` is offered and
documented, not defaulted. Re-survey before changing that; do NOT instead widen an
exemption list, and do NOT drop `no_card`, which is the case the card exists for.
Scope: 95 modules INSERT INTO kanban_tasks directly and never reach `create_tasks`.
UNMEASURABLE is never 0%: an unreadable projects.yaml, and an empty board — the worktree
trap, where a missing .env silently reads a throwaway SQLite DB. The survey itself stays
REPORT ONLY with no --gate; `enforcement.mode` in its output says which posture is live.
