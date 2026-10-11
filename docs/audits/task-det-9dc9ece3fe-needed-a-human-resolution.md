<!-- CUI // SP-CTI -->
# task-det-9dc9ece3fe — `needed_a_human` for aicur-eng-03: landed by hand, finding already cleared

- **Task:** task-det-9dc9ece3fe (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `9dc9ece3fee92f8b`)
- **Subject:** aicur-eng-03 — icdev PR #2336 (`kanban/aicur-eng-03`),
  `feat(academy): m-aie-03 vibe coding vs engineering`, escalated by pr_watcher
  after 10 `rebase_failed` attempts
- **Date measured:** 2026-10-11, against the live PG board

## Verdict

Nothing is left to land. The escalation was correct and the human fix it asked
for has already happened:

- PR #2336 **MERGED** at 2026-10-06 10:55:45Z, merge commit `299f56e3b`, which is
  on `origin/main`. That was about 1.5h after the 09:27:55Z escalation.
- Board row `aicur-eng-03` reads `done`.
- The card's derivation (`summarize_recovery(_recovery_rows())` filtered to
  `aicur-eng-03`) returns `[]`.
- `detector_findings` row `9dc9ece3fee92f8b` reads `status=cleared`,
  `cleared_at` 2026-10-07 09:47:10, `seen_count` 4.

## Why the watcher could not fix it

The branch carried merge-commit resolutions of its conflicts with main:
`d1c435d35` ("Merge origin/main into aicur-eng-03; m-aie-03 joins the m-aie
series") and `e0ca25d01` ("... after aicur-fun-05, aicur-fix-02, aicur-fun-02").
pr_watcher's repair is `git rebase origin/main`, and a rebase replays only the
linear commits. So it tried to re-apply `b6ab0af7a` (the original feature
commit) onto a main that the m-aie / aicur siblings had moved, without the
resolution carried by the merge commits, and conflicted every time.

The watcher's ledger for the subject (`audit_trail`, `pr_watcher.%`):
`rebase_failed` 16, `union_refused` 16, `resume` 1, `escalate` 1,
`sibling_conflict_warn` 1 (shared `apps/forge_academy/content_loader.py` with
#2345), `wait` 331. An LLM resume could not help, because there was no defect
in the branch. The only fix was a merge by a human or a merge of main into the
branch, and that is what landed.

As the card says, the 10:55:45Z merge is a human's merge and is **not** a
recovery. `summarize_recovery` ranks `escalate` above any later `merge`.

## Closing this card

There was no code change to make. This note is the card's artifact. The
detector, its threshold and its window were not touched.
