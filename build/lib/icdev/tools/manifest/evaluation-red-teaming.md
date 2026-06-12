# Evaluation & Red Teaming (Phase 65)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Evaluation & Red Teaming (Phase 65)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Red Team Registry | tools/security/red_team_registry.py | YAML-driven adversarial testing framework (6 plugins, promptfoo-inspired) | --run-all, --plugin, --category, --gate, --list, --project-id, --json | Plugin results + gate evaluation |
| Convergence Gates | tools/genesis/convergence.py | Detect phantom improvements and reflex plateau (3 drift vectors + ambiguity, Ouroboros-inspired) | (library — called by daemon post-reflex hook) | Drift scores + recommendation |
| Stagnation Detector | tools/genesis/stagnation_detector.py | Detect stuck reflexes, break plateaus via 5 lateral thinking personas (Ouroboros-inspired) | (library — called by daemon when convergence flags stagnation) | Pattern detection + alternatives |
| Agent Benchmark | tools/evaluation/agent_benchmark.py | Scenario-based 2-tier evaluation of ICDEV™ agents (12 scenarios, 4 agent types, TheAgentCompany-inspired) | --run-all, --agent-type, --scenario, --trend, --gate, --list, --json | Per-agent scores + trend + gate |

