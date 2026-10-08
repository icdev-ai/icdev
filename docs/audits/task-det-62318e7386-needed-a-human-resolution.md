<!-- CUI // SP-CTI -->
# task-det-62318e7386 — `needed_a_human` for ftl-bz-stream-01: a DROPPED track, nothing to land

- **Task:** task-det-62318e7386 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `62318e73862b5398`)
- **Subject:** ftl-bz-stream-01 — icdev_ft PR #467 (`kanban/ftl-bz-stream-01`),
  resumed 1x by pr_watcher, escalated
- **Date measured:** 2026-10-08, against the live PG board

## Verdict

The escalation was correct, and the fix is **not** to land the branch. The
subject is work the operator had already cancelled: the BoxZone paper-stream /
Lab follow-on track was DROPPED on 2026-10-06, after the pre-registered study
ftl-bz-study-01 (icdev_ft #420) failed its bar — median arm-B PF 0.52 against a
committed >= 1.2 (`docs/cards/ftl-bz-study-01.md` in icdev_ft).

The task had been force-done at that point. An IT kanban `orphan_sweep` re-opened
it while its parent was still `scheduled`, and the runner then built it, opening
#467. That PR's checks went red (`Test`, `E2E (Playwright)`) and it went
`CONFLICTING` against icdev_ft main. An LLM resume was asked to repair a branch
that should not exist.

## The watcher's ledger for the subject

`audit_trail`, `action LIKE 'pr_watcher.%'`, `details.task_id = 'ftl-bz-stream-01'`:

| action | at (UTC) | reason |
|---|---|---|
| `pr_watcher.resume` | 2026-10-07 11:18:37 | `ci_failed` — failing check `E2E (Playwright)`; first injection |
| `pr_watcher.escalate` | 2026-10-07 11:28:50 | resume undelivered — 1 message unread in the queue; "the repair is DELIVERY, not the branch" |

So there were two problems, and neither was in the branch. The resume never
reached a consumer (an icdev_ft task's queue has no reader on this executor),
and the work itself had been cancelled.

## The resolution, by hand

- icdev_ft PR #467 **closed without merge** at 2026-10-07 23:57:47Z, with a
  comment that names the drop and this card. The branch was kept, not deleted.
- Board row `ftl-bz-stream-01` reads `done` (updated 2026-10-07 23:57:47Z),
  which matches its pre-sweep force-done state.
- Nothing was merged. A merge here would have shipped a feature the operator
  cancelled. It would also not count as a recovery: `summarize_recovery` ranks
  `escalate` above any later `merge`.

## When this finding clears

`_recovery_rows` (`tools/awareness/claims.py`) reads a 24-hour window of
`pr_watcher.rebase/resume/escalate/merge` rows. `summarize_recovery` drops a task
that has no attempt row inside that window. The subject's only attempt is the
11:18:37Z resume. So:

- **clear-by:** 2026-10-08 **11:18:37Z** (last resume + 24h)
- `62318e73862b5398` was seen at 15:54:42Z and 21:57:40Z on 10-07 (`seen_count`
  2). The reflex cycles about every 6h, and the first cycle after clear-by marks
  it `cleared`.
- If this card closes early it is **held, not re-filed** (`earliest_clear_at`,
  task-f05d2bc8d1 / #2057).

The detector, its threshold and its window were not touched.

## Not this card's defect, reported rather than folded in

The `orphan_sweep` re-opened a force-done task in a project the operator had
dropped, and the runner then spent a build on it. A force-done row should
survive a sweep keyed on its parent's status. That belongs on a separate card
against the sweep.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000)
       if e['task_id'] == 'ftl-bz-stream-01'])
EOF
# [] after 2026-10-08 11:18:37Z.
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
```
