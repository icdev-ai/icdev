# Autonomy Engine

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Autonomy Engine
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Behavior Learner | tools/autonomy/behavior_learner.py | Behavioral pattern learning from agent actions | --json | Learned patterns |
| Federation Router | tools/autonomy/federation.py | Engine federation router — auto-routes signals between Innovation/Creative/Research engines via Bayesian trust gate (D-AE-11) | --check / --route / --dry-run / --status --json | Routeable signals / routing results |
| Kill Switch | tools/autonomy/kill_switch.py | Emergency agent termination control | --json | Termination status |
| Self Evolve | tools/autonomy/self_evolve.py | Self-evolution capability engine | --json | Evolution results |
| Trust Engine | tools/autonomy/trust_engine.py | Dynamic trust scoring for autonomous actions | --json | Trust scores |

