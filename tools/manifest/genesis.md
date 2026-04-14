# Genesis (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Genesis (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Goal Template Generator | tools/genesis/goal_template_generator.py | Generate goal templates from GKP artifacts | --json | Goal templates |
| Goal Learner | tools/genesis/goal_learner.py | Detect novel problem-solving not covered by existing goals, auto-generate FORGE goal files with version history and quality scoring | --scan --json | Generated goal markdown files + DB records |
| Synthesize Reflex | tools/genesis/reflexes/synthesize.py | Synthesize reflex: tool-chain pattern detection | --json | Pattern results |

