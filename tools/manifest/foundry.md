# Autonomous Capability Foundry (ACF)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Autonomous Capability Foundry (ACF)
A 0→1 product factory (`tools/foundry/`, prefix `acf-`, dashboard `/foundry`) that bridges
signal-discovery → coherent net-new product → autonomous Kanban build. Consumes the
Innovation/Creative/Research signal stores + Genesis predictions + internal telemetry; it
does not build new scanners. See `docs/features/phase-acf-autonomous-capability-foundry.md`
for the full design and guardrails (novelty gate, score threshold, CoD go/no-go, SIPA
self-vetting, circuit breaker).

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Constants | tools/foundry/constants.py | Source-engine + signal-status enums, severity→weight map, source precedence (telemetry > genesis > external), `ICDEV_FOUNDRY_ENABLED` master toggle. SQL CHECK constraints in db/init_db.py are derived from these. | (data) | SOURCE_ENGINES, SIGNAL_STATUSES, SEVERITY_WEIGHTS, SOURCE_PRECEDENCE |
| DB Schema | tools/foundry/db/init_db.py | Creates `foundry_signals` (append-only, RLS-aware: tenant_id + classification). Dual PG/SQLite schema. UNIQUE(dedup_hash) enforces cross-source dedup at the DB layer. | init_foundry_db(db_path=None) | foundry_signals table |
| Harvester | tools/foundry/harvester.py | Pulls signals from 5 sources → normalized `foundry_signals`: innovation (innovation_signals), creative/research (optional stores), genesis (oracle_predictions incl. KG self-awareness gap nodes), telemetry (introspective_analyzer: gate failures, unused tools, slow pipelines, knowledge gaps). Cross-source dedup via SHA-256 of normalized theme+keywords — the same signal from two engines collapses to one row, recording every contributing engine. | --harvest [--source <engine>] [--db-path] [--json] | {raw_signals, per_source, collapsed_signals, deduped, inserted} |
| Meta Scorer | tools/foundry/meta_scorer.py | Adaptive-threshold self-tuning for the ACF composite score gate (acf-ada-02). Mirrors the Genesis `meta_harness` pattern: sliding-window false-approve analytics tighten `scoring.min_composite` when `vv_fail`/`abandoned` rate exceeds the ceiling, and loosen it on a clean run; retired sub-score weights are proposed to `args/meta_harness_proposals.yaml` for the shared human-merge review queue. Pure statistics — no LLM, no network. | --run [--dry-run] [--json] | {action, old_threshold, new_threshold, false_approve_rate, proposals, written} |
