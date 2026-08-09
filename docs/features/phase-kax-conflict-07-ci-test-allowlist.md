# CI test allowlist moved out of icdev-ci.yml (kax-conflict-07)

**Status:** shipped · **Date:** 2026-08-09

## Problem

`.github/workflows/icdev-ci.yml` was a single hand-written file that every task
edited. The `test` job's gated test list is an explicit per-file allowlist —
deliberately, because a glob that matches nothing silently shrinks the gate — so
every task that added a test appended to the same chain at the same offset.

Two costs, both measured 2026-08-09:

* **Merge conflicts.** Five open PRs collided on this file in one night. Each
  hand-resolve was the same edit: *keep both added lines*.
* **Pipeline deadlock.** `pr_watcher.hold_on_sibling_conflict` refuses to merge a
  PR that shares a non-additive file with another open PR. Once five PRs shared
  this one, each was a sibling of every other and none could merge. The
  generated-artifact form of this was fixed in #1434 by excluding derived files;
  this workflow is hand-written, so that fix did not cover it.

## Decision — option (a), move the list out

Adding the workflow to `_ADDITIVE_PATH_MARKERS` was the tempting non-fix: the
file holds real job definitions, and two PRs editing a job's `run:` block IS a
collision worth serializing. Only the list is additive.

Option (b), a directory convention with a glob and a floor assertion, was
rejected because the floor is the weaker half of the property being protected: a
glob plus a count tells you *how many* files matched, never *which*, so a rename
that moves a test out of the marked directory passes the floor as long as
something else was added. Option (c), teaching `pr_watcher` a line-range rule
over a region of a YAML file, was rejected in the task statement and nothing
found here changes that.

So:

| Before | After |
|---|---|
| 97 paths in a `\`-continuation chain in the `test` job | `args/ci_test_files/core.txt` |
| 13 paths in a `\`-continuation chain in `test-windows` | `args/ci_test_files/windows.txt` |
| — | `args/ci_test_files/*.txt merge=union` in `.gitattributes` |
| — | `tools/ci/gated_test_list.py` — resolves and validates |
| — | `args/ci_test_files/` in `_ADDITIVE_PATH_MARKERS` (the workflow is NOT) |

Each entry's rationale comment moved with it and now sits directly above the
entry it justifies, rather than in a block above the `run:` step.

## Acceptance criteria

### 1. Two branches that each add a test file both merge without a hand-resolve

Proven by `python tools/git/ci_test_list_merge_rehearsal.py`, which builds a
throwaway repo per scenario, cuts N branches off one base, adds a test file on
each, and merges them back. Both merge paths are exercised: the local `git merge`
a developer runs, and the bare `git merge-tree --write-tree` plumbing a forge runs
server-side for its mergeability probe — a layout clean only in the first still
shows a PR as conflicted.

```
$ python tools/git/ci_test_list_merge_rehearsal.py --branches 2
layout           mode         conflict   entries kept   notes
inline           worktree     CONFLICT   -              merge of feat/add_b required a human: .github/workflows/icdev-ci.yml
external         worktree     CONFLICT   -              merge of feat/add_b required a human: args/ci_test_files/core.txt
external-union   worktree     clean      yes
inline           merge-tree   CONFLICT   -              server-side merge of feat/add_b conflicted: .github/workflows/icdev-ci.yml
external         merge-tree   CONFLICT   -              server-side merge of feat/add_b conflicted: args/ci_test_files/core.txt
external-union   merge-tree   clean      yes

conflict-free: external-union
```

Identical result at `--branches 3` and `--branches 5`. Note the `external` row:
**moving the file is not sufficient on its own** — the appends still land at the
same end-of-file offset. The `merge=union` attribute is what does the work, and
the rehearsal is what showed that rather than assuming it.

`--gate` also fails when the `inline` control stops conflicting, so a rehearsal
that has drifted out of reproducing the problem cannot report success.

### 2. The gate cannot silently shrink

`tools/ci/gated_test_list.py --check` runs as its own CI step before pytest and
exits 1 when the list is missing, empty, below its recorded floor, names a path
absent from the checkout, or lists a path twice. The workflow additionally
refuses an empty resolved array in-shell, because `pytest "${EMPTY[@]}" -v`
collects the *whole* suite rather than erroring.

Demonstrated on a branch that emptied `core.txt`; see the PR for the red run and
the restoring commit. Pinned by `tests/ci/test_gated_test_list.py`, which covers
empty, truncated, missing-path and duplicate-entry lists in all four directions.

### 3. Every existing entry moved unchanged

`--extract-workflow` parses the inline chain out of an arbitrary (including
historical) workflow file, so the before/after list is a real diff rather than an
assertion:

```
$ git show <base>:.github/workflows/icdev-ci.yml > /tmp/old.yml
$ python tools/ci/gated_test_list.py --extract-workflow /tmp/old.yml --job test --min-targets 2 > before.txt
$ python tools/ci/gated_test_list.py --print --list core > after.txt
$ diff before.txt after.txt          # 97 entries, identical
```

Same for `--job test-windows` against `--list windows` (13 entries). The only
subsequent change is one deliberate addition: `tests/ci/test_gated_test_list.py`,
the test that guards this file.

## Operating notes

* **Adding a test to the gate is now a one-file append.** Put the path (and its
  rationale above it) at the end of `args/ci_test_files/core.txt`. Do not reflow
  or re-sort — union merge is safe because the lines are independent.
* **Do not hand-sync `icdev/data/args/ci_test_files/`.**
  `tools/installer/sync_package_tree.py` refreshes it at release; hand-syncing
  would restore the two-files-per-append cost this change removed.
* **Union merge resurrects deletions.** If a hunk removing an entry conflicts
  with another branch's append, git keeps the superset and the removal is
  undone. Verify a removal actually stuck. Byte-identical duplicates left behind
  by two branches adding the same entry are caught by `--check`.
* **The workflow is still serialized.** Two PRs editing a job's `run:` block are
  still held by `hold_on_sibling_conflict`, which is the intended behaviour.

## Related

* kax-conflict-03 — `tools/manifest/` shards, same `merge=union` mechanism, a
  different file class. Deliberately not bundled.
* #1434 — derived-artifact exclusion in `hold_on_sibling_conflict`.
