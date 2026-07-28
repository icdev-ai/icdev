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

## Migration Canvas Extensions (MCE)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Compliance Gate | tools/migration_canvas/compliance_gate.py | Deterministic IL-level / target-environment compliance checks — NIST 800-53, FedRAMP, DISA STIG; no LLM; CLI: `--il-level`, `--target-env`, `--migration-type`, `--frameworks` | il_level, target_env, migration_type?, frameworks? | {proceed, status, findings, frameworks_applied} |
| Inventory Scanner | tools/migration_canvas/inventory_scanner.py | Parses server/VM inventory from CSV, JSON, or Nmap XML and populates mc_srv_inventory + mc_inventory_imports; CLI: `--csv`/`--json`/`--nmap`, `--session-id`, `--dry-run` | file path, session_id | {inserted, skipped, import_id, dry_run} |
| Wave Planner | tools/migration_canvas/wave_planner.py | Groups server migration sessions into sequenced waves based on readiness score; builds Sigma.js-compatible dependency graph; also emits per-wave template-driven, editable, HITL-approved backout/recovery sections (`generate_backout_section`/`upsert_backout_section`/`approve_backout_section`, stored in mc_wave_backout — editing resets approval); CLI: `--list`/`--graph`/`--auto-assign`/`--deps` | session_id | waves list / graph {nodes, edges} / assignments dict / backout {snapshot_prerequisites, decision_points, go_no_go_criteria, recovery_steps, approved} |
| Workload Validator | tools/migration_canvas/workload_validator.py | Per-workload post-migration validation checklist that COMPOSES existing engines (health probe → post_migration_validator; security scan → code_pattern_scanner; STIG readiness → aiify stig_compliance PILLAR; optional IDC twin before/after resource diff); records status in mc_workload_validations; gates wave close (`can_close_wave`/`close_wave`) — a wave with any failing validation is not closeable without an audited HITL `force` override logged to append-only mc_wave_close_overrides; CLI: `--status`/`--can-close`/`--list` | session_id, wave_id, workload dict | {results, summary} / {all_pass, failing, counts} / close {ok, status, override_id?} |
| Dossier Advisor | tools/migration_canvas/dossier_advisor.py | Surfaces relevant research challenges from Research Engine DB per wizard step; maps step → conceptual category → DB category → top-k challenges by composite_score; CLI: `--step`, `--top-k` | wizard_step (1–7), migration_type?, top_k | list[{title, description, severity, category}] |
| Wave Planner | tools/migration_canvas/wave_planner.py | Groups server migration sessions into sequenced waves based on readiness score; builds Sigma.js-compatible dependency graph; CLI: `--list`/`--graph`/`--auto-assign`/`--deps` | session_id | waves list / graph {nodes, edges} / assignments dict |

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

## Hypervisor Adapters (Gap Fill — 2026-05-09)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Adapter Dispatcher | tools/migration_canvas/adapters/__init__.py | Unified `pull_inventory()` dispatcher across vmware/hyperv/nutanix; returns canonical `{ok, adapter, host, vms, vm_count, error}` | adapter_type, host, user, password, kwargs | dict |
| VMware vSphere Adapter | tools/migration_canvas/adapters/vmware_adapter.py | Pull live VM inventory from vSphere REST API (`/rest/vcenter/vm`); session-based auth via stdlib urllib; graceful offline fallback (socket pre-check); maps to canonical inventory schema | host, user, password, datacenter? | list[dict] |
| Hyper-V Adapter | tools/migration_canvas/adapters/hyperv_adapter.py | Pull live VM inventory via PowerShell subprocess (`Get-VM`); local and remote (WinRM) modes; no pywin32 dep; air-gap safe | host, user, password | list[dict] |
| Nutanix Prism Adapter | tools/migration_canvas/adapters/nutanix_adapter.py | Pull live VM inventory from Nutanix Prism REST API v2 (`/api/nutanix/v2.0/vms/`); paginated; Basic auth; port 9440 pre-check | host, user, password, cluster? | list[dict] |

**New DB Table:** `mc_srv_hypervisor_sessions` — records each pull attempt (adapter, host, vm_count, status, error).

**New Routes (in blueprint.py):**
- `POST /api/srv/<session_id>/hypervisor-pull` — pull live inventory from hypervisor, upsert to mc_srv_inventory
- `GET /api/srv/<session_id>/hypervisor-sessions` — list prior hypervisor pull records

