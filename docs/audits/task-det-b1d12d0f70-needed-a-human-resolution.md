<!-- CUI // SP-CTI -->
# task-det-b1d12d0f70 — `needed_a_human` finding for task-42a17b8956, resolved

- **Task:** task-det-b1d12d0f70 (filed by `detector_findings_reflex`, detector
  `recovery_summary` / rem-hyg-16, finding `b1d12d0f70694dc2`)
- **Subject:** task-42a17b8956 — PR #1910, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-08-24/25, against the live PG board

## Verdict

Unlike its five siblings, this one had **real work left to land**. The
[task-det-c3cf418aed audit](task-det-c3cf418aed-needed-a-human-resolution.md)
surveyed all six `needed_a_human` findings on 2026-08-23 and recorded this
subject as the only one whose PR was still open:

| finding | subject | subject then | merged before its card was filed? |
|---|---|---|---|
| ... | ... | ... | ... |
| b1d12d0f70694dc2 | task-42a17b8956 | **pr_opened** | no — still open |

So the clear here is **by outcome, not by window** — the distinction the
[task-det-bbc0fa01ea audit](task-det-bbc0fa01ea-needed-a-human-resolution.md)
drew for the opposite case.

That matters, because the derivation *had already stopped reporting the
subject* before any repair happened. `_recovery_rows()` reads a **24h**
window, and the escalation is dated 2026-08-23 03:16 — it aged out on its own.
Re-running the acceptance derivation on 2026-08-24 returned `[]` against a
board where PR #1910 was still red and still open. **A green derivation was
available for free, and taking it would have been fabricating a recovery.**

## The actual cause

One cause, five red checks.

| When (UTC) | Event |
|---|---|
| 2026-08-23 02:32 | PR #1910 opened from `kanban/task-42a17b8956` (branch point `25692314e`) |
| 2026-08-23 02:33–02:34 | `Test Gates` + `Test Shard {1,2,3,4} of 4` all FAILURE; Lint, Security Scan, Test (Windows), Test (PostgreSQL), all four E2E shards SUCCESS |
| 2026-08-23 02:33 → 03:15 | `pr_watcher.resume` x5, `classification: ci_failed`, reason "injected resume context" |
| 2026-08-23 03:16 | `pr_watcher.escalate` — "resume cap reached (5/5) — manual intervention required" |
| 2026-08-23 05:08, 2026-08-24 22:50 | `pr_watcher.wait` — `gh pr view` fetch failures (host/forge, unrelated) |
| 2026-08-25 | rebase-equivalent merge + one-line fix pushed by hand (this record) |

The refusal, identical in all five jobs:

```
##[error]CI test allowlist (core): listed more than once: tests/test_ace_instance_page_render.py
```

The PR added `args/ci_test_files/core.d/task-42a17b8956.txt` naming
`tests/test_ace_instance_page_render.py`. That file was **already gated** by
`core.d/qa-fail-5f7cf03a0b0a4351.txt:6`, which landed in `25692314e` — this
branch's own merge base, so the collision existed from the branch's first
commit.

`gated_test_list.py --check` runs *before* pytest in every one of those jobs, so
each aborted at collection. The shards' second error —

```
skip census: no JUnit XML could be read (.tmp/ci-junit-shard-1.xml: ... No such file or directory)
  — the gated run's skip count is UNKNOWN, which is not the same as zero
```

— is that abort's consequence, and the census correctly refuses to read a
missing file as a clean zero.

**The fix is deleting the second fragment.** No test, route or assertion
changed. The test file is not new to this PR (its own body says the tests were
*appended to the already-gated* module), so nothing needed to gate it.

## Why five LLM resumes could not reach it

The card's standing claim is that an LLM resume cannot fix this class. Here the
defect *was* in the branch, which makes the failure mode more specific and worth
recording:

1. **The error names the wrong half.** It reports the duplicated *file*, not the
   *other fragment* holding it. Nothing in the branch's own diff is wrong to
   read; the duplicate is a property of the diff **plus the other 509 entries**.
2. **The PR's own verification claimed this check passed.** Its body reports
   "gated-list coverage all pass" — and `--check-coverage` genuinely does pass
   (0 unlisted). `--check`, the one that fails, is a *different* invocation.
   A resume reading that body has an explicit, false, all-clear.
3. **Nine of fourteen checks were green,** including both `Test (Windows)` and
   `Test (PostgreSQL)` and all four E2E shards, which do not run `--check`.

The branch was also **13 commits behind main**, over the
`max_behind_commits: 10` staleness threshold in `args/pr_watcher_config.yaml`,
so it would have been refused at the `behind_main` rung even once green.

## Repair, and how it was landed

`git push --force-with-lease` is refused in this environment, so the branch was
**merged with `origin/main` rather than rebased onto it** and the fix committed
on top — fast-forward, no history rewrite. Both apply the `merge=union` driver
and write the resolution into the branch, which is what CLAUDE.md's DIRTY-union
rule actually requires; the merge cost is one extra commit.

Verified on the pushed tree:

| Check | Result |
|---|---|
| `gated_test_list.py --check --list core` | 510 targets, all present, **no duplicates** |
| `gated_test_list.py --check-coverage` | 530 gated, 1690 grandfathered (ceiling 1693), 0 unlisted |
| `skip_census.py --check` | 81 registered / ceiling 81, 0 unregistered |
| `pytest tests/test_ace_instance_page_render.py` | 19 passed |
| `red_first_gate.py --gate` | **discriminating** — 4 failed at merge base, 19 pass here |
| behind `origin/main` | 0 |

## What was deliberately not done

- **The detector, its threshold and its 24h window are untouched.** An actuator
  never edits what it verifies. The window aging the finding out is not a
  detector defect — it is why this record exists, so the clear is legible as an
  outcome and not as elapsed time.
- **No recovery is claimed.** `summarize_recovery` keeps the outcome
  `needed_a_human`; a merge after an escalation is a human's merge. This repair
  was a human-directed one, and counting it as automation recovering would be
  precisely the inversion rem-hyg-16 was built to refuse.
