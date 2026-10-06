---
ontology_id: icdev:mission:m13-benchmarks-evals:step:2
step_class: icdev:Lesson
skill_tag: evaluation
---

# Why Your Own Eval Beats a Leaderboard

A leaderboard answers "which model is good at *those* tasks?". You need to answer
"which model, prompt and settings are good at *my* task, at a cost and latency I can
afford, and did my last change make it worse?". Only an eval you own can answer that.

## The golden set

A **golden set** is a fixed collection of inputs from your real workload, each with an
expected output (or a checkable property of the output) that a human has signed off.

- **Draw it from production**, not from imagination: real tickets, real documents,
  real user questions — including the awkward ones.
- **Keep it small and curated before you make it big.** 50 well-chosen cases that
  cover your failure modes beat 5,000 scraped ones nobody has read.
- **Version it.** A golden set that changes silently makes this week's score
  incomparable with last week's.
- **Keep it out of your prompts and fine-tuning data.** The moment a golden case is
  copied into a few-shot example, you have contaminated your own benchmark — the same
  defect as a leaked public benchmark, created by you.

## How to score an output

Prefer the cheapest scorer that is actually correct, in this order:

1. **Deterministic checks** — exact match after normalisation, regex, a JSON schema,
   unit tests on generated code. Cheap, repeatable, explainable. ICDEV's
   `tools/llm/eval_runner.py` is a declarative harness of exactly this kind:
   `contains`, `regex`, `max_length`, `json_schema` assertions run against each model.
2. **Task-specific scorers** — for example CRAG-style scoring in
   `tools/rag/crag_evaluator.py`, which gives a correct answer 1, an honest abstention
   0 and a hallucination −1, so a model cannot buy accuracy by guessing.
3. **LLM-as-judge** — another model grades the output against a rubric. Necessary for
   open-ended text, but it is a measuring instrument with known biases:

| Judge bias | What happens | Mitigation |
|------------|--------------|------------|
| **Position bias** | In pairwise comparison the judge favours the answer shown first (or second) | Run both orders; count a win only if it holds in both |
| **Verbosity bias** | Longer answers are rated higher regardless of correctness | Rubric that rewards correctness explicitly; length-controlled comparison |
| **Self-preference** | A judge rates outputs from its own model family higher | Use a judge from a different family than the candidates |
| **Rubric drift** | Vague criteria let the judge invent its own standard | Concrete, itemised rubric with examples of each score |

Calibrate any judge against human labels on a sample of your golden set before you
trust it. A judge you never checked against humans is an unmeasured instrument.

## Regression evals in CI

Treat the eval like a test suite. Every change to a prompt, model, retrieval setting or
tool definition re-runs the golden set, and the build **fails** when a metric drops
below its threshold. That turns "the new prompt felt better" into "the new prompt kept
accuracy at or above the bar on 50 fixed cases". `tools/llm/eval_runner.py --gate`
exits non-zero for exactly this purpose, and `tools/evaluation/agent_benchmark.py`
does the same for agents with a weighted composite of **outcome** (right answer, 0.6)
and **methodology** (right approach, 0.4).

Two rules keep a regression eval honest:

- **Pin everything that moves:** model version, temperature, prompt version, golden set
  version. An unpinned eval measures drift in the inputs, not your change.
- **Run more than once when sampling is non-deterministic**, and look at the spread.
  A one-point drop inside run-to-run noise is not a regression.

## Cost and latency are eval axes, not afterthoughts

Accuracy alone picks the most expensive model every time. Record per case:

- **Cost** — input and output tokens times the provider's price on the day you ran it
  (prices change; store the date with the price).
- **Latency** — wall-clock time per answer; report a percentile such as p50/p95, not
  only the mean, because users feel the tail.
- **Cost per correct answer** — total cost divided by the number of correct outputs.
  A cheaper model with slightly lower accuracy can win on this measure, and a model
  that is wrong cheaply is not actually cheap.

## What does your own eval NOT tell you?

Even a good golden set has limits: it covers only the cases you thought to include,
it ages as your users' questions change, and a high score on it says nothing about
inputs from outside its distribution. Refresh it from production failures, and keep
monitoring in production — an eval is a gate before release, not a guarantee after it.
