# AICUR — AI Fundamentals + AI-Assisted Engineering Curriculum

**Project card:** `aicur` in `args/projects.yaml` (tasks `aicur-fun-*`, `aicur-eng-*`,
`aicur-gd-*`, `aicur-fix-*`). Gap analysis dated 2026-10-05; this doc as of October 2026.

## What it is

A curriculum for FORGE Academy (`apps/forge_academy/`) and GameDay
(`apps/ai_gameday/`, `scenarios/`) covering nine topics in two tracks: **AI fundamentals**
(1a–1f) and **coding with AI** (2a–2c). The gap analysis found 3 topics covered, 3 partial
and 3 absent, and found that only one mission had an item bank. Every mission listed below
is **graded, not acknowledged**: each lesson step ships an item bank
(`content/item_banks/<slug>.yaml`, validated by `assessment.validate_item_bank`) or a coding
lab graded server-side by `grading.grade_step` in the offline `code_runner` sandbox.

## Coverage matrix

| Topic | Track | Mission(s) | How it is graded |
|---|---|---|---|
| 1a Tokens and LLM fundamentals | Fundamentals | `m01-llm-fundamentals` | Item bank |
| 1b vLLM and serving many models | Fundamentals | `m12-model-serving` | Item bank + routing lab |
| 1c Benchmarks and evals | Fundamentals | `m13-benchmarks-evals` | Item bank + eval lab |
| 1d Agents and RAG | Fundamentals | `m03-rag-basics`, `m04-first-agent` | Item banks + labs |
| 1e Prompt engineering | Fundamentals | `m02-prompt-engineering` | Item bank + labs |
| 1f Verification against hallucinations | Fundamentals | `m-trust-01-citation-grounding`, `m-dic-01-grounded-citations` | Labs |
| 2a Coding harnesses | Coding with AI | `m-aie-01-coding-harnesses`, `m-aie-04-capstone` | Item bank + lab |
| 2b Verification harnesses | Coding with AI | `m-aie-02-verification-harnesses`, `m-aie-04-capstone` | Red-first lab + item bank |
| 2c Vibe coding vs engineering | Coding with AI | `m-aie-03-vibe-vs-engineering`, `m-aie-04-capstone` | Item bank + spot-the-defect |

GameDay exercises the same skills as a team: the `vibe_to_verified` scenario pack (PR
review, red-first sprint, routing outage, benchmark claim), plus `grounding-red-team` and
`document-integrity` for 1f.

## The capstone — `m-aie-04-capstone`

The learner gets a vibe-coded token-budget ledger with **three planted defects**, one per
anti-pattern class from m-aie-03: an off-by-one boundary, a swallowed exception, and a
partial write on failure.

| Step | Type | Graded by |
|---|---|---|
| 1 Capstone Brief | Lesson | Item bank (7 items) |
| 2 Kill the Mutants, Then Fix the Module | Lab | `step2_test.py`: hidden spec checks on the learner's fix; the learner's tests must pass on the fix and on a hidden reference, and must **kill** three mutants (the reference with one planted defect put back). A surviving mutant is named by defect class. |
| 3 Write the Instruction File | Lab | `step3_test.py`: structural checks on an `AGENTS.md`-style file — exact test command, one imperative rule per defect class, a "Done means" that names the command, no `\|\| true`, no vague phrases, at most 40 non-blank lines |

## Learning path

All six new or extended missions (`m-aie-01`..`04`, `m12`, `m13`) are in `BUILTIN_MISSIONS`
(`apps/forge_academy/content_loader.py`) with `role_filter` and `prereqs`:

```
m01 ─┬─ m12-model-serving
     └─ m13-benchmarks-evals
m05 ── m-aie-01 ── m-aie-02 ── m-aie-03 ── m-aie-04-capstone
```

`GET /api/academy/learning-path` (`_recommend_next_missions` in `blueprint.py`) now ranks
missions whose prerequisites are all completed ahead of locked ones, orders by tier then
`order_idx`, and never recommends a mission with no steps. Previously it ranked by title
alone, so a learner who finished m-aie-03 was offered alphabetically-earlier missions
ahead of the capstone it unlocked.

## Verify

```bash
pytest tests/test_aca_aie04_capstone.py tests/test_aca_aie03_vibe_vs_engineering.py \
       tests/test_aca_aie_verification_harnesses.py tests/test_aca_aie_coding_harnesses.py -q
```

Enable the app with `ICDEV_FORGE_ACADEMY_ENABLED=true`; the capstone is at
`/academy/mission/m-aie-04-capstone`.
