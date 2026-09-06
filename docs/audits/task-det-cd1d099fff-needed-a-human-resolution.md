<!-- CUI // SP-CTI -->
# task-det-cd1d099fff — `needed_a_human` finding for rmf-ui-07, resolved by hand before the card was filed

- **Task:** task-det-cd1d099fff (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `cd1d099fffe3ddc5`, `card_count` 1)
- **Subject:** rmf-ui-07 — PR #2044, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-09-04 11:31Z, against the live PG board

## Verdict

Nothing is left to land for the subject. The card's own instruction — *find the
actual cause, land it by hand, and release the claim* — was carried out by a
human on 2026-09-03, **before the card was filed**:

| Criterion | Measured 2026-09-04 11:31Z |
|---|---|
| subject rmf-ui-07 | `done` since 23:06:59Z 2026-09-03, `completed_via_bypass` unset |
| PR #2044 | `MERGED` at 23:04:47Z, merge commit `96a8c4056` |
| deliverable on `origin/main` | `@bp.route("/poam")` at tools/boundary_canvas/blueprint.py:1671 rendering `boundary_canvas/poam/list.html` |
| `kanban:task:rmf-ui-07` lease | no lease file under `.tmp/coordination/leases` — released |
| the derivation still reports `rmf-ui-07` | **yes** — `outcome: needed_a_human`, `merged: true`, `at: 21:01:41` |
| `detector_findings.cd1d099fffe3ddc5` | `active`, `seen_count 1`, `last_seen_at 23:56:24Z` |

The last two rows are not a defect in the repair. They are the rem-hyg-16 rule
working, and they cannot read otherwise before 21:01:42Z today.

## The actual cause, and who fixed it

The watcher's ledger for the subject (`audit_trail`, `action LIKE
'pr_watcher.%'`, `details.task_id = 'rmf-ui-07'`; all times UTC 2026-09-03):

| action | rows | first | last |
|---|---|---|---|
| `pr_watcher.rebase_failed` | 4 | 20:12:41 | 20:32:17 |
| `pr_watcher.resume` | 5 | 20:12:41 | 21:01:41 |
| `pr_watcher.wait` | 40 | 20:16:10 | 23:03:47 |
| `pr_watcher.escalate` | 1 | 21:02:21 | — |
| `pr_watcher.sibling_conflict_warn` | 1 | 23:04:42 | — |
| `pr_watcher.merge` | 2 | 23:04:46 | 23:06:59 |

The cause was a **real sibling conflict**, the class the card's text names.
Every `rebase_failed` row says the same thing: `rebase onto origin/main hit
conflicts: Could not apply 902b186dc` against base `d2b4383e5`, which is
rmf-ui-05 (`/boundary/cato-health`, #2043) landing first. rmf-ui-05 and
rmf-ui-07 are siblings of one epic and append to the same shared lines — the
Pages line in `.claude/commands/start.md`, the Compliance dropdown in both
`base.html` copies, the BDC blueprint, and the full-coverage e2e table — so
the card that lands second conflicts on every hunk. Five LLM resumes could
not fix it: a worker session cannot force-push a rebased branch, and the
branch it was asked to repair had no defect in it. The escalation at 21:02:21
was correct.

A human did the repair the escalation asked for: `02dc6ead2` (22:46:00Z,
operator CLI session) merged `origin/main` **into** `kanban/rmf-ui-07`,
resolved each hunk as the union of main's `/boundary/cato-health` and this
card's `/boundary/poam` token, link, route block and e2e row, kept both
`icdev/` mirrors byte-identical, and pushed a fast-forward. CI went green and
the watcher merged #2044 at 23:04:46Z, moving the task to `done` at 23:06:59Z.

The merge after the escalation is correctly **not** counted as a recovery:
`summarize_recovery` gives `escalate` priority over any later `merge`, because
that merge is the human the escalation asked for. Counting it would inflate the
recovery panel — the exact rem-hyg-16 defect.

## When the finding clears, and why this card is safe to close before then

`_recovery_rows` (`tools/awareness/claims.py`) reads only the last 24 hours of
`pr_watcher.rebase/resume/escalate/merge` rows, and `summarize_recovery` drops
a task with **no attempt row in the window** — `merge` rows alone never make
an entry. The subject leaves the derivation only when its last `resume` row
ages out:

- **clear-by:** 2026-09-04 **21:01:42Z** (last resume 21:01:41.657Z + 24h) —
  the same value the detector now records on the finding as
  `earliest_clear_at` (#2057).
- `detector_findings_reflex` runs every 6h. Its last run was 2026-09-03
  23:56:26Z: the genesis daemon was down from about then until it was
  relaunched at 11:28:33Z on 09-04 (pid 19552), so no cycle ran overnight and
  the reflex is due at the daemon's next sweep. From that cycle the cadence
  falls roughly every six hours; the first MEASURABLE cycle after clear-by is
  the one that marks the row `cleared`, expected late on 09-04 UTC.

This card does **not** need to be held open until then, and the precedent that
said otherwise has since been measured twice:

1. **The rule that re-filed an early-closed card is gone.** PR #2057
   (task-f05d2bc8d1, merged 02:00Z 09-04) taught `detector_findings` that a
   recovery finding cannot clear before `earliest_clear_at`, so a terminal
   card before that instant is HELD (`held_closed_early`: finding stays active
   on its task_id, `seen_count` rises, nothing is filed) and a `-rN` is filed
   only by a measurable run after that time that still reports the subject.
   The main checkout the daemon runs from carries `fb989f6ad`, and the daemon
   itself started after the merge, so the live reflex applies the new rule.
2. **The `hold` label does not hold a kanban PR.** The record for
   task-det-2d74ec6cdc-r2 (#2058) held its PR with the `hold` label, which the
   local watcher honours. `.github/workflows/pr-watcher.yml` does not read
   labels at all — it merges any non-draft `kanban/*` PR whose required checks
   are green — and merged #2058 at 05:31:35Z (`app/github-actions`), still
   labelled, six and a half hours before its finding's clear-by. The board then
   read that card `done` at 11:29:36Z, inside its window. The combination that
   survives both mergers is **draft + `hold`**: the workflow skips drafts and
   the watcher refuses to un-draft a held PR. It is not needed here because of
   point 1, and it is recorded so the next session does not repeat #2058.

So this record lands as the card's ordinary PR. The pipeline merges it and
marks the card `done`; the finding is held on this task_id until 21:01:42Z and
cleared by the first measurable reflex cycle after that. If a `-r2` card for
this finding ever appears, the reflex ran OLD code, and the fix is a daemon
restart from a tree carrying #2057 — never an edit to the detector.

Three sibling findings are `active` on the same board for the same reason
(rmf-ui-12, rmf-ui-13, rmf-ui-03), each with its subject already `done`. They
clear on their own clocks and are not touched here.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'rmf-ui-07'])
EOF
# Expect [] after 2026-09-04 21:01:42Z. Before that, one needed_a_human entry
# with merged=true — the subject is done and the entry is a statement about
# the past.
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
```
