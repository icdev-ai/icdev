# Registration Sweep — issue #14 (tool_not_in_manifest)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

Registers tools previously flagged by the gap detector's `tool_not_in_manifest`
rule (8-point new-tool checklist, point 1). Descriptions are sourced from each
module's docstring.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cross Agency Transfer | tools/dashboard/api/cross_agency_transfer.py | Flask Blueprint: cross-agency data transfer audit API. | `--json` / (library) | JSON / objects |
| Executive | tools/dashboard/api/executive.py | Dashboard API: Executive Migration Metrics. | `--json` / (library) | JSON / objects |
| Genesis | tools/dashboard/api/genesis.py | Dashboard API: Genesis Reflex Coverage and Health. | `--json` / (library) | JSON / objects |
| Il5 | tools/dashboard/api/il5.py | Flask Blueprint for IL5 data ingestion and display API. | `--json` / (library) | JSON / objects |
| Jise | tools/dashboard/api/jise.py | tools/dashboard/api/jise.py — JISE Portal REST API. | `--json` / (library) | JSON / objects |
| Servicenow Changes | tools/dashboard/api/servicenow_changes.py | Dashboard API: ServiceNow Change Management Tickets. | `--json` / (library) | JSON / objects |
| Provenance | tools/dashboard/pages/provenance.py | Dashboard API: Provenance verification endpoints. | `--json` / (library) | JSON / objects |
| Contract Engine | tools/data_canvas/data_mesh/contract_engine.py | Data Mesh — Contract Engine (ODCS v1.1+ compatible). | `--json` / (library) | JSON / objects |
| Domain Manager | tools/data_canvas/data_mesh/domain_manager.py | Data Mesh — Domain Manager. | `--json` / (library) | JSON / objects |
| Governance Engine | tools/data_canvas/data_mesh/governance_engine.py | Federated Governance Engine — OPA REST client with pure-Python fallback. | `--json` / (library) | JSON / objects |
| Lineage Emitter | tools/data_canvas/data_mesh/lineage_emitter.py | ICDEV™ Data Mesh — OpenLineage Emission Wrapper. | `--json` / (library) | JSON / objects |
| Product Registry | tools/data_canvas/data_mesh/product_registry.py | Data Mesh — Product Registry. | `--json` / (library) | JSON / objects |
| Servicenow Connector | tools/databridge/connectors/servicenow_connector.py | ServiceNow CMDB DataBridge Connector. | `--json` / (library) | JSON / objects |
| 024 Telegram Inbox | tools/db/migrations/024_telegram_inbox.py | Migration 024 — telegram_inbox table for durable Telegram message receipt. | `--json` / (library) | JSON / objects |
| 168 Seed Canvas Grants | tools/db/migrations/168_seed_canvas_grants.py | Migration 168 — Seed canvas_access_grants for existing tenants. | `--json` / (library) | JSON / objects |
| 172 Aiify Rename | tools/db/migrations/172_aiify_rename.py | Migration 172: AAC -> AI-ify canvas table rename (data-preserving). | `--json` / (library) | JSON / objects |
| 173 White Team Review Type | tools/db/migrations/173_white_team_review_type.py | Migration 173: Add 'white_team' to proposal_reviews review_type CHECK constraint. | `--json` / (library) | JSON / objects |
| 177 Cpmp Contract Mods | tools/db/migrations/177_cpmp_contract_mods.py | Migration 177: Add cpmp_contract_mods table + status history support. | `--json` / (library) | JSON / objects |
| Enrich Govcon Demo | tools/db/seeds/enrich_govcon_demo.py | Enrich synthetic GovCon demo proposals with compliance items, reviews, findings, and questions. | `--json` / (library) | JSON / objects |
| Seed Ai Canvases Aac | tools/db/seeds/seed_ai_canvases_aac.py | Seed DoD/IC synthetic demo data for the AI Augmentation Canvas (AAC). | `--json` / (library) | JSON / objects |
| Seed Ai Canvases Docs | tools/db/seeds/seed_ai_canvases_docs.py | Download and ingest 12 public DoD/IC AI governance PDFs + 5 synthetic | `--json` / (library) | JSON / objects |
| Seed Ai Canvases Kg | tools/db/seeds/seed_ai_canvases_kg.py | Seed DoD/IC AI Governance Knowledge Graph — 38 nodes + 30+ edges. | `--json` / (library) | JSON / objects |
| Seed Ai Canvases Observatory | tools/db/seeds/seed_ai_canvases_observatory.py | Seed AI Observatory with 200 realistic DoD/IC canvas_ai_decisions. | `--json` / (library) | JSON / objects |
| Seed Aisg Demo | tools/db/seeds/seed_aisg_demo.py | AISG Demo Seed -- populates aisg_canvas.db with realistic AI strategy demo data. | `--json` / (library) | JSON / objects |
| Seed Bdc Demo | tools/db/seeds/seed_bdc_demo.py | BDC Demo Seed -- populates boundary_canvas.db with realistic boundary design demo data. | `--json` / (library) | JSON / objects |
| Seed Canvas Quartet | tools/db/seeds/seed_canvas_quartet.py | Canvas Quartet Orchestrator -- seeds all four canvases (NOCC, PMC, CCC, DSOC) in sequence. | `--json` / (library) | JSON / objects |
| Seed Ccc Demo | tools/db/seeds/seed_ccc_demo.py | CCC Demo Seed -- populates ccc_canvas.db with realistic circuit & capacity demo data. | `--json` / (library) | JSON / objects |
| Seed Cu Plan | tools/db/seeds/seed_cu_plan.py | seeds module — seed cu plan. | `--json` / (library) | JSON / objects |
| Seed Ddc Demo | tools/db/seeds/seed_ddc_demo.py | DDC Demo Seed -- populates data_canvas.db with realistic data design demo data. | `--json` / (library) | JSON / objects |
| Seed Dsoc Demo | tools/db/seeds/seed_dsoc_demo.py | DSOC Demo Seed -- populates dsoc_canvas.db with realistic DDoS & security ops demo data. | `--json` / (library) | JSON / objects |
| Seed Idc Demo | tools/db/seeds/seed_idc_demo.py | IDC Demo Seed -- populates infra_canvas.db with realistic infrastructure design demo data. | `--json` / (library) | JSON / objects |
| Seed Mc Demo | tools/db/seeds/seed_mc_demo.py | Migration Canvas Demo Seed -- populates migration_canvas.db with realistic migration demo data. | `--json` / (library) | JSON / objects |
| Seed Mission Demo | tools/db/seeds/seed_mission_demo.py | Mission Canvas Demo Seed -- populates mission_canvas.db with realistic mission demo data. | `--json` / (library) | JSON / objects |
| Seed Nc Demo | tools/db/seeds/seed_nc_demo.py | NC Demo Seed -- populates network_canvas.db with realistic network design demo data. | `--json` / (library) | JSON / objects |
| Seed Nocc Demo | tools/db/seeds/seed_nocc_demo.py | NOCC Demo Seed -- populates noc_canvas.db with realistic NOC operations demo data. | `--json` / (library) | JSON / objects |
| Seed Odc Demo | tools/db/seeds/seed_odc_demo.py | ODC Demo Seed -- populates observability_canvas.db with realistic observability design demo data. | `--json` / (library) | JSON / objects |
| Seed Pmc Demo | tools/db/seeds/seed_pmc_demo.py | PMC Demo Seed -- populates pmc_canvas.db with realistic peering management demo data. | `--json` / (library) | JSON / objects |
| Seed Qdc Demo | tools/db/seeds/seed_qdc_demo.py | QDC Demo Seed -- populates qdc_canvas.db with realistic quality design demo data. | `--json` / (library) | JSON / objects |
| Seed Sdc Demo | tools/db/seeds/seed_sdc_demo.py | SDC Demo Seed -- populates security_canvas.db with realistic before/after demo data. | `--json` / (library) | JSON / objects |
| E2E Runner | tools/genesis/reflexes/e2e_runner.py | Genesis E2E Runner Reflex — daily Playwright smoke suite via Kanban scheduler. | `--json` / (library) | JSON / objects |
| Harness | tools/genesis/reflexes/harness.py | Harness Reflex — runs every 6h, checks evaluation gates, promotes degradation cards. | `--json` / (library) | JSON / objects |
| Migration Canvas | tools/genesis/reflexes/migration_canvas.py | Genesis Reflex — Network Migration Canvas Health Monitor. | `--json` / (library) | JSON / objects |
| Seed Demo | tools/govlift/db/seed_demo.py | GovLift Demo Data Seeder. | `--json` / (library) | JSON / objects |
| Integrations | tools/govlift/integrations.py | GovLift — External Interface Integration Registry and Health Module. | `--json` / (library) | JSON / objects |
| Rbac | tools/govlift/rbac.py | GovLift RBAC — Role-Based Access Control (AC-2, AC-3, AC-6). | `--json` / (library) | JSON / objects |
| Iac Generator | tools/idc/iac_generator.py | Infrastructure IaC Generator — IDC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Bus Subscriber | tools/infra_canvas/bus_subscriber.py | IDC Cross-Canvas Event Bus Subscriber. | `--json` / (library) | JSON / objects |
| Dockerfile Generator | tools/infra_canvas/dockerfile_generator.py | Infrastructure Canvas — Dockerfile & Docker Compose Generator. | `--json` / (library) | JSON / objects |
| Cam | tools/iqe/adapters/cam.py | IQE Cloud Application Migration (CAM) collection adapters. | `--json` / (library) | JSON / objects |
| Demo Runner | tools/iqe/adapters/demo_runner.py | IQE Demo Runner collection adapters. | `--json` / (library) | JSON / objects |
| Dic | tools/iqe/adapters/dic.py | IQE Document Intelligence Canvas collection adapters. | `--json` / (library) | JSON / objects |
| Dsoc | tools/iqe/adapters/dsoc.py | IQE adapter for DDoS & Security Ops Canvas (DSOC). | `--json` / (library) | JSON / objects |
| Gameday | tools/iqe/adapters/gameday.py | IQE AI GameDay League collection adapters. | `--json` / (library) | JSON / objects |
| Govcon | tools/iqe/adapters/govcon.py | IQE adapter — GovCon / Proposals canvas (prop-cap-13). | `--json` / (library) | JSON / objects |
| Innovation | tools/iqe/adapters/innovation.py | IQE FORGE IGNITE Innovation canvas collection adapters. | `--json` / (library) | JSON / objects |
| Ontology | tools/iqe/adapters/ontology.py | IQE ontology collection adapters. | `--json` / (library) | JSON / objects |
| Strategos | tools/iqe/adapters/strategos.py | IQE adapter — Strategos Intelligence Canvas. | `--json` / (library) | JSON / objects |
| Supply Chain | tools/iqe/adapters/supply_chain.py | IQE supply chain collection adapters. | `--json` / (library) | JSON / objects |
| Init Db | tools/kanban/init_db.py | Initialize Kanban DB tables (idempotent — safe to call at startup). | `--json` / (library) | JSON / objects |
| Metrics | tools/kanban/metrics.py | Kanban workflow metrics — process-health analytics for the Inspect & Adapt pipeline. | `--json` / (library) | JSON / objects |
| A2A Bridge Server | tools/mcp/a2a_bridge_server.py | MCP-A2A Bridge Server — exposes Unified MCP tools over HTTP (A2A protocol). | `--json` / (library) | JSON / objects |
| Ontology Server | tools/mcp/ontology_server.py | Ontology MCP server — tools for ontology federation and external standard mappings. | `--json` / (library) | JSON / objects |
| Iac Generator | tools/mdc/iac_generator.py | Migration IaC Generator — MDC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Inventory Scanner | tools/mdc/inventory_scanner.py | Inventory Scanner — MDC Workflow Step 1. | `--json` / (library) | JSON / objects |
| Migration Executor | tools/migration/migration_executor.py | ICDEV™ Migration Workflow — Migration Executor. | `--json` / (library) | JSON / objects |
| Validator | tools/migration/validator.py | ICDEV™ Migration Workflow — Post-Migration Validator. | `--json` / (library) | JSON / objects |
| Wave Planner | tools/migration/wave_planner.py | ICDEV™ Migration Workflow — Wave Planner. | `--json` / (library) | JSON / objects |
| Blueprint | tools/mission_canvas/blueprint.py | Mission Canvas — Flask Blueprint. | `--json` / (library) | JSON / objects |
| Conflict Resolver | tools/mission_canvas/conflict_resolver.py | Mission Canvas — Conflict Detection & Resolution wrapper. | `--json` / (library) | JSON / objects |
| Correlator | tools/mission_canvas/correlator.py | Mission Canvas — Real-Time Correlation wrapper. | `--json` / (library) | JSON / objects |
| Discovery | tools/mission_canvas/discovery.py | Mission Canvas — Automated Discovery & Visualization wrapper. | `--json` / (library) | JSON / objects |
| Evidence | tools/mission_canvas/evidence.py | Mission Canvas — Traceable Source-Attributed Evidence wrapper. | `--json` / (library) | JSON / objects |
| Narrative | tools/mission_canvas/narrative.py | Mission Canvas — Plain-English Mission-Ready Outputs wrapper. | `--json` / (library) | JSON / objects |
| Orchestrator | tools/mission_canvas/orchestrator.py | Mission Canvas — Autonomous AI Agent Orchestration wrapper. | `--json` / (library) | JSON / objects |
| Portfolio | tools/mission_canvas/portfolio.py | Mission Canvas — Portfolio Scaling & Optimization wrapper. | `--json` / (library) | JSON / objects |
| Twin | tools/mission_canvas/twin.py | Mission Canvas — Living Digital Twin wrapper. | `--json` / (library) | JSON / objects |
| Demo Runner | tools/ndc/demo_runner.py | NDC Demo Runner — executes 3 live scenarios for executive demos. | `--json` / (library) | JSON / objects |
| Iac Generator | tools/ndc/iac_generator.py | Network IaC Generator — NDC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Bus Subscriber | tools/observability_canvas/bus_subscriber.py | ODC Cross-Canvas Event Bus Subscriber. | `--json` / (library) | JSON / objects |
| Iac Generator | tools/odc/iac_generator.py | ODC IaC Generator — ODC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Iac Generator | tools/ohc/iac_generator.py | Ops Hub IaC Generator — OHC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Blueprint | tools/ontology/blueprint.py | Ontology Explorer Flask blueprint. | `--json` / (library) | JSON / objects |
| Studio Steps | tools/pipeline/studio_steps.py | PDC Studio workflow steps (scan/antipattern/iac) — reads live pipelines table, runs the live analysis engine, fails loud on missing design. Replaced the retired fabricated-result tools/pdc trio. | `--step <scan\|antipattern\|iac> --project-id <id> --json` | JSON w/ gate + artifacts |
| Bus Subscriber | tools/pipeline/bus_subscriber.py | PDC Cross-Canvas Event Bus Subscriber. | `--json` / (library) | JSON / objects |
| Enforce | tools/pki/enforce.py | mTLS Enforcement Middleware — reject inbound requests lacking a valid client certificate. | `--json` / (library) | JSON / objects |
| Generate | tools/pki/generate.py | PKI Certificate Management — generate CA, server, and client certificates. | `--json` / (library) | JSON / objects |
| Validate | tools/pki/validate.py | PKI Certificate Validation — verify chain, expiry, and mTLS configuration. | `--json` / (library) | JSON / objects |
| Trust Scorer | tools/provenance/trust_scorer.py | Citation Trust Score calculator for blockchain-provenance-backed citations. | `--json` / (library) | JSON / objects |
| Iac Generator | tools/qdc/iac_generator.py | Quality IaC Generator — QDC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Saml Auth | tools/saas/auth/saml_auth.py | ICDEV™ SaaS — SAML 2.0 Authentication for DoD Identity Providers + CAC/PIV. | `--json` / (library) | JSON / objects |
| Saml Routes | tools/saas/auth/saml_routes.py | ICDEV™ SaaS — SAML 2.0 Authentication Routes. | `--json` / (library) | JSON / objects |
| Demo Runner | tools/sdc/demo_runner.py | SDC Demo Runner — executes 3 live scenarios for executive/customer/prospect demos. | `--json` / (library) | JSON / objects |
| Iac Generator | tools/sdc/iac_generator.py | SDC IaC Generator — SDC Workflow Step 3. | `--json` / (library) | JSON / objects |
| Roi Calculator | tools/sdc/roi_calculator.py | SDC ROI Calculator — compute automation savings per security design. | `--json` / (library) | JSON / objects |
| Stig Checker | tools/sdc/stig_checker.py | STIG Checker — SDC Workflow Step 2. | `--json` / (library) | JSON / objects |
| Db Encryption | tools/security/db_encryption.py | SQLite Database Encryption at Rest — AES-256-GCM transparent file wrapper. | `--json` / (library) | JSON / objects |
| Blueprint | tools/showcase/blueprint.py | AI Canvas Demo Runner — Flask Blueprint. | `--json` / (library) | JSON / objects |
| Adversarial Validator | tools/strategos/adversarial_validator.py | Adversarial Data Validation Pipeline — bias, deepfake, and manipulation detection. | `--json` / (library) | JSON / objects |
| Ais Importer | tools/strategos/ais_importer.py | AIS CSV Importer — loads NOAA Marine Cadastre AIS data into sg_vessel_tracks. | `--json` / (library) | JSON / objects |
| Bda | tools/strategos/bda.py | BDA Module — Battle Damage Assessment (JP 3-60 / ATP 3-60). | `--json` / (library) | JSON / objects |
| Source Registry | tools/strategos/source_registry.py | Source Registry — STANAG 2022 Intelligence Source Grading. | `--json` / (library) | JSON / objects |
| Ansible Executor | tools/studio/executors/ansible_executor.py | Ansible Executor — Shared Workflow Step. | `--json` / (library) | JSON / objects |
| Aws Config Executor | tools/studio/executors/aws_config_executor.py | AWS Config Executor — Shared Workflow Executor (canvas-agnostic). | `--json` / (library) | JSON / objects |
| MCP Executor | tools/studio/executors/mcp_executor.py | Generic MCP tool executor — dispatches any `tool_registry.TOOL_REGISTRY` entry in-process (importlib module + handler), validating params against the entry's `input_schema` first. Unknown tool → exit 1 with closest matches. Results persist to run memory under `step:<step_id>` when `tools.studio.run_memory` (dwo-mem-01) is available. Authorization gating is dwo-mcp-02 — not yet a registered workflow node type. | `--tool <name> --params '<json>' [--run-id --step-id --project-id --json]` | JSON `{status, tool, category, handler, duration_ms, result, step_id, memory_key, memory_written}` |
| Migration Reporter | tools/studio/executors/migration_reporter.py | Migration Reporter — Shared Workflow Step. | `--json` / (library) | JSON / objects |
| Terraform Apply | tools/studio/executors/terraform_apply.py | Terraform Apply — Shared Workflow Executor (canvas-agnostic). | `--json` / (library) | JSON / objects |
| Terraform Destroy | tools/studio/executors/terraform_destroy.py | Terraform Destroy — Shared Workflow Executor (canvas-agnostic). | `--json` / (library) | JSON / objects |
| Terraform Plan | tools/studio/executors/terraform_plan.py | Terraform Plan Executor — Shared Workflow Step. | `--json` / (library) | JSON / objects |
| Validation Runner | tools/studio/executors/validation_runner.py | Validation Runner — Shared Workflow Step. | `--json` / (library) | JSON / objects |
| Ndc Topology | tools/studio/sim/ndc_topology.py | NDC topology builder — reuses live GNS3 projects from NDC discovery. | `--json` / (library) | JSON / objects |
| Ohc Topology | tools/studio/sim/ohc_topology.py | OHC topology builder — ops runbook automation and incident response nodes. | `--json` / (library) | JSON / objects |
| Context Builder | tools/studio/wne/context_builder.py | ICDEV™ Studio — Workflow Narrative Engine (WNE) Context Builder. | `--json` / (library) | JSON / objects |
| Narrative Generator | tools/studio/wne/narrative_generator.py | ICDEV™ Studio — Workflow Narrative Engine (WNE) Narrative Generator. | `--json` / (library) | JSON / objects |
| Blueprint | tools/supply_chain/blueprint.py | ICDEV™ Supply Chain Intelligence — Flask Blueprint. | `--json` / (library) | JSON / objects |
| Service | tools/threat_analysis/service.py | ICDEV™ Threat Analysis Service — baseline score validation. | `--json` / (library) | JSON / objects |
| Data Sync | tools/trading/agents/data_sync.py | Intelligence Data Sync — cross-agent data sharing bridge. | `--json` / (library) | JSON / objects |
| Portfolio Manager | tools/trading/agents/portfolio_manager.py | Portfolio Manager Agent (PMA) — unified intelligence synthesizer. | `--json` / (library) | JSON / objects |
| Delivery | tools/trading/alerts/delivery.py | Alert delivery — pushes fired alerts to configured out-of-band channels. | `--json` / (library) | JSON / objects |
| Tokens | tools/trading/leagues/tokens.py | League invitation tokens — Phase 6.4.5. | `--json` / (library) | JSON / objects |
| Blueprint | tools/trading/ta/blueprint.py | TA Patterns Blueprint — REST endpoint for chart pattern detection. | `--json` / (library) | JSON / objects |
