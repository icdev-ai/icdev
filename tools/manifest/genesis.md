# Genesis (Additional)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Genesis (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Goal Template Generator | tools/genesis/goal_template_generator.py | Generate goal templates from GKP artifacts | --json | Goal templates |
| Goal Learner | tools/genesis/goal_learner.py | Detect novel problem-solving not covered by existing goals, auto-generate FORGE goal files with version history and quality scoring | --scan --json | Generated goal markdown files + DB records |
| Synthesize Reflex | tools/genesis/reflexes/synthesize.py | Synthesize reflex: tool-chain pattern detection | --json | Pattern results |
| cATO Monitor Reflex | tools/genesis/reflexes/cato_monitor.py | 6-hour continuous compliance monitoring reflex — discovers *.iqe queries under context/iqe/queries/compliance/, executes them via IQE Executor, and triggers POAM generation for new violations; scanner-tier, air-gap safe | IQE query files (auto-discovered) | Compliance violations + triggered POAM records |
| Canvas Indexer Reflex | tools/genesis/reflexes/canvas_indexer.py | Genesis daemon reflex that indexes 5 canvases (PDC/BDC/DDC/ODC/IDC) from SQLite sidecars into kg_nodes/kg_edges every 6 hours; exports success metrics as JSON | `run(config, trust)` called by Genesis daemon | JSON metrics dict {indexed_count, duration_ms} |

