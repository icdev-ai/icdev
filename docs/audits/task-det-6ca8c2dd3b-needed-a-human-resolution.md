<!-- CUI // SP-CTI -->
# task-det-6ca8c2dd3b — `needed_a_human` finding for rmf-ui-11: a real sibling conflict, repaired by hand, and the finding was already `cleared` when the card was dispatched

- **Task:** task-det-6ca8c2dd3b (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `6ca8c2dd3b67715e`, `card_count` 1)
- **Subject:** rmf-ui-11 — PR #2050, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-09-05 17:40Z, against the live PG board

## Verdict

Nothing is left to land for the subject, and **both acceptance criteria were
already satisfied before this card was dispatched**. The card's own
instruction — *find the actual cause, land it by hand, and release the claim* —
was carried out by a human on 2026-09-04 at 01:25:29Z, ~10.5 hours before the
card was filed.

| Criterion | Measured 2026-09-05 17:40Z |
|---|---|
| subject rmf-ui-11 | `done` since 2026-09-04 01:48:13Z |
| PR #2050 | `MERGED` 2026-09-04T01:46:43Z, merge commit `86e5f65a9` |
| `86e5f65a9` is an ancestor of `origin/main` | **yes** (`git merge-base --is-ancestor`) |
| `kanban:task:rmf-ui-11` lease / claim file | neither exists — released |
| the derivation reports `rmf-ui-11` | **no** — `[]` |
| the derivation is MEASURABLE, not empty | **yes** — 89 `pr_watcher.*` rows in the 24h window |
| `detector_findings.6ca8c2dd3b67715e` | `cleared`, `cleared_at` 2026-09-05 06:03:51Z |

The last two rows are the ones that matter together. An empty derivation over
an unreadable or empty board is `unmeasurable`, not a clean bill of health — so
the row count is reported beside the verdict rather than the verdict alone.

## The actual cause

The watcher's ledger for the subject (`audit_trail`, `action LIKE
'pr_watcher.%'`, grouped; all times UTC):

| action | rows | first | last |
|---|---|---|---|
| `pr_watcher.rebase_failed` | 13 | 09-03 23:21:16 | 09-04 01:25:14 |
| `pr_watcher.resume` | 5 | 09-03 23:21:16 | 09-04 00:12:04 |
| `pr_watcher.wait` | 40 | 09-03 23:25:12 | 09-04 01:45:46 |
| `pr_watcher.escalate` | 1 | 09-04 00:13:10 | — |
| `pr_watcher.sibling_conflict_warn` | 1 | 09-04 01:46:37 | — |
| `pr_watcher.merge` | 2 | 09-04 01:46:42 | 09-04 01:48:13 |

The cause was a **real sibling conflict**, the class the card's text names — not
a stale forge cache and not a union-only collision. Every one of the 13 rebase
attempts aborted on the same commit:

```
rebase onto origin/main hit conflicts: Could not apply 897879d56...
```

and the watcher's own `sibling_conflict_warn` names the siblings and the files:

- **#2052** — `tools/dashboard/app.py`, `icdev/tools/dashboard/app.py`,
  `docs/features/rmf-ui-compliance-route-migration.md`,
  `tests/e2e/key_pages_smoke.spec.ts`,
  `tests/e2e/nav_intelligence_compliance.spec.ts`, `tests/e2e_ui_full_coverage.py`
- **#2051** — the same shared `app.py` pair and feature doc, plus
  `tools/boundary_canvas/blueprint.py` and its `icdev/` mirror

Three sibling cards in one epic migrating routes onto the same canvas append to
the same route table, the same nav block and the same shared feature doc. Once a
sibling squash-merged, the branch's own commit no longer applied.

Five LLM resumes could not fix this and were never going to: **the branch the
worker is asked to repair has no defect in it**. The escalation at 00:13:10 was
correct — it is the watcher reporting that its one automated repair (rebase +
`--force-with-lease`) does not address this cause.

## Who fixed it, and why the later merge is correctly not a recovery

`gh pr view 2050 --json commits` — the branch's own history:

| committed (UTC) | commit | what |
|---|---|---|
| 09-03 23:01:05 | `897879d56` | the card's work |
| 09-03 23:04:51 | `cb29d9a54` | fix: hub IQE endpoint import |
| 09-03 23:14:28 | `72c004bae` | fix: dead IQE example chips |
| 09-03 23:22:39 | `a7700c642` | merge `origin/main` into the card branch |
| **09-04 01:25:29** | **`e5464045e`** | **"Merge origin/main into the card branch after a sibling squash-merged"** |

`e5464045e` is the repair the escalation asked for, made by a human, one minute
after the last failed rebase. The watcher merged #2050 twenty-one minutes later.

So the `merge` rows at 01:46:42 and 01:48:13 sit **after** the escalation and
**after** a human's merge commit. `summarize_recovery` gives `escalate`
priority over any later `merge`, so the outcome stayed `needed_a_human` and this
was never counted as a recovery. That is the rem-hyg-16 rule working exactly as
written: counting it would have credited the watcher with a repair a person
made, inflating the recovery panel — the defect rem-hyg-16 exists to refuse.

## Why the finding cleared, and why no `hold` is needed here

`_recovery_rows` (`tools/awareness/claims.py`) reads only the last 24 hours of
`pr_watcher.rebase/resume/escalate/merge` rows, and `summarize_recovery` drops a
task with no attempt row in the window. So the subject leaves the derivation
when its last `resume` row ages out:

- last `resume`: 2026-09-04 **00:12:04Z**
- clear-by: 2026-09-05 **00:12:04Z**
- reflex cycle 2026-09-05 00:00:00Z — 12 minutes early, still reported
  (`last_seen_at`, `seen_count` 3)
- reflex cycle 2026-09-05 **06:03:51Z** — first MEASURABLE cycle past clear-by,
  row marked **`cleared`**

This card is being closed at ~17:40Z on 09-05, **after** `cleared_at`. The
task-f05d2bc8d1 survey found that every finding whose card closed after
`cleared_at` never recurred (and all 3 lifetime `-r2` recovery cards were cards
closed *inside* the window). The `earliest_clear_at` / `held_closed_early` rule
now shipped in `detector_findings.py` covers the same case structurally. So
unlike task-det-2d74ec6cdc-r2, this record does **not** need the `hold` label —
there is no open window left for a `-r2` to be filed from.

## What was not done, deliberately

Neither the detector, its threshold nor its 24h window was touched. The finding
was true when filed, the escalation it describes was real, and the derivation
cleared on its own evidence. An actuator never edits what it verifies.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
rows = _recovery_rows()
print("window rows:", len(rows))          # 0 here means UNMEASURABLE, not clean
print([e for e in summarize_recovery(rows, limit=10_000) if e['task_id'] == 'rmf-ui-11'])
EOF
# Expect a non-zero row count and [].

python -m tools.kanban.detector_findings --list --status cleared   # 6ca8c2dd3b67715e
```