## Cloud Application Migration (CAM) — K8s → AWS + AI Modernization

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| CAM Constants | tools/migration_canvas/cam_constants.py | Config-driven constants: SERVICE_MAPPINGS from service_mappings.yaml, CANVAS_SOP_TABLES/ID/STEPS column maps, AI_SERVICE_LABELS, status enums | (library) | SERVICE_MAPPINGS, CANVAS_SOP_TABLES, AI_SERVICE_LABELS, etc. |
| CAM Engine | tools/migration_canvas/cam_engine.py | Project loader: get_projects(), get_project_detail(), _find_sop(), _load_sop_steps(), get_canvas_links(); cross-canvas SOP resolution via mc→idc→ddc→ndc | project_id, conn | project dict with phases, linked_sops (normalized), components, ai_opportunities, canvas_links |
| CAM SOP Seeder | tools/migration_canvas/cam_seed_sops.py | Discovers context/migration/sop_catalog/*.json; routes each to mc_sops/ddc_sops/idc_sops/ndc_sops per sop_target_canvas; INSERT OR IGNORE; CLI: --reset, --json | (standalone) | {total, by_canvas} |
| CAM Demo Seeder | tools/migration_canvas/cam_seed_demo.py | Seeds Analytics Platform K8s→AWS demo project: mc_projects, phases, SOP links, app_inventory, data_migration, wave_plans, ai_opportunities; also seeds DDC/IDC/NDC designs | --project, --reset, --json | {project_id, phases, sop_links, ai_opportunities, app_components, ddc/idc/ndc IDs} |
| CAM Refactor Engine | tools/migration_canvas/cam_refactor_engine.py | Bridges CAM pipeline with code transformation toolchain: reads refactor_rules.yaml, dispatches jobs to db_migration_planner / framework_migrator / version_migrator / translation_manager / scaffold generators; stores results in mc_refactor_jobs with per-artifact paths | --project, --component, --dry-run, --list-jobs, --run-job, --json | {total, completed, failed, jobs[]} with artifacts_json per job |
| COA Engine | tools/migration_canvas/coa_engine.py | Generates Courses of Action with pros/cons for any source technology; reads service_mappings.yaml + refactor_rules.yaml; get_coas(tech, cloud), get_deprecation_status(tech), format_coa_markdown(coas), get_all_coas_for_stack(components) | tech, cloud='aws' | list[coa_dict] with pros[], cons[], effort_days, risk, tools[], ai_opportunities[], canvas_hints{} |
| Migration Chat Advisor | tools/migration_canvas/migration_chat_advisor.py | Slash-command processor + intent detector for cam canvas: /coa, /deprecated, /refactor, /status, /components, /analyze; get_migration_advisory() for chat_message_after hook; LLM-enhanced with migration system prompt | content, session_id, canvas_type | {reply, mode, coa_cards?, deprecation?, jobs?, findings?} |

**New DB Tables (5):** `mc_projects`, `mc_project_phases`, `mc_project_phase_sops`, `mc_ai_opportunities`, `mc_refactor_jobs`.

**Canvas Extensions:** `dd_migration_jobs` added to DDC, `idc_migration_baselines` added to IDC.

**New Routes (in blueprint.py):**
- `GET /migration-canvas/projects` → cam_projects.html
- `GET /migration-canvas/projects/<project_id>` → cam_project_detail.html (includes Refactoring section)
- `GET /migration-canvas/api/projects` → JSON list
- `GET /migration-canvas/api/projects/<project_id>` → JSON detail
- `GET /migration-canvas/api/projects/<id>/refactor-jobs` → list all refactor jobs
- `POST /migration-canvas/api/projects/<id>/refactor` → dispatch + run jobs `{component_name?, run?, dry_run?}`
- `GET /migration-canvas/api/projects/<id>/refactor-jobs/<job_id>` → job detail + artifacts
- `POST /migration-canvas/api/projects/<id>/refactor-jobs/<job_id>/run` → execute single queued job

**Config files:**
- `context/migration/service_mappings.yaml` — 10 source-tech → AWS/Azure/GCP service mappings
- `context/migration/refactor_rules.yaml` — dispatch rules: (tech, strategy_7r, language, framework) → refactor job type(s)
- `context/migration/sop_catalog/*.json` — 17 SOP definitions (auto-discovered by seed)
- `context/migration/projects/analytics-k8s-aws.yaml` — demo project definition

## EOL Sync (Gap Fill — 2026-05-09)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| EOL Sync | tools/migration_canvas/eol_sync.py | Vendor EOL/EOS date lookup and auto-sync; 101-entry static YAML seed (`args/eol_data.yaml`) + best-effort Cisco API live sync; 5-match-strategy lookup (exact/prefix/substring/regex/fuzzy); `flag_eol_devices()` annotates inventory | vendors?, force? | sync results dict |
| EOL Data Seed | args/eol_data.yaml | 101 static EOL entries for Cisco, Juniper, Arista, Palo Alto, Fortinet, Check Point, Extreme/Brocade, F5, Nokia — offline fallback for eol_sync | (config file) | loaded by eol_sync |

**New DB Table:** `mc_net_eol_data` — vendor, model_pattern, eol_date, eos_date, eosm_date, source, synced_at.

**New Routes:**
- `POST /api/net/eol-sync` — trigger EOL database sync
- `GET /api/net/eol/<session_id>/flags` — flagged devices in a session

## Vendor Migration Paths (Gap Fill — 2026-05-09)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Vendor Migration Paths | tools/migration_canvas/vendor_migration_paths.py | YAML-driven catalog of 15 source→target migration playbooks (e.g. Cisco ASA→Fortinet, Nexus→Arista); `get_migration_path()`, `list_compatible_targets()`, `get_migration_checklist()`; lazy-loaded `_CACHE` | source_vendor, source_family, target_vendor | dict\|list |
| Migration Paths Config | args/vendor_migration_paths.yaml | 15 migration paths with phases, protocol_notes, gotchas, complexity, estimated_hours; YAML-driven — add new paths without code changes | (config file) | loaded by vendor_migration_paths |

**New Routes:**
- `GET /api/net/migration-paths` — list all paths (summary only)
- `GET /api/net/migration-paths/<source_vendor>/<source_family>` — get target options or specific path

## Post-Migration Validation (Gap Fill — 2026-05-09)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Post-Migration Validator (Server) | tools/migration_canvas/post_migration_validator.py | 6 validation checks: TCP connectivity, SSL cert expiry, HTTP service health, DNS resolution, disk space (via SSH), process running (via SSH); `run_validation_suite()` dispatches all checks and writes results | session_id, targets: list[{type, ...}] | {suite_id, session_id, summary, results, pass_count, fail_count} |
| Network Config Validator | tools/migration_canvas/network_config_validator.py | Section-based config diff (interfaces, routing, BGP, OSPF, ACL, VLAN, MPLS, NTP, logging); weighted completeness score (interfaces=30%, routing+BGP=30%, etc.); `validate_migration_completeness()` writes to mc_net_config_validation | session_id \| source/target config strings + vendor | diff dict with {added, removed, changed, unchanged, completeness_score} |

**New DB Tables:**
- `mc_srv_post_migration_tests` — per-check results (check_type, target, status, detail, elapsed_ms)
- `mc_net_config_validation` — per-session config diff summary (diff_summary, completeness_score, status)

**New Routes:**
- `POST /api/srv/<session_id>/validate` — run post-migration checks
- `GET /api/srv/<session_id>/validation-runs` — list prior validation runs
- `POST /api/net/<session_id>/validate-config` — run config diff vs source
- `GET /api/net/<session_id>/validation` — fetch latest config validation result

## Advanced Cloud Pricing + Protocol Plans (Gap Fill — 2026-05-09)

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| `get_cloud_instances_advanced` | tools/migration_canvas/server_migration.py | Extended catalog query supporting spot/reserved_1yr/reserved_3yr/savings_plan pricing models; ranks by price for selected model; new columns on mc_cloud_instances | provider, filters, pricing_model, vcpu_min, ram_min | list[dict] |
| `import_from_hypervisor` | tools/migration_canvas/server_migration.py | Pull live VM inventory from hypervisor (via adapters), record session to mc_srv_hypervisor_sessions, upsert VMs to mc_srv_inventory | session_id, adapter_type, host, user, password, datacenter?, cluster? | {ok, session_id, vm_count, adapter, status} |
| Advanced Protocol Plans | tools/migration_canvas/network_migration.py | `plan_protocol_migration()` extended with variant overrides; BGP variants: multipath/route_reflector/graceful_restart; OSPF variants: multi_area/stub_nssa/virtual_link; MPLS variants: vrf_lite/segment_routing/evpn_vxlan; new SD-WAN 8-step plan | session_id, variant_overrides? | protocols dict with variant + advanced_config per plan |

**New Columns on Existing Tables:**
- `mc_cloud_instances`: pricing_model, spot_price, reserved_1yr_price, reserved_3yr_price, savings_plan_price, interruption_rate
- `mc_net_protocol_plans`: variant, advanced_config

**New Route:**
- `GET /api/srv/cloud-catalog/advanced` — filtered advanced cloud catalog with pricing model selection

