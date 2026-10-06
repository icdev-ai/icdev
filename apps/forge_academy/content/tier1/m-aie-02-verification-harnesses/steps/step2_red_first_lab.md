---
ontology_id: icdev:mission:m-aie-02-verification-harnesses:step:2
step_class: icdev:Lab
skill_tag: verification_harnesses
---

# Lab: Write a Test That Goes RED

An agent implemented this pricing rule and reported it done, with a test it wrote itself:

> **Spec.** `bulk_price(quantity, unit_price)` returns the order total, rounded to 2 decimal places. Orders of **10 or more** units get **10% off**. A `quantity` below 1 raises `ValueError`.

The starter contains the agent's `bulk_price` and the agent's test. Run it: the test passes. Ship it? Read the implementation against the spec first.

## Your task

Replace the agent's test with your own pytest-style tests — plain functions named `test_...` that use `assert`. Your tests must **discriminate**:

- they must **FAIL** against the agent's buggy `bulk_price`, and
- they must **PASS** against a correct `bulk_price`.

This is the check `tools/ci/red_first_gate.py` runs on every ICDEV pull request: a test that passes on the buggy code is not a test.

## How the grader works

The grader collects every function in your script whose name starts with `test_`, exactly as pytest would. It then swaps in each implementation in turn — the buggy one and a hidden correct one — and calls each of your tests:

| Run | Required result |
|---|---|
| Against the **buggy** `bulk_price` | At least one of your tests **fails** |
| Against the **correct** `bulk_price` | **Every** test passes |

So a test that is always red (for example `assert False`) fails the grader just as surely as one that is always green. Your tests must call `bulk_price` by name — the grader replaces that name, and a test that keeps its own private copy of the function is testing the wrong thing.

## Hints

- Read the spec, not the code. Write down the cases the spec names: below 10, **exactly 10**, above 10, and below 1.
- Boundaries are where generated code is most often wrong.
- The sandbox does not include pytest, so `pytest.raises` is unavailable. Test the exception with `try:` / `except ValueError:` and fail if nothing was raised.
- Compare money after rounding, e.g. `assert bulk_price(10, 2.0) == 18.0`.

The lab runs entirely offline: no model is called and nothing is written to disk.
