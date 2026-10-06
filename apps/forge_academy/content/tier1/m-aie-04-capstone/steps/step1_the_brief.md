---
ontology_id: icdev:mission:m-aie-04-capstone:step:1
step_class: icdev:Lesson
skill_tag: ai_assisted_engineering
---

# Capstone Brief: From Vibe-Coded to Production Grade

*Facts in this lesson are current as of October 2026.*

The previous three missions taught you to drive a coding harness (m-aie-01), build the
verification harness that decides whether its output is done (m-aie-02), and recognise the
anti-patterns that report success while doing nothing (m-aie-03). The capstone puts all
three to work on one small module.

## The module

A teammate asked an AI assistant for a token-budget ledger, ran it once, saw no error,
and shipped it. This is the **specification** it was supposed to meet:

> `charge(ledger, user, tokens, limit=1000)` adds `tokens` to `user`'s running total in
> the `ledger` dict and **returns the new total**.
>
> 1. `tokens` below 1 raises `ValueError`.
> 2. A charge that would take the total **above** `limit` raises `BudgetExceeded`.
>    Spending **exactly up to** the limit is allowed.
> 3. A refused charge leaves the ledger **unchanged**.

The module contains **three planted defects** — one per numbered rule. Every one of them is
an anti-pattern from m-aie-03, and none of them crashes on the happy path, which is exactly
why "it ran once" was not evidence.

## What production grade means here

You will do three things, in this order, and each one is graded by a program, not by a
reviewer's impression:

| Step | You produce | The grader checks |
|---|---|---|
| 2 | Discriminating tests **and** the fixed `charge` | Your fix meets the spec; your tests pass on it; each planted defect, re-introduced one at a time, is **killed** by at least one of your tests |
| 3 | An instruction file for the next agent | It names the exact test command, states one checkable rule per defect class, and defines what "done" means |

### Tests before fixes

Write the tests from the spec **first** and watch them go red against the shipped code.
A test you only ever saw green might pass whatever the implementation does — m-aie-02 called
that "not a test". The step 2 grader enforces it with **mutants**: it puts each planted
defect back into an otherwise-correct `charge`, one at a time, and runs your tests against
each version. A mutant your tests do not fail on has **survived**, and a surviving mutant
means a test is missing — the same idea as `tools/ci/red_first_gate.py`, which re-runs a
changed test against the pre-change tree.

### Fix the defect, not the test

When a test goes red against the shipped code, the defect is in the code. Weakening the
assertion until it goes green is the "editing the test until it passes" anti-pattern.
Fix only what the spec names — **bound your edit scope**. A rewrite of the whole module is
not what the brief asked for, and it is harder to review.

### An instruction file is a contract, not a wish

Coding harnesses read a project instruction file — `CLAUDE.md`, `AGENTS.md`,
`.github/copilot-instructions.md` — at the start of every session. "Write good code" in that
file changes nothing, because no one can check whether it was followed. A useful rule is
**checkable**: it names a command, a construct or a value. ICDEV's own `CLAUDE.md` is
budgeted by `args/claude_md_budget.yaml` because the file is paid for on every session —
keep yours short.

### Done means evidence

"I've fixed it and all tests pass" is a sentence. **Evidence** is the test output from
running the exact command. ICDEV makes `done` merge-verified for the same reason: a claim
of completion without the artifact behind it is not completion.
