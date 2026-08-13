# Phase `tsg-policy-02` — the test-gating census at commit time

**CUI // SP-CTI**

**Status:** shipped 2026-08-13
**Depends on:** [`tsg-policy-01`](phase-tsg-policy-01-ci-test-gating-policy.md)
**Policy:** [docs/ci/test-gating-policy.md](../ci/test-gating-policy.md)
**Touches:** `.githooks/pre-commit`, `tools/testing/pre_commit_check.py`,
`tools/ci/gated_test_list.py` (+ the `icdev/tools/` mirrors)

---

## The observation

`tsg-policy-01` landed `gated_test_list.py --check-coverage` as a step of the required
`test` job. It works. Within **two hours** of it existing it caught two test files that
were gated by nothing:

| File | Arrived via | Author |
|---|---|---|
| `tests/test_bootstrap_hook_payload.py` | `exa-bench-10` (#1582) | autonomous worker |
| `tests/test_kanban_gate_sentinel_seeding.py` | `kax-exec-04` (#1598) | interactive session |

A third followed the next day (`tests/test_ndc_graph_json_type_guard.py`, #1599), and it
is registered in this PR.

Both original cases were caught in the **wrong place**. Each turned `main` RED, which
blocks *every* open PR, and each cost a follow-up branch + PR + full CI cycle (#1601) to
add one line that the author could have added in one second. One was written by an
autonomous worker and one by an interactive session, so this is not one actor's discipline
problem: item 8 of the CLAUDE.md registration checklist was enforced **nowhere the author
could feel it**.

## The decision

Run the same census at `git commit`, gated on the staged file list.

The cost was already paid for. `--check-coverage` is a filesystem walk over `tests/` plus
two config reads — no database, no network, no LLM, and it does not `import tools` (that
package's shim costs 136ms; loading `gated_test_list.py` by path costs ~5ms). Measured
over three runs on this repo: **0.17s**.

## What shipped

**`tools/ci/gated_test_list.py`**

- `in_scope(rel, config)` — extracted from `collect_test_files`, so the hook decides "is
  this a test file?" through the same `scope` block the census uses. A fast path that
  scoped differently would nag about files the gate ignores, or wave through files it
  fails on.
- `staged_added_or_renamed(root)` — `git diff --cached --name-only --diff-filter=AR`. A
  rename reports its **destination**, which is the name that has to be registered; a
  modification to an already-registered file reports nothing.
- `staged_new_test_files(root, files, config)` — the intersection. Returns **before
  reading the config at all** when the commit adds nothing.

**`tools/testing/pre_commit_check.py`**

- One `git diff --cached --name-status` call now serves both the pre-existing dashboard
  detection and the new gate — no extra subprocess for any commit.
- The census module is loaded **by path**, not as `tools.ci.gated_test_list`, and only
  when the commit adds or renames something.
- On failure it prints the staged files it is objecting to, then the census's own message
  **verbatim** — that message already names every offending file and names
  `args/ci_test_files/core.txt`. Running the same CLI CI runs makes the two messages
  identical by construction rather than by convention.
- It refuses only for files **this commit introduces**. `--check-coverage --json` puts the
  classified list on stdout and the human message on stderr, so the hook can attribute:
  offenders already in the tree print as a `NOTE` and are left to CI. This was not
  theoretical — `main` was red on two other people's files while this was being written,
  and a hook that refuses commits the author cannot fix gets `--no-verify`d permanently.

**`.githooks/pre-commit`** — comment only; the driver it already called does the work.

## Three things deliberately NOT done

1. **The CI step stays.** A hook is skippable with `--no-verify` and is absent for
   anything that does not land through a local commit. CI is the backstop; this is the
   fast path.
2. **The hook never appends to `core.txt`.** The census message deliberately says "make
   each one pass and append it". A hook that appended the line itself would gate a test
   nobody has run — the exact failure `tsg-policy-01` exists to close, reintroduced by its
   own convenience feature. `tests/ci/test_precommit_test_gating.py` asserts `core.txt`
   and the backlog are byte-identical after a refusal.
3. **It does not fail closed on its own machinery.** A missing policy config, missing
   `pyyaml`, or unavailable git prints `SKIPPED` and lets the commit through. CI runs the
   same census and will not be so kind.

## Measured

| | Result |
|---|---|
| Commit touching no test file | **allowed**, 154ms median vs 155ms before the change (5 runs each, real driver, real staged index) — no measurable cost |
| Commit adding an unregistered test file | **refused** in 0.30–0.39s; message named `tests/test_unregistered.py` and `args/ci_test_files/core.txt` |
| `core.txt` after that refusal | **byte-identical** |
| Commit adding the test *and* its `core.txt` line | **allowed** |
| Commit adding a registered test while the tree is red on someone else's | **allowed**, other file reported |

The first four were verified through a real `git commit` in a throwaway repository with
the real hook installed; all five are pinned as 16 tests in
`tests/ci/test_precommit_test_gating.py` — each
against a real git index, because the load-bearing assumption is that `git ls-files`
inside a pre-commit hook already sees a file that is staged but not committed. A test that
mocked git would pass whether or not that were true.

## Found on the way

`subprocess.run(..., text=True)` decodes with `locale.getencoding()` — `cp1252` on a
Windows dev box — while git and the census both emit UTF-8. The census message contains em
dashes and curly quotes, and `0x9d` is undefined in cp1252, so the hook died in a
`UnicodeDecodeError` traceback instead of printing the message. The three `git`/census
subprocess calls on this path now pass `encoding="utf-8", errors="replace"` explicitly.
This only ever mattered because the census moved from a UTF-8 CI runner onto every
developer's Windows machine.
