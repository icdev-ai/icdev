# Autonomous Capability Foundry (ACF)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

ACF (`tools/foundry/`, route `/foundry`, project prefix `acf-`, flag
`ICDEV_FOUNDRY_ENABLED`) is a 0→1 product factory: it autonomously invents,
designs, decomposes, and ships brand-new ICDEV capabilities. Distinct from
Oracle/Genesis reflexes (which improve EXISTING tasks incrementally) — ACF
creates net-new products. Loop: harvester → synthesizer → **novelty_gate** →
scorer → deliberator (CoD go/no-go) → spec_generator → task_graph → seeder.

## Autonomous Capability Foundry
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Novelty Gate | tools/foundry/novelty_gate.py | THE Oracle differentiator — dedup a candidate concept vs a capability catalog built from EXISTING assets (canvas_registry + tools/manifest/*.md + goals/manifest.md). `score_novelty(concept)` returns `{novelty_score=1-max_similarity, nearest, is_duplicate, rejected, reject_reason}`; below `config.novelty.min_novelty` → rejected ('duplicate' or 'low_novelty'). Deterministic token cosine+Jaccard blend; optional embedding re-rank (air-gap safe). | --concept-json, --catalog, --json | Novelty verdict JSON |
