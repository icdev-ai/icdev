# mfx-mrg-06 — the superseded verdict gets a rung in the unlinked sweep

*2026-09-06. Repairs the population defect mfx-mrg-05 measured. The predicate is untouched.*

## 0. The one-line answer

`_sweep_unlinked_prs` now asks `decide_superseded` of every open PR the poll cannot see,
before `classify_merge_readiness` decides anything; a fire is closed through the linked
path's own `_handle_superseded` when the branch resolves to a task, HELD when it does not,
and merged in neither case. Surveyed over the whole open population at the instant of
arming: **population 3, fires 0, unchecked 0**.

## 1. What was wrong, restated from the survey

`docs/audits/mfx-mrg-05-superseded-population-survey.md` §5: fifteen duplicates an operator
closed by hand each had a `done` task 12–67 s before the duplicate opened. `list_pr_tasks`
selects the six pollable statuses, so the linked path never saw them; the sweep listed
them but computed `linked` from the same query and treated each as an ordinary unlinked PR.
Five of the same shape had already merged as empty commits (#1905, #1926, #1950, #2096,
#2101). The only thing that stopped the other fifteen was a fail-closed accident — a
protected-path refusal that fired because the changed-file listing could not be read.

## 2. What changed (`tools/ci/pr_watcher.py`, mirrored byte-identical to `icdev/`)

| piece | what it does |
|---|---|
| `poll_once` | keeps its once-per-cycle `merged_index` on the instance for the sweep that follows |
| `_sweep_merged_index` | the poll's index, never a second `gh pr list --state merged`; lists once only when no poll preceded |
| `_in_a_merged_family` | the pre-screen: `family_kind` against every merged sibling, needing no commit list |
| `_sweep_superseded` | fetch the candidate's state (one `gh pr view`), run the SHIPPED `_superseded_verdict`, act |
| `task_row_for_branch` | `kanban/<id>[-suffix]` → the task row, ANY status, longest-prefix at a `-` boundary |
| the loop | `if url not in linked: if self._sweep_superseded(...): continue` — before the ladder |

### Why the state is fetched per candidate rather than listed

`decide_superseded` needs the PR's commit list. The sweep's listing asks for 100 PRs, and the
merged listing was measured refused by GitHub's GraphQL node budget at `--limit 60` with
`commits` (mfx-mrg-02). Putting `commits` on the sweep's listing would have made the whole
sweep fail on every poll. So a PR is a *candidate* only if some merged sibling is related to
it by the same facts `family_kind` uses (same branch, our branch or number named in its
prose) — facts the listing already carries — and only a candidate costs a `gh pr view`. A PR
outside every merged family cannot fire and is never fetched (pinned by test).

### Why the task is resolved from the branch and not from `executor_url`

On every one of the fifteen measured duplicates the task's `executor_url` named the MERGED
sibling, not the duplicate. The branch name is the only link that survives, and the rule is
`pr_linker.branch_to_task_id`'s (longest id that is a prefix at a `-` boundary), asked of the
board as at most one query per segment, only after a fire.

## 3. The rules, and where each is pinned

| rule (from the card) | how it holds | test |
|---|---|---|
| close gated on the PREDICATE, never on the terminal task | same PR, no merged sibling → takes exactly the old path (merges) | `test_the_terminal_task_alone_is_never_a_close` |
| FAIL-OPEN | unreadable listing → no close, no fetch, old path | `test_an_unreadable_merged_listing_changes_nothing` |
| reuse `merged_index`, no second listing | one `--state merged` across poll + sweep | `test_the_polls_merged_index_is_reused_by_the_sweep` |
| do NOT arm `superseded_revert_leg` | config untouched, default `false` | (unchanged) |
| the linked path keeps its own rung | a PR in `linked` is skipped here | `test_a_task_the_poll_can_see_is_left_to_the_poll` |
| a terminal task is never re-completed | `_handle_superseded`'s existing guard | `test_a_closed_duplicate_never_completes_a_terminal_task` |

Two decisions the card did not spell out, stated here:

* **A firing PR with NO task row is HELD, not closed.** The sweep's charter is that it never
  closes a human's PR; a `fix/*` branch reopened on a merged head is exactly that. It is also
  never merged — merging a superseded PR is the defect. Audited once per PR as
  `superseded_hold` (kpr-watch-10's dedupe, read from `audit_trail` so it survives a restart).
* **Report-only (`superseded_close: false`) WITHHOLDS the merge here.** On the linked path
  report-only falls through so the resume/rebase machinery keeps running for a PR a human may
  still want. The sweep's only downstream act is the merge, so falling through would merge an
  empty commit under a setting that says "do not act". `superseded_warn` is audited once.

## 4. Survey before arming — the WHOLE open population

Read-only, `python -m tools.ci.pr_superseded --survey --json`, 2026-09-06T18:03:10Z:

```
population=3 (open)  merged considered=40  fires=0  unchecked=0  rate=0.00%
  #2145 kanban/mfx-own-04                  no merged sibling in this task family
  #2143 kanban/mfx-mrg-04                  no merged sibling in this task family
  #2137 kanban/qa-fail-6a87916931be3793    no merged sibling in this task family
```

This is the fire rate against the population the rung now reaches. The 92.31% in mfx-mrg-05
§6 is the measured size of the defect on a population *selected for it* and is not a refusal
rate against ordinary PRs; the card forbids quoting it as one, and it is not quoted as one
here. The predicate has 0 false positives over all 26 terminal-born PRs in the window, and
this change routes those PRs to it without changing what it decides.

## 5. Not done, and named

* **The producer** — a worker opening a second PR 12–67 s after its own squash-merge
  (`_pr_flow_outcome`'s post-dispatch confirmation racing `gh pr create`). Out of scope per
  the card; this repair holds whichever way that race falls.
* **#2082's shape** (branch commit and squash byte-identical in diffstat but with different
  patch-ids) is still a miss. The card forbids arming leg B for it, and leg B is measured
  blind to it anyway.
* `_open_pr_index()` is still called twice per PR inside the sweep loop; pre-existing, not
  this card's.

## 6. Re-derive it

```
python -m pytest tests/ci/test_pr_watcher_sweep_superseded.py -q
python -m tools.ci.pr_superseded --survey            # the open population, right now
grep -n "_sweep_superseded\|task_row_for_branch" tools/ci/pr_watcher.py
```
