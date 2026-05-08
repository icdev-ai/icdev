# Migration Canvas

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Migration Design Canvas (MDC)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Blueprint | tools/migration_canvas/blueprint.py | Flask Blueprint for ICDEV™ Migration Design Canvas; self-contained at /migration-canvas/ with separate database and ICDEV_MIGRATION_CANVAS_ENABLED feature flag | (library) | create_migration_blueprint() |
| SOPs | tools/migration_canvas/sops.py | Standard Operating Procedures CRUD and approval workflow for Migration Design Canvas (readiness assessment, cutover planning, rollback, validation) | (library) | SOP CRUD functions |

## Network Migration Canvas Enhancement (NMCE)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Network Migration Engine | tools/migration_canvas/network_migration.py | Core engine — inventory, AI recommendation, config loader, protocol planning, parallel timeline; 6 public functions added in NMCE | (library) | see functions below |
| `get_network_inventory` | tools/migration_canvas/network_migration.py | Query ni_devices (network_canvas.db) with EOL/vendor/type filters; falls back to nc_hardware_profiles; returns config-source + active-session status per device | site, device_type, vendor, eol_within_years | list[dict] |
| `load_device_config_from_db` | tools/migration_canvas/network_migration.py | Pull most-recent running/startup config from ni_device_configs for a given device_id | device_id | str \| None |
| `recommend_hardware` | tools/migration_canvas/network_migration.py | LLM-ranked top-3 hardware replacements from nc_hardware_profiles; deterministic fallback by throughput delta; saves AI turn to mc_net_ai_sessions | device_info, engineer_notes, session_id | {recommendations, model} |
| `ai_assist` | tools/migration_canvas/network_migration.py | Contextual chat for migration engineers; loads last 5 turns from mc_net_ai_sessions; degrades to "AI unavailable" on LLMUnavailableError | session_id, engineer_prompt | {response, model} |
| `plan_protocol_migration` | tools/migration_canvas/network_migration.py | Per-protocol migration steps (BGP/OSPF/VLAN/LAG/MPLS/ACL) generated from parsed config; upserts mc_net_protocol_plans | session_id | {protocols: {name: {steps, risk_level, ...}}} |
| `build_parallel_timeline` | tools/migration_canvas/network_migration.py | 15–20 conditional milestones from D-30 to D+30 relative to cutover; writes mc_net_parallel_timelines; conditional on BGP/OSPF/MPLS/optic_change flags | session_id | list[dict] |
| Network Inventory Page | tools/dashboard/templates/migration_canvas/network_inventory.html | Inventory dashboard at /migration-canvas/network-migration/ — 27 devices, EOL chips, config-source chips, active migration links, 5-filter toolbar | (template) | HTML page |

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

