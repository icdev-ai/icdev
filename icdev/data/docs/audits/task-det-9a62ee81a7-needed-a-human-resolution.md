<!-- CUI // SP-CTI -->
# task-det-9a62ee81a7 — `needed_a_human` finding for mfx-mrg-01, resolved

- **Task:** task-det-9a62ee81a7 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding row `subject=mfx-mrg-01`,
  `fingerprint=needed_a_human`)
- **Subject:** mfx-mrg-01 — PR #2064, 63 `rebase_failed`, 5 `resume`, escalated
- **Date measured:** 2026-09-05, against the live PG board

## Verdict — NOT moot. Real work was outstanding.

This is the second recovery card where the escalation was correct and something
still had to be landed by hand (the first was mfx-sib-03 / task-det-bc286d2ef5).
Do **not** open one of these expecting "nobody did anything": at the moment this
card dispatched (17:30:48Z) PR #2064 was still `draft`, still `mergeable=false` /
`mergeable_state=dirty`, and the watcher was still logging `rebase_failed` — the
most recent at **18:04:46Z, while this card was being worked**.

| Criterion | At dispatch | After this card |
|---|---|---|
| derivation reports `mfx-mrg-01` | yes — `attempts 3, escalated true, outcome needed_a_human` | clears when the last `resume` row (2026-09-04 18:42:07Z) ages out of the rolling 24h window, i.e. **2026-09-05 18:42:07Z** |
| PR #2064 mergeable | `false` / `dirty` | `true` |
| the deliverable on `origin/main` | absent (`--squash` still in `_auto_merge`) | landed through the governed door |

## The ledger

`audit_trail`, `action LIKE 'pr_watcher.%'`, payload naming `mfx-mrg-01`:

| action | rows | first | last |
|---|---:|---|---|
| `pr_watcher.wait` | 96 | 2026-09-04 13:50:55 | 2026-09-05 18:27:38 |
| `pr_watcher.rebase_failed` | 63 | 2026-09-04 18:00:45 | 2026-09-05 18:04:46 |
| `pr_watcher.resume` | 5 | 2026-09-04 18:00:45 | 2026-09-04 18:42:07 |
| `pr_watcher.escalate` | 1 | 2026-09-04 18:42:51 | *"resume cap reached (5/5) — manual intervention required"* |

## Cause 1 — the watcher could NEVER have merged this PR, and never said so

#2064 changes `tools/ci/pr_watcher.py`, the **first entry in `protected_paths`**
(`args/pr_watcher_config.yaml:388`). Both merge paths refuse such a PR by design
(kpr-watch-05). It was unmergeable-by-the-watcher from the moment it opened, and
it still spent **63 rebases, 5 resumes and an escalation**.

**Measured: `0` of the 165 `pr_watcher.*` rows for this task mention
`protected`.** The refusal never fired, so nothing in the ledger, the panel or
the escalation states the real reason.

