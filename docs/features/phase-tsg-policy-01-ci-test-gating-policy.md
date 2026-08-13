# Phase tsg-policy-01 — CI test gating policy, and the ratchet that enforces it

**CUI // SP-CTI**

**Task:** `tsg-policy-01` — POLICY: decide the CI allowlist policy the fixes have been assuming
**Project:** TSG — Test Suite Gap Remediation
**Date:** 2026-08-12

The policy of record lives at **[docs/ci/test-gating-policy.md](../ci/test-gating-policy.md)**.
This is the implementation record.

---

## What the task asked

Two questions, both answered here.

1. **Confirm or replace the proposed policy** — "add each file to `core.txt` as part of the
   PR that fixes it, add nothing unfixed."
2. **Decide whether the `test` job should FAIL** when a test file exists outside the
   allowlist and outside a documented exclusion, "so the gap cannot silently regrow."

## The answers

**1. Confirmed, unchanged, and now written down.** It was already the practice — #1526,
#1533 and the twelve stale-test PRs of 2026-08-11 all did exactly this — but it existed
only as prose in a kanban card. It is now a CLAUDE.md guardrail, a policy doc, and a gate.

**2. Yes, the `test` job now fails — but as a ratchet, not a sweep.**

The literal form of the question ("fail when a test file is outside the allowlist") would
fail on 1,828 files today. That is not a gate anyone can ship: it reddens `main`
immediately and gets disabled within a day, which is strictly worse than the debt,
because the debt at least stays visible. Same argument the task itself makes against
bulk-widening.

So the enforced rule is the *derivative*, not the level: the pre-existing 1,828 are
grandfathered **by name**, and what fails is a test file that is **new** — in no
allowlist, matching no documented exclusion, and not in the census.

## The measurement

| | count | |
|---|---:|---|
| Collectible test modules under `tests/` | 2,149 | matches pytest's own `testpaths` + default `python_files` |
| `args/ci_test_files/core.txt` | 167 | the required `test` job |
| `args/ci_test_files/windows.txt` (adds) | 20 | `Test (Windows)`, not a required check |
| Documented exclusions | 134 | `tests/e2e_selenium/**` (28) + `tests/genesis_auto/**` (106) |
| **Ungated backlog** | **1,828** | grandfathered by name |

The task card said "148 of 2,120". Both numbers moved during the TSG epics: the allowlist
grew as files were fixed, and the census here counts `*_test.py` as well as `test_*.py`
because pytest does.

## What shipped

| File | Change |
|---|---|
| `args/test_gating_gate.yaml` | **new** — scope, exclusions (pattern + reason), backlog pointer, `backlog_max` ceiling |
| `args/ci_test_backlog.txt` | **new** — the 1,828 grandfathered paths, enumerated, shrink-only |
| `tools/ci/gated_test_list.py` | `census()`, `prune_backlog()`, `--check-coverage`, `--prune-backlog` |
| `.github/workflows/icdev-ci.yml` | new **Test gating census** step in the required `test` job |
| `tests/ci/test_test_gating_census.py` | 20 tests; added to `core.txt` in this same PR |
| `docs/ci/test-gating-policy.md` | the policy of record |
| `CLAUDE.md` (+ packaged copy) | guardrail bullet |
| `docs/reference/commands.md`, `tools/manifest/git-worktree-parallel-ci-cd.md` | registration |

## Three design decisions worth the words

**The census is enumerated, not counted.** A ceiling alone can be held constant while the
set churns: fix one file, add one ungated file, count unchanged, gate green, gap silently
regrown — precisely the regression this task exists to stop. `test_a_bare_count_would_not_catch_the_churn`
pins it. The ceiling still exists as the *second* line of defence: it is what turns
"append the new file to the backlog" from a one-line edit nobody reads into a visible,
arguable change to a policy file.

**The backlog is deliberately NOT `merge=union`, unlike the allowlists next to it.**
Union is right for `args/ci_test_files/*.txt` because they are append-only, and its one
cost — a line deleted on one branch is resurrected when the hunk collides — is harmless
there. For a **shrink-only** file that cost is the whole behaviour: a file you just fixed
and removed would come back. So the census lives outside `args/ci_test_files/` and takes
a normal three-way merge. Two PRs deleting different lines from a sorted 1,828-line file
merge cleanly on their own, and forgetting the deletion is already non-fatal (reported as
stale, swept by `--prune-backlog`).

**`tests/e2e_selenium/**` is excluded rather than gated because gating it would be
dishonest.** Its `conftest.py` has a session-scoped autouse fixture that skips the whole
module when the dashboard port does not answer, so on a CI runner all 28 report green by
skipping. Adding them would have grown the allowlist by 28 and the *signal* by zero —
file existence counted as evidence, the exact failure mode the EXA card names as this
platform's signature bug.

## Verification

```
$ python tools/ci/gated_test_list.py --check-coverage
Test gating census: 2149 collectible test modules — 187 gated, 134 excluded,
1828 grandfathered (ceiling 1828), 0 unlisted.

# The gate can fail — a gate that cannot go red is decoration:
$ printf 'def test_x():\n    assert True\n' > tests/test_zz_probe.py && git add -N tests/test_zz_probe.py
$ python tools/ci/gated_test_list.py --check-coverage; echo "exit=$?"
::error::test gating census: 1 test file(s) are gated by nothing:
tests/test_zz_probe.py. ... Make each one pass and append it to
args/ci_test_files/core.txt in this PR ...
exit=1

$ python -m pytest tests/ci/test_test_gating_census.py tests/ci/test_gated_test_list.py -q
40 passed
```

## What this does not claim

187 files gating a merge is 8.7% of the suite. This stops the number falling and makes
every increment permanent; it does not make the number adequate — paying down the 1,828
is the rest of the TSG epics. And the census counts *files*, not assertions: a gated file
full of `assert True` passes it. That is a different gate's job.

## Follow-on

`tsg-gen-02` owns the generator-side gate for `tests/genesis_auto/**`. The exclusion entry
carries `review: tsg-gen-02` so the decision has a named owner rather than calcifying into
a permanent carve-out.
