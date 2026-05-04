# Migration Canvas

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Migration Design Canvas (MDC)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Blueprint | tools/migration_canvas/blueprint.py | Flask Blueprint for ICDEV™ Migration Design Canvas; self-contained at /migration-canvas/ with separate database and ICDEV_MIGRATION_CANVAS_ENABLED feature flag | (library) | create_migration_blueprint() |
| SOPs | tools/migration_canvas/sops.py | Standard Operating Procedures CRUD and approval workflow for Migration Design Canvas (readiness assessment, cutover planning, rollback, validation) | (library) | SOP CRUD functions |

## Migration Intelligence Engine (MI)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DB Init | tools/migration_intelligence/db/init_db.py | Initialize migration_intel.db with 8 tables: mi_goals, mi_goal_chat_log, mi_opportunities, mi_goal_alignments, mi_strategies, mi_roadmaps, mi_wishlist, mi_budget_cycles, mi_scans | db_path? | init_db(), get_connection() |
| Migration Manager | tools/migration_intelligence/migration_manager.py | 8-stage pipeline orchestrator: SCAN→ALIGN→SCORE→STRATEGIZE→ROADMAP→SIMULATE→PROMOTE; daemon mode; air-gap conditional | --run, --scan, --status, --daemon | run_full_pipeline(), get_status() |
| Goal Manager | tools/migration_intelligence/goal_manager.py | Enterprise goal CRUD + NL chat parsing (air-gap conditional LLM parse); goal↔opportunity alignment scoring | title, description, category, priority, source | create_goal(), parse_goals_from_chat(), compute_goal_alignment() |
| Opportunity Scanner | tools/migration_intelligence/opportunity_scanner.py | Cross-canvas discovery: NDC hardware EOL, network topology, MDC designs; writes to mi_opportunities | config?, db_path? | run_full_scan(), scan_hardware_eol() |
| Strategy Generator | tools/migration_intelligence/strategy_generator.py | 7R strategy generation + composite scoring + wave-based roadmap builder | opportunity_id, config?, db_path? | score_all_opportunities(), generate_all_strategies(), build_roadmap() |
| Blueprint | tools/migration_intelligence/blueprint.py | Flask blueprint at /migration-intel/ + /api/migration-intel/...; goals, opportunities, strategies, roadmaps, wishlist, budget cycles, scan history | (library) | create_migration_intel_blueprint() |
| Config | args/migration_intelligence_config.yaml | Air-gap gates, scoring weights, scan intervals, EOL thresholds, strategy templates, roadmap wave config | (config file) | read by migration_manager |
| Genesis Reflex | tools/genesis/reflexes/migration_intel.py | Autonomous 24h Genesis reflex — runs full MI pipeline; air-gap safe | config, trust | run(config, trust) |

