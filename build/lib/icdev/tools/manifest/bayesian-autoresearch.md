# Bayesian Autoresearch (Phase 67, D-AR-1 through D-AR-10)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Bayesian Autoresearch (Phase 67, D-AR-1 through D-AR-10)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Experiment Engine | tools/autoresearch/experiment_engine.py | Core Karpathy Loop — create, run, evaluate, decide, autonomous loop (D-AR-1) | --create, --run, --evaluate, --decide, --loop, --status, --health, --domain, --experiment-id, --max-experiments, --overnight, --json | Experiment results + decisions |
| Bayesian Selector | tools/autoresearch/bayesian_selector.py | Bayesian info-gain experiment selection + Thompson Sampling + pgvector dedup (D-AR-5, D-AR-6) | --score, --select, --estimate, --category-order, --health, --domain, --json | Scored candidates + selection |
| Fitness Evaluator | tools/autoresearch/fitness_evaluator.py | Wraps 6 ICDEV™ tools into single-metric [0,1] scorers (D-AR-7) | --evaluate, --evaluate-all, --list-domains, --health, --project-id, --project-dir, --json | Domain metric values |
| Hypothesis Generator | tools/autoresearch/hypothesis_generator.py | Scanner-tier LLM + template fallback hypothesis creation (D-AR-1) | --domain, --max, --from-signals, --health, --json | Hypothesis candidates |
| Experiment Reflex | tools/genesis/reflexes/experiment.py | 14th Genesis reflex — Bayesian Autoresearch at ORANGE tier (D-AR-9) | config dict, trust kernel | Reflex results + GKP export |

