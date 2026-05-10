# CUI // SP-CTI
# Bayesian Autoresearch — Autonomous Experiment Engine

> Phase 67 — Karpathy's Autoresearch pattern fused with ICDEV™ Bayesian Teaching Intelligence for self-improving experiments.

---

## Overview

ICDEV™ continuously improves itself through autonomous, time-boxed experiments guided by Bayesian information-gain scoring. The system picks the most informative experiment to run next, executes it within a strict time budget, measures the metric delta, and keeps or discards the change via git commit/reset. Inspired by Karpathy's Autoresearch (630-line autonomous ML experiment loop, March 2026) and adapted for ICDEV™'s compliance, code quality, security, RAG, Pulse, and skill domains.

**Key insight (Karpathy):** "The bottleneck isn't model capability — it's evaluation." Binary success criteria + time-boxed execution + one-change isolation = autonomous improvement overnight.

**Key insight (ICDEV™ adaptation):** Autoresearch's LLM functions as an implicit, uncalibrated acquisition function. ICDEV™'s Bayesian Teaching provides the explicit, calibrated acquisition function — information gain scoring, teaching dimension, and Thompson Sampling replace LLM intuition for experiment selection.

## Architecture Decisions

- **D-AR-1:** Deterministic orchestration; only hypothesis generation uses LLM (scanner-tier qwen3.5, zero Claude tokens). Template fallback for air-gap.
- **D-AR-2:** One hypothesis per experiment for attribution clarity (Karpathy one-change rule).
- **D-AR-3:** Git worktree per experiment series; commit on keep, reset on discard. Reuses `tools/ci/modules/worktree.py` (D32).
- **D-AR-4:** `experiment_results` and `bayesian_experiment_scores` are append-only (D6, NIST AU-2). `experiment_candidates` allows UPDATE for status transitions.
- **D-AR-5:** pgvector embedding dedup (cosine > 0.85 rejection) for candidates. Python cosine fallback for SQLite. Reuses `pg_vector_store.py` patterns.
- **D-AR-6:** Thompson Sampling from `trust_engine.py` for explore/exploit across experiment categories. New `run_experiment` category with cautious Beta(2, 8) prior.
- **D-AR-7:** Fitness evaluators wrap existing ICDEV™ tools via subprocess (D191 SDK pattern). Read-only, advisory-only (D110).
- **D-AR-8:** Experiment programs are declarative YAML per domain (D26 pattern). Add new domains without code changes.
- **D-AR-9:** Genesis integration as 14th reflex `experiment` at ORANGE tier (code mutation with test gate).
- **D-AR-10:** Circuit breaker: 3 consecutive failures disables the experiment loop (consistent with Genesis D-GEN-8).

## Prerequisites

- Phase 35 (Innovation Engine) — signal pipeline for hypothesis sourcing
- Phase 52 (Code Intelligence) — code quality metrics for fitness evaluation
- Phase 64 (RAG) — pgvector for embedding dedup
- Phase 65 (Bayesian Teaching) — `score_candidate()`, `teaching_dimension()`, `optimal_compliance_order()`
- Phase 68 (Autonomy Engine) — `trust_engine.should_act()`, `observe()` for Thompson Sampling
- Genesis v2.0 — daemon infrastructure, reflex management, circuit breakers

## The Karpathy Loop (adapted)

```
HYPOTHESIZE (scanner-tier LLM / template fallback)
    ↓
SCORE (Bayesian info-gain: posterior_shift × discriminability × diversity × complexity_match)
    ↓
DEDUP (pgvector cosine > 0.85 rejection + content hash + tsvector overlap)
    ↓
SELECT (Thompson Sampling explore/exploit from Beta posteriors)
    ↓
EXECUTE (time-boxed: 5-min default, configurable per domain)
    ↓
EVALUATE (fitness evaluator wraps existing ICDEV™ tools → single [0,1] metric)
    ↓
DECIDE (metric improved ≥ threshold → keep; else → discard)
    ↓
UPDATE (landscape posteriors, trust engine, audit trail)
    ↓
REPEAT (no human intervention until circuit breaker trips)
```

## Component 1: Experiment Engine

**File:** `tools/autoresearch/experiment_engine.py`

The core Karpathy Loop adapted for ICDEV™.

### CLI
```bash
python tools/autoresearch/experiment_engine.py --create --domain compliance --hypothesis "..." --json
python tools/autoresearch/experiment_engine.py --run --experiment-id "exp-xxx" --json
python tools/autoresearch/experiment_engine.py --evaluate --experiment-id "exp-xxx" --json
python tools/autoresearch/experiment_engine.py --decide --experiment-id "exp-xxx" --json
python tools/autoresearch/experiment_engine.py --loop --domain compliance --max-experiments 5 --json
python tools/autoresearch/experiment_engine.py --loop --domain compliance --overnight --json
python tools/autoresearch/experiment_engine.py --status --json
python tools/autoresearch/experiment_engine.py --health --json
```

## Component 2: Bayesian Experiment Selector

**File:** `tools/autoresearch/bayesian_selector.py`

