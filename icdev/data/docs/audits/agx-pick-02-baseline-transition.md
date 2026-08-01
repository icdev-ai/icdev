<!-- CUI // SP-CTI -->
# agx-pick-02 — Deterministic-Picker Conversion & Baseline Transition

- **Task:** agx-pick-02 (converts the surfaces ranked in
  `docs/audits/agx-pick-01-deterministic-picker-audit.md`)
- **Rule applied:** the LLM commits only to a small enum per dimension; pure
  Python (`tools/quality/categorical_scoring.py`) composes every number.
- **Date:** 2026-07-25

## What changed

| Surface | Before | After |
|---|---|---|
| `tools/evolution/fitness.py::score_full` (#1) | LLM emits 3 free-form floats 0.0-1.0 | LLM emits 3 enums; `compose_fitness` maps each `{correct/partially_correct/incorrect}`→`{1.0/0.5/0.0}` (and the procedure/conciseness analogues) and keeps the **unchanged** `0.5/0.3/0.2` composite weights |
| `tools/ace/evaluator.py::grade_output_quality` (#2) | LLM emits 6 floats + per-dim confidence | LLM emits one `{supported/partial/unsupported}` per dimension; `compose_eval_overall` composes `overall`; an `unsupported` faithfulness verdict caps `overall` (constitutional fail band) |
| `tools/quality/content_grounding.py::_ground_content_llm` (#3, opt-in) | LLM emits a `{"score": float}` | LLM labels each claim `{grounded/partial/ungrounded}`; `compose_grounding` composes the support ratio; unknown token fails **closed** (ungrounded) |

The composition arithmetic lives in one place and is unit-tested against the
**full enum truth table** (`tests/test_categorical_scoring.py`, 21 cases) — the
aggregation is a spec, not an approximation.

## Provenance

Every composed score now carries `vocabulary_version` (`cat-1.0`). `FitnessScore`
gained a `vocabulary_version` field (empty for heuristic `score_fast`, which is
LLM-free and unchanged); ACE grades gain `vocabulary_version` +
`faithfulness_failed`; grounding results gain `vocabulary_version`. A future
change to any vocabulary or weight MUST bump `VOCABULARY_VERSION`, which is the
signal that comparisons across the boundary are invalid.

## Baseline transition — GATED, not a side effect

Changing a scoring function shifts the score distribution and invalidates stored
comparisons. The SIPA re-signature incident (`kanban-manual-gate-integrity`,
`sipa-rel-path-basename-fallback`) shows an unplanned transition can flood the
board on the next reflex run. Controls shipped here:

1. **Fitness (#1) is the highest-risk transition** because its composite feeds
   GEPA/SELA skill-candidate acceptance. During cutover, set
   **`ICDEV_GEPA_FROZEN=1`** — `gepa_optimizer.run()` honors this flag and
   no-ops the promotion cycle (returns `skipped: gepa_frozen_baseline_transition`)
   so a shifted distribution cannot mass-accept or mass-reject candidates. The
   freeze is an explicit human-flipped gate, never automatic. Unfreeze only after
   re-baselining from a held-out set.
2. **Evaluator (#2)** stores historical `llm_grade_json`; old grades stay
   readable, new ones are stamped `vocabulary_version` so the two eras are
   distinguishable. No destructive migration.
3. **Grounding (#3)** is opt-in (`method="llm"`, default heuristic is unchanged
   and LLM-free), so it carries no stored-baseline risk.

## Portability proof (the agx-core-02 coupling)

A free-form "rate 0.0-1.0" prompt yields incomparable numbers across model
families; a 3-token enum yields the same token from a frontier model and a local
7B. The composition is deterministic Python, so given the same enums the score is
byte-identical regardless of which model produced them. The **live** cross-model
enum-distribution comparison (frontier vs. local Ollama, held-out set) is graded
by the architecture benchmark suite (**agx-bench-01/02**), which runs ≥2 model
families including local Ollama and is intentionally sequenced last so it grades
every converted surface at once. The deterministic discrimination proof — that
the categorical path produces distinct, correctly-ordered composites across the
truth table — ships here in `tests/test_categorical_scoring.py`.

## Air-gap / LLM-agnostic conformance

No vendor SDK imports, no hardcoded model IDs, no LangChain in the new module or
the converted call sites; all inference routes through `LLMRouter`. The 3-value
vocabularies are small enough for a 7B local model to hit reliably, and every
`map_*_enum` provides a deterministic fallback for malformed structured output
(neutral midpoint for fitness/eval, fail-closed ungrounded for grounding).
