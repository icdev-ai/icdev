---
ontology_id: icdev:mission:m13-benchmarks-evals:step:1
step_class: icdev:Lesson
skill_tag: evaluation
---

# Reading Benchmarks: What a Score Measures and What It Hides

A benchmark score is a measurement of one model, on one fixed set of tasks, under one
harness, at one point in time. Every model launch quotes a table of them. This lesson
teaches you to read that table as evidence with error bars and blind spots rather
than as a ranking of which model is "smartest".

> **Dating note.** Benchmark descriptions here are current as of October 2026. This
> lesson deliberately quotes **no** scores: a score is only meaningful with its date,
> its source and its harness, and leaderboards move monthly. When you need a number,
> fetch it from the benchmark's own published leaderboard and write down the date.

## The benchmarks you will see most

| Benchmark | What it measures | Format | Known limits |
|-----------|------------------|--------|--------------|
| **MMLU** (Hendrycks et al., 2020) | Broad knowledge across 57 academic and professional subjects | 4-option multiple choice | Saturated at the frontier; some questions have wrong or ambiguous answer keys; widely present in web training data |
| **MMLU-Pro** (2024) | Harder, more reasoning-heavy successor to MMLU | 10-option multiple choice | Less saturated than MMLU, but still multiple choice: it measures picking an answer, not producing one |
| **GPQA** (2023), usually the **Diamond** subset | Graduate-level biology, chemistry and physics questions written to be "Google-proof" | Multiple choice | Small (the Diamond subset is under 200 questions), so a few questions swing the score by whole points |
| **HumanEval** (OpenAI, 2021) | Writing a short Python function from a docstring | 164 problems, graded by unit tests, reported as **pass@k** | Saturated; tiny; problems are short self-contained functions, nothing like a real codebase |
| **MBPP** (Google, 2021) | Basic Python programming problems | Several hundred entry-level problems with tests | Same limits as HumanEval: short, isolated, heavily trained on |
| **SWE-bench Verified** (2024) | Resolving real GitHub issues in open-source Python repositories | 500 human-validated issues; the model's patch must pass the repo's hidden tests | Python only, a dozen repositories, and the issues are public — contamination is a standing concern; scores depend heavily on the agent scaffold |
| **LiveCodeBench** (2024) | Competitive-programming problems collected continuously from contest sites | Problems tagged with their release date | Designed against contamination: you can score a model only on problems published **after** its training cutoff |
| **Terminal-Bench** (2025) | Completing real tasks in a sandboxed command-line environment | Agentic, multi-step, graded by checks on the final machine state | Scores depend on the agent harness as much as the model; tasks change between versions, so versions are not comparable |
| **Chatbot Arena / LMArena** | Human preference between two anonymous model answers | Crowdsourced pairwise votes turned into an **Elo-style** (Bradley-Terry) rating | Measures what voters *prefer* — style, length and formatting sway it — not whether the answer is correct |

Two more families matter for the systems you build in this academy:

- **Agentic benchmarks** (for example tau-bench, GAIA, WebArena, OSWorld) score a model
  driving tools over many steps. The unit of success is a whole task, so a single
  early mistake fails everything after it.
- **RAG benchmarks** (for example CRAG, Meta 2024) score answers grounded in retrieved
  documents. CRAG penalises a hallucinated answer *more* than an honest "I don't
  know" — ICDEV implements that scoring scheme in `tools/rag/crag_evaluator.py`.

## pass@k: why "solved" depends on k

Code benchmarks generate **n** samples per problem and run each against unit tests.
If **c** of the n samples pass, the unbiased estimate of the probability that at least
one of **k** randomly drawn samples passes is (Chen et al., 2021):

```
pass@k = 1 - C(n - c, k) / C(n, k)
```

where `C(a, b)` is "a choose b". Two consequences:

- **pass@1** is simply `c / n` — the chance a single attempt is right. That is what you
  get in production when you call the model once.
- **pass@k rises with k.** A model that solves a problem 1 time in 5 has pass@1 = 0.2
  but pass@5 = 1.0. A vendor quoting pass@10 is quoting a far more generous number
  than pass@1 — and assumes you have a test suite to pick the winning sample.

Always check which k a score is reported at before comparing two models.

## Five ways a score misleads

1. **Contamination.** If benchmark questions (or their answers) were in the training
   data, the score measures memory, not skill. Public benchmarks leak into web crawls.
   Signals: a model scores much higher on old problems than on ones published after
   its cutoff (exactly what LiveCodeBench is built to expose).
2. **Saturation.** When every frontier model scores near the ceiling, the remaining
   gap is mostly noise and answer-key errors. A saturated benchmark can no longer
   separate models — MMLU and HumanEval are in this state.
3. **Vendor-reported vs independently reproduced.** A launch table is run by the
   vendor, with the vendor's prompt, sampling settings and sometimes a tuned variant of
   the model. Independent evaluators often get different numbers. Prefer a score you
   or a third party reproduced; treat a vendor number as a claim.
4. **Harness and scaffold effects.** For agentic benchmarks (SWE-bench, Terminal-Bench)
   the score belongs to *model + scaffold*: the tools offered, the retry policy, the
   context management, the time limit. The same model in two harnesses can differ by
   more than two different models in the same harness. A score with no harness named
   is not comparable to anything.
5. **Leaderboard gaming.** Preference leaderboards can be tuned for: longer, more
   formatted answers win votes. Labs have also submitted specially tuned variants to
   Arena that differ from the released model. A high Elo tells you voters liked the
   answers, not that the answers were right.

## What does this score NOT tell you?

Ask this of every number before you act on it:

- **Not your task.** A score on graduate physics says nothing about whether the model
  extracts fields correctly from *your* contract PDFs.
- **Not your cost or latency.** Benchmarks rarely report what the run cost or how long
  each answer took.
- **Not the variance.** One run at one temperature hides how often the model gets the
  same question right on a re-run.
- **Not the failure mode.** An accuracy number does not say whether failures are
  harmless refusals or confident fabrications.

That gap — between "good on a leaderboard" and "good for my workload" — is what the
next step closes: building your own eval.
