# Audit: km-autoclose-01 — Decomposed-Parent Auto-Close Gap
**Date:** 2026-04-26
**Task:** km-autoclose-01

## Stuck Parents Count
**0 currently stuck** as of 2026-04-26.

## Query
`kanban_tasks WHERE status IN ('in_progress','backlog','decomposed')` and ALL children
(`depends_on_task_id = parent.id`) are `done`. Zero rows returned.

## Current State
- `fd-floor-02`: 1 live child (`fd-floor-03`, backlog) — not stuck yet.
- 701 tasks have `depends_on_task_id` set; all have ≥1 non-done child.
- 616 done parents closed correctly historically.

## Root Cause
Two existing mechanisms only handle `status=decomposed`:

1. `_auto_close_decomposed_parent` (kanban.py:~1462) — per-completion, `source_prediction_id` linkage only.
2. `_close_orphaned_decomposed` (kanban.py:~4401, step 3d ~line 5026) — periodic sweep, decomposed only.

**Gap:** Parents in `in_progress`/`backlog` whose children use `depends_on_task_id` (not
`source_prediction_id`) are never swept.

## Fix Hook Point
**File:** `tools/genesis/reflexes/kanban.py`
**Where:** Add step `3e _close_completed_dep_parents()` after step 3d (~line 5028).

```sql
SELECT DISTINCT t.id FROM kanban_tasks t
WHERE t.status IN ('in_progress', 'backlog')
  AND EXISTS (SELECT 1 FROM kanban_tasks c WHERE c.depends_on_task_id = t.id)
  AND NOT EXISTS (
    SELECT 1 FROM kanban_tasks c
    WHERE c.depends_on_task_id = t.id AND c.status != 'done'
  )
```

Then call `_move_task(tid, 'done')` for each result.

**Implementation note:** Periodic sweep preferred over hot-path hook to avoid cascading
calls during batch completions.
