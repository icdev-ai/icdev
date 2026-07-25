<!-- CUI // SP-CTI -->
# Audit: Deterministic-Picker — LLM-as-Scorer Surfaces (agx-pick-01)

- **Task ID:** agx-pick-01
- **Type:** research / audit — **NO behavior change** (conversion is agx-pick-02)
- **Date:** 2026-07-24
- **Scope:** live `tools/*` (the `icdev/tools/*` tree is the companion-sync mirror)
- **Method:** AST/grep sweep of every surface where an LLM is prompted to emit a
  number/score/confidence/ranking that drives a decision, classified against the
  upstream deterministic-picker rule.

## The rule being applied

> The LLM may only commit to **categorical features** — booleans and enums.
> **Python composes the final signal** (the score, the ranking, the gate verdict).

This is FORGE's thesis one level deeper: FORGE makes *execution* deterministic;
deterministic-picker makes *aggregation* deterministic. It targets a documented,
recurring ICDEV failure mode — free-form LLM confidence numbers that look
meaningful and are not. Reference cases:
`trust-confidence-is-a-per-rule-constant` (a "0.9 → 0.33 drift" that was a
per-rule **constant** — the number carried no signal), and the `phantom`
trust-event class in `ace_trust_ledger`.

**Portability corollary (why this also serves agx-core-02):** a free-form
"rate 0.0–1.0" prompt yields incomparable numbers across model families; a
3-value enum yields the same token from a 70B and a 7B. Categorical outputs are
the portability layer, so this audit and the LLM-agnostic gate are one project.

## Classification legend

| Type | Meaning | Verdict |
|---|---|---|
| **NUMBER** | LLM is prompted to emit a free-form float/int that drives a decision | **needs-conversion** |
| **RANKING** | LLM emits an ordering; Python derives any numeric score from position | mostly-immune (position→score map is already in Python) |
| **CATEGORICAL** | LLM emits only enum/boolean; Python composes the number | **immune** |
| **PYTHON** | The number is computed entirely in Python; no LLM emission | **immune** |

---

## Findings A — free-form NUMBER surfaces (needs-conversion), ranked by blast radius

Blast radius = what breaks if the emitted number is meaningless.

| Rank | File:line | LLM is asked to emit | Consumed by → decision | Blast radius | Proposed categorical vocabulary (3–5) | pick-02? |
|---|---|---|---|---|---|---|
| **1** | `tools/evolution/fitness.py:129-160` (`score_full`) | 3 floats 0.0–1.0: `correctness`, `procedure_following`, `conciseness` (prompt: *"Score each dimension from 0.0 to 1.0"*) | `composite = 0.5·correctness + 0.3·procedure + 0.2·conciseness` → `score_examples()` mean → **GEPA/SELA skill-candidate acceptance** (`gepa_optimizer` gate `composite ≥ 0.60`, `delta ≥ 0.05`) and `autoresearch/fitness_proposal_quality` | **HIGH** — a meaningless number silently accepts/rejects self-improvement candidates and evolves the genome | `correctness {correct, partially_correct, incorrect}`; `procedure {followed, partial, violated}`; `conciseness {concise, acceptable, verbose}` → Python maps each enum→{1.0, 0.5, 0.0} and keeps the 0.5/0.3/0.2 weights | **YES (primary)** |
| **2** | `tools/ace/evaluator.py:438-474` (`_JUDGE_PROMPT`) → `grade_output_quality()` | 6 floats 0.0–1.0 (`faithfulness`, `completeness`, `reasoning_quality`, `cod_quality`, `error_adaptation`, `overall`) **+ a categorical `*_confidence` (HIGH/MEDIUM/LOW/UNKNOWN) per dim** | stored `llm_grade_json`; `overall` = session **quality grade** feeding co-learning | **HIGH** — grades the coworker; a phantom grade corrupts co-learning signal | already half-categorical: **drop the floats, keep+extend the per-dim enum** `{supported, partial, unsupported}`; Python composes `overall` from the six enums (any-`unsupported`-on-faithfulness fails) | **YES (primary)** |
| **3** | `tools/quality/content_grounding.py:288-306` (`_ground_content_llm`) | `{"score": <float 0-1>, "ungrounded_claims":[...]}` (prompt: *"You are a grounding judge… score…"*). **Opt-in only** — default `method="heuristic"` is LLM-free | grounding `score` vs `support_floor`/`CONF_ABSTAIN` band → flag ungrounded content | **MEDIUM but TRUST-adjacent** — when enabled, it gates grounding for LLM-drafted artifacts; feeds agx-verify-01 (CoVe) | per-claim `{grounded, partial, ungrounded}` → Python composes support ratio; conservative fallback = `ungrounded` on malformed output | **YES (TRUST-adjacent, low-effort)** |
| 4 | `tools/gameday/judge_agent.py:41-57, 265-295` | panel: 5 floats 0.0–1.0 (quality/innovation/ethics/adversarial/compliance); adversarial lenses: *"Score 0-100 for CORRECTNESS…"* → `{"score": N, "issues":[…]}` | `final_score` (normalized `/100`), `judge_pts` 0–100 persisted for gameday scoring | **MEDIUM** — exercise/red-team scoring, not a production gate | per-dim `{strong, adequate, weak}`; lenses `{pass, minor_issues, major_issues}` → Python→points | seed follow-up (agx-pick-02b) |
| 5 | `tools/ttx/ai_scorer.py:100-150` | integer 0–10 per rubric dim **+** overall `confidence` 0.0–1.0 | Python weights dims → `round((weighted/total)·10)` = `judge_pts` 0–100 for TTX response scoring | **MEDIUM** — tabletop-exercise scoring, not a production gate | per-dim `{excellent, adequate, poor}` → Python→points; drop the free-form confidence float | seed follow-up (agx-pick-02b) |