This is a sharper form of the mechanism recorded for mfx-sib-03 (#2070), and the
earlier note — "`protected_paths` is checked AFTER the rebase rung" — understates
it. The check is not merely later in one ladder; it is **on a different arm of
the ladder**:

- `_refuse_protected` is called at `tools/ci/pr_watcher.py:3318`, guarded by
  `approved_ok` — inside the **MERGEABLE** arm, immediately before `_mark_ready`
  and `_auto_merge`.
- `_maybe_rebase` is called at `tools/ci/pr_watcher.py:3421`, inside the
  **`KanbanState.MERGE_CONFLICT`** arm.

A conflicting PR therefore *never reaches* the rung that would refuse it. #2064
was conflicting from the moment it opened, so the protected refusal was
structurally unreachable for its entire life. The rung fires only once a PR
becomes mergeable — precisely when it is no longer needed to prevent wasted work.

## Cause 2 — the per-base-era rebase budget is not a ceiling on a busy repo

`max_rebase_attempts_per_task` is budgeted per base era, so any landing on main
refunds it. Grouping the 63 `rebase_failed` rows by their own recorded
`base_sha`: **30 distinct base eras, exactly 2 failures in each** (29 eras x2,
one x1).

Main landed 30 times in ~24h, so the budget was refunded 30 times and the loop is
unbounded in practice. A conflict train and one permanently unresolvable conflict
are indistinguishable by row count alone — tell them apart with
`git log origin/main --first-parent -- <conflicted file>`, not timing correlation.

## Cause 3 — the conflict was a REDUNDANT DRIVE-BY, not a sibling train

The six prior recovery findings of this shape were shared-file conflict trains
(sibling cards appending to the same lines). **This one is not**, and the
distinction matters because the repair differs.

The conflict was in `tools/document_intelligence/exporter.py` and its `icdev/`
mirror, and nowhere else. The mfx-mrg-01 commit carried a change outside its own
scope, declared in its own message:

> *"Also migrates document_intelligence/exporter.py off a raw logging.getLogger
> (the log_standard coherence check was failing on it since rmf-wp-02)."*

It renamed `_log` to `logger` and deleted the `_log` binding. Meanwhile
**mfx-own-01 landed the same migration on main independently**, keeping `_log`
*and* adding `tests/document_intelligence/test_export.py::test_the_exporter_logs_through_the_icdev_logger_only`,
which asserts `exporter._log.name == exporter.logger.name`.

So the drive-by became simultaneously **redundant** (main had already satisfied
the check it was written for) and **conflicting** (main now pins the very symbol
it deleted). Taking the branch's side would have failed a test that is green on
main.

**Resolution:** `git merge origin/main`, then take **main's `exporter.py`
wholesale** in both mirrors — the branch contributes nothing there main has not
already done, better. The card's net diff no longer touches `exporter.py`:

```
 args/ci_test_files/core.d/mfx-mrg-01.txt  |   1 +
 icdev/tools/ci/pr_watcher.py              |  37 ++++-
 icdev/tools/kanban/land.py                |   4 +-
 tests/kanban/test_merge_commit_landing.py | 144 ++++++++++++++++++
 tests/test_auto_merge_fallback.py         |  26 ++-
 tools/ci/pr_watcher.py                    |  37 ++++-
 tools/kanban/land.py                      |   4 +-
```

Merge commit `4b57f7b1a`. Verified before pushing: no conflict markers,
`git merge-tree --write-tree origin/main HEAD` exits 0, both `pr_watcher.py`
mirrors byte-identical, both `exporter.py` mirrors byte-identical, `ruff` clean,
and 27 tests pass — including main's `_log` test, which is what proves the
resolution took the right side.

> **The generalisable rule: a drive-by fix is what turned a clean card into a
> five-resume escalation.** Karpathy #4 (bound your edit scope) is not a style
> preference here — an out-of-scope hunk is the hunk most likely to be landed
> differently by somebody else while your PR waits, and it conflicts in a file
> your reviewer has no reason to be looking at.

## Cause 4 (separate, unrepaired) — the GraphQL rate limit

96 of the 165 rows are `wait` carrying
`fetch failed: gh pr view failed: exit=1 stderr=GraphQL: API rate limit already
exceeded for user ID 263484343`. Measured concurrently: `gh api rate_limit`
reports **core 5000/5000 and graphql 5000/5000 remaining** while every GraphQL
call is refused — so the documented remedy ("sleep until reset") does not apply
and the limit is not the one `rate_limit` describes. **REST is unaffected**,
which is how every forge read in this record was taken
(`gh api repos/icdev-ai/icdev/pulls/2064`). This is the known `gh pr create`
GraphQL/REST split, now observed on `gh pr view` too. It is an independent
liveness problem and is *not* repaired by this card.

## What was done

1. Re-derived the finding — reproduced (`attempts 3, escalated true`).
2. Established nobody owned `mfx-mrg-01` (no lease, no claim file; the original
   worker's worktree clean at the 09-04 commit).
3. Merged `origin/main` into `kanban/mfx-mrg-01`, resolved both `exporter.py`
   mirrors to main's version, ran the tests, pushed the fast-forward
   (`101789c25..4b57f7b1a`). `mergeable` went `false/dirty` -> `true`.
4. Un-drafted #2064 so the required checks could run.
5. Landed it through the governed door — `cli.py --set-status mfx-mrg-01 done
   --merge` — the correct door for a protected-path PR: `land.py` runs the
   thirteen done-gate checks and marks `done` only once the forge confirms the
   merge.

## Follow-up seeded, not fixed here

Causes 1 and 2 are defects in `tools/ci/pr_watcher.py` — itself a protected path,
so a fix needs its own reviewed PR and its own fire-rate survey. Seeded as a card
carrying this record as evidence rather than fixed inline. Cause 3 needs no code:
it is a discipline finding, recorded above.

Per the standing rule, **the detector was not touched** — its verdict is correct.
`escalate` outranking a later merge is exactly right here: the merge was mine,
and this record is the human the escalation asked for.
