# CI Test Gating Policy

**CUI // SP-CTI**

**Status:** adopted 2026-08-12 (`tsg-policy-01`); extended to commit time 2026-08-13 (`tsg-policy-02`)
**Supersedes:** the unwritten convention that PRs #1526 and #1533 were already following
**Enforced by:** `tools/ci/gated_test_list.py --check-coverage`, run as the **Test gating
census** step of the required `test` job — and, for a local commit, by
`.githooks/pre-commit` via `tools/testing/pre_commit_check.py`
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

## Where the census runs (`tsg-policy-02`)

CI works, and it caught two unregistered test files within two hours of existing —
`tests/test_bootstrap_hook_payload.py` (#1582) and `tests/test_kanban_gate_sentinel_seeding.py`
(#1598), one from an autonomous worker and one from an interactive session. But it caught
them in the **wrong place**. A census failure in CI turns `main` red, which blocks every
open PR, and each one cost a follow-up branch + PR + full CI cycle (#1601) to add a single
line the author could have added in one second. Item 8 of the CLAUDE.md registration
checklist was enforced nowhere the author could feel it.

So the same census also runs at `git commit`:

| | Pre-commit (`.githooks/pre-commit`) | CI (`test` job) |
|---|---|---|
| When | the commit **adds or renames** a file in the census `scope` | every push |
| Cost | 0.17s when it fires; **0 when it does not** — measured 155ms before / 154ms after for a commit touching no test file | already in the job |
| Skippable | yes (`--no-verify`) | no |
| Covers | local commits only | everything, including anything that never was a local commit |
| Role | **fast path** | **backstop** |

Four properties are deliberate:

0. **It refuses only what THIS commit introduces.** The census describes the whole tree,
   and the tree can already be non-compliant through no fault of the author — `main` was
   red on two other people's files while this was being written. A pre-existing offender
   is printed as a `NOTE` and left to CI; blocking on it would refuse a commit the author
   cannot fix without stepping on the PR that already owns the line, and a hook that
   refuses commits you cannot fix gets `--no-verify`d permanently.


1. **CI keeps its step.** A hook is bypassable and is simply absent for changes that do
   not arrive through a local commit. Removing the CI step would trade a real gate for a
   convenience.
2. **The hook never edits `core.txt`.** Auto-appending would gate a test nobody has run —
   the exact failure `tsg-policy-01` exists to close, reintroduced as a feature. The
   message tells the author what to append and where; the append is theirs.
3. **It fails open on its own machinery.** A missing `args/test_gating_gate.yaml`, missing
   `pyyaml`, or an unavailable git prints `SKIPPED` and lets the commit through. A fast
   path whose whole justification is that CI still runs the same check must never be the
   reason a good commit is refused.

Scope is read from `args/test_gating_gate.yaml` — the hook shares `in_scope()` with the
census rather than re-deriving `tests/` + `test_*.py`, so the two cannot drift into
nagging about files the gate ignores.

## A gated test that SKIPS is unmeasured, not passing (`trust-disc-03`)

Everything above answers one question: **does CI run this file?** It cannot answer
whether the file asserted anything, and `pytest.skip` makes those two different
questions.

The case this was built from was live in the tree the day it was written — reproduced
2026-08-15 by running the gated file:

```
SKIPPED [1] tests/test_app.py:46: SQLite test DB lacks platform schema for
overview: no such table: agents
```

That test is on `core.txt`. It has been gated the whole time, ran on every PR, and
reported green. What it covers is `/api/charts/overview` against the platform schema.
Its `except OperationalError` catches whatever the route raises **first**, so the message
moves as `MINIMAL_ICDEV_SCHEMA` in `tests/conftest.py` gains pieces: it read `no such
column: classification` when the missing piece was the column the RLS predicate in
`get_connection()` filters on — which turned **every** read of `kanban_tasks` into a
raise — and today the first missing piece is the `agents` table. Same site, same
outcome: an unrun route presented as coverage for an unknown length of time. Nothing was
red. Nothing was measured.

A coverage census counts files. A skipped file is counted and asserts nothing, so the
census and the truth diverge silently — which is the same shape as a count-based backlog
whose set churns, and it gets the same instrument.

### The two halves

`tools/ci/skip_census.py` measures skips twice, because neither measurement subsumes the
other.

| | Static site census (`--check`) | Runtime report (`--from-report`) |
|---|---|---|
| Source | AST scan of the gated files | the gated run's JUnit XML |
| Sees | `pytest.skip`, `pytest.importorskip`, `@pytest.mark.skip[if]`, `unittest.skip*`, `self.skipTest` | whatever actually skipped, however it was spelled |
| Blind to | a skip raised from a conftest fixture, a plugin, or a rebound alias | a latent site that did not fire in this environment |
| Cost | ~1s, no test run | free — the suite already ran |
| Runs | before pytest, and at `git commit` | after pytest |

The static half is the ratchet you can run before you commit. The runtime half is the one
that cannot be fooled by indirection: a gated file that skipped at runtime while declaring
no skip site in its own source is reported **unaccounted**, and that is a failure too.

Attribution in the runtime half is per **file**, not per site, on purpose. One site can
skip many parametrized cases and one case can pass several sites, so a 1:1 map between XML
entries and AST nodes would be fiction. "This file skipped and owns no registered skip" is
a claim the data supports.

### What the first full survey measured

Arming a check without measuring its fire rate first is how this repo got eight
PreToolUse checks refusing routine work — a check enabled behind an unmeasured assumption
is *unmeasured*, not proven. So the runtime half was surveyed against the whole allowlist
before `--check` went into the workflow.

Full gated run, 2026-08-15 — 240 targets, 6,864 tests collected, 9m36s:

| | |
|---|---|
| Skipped | **45 (0.66%)** |
| Attributed to a gated file | 45 |
| **Unaccounted** (skipped, owns no registered site) | **0** |
| Static sites in the census | 81 across 31 files |

Zero unaccounted, so the gate refuses nothing that exists today — it is a ratchet against
the next one, not a bulk cleanup.

The survey also settled the per-file-vs-per-site question with data.
`tests/test_ski_roles_lifecycle.py` produced **37 of the 45 skips — 82% of the runtime
total — from exactly 2 static sites**: one parametrized guard over a skill pack that is
not vendored in this checkout. Site counts and skip counts are not the same quantity and
never will be, which is why both halves report their own and neither pretends to be the
other.

### The census discipline is the backlog's, for the backlog's reason

`args/ci_skip_census.txt` **enumerates** sites by name, one per line:

```
<file>::<qualname>::<kind>[<ordinal>]  # <written reason>
```

A bare count can be held constant while the set churns — delete one skip, add another,
count unchanged, gate green, and the thing the gate exists to notice has happened
unobserved. Identity is what gets tracked, exactly as in `args/ci_test_backlog.txt`.

The key deliberately excludes the line number: line numbers churn on every edit above the
site, which would make the census a merge-conflict generator and every unrelated PR a
census edit. The ordinal is always present, even for a lone site, so adding a *second*
skip to a function does not renumber the first and orphan its reason.

**`skip_census.skip_max` may only go down.** It equals the count at adoption — 81 sites
across 31 gated files — with no headroom, because headroom is room for unmeasured surface
to grow into. Two independent things fail: registering a new skip breaches the ceiling,
and *not* registering it breaches the by-name check. There is no third door.

**A reason must be a reason.** Shorter than 12 characters is refused, and so is a
placeholder from `PLACEHOLDER_REASONS` — `flaky`, `TBD`, `WIP`, `needs investigation`.
Those record that a skip happened, not why it is acceptable.

### Unlike the backlog, this census is `merge=union`

`args/ci_test_backlog.txt` is a normal three-way merge because it is shrink-only and union
would resurrect a deletion you had just earned. The skip census is appended to *and*
deleted from by unrelated branches at the same end-of-file offset, and the resolution for
two branches registering different skips is always "keep both lines" — so it is union,
like `args/ci_test_files/*.txt`.

Union's cost is real and is covered: a duplicated site fails `--check` by name (tested), a
malformed line fails, and a resurrected deletion surfaces as a stale entry that
`--prune` clears. The ceiling fails independently if the census grew at all.

### What it refuses to do

- **It never registers a skip for you.** Same reasoning as the allowlist: a hook that
  wrote the census line itself would grant coverage nobody reviewed.
- **A deleted skip never fails the PR that deleted it.** A census entry whose site is gone
  is a warning and a `--prune` away, not a red build. The tool's preferred outcome must
  not carry a penalty.
- **It does not census ungated files.** A skip in a file CI never runs is already
  governed by the backlog above; counting it here would double-count the same debt.
- **An empty run is not a clean run.** `--from-report` fails when the XML holds zero
  testcases or cannot be read, because 0 skips out of 0 collected is *unknown*, not zero.

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
  `assert True` passes it. The one form of "gated but asserting nothing" that *is* now
  governed is the skip — see the `trust-disc-03` section above; the rest is still a
  different gate's job.
- **The Windows job is still not required.** `args/ci_test_files/windows.txt` counts
  toward coverage because those files do run, but `Test (Windows)` is deliberately absent
  from branch protection while its stability is characterised. A file gated *only* there
  is weaker evidence than one in `core.txt`.
