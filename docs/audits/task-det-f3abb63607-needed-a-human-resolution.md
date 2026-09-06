<!-- CUI // SP-CTI -->
# task-det-f3abb63607 — `needed_a_human` for task-det-cd1d099fff: a STALE BASE, and both red checks were somebody else's

- **Task:** task-det-f3abb63607 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `f3abb6360738cb62`, `card_count` 1)
- **Subject:** task-det-cd1d099fff — PR #2059, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-09-05 18:03Z, against the live PG board

## Verdict

The subject's branch had **no defect in it**. It carries exactly one new file —
`docs/audits/task-det-cd1d099fff-needed-a-human-resolution.md`, 127 lines of
markdown — and its two red required checks were both inherited from a base era
that `origin/main` has since repaired. The branch was **47 commits behind main**
(`ahead 1, behind 47, status diverged`) when the watcher gave up on it.

Five LLM resumes could not fix that, and the escalation at 12:34:43Z was
correct: a resume is asked to repair the branch, and the failures were not in
the branch.

## The actual cause

The watcher's ledger for the subject (`audit_trail`, `action LIKE
'pr_watcher.%'`, `details.task_id = 'task-det-cd1d099fff'`; UTC):

| action | rows | first | last |
|---|---|---|---|
| `pr_watcher.resume` | 5 | 2026-09-04 11:53:27 | 2026-09-04 12:34:08 |
| `pr_watcher.escalate` | 1 | 2026-09-04 12:34:43 | — |
| `pr_watcher.wait` | 50 | 2026-09-04 11:38:31 | 2026-09-05 17:25:33 |

There is no `rebase` or `rebase_failed` row: the forge reported the PR
`MERGEABLE` / `BLOCKED`, so nothing ever tried to move it forward. `BLOCKED`
here means *red required checks*, not a conflict — and that is the whole
finding, because a PR that merges cleanly and fails on a base it did not
choose looks identical, to a resume prompt, to a PR that is genuinely broken.

Both failures on run `33868897037` (head `1117fa6e0`, 2026-09-04 11:38Z) are
`origin/main`'s, not the subject's:

| check | failure | whose |
|---|---|---|
| `E2E Shard 1..4 of 4` (all four) | `SyntaxError: /home/runner/work/icdev/icdev/tests/e2e/key_pages_smoke.spec.ts: Unexpected token (115:0)` | the #2052 (rmf-ui-16) squash truncated that spec; Playwright loads every spec before running one, so **all four shards died at COLLECTION** and zero tests executed |
| `Test Shard 2 of 4` | `test_mirror_drift_baseline.py::test_no_new_package_starts_drifting — package(s) newly out of mirror parity: ['db']` | the rmf-rfp-01 incident: `tools/db/schema/*` changed without their `icdev/` twins |

Both are the two incidents `CLAUDE.md`'s mfx-ci-01 block names by name, and
both were repaired on main *after* this run: `a72192653` (#2060, "close the
compliance-hub test the rmf-ui-16 merge truncated — every Playwright shard on
main fails at collection") and the 199-file `db` mirror reconciliation.
Re-derived on a tree at `df1dae7fb` (2026-09-05): `origin/main`'s
`key_pages_smoke.spec.ts` is 111 lines and closes properly, and
`pytest tests/test_mirror_drift_baseline.py` reports **9 passed**.

So the class is the one the card's own text names — *a stale branch* — and it
is the class an LLM resume structurally cannot address.

## The repair, by hand

`git merge origin/main` **into** `kanban/task-det-cd1d099fff`, not a rebase:
history-rewriting git operations are refused on this host, and a merge needs no
force-push. It was a clean merge with no conflicted hunk (the branch adds one
file nothing else touches), and the result is provably docs-only:

```
git diff origin/main --stat
 ...ask-det-cd1d099fff-needed-a-human-resolution.md | 127 +++++++++++++++++++++
 1 file changed, 127 insertions(+)
git rev-list --count origin/main..HEAD   # 2 (the doc + the merge)
git rev-list --count HEAD..origin/main   # 0
```

Pushed fast-forward `1117fa6e0..9da0d7b0f`; CI re-ran as run `33982856002` on a
base 0 behind. Nothing in the subject's deliverable was edited — the repair was
entirely to *what it sits on top of*.

The `docs/audits/…` record the subject ships is itself accurate and was checked
rather than assumed: its subject rmf-ui-07 is `done`, #2044 merged at
2026-09-03 23:04:47Z, and its own finding `cd1d099fffe3ddc5` is now `cleared`.

## When this finding clears, and why it still read `active` at the last cycle

`_recovery_rows` (`tools/awareness/claims.py`) reads a **24-hour** window of
`pr_watcher.rebase/resume/escalate/merge` rows, and `summarize_recovery` drops
a task with no attempt row inside it. The subject leaves the derivation when
its last `resume` ages out:

- **clear-by:** 2026-09-05 **12:34:08Z** (last resume 12:34:08.003Z + 24h)
- the last `detector_findings_reflex` cycle ran at **12:24:18Z** — *ten minutes
  before* that instant — which is why `f3abb6360738cb62` still reads `active`
  with `seen_count` 4 while the derivation run at 18:03Z returns `[]`. Nothing
  is wrong: the reflex marks a finding `cleared` on the first MEASURABLE cycle
  that no longer reports it, and no such cycle has run yet.
- the precedent is on the same board: the subject's own finding
  `cd1d099fffe3ddc5` (rmf-ui-07) was `active` past its clear-by and was marked
  `cleared` at 2026-09-05 00:00:00.735Z by exactly this mechanism.

An early close is **held, not re-filed** — task-f05d2bc8d1 (#2057) taught
`detector_findings` that a recovery finding cannot clear before
`earliest_clear_at`, so a terminal card inside the window keeps the finding
active on its `task_id` and files no `-rN`.

The `merge` that lands #2059 is **not** a recovery: `summarize_recovery` gives
`escalate` priority over any later `merge`, because that merge is the human the
escalation asked for. Counting it would inflate the recovery panel — the exact
rem-hyg-16 defect.

## Not this card's defect, reported rather than folded in

Every one of the subject's last `pr_watcher.wait` rows, through 2026-09-05
17:25:33Z, carries the same reason:

```
fetch failed: gh pr view failed: exit=1
stderr=GraphQL: API rate limit already exceeded for user ID 263484343.
```

That is a forge outage affecting the whole fleet, not a verdict about this PR,
and it is why the watcher had not re-evaluated #2059 in the hours before this
repair. It is named here so a reader does not mistake 50 `wait` rows for 50
refusals of this branch.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000)
       if e['task_id'] == 'task-det-cd1d099fff'])
EOF
# [] since 2026-09-05 12:34:08Z.
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
```
