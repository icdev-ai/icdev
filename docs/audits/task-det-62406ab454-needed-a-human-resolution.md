<!-- CUI // SP-CTI -->
# task-det-62406ab454 — `needed_a_human` for aicur-fun-01: already landed by hand, finding already cleared

- **Task:** task-det-62406ab454 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `62406ab454533fe6`)
- **Subject:** aicur-fun-01 — PR #2345 (`kanban/aicur-fun-01`), escalated by
  pr_watcher after a run of `rebase_failed` attempts
- **Date measured:** 2026-10-11, against the live PG board

## Verdict

Nothing is left to land. A human merged the subject on 2026-10-06, and the
finding cleared by itself on 2026-10-07. This card was dispatched after both
had happened. Attempt #1 correctly found nothing to change, but it wrote no
record, so verification read the run as `no_commits`. This file is that record.

## The watcher's ledger for the subject

`audit_trail`, `action LIKE 'pr_watcher.%'`, details naming `aicur-fun-01`
(times UTC, 2026-10-06):

| action | count | first | last |
|---|---|---|---|
| `pr_watcher.resume` | 1 | 04:30:05 | 04:30:05 |
| `pr_watcher.escalate` | 1 | 04:40:36 | 04:40:36 |
| `pr_watcher.rebase_failed` | 31 | 04:51:47 | 10:37:56 |
| `pr_watcher.union_refused` | 31 | 04:51:47 | 10:37:56 |
| `pr_watcher.wait` | 54 | — | 11:16:29 |

## Cause

The branch carried merge-commit resolutions: `bb75ea281`, `42ec49b27` and
`f8c4887a7`, each a "Merge origin/main into aicur-fun-01" commit. A rebase onto
`origin/main` throws those resolutions away and replays the original feature
commit `69309b566` against a main that has moved. The replay conflicts on every
attempt, and the reason string records exactly that ("Could not apply
69309b566..."). This is the known kpr-watch-15 class: a branch that carries a
merge-commit resolution can never be rebased by the watcher, so you merge main
INTO it instead. An LLM resume cannot fix it, because the branch has no defect.
The union rung refused every time (`union_refused` x31), so all 31 rebase
attempts were spent against the same cause.

The last `wait` rows show a separate, ordinary hold: the enforced gate was
awaiting ICDEV done-verification, and the gate itself names the remedy
(`--reverify aicur-fun-01`).

## The resolution, by hand (already done before this card ran)

- PR #2345 **MERGED** at 2026-10-06 11:16:42Z as merge commit `4fccab862`. The
  feature commit and its three main-merges are all on `origin/main`.
- Board row `aicur-fun-01` reads `done`.
- That merge came after the 04:40:36Z escalation, so it is a human's merge. It
  does not count as a recovery: `summarize_recovery` ranks `escalate` above any
  later `merge`.

## Finding state

- `62406ab454533fe6`: `status=cleared`, `seen_count` 4, first seen
  2026-10-06 09:28:27, last seen 2026-10-07 03:29:30, **cleared 2026-10-07
  09:47:10**. The last attempt row was at 10:37:56 on 10-06, and it dropped out
  of the 24h `_recovery_rows` window after that.
- The derivation returns `[]` (re-run 2026-10-11).

The detector, its threshold and its window were not touched.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000)
       if e['task_id'] == 'aicur-fun-01'])
EOF
# []
python -m tools.kanban.detector_findings --list --status cleared | grep 62406ab454
gh pr view 2345 --json state,mergedAt
```
