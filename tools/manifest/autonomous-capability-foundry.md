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
| Harvester | tools/foundry/harvester.py | Pulls raw signals from EXISTING engine stores (no web re-scan): innovation (`innovation_signals`+`innovation_trends`), creative (`creative_pain_points`+`creative_feature_gaps`), research (`research_challenges`+`research_dossiers`). `harvest(run_id, *, config, conn)` normalizes each row into the foundry_signals shape `(source_engine, source_ref, theme, raw_score, keywords)` and appends it under run_id (tenant_id/classification stamped). Per-source enable/`max_signals` caps from args/foundry_config.yaml. Best-effort: empty/disabled/unmigrated source → 0 signals, never an error. | --run-id, --json | Normalized signal list + foundry_signals rows |
| Foundry Schema | tools/foundry/db/init_db.py | Idempotent PG-first dual-schema (`_SCHEMA_SQLITE` canonical, `_SCHEMA_PG` via .replace transform) creating the six `foundry_*` platform findings tables (runs/signals/concepts/specs/tasks_emitted/outcomes). All carry tenant_id+classification (RLS-aware get_connection, NOT canvas). CHECK constraints derived from `tools/foundry/constants.py`. `init_db()` degrades gracefully (logs, never raises). | init_db(force) | 6 foundry_* tables |
