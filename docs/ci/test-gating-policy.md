# CI Test Gating Policy

**CUI // SP-CTI**

**Status:** adopted 2026-08-12 (`tsg-policy-01`)
**Supersedes:** the unwritten convention that PRs #1526 and #1533 were already following
**Enforced by:** `tools/ci/gated_test_list.py --check-coverage`, run as the **Test gating
census** step of the required `test` job
**Config:** [`args/test_gating_gate.yaml`](../../args/test_gating_gate.yaml)

---

## The problem this decides

CI runs an explicit per-file allowlist. Measured on 2026-08-12:

| | count |
|---|---|
| Collectible test modules under `tests/` | **2,150** |
| In `args/ci_test_files/core.txt` (required `test` job) | 172 |
| Additionally in `args/ci_test_files/windows.txt` (non-required) | 20 |
| Documented exclusions | 132 |
| **Ungated — never gated a merge** | **1,826** |

A test file CI never runs has never gated anything. It can be wrong from its very first
commit and no pipeline will ever say so. That is not hypothetical:

- `tsg-dead-01` — `remediation_simulator._run_nqe_layer` was **dead since June**. A
  swallowed `ImportError` returned `"skipped"`, indistinguishable from a legitimately
  unreachable Forward API. The test that would have caught it was ungated.
- The 2026-08-11 sweep found **531 failure lines across 87 files** when the ungated set
  was run in isolation. Twelve PRs of repairs merged the same day.

The allowlist already has a gate against **shrinking** (`--check`: empty list, below
floor, missing path, duplicate). It had nothing against the gap **regrowing** — every new
test file landed ungated by default, silently, forever.

## The decision

**1. Widen the allowlist one file at a time, in the PR that makes the file pass.**

This is the policy the TSG fixes were already assuming, and it is now the written rule.
A file is added to `args/ci_test_files/core.txt` in the same commit that makes it green.
That is the *only* sanctioned way to widen the allowlist.

**2. Do not bulk-widen. Ever.**

Adding the 1,826 wholesale turns `main` red — an unknown number of them fail — and a
gate that reddens `main` gets disabled within a day. A disabled gate is strictly worse
than the debt it was meant to measure, because the debt at least stays visible. The same
argument rules out globbing `tests/**`: it would run everything and fail immediately.

**3. Grandfather the existing 1,826 by name, and let the census only shrink.**

[`args/ci_test_backlog.txt`](../../args/ci_test_backlog.txt) enumerates them. Enumerated
and not counted, deliberately: a bare number can be held constant while the set churns —
fix one file, add one ungated file, count unchanged, gate green, gap silently regrown.
That is the precise regression this policy exists to prevent, so **identity** is what
gets tracked.

**4. A test file that is in none of the three places fails the required `test` job.**

Yes — the answer to the question the task posed. The `test` job now fails when a
collectible test module is in no allowlist, matches no documented exclusion, and is not
in the census. The failure names the file and names `core.txt`. The gap cannot regrow
without someone deliberately editing a policy file, in a diff a reviewer can see.

**5. An exclusion must state a reason, in the config, next to the pattern.**

An exclusion is *not* a backlog entry. The backlog says "should be gated, is not yet".
An exclusion says "gating this would buy no signal". Two exist today:

| Pattern | Why |
|---|---|
| `tests/e2e_selenium/**` (28) | `conftest.py` has an autouse fixture that **skips the whole module** when the dashboard port does not answer. On a CI runner every one reports green by skipping — file existence counted as evidence, which is the failure mode this project exists to close. Run deliberately by `tools/testing/e2e_runner.py` against a started server. |
| `tests/genesis_auto/**` (104 of 106) | Generated: the file *set* is controlled by the generator, not by a human opening a PR, so a per-file human allowlist is the wrong instrument. The right gate is on the **generator** — owned by `tsg-gen-02`. |

## How to work with it

```bash
# Where does the tree stand?
python tools/ci/gated_test_list.py --check-coverage
python tools/ci/gated_test_list.py --check-coverage --json

# You fixed a test file. Two edits, one PR:
#   1. append the path to args/ci_test_files/core.txt (at the END — the file is
#      merge=union, so do not reflow neighbouring lines)
#   2. delete its line from args/ci_test_backlog.txt
#   3. lower `backlog_max` in args/test_gating_gate.yaml by the number you gated
# Forgot step 2? Not fatal — it is reported as a stale entry. Sweep them:
python tools/ci/gated_test_list.py --prune-backlog
```

**You added a new test file and CI is red.** Make it pass and append it to `core.txt`.
Do **not** add it to `args/ci_test_backlog.txt`: that census is closed and only shrinks,
and appending to it pushes the effective backlog over `backlog_max`, which reddens the
job for a second, louder reason.

**`backlog_max` may only go down.** It equals the count at adoption — no headroom,
because headroom is room for the gap to regrow unobserved. Raising it is the visible,
reviewable act of deciding to take on more surface that CI does not check, and it should
be argued for in the PR body. This is the same convention as `args/model_id_gate.yaml`
and `args/insert_schema_gate.yaml`.

## Why the backlog is not `merge=union`

`args/ci_test_files/*.txt` **is** union-merged (`kax-conflict-07`): it is append-only, so
the superset of two branches' appends is always the right resolution. Union's one cost is
that a line deleted on one branch is resurrected when that hunk collides with the other
branch's edit.

For a **shrink-only** file that cost is the entire behaviour. A file you just fixed and
removed from the census would come back. So `args/ci_test_backlog.txt` lives outside the
`args/ci_test_files/` directory and is a normal three-way merge — two PRs deleting
different lines from a 1,826-line sorted file merge cleanly on their own, and forgetting
a deletion is already non-fatal.

## What this does not claim

- **Gated ≠ good.** 192 files gating a merge is still 8.9% of the suite. This policy
  stops the number falling and makes every increment permanent; it does not make the
  number adequate. Paying down the 1,826 is the rest of the TSG epics.
- **Green ≠ meaningful.** The census counts files, not assertions. A gated file full of
  `assert True` passes it. That is a different gate's job.
- **The Windows job is still not required.** `args/ci_test_files/windows.txt` counts
  toward coverage because those files do run, but `Test (Windows)` is deliberately absent
  from branch protection while its stability is characterised. A file gated *only* there
  is weaker evidence than one in `core.txt`.
