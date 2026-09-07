# mfx-own-05 — the branch matcher admitted a PREFIX extension, surveyed before narrowing

**Measured:** 2026-09-06, live ref listing of `C:/AI/ICDev` (6,286 refs under
`refs/heads` + `refs/remotes/origin`) × the live PostgreSQL board.
**Re-derive:** `python -m tools.kanban.branch_match_survey --env-file C:/AI/ICDev/.env [--include-terminal] [--json]`

## The defect

`tools/genesis/reflexes/kanban.py::_branches_for_task` answers "which branch
refs carry this task id" for the done-gate (`_branch_has_unmerged_commits`),
`stranded_audit`, `artifact_evidence`, and both `orphan_requeue` proofs. Its
boundary was

    (^|[/_-])<id>([/_.-]|$)

The docstring anticipated one direction of extension — a decomposed child
appends to its parent's id (`kanban/dwo-mcp-03-d5-d1` for `dwo-mcp-03-d5`) —
and the leading alternative also admitted the other: a repark card PREPENDS
(`kph-repark-<id>`, then `kph-repark-kph-repark-<id>`). A repark is a separate
board row with its own branch and its own PR, not a child of the card it was
reparked from.

On 2026-09-06 `mfx-ci-04` sat in `validating` from 12:49 to ~18:00 while
`kanban_requeue_reflex` refused it every cycle with

    branch_not_ancestor:kanban/kph-repark-kph-repark-mfx-ci-04
    branch_not_ancestor:origin/kanban/kph-repark-kph-repark-mfx-ci-04

That branch belongs to `kph-repark-kph-repark-mfx-ci-04`, which built and
landed the worktree-add budget fix (PR #2146). `kph-repark-mfx-ci-04` was
refused on the same foreign branch: one rule stranded two cards.

## The narrowed rule

The id must START a path segment of the ref name:

    (^|/)<id>([/_.-]|$)

`kanban/<id>` and `kanban/<id>-<suffix>` bind; `kanban/<something>-<id>` does
not. Everything else about the matcher is unchanged: `origin/` is stripped
before matching, the canonical `kanban/<id>` sorts first, and a git error
still returns `[]` (fail-open).

## The survey

Both rules were replayed over every ref × every task id. "Today" is the
shipped `_branches_for_task`; the old rule exists only in the survey module
as `legacy_matches`, labelled as history. The narrowed set is a subset of the
old one by construction, and `added` was 0 in both runs.

### Non-terminal ids (the population the done-gate asks about)

| | legacy | current |
|---|---|---|
| task ids surveyed | 11 | 11 |
| (task, ref) pairs | 21 | 13 |
| tasks with ≥ 1 ref | 10 | 10 |
| dropped | | 8 — **all repark**, 0 other |

Every dropped pair, by name:

| task | ref dropped | task status |
|---|---|---|
| mfx-ci-04 | kanban/kph-repark-kph-repark-mfx-ci-04 | in_progress |
| mfx-ci-04 | kanban/kph-repark-mfx-ci-04 | in_progress |
| mfx-mrg-06 | kanban/kph-repark-mfx-mrg-06 | in_progress |
| mfx-mrg-06 | origin/kanban/kph-repark-mfx-mrg-06 | in_progress |
| mfx-own-04 | kanban/kph-repark-mfx-own-04 | pr_opened |
| task-det-4f4ca191bc | kanban/kph-repark-kph-repark-task-det-4f4ca191bc | token_exhausted |
| task-det-4f4ca191bc | kanban/kph-repark-task-det-4f4ca191bc | token_exhausted |
| task-wt-20f94d17 | kanban/kph-repark-task-wt-20f94d17 | validating |

No non-terminal task loses its last ref (10 → 10), so no task that had a
branch under the old rule reads as branchless under the new one.

### Every id, terminal included

| | legacy | current |
|---|---|---|
| task ids surveyed | 3,966 | 3,966 |
| (task, ref) pairs | 4,260 | 4,229 |
| tasks with ≥ 1 ref | 2,179 | 2,178 |
| dropped | | 31 — 13 repark, **18 other** |

The 13 repark drops add `fni-ana-01`, `kph-repark-mfx-ci-04`,
`kph-repark-task-det-4f4ca191bc` and `task-det-e9a2e3ea16` (all `done`) to the
eight above.

The 18 `other` drops are what the narrowing gives up, and they are named
rather than summed. Every one is a hand-named branch whose prefix is joined to
the id with `-` instead of `/`, on a task that is `done`:

| task (status, last updated) | ref dropped | last commit | unmerged vs origin/main |
|---|---|---|---|
| kax-conflict-07 (done, 2026-08-09) | feature-kax-conflict-07-empty-list-proof, origin/… | 2026-08-09 | 2 |
| prop-cap-11 (done, 2026-07-08) | icdev-prop-cap-11, origin/… | 2026-07-07 | 0 |
| prop-cap-12 (done, 2026-07-08) | icdev-prop-cap-12, origin/… | 2026-07-07 | 0 |
| prop-cap-13 (done, 2026-07-08) | icdev-prop-cap-13, origin/… | 2026-07-07 | 0 |
| prop-cap-14 (done, 2026-07-08) | icdev-prop-cap-14, origin/… | 2026-07-07 | 0 |
| prop-fix-10 (done, 2026-07-08) | icdev-prop-fix-10-11, origin/… | 2026-07-07 | 0 |
| prop-iqe-01 (done, 2026-07-08) | icdev-prop-iqe-01, origin/… | 2026-07-07 | 0 |
| prop-sec-02 (done, 2026-07-08) | icdev-prop-sec-02, origin/… | 2026-07-07 | 0 |
| prop-vv-02 (done, 2026-07-08) | icdev-prop-vv-02 | 2026-07-08 | 0 |
| sbx-fld-06 (done, 2026-08-09) | origin/feature-sbx-fld-06-gate | 2026-08-08 | 3 |

The one task that loses its last ref (2,179 → 2,178) is in this table. Two of
these branches hold unmerged commits (`feature-kax-conflict-07-empty-list-proof`
2, `origin/feature-sbx-fld-06-gate` 3); both tasks were marked `done` on
2026-08-09, weeks before this change, so neither reached `done` through the
narrowed gate.

## What the narrowing costs, stated

A `-`-joined prefix that is not a repark (`icdev-<id>`, `feature-<id>-…`) is
structurally indistinguishable from the repark shape, so the rule cannot keep
one and drop the other. The trade is: zero non-terminal tasks carry such a
branch today, the worker convention is `kanban/<id>`, and the shape that DID
strand two cards is removed. A blocklist of repark prefixes was rejected —
`orphan_requeue` could grow a second prefix tomorrow and the matcher would be
wrong again until somebody noticed. Re-run the survey before widening the rule
back; if a non-terminal task ever appears under `other`, that is the evidence
to widen on.

## Named, not built here

The refusal itself was `branch_not_ancestor`: `git cherry origin/main <branch>`
reported 0 unmerged `+` lines while `merge-base --is-ancestor` failed — the
squash-merge signature, which `orphan_requeue.probe_branch`'s own comment
calls "a human's". A merged PR is knowable from the forge (mfx-mrg-02's
superseded guard reads a merged PR's commit oids for exactly this reason), so
the refusal could be narrowed, but that puts a forge round-trip inside a proof
that is pure git today and fails open differently. Its own card if the matcher
fix alone does not clear the population.
