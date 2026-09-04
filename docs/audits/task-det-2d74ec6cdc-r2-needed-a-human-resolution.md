<!-- CUI // SP-CTI -->
# task-det-2d74ec6cdc-r2 — `needed_a_human` finding for rmf-ui-12, resolved by hand before card #1 was filed; card #2 is a window artifact

- **Task:** task-det-2d74ec6cdc-r2 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `2d74ec6cdc57ea7a`, `card_count` 2)
- **Subject:** rmf-ui-12 — PR #2032, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-09-04 01:39Z, against the live PG board

## Verdict

Nothing is left to land for the subject. The card's own instruction — *find the
actual cause, land it by hand, and release the claim* — was carried out by a
human on 2026-09-03, **before the first card was filed**:

| Criterion | Measured 2026-09-04 01:39Z |
|---|---|
| subject rmf-ui-12 | `done` since 18:09:02Z 2026-09-03, `completed_via_bypass=0` |
| PR #2032 | `MERGED` at 18:08:20Z, merge commit `b2f9ec5cd` |
| `kanban:task:rmf-ui-12` lease | no lease file — released |
| the derivation still reports `rmf-ui-12` | **yes** — `outcome: needed_a_human`, `merged: true`, `at: 12:22:01` |
| `detector_findings.2d74ec6cdc57ea7a` | `active`, `seen_count 3`, `last_seen_at 23:56:24Z` |

The last two rows are not a defect in the repair. They are the rem-hyg-16 rule
working, and the reason this card exists twice.

## The actual cause, and who fixed it

The watcher's ledger for the subject (`audit_trail`, `actor='pr_watcher'`,
grouped by `action`; all times UTC 2026-09-03):

| action | rows | first | last |
|---|---|---|---|
| `pr_watcher.wait` | 49 | 11:19:12 | 18:07:32 |
| `pr_watcher.rebase_failed` | 6 | 11:39:22 | 12:58:18 |
| `pr_watcher.resume` | 5 | 11:39:22 | 12:22:01 |
| `pr_watcher.escalate` | 1 | 12:22:44 | — |
| `pr_watcher.sibling_conflict_warn` | 1 | 18:08:14 | — |
| `pr_watcher.merge` | 2 | 18:08:20 | 18:09:02 |

The cause was a **real sibling conflict**, the class the card's text names:
rmf-ui-12 (`/security/stig-manager`) appends to the same shared lines as its
epic siblings rmf-ui-14 and rmf-ui-15 (the Pages line in
`.claude/commands/start.md`, the nav dropdown in `base.html`, the canvas
blueprint), and once those two landed the watcher's own `--force-with-lease`
rebase aborted on the first hunk six times. Five LLM resumes could not fix it:
a worker session cannot force-push a rebased branch, and the branch it was
asked to repair had no defect in it. The escalation at 12:22:44 was correct.

A human did the repair the escalation asked for: `9689c9fb3` (17:53:08Z)
merged `origin/main` **into** `kanban/rmf-ui-12`, resolved each hunk as the
union of main's side and this card's route block, and pushed a fast-forward.
The watcher then merged #2032 at 18:08:20Z and moved the task to `done`.

The merge after the escalation is correctly **not** counted as a recovery:
`summarize_recovery` gives `escalate` priority over any later `merge`, because
that merge is the human the escalation asked for. Counting it would inflate the
recovery panel — the exact rem-hyg-16 defect.

## Why there is a card #2 — and why closing card #1 caused it

| When (UTC) | Event |
|---|---|
| 09-03 12:22:01 | last `pr_watcher.resume` for rmf-ui-12 (the entry's `at`) |
| 09-03 15:17:42 | finding `2d74ec6cdc57ea7a` first seen; card #1 (task-det-2d74ec6cdc) filed |
| 09-03 17:53:08 | human merge of main into the branch (`9689c9fb3`) |
| 09-03 17:53:29 | card #1 dispatched — nothing left to land |
| 09-03 18:08:20 | #2032 merged by the watcher; rmf-ui-12 `done` at 18:09:02 |
| 09-03 18:10:41 | card #1 marked `done` |
| 09-03 23:56:24 | reflex cycle: finding still reported, card #1 terminal — card #2 (this one) filed |
| 09-04 01:38:25 | card #2 dispatched |

`_recovery_rows` (`tools/awareness/claims.py`) reads only the last 24 hours of
`pr_watcher.rebase/resume/escalate/merge` rows, and `summarize_recovery` drops
a task with **no attempt row in the window** — `merge` rows alone never make
an entry. So the subject leaves the derivation only when its last `resume` row
ages out:

- **clear-by:** 2026-09-04 **12:22:01Z** (last resume + 24h)
- `detector_findings_reflex` runs every 6h; last run 2026-09-03 23:56:26Z, so
  its cycles fall at roughly 05:56Z, 11:56Z and **17:56Z** on 09-04. The
  first MEASURABLE cycle after clear-by is the ~17:56Z one, and that is when
  the row reads `cleared`.

`detector_findings` (tools/kanban/detector_findings.py) re-files a finding
whose card is in `TERMINAL_CARD_STATUSES` (`done`, `failed`) while the
finding is still reported. Card #1 was closed at 18:10Z, six hours into an
eighteen-hour window, so the 23:56Z cycle read "the fix did not hold" and
filed this card. Nothing about the subject changed between the two cards.

This is the third such recurrence: the survey behind task-f05d2bc8d1 found
**3 of 3** lifetime recovery `-r2` cards (ff0a66836b, d46896e15d,
2d74ec6cdc) were this window artifact, and the 10 findings whose card closed
AFTER `cleared_at` never recurred. task-f05d2bc8d1 is the card against the
detector (it was `in_progress` while this was measured) and it is deliberately
**not** touched here — an actuator never edits what it verifies.

## What this card does instead of closing

Card #2 must not become `done` or `failed` before the ~17:56Z 09-04 cycle,
or the same rule files card #3. A runner-dispatched worker cannot hold its own
card with a future `scheduled_at`: the scheduler's post-run verification moves
the row itself and overwrites `scheduled_at` with now. What it CAN do is keep
the card in `pr_opened`, which is non-terminal (the control case: rmf-ui-13's
card task-det-e9a2e3ea16 sat in `validating` and its finding did not recur).

So this record lands as the card's PR, and the PR carries the **`hold`** label
(one of `NO_AUTOMERGE_LABELS` in `tools/ci/merge_readiness.py`; the label was
created for this purpose — the repo had none). The watcher reports it
`held_label` and never merges a held PR; a draft would not do, because the
watcher promotes drafts with `gh pr ready`.

**To lift the hold** (a human, or the next session), after the finding reads
cleared:

```
python -m tools.kanban.detector_findings --list --status cleared | grep 2d74ec6cdc57ea7a
gh pr edit <this PR> --remove-label hold
```

The watcher then merges it and marks this card `done` against a finding that
is already `cleared`, so nothing is re-filed. If task-f05d2bc8d1 lands first,
the hold is unnecessary and can be lifted at once.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'rmf-ui-12'])
EOF
# Expect [] after 2026-09-04 12:22:01Z. Before that, one needed_a_human entry
# with merged=true — the subject is done and the entry is a statement about
# the past.
```