Wraps existing Bayesian Teaching for experiment selection.

### Scoring Dimensions (extends D-BT-2)
| Dimension | Weight | Purpose |
|-----------|--------|---------|
| posterior_shift | 0.30 | How much does this experiment update our beliefs? |
| discriminability | 0.25 | How well does it distinguish promising regions? |
| diversity | 0.25 | How different from recent experiments? |
| complexity_match | 0.20 | Is the difficulty appropriate? |

### CLI
```bash
python tools/autoresearch/bayesian_selector.py --score --domain compliance --json
python tools/autoresearch/bayesian_selector.py --select --domain compliance --json
python tools/autoresearch/bayesian_selector.py --estimate --domain compliance --json
python tools/autoresearch/bayesian_selector.py --category-order --domain compliance --json
python tools/autoresearch/bayesian_selector.py --health --json
```

## Component 3: Fitness Evaluator

**File:** `tools/autoresearch/fitness_evaluator.py`

Wraps 6 ICDEV™ tools into single-metric [0,1] scorers.

| Domain | Metric | Tool Wrapped |
|--------|--------|-------------|
| compliance | gate_pass_rate | multi_regime_assessor.py |
| code_quality | maintainability_score | code_analyzer.py |
| security | inverse_vulnerability_density | sast_runner.py |
| rag_quality | retrieval_relevance_at_5 | rag/evaluator.py |
| pulse_quality | writeguard_score | quality_checker.py |
| skill_quality | assertion_pass_rate | per-skill assertions |

### CLI
```bash
python tools/autoresearch/fitness_evaluator.py --evaluate compliance --project-id sparkpilot --json
python tools/autoresearch/fitness_evaluator.py --evaluate code_quality --project-dir tools/ --json
python tools/autoresearch/fitness_evaluator.py --evaluate-all --json
python tools/autoresearch/fitness_evaluator.py --list-domains --json
python tools/autoresearch/fitness_evaluator.py --health --json
```

## Component 4: Hypothesis Generator

**File:** `tools/autoresearch/hypothesis_generator.py`

Scanner-tier LLM (qwen3.5) + deterministic template fallback.

### CLI
```bash
python tools/autoresearch/hypothesis_generator.py --domain compliance --max 5 --json
python tools/autoresearch/hypothesis_generator.py --from-signals --domain compliance --json
python tools/autoresearch/hypothesis_generator.py --health --json
```

## Component 5: Genesis Experiment Reflex

**File:** `tools/genesis/reflexes/experiment.py`

14th Genesis reflex at ORANGE tier. Runs nightly at 01:00.

### Pipeline
1. Pull high-scoring signals from `innovation_signals` (score ≥ 0.70)
2. Load experiment programs for eligible domains (compliance, code_quality, security)
3. Run Bayesian-guided experiment loop per domain (max 3 experiments per domain)
4. Export results as GKP for promotion

## Experiment Programs

Declarative YAML configs in `args/experiment_programs/`:

| Domain | File | Metric | Keep Threshold |
|--------|------|--------|----------------|
| compliance | compliance.yaml | gate_pass_rate | 1% |
| code_quality | code_quality.yaml | maintainability_score | 0.5% |
| security | security.yaml | inverse_vulnerability_density | 0.5% |
| rag_quality | rag_quality.yaml | retrieval_relevance_at_5 | 1% |
| pulse_quality | pulse_quality.yaml | writeguard_score | 2% |
| skill_quality | skill_quality.yaml | assertion_pass_rate | 1% |

## Database Tables (5 new)

| Table | Mutability | Purpose |
|-------|-----------|---------|
| experiment_programs | UPDATE | Domain config reference data |
| experiment_candidates | UPDATE | Hypothesis pool with pgvector |
| experiment_results | **Append-only** | Outcomes (NIST AU-2, D-AR-4) |
| experiment_landscapes | UPDATE | Thompson Sampling posteriors per domain/category |
| bayesian_experiment_scores | **Append-only** | Info gain scores per candidate (D-AR-4) |

## Security Gate

```yaml
autoresearch:
  blocking:
    - experiment_modifies_forbidden_path
    - experiment_exceeds_time_budget
    - experiment_fails_coherence_check
    - experiment_disables_security_gate
  warning:
    - experiment_acceptance_rate_below_threshold
    - experiment_consecutive_failures_high
    - hypothesis_dedup_rate_above_threshold
  thresholds:
    require_test_pass: true
    require_coherence_check: true
    max_consecutive_failures: 3
```

## Configuration

- **Master config:** `args/autoresearch_config.yaml`
- **Domain programs:** `args/experiment_programs/*.yaml`
- **Genesis reflex:** `args/genesis_config.yaml` → `reflexes.experiment`
- **Security gate:** `args/security_gates.yaml` → `autoresearch`

## Testing

```bash
pytest tests/test_autoresearch.py -v    # 33 tests
```

Test categories: CRUD, Bayesian scoring, dedup, fitness evaluation, hypothesis generation, keep/discard decision, append-only enforcement, landscape posteriors, category ordering, CLI output, Genesis reflex interface, experiment count estimation.
