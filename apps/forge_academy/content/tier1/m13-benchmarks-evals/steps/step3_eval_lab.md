---
ontology_id: icdev:mission:m13-benchmarks-evals:step:3
step_class: icdev:Lab
skill_tag: evaluation
---

# Lab: Implement pass@k and a Golden-Set Eval

You will implement the two calculations from the previous lessons and run them over a
**fixed fixture of model outputs** that is already in the starter file. No model is
called and nothing touches the network: an eval over recorded outputs is reproducible,
which is the whole point.

## Part 1 — pass@k

`SAMPLE_RESULTS` records, for four coding problems, whether each of 5 generated
samples passed its unit tests. Implement:

- `pass_at_k(n, c, k)` — the unbiased estimator `1 - C(n - c, k) / C(n, k)`.
  - Raise `ValueError` if `k < 1` or `k > n`.
  - If `n - c < k`, every draw of k samples contains a passing one: return `1.0`.
  - `math.comb` gives you `C(a, b)`.
- `mean_pass_at_k(results, k)` — pass@k for each problem (`n` = samples, `c` = passing
  samples), averaged over problems.

Before you run it, predict: the fixture's pass@1 is 0.4. What will pass@5 be? The gap
between the two is the gap between "one call in production" and "best of five with a
test suite to pick the winner".

## Part 2 — a golden-set accuracy eval

`GOLDEN_ROWS` holds six recorded answers, each with the expected answer, the latency
and the cost of the call. (Costs are fixture values for the exercise, not real
provider prices.) Implement:

- `normalize(text)` — strip, lower-case and collapse runs of whitespace to one space.
- `score_golden_set(rows)` returning a dict with:
  - `n` — number of rows
  - `correct` — rows whose normalised `output` equals the normalised `expected`
  - `accuracy` — `correct / n`
  - `mean_latency_ms` — mean of `latency_ms`
  - `total_cost_usd` — sum of `cost_usd`
  - `cost_per_correct_usd` — `total_cost_usd / correct` (use `float("inf")` when
    nothing is correct)

Look at row 5 after you score it: the model said `"The answer is SC-7"` and the gold is
`"SC-7"`. Exact match marks a correct answer wrong. That is a scorer limitation, not a
model failure — and it is why real harnesses such as `tools/llm/eval_runner.py` offer
`contains` and `regex` assertions as well as exact equality.

## How this maps to ICDEV

| Your function | Platform equivalent |
|---------------|---------------------|
| `pass_at_k` | The metric HumanEval-style code benchmarks report |
| `score_golden_set` | `tools/llm/eval_runner.py` — per-prompt assertions, a report, and `--gate` for CI |
| cost / latency fields | The cost and latency axes from step 2 |
| (not in this lab) | `tools/evaluation/agent_benchmark.py` — outcome + methodology scoring for agents |
| (not in this lab) | `tools/rag/crag_evaluator.py` — hallucination-penalising RAG scoring |

Submit when the grader's checks pass. The grader asserts the exact numbers your
functions produce on this fixture.
