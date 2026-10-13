<!-- CUI // SP-CTI -->
# task-det-edec8bbce4 — `needed_a_human` for omx-guard-05: repaired by hand, finding already cleared

- **Task:** task-det-edec8bbce4 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `edec8bbce4047390`)
- **Subject:** omx-guard-05 — PR #2430 (`kanban/omx-guard-05`), escalated by
  pr_watcher after 1 resume
- **Date measured:** 2026-10-13, against the live PG board

## Verdict

Nothing is left to land. The branch was repaired by hand and PR #2430 merged
on 2026-10-11. The finding cleared by itself on 2026-10-12. This card was
dispatched after both had happened. Attempt #1 found nothing to change and
wrote no record, so verification read the run as `no_commits`. This file is
that record.

## The watcher's ledger for the subject

`audit_trail`, `action LIKE 'pr_watcher.%'`, details naming `omx-guard-05`
(times UTC, 2026-10-11):

| action | count | first | last |
|---|---|---|---|
| `pr_watcher.wait` | 59 | 15:52:13 | 20:39:16 |
| `pr_watcher.resume` | 1 | 16:07:23 | 16:07:23 |
| `pr_watcher.escalate` | 1 | 16:17:34 | 16:17:34 |
| `pr_watcher.sibling_conflict_warn` | 1 | 20:39:58 | 20:39:58 |
| `pr_watcher.merge` | 2 | 20:40:01 | 20:40:47 |

## Cause

The resume was for `ci_failed` (failing checks: `Test Gates`, `Test`). Ten
minutes later the watcher escalated with this reason: "resume undelivered after
1 attempt(s) — 1 pr_watcher message(s) still unread in the queue. Nothing is
consuming this task's queue … The repair is DELIVERY, not the branch." The
worker that opened the PR had already exited, so nothing was consuming the
resume. This is the class that kpr-watch-19 (resume-queue consumer on the
executor) and kpr-watch-27 (a repair session for a red PR whose worker exited,
PR #2440, merged 2026-10-12) later closed. The escalation itself was correct:
no further injection could have reached a reader.

## The resolution, by hand (already done before this card ran)

- The red CI was repaired on `fix/omx-guard-05-repair`: commit `645a370a8`
  ("copilot measures sandboxed through the runner's wrapper; B108 on a fake
  root", 20:18:45Z), then `edc9fd6c9`, which merged `origin/main` in
  (20:19:19Z).
- PR #2430 **MERGED** at 2026-10-11 20:40:00Z as merge commit `5577dea78`.
  Board row `omx-guard-05` reads `done`.
- That merge came after the 16:17:34Z escalation, so it is a human's merge. It
  does not count as a recovery: `summarize_recovery` ranks `escalate` above any
  later `merge`.

## Finding state

- `edec8bbce4047390`: `status=cleared`, `seen_count` 4, `card_count` 1, first
  seen 2026-10-11 18:33:52, last seen 2026-10-12 12:36:20, **cleared
  2026-10-12 18:46:25**. It dropped out of the `_recovery_rows` window once the
  last attempt row aged out.
- The derivation returns `[]` (re-run 2026-10-13).

The detector, its threshold and its window were not touched.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000)
       if e['task_id'] == 'omx-guard-05'])
EOF
# []
python -m tools.kanban.detector_findings --list --status cleared | grep omx-guard-05
gh pr view 2430 --json state,mergedAt
```
