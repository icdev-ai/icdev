---
ontology_id: icdev:mission:m01-llm-fundamentals:step:6
step_class: icdev:Lab
---

# Lab: Price a Request and Fit a Prompt to Its Budget

Steps 2 and 4 gave you the arithmetic. In this lab you write it down as code, and the grader runs it against inputs you have not seen.

The lab runs **offline**. There is no live model and no real tokenizer. Instead, the starter gives you `toy_tokenize()`, a small deterministic tokenizer that behaves like a real one in the ways that matter here: words, punctuation and long identifiers all cost tokens, and long words split into several pieces. Use `count_tokens()` for every count — the grader checks your answers with the same rules.

## Part 1 — `request_cost(usage, prices)`

`usage` reports the four kinds of token from Step 2:

```python
{"input_tokens": 5000, "cached_input_tokens": 15000,
 "output_tokens": 500, "reasoning_tokens": 1500}
```

`input_tokens` counts only the **uncached** input. `prices` is one row of the starter's `PRICE_TABLE`, quoted in US dollars **per million tokens**:

```python
{"input": 2.00, "cached_input": 0.20, "output": 10.00}
```

Return the cost of the request in dollars. Remember that reasoning tokens are billed at the **output** rate. A missing usage key counts as zero.

## Part 2 — `fit_to_budget(system_prompt, history, question, context_window, reserved_output)`

`history` is a list of earlier conversation turns (strings), oldest first. The request must leave `reserved_output` tokens free for the reply, so the prompt may use at most `context_window - reserved_output` tokens in total.

Apply the **sliding window** strategy from Step 4:

1. The system prompt and the question are always sent.
2. Drop turns from the **oldest** end of `history` until everything fits.
3. Return the turns you kept, in their original order. Do not change the list you were given.
4. If the system prompt and question do not fit even with no history at all, raise `ValueError`. Sending an oversized request is worse than refusing it.

## How this maps to ICDEV

The production version is `tools/llm/context_budget.py`: `available_input_tokens()` does the same reserve-then-subtract arithmetic (plus a 10% safety margin), and it budgets against the **smallest** window in the routing chain. Your toy tokenizer stands in for its deliberately pessimistic `estimate_tokens()`.

Submit when both functions are implemented. The grader prices several requests, trims several conversations, and checks that you raise on a prompt that cannot fit.
