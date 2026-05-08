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

## Server Migration Canvas (SMC)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Server Migration Engine | tools/migration_canvas/server_migration.py | Core engine — inventory parsing, cloud catalog, rightsizing, compat checks, cutover templates, test cases, ERB export, readiness score, live API sync with air-gap fallback | (library) | parse_server_inventory, get_cloud_instances, recommend_target, run_compatibility_checks, compute_rightsizing, generate_default_cutover_steps, build_erb_package, compute_readiness_score, sync_cloud_catalog |
| `parse_server_inventory` | tools/migration_canvas/server_migration.py | Parse server inventory from CSV, JSON, VMware OVF/XML, or manual form dict; returns canonical inventory + nics + disks + services lists | raw_input (str), fmt (csv/json/vmware_export/manual) | {ok, inventory, nics, disks, services, errors} |
| `get_cloud_instances` | tools/migration_canvas/server_migration.py | Query mc_cloud_instances catalog with optional filters (min/max_vcpus, ram, govcloud_only, il_required, family, cost_tier, eol_status) | provider (str\|None), filters (dict) | list[dict] — rows from catalog |
| `recommend_target` | tools/migration_canvas/server_migration.py | Rightsizing engine: effective_vcpu = src × (cpu_util/100) × headroom; scores and ranks top-3 fitting instances | src_specs, migration_type, tgt_platform, perf_data, headroom=1.2 | list[dict] — ranked recommendations |
| `run_compatibility_checks` | tools/migration_canvas/server_migration.py | Auto-generate CAT1/CAT2/CAT3 checks across 6 categories (compute, os, security, storage, network, licensing); refreshes auto_detected rows | session_id | list[dict] |
| `compute_rightsizing` | tools/migration_canvas/server_migration.py | Call recommend_target() and write top-3 to mc_srv_rightsizing; return ranked recommendations | session_id | {recommendations, session_id} |
| `generate_default_cutover_steps` | tools/migration_canvas/server_migration.py | Seed mc_srv_cutover_steps with phase-templated steps for p2p (16), p2v_onprem (13), p2v_cloud (17), v2v_cloud (13), v2v_hypervisor (14) | session_id, migration_type | list[dict] — seeded steps |
| `generate_default_test_cases` | tools/migration_canvas/server_migration.py | Seed mc_srv_test_cases with 10 default tests: 4 pre-migration, 6 post-migration | session_id | list[dict] |
| `build_erb_package` | tools/migration_canvas/server_migration.py | Assemble full ERB JSON from all sub-tables; includes classification: "CUI // SP-CTI" | session_id | dict — ERB package |
| `compute_readiness_score` | tools/migration_canvas/server_migration.py | Weighted 100-pt score (9 components); −20pt per unresolved CAT1; updates mc_srv_sessions.readiness_score | session_id | {score, cat1_blockers, breakdown} |
| `sync_cloud_catalog` | tools/migration_canvas/server_migration.py | Check connectivity (tools.airgap + socket); if online fetch public AWS/Azure/GCP/OCI pricing APIs and upsert with source='api'; returns airgap status if offline | providers (list\|None), force (bool) | {status, providers_synced, rows_upserted, skipped_reason} |
| Server Wizard Template | tools/dashboard/templates/migration_canvas/server_wizard.html | 8-step wizard at /migration-canvas/server-migration/ — migration type, inventory, performance, target selection, compat checks, NIC+storage map, cutover planner, ERB & test plan | (template) | HTML 8-step wizard |

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

