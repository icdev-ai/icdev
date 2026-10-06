---
ontology_id: icdev:mission:m-aie-02-verification-harnesses:step:1
step_class: icdev:Lesson
skill_tag: verification_harnesses
---

# Why AI Code Needs a Verification Harness

In m-aie-01 you met the **coding harness**: the software that lets a model read, edit and run your code. This mission is about the other harness — the **verification harness**: the tests, linters, scanners, hooks and CI checks that decide whether the code the agent wrote is actually done.

> **Tooling snapshot: as of October 2026.** Tool names and flags below were current when this lesson was authored; check each tool's documentation before you rely on a detail. Nothing here quotes a price or a benchmark score.

## The agent's "done" is a claim, not a fact

A coding agent ends its turn by saying the work is finished. That sentence is generated text — the same kind of output as the code. An agent can report "all tests pass" without having run them, run the wrong command, or run a suite that never exercised the change. Generation is cheap and fast; **the bottleneck moves to verification**. A verification harness turns "the model says it works" into "a deterministic check says it works", and it is the same check every time, whoever or whatever wrote the code.

## Tests are the spec

For a human, a test confirms an implementation. For an agent, a test is the most precise **specification** you can give it: an executable statement of required behaviour that it can run, read the failure of, and iterate against. "Orders of 10 or more get 10% off" is ambiguous at the boundary; `assert bulk_price(10, 2.0) == 18.0` is not.

That is why test-driven development fits agent work so well:

1. **RED** — write a test for the behaviour you want, run it, and watch it **fail**. The failure proves the test can detect the missing behaviour.
2. **GREEN** — write (or let the agent write) the smallest change that makes it pass.
3. **REFACTOR** — clean up with the test as a safety net; it must stay green.

Skipping step 1 is the most common mistake, and the most expensive one.

## A test that passes on the buggy code is not a test

A test only has value if it **discriminates**: it fails on the wrong behaviour and passes on the right one. A test that passes before the fix *and* after it asserts the current behaviour, not the required behaviour — it could never have gone red, so it proves nothing. Agents produce these easily: asked to "add a test", they read the existing code and assert whatever it currently does.

ICDEV makes this a merge gate. `tools/ci/red_first_gate.py` takes every test file a pull request adds or modifies, checks out the **merge base** (the code before the change), applies **only that test file** on top, and runs it. The test must **fail** there and **pass** on the branch. The pre-change output is uploaded as the `red-first-proof` artifact, so the RED is recorded rather than merely promised. The motivating case: a reviewed, correct-looking test passed only because its monkeypatch had landed on a different module object than the code under test used — it would have passed against any implementation at all.

You will build exactly this check, by hand, in the lab.

## Static checks: linters, type checkers, SAST

Tests check behaviour you thought to write down. Static checks catch whole classes of defect without running anything:

| Tool | Kind | What it catches |
|---|---|---|
| **ruff** | Linter (and formatter) | Unused imports, undefined names, risky patterns, style drift — fast enough to run on every save. |
| **mypy** | Type checker | Calling a function with the wrong type, returning `None` where a value is promised, attributes that do not exist. |
| **bandit** | SAST (static application security testing) | Security smells in Python: `shell=True`, hardcoded passwords, unsafe deserialisation, weak hashes. |

They are cheap, deterministic and agent-proof: an agent cannot argue with `ruff check .` exiting non-zero. ICDEV's CI runs ruff in its lint job and bandit in its security job.

## Where the checks run: hooks, CI, required checks

The same checks run at three distances from the keyboard:

- **Pre-commit hooks** — scripts git runs before a commit is created (ICDEV keeps its own in `.githooks/pre-commit`). Fast feedback in a second, but **bypassable** with `git commit --no-verify`, so a hook is the fast path, never the only gate.
- **CI** — the same checks re-run on a clean machine for every push, where nobody's local shortcut applies.
- **Required checks** — branch protection that refuses to merge a pull request until named CI checks are green. This is the gate that holds even when a hook was skipped.

## Sandboxed execution

Running tests means running code — including code an agent just wrote. Run it **sandboxed**: an isolated process or container, a clean environment with no secrets in it, no network unless the test needs it, and a time limit. This Academy grades your labs exactly that way: `apps/forge_academy/code_runner.py` runs submissions in a subprocess with an import allowlist, a scrubbed environment and a wall-clock timeout.
