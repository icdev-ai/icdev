---
ontology_id: icdev:mission:m-aie-04-capstone:step:2
step_class: icdev:Lab
skill_tag: ai_assisted_engineering
---

# Lab: Kill the Mutants, Then Fix the Module

The starter is the vibe-coded ledger exactly as the AI wrote it. Read it against the spec:

> `charge(ledger, user, tokens, limit=1000)` adds `tokens` to `user`'s running total in
> the `ledger` dict and **returns the new total**.
>
> 1. `tokens` below 1 raises `ValueError`.
> 2. A charge that would take the total **above** `limit` raises `BudgetExceeded`.
>    Spending **exactly up to** the limit is allowed.
> 3. A refused charge leaves the ledger **unchanged**.

## Your task

1. **Write tests first.** Replace the agent's single test with your own `test_...`
   functions written from the spec. Run them against the shipped `charge` and watch them
   fail.
2. **Fix `charge`.** Make the smallest change that meets the spec. Keep the name, the
   signature and the `BudgetExceeded` class.
3. Run again. Every test should now pass.

## How the grader works

The grader runs four checks, in this order:

| Check | Required result |
|---|---|
| Hidden spec checks against **your** `charge` | All pass — every planted defect is fixed |
| Your tests against **your** `charge` | All pass |
| Your tests against a hidden **reference** `charge` | All pass — your tests encode the spec, not your implementation's quirks |
| Your tests against **three mutants** — the reference with one planted defect put back | Each mutant is **killed**: at least one of your tests fails on it |

A test that is always red fails the third check; a suite that never exercises the
boundary lets a mutant survive and fails the fourth. The grader names the defect class of
any mutant that survives, so you know which case your suite never tried.

## Hints

- One test per numbered rule is the minimum. Rule 2 has a boundary: try a charge that lands
  **exactly** on the limit, and one that goes one token over.
- For rule 3, catch the `BudgetExceeded` and then assert the ledger still holds what it held
  before the refused charge.
- The sandbox does not include pytest, so `pytest.raises` is unavailable. Use `try:` /
  `except BudgetExceeded:` and raise `AssertionError` if nothing was raised.
- Give every test its own fresh `ledger = {}` so one test cannot leak state into another.
- Your tests must call `charge` by name — the grader swaps that name to run the reference
  and the mutants.

The lab runs entirely offline: no model is called and nothing is written to disk.