**Note on #2:** `ace/evaluator.py` already emits a categorical confidence label
alongside each float — it is the closest to correct and the cheapest full
conversion. `tools/chat_router/intent_classifier.py:273-313` is the reference
target shape: the LLM emits only `{"mode": <enum>, "reason": …}` and the
`confidence` is a Python constant, never LLM-emitted.

## Findings B — RANKING surface (mostly immune)

| File:line | LLM emits | Python derives | Verdict |
|---|---|---|---|
| `tools/rag/reranker_provider.py:297-384` (`LLMRerankerProvider.rerank`) | an **ordering** `{"ranked_indices":[3,0,7,…]}` (never a float) — verified first-hand | score `1.0 - rank/max(len,1)` (line 375); blended in `reranker.py:82-84` | **mostly immune** — the number is already Python-composed from position. The residual issue is *single-axis conflation* (relevance vs sufficiency), which is **agx-rag-02's** concern, NOT a free-form-number conversion. The BGE path (`:238-281`) is a cross-encoder *model* score, not a prompted LLM. |

## Findings C — surfaces recorded as IMMUNE (do NOT re-audit)

Every "scorer"-named module flagged in the task brief turns out to be
Python-composed or categorical already. Recorded explicitly so no future session
re-derives this (upstream found 9 of 35 architecturally immune; ICDEV's scoring
surfaces skew even more immune because FORGE already pushed composition into
Python):

| Surface | Why immune |
|---|---|
| `tools/ace/trust_calibrator.py` | `trust_score = clamp(old + delta)` from a DB event ledger; weekly recalibration from outcome rates. **No LLM emits the number.** |
| `tools/creative/gap_scorer.py` | 3-dim composite = deterministic Python from DB signal counts. |
| `tools/research/challenge_scorer.py:1046-1056` | 6-dim composite = Python from paper/patent/regulation/solution counts + `coverage_score`. |
| `tools/research/capability_mapper.py:323-340` | `coverage_score` = keyword set-overlap ratio (Python). |
| `tools/foundry/novelty_gate.py:409-490` | `novelty_score = 1 − max_cosine_similarity` (embeddings); gate `< min_novelty → reject`. |
| `tools/skills/gepa_optimizer.py:32-35,120-131` | gate is Python (`composite ≥ 0.60`, `delta ≥ 0.05`); the LLM writes skill **text**, not a score — it *consumes* surface #1. |
| `tools/requirements/ai_governance_scorer.py` | 6-component weighted score = Python DB existence checks. |
| `tools/autoresearch/fitness_evaluator.py`, `fitness_proposal_quality.py:110` | Python from subprocess metrics / DB `composite_score` + win/loss. |
| `tools/quality/citation_grounding.py:22` | explicitly *"pure regex/dict/dataclass — no LLM"*; `classify_confidence` maps a heuristic score to include/flag/abstain. |
| `tools/migration_canvas/grounding.py:90` | `confidence` Python-set. |
| `tools/oracle/base_lens.py:34-44`, `prediction.py` | `OraclePrediction.confidence` populated by each lens's Python `score()` (the `analyze→score→propose` contract), not an LLM prompt. |
| `tools/chat_router/intent_classifier.py`, `url_analyzer.py:184` | LLM emits enum only (`mode`; Low/Medium/High); confidence is a Python constant. Reference pattern. |

## Baseline-transition risk (hand-off note for agx-pick-02)

Converting surfaces #1–#5 **changes the score distribution**, which invalidates
stored comparisons. This is exactly the failure mode of the SIPA re-signature
incident (`kanban-manual-gate-integrity`, `sipa-rel-path-basename-fallback`): an
unplanned scoring change flooded the board on the next reflex run. Each
conversion in agx-pick-02 MUST:

1. **Gate the transition** behind an explicit version bump, not a side effect —
   record `vocabulary_version` on every new score (deliverable (c) of pick-02).
2. **Fitness (#1)** feeds GEPA/SELA acceptance and stored fitness baselines —
   the highest-risk transition. A shifted composite could mass-accept or
   mass-reject skill candidates. Freeze GEPA promotion during the cutover and
   re-baseline from a held-out set, the way `opx-sipa-02` handled its baseline
   transition.
3. **Evaluator (#2)** stores historical `llm_grade_json`; keep the old grades
   readable and mark new ones with the vocabulary version.
4. Prove the categorical+Python path is **at least as discriminating** as the
   free-form number on a held-out set, and that it yields the **same enum
   distribution from a frontier model and a local 7B** (the portability proof).

## Recommended agx-pick-02 scope

- **Primary (convert now):** #1 `fitness.py`, #2 `ace/evaluator.py`, #3
  `content_grounding.py` (TRUST-adjacent, cheapest). These carry the real blast
  radius and the TRUST coupling.
- **Seed follow-ups (agx-pick-02b, exercise-scoring, lower priority):** #4
  `gameday/judge_agent.py`, #5 `ttx/ai_scorer.py`.
- **No action:** Findings B (rag-02 owns the axis-split) and all of Findings C
  (immune).

## Reproduction

```bash
# From repo root — the NUMBER surfaces (prompted floats/ints):
rg -n "0\.0 to 1\.0|0-10|0-100|Score each|rate .* 0|confidence.*float|float 0-1" tools/
# Cross-check the immune Python-composed scorers:
rg -n "composite|weighted|_score\(|coverage_score|novelty_score" tools/ --glob '!tools/llm/**'
```
