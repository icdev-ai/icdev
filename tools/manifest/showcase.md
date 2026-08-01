# Showcase

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Showcase
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Synthetic Data Engine | tools/showcase/synthetic_data_engine.py | Generate realistic synthetic datasets per domain. **Library, not a CLI** — import `SyntheticDataEngine` / `DOMAINS` | domain, record count (constructor args) | Dataset records |
| AI Canvas Demo Runner | tools/showcase/ai_canvas_demo_runner.py | 5-act DoD/IC demo: Observatory → AADC → AIMC → AAC → Cross-canvas. Queries live canvas DBs, prints formatted demo output with IQE queries and ROI metrics | --scenario 1-5, --audience exec\|tech, --json | Per-scenario result dicts + executive summary |
