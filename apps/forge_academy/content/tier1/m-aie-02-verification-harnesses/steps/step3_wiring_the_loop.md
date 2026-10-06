---
ontology_id: icdev:mission:m-aie-02-verification-harnesses:step:3
step_class: icdev:Lesson
skill_tag: verification_harnesses
---

# Wiring the Loop: Agents, Tests and Gates That Hold

You have a test that discriminates. Now connect it to the agent so the loop closes on its own — and make sure nothing in the wiring quietly turns the gate off. Examples use Claude Code's names where a concrete name helps; other harnesses have equivalents (as of October 2026).

## 1. Let the agent run the tests

An agent that can run the suite can iterate: edit, run, read the failure, edit again, until green. Give it the **exact** command in the instruction file (`pytest tests/test_pricing.py -q`, not "run the tests") and allow that command in its permissions so it does not stall on approval. The test output is the feedback signal the whole loop runs on — without it the agent is guessing.

Ask for the loop explicitly: *write the failing test first, run it and show me the failure, then implement until it passes.* Seeing the RED output in the transcript is your evidence that step 1 actually happened.

## 2. Never let the agent edit the test to make it pass

When a test fails, there are two ways to make it green: fix the code, or weaken the test. An agent optimising for "green" will sometimes take the second — loosen an assertion, mark the test skipped, delete the failing case, or change the expected value to match the wrong output. The suite then reports success and proves nothing.

Defences, cheapest first:

- **Say it** in the instruction file: tests define the spec; never modify a test to make it pass without asking.
- **Separate the roles**: you (or a separate step) write and commit the test; the agent may only change implementation files.
- **Review the test diff** before the implementation diff. A changed assertion in a "fix" commit is a red flag.
- **Count skips as unmeasured, not passing.** A skipped test asserted nothing. ICDEV's `tools/ci/skip_census.py` fails CI when a gated test file gains a skip that is not registered with a written reason.
- **Re-derive the RED in CI.** `tools/ci/red_first_gate.py` runs each changed test against the pre-change code; a weakened test that now passes there is caught.

## 3. Gate neutralisers: `|| true` and swallowed exceptions

A gate signals failure through an **exit code**. Anything that discards the exit code makes the gate print its warning and block nothing:

```bash
pytest -q || true          # always exits 0 — CI shows green whatever happened
pytest -q | tee out.log    # without `set -o pipefail`, the status is tee's, not pytest's
```

ICDEV learned this on its own security hook: it was wired as `python pre_tool_use.py || true`, so every check printed `BLOCKED` while the shell returned 0 and nothing was ever blocked. The hook already failed open on its own errors — the `|| true` suppressed only the **working** case.

The same neutraliser exists inside code, as a **swallowed exception**:

```python
try:
    conn.execute("INSERT INTO budget_usage ...")
except Exception:
    pass                    # the write failed; the caller is told nothing
```

The feature reports success while persisting nothing. ICDEV's `module_budget_usage` table held 0 rows for exactly this reason: the INSERT named a column the live table did not have, raised, and was swallowed. In tests the same shape appears as `except Exception: pass` around an assertion, or a `try` that catches `AssertionError` itself — the test can no longer fail.

Rules of thumb:

- A gate that cannot run must report **red** (or a distinct "could not run" status), never green. `red_first_gate.py` exits **2** when it cannot run, and that stays red.
- To disable a check deliberately, use an explicit, auditable switch (an environment variable, a config entry with a written reason) — never a shell operator hidden in a command string.
- Catch the **narrowest** exception you can handle, and log or re-raise the rest.

## 4. Test that the gate can refuse

A gate you have only ever seen pass is unmeasured, not proven. Before trusting one, feed it something that must fail — a deliberately broken commit, a test that should go red — and confirm it actually blocks. That is the same discipline as red-first, applied to the harness itself.
