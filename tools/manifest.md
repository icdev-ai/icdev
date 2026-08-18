# Tools Manifest

> Master list of all tools. Check here before writing a new script.
> Split into shards by topic (2026-04-14). Original: `tools/manifest.md.bak`.

## Index

- [Standalone Agent Runtime (SAG)](manifest/standalone-agent-runtime.md)
- [LLM Chain Orchestration (CoT / CoD)](manifest/llm-chain-orchestration.md)
- [AIS Vessel Data Importer](manifest/ais-importer.md)
- [Network Design Canvas + IQE (ICDEV Query Engine)](manifest/network-iqe.md)
- [Memory System](manifest/memory-system.md)
- [Database](manifest/database.md)
- [Resilience (D146-D149)](manifest/resilience.md)
- [Compatibility Utilities (D145)](manifest/compatibility-utilities.md)
- [Audit Trail](manifest/audit-trail.md)
- [MCP Servers](manifest/mcp-servers.md)
- [Innovation Engine (Phase 35 — D199-D208)](manifest/innovation-engine.md)
- [A2A Protocol](manifest/a2a-protocol.md)
- [Project Management](manifest/project-management.md)
- [DX Companion — Universal AI Coding Tool Support (D194-D198)](manifest/dx-companion-universal-ai-coding-tool-support.md)
- [SDK](manifest/sdk.md)
- [CI/CD Pipeline](manifest/ci-cd-pipeline.md)
- [Compliance Engine](manifest/compliance-engine.md)
- [FIPS 199/200 Security Categorization](manifest/fips-199-200-security-categorization.md)
- [Universal Compliance Platform (Phase 23)](manifest/universal-compliance-platform.md)
- [CSSP Compliance (DI 8530.01)](manifest/cssp-compliance.md)
- [Xacta 360 Integration](manifest/xacta-360-integration.md)
- [Secure by Design (CISA SbD)](manifest/secure-by-design.md)
- [IV&V (IEEE 1012)](manifest/iv-v.md)
- [Multi-Framework Compliance (Phase 17)](manifest/multi-framework-compliance.md)
- [eMASS Integration](manifest/emass-integration.md)
- [Builder (TDD)](manifest/builder.md)
- [Security Scanning](manifest/security-scanning.md)
- [Analyzer / Responder Contract (ANZ)](manifest/analyzer-contract.md)
- [Showcase](manifest/showcase.md)
- [Deploy](manifest/deploy.md)
- [Infrastructure](manifest/infrastructure.md)
- [Knowledge & Self-Healing](manifest/knowledge-self-healing.md)
- [Monitoring](manifest/monitoring.md)
- [Dashboard](manifest/dashboard.md)
- [Enterprise-Configurable Platform](manifest/enterprise-configurable-platform.md)
- [CLI Output Formatting](manifest/cli-output-formatting.md)
- [Browser Automation (Selenium Driver Manager)](manifest/browser.md)
- [Testing Framework (Adapted from ADW)](manifest/testing-framework.md)
- [CI/CD Integration (GitHub + GitLab)](manifest/ci-cd-integration.md)
- [Maintenance Audit](manifest/maintenance-audit.md)
- [MBSE Integration (Phase 18)](manifest/mbse-integration.md)
- [Application Modernization (Phase 19 — 7Rs Migration)](manifest/application-modernization.md)
- [Requirements Intake (RICOAS Phase 1)](manifest/requirements-intake.md)
- [Spec-Kit Patterns (D156–D161)](manifest/spec-kit-patterns.md)
- [ATO Boundary Impact (RICOAS Phase 2)](manifest/ato-boundary-impact.md)
- [Supply Chain Intelligence (RICOAS Phase 2)](manifest/supply-chain-intelligence.md)
- [Digital Program Twin Simulation (RICOAS Phase 3)](manifest/digital-program-twin-simulation.md)
- [TTX Tabletop Exercise (GameDay) Engine](manifest/ttx-tabletop-exercise-engine.md)
- [External Integration (RICOAS Phase 4)](manifest/external-integration.md)
- [SharePoint Integration (Phase E / P4.1)](manifest/sharepoint.md)
- [Agent Execution Framework (Phase 39)](manifest/agent-execution-framework.md)
- [LLM Provider Abstraction (Vendor-Agnostic)](manifest/llm-provider-abstraction.md)
- [ICDEV Cortex (Unified AI Facade)](manifest/cortex.md)
- [BI Dashboard Canvas](manifest/bi-dashboard.md)
- [Bedrock Client (Opus 4.6 Multi-Agent — Phase A)](manifest/bedrock-client.md)
- [Multi-Agent Orchestration (Opus 4.6 Multi-Agent — Phase B)](manifest/multi-agent-orchestration.md)
- [Agent Collaboration (Opus 4.6 Multi-Agent — Phase C)](manifest/agent-collaboration.md)
- [Observability Hooks (Phase 39)](manifest/observability-hooks.md)
- [NLQ Compliance Queries (Phase 40)](manifest/nlq-compliance-queries.md)
- [Git Worktree Parallel CI/CD (Phase 41)](manifest/git-worktree-parallel-ci-cd.md)
- [Framework Planning Commands (Phase 42)](manifest/framework-planning-commands.md)
- [Billing (Metering + Stripe)](manifest/billing.md)
- [SaaS Multi-Tenancy (Phase 21)](manifest/saas-multi-tenancy.md)
- [Marketplace (Phase 22)](manifest/marketplace.md)
- [DevSecOps & Zero Trust Architecture (Phase 24-25)](manifest/devsecops-zero-trust-architecture.md)
- [DoD MOSA — Modular Open Systems Approach (Phase 26)](manifest/dod-mosa-modular-open-systems-approach.md)
- [Dashboard Auth, Activity Feed, BYOK & Usage Tracking (Phase 30)](manifest/dashboard-auth-activity-feed-byok-usage-tracking.md)
- [Modular Installation (Phase 33)](manifest/modular-installation.md)
- [AI Security (Phase 37 — MITRE ATLAS, D209-D231)](manifest/ai-security.md)
- [Evolutionary Intelligence (Phase 36 — D209-D214)](manifest/evolutionary-intelligence.md)
- [Bayesian Teaching Intelligence (D-BT-1 through D-BT-6)](manifest/bayesian-teaching-intelligence.md)
- [Engineering Review Board (Phase 67, D-RB-1 through D-RB-7)](manifest/engineering-review-board.md)
- [Workflow Discipline Engine (Phase 66, D-WF-1 through D-WF-7)](manifest/workflow-discipline-engine.md)
- [Cloud-Agnostic Architecture (Phase 38 — D223-D231)](manifest/cloud-agnostic-architecture.md)
- [Cross-Language Translation (Phase 43 — D242-D256)](manifest/cross-language-translation.md)
- [Remote Command Gateway (Phase 28 — D133-D140)](manifest/remote-command-gateway.md)
- [Innovation Adaptation (Phase 44 — D257-D279)](manifest/innovation-adaptation.md)
- [Observability, Traceability & Explainable AI (Phase 46)](manifest/observability-traceability-explainable-ai.md)
- [Ontology](manifest/ontology.md)
- [Provenance](manifest/provenance.md)
- [Blockchain / GovChain (D-GC-1 through D-GC-11)](manifest/blockchain.md)
- [Code Intelligence (Phase 52 — D331-D337)](manifest/code-intelligence.md)
- [AI Governance Integration (Phase 50)](manifest/ai-governance-integration.md)
- [FedRAMP 20x KSI + OWASP ASI (Phase 53)](manifest/fedramp-20x-ksi-owasp-asi.md)
- [SWFT/SLSA + Cross-Phase Orchestration (Phase 54)](manifest/swft-slsa-cross-phase-orchestration.md)
- [A2A v0.3 + MCP OAuth (Phase 55)](manifest/a2a-v0-3-mcp-oauth.md)
- [Compliance Evidence Auto-Collection + Lineage (Phase 56)](manifest/compliance-evidence-auto-collection-lineage.md)
- [EU AI Act + Platform One (Phase 57)](manifest/eu-ai-act-platform-one.md)
- [GovCon Intelligence (Phase 59)](manifest/govcon-intelligence.md)
- [Industry Research Engine (Phase 63)](manifest/industry-research-engine.md)
- [ANVIL Critique Phase (Phase 61 — Feature 3)](manifest/anvil-critique-phase.md)
- [Universal RAG Subsystem (Phase 64)](manifest/universal-rag-subsystem.md)
- [File Sync (`tools/filesync/`)](manifest/file-sync.md)
- [Safety Hooks](manifest/safety-hooks.md)
- [Daemon Infrastructure — Shared Base Classes](manifest/daemon-infrastructure-shared-base-classes.md)
- [Scheduler](manifest/scheduler.md)
- [Genesis v2.0 — Autonomous Research Lab](manifest/genesis-v2-0-autonomous-research-lab.md)
- [Proposal Genesis — Autonomous Proposal Intelligence](manifest/proposal-genesis-autonomous-proposal-intelligence.md)
- [AppForge — Autonomous Vertical App Builder](manifest/appforge-autonomous-vertical-app-builder.md)
- [Internal Awareness Engine (Phase 1a-1g)](manifest/internal-awareness-engine.md)
- [Code Intelligence & Verification](manifest/code-intelligence-verification.md)
- [AI Transparency & Accountability (Phase 48-49)](manifest/ai-transparency-accountability.md)
- [AI Compliance Assessors (Phase 48-49 — Additional)](manifest/ai-compliance-assessors.md)
- [Creative Engine (Phase 58)](manifest/creative-engine.md)
- [DataBridge](manifest/databridge.md)
- [Fine-Tuning (Phase 64 Extension)](manifest/fine-tuning.md)
- [Genesis Launcher](manifest/genesis-launcher.md)
- [Harness Engineering (Additional)](manifest/harness-engineering.md)
- [Knowledge Graph & GraphRAG](manifest/knowledge-graph-graphrag.md)
- [LLM Providers (Additional)](manifest/llm-providers.md)
- [LLM Provider SDK](manifest/llm-provider-sdk.md)
- [Playground](manifest/playground.md)
- [Proposal Genesis CRM](manifest/proposal-genesis-crm.md)
- [RAG Subsystem (Additional)](manifest/rag-subsystem.md)
- [Codebase Assistant (Phase 69)](manifest/codebase-assistant.md)
- [Requirements (Additional)](manifest/requirements.md)
- [Research Engine (Additional)](manifest/research-engine.md)
- [Security (Additional)](manifest/security.md)
- [Linting](manifest/linting.md)
- [Testing (Additional)](manifest/testing.md)
- [WriteGuard — Writing Quality Analysis](manifest/writeguard-writing-quality-analysis.md)
- [Slides Deck Generator](manifest/slides.md)
- [Pulse AI Blog Engine](manifest/pulse-ai-blog-engine.md)
- [Evaluation & Red Teaming (Phase 65)](manifest/evaluation-red-teaming.md)
- [Bayesian Autoresearch (Phase 67, D-AR-1 through D-AR-10)](manifest/bayesian-autoresearch.md)
- [SRE — Site Reliability Engineering](manifest/sre-site-reliability-engineering.md)
- [Redaction & Data Protection (Phase 70 — D-RDT-1)](manifest/redaction-data-protection.md)
- [ICDEV™ Studio — Low-Code/No-Code Platform (Phase 72 — D361-D366)](manifest/icdev-studio-low-code-no-code-platform.md)
- [Autonomy Engine](manifest/autonomy-engine.md)
- [Autoresearch (Additional)](manifest/autoresearch.md)
- [Dashboard API (Additional)](manifest/dashboard-api.md)
- [Extensions (Additional)](manifest/extensions.md)
- [Genesis (Additional)](manifest/genesis.md)
- [GovCon (Additional)](manifest/govcon.md)
- [Harness (Additional)](manifest/harness.md)
- [Notifications](manifest/notifications.md)
- [Proposal Genesis (Additional)](manifest/proposal-genesis.md)
- [RAG (Additional)](manifest/rag.md)
- [Review Board (Additional)](manifest/review-board.md)
- [SaaS (Additional)](manifest/saas.md)
- [Scout](manifest/scout.md)
- [FathomDesk Trading Engine](manifest/fathomdesk-trading-engine.md)
- [IL5 Data Ingestion & SLA Enforcement](manifest/il5-data-ingestion.md)
- [Auto-Registered (Coherence Fix)](manifest/auto-registered.md)
- [Air-Gap Mode (OPT-51/OPT-61)](manifest/air-gap-mode.md)
- [AI/ML Model Canvas (AIMC)](manifest/aiml-canvas.md)
- [Design Canvases (7-Canvas Suite)](manifest/design-canvases.md)
- [Migration Canvas](manifest/migration-canvas.md)
- [Canvas Auto-Remediation](manifest/canvas-auto-remediation.md)
- [BDC cATO Twin (Phase BDC-1)](manifest/bdc-cato-twin.md)
- [IDC IaC Twin (Phase IDC-1)](manifest/idc-twin.md)
- [Twin Core — Cross-Canvas Digital-Twin Unification (TWX)](manifest/twin-core.md)
- [Agent Adapters (OPT-71)](manifest/agent-adapters.md)
- [Agent Governance — Detection (AGOV / DET)](manifest/agent-governance-detection.md)
- [Skill Invocation (OPT-41, 2026-04-12)](manifest/skill-invocation.md)
- [ANVIL Headless Commands (OPT-42, 2026-04-12)](manifest/anvil-headless-commands.md)
- [Dashboard UX Enhancements (OPT-68, 2026-04-12)](manifest/dashboard-ux-enhancements.md)
- [Manifest Gap Fill (2026-04-12)](manifest/manifest-gap-fill.md)
- [AISG — AI Strategy Guide Tools](manifest/aisg.md)
- [IQE — Internal Query Engine](manifest/iqe-query-engine.md)
- [IDP — Scorecard-as-Code (idp-score)](manifest/idp-scorecards.md)
- [Kanban System](manifest/kanban.md)
- [Regulatory Foresight Engine (D352 — pint-regfore)](manifest/regulatory-foresight-engine.md)
- [Voice-of-Customer (VOC) Signal Capture (pint-voc)](manifest/voc.md)
- [Strategos — DIB Supply Chain & Strategy Intelligence](manifest/strategos.md)
- [Strategos Chat — Floating Analyst Chat Panel](manifest/strategos-chat.md)
- [TTX Engine — Tabletop Exercise (AI GameDay)](manifest/ttx.md)
- [Unclassified (auto-added)](manifest/unclassified.md)
- [DDC Data Science — Explore, Query Sandbox, Quality Rules](manifest/ddc-data-science.md)
- [Data Mesh (dm-*)](manifest/data-mesh.md) — Data Mesh: Domains, Products, Contracts, Governance, CSP (9 tables, 6 modules, 6 pages)
- [System Graph — Federated Sigma.js Graph](manifest/system-graph.md)
- [IC IE Data Fabric — Multi-Agency Data Sharing](manifest/ic-ie-data-fabric.md)
- [AI Augmentation Canvas (AAC)](manifest/ai-augmentation-canvas.md)
- [Document Modernization Engine (docmod)](manifest/doc-modernization.md)
- [FORGE Academy](manifest/forge-academy.md) — learner platform: server-authoritative grading, XP provenance ledger, tier gating (`apps/forge_academy/`)
- [GeoSIGINT Indo-Pacific Analyzer](manifest/geosigint.md) — 6 OSINT analyzers, 7 pages, 23 APIs (`apps/geosigint/`)
- [Entity Currency Store](manifest/entity-currency.md) — one domain-agnostic "is it still current", source-agnostic, disagreement preserved (`tools/currency/`)


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pdf Import | tools\network\pdf_import.py | Auto-registered: network/pdf_import.py | --json | JSON |
| Snapshot Builder | tools\trading\market_intel\snapshot_builder.py | Auto-registered: market_intel/snapshot_builder.py | --json | JSON |
| Schedule Enterprise Frontend Plan | tools\scripts\schedule_enterprise_frontend_plan.py | Auto-registered: scripts/schedule_enterprise_frontend_plan.py | --json | JSON |
| Schedule Fathomdesk News Plan | tools\scripts\schedule_fathomdesk_news_plan.py | Auto-registered: scripts/schedule_fathomdesk_news_plan.py | --json | JSON |
| Wheel Vendor | tools\airgap\wheel_vendor.py | Auto-registered: airgap/wheel_vendor.py | --json | JSON |
| Self Debug | tools\workflow\self_debug.py | Auto-registered: workflow/self_debug.py | --json | JSON |
| Rss Ingestor | tools\trading\news\rss_ingestor.py | Auto-registered: news/rss_ingestor.py | --json | JSON |
| Scenario Matcher | tools\trading\news\scenario_matcher.py | Auto-registered: news/scenario_matcher.py | --json | JSON |
| News Reasoner | tools\trading\news\news_reasoner.py | Auto-registered: news/news_reasoner.py | --json | JSON |
| Crisis Fingerprints | tools\trading\market_intel\crisis_fingerprints.py | Auto-registered: market_intel/crisis_fingerprints.py | --json | JSON |
| Cross Asset Divergence | tools\trading\market_intel\cross_asset_divergence.py | Auto-registered: market_intel/cross_asset_divergence.py | --json | JSON |
| Lens News Intelligence | tools\trading\oracle\lens_news_intelligence.py | Auto-registered: oracle/lens_news_intelligence.py | --json | JSON |
| Lens Portfolio Stress | tools\trading\oracle\lens_portfolio_stress.py | Auto-registered: oracle/lens_portfolio_stress.py | --json | JSON |
| Lens Regime Trajectory | tools\trading\oracle\lens_regime_trajectory.py | Auto-registered: oracle/lens_regime_trajectory.py | --json | JSON |
| Lens Signal Convergence | tools\trading\oracle\lens_signal_convergence.py | Auto-registered: oracle/lens_signal_convergence.py | --json | JSON |
| Pulumi | tools\infra_canvas\emitters\pulumi.py | Auto-registered: emitters/pulumi.py | --json | JSON |
| Aws Rgt | tools\infra_canvas\importers\aws_rgt.py | Auto-registered: importers/aws_rgt.py | --json | JSON |
| Pulumi State | tools\infra_canvas\importers\pulumi_state.py | Auto-registered: importers/pulumi_state.py | --json | JSON |
| Tf State | tools\infra_canvas\importers\tf_state.py | Auto-registered: importers/tf_state.py | --json | JSON |
| Mitre Loader | tools\observability_canvas\mitre_loader.py | Auto-registered: observability_canvas/mitre_loader.py | --json | JSON |
| Premerge Runner | tools\pipeline\premerge_runner.py | Auto-registered: pipeline/premerge_runner.py | --json | JSON |
| Path Enumerator | tools\security_canvas\path_enumerator.py | Auto-registered: security_canvas/path_enumerator.py | --json | JSON |
| Attackpath | tools\security_canvas\attackpath.py | Auto-registered: security_canvas/attackpath.py | --json | JSON |
| Schedule Phase 7 6 Ai Options Plan | tools\scripts\schedule_phase_7_6_ai_options_plan.py | Auto-registered: scripts/schedule_phase_7_6_ai_options_plan.py | --json | JSON |
| Intent Parser | tools\trading\options\intent_parser.py | Auto-registered: options/intent_parser.py | --json | JSON |
| Canvas Ask | tools\knowledge_graph\canvas_ask.py | Auto-registered: knowledge_graph/canvas_ask.py | --json | JSON |
| Proposal Builder | tools\trading\options\proposal_builder.py | Auto-registered: options/proposal_builder.py | --json | JSON |
| Strategy Selector | tools\trading\options\strategy_selector.py | Auto-registered: options/strategy_selector.py | --json | JSON |
| Strike Picker | tools\trading\options\strike_picker.py | Auto-registered: options/strike_picker.py | --json | JSON |
| Coach Engine | tools\trading\options\coach_engine.py | Auto-registered: options/coach_engine.py | --json | JSON |
| Coach Llm | tools\trading\options\coach_llm.py | Auto-registered: options/coach_llm.py | --json | JSON |
| Schedule Phase 7 7 Prob Compare Plan | tools\scripts\schedule_phase_7_7_prob_compare_plan.py | Auto-registered: scripts/schedule_phase_7_7_prob_compare_plan.py | --json | JSON |
| Schedule Phase 7 8 Greeks Share Plan | tools\scripts\schedule_phase_7_8_greeks_share_plan.py | Auto-registered: scripts/schedule_phase_7_8_greeks_share_plan.py | --json | JSON |
| Portfolio Greeks | tools\trading\options\portfolio_greeks.py | Auto-registered: options/portfolio_greeks.py | --json | JSON |
| Schedule Phase 7 10 Traps Plan | tools\scripts\schedule_phase_7_10_traps_plan.py | Auto-registered: scripts/schedule_phase_7_10_traps_plan.py | --json | JSON |
| Schedule Phase 7 11 News 2 Plan | tools\scripts\schedule_phase_7_11_news_2_plan.py | Auto-registered: scripts/schedule_phase_7_11_news_2_plan.py | --json | JSON |
| Schedule Phase 7 9 Ta Foundation Plan | tools\scripts\schedule_phase_7_9_ta_foundation_plan.py | Auto-registered: scripts/schedule_phase_7_9_ta_foundation_plan.py | --json | JSON |
| Swings | tools\trading\ta\swings.py | Auto-registered: ta/swings.py | --json | JSON |
| Volume Profile | tools\trading\ta\volume_profile.py | Auto-registered: ta/volume_profile.py | --json | JSON |
| Triple | tools\trading\ta\patterns\triple.py | Auto-registered: patterns/triple.py | --json | JSON |
| Wedge | tools\trading\ta\patterns\wedge.py | Auto-registered: patterns/wedge.py | --json | JSON |
| Support Resistance | tools\trading\ta\support_resistance.py | Auto-registered: ta/support_resistance.py | --json | JSON |
| Combo Analyzer | tools\trading\options\combo_analyzer.py | Auto-registered: options/combo_analyzer.py | --json | JSON |
| Auto Trade Options | tools\trading\options\auto_trade_options.py | Auto-registered: options/auto_trade_options.py | --json | JSON |
| FathomDesk Trap Scenarios | tools\genesis\reflexes\fathomdesk_trap_scenarios.py | Auto-registered: reflexes/fathomdesk_trap_scenarios.py | --json | JSON |
| Git Utils | tools\workflow\git_utils.py | Auto-registered: workflow/git_utils.py | --json | JSON |
| Twin Chat | tools\twin_chat.py | Auto-registered: tools/twin_chat.py | --json | JSON |
| Bond Etf Data | tools\trading\data\bond_etf_data.py | Auto-registered: data/bond_etf_data.py | --json | JSON |
| Seed Dt Competitors | tools\creative\seed_dt_competitors.py | Auto-registered: creative/seed_dt_competitors.py | --json | JSON |
| Cross Asset Rotation | tools\trading\market_intel\cross_asset_rotation.py | Auto-registered: market_intel/cross_asset_rotation.py | --json | JSON |
| Nl To Iqe | tools\iqe\nl_to_iqe.py | Auto-registered: iqe/nl_to_iqe.py | --json | JSON |
| Traffic Flow | tools\network\traffic_flow.py | Auto-registered: network/traffic_flow.py | --json | JSON |
| Narrative Generator | tools\network\narrative_generator.py | TFW Narrative Generator — wraps `TrafficFlowEngine` walkthrough steps with per-persona LLM narratives (seceng, neteng, cloudarch, compofficer, appdev, missionowner, ciso) and deterministic `detail_json` enrichment (CSP detection, multi-CSP inter-hop, classification overlay, NIST 800-53/FedRAMP control pre-population). Public API: `generate_for_persona(step, node, persona_id, flow, classification, prev_node, llm_client, use_llm) -> {"narrative": str, "detail_json": dict}` and `generate_all(flow_id, conn, personas, classification, use_llm) -> {"steps": [...], "summary": {...}}`. Falls back to `NARRATIVE_TEMPLATES` then generic text when LLM unavailable. CLI: `--flow-id <uuid> [--classification NIPR\|IL4\|IL5\|IL6\|SIPR] [--personas <id>...] [--no-llm] --json`. Full API reference: [manifest/network-iqe.md](manifest/network-iqe.md#tfw-narrative-generator--detailed-analysis) | `--flow-id <uuid> [--classification NIPR] [--personas seceng compofficer] [--no-llm] --json` | JSON `{steps, summary}` |
| Canvas Registry | tools\canvas\canvas_registry.py | Auto-registered: canvas/canvas_registry.py | --json | JSON |
| Tfw Chat Schema | tools\simulation\tfw_chat_schema.py | Auto-registered: simulation/tfw_chat_schema.py | --json | JSON |
| Mermaid Parser | tools\simulation\parsers\mermaid_parser.py | Auto-registered: parsers/mermaid_parser.py | --json | JSON |
| Genesis Daemon | tools\trading\options\genesis_daemon.py | Auto-registered: options/genesis_daemon.py | --json | JSON |
| Oracle Engine | tools\trading\options\oracle_engine.py | Auto-registered: options/oracle_engine.py | --json | JSON |
| Tfw Chat Agent | tools\simulation\tfw_chat_agent.py | Auto-registered: simulation/tfw_chat_agent.py | --json | JSON |
| Oracle Notify | tools\trading\options\oracle_notify.py | Auto-registered: options/oracle_notify.py | --json | JSON |
| Dfd Generator | tools\simulation\artifacts\dfd_generator.py | Auto-registered: artifacts/dfd_generator.py | --json | JSON |
| Event Bus | tools\canvas\event_bus.py | Auto-registered: canvas/event_bus.py | --json | JSON |
| Broker Adapter | tools\fathomdesk\broker_adapter.py | Auto-registered: fathomdesk/broker_adapter.py | --json | JSON |
| Fathomdesk Trap Sweep | tools\genesis\reflexes\fathomdesk_trap_sweep.py | Auto-registered: reflexes/fathomdesk_trap_sweep.py | --json | JSON |
| Isa Expiry | tools\boundary_canvas\isa_expiry.py | Auto-registered: boundary_canvas/isa_expiry.py | --json | JSON |
| Bdc Isa Expiry | tools\genesis\reflexes\bdc_isa_expiry.py | Auto-registered: reflexes/bdc_isa_expiry.py | --json | JSON |
| Mitre Ingestor | tools\observability\mitre_ingestor.py | Auto-registered: observability/mitre_ingestor.py | --json | JSON |
| Bus Subscriber | tools\security_canvas\bus_subscriber.py | Auto-registered: security_canvas/bus_subscriber.py | --json | JSON |
| Network Migration | tools\migration_canvas\network_migration.py | Auto-registered: migration_canvas/network_migration.py | --json | JSON |
| Goal Manager | tools\migration_intelligence\goal_manager.py | Auto-registered: migration_intelligence/goal_manager.py | --json | JSON |
| Migration Manager | tools\migration_intelligence\migration_manager.py | Auto-registered: migration_intelligence/migration_manager.py | --json | JSON |
| Opportunity Scanner | tools\migration_intelligence\opportunity_scanner.py | Auto-registered: migration_intelligence/opportunity_scanner.py | --json | JSON |
| Strategy Generator | tools\migration_intelligence\strategy_generator.py | Auto-registered: migration_intelligence/strategy_generator.py | --json | JSON |
| Migration Intel | tools\genesis\reflexes\migration_intel.py | Auto-registered: reflexes/migration_intel.py | --json | JSON |
| Fathomdesk Openbb Refresh | tools\genesis\reflexes\fathomdesk_openbb_refresh.py | Auto-registered: reflexes/fathomdesk_openbb_refresh.py | --json | JSON |
| Data Gateway | tools\fathomdesk\data_gateway.py | Auto-registered: fathomdesk/data_gateway.py | --json | JSON |
| Reflex Observer | tools\monitoring\reflex_observer.py | Auto-registered: monitoring/reflex_observer.py | --json | JSON |
| Backtester | tools\fathomdesk\backtester.py | Auto-registered: fathomdesk/backtester.py | --json | JSON |
| Setup Wizard | tools\rag\setup_wizard.py | Auto-registered: rag/setup_wizard.py | --json | JSON |
| Pir Manager | tools\intelligence\pir_manager.py | Auto-registered: intelligence/pir_manager.py | --json | JSON |
| Baseline Importer | tools\sg\baseline_importer.py | Auto-registered: sg/baseline_importer.py | --json | JSON |
| War Endurance | tools\simulation\war_endurance.py | Auto-registered: simulation/war_endurance.py | --json | JSON |
| Cta Positioning | tools\trading\market_intel\cta_positioning.py | Auto-registered: market_intel/cta_positioning.py | --json | JSON |
| Reflex Registry | tools\genesis\reflex_registry.py | Auto-registered: genesis/reflex_registry.py | --json | JSON |
| Interdiction Ranker | tools\strategos\interdiction_ranker.py | Auto-registered: strategos/interdiction_ranker.py | --json | JSON |
| Temporal Correlator | tools\strategos\temporal_correlator.py | Auto-registered: strategos/temporal_correlator.py | --json | JSON |
| OSINT Harvester | tools\genesis\reflexes\strategos\osint_harvester.py | STRATEGOS reflex — background OSINT collection every 4h from RSS/ACLED/Telegram/file-inbox into sg_raw_signals; max 200 signals/run, sha256 dedup | --json | JSON {success, metric_value, details} |
| Brief Generator | tools\intelligence\brief_generator.py | Auto-registered: intelligence/brief_generator.py | --json | JSON |
| Signal Scout | tools\genesis\reflexes\strategos\signal_scout.py | Auto-registered: strategos/signal_scout.py | --json | JSON |
| Reverse Cascade Inference | tools\strategos\reverse_cascade_inference.py | Auto-registered: strategos/reverse_cascade_inference.py | --json | JSON |
| Signal Pricer | tools\intelligence\signal_pricer.py | Auto-registered: intelligence/signal_pricer.py | --json | JSON |
| Ais Connector | tools\databridge\connectors\ais_connector.py | Auto-registered: connectors/ais_connector.py | --json | JSON |
| Osint Prestage | tools\strategos\osint_prestage.py | Auto-registered: strategos/osint_prestage.py | --json | JSON |
| Put Call Sentiment | tools\trading\analysis\confluence_pillars\put_call_sentiment.py | Auto-registered: confluence_pillars/put_call_sentiment.py | --json | JSON |
| Fathomdesk Pc Ratio | tools\genesis\reflexes\fathomdesk_pc_ratio.py | Auto-registered: reflexes/fathomdesk_pc_ratio.py | --json | JSON |
| Signal Tuner | tools\fathomdesk\signal_tuner.py | Auto-registered: fathomdesk/signal_tuner.py | --json | JSON |
| Signal Generator | tools\fathomdesk\signal_generator.py | FathomDesk threshold-gated signal filter — loads `args/signal_thresholds.yaml` (min_confidence, min_score, max_signals, per-category biases) and returns only signals that pass all gates. Public API: `generate(signals, thresholds=None) -> list[dict]`, `load_thresholds(path=None) -> dict`. | `generate(signals=[...])` | filtered signal list |
| Ew Monitor | tools\strategos\ew_monitor.py | Auto-registered: strategos/ew_monitor.py | --json | JSON |
| Iw Engine | tools\strategos\iw_engine.py | Auto-registered: strategos/iw_engine.py | --json | JSON |
| Ooda | tools\strategos\ooda.py | Auto-registered: strategos/ooda.py | --json | JSON |
| Dib Mapper | tools\strategos\dib_mapper.py | Auto-registered: strategos/dib_mapper.py | --json | JSON |
| Information Scorer | tools\intelligence\war_readiness\information_scorer.py | Auto-registered: war_readiness/information_scorer.py | --json | JSON |
| Pattern Learner | tools\genesis\reflexes\strategos\pattern_learner.py | Auto-registered: strategos/pattern_learner.py | --json | JSON |
| Red Cell | tools\genesis\reflexes\strategos\red_cell.py | Auto-registered: strategos/red_cell.py | --json | JSON |
| Engine Registry | tools\product_intel\engine_registry.py | Auto-registered: product_intel/engine_registry.py | --json | JSON |
| Transcript Ingestor | tools\voc\transcript_ingestor.py | Auto-registered: voc/transcript_ingestor.py | --json | JSON |
| Voc Engine | tools\voc\voc_engine.py | Auto-registered: voc/voc_engine.py | --json | JSON |
| Win Loss Engine | tools\win_loss\win_loss_engine.py | Auto-registered: win_loss/win_loss_engine.py | --json | JSON |
| Doctrine Corpus | tools\strategos\doctrine_corpus.py | Auto-registered: strategos/doctrine_corpus.py | --json | JSON |
| War Council | tools\strategos\war_council.py | Auto-registered: strategos/war_council.py | --json | JSON |
| War Council Generator | tools\strategos\war_council_generator.py | Auto-registered: strategos/war_council_generator.py | --json | JSON |
| Rare Earth Cascade | tools\supply_chain\rare_earth_cascade.py | Auto-registered: supply_chain/rare_earth_cascade.py | --json | JSON |
| Semiconductor Chain | tools\supply_chain\semiconductor_chain.py | Auto-registered: supply_chain/semiconductor_chain.py | --json | JSON |
| Verify Manifest | tools\verify_manifest.py | Auto-registered: tools/verify_manifest.py | --json | JSON |
| Check Manifest Coverage | tools\check_manifest_coverage.py | Auto-registered: tools/check_manifest_coverage.py | --json | JSON |
| Research Bridge | tools\strategos\research_bridge.py | Auto-registered: strategos/research_bridge.py | --json | JSON |
| Darkweb | tools\strategos\darkweb.py | Auto-registered: strategos/darkweb.py | --json | JSON |
| Darkweb Monitor | tools\strategos\darkweb_monitor.py | Auto-registered: strategos/darkweb_monitor.py | --json | JSON |
| Seed Fdt Amendment | tools\kanban\seed_fdt_amendment.py | Auto-registered: kanban/seed_fdt_amendment.py | --json | JSON |
| Seed Fdt Tradingagents | tools\kanban\seed_fdt_tradingagents.py | Auto-registered: kanban/seed_fdt_tradingagents.py | --json | JSON |
| Eo Importer | tools\strategos\eo_importer.py | Auto-registered: strategos/eo_importer.py | --json | JSON |
| Rf Attribution | tools\strategos\rf_attribution.py | Auto-registered: strategos/rf_attribution.py | --json | JSON |
| Socmint | tools\genesis\reflexes\socmint.py | Auto-registered: reflexes/socmint.py | --json | JSON |
| Analyst Panel | tools\fathomdesk\analyst_panel.py | Auto-registered: fathomdesk/analyst_panel.py | --json | JSON |
| Base Analyst | tools\fathomdesk\agents\base_analyst.py | Auto-registered: agents/base_analyst.py | --json | JSON |
| Research Manager | tools\fathomdesk\agents\research_manager.py | Auto-registered: agents/research_manager.py | --json | JSON |
| Adsb Importer | tools\strategos\adsb_importer.py | Auto-registered: strategos/adsb_importer.py | --json | JSON |
| Ground Vehicle Importer | tools\strategos\ground_vehicle_importer.py | Auto-registered: strategos/ground_vehicle_importer.py | --json | JSON |
| Tle Importer | tools\strategos\tle_importer.py | Auto-registered: strategos/tle_importer.py | --json | JSON |
| Uas Importer | tools\strategos\uas_importer.py | Auto-registered: strategos/uas_importer.py | --json | JSON |
| Wargame Advisor | tools\strategos\wargame_advisor.py | Auto-registered: strategos/wargame_advisor.py | --json | JSON |
| Wargame Orbat | tools\strategos\wargame_orbat.py | Auto-registered: strategos/wargame_orbat.py | --json | JSON |
| Wargame Turn Engine | tools\strategos\wargame_turn_engine.py | Auto-registered: strategos/wargame_turn_engine.py | --json | JSON |
| Template Linter | tools\studio\template_linter.py | Auto-registered: studio/template_linter.py | --json | JSON |
| Seed Icdev Templates | tools\workflow_hitl\seed_icdev_templates.py | Auto-registered: workflow_hitl/seed_icdev_templates.py | --json | JSON |
| Explain Translator | tools\aisg\explain_translator.py | Auto-registered: aisg/explain_translator.py | --json | JSON |
| Roi Tracker | tools\aisg\roi_tracker.py | Auto-registered: aisg/roi_tracker.py | --json | JSON |
| Export Pdf | tools\agentic_ai_canvas\export_pdf.py | Auto-registered: agentic_ai_canvas/export_pdf.py | --json | JSON |
| Version Diff | tools\agentic_ai_canvas\version_diff.py | Auto-registered: agentic_ai_canvas/version_diff.py | --json | JSON |
| Autotune | tools\aisg\autotune.py | Auto-registered: aisg/autotune.py | --json | JSON |
| Compliance View | tools\aisg\compliance_view.py | Auto-registered: aisg/compliance_view.py | --json | JSON |
| Executive View | tools\aisg\executive_view.py | Auto-registered: aisg/executive_view.py | --json | JSON |
| Knowledge Handoff | tools\aisg\knowledge_handoff.py | Auto-registered: aisg/knowledge_handoff.py | --json | JSON |
| Learning Paths | tools\aisg\learning_paths.py | Auto-registered: aisg/learning_paths.py | --json | JSON |
| Pattern Registry | tools\aisg\pattern_registry.py | Auto-registered: aisg/pattern_registry.py | --json | JSON |
| Pm View | tools\aisg\pm_view.py | Auto-registered: aisg/pm_view.py | --json | JSON |
| Skills Tracker | tools\aisg\skills_tracker.py | Auto-registered: aisg/skills_tracker.py | --json | JSON |
| Sprint Seeder | tools\aisg\sprint_seeder.py | Auto-registered: aisg/sprint_seeder.py | --json | JSON |
| Seed Aadc Enhancement | tools\kanban\seed_aadc_enhancement.py | Auto-registered: kanban/seed_aadc_enhancement.py | --json | JSON |
| Seed Fde Epic E | tools\scripts\seed_fde_epic_e.py | Auto-registered: scripts/seed_fde_epic_e.py | --json | JSON |
| Cisa Kev Importer | tools\strategos\cisa_kev_importer.py | Auto-registered: strategos/cisa_kev_importer.py | --json | JSON |
| Wargame Exporter | tools\strategos\wargame_exporter.py | Auto-registered: strategos/wargame_exporter.py | --json | JSON |
| Engine | tools\ttx\engine.py | TTX Engine — facade orchestrating all TTX subsystems: session lifecycle, inject dispatch, AI scoring, leaderboard, AAR. Entry point for all exercise operations via `TTXEngine()` instance methods. | TTXEngine() instance methods | dict / list[dict] |
| Aar Generator | tools\ttx\aar_generator.py | Auto-registered: ttx/aar_generator.py | --json | JSON |
| Ai Scorer | tools\ttx\ai_scorer.py | Auto-registered: ttx/ai_scorer.py | --json | JSON |
| Inject Dispatcher | tools\ttx\inject_dispatcher.py | Auto-registered: ttx/inject_dispatcher.py | --json | JSON |
| Persona Generator | tools\ttx\persona_generator.py | Auto-registered: ttx/persona_generator.py | --json | JSON |
| Scenario Loader | tools\ttx\scenario_loader.py | Auto-registered: ttx/scenario_loader.py | --json | JSON |
| Session Manager | tools\ttx\session_manager.py | TTX Engine — session lifecycle CRUD. Create/get/list/update ttx_sessions rows; generates a random join_code; enforces SESSION_STATES enum on state transitions. Public API: `create_session(scenario_slug, session_mode, facilitator_name, duration_minutes, max_teams, config)`, `get_session(session_id)`, `get_session_by_code(join_code)`, `list_sessions(state)`, `update_session_state(session_id, new_state)`. | imported by TTX blueprint | dict \| list[dict] |
| Migrate To Vault | tools\trading\credentials\migrate_to_vault.py | Auto-registered: credentials/migrate_to_vault.py | --json | JSON |
| Certificates | tools\trading\lessons\certificates.py | Auto-registered: lessons/certificates.py | --json | JSON |
| Bootstrap Hmm | tools\trading\ml\bootstrap_hmm.py | Auto-registered: ml/bootstrap_hmm.py | --json | JSON |
| Cmmi L3 Assessor | tools\compliance\cmmi_l3_assessor.py | Auto-registered: compliance/cmmi_l3_assessor.py | --json | JSON |
| Connectivity Ref | tools\network\connectivity_ref.py | Auto-registered: network/connectivity_ref.py | --json | JSON |
| Academy Oracle Reflex | tools\genesis\reflexes\academy_oracle_reflex.py | Auto-registered: reflexes/academy_oracle_reflex.py | --json | JSON |
| Cost Estimator | tools\agentic_ai_canvas\cost_estimator.py | Auto-registered: agentic_ai_canvas/cost_estimator.py | --json | JSON |
| Data Profiler | tools\data_canvas\data_profiler.py | Auto-registered: data_canvas/data_profiler.py | --json | JSON |
| Quality Engine | tools\data_canvas\quality_engine.py | Auto-registered: data_canvas/quality_engine.py | --json | JSON |
| Acled Importer | tools\strategos\acled_importer.py | Auto-registered: strategos/acled_importer.py | --json | JSON |
| Economic Importer | tools\strategos\economic_importer.py | Auto-registered: strategos/economic_importer.py | --json | JSON |
| Historical Baselines | tools\strategos\historical_baselines.py | Auto-registered: strategos/historical_baselines.py | --json | JSON |
| Iw Bayesian | tools\strategos\iw_bayesian.py | Auto-registered: strategos/iw_bayesian.py | --json | JSON |
| Iw Pattern Matcher | tools\strategos\iw_pattern_matcher.py | Auto-registered: strategos/iw_pattern_matcher.py | --json | JSON |
| Iw Scorers | tools\strategos\iw_scorers.py | Auto-registered: strategos/iw_scorers.py | --json | JSON |
| Oryx Importer | tools\strategos\oryx_importer.py | Auto-registered: strategos/oryx_importer.py | --json | JSON |
| Dossier Advisor | tools\migration_canvas\dossier_advisor.py | Auto-registered: migration_canvas/dossier_advisor.py | --json | JSON |
| Wave Planner | tools\migration_canvas\wave_planner.py | Auto-registered: migration_canvas/wave_planner.py | --json | JSON |
| Code Lens | tools\analysis\code_lens.py | Auto-registered: analysis/code_lens.py | --json | JSON |
| Anomaly Detector | tools\data_canvas\anomaly_detector.py | Auto-registered: data_canvas/anomaly_detector.py | --json | JSON |
| Freshness Guardian | tools\data_canvas\freshness_guardian.py | Auto-registered: data_canvas/freshness_guardian.py | --json | JSON |
| Pii Scanner | tools\data_canvas\pii_scanner.py | Auto-registered: data_canvas/pii_scanner.py | --json | JSON |
| Inventory Scanner | tools\migration_canvas\inventory_scanner.py | Auto-registered: migration_canvas/inventory_scanner.py | --json | JSON |
| Canvas Bridge | tools\agentic_ai_canvas\canvas_bridge.py | Auto-registered: agentic_ai_canvas/canvas_bridge.py | --json | JSON |
| Seed Ddc Datasci | tools\kanban\seed_ddc_datasci.py | Auto-registered: kanban/seed_ddc_datasci.py | --json | JSON |
| Seed M03 M05 | tools\kanban\seed_m03_m05.py | Auto-registered: kanban/seed_m03_m05.py | --json | JSON |
| Seed Mce Kanban | tools\kanban\seed_mce_kanban.py | Auto-registered: kanban/seed_mce_kanban.py | --json | JSON |
| Seed Nmce Kanban | tools\kanban\seed_nmce_kanban.py | Auto-registered: kanban/seed_nmce_kanban.py | --json | JSON |
| Seed Remaining Tasks | tools\kanban\seed_remaining_tasks.py | Auto-registered: kanban/seed_remaining_tasks.py | --json | JSON |
| Validate Secops03 | tools\testing\validate_secops03.py | Auto-registered: testing/validate_secops03.py | --json | JSON |
| Sim Cipher Forge | tools\ttx\scenarios\sim_cipher_forge.py | Auto-registered: scenarios/sim_cipher_forge.py | --json | JSON |
| Sim Forge Ascent | tools\ttx\scenarios\sim_forge_ascent.py | Auto-registered: scenarios/sim_forge_ascent.py | --json | JSON |
| Sim Hunt The Fleet | tools\ttx\scenarios\sim_hunt_the_fleet.py | Auto-registered: scenarios/sim_hunt_the_fleet.py | --json | JSON |
| Sim Meridian | tools\ttx\scenarios\sim_meridian.py | Auto-registered: scenarios/sim_meridian.py | --json | JSON |
| Solarwinds Connector | tools\databridge\connectors\solarwinds_connector.py | Auto-registered: connectors/solarwinds_connector.py | --json | JSON |
| Librenms Connector | tools\databridge\connectors\librenms_connector.py | Auto-registered: connectors/librenms_connector.py | --json | JSON |
| Riverbed Netim Connector | tools\databridge\connectors\riverbed_netim_connector.py | Auto-registered: connectors/riverbed_netim_connector.py | --json | JSON |
| Riverbed Netim Adapter | tools\network\adapters\riverbed_netim_adapter.py | Auto-registered: adapters/riverbed_netim_adapter.py | --json | JSON |
| Splunk Adapter | tools\network\adapters\splunk_adapter.py | Auto-registered: adapters/splunk_adapter.py | --json | JSON |
| Splunk Connector | tools\databridge\connectors\splunk_connector.py | Auto-registered: connectors/splunk_connector.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Idc Cloud Drift | tools\genesis\reflexes\idc_cloud_drift.py | Auto-registered: reflexes/idc_cloud_drift.py | --json | JSON |
| Mdc Cutover Countdown | tools\genesis\reflexes\mdc_cutover_countdown.py | Auto-registered: reflexes/mdc_cutover_countdown.py | --json | JSON |
| Ndc Topology Drift | tools\genesis\reflexes\ndc_topology_drift.py | Auto-registered: reflexes/ndc_topology_drift.py | --json | JSON |
| Pdc Pipeline Stale | tools\genesis\reflexes\pdc_pipeline_stale.py | Auto-registered: reflexes/pdc_pipeline_stale.py | --json | JSON |
| Qdc Gate Breach | tools\genesis\reflexes\qdc_gate_breach.py | Auto-registered: reflexes/qdc_gate_breach.py | --json | JSON |
| Sdc Control Expiry | tools\genesis\reflexes\sdc_control_expiry.py | Auto-registered: reflexes/sdc_control_expiry.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Seed Aadc Aimc Appmigration | tools\kanban\seed_aadc_aimc_appmigration.py | Auto-registered: kanban/seed_aadc_aimc_appmigration.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Adapter Registry | tools\ops_hub\adapter_registry.py | Auto-registered: ops_hub/adapter_registry.py | --json | JSON |
| Aiops Engine | tools\ops_hub\aiops_engine.py | Auto-registered: ops_hub/aiops_engine.py | --json | JSON |
| Llmops Engine | tools\ops_hub\llmops_engine.py | Auto-registered: ops_hub/llmops_engine.py | --json | JSON |
| Mlops Engine | tools\ops_hub\mlops_engine.py | Auto-registered: ops_hub/mlops_engine.py | --json | JSON |
| Ops Aggregator | tools\ops_hub\ops_aggregator.py | Auto-registered: ops_hub/ops_aggregator.py | --json | JSON |
| Azureml Adapter | tools\ops_hub\adapters\azureml_adapter.py | Auto-registered: adapters/azureml_adapter.py | --json | JSON |
| Bedrock Guardrails Adapter | tools\ops_hub\adapters\bedrock_guardrails_adapter.py | Auto-registered: adapters/bedrock_guardrails_adapter.py | --json | JSON |
| Cloudwatch Adapter | tools\ops_hub\adapters\cloudwatch_adapter.py | Auto-registered: adapters/cloudwatch_adapter.py | --json | JSON |
| Dvc Adapter | tools\ops_hub\adapters\dvc_adapter.py | Auto-registered: adapters/dvc_adapter.py | --json | JSON |
| Evidently Adapter | tools\ops_hub\adapters\evidently_adapter.py | Auto-registered: adapters/evidently_adapter.py | --json | JSON |
| Langfuse Adapter | tools\ops_hub\adapters\langfuse_adapter.py | Auto-registered: adapters/langfuse_adapter.py | --json | JSON |
| Mlflow Adapter | tools\ops_hub\adapters\mlflow_adapter.py | Auto-registered: adapters/mlflow_adapter.py | --json | JSON |
| Onnx Adapter | tools\ops_hub\adapters\onnx_adapter.py | Auto-registered: adapters/onnx_adapter.py | --json | JSON |
| Prometheus Adapter | tools\ops_hub\adapters\prometheus_adapter.py | Auto-registered: adapters/prometheus_adapter.py | --json | JSON |
| Sagemaker Adapter | tools\ops_hub\adapters\sagemaker_adapter.py | Auto-registered: adapters/sagemaker_adapter.py | --json | JSON |
| Vertexai Adapter | tools\ops_hub\adapters\vertexai_adapter.py | Auto-registered: adapters/vertexai_adapter.py | --json | JSON |
| Ohc | tools\iqe\adapters\ohc.py | Auto-registered: adapters/ohc.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Modernization Bridge | tools\aiml_canvas\modernization_bridge.py | Auto-registered: aiml_canvas/modernization_bridge.py | --json | JSON |
| Seed Ohc Kanban | tools\kanban\seed_ohc_kanban.py | Auto-registered: kanban/seed_ohc_kanban.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Ops Config Generator | tools\agentic_ai_canvas\ops_config_generator.py | Auto-registered: agentic_ai_canvas/ops_config_generator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Academy Reflex | tools\genesis\reflexes\academy_reflex.py | Auto-registered: reflexes/academy_reflex.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Migration Executor | tools\govlift\migration_executor.py | Auto-registered: govlift/migration_executor.py | --json | JSON |
| Workload Scanner | tools\govlift\workload_scanner.py | Auto-registered: govlift/workload_scanner.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Audit Engine | tools\govlift\audit_engine.py | Auto-registered: govlift/audit_engine.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Seed Migration Demo | tools\network\seed_migration_demo.py | Auto-registered: network/seed_migration_demo.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Fix Py39 Annotations | tools\compat\fix_py39_annotations.py | Auto-registered: compat/fix_py39_annotations.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Seed Ddx Extensions | tools\kanban\seed_ddx_extensions.py | Auto-registered: kanban/seed_ddx_extensions.py | --json | JSON |
| Mcp Debug Wrapper | tools\mcp\mcp_debug_wrapper.py | Auto-registered: mcp/mcp_debug_wrapper.py | --json | JSON |
| Commander Dashboard | tools\strategos\commander_dashboard.py | Auto-registered: strategos/commander_dashboard.py | --json | JSON |
| Trap Db | tools\trading\ta\trap_db.py | Auto-registered: ta/trap_db.py | --json | JSON |
| 090 Migration Advisory Chat | tools\extensions\builtins\090_migration_advisory_chat.py | Auto-registered: builtins/090_migration_advisory_chat.py | --json | JSON |
| 021 Sg Sigint Events | tools\db\migrations\021_sg_sigint_events.py | Auto-registered: migrations/021_sg_sigint_events.py | --json | JSON |
| 022 Sg Eo Signals | tools\db\migrations\022_sg_eo_signals.py | Auto-registered: migrations/022_sg_eo_signals.py | --json | JSON |
| 023 Sg Socmint Signals | tools\db\migrations\023_sg_socmint_signals.py | Auto-registered: migrations/023_sg_socmint_signals.py | --json | JSON |
| Seed Sgx Osint Epics | tools\db\seeds\seed_sgx_osint_epics.py | Auto-registered: seeds/seed_sgx_osint_epics.py | --json | JSON |
| Seed Sg Cyber Ext | tools\db\seeds\seed_sg_cyber_ext.py | Auto-registered: seeds/seed_sg_cyber_ext.py | --json | JSON |
| Seed Sg Theaters | tools\db\seeds\seed_sg_theaters.py | Auto-registered: seeds/seed_sg_theaters.py | --json | JSON |
| Seed Sg Twin | tools\db\seeds\seed_sg_twin.py | Auto-registered: seeds/seed_sg_twin.py | --json | JSON |
| Seed Supply Kg Edges | tools\db\seeds\seed_supply_kg_edges.py | Auto-registered: seeds/seed_supply_kg_edges.py | --json | JSON |
| Seed Wfs Decomp | tools\db\seeds\seed_wfs_decomp.py | Auto-registered: seeds/seed_wfs_decomp.py | --json | JSON |
| Seed Wfs Plan | tools\db\seeds\seed_wfs_plan.py | Auto-registered: seeds/seed_wfs_plan.py | --json | JSON |
| Seed Wne Plan | tools\db\seeds\seed_wne_plan.py | Auto-registered: seeds/seed_wne_plan.py | --json | JSON |
| Seed Wne Writeguard | tools\db\seeds\seed_wne_writeguard.py | Auto-registered: seeds/seed_wne_writeguard.py | --json | JSON |
| Safety Monitor | tools\dashboard\api\safety_monitor.py | Auto-registered: api/safety_monitor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intent Classifier | tools\chat_router\intent_classifier.py | Auto-registered: chat_router/intent_classifier.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Url Analyzer | tools\chat_router\url_analyzer.py | Auto-registered: chat_router/url_analyzer.py | --json | JSON |
| Seed Wex Kanban | tools\studio\seed_wex_kanban.py | Auto-registered: studio/seed_wex_kanban.py | --json | JSON |
| Seed Wex Pg | tools\studio\seed_wex_pg.py | Auto-registered: studio/seed_wex_pg.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pytest Plugins | tools\pytest_plugins.py | Auto-registered: tools/pytest_plugins.py | --json | JSON |
| Compliance Checker | tools\aadc\compliance_checker.py | Auto-registered: aadc/compliance_checker.py | --json | JSON |
| Governance Scanner | tools\aadc\governance_scanner.py | Auto-registered: aadc/governance_scanner.py | --json | JSON |
| Deployment Checker | tools\aimc\deployment_checker.py | Auto-registered: aimc/deployment_checker.py | --json | JSON |
| Model Scanner | tools\aimc\model_scanner.py | Auto-registered: aimc/model_scanner.py | --json | JSON |
| Game Pack | tools\ai_game_engine\game_pack.py | Auto-registered: ai_game_engine/game_pack.py | --json | JSON |
| Game Session | tools\ai_game_engine\game_session.py | Auto-registered: ai_game_engine/game_session.py | --json | JSON |
| Round Runner | tools\ai_game_engine\round_runner.py | Auto-registered: ai_game_engine/round_runner.py | --json | JSON |
| Blockchain Config | tools\blockchain\blockchain_config.py | Auto-registered: blockchain/blockchain_config.py | --json | JSON |
| Chain Anchor | tools\blockchain\chain_anchor.py | Auto-registered: blockchain/chain_anchor.py | --json | JSON |
| Channel Manager | tools\blockchain\channel_manager.py | Auto-registered: blockchain/channel_manager.py | --json | JSON |
| Provenance Verifier | tools\blockchain\provenance_verifier.py | Auto-registered: blockchain/provenance_verifier.py | --json | JSON |
| Zk Prover | tools\blockchain\zk_prover.py | Auto-registered: blockchain/zk_prover.py | --json | JSON |
| Attestation Signer | tools\crypto\attestation_signer.py | Auto-registered: crypto/attestation_signer.py | --json | JSON |
| Key Manager | tools\crypto\key_manager.py | Auto-registered: crypto/key_manager.py | --json | JSON |
| Merkle Tree | tools\crypto\merkle_tree.py | Auto-registered: crypto/merkle_tree.py | --json | JSON |
| Ansible Executor | tools\data\ansible_executor.py | Auto-registered: data/ansible_executor.py | --json | JSON |
| Aws Config Executor | tools\data\aws_config_executor.py | Auto-registered: data/aws_config_executor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Reader | tools\data\canvas_reader.py | Auto-registered: data/canvas_reader.py | --json | JSON |
| Lineage Scanner | tools\data\lineage_scanner.py | Auto-registered: data/lineage_scanner.py | --json | JSON |
| Migration Reporter | tools\data\migration_reporter.py | Auto-registered: data/migration_reporter.py | --json | JSON |
| Schema Checker | tools\data\schema_checker.py | Auto-registered: data/schema_checker.py | --json | JSON |
| Terraform Apply | tools\data\terraform_apply.py | Auto-registered: data/terraform_apply.py | --json | JSON |
| Terraform Destroy | tools\data\terraform_destroy.py | Auto-registered: data/terraform_destroy.py | --json | JSON |
| Terraform Executor | tools\data\terraform_executor.py | Auto-registered: data/terraform_executor.py | --json | JSON |
| Validation Runner | tools\data\validation_runner.py | Auto-registered: data/validation_runner.py | --json | JSON |
| Hardening Checker | tools\idc\hardening_checker.py | Auto-registered: idc/hardening_checker.py | --json | JSON |
| Infra Scanner | tools\idc\infra_scanner.py | Auto-registered: idc/infra_scanner.py | --json | JSON |
| Readiness Checker | tools\mdc\readiness_checker.py | Auto-registered: mdc/readiness_checker.py | --json | JSON |
| Discovery Scanner | tools\migration\discovery_scanner.py | Auto-registered: migration/discovery_scanner.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Config Checker | tools\ndc\config_checker.py | Auto-registered: ndc/config_checker.py | --json | JSON |
| Gns3 Topology Builder | tools\ndc\gns3_topology_builder.py | Auto-registered: ndc/gns3_topology_builder.py | --json | JSON |
| Gns3 Topology Probe | tools\ndc\gns3_topology_probe.py | Auto-registered: ndc/gns3_topology_probe.py | --json | JSON |
| Topology Scanner | tools\ndc\topology_scanner.py | Auto-registered: ndc/topology_scanner.py | --json | JSON |
| Ztp Config Generator | tools\ndc\ztp_config_generator.py | Auto-registered: ndc/ztp_config_generator.py | --json | JSON |
| Ztp Console Push | tools\ndc\ztp_console_push.py | Auto-registered: ndc/ztp_console_push.py | --json | JSON |
| Gns3 Image Downloader | tools\network\gns3_image_downloader.py | Auto-registered: network/gns3_image_downloader.py | --json | JSON |
| Coverage Scanner | tools\odc\coverage_scanner.py | Auto-registered: odc/coverage_scanner.py | --json | JSON |
| Gap Checker | tools\odc\gap_checker.py | Auto-registered: odc/gap_checker.py | --json | JSON |
| Ops Scanner | tools\ohc\ops_scanner.py | Auto-registered: ohc/ops_scanner.py | --json | JSON |
| Runbook Checker | tools\ohc\runbook_checker.py | Auto-registered: ohc/runbook_checker.py | --json | JSON |
| Antipattern Checker | tools\pdc\antipattern_checker.py | Auto-registered: pdc/antipattern_checker.py | --json | JSON |
| Pipeline Scanner | tools\pdc\pipeline_scanner.py | Auto-registered: pdc/pipeline_scanner.py | --json | JSON |
| Gate Checker | tools\qdc\gate_checker.py | Auto-registered: qdc/gate_checker.py | --json | JSON |
| Quality Scanner | tools\qdc\quality_scanner.py | Auto-registered: qdc/quality_scanner.py | --json | JSON |
| Threat Scanner | tools\sdc\threat_scanner.py | Auto-registered: sdc/threat_scanner.py | --json | JSON |
| Feature Flags | tools\databridge\feature_flags.py | Auto-registered: databridge/feature_flags.py | --json | JSON |
| Localstack Provisioner | tools\data_canvas\localstack_provisioner.py | Auto-registered: data_canvas/localstack_provisioner.py | --json | JSON |
| Base Agent | tools\gameday\base_agent.py | Auto-registered: gameday/base_agent.py | --json | JSON |
| Game Master | tools\gameday\game_master.py | Auto-registered: gameday/game_master.py | --json | JSON |
| Judge Agent | tools\gameday\judge_agent.py | Auto-registered: gameday/judge_agent.py | --json | JSON |
| Leaderboard Engine | tools\gameday\leaderboard_engine.py | Auto-registered: gameday/leaderboard_engine.py | --json | JSON |
| Ops Bridge | tools\gameday\ops_bridge.py | Auto-registered: gameday/ops_bridge.py | --json | JSON |
| Round Manager | tools\gameday\round_manager.py | Auto-registered: gameday/round_manager.py | --json | JSON |
| Scenario Enhancer | tools\gameday\scenario_enhancer.py | Auto-registered: gameday/scenario_enhancer.py | --json | JSON |
| Team Runner | tools\gameday\team_runner.py | Auto-registered: gameday/team_runner.py | --json | JSON |
| Auto Wave Planner | tools\govlift\auto_wave_planner.py | Auto-registered: govlift/auto_wave_planner.py | --json | JSON |
| Map Assessor | tools\govlift\map_assessor.py | Auto-registered: govlift/map_assessor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Scenario Registry | tools\ai_game_engine\scenario_registry.py | Auto-registered: ai_game_engine/scenario_registry.py | --json | JSON |
| Ontology Bridge | tools\canvas\ontology_bridge.py | Auto-registered: canvas/ontology_bridge.py | --json | JSON |
| Performance Benchmark | tools\govlift\performance_benchmark.py | Auto-registered: govlift/performance_benchmark.py | --json | JSON |
| Rollback Engine | tools\govlift\rollback_engine.py | Auto-registered: govlift/rollback_engine.py | --json | JSON |
| Dr Failover | tools\infra\dr_failover.py | Auto-registered: infra/dr_failover.py | --json | JSON |
| Dr Generator | tools\infra\dr_generator.py | Auto-registered: infra/dr_generator.py | --json | JSON |
| Seed Cot Cod | tools\kanban\seed_cot_cod.py | Auto-registered: kanban/seed_cot_cod.py | --json | JSON |
| Seed Llm Cache | tools\kanban\seed_llm_cache.py | Auto-registered: kanban/seed_llm_cache.py | --json | JSON |
| Seed Ontology | tools\kanban\seed_ontology.py | Auto-registered: kanban/seed_ontology.py | --json | JSON |
| Seed Security Framework | tools\kanban\seed_security_framework.py | Auto-registered: kanban/seed_security_framework.py | --json | JSON |
| Seed Showcase | tools\kanban\seed_showcase.py | Auto-registered: kanban/seed_showcase.py | --json | JSON |
| Response Cache | tools\llm\response_cache.py | Auto-registered: llm/response_cache.py | --json | JSON |
| Response Cache Test | tools\llm\response_cache_test.py | Auto-registered: llm/response_cache_test.py | --json | JSON |
| Sla Enforcer | tools\migration_intelligence\sla_enforcer.py | Auto-registered: migration_intelligence/sla_enforcer.py | --json | JSON |
| Agentic Netops | tools\ndc\agentic_netops.py | Auto-registered: ndc/agentic_netops.py | --json | JSON |
| Dod Lab Api | tools\ndc\dod_lab_api.py | Auto-registered: ndc/dod_lab_api.py | --json | JSON |
| Dod Lab Demo Runner | tools\ndc\dod_lab_demo_runner.py | Auto-registered: ndc/dod_lab_demo_runner.py | --json | JSON |
| Dod Lab Synthetic Data | tools\ndc\dod_lab_synthetic_data.py | Auto-registered: ndc/dod_lab_synthetic_data.py | --json | JSON |
| Gns3 Backup | tools\ndc\gns3_backup.py | Auto-registered: ndc/gns3_backup.py | --json | JSON |
| Gns3 Dod Topology Builder | tools\ndc\gns3_dod_topology_builder.py | Auto-registered: ndc/gns3_dod_topology_builder.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Ai Trust | tools\mission_canvas\ai_trust.py | Auto-registered: mission_canvas/ai_trust.py | --json | JSON |
| Cicd Bridge | tools\mission_canvas\cicd_bridge.py | Auto-registered: mission_canvas/cicd_bridge.py | --json | JSON |
| Security Posture | tools\mission_canvas\security_posture.py | Auto-registered: mission_canvas/security_posture.py | --json | JSON |
| Gns3 Traffic Engine | tools\ndc\gns3_traffic_engine.py | Auto-registered: ndc/gns3_traffic_engine.py | --json | JSON |
| Ztp Dod Config Generator | tools\ndc\ztp_dod_config_generator.py | Auto-registered: ndc/ztp_dod_config_generator.py | --json | JSON |
| Enclave Scanner | tools\network\enclave_scanner.py | Auto-registered: network/enclave_scanner.py | --json | JSON |
| Nipr Constraint Validator | tools\network\nipr_constraint_validator.py | Auto-registered: network/nipr_constraint_validator.py | --json | JSON |
| Schema Extractor | tools\ontology\schema_extractor.py | Auto-registered: ontology/schema_extractor.py | --json | JSON |
| Timeline Loader | tools\project\timeline_loader.py | Auto-registered: project/timeline_loader.py | --json | JSON |
| Backfill Registry | tools\provenance\backfill_registry.py | Auto-registered: provenance/backfill_registry.py | --json | JSON |
| Budget Validator | tools\requirements\budget_validator.py | Auto-registered: requirements/budget_validator.py | --json | JSON |
| Decompose Backlog | tools\scripts\decompose_backlog.py | Auto-registered: scripts/decompose_backlog.py | --json | JSON |
| Cui Crypto | tools\security\cui_crypto.py | Auto-registered: security/cui_crypto.py | --json | JSON |
| Gns3 Sim | tools\security_canvas\gns3_sim.py | Auto-registered: security_canvas/gns3_sim.py | --json | JSON |
| E2E Migration Intel | tools\testing\e2e_migration_intel.py | Auto-registered: testing/e2e_migration_intel.py | --json | JSON |
| Team Composition | tools\workforce\team_composition.py | Auto-registered: workforce/team_composition.py | --json | JSON |
| Gns3 Sim | tools\studio\executors\gns3_sim.py | Auto-registered: executors/gns3_sim.py | --json | JSON |
| Aadc Topology | tools\studio\sim\aadc_topology.py | Auto-registered: sim/aadc_topology.py | --json | JSON |
| Aimc Topology | tools\studio\sim\aimc_topology.py | Auto-registered: sim/aimc_topology.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Base Topology | tools\studio\sim\base_topology.py | Auto-registered: sim/base_topology.py | --json | JSON |
| Bdc Topology | tools\studio\sim\bdc_topology.py | Auto-registered: sim/bdc_topology.py | --json | JSON |
| Canvas Traffic Engine | tools\studio\sim\canvas_traffic_engine.py | Auto-registered: sim/canvas_traffic_engine.py | --json | JSON |
| Ddc Topology | tools\studio\sim\ddc_topology.py | Auto-registered: sim/ddc_topology.py | --json | JSON |
| Idc Topology | tools\studio\sim\idc_topology.py | Auto-registered: sim/idc_topology.py | --json | JSON |
| Mdc Topology | tools\studio\sim\mdc_topology.py | Auto-registered: sim/mdc_topology.py | --json | JSON |
| Odc Topology | tools\studio\sim\odc_topology.py | Auto-registered: sim/odc_topology.py | --json | JSON |
| Pdc Topology | tools\studio\sim\pdc_topology.py | Auto-registered: sim/pdc_topology.py | --json | JSON |
| Qdc Topology | tools\studio\sim\qdc_topology.py | Auto-registered: sim/qdc_topology.py | --json | JSON |
| Sdc Topology | tools\studio\sim\sdc_topology.py | Auto-registered: sim/sdc_topology.py | --json | JSON |
| Sim Hub | tools\studio\sim\sim_hub.py | Auto-registered: sim/sim_hub.py | --json | JSON |
| Training Exporter | tools\studio\sim\training_exporter.py | Auto-registered: sim/training_exporter.py | --json | JSON |
| Studio Sim | tools\iqe\adapters\studio_sim.py | Auto-registered: adapters/studio_sim.py | --json | JSON |
| Localstack Adapter | tools\infra_canvas\adapters\localstack_adapter.py | Auto-registered: adapters/localstack_adapter.py | --json | JSON |
| Gameday Orchestrator | tools\genesis\reflexes\gameday_orchestrator.py | Auto-registered: reflexes/gameday_orchestrator.py | --json | JSON |
| Sim Training Export | tools\genesis\reflexes\sim_training_export.py | Auto-registered: reflexes/sim_training_export.py | --json | JSON |
| Aws Controltower Connector | tools\databridge\connectors\aws_controltower_connector.py | Auto-registered: connectors/aws_controltower_connector.py | --json | JSON |
| Gns3 Connector | tools\databridge\connectors\gns3_connector.py | Auto-registered: connectors/gns3_connector.py | --json | JSON |
| Localstack Connector | tools\databridge\connectors\localstack_connector.py | Auto-registered: connectors/localstack_connector.py | --json | JSON |
| Servicenow Itsm Connector | tools\databridge\connectors\servicenow_itsm_connector.py | Auto-registered: connectors/servicenow_itsm_connector.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Chain Orchestrator | tools\llm\chain_orchestrator.py | Auto-registered: llm/chain_orchestrator.py | --json | JSON |
| Chain Prompts | tools\llm\chain_prompts.py | Auto-registered: llm/chain_prompts.py | --json | JSON |
| Tenable Connector | tools\databridge\connectors\tenable_connector.py | Auto-registered: connectors/tenable_connector.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Multi Persona Panel | tools\requirements\multi_persona_panel.py | Auto-registered: requirements/multi_persona_panel.py | --json | JSON |
| Govchain Anchor | tools\genesis\reflexes\govchain_anchor.py | Auto-registered: reflexes/govchain_anchor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Il5 Display Service | tools\il5\il5_display_service.py | Auto-registered: il5/il5_display_service.py | --json | JSON |
| Il5 Ingestion Service | tools\il5\il5_ingestion_service.py | Auto-registered: il5/il5_ingestion_service.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Generate Compliance Report | tools\generate_compliance_report.py | Auto-registered: tools/generate_compliance_report.py | --json | JSON |
| Dti Calculator | tools\dat\dti_calculator.py | Auto-registered: dat/dti_calculator.py | --json | JSON |
| Jise Portal | tools\intelligence\jise_portal.py | Auto-registered: intelligence/jise_portal.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Siem Alert Forwarder | tools\siem_alert_forwarder.py | Auto-registered: tools/siem_alert_forwarder.py | --json | JSON |
| Pir Alert Generator | tools\intelligence\pir_alert_generator.py | Auto-registered: intelligence/pir_alert_generator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Module Budget Tracker | tools\budget\module_budget_tracker.py | Auto-registered: budget/module_budget_tracker.py | --json | JSON |
| Osint Privacy Sanitizer | tools\strategos\osint_privacy_sanitizer.py | Auto-registered: strategos/osint_privacy_sanitizer.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Data Validator | tools\data_validator.py | Auto-registered: tools/data_validator.py | --json | JSON |
| Predictive Analysis | tools\strategos\predictive_analysis.py | Auto-registered: strategos/predictive_analysis.py | --json | JSON |
| Osint Normalizer | tools\threat_analysis\osint_normalizer.py | Auto-registered: threat_analysis/osint_normalizer.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Deploy Staging | tools\il5\deploy_staging.py | Auto-registered: il5/deploy_staging.py | --json | JSON |
| Async Alert Writer | tools\monitor\async_alert_writer.py | Auto-registered: monitor/async_alert_writer.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Build Logger | tools\logging\build_logger.py | Auto-registered: logging/build_logger.py | --json | JSON |
| Icdev Logger | tools\logging\icdev_logger.py | Auto-registered: logging/icdev_logger.py | --json | JSON |
| Generate Exec Deck | tools\presentations\generate_exec_deck.py | Auto-registered: presentations/generate_exec_deck.py | --json | JSON |
| Generate Exec Doc | tools\presentations\generate_exec_doc.py | Auto-registered: presentations/generate_exec_doc.py | --json | JSON |
| Feature Task Template | tools\testing\feature_task_template.py | Auto-registered: testing/feature_task_template.py | --json | JSON |
| Pre Commit Check | tools\testing\pre_commit_check.py | Auto-registered: testing/pre_commit_check.py | --json | JSON |
| Route Smoke | tools\testing\route_smoke.py | Auto-registered: testing/route_smoke.py | --json | JSON |
| Seed Gcpl Kanban | tools\testing\seed_gcpl_kanban.py | Auto-registered: testing/seed_gcpl_kanban.py | --json | JSON |
| Seed Logging Kanban | tools\testing\seed_logging_kanban.py | Auto-registered: testing/seed_logging_kanban.py | --json | JSON |
| Seed Playwright Kanban | tools\testing\seed_playwright_kanban.py | Auto-registered: testing/seed_playwright_kanban.py | --json | JSON |
| Log Triage | tools\genesis\reflexes\log_triage.py | Auto-registered: reflexes/log_triage.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Telco Rfp Adapter | tools\govcon\telco_rfp_adapter.py | Auto-registered: govcon/telco_rfp_adapter.py | --json | JSON |
| Fcc Compliance | tools\network\fcc_compliance.py | Auto-registered: network/fcc_compliance.py | --json | JSON |
| Transit Pricing Benchmark | tools\pmc_canvas\transit_pricing_benchmark.py | Auto-registered: pmc_canvas/transit_pricing_benchmark.py | --json | JSON |
| Bgp Route Monitor | tools\genesis\reflexes\bgp_route_monitor.py | Auto-registered: reflexes/bgp_route_monitor.py | --json | JSON |
| Nocc Alarm Triage | tools\genesis\reflexes\nocc_alarm_triage.py | Auto-registered: reflexes/nocc_alarm_triage.py | --json | JSON |
| Nocc Sla Watcher | tools\genesis\reflexes\nocc_sla_watcher.py | Auto-registered: reflexes/nocc_sla_watcher.py | --json | JSON |
| Peering Health Monitor | tools\genesis\reflexes\peering_health_monitor.py | Auto-registered: reflexes/peering_health_monitor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Test Gcpl Disc 10 | tools\testing\test_gcpl_disc_10.py | Auto-registered: testing/test_gcpl_disc_10.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Test Gcpl Ext 06 | tools\testing\test_gcpl_ext_06.py | Auto-registered: testing/test_gcpl_ext_06.py | --json | JSON |
| Test Gcpl Map 02 | tools\testing\test_gcpl_map_02.py | Auto-registered: testing/test_gcpl_map_02.py | --json | JSON |
| Circuit Capacity Monitor | tools\genesis\reflexes\circuit_capacity_monitor.py | Auto-registered: reflexes/circuit_capacity_monitor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Opportunity Scorer | tools\ai_augmentation\opportunity_scorer.py | Auto-registered: ai_augmentation/opportunity_scorer.py | --json | JSON |
| Pattern Classifier | tools\ai_augmentation\pattern_classifier.py | Auto-registered: ai_augmentation/pattern_classifier.py | --json | JSON |
| Roadmap Generator | tools\ai_augmentation\roadmap_generator.py | Auto-registered: ai_augmentation/roadmap_generator.py | --json | JSON |
| Bgp Hijack Detector | tools\dsoc_canvas\bgp_hijack_detector.py | Auto-registered: dsoc_canvas/bgp_hijack_detector.py | --json | JSON |
| Bgpq4 Wrapper | tools\pmc_canvas\bgpq4_wrapper.py | Auto-registered: pmc_canvas/bgpq4_wrapper.py | --json | JSON |
| Ai Augmentation | tools\iqe\adapters\ai_augmentation.py | Auto-registered: adapters/ai_augmentation.py | --json | JSON |
| Bgp Alerter Ingest | tools\genesis\reflexes\bgp_alerter_ingest.py | Auto-registered: reflexes/bgp_alerter_ingest.py | --json | JSON |
| Peering Manager Connector | tools\databridge\connectors\peering_manager_connector.py | Auto-registered: connectors/peering_manager_connector.py | --json | JSON |
| Pmacct Connector | tools\databridge\connectors\pmacct_connector.py | Auto-registered: connectors/pmacct_connector.py | --json | JSON |
| Routinator Connector | tools\databridge\connectors\routinator_connector.py | Auto-registered: connectors/routinator_connector.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Xc Order Manager | tools\ccc_canvas\xc_order_manager.py | Auto-registered: ccc_canvas/xc_order_manager.py | --json | JSON |
| Agreement Lifecycle | tools\network\agreement_lifecycle.py | Auto-registered: network/agreement_lifecycle.py | --json | JSON |
| Partner Registry | tools\network\partner_registry.py | Auto-registered: network/partner_registry.py | --json | JSON |
| Peering Agreement Renewal | tools\genesis\reflexes\peering_agreement_renewal.py | Auto-registered: reflexes/peering_agreement_renewal.py | --json | JSON |
| Xc Order Poller | tools\genesis\reflexes\xc_order_poller.py | Auto-registered: reflexes/xc_order_poller.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Append Only Audit | tools\ai_augmentation\agent_readiness\pillars\append_only_audit.py | Auto-registered: pillars/append_only_audit.py | --json | JSON |
| Il Classification | tools\ai_augmentation\agent_readiness\pillars\il_classification.py | Auto-registered: pillars/il_classification.py | --json | JSON |
| Nist Controls | tools\ai_augmentation\agent_readiness\pillars\nist_controls.py | Auto-registered: pillars/nist_controls.py | --json | JSON |
| Stig Compliance | tools\ai_augmentation\agent_readiness\pillars\stig_compliance.py | Auto-registered: pillars/stig_compliance.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Synthetic Network Generator | tools\ndc\synthetic_network_generator.py | Auto-registered: ndc/synthetic_network_generator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Eol Scanner | tools\ndc\eol_scanner.py | Auto-registered: ndc/eol_scanner.py | --json | JSON |
| Replacement Recommender | tools\ndc\replacement_recommender.py | Auto-registered: ndc/replacement_recommender.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Config Alignment Analyzer | tools\ndc\config_alignment_analyzer.py | Auto-registered: ndc/config_alignment_analyzer.py | --json | JSON |
| Migration Document Generator | tools\ndc\migration_document_generator.py | Auto-registered: ndc/migration_document_generator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Cloud Topology Overlay | tools\ndc\cloud_topology_overlay.py | Auto-registered: ndc/cloud_topology_overlay.py | --json | JSON |
| Executive Summary Generator | tools\ndc\executive_summary_generator.py | Auto-registered: ndc/executive_summary_generator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Config Translator | tools\ndc\config_translator.py | Auto-registered: ndc/config_translator.py | --json | JSON |
| Port Mapping Generator | tools\ndc\port_mapping_generator.py | Auto-registered: ndc/port_mapping_generator.py | --json | JSON |
| Seed Dewie Demo | tools\ndc\seed_dewie_demo.py | Auto-registered: ndc/seed_dewie_demo.py | --json | JSON |
| Isso Gate | tools\sdc\isso_gate.py | Auto-registered: sdc/isso_gate.py | --json | JSON |
| Sdc Demo | tools\iqe\adapters\sdc_demo.py | Auto-registered: adapters/sdc_demo.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Version Anomaly Detector | tools\ai_augmentation\version_anomaly_detector.py | Auto-registered: ai_augmentation/version_anomaly_detector.py | --json | JSON |
| Migrate To Icdev Logger | tools\refactor\migrate_to_icdev_logger.py | Auto-registered: refactor/migrate_to_icdev_logger.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Runbook Engine | tools\govlift\runbook_engine.py | Auto-registered: govlift/runbook_engine.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Entrypoint | tools\a2a\agent_entrypoint.py | Auto-registered: a2a/agent_entrypoint.py | --json | JSON |
| Provision Dev Certs | tools\a2a\provision_dev_certs.py | Auto-registered: a2a/provision_dev_certs.py | --json | JSON |
| Signal Ingester | tools\ai_augmentation\signal_ingester.py | Auto-registered: ai_augmentation/signal_ingester.py | --json | JSON |
| Agentic Runner | tools\anvil\agentic_runner.py | Auto-registered: anvil/agentic_runner.py | --json | JSON |
| Eval Harness | tools\genesis\harness\eval_harness.py | Auto-registered: harness/eval_harness.py | --json | JSON |
| Heuristic Writer | tools\genesis\harness\heuristic_writer.py | Auto-registered: harness/heuristic_writer.py | --json | JSON |
| Llm Triage | tools\genesis\harness\llm_triage.py | Auto-registered: harness/llm_triage.py | --json | JSON |
| Meta Harness | tools\genesis\harness\meta_harness.py | Auto-registered: harness/meta_harness.py | --json | JSON |
| Iceberg | tools\data_canvas\exporters\iceberg.py | Auto-registered: exporters/iceberg.py | --json | JSON |
| Lake Zones | tools\data_canvas\exporters\lake_zones.py | Auto-registered: exporters/lake_zones.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Data Mesh | tools\data_canvas\data_mesh.py | Auto-registered: data_canvas/data_mesh.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Ext Databridge | tools\iqe\adapters\ext_databridge.py | Auto-registered: adapters/ext_databridge.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Event Dispatcher | tools\ace\event_dispatcher.py | Auto-registered: ace/event_dispatcher.py | --json | JSON |
| Problem Classifier | tools\ace\problem_classifier.py | Auto-registered: ace/problem_classifier.py | --json | JSON |
| Role Loader | tools\ace\role_loader.py | Auto-registered: ace/role_loader.py | --json | JSON |
| Simulate Roles | tools\ace\simulate_roles.py | Auto-registered: ace/simulate_roles.py | --json | JSON |
| Skill Adapter | tools\ace\skill_adapter.py | Auto-registered: ace/skill_adapter.py | --json | JSON |
| Pm Skills Wiring | tools\cpmp\pm_skills_wiring.py | Auto-registered: cpmp/pm_skills_wiring.py | --json | JSON |
| Freshness Engine | tools\document_intelligence\freshness_engine.py | Auto-registered: document_intelligence/freshness_engine.py | --json | JSON |
| Geoint Ingestor | tools\geoint\geoint_ingestor.py | Auto-registered: geoint/geoint_ingestor.py | --json | JSON |
| Contract Mods Manager | tools\govcon\contract_mods_manager.py | Auto-registered: govcon/contract_mods_manager.py | --json | JSON |
| Generate Icdev Proposal Content | tools\govcon\generate_icdev_proposal_content.py | Auto-registered: govcon/generate_icdev_proposal_content.py | --json | JSON |
| Igce Estimator | tools\govcon\igce_estimator.py | Auto-registered: govcon/igce_estimator.py | --json | JSON |
| Map Icdev Capabilities | tools\govcon\map_icdev_capabilities.py | Auto-registered: govcon/map_icdev_capabilities.py | --json | JSON |
| Option Period Tracker | tools\govcon\option_period_tracker.py | Auto-registered: govcon/option_period_tracker.py | --json | JSON |
| Personnel Manager | tools\govcon\personnel_manager.py | Auto-registered: govcon/personnel_manager.py | --json | JSON |
| Pmo Ai Advisor | tools\govcon\pmo_ai_advisor.py | Auto-registered: govcon/pmo_ai_advisor.py | --json | JSON |
| Procurement Vehicles | tools\govcon\procurement_vehicles.py | Auto-registered: govcon/procurement_vehicles.py | --json | JSON |
| Run Writeguard On Drafts | tools\govcon\run_writeguard_on_drafts.py | Auto-registered: govcon/run_writeguard_on_drafts.py | --json | JSON |
| Seed Icdev Knowledge Base | tools\govcon\seed_icdev_knowledge_base.py | Auto-registered: govcon/seed_icdev_knowledge_base.py | --json | JSON |
| Seed Solicitation Requirements | tools\govcon\seed_solicitation_requirements.py | Auto-registered: govcon/seed_solicitation_requirements.py | --json | JSON |
| Update Icdev Proposal Metadata | tools\govcon\update_icdev_proposal_metadata.py | Auto-registered: govcon/update_icdev_proposal_metadata.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Gdpr Eraser | tools\compliance\gdpr_eraser.py | Auto-registered: compliance/gdpr_eraser.py | --json | JSON |
| Soc2 Collector | tools\compliance\soc2_collector.py | Auto-registered: compliance/soc2_collector.py | --json | JSON |
| Soc2 Exporter | tools\compliance\soc2_exporter.py | Auto-registered: compliance/soc2_exporter.py | --json | JSON |
| Vehicle Identifier | tools\govcon\vehicle_identifier.py | Auto-registered: govcon/vehicle_identifier.py | --json | JSON |
| Dep Version Advisor | tools\installer\dep_version_advisor.py | Auto-registered: installer/dep_version_advisor.py | --json | JSON |
| Claim Parser | tools\integrity\claim_parser.py | Auto-registered: integrity/claim_parser.py | --json | JSON |
| Intent Reconciler | tools\integrity\intent_reconciler.py | Auto-registered: integrity/intent_reconciler.py | --json | JSON |
| Skillspector Cache | tools\integrity\skillspector_cache.py | Auto-registered: integrity/skillspector_cache.py | --json | JSON |
| Scheduler Control | tools\kanban\scheduler_control.py | Auto-registered: kanban/scheduler_control.py | --json | JSON |
| Seed Ace Kanban | tools\kanban\seed_ace_kanban.py | Auto-registered: kanban/seed_ace_kanban.py | --json | JSON |
| Seed Aisg Ship | tools\kanban\seed_aisg_ship.py | Auto-registered: kanban/seed_aisg_ship.py | --json | JSON |
| Seed Appendix Initiatives | tools\kanban\seed_appendix_initiatives.py | Auto-registered: kanban/seed_appendix_initiatives.py | --json | JSON |
| Seed Chyg Kanban | tools\kanban\seed_chyg_kanban.py | Auto-registered: kanban/seed_chyg_kanban.py | --json | JSON |
| Seed Dic Kanban | tools\kanban\seed_dic_kanban.py | Auto-registered: kanban/seed_dic_kanban.py | --json | JSON |
| Seed Irad Kanban | tools\kanban\seed_irad_kanban.py | Auto-registered: kanban/seed_irad_kanban.py | --json | JSON |
| Seed Pma Gaps Kanban | tools\kanban\seed_pma_gaps_kanban.py | Auto-registered: kanban/seed_pma_gaps_kanban.py | --json | JSON |
| Seed Pmo Demo Wow | tools\kanban\seed_pmo_demo_wow.py | Auto-registered: kanban/seed_pmo_demo_wow.py | --json | JSON |
| Seed Prop Security Kanban | tools\kanban\seed_prop_security_kanban.py | Auto-registered: kanban/seed_prop_security_kanban.py | --json | JSON |
| Seed Sipa Integrity | tools\kanban\seed_sipa_integrity.py | Auto-registered: kanban/seed_sipa_integrity.py | --json | JSON |
| Seed Wgint | tools\kanban\seed_wgint.py | Auto-registered: kanban/seed_wgint.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Seed Zig Kanban | tools\kanban\seed_zig_kanban.py | Auto-registered: kanban/seed_zig_kanban.py | --json | JSON |
| Skill Promotion Gate | tools\kanban\skill_promotion_gate.py | Auto-registered: kanban/skill_promotion_gate.py | --json | JSON |
| Provider Health | tools\llm\provider_health.py | Auto-registered: llm/provider_health.py | --json | JSON |
| Seed Demo Showcase | tools\ndc\seed_demo_showcase.py | Auto-registered: ndc/seed_demo_showcase.py | --json | JSON |
| Synthetic Config Gen | tools\network\synthetic_config_gen.py | Auto-registered: network/synthetic_config_gen.py | --json | JSON |
| Alert Service | tools\notification_service\alert_service.py | Auto-registered: notification_service/alert_service.py | --json | JSON |
| Event Service | tools\notification_service\event_service.py | Auto-registered: notification_service/event_service.py | --json | JSON |
| Handler Service | tools\notification_service\handler_service.py | Auto-registered: notification_service/handler_service.py | --json | JSON |
| Render Handler Service | tools\notification_service\render_handler_service.py | Auto-registered: notification_service/render_handler_service.py | --json | JSON |
| Report Service | tools\notification_service\report_service.py | Auto-registered: notification_service/report_service.py | --json | JSON |
| Osint Ingestor | tools\osint\osint_ingestor.py | Auto-registered: osint/osint_ingestor.py | --json | JSON |
| Hacker News | tools\platform_connectors\hacker_news.py | Auto-registered: platform_connectors/hacker_news.py | --json | JSON |
| Credential Monitor | tools\pma\credential_monitor.py | Auto-registered: pma/credential_monitor.py | --json | JSON |
| Int Gap Monitor | tools\pma\int_gap_monitor.py | Auto-registered: pma/int_gap_monitor.py | --json | JSON |
| Meeting Coordinator | tools\pma\meeting_coordinator.py | Auto-registered: pma/meeting_coordinator.py | --json | JSON |
| Entitlement Rag | tools\rag\entitlement_rag.py | Auto-registered: rag/entitlement_rag.py | --json | JSON |
| Zig Activity Tracker | tools\security_canvas\zig_activity_tracker.py | Auto-registered: security_canvas/zig_activity_tracker.py | --json | JSON |
| Zig Artifact Generator | tools\security_canvas\zig_artifact_generator.py | Auto-registered: security_canvas/zig_artifact_generator.py | --json | JSON |
| Zig Assessor | tools\security_canvas\zig_assessor.py | Auto-registered: security_canvas/zig_assessor.py | --json | JSON |
| Zig External Adapter | tools\security_canvas\zig_external_adapter.py | Auto-registered: security_canvas/zig_external_adapter.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Zig Phase Tracker | tools\security_canvas\zig_phase_tracker.py | Auto-registered: security_canvas/zig_phase_tracker.py | --json | JSON |
| Zig Pillar Scorer | tools\security_canvas\zig_pillar_scorer.py | Auto-registered: security_canvas/zig_pillar_scorer.py | --json | JSON |
| Zig Portfolio | tools\security_canvas\zig_portfolio.py | Auto-registered: security_canvas/zig_portfolio.py | --json | JSON |
| Zig Roadmap Generator | tools\security_canvas\zig_roadmap_generator.py | Auto-registered: security_canvas/zig_roadmap_generator.py | --json | JSON |
| Seed Official Skills | tools\skillhub\seed_official_skills.py | Auto-registered: skillhub/seed_official_skills.py | --json | JSON |
| Gepa Optimizer | tools\skills\gepa_optimizer.py | Auto-registered: skills/gepa_optimizer.py | --json | JSON |
| Ndaa 889 Screener | tools\supply_chain\ndaa_889_screener.py | Auto-registered: supply_chain/ndaa_889_screener.py | --json | JSON |
| Qa Verify Backgrounds | tools\testing\qa_verify_backgrounds.py | Auto-registered: testing/qa_verify_backgrounds.py | --json | JSON |
| Lesson Learned Remediation | tools\workflow\lesson_learned_remediation.py | Auto-registered: workflow/lesson_learned_remediation.py | --json | JSON |
| Lac Scenarios | tools\zta\lac_scenarios.py | Auto-registered: zta/lac_scenarios.py | --json | JSON |
| Lac Simulator | tools\zta\lac_simulator.py | Auto-registered: zta/lac_simulator.py | --json | JSON |
| Alpaca Provider | tools\trading\data\alpaca_provider.py | Auto-registered: data/alpaca_provider.py | --json | JSON |
| Fixture Provider | tools\trading\data\fixture_provider.py | Auto-registered: data/fixture_provider.py | --json | JSON |
| Icdev Executive Overview | tools\slides\curated_decks\icdev_executive_overview.py | Auto-registered: curated_decks/icdev_executive_overview.py | --json | JSON |
| Govcon Discovery | tools\nova\reflexes\govcon_discovery.py | Auto-registered: reflexes/govcon_discovery.py | --json | JSON |
| Product Manager Soul | tools\nova\souls\product_manager_soul.py | Auto-registered: souls/product_manager_soul.py | --json | JSON |
| Software Craftsperson Soul | tools\nova\souls\software_craftsperson_soul.py | Auto-registered: souls/software_craftsperson_soul.py | --json | JSON |
| Github Listener | tools\notifications\adapters\github_listener.py | Auto-registered: adapters/github_listener.py | --json | JSON |
| Github Notify | tools\notifications\adapters\github_notify.py | Auto-registered: adapters/github_notify.py | --json | JSON |
| Gitlab Listener | tools\notifications\adapters\gitlab_listener.py | Auto-registered: adapters/gitlab_listener.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Seeders | tools\demo\canvas_seeders.py | Auto-registered: demo/canvas_seeders.py | --json | JSON |
| Config Review | tools\network\config_review.py | Auto-registered: network/config_review.py | --json | JSON |
| Health Blueprint | tools\observability\health_blueprint.py | Auto-registered: observability/health_blueprint.py | --json | JSON |
| Gitlab Notify | tools\notifications\adapters\gitlab_notify.py | Auto-registered: adapters/gitlab_notify.py | --json | JSON |
| Listener Base | tools\notifications\adapters\listener_base.py | Auto-registered: adapters/listener_base.py | --json | JSON |
| Mattermost Listener | tools\notifications\adapters\mattermost_listener.py | Auto-registered: adapters/mattermost_listener.py | --json | JSON |
| Skype Listener | tools\notifications\adapters\skype_listener.py | Auto-registered: adapters/skype_listener.py | --json | JSON |
| Skype Notify | tools\notifications\adapters\skype_notify.py | Auto-registered: adapters/skype_notify.py | --json | JSON |
| Teams Bot | tools\notifications\adapters\teams_bot.py | Auto-registered: adapters/teams_bot.py | --json | JSON |
| Teams Listener | tools\notifications\adapters\teams_listener.py | Auto-registered: adapters/teams_listener.py | --json | JSON |
| Mcip | tools\iqe\adapters\mcip.py | Auto-registered: adapters/mcip.py | --json | JSON |
| Skillspector Adapter | tools\integrity\adapters\skillspector_adapter.py | Auto-registered: adapters/skillspector_adapter.py | --json | JSON |
| Ace Team Monitor | tools\genesis\reflexes\ace_team_monitor.py | Auto-registered: reflexes/ace_team_monitor.py | --json | JSON |
| Aidp Monitor | tools\genesis\reflexes\aidp_monitor.py | Auto-registered: reflexes/aidp_monitor.py | --json | JSON |
| Cpmp Monitor | tools\genesis\reflexes\cpmp_monitor.py | Auto-registered: reflexes/cpmp_monitor.py | --json | JSON |
| Dic Review Cadence | tools\genesis\reflexes\dic_review_cadence.py | Auto-registered: reflexes/dic_review_cadence.py | --json | JSON |
| Fathomdesk Correlation Monitor | tools\genesis\reflexes\fathomdesk_correlation_monitor.py | Auto-registered: reflexes/fathomdesk_correlation_monitor.py | --json | JSON |
| Inspect Adapt | tools\genesis\reflexes\inspect_adapt.py | Auto-registered: reflexes/inspect_adapt.py | --json | JSON |
| Integrity Monitor | tools\genesis\reflexes\integrity_monitor.py | Auto-registered: reflexes/integrity_monitor.py | --json | JSON |
| Mcip Dti Scorer | tools\genesis\reflexes\mcip_dti_scorer.py | Auto-registered: reflexes/mcip_dti_scorer.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Pma Credential Monitor | tools\genesis\reflexes\pma_credential_monitor.py | Auto-registered: reflexes/pma_credential_monitor.py | --json | JSON |
| Pma Int Gap Monitor | tools\genesis\reflexes\pma_int_gap_monitor.py | Auto-registered: reflexes/pma_int_gap_monitor.py | --json | JSON |
| Pmo Option Tracker | tools\genesis\reflexes\pmo_option_tracker.py | Auto-registered: reflexes/pmo_option_tracker.py | --json | JSON |
| Pmo Weekly Report | tools\genesis\reflexes\pmo_weekly_report.py | Auto-registered: reflexes/pmo_weekly_report.py | --json | JSON |
| Skill Security Monitor | tools\genesis\reflexes\skill_security_monitor.py | Auto-registered: reflexes/skill_security_monitor.py | --json | JSON |
| Usage Rollup | tools\genesis\reflexes\usage_rollup.py | Auto-registered: reflexes/usage_rollup.py | --json | JSON |
| Botframework Base | tools\gateway\adapters\botframework_base.py | Auto-registered: adapters/botframework_base.py | --json | JSON |
| Clawhub Connector | tools\databridge\connectors\clawhub_connector.py | Auto-registered: connectors/clawhub_connector.py | --json | JSON |
| Github Connector | tools\databridge\connectors\github_connector.py | Auto-registered: connectors/github_connector.py | --json | JSON |
| Skype Connector | tools\databridge\connectors\skype_connector.py | Auto-registered: connectors/skype_connector.py | --json | JSON |
| Teams Connector | tools\databridge\connectors\teams_connector.py | Auto-registered: connectors/teams_connector.py | --json | JSON |
| Llm Http Auth | tools\ai_augmentation\implementations\llm_http_auth.py | Auto-registered: implementations/llm_http_auth.py | --json | JSON |
| Stig Nlp Extractor | tools\ai_augmentation\agent_readiness\pillars\stig_nlp_extractor.py | Auto-registered: pillars/stig_nlp_extractor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Diagram Analysis | tools\network\diagram_analysis.py | Auto-registered: network/diagram_analysis.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Domain Profiles | tools\docgen\domain_profiles.py | Auto-registered: docgen/domain_profiles.py | --json | JSON |
| Docgen | tools\iqe\adapters\docgen.py | Auto-registered: adapters/docgen.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Advisory Extractor | tools\network\advisory_extractor.py | Auto-registered: network/advisory_extractor.py | --json | JSON |
| Exception Registry | tools\network\exception_registry.py | Auto-registered: network/exception_registry.py | --json | JSON |
| Nqe Client | tools\network\nqe_client.py | Auto-registered: network/nqe_client.py | --json | JSON |
| Nql Translator | tools\network\nql_translator.py | Auto-registered: network/nql_translator.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Nova Hook | tools\gameday\nova_hook.py | Auto-registered: gameday/nova_hook.py | --json | JSON |
| Viz Reporter | tools\gameday\viz_reporter.py | Auto-registered: gameday/viz_reporter.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Iac Review | tools\devops\iac_review.py | Auto-registered: devops/iac_review.py | --json | JSON |
| Firewall Config Review | tools\security\firewall_config_review.py | Auto-registered: security/firewall_config_review.py | --json | JSON |

- [Workflow Forms Canvas (WFC)](manifest/workflow-forms-canvas.md)
- [Agent Detection (AGOV / DET)](manifest/agent-detection.md)
- [AGOV CASE — Agent-Session Forensics](manifest/agent-case-forensics.md)


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Ip Address Space | tools\network\ip_address_space.py | Auto-registered: network/ip_address_space.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Co Learning Store | tools\llm\co_learning_store.py | Auto-registered: llm/co_learning_store.py | --json | JSON |
| Session Store | tools\llm\session_store.py | Auto-registered: llm/session_store.py | --json | JSON |
| Tool Result Sanitizer | tools\llm\tool_result_sanitizer.py | Auto-registered: llm/tool_result_sanitizer.py | --json | JSON |
| Episodic Distiller | tools\genesis\reflexes\episodic_distiller.py | Auto-registered: reflexes/episodic_distiller.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Technical Legal Review | tools\network\technical_legal_review.py | Auto-registered: network/technical_legal_review.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Output Redactor | tools\llm\output_redactor.py | Auto-registered: llm/output_redactor.py | --json | JSON |
| Role Cost Caps | tools\llm\role_cost_caps.py | Auto-registered: llm/role_cost_caps.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Health Data | tools\canvas_health\health_data.py | Auto-registered: canvas_health/health_data.py | --json | JSON |
| Tech Writing Assist | tools\document_intelligence\tech_writing_assist.py | Auto-registered: document_intelligence/tech_writing_assist.py | --json | JSON |
| Api Contract Tester | tools\testing\api_contract_tester.py | Auto-registered: testing/api_contract_tester.py | --json | JSON |
| Dep Health | tools\testing\dep_health.py | Auto-registered: testing/dep_health.py | --json | JSON |
| Flaky Tracker | tools\testing\flaky_tracker.py | Auto-registered: testing/flaky_tracker.py | --json | JSON |
| Visual Regression | tools\testing\visual_regression.py | Auto-registered: testing/visual_regression.py | --json | JSON |
| Second Brain | tools\iqe\adapters\second_brain.py | Auto-registered: adapters/second_brain.py | --json | JSON |
| Api Contract Reflex | tools\genesis\reflexes\api_contract_reflex.py | Auto-registered: reflexes/api_contract_reflex.py | --json | JSON |
| Coherence To Kanban Reflex | tools\genesis\reflexes\coherence_to_kanban_reflex.py | Auto-registered: reflexes/coherence_to_kanban_reflex.py | --json | JSON |
| Critical Task Watchdog Reflex | tools\genesis\reflexes\critical_task_watchdog_reflex.py | Auto-registered: reflexes/critical_task_watchdog_reflex.py | --json | JSON |
| Dead Code Reflex | tools\genesis\reflexes\dead_code_reflex.py | Auto-registered: reflexes/dead_code_reflex.py | --json | JSON |
| Dep Health Reflex | tools\genesis\reflexes\dep_health_reflex.py | Auto-registered: reflexes/dep_health_reflex.py | --json | JSON |
| Flaky Tracker Reflex | tools\genesis\reflexes\flaky_tracker_reflex.py | Auto-registered: reflexes/flaky_tracker_reflex.py | --json | JSON |
| Qa Agent Reflex | tools\genesis\reflexes\qa_agent_reflex.py | Auto-registered: reflexes/qa_agent_reflex.py | --json | JSON |
| Route Perf Reflex | tools\genesis\reflexes\route_perf_reflex.py | Auto-registered: reflexes/route_perf_reflex.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Role Generator | tools\ace\role_generator.py | Auto-registered: ace/role_generator.py | --json | JSON |
| Proactive Advisor | tools\second_brain\proactive_advisor.py | Auto-registered: second_brain/proactive_advisor.py | --json | JSON |
| Role Advisor | tools\second_brain\role_advisor.py | Auto-registered: second_brain/role_advisor.py | --json | JSON |
| Nightly Prep Reflex | tools\genesis\reflexes\nightly_prep_reflex.py | Auto-registered: reflexes/nightly_prep_reflex.py | --json | JSON |
| Thought Leadership Reflex | tools\genesis\reflexes\thought_leadership_reflex.py | Auto-registered: reflexes/thought_leadership_reflex.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Recipe Loader | tools\config\recipe_loader.py | Auto-registered: config/recipe_loader.py | --json | JSON |
| Context Factory | tools\coworkers\context_factory.py | Auto-registered: coworkers/context_factory.py | --json | JSON |
| Chat Memory | tools\document_intelligence\chat_memory.py | Auto-registered: document_intelligence/chat_memory.py | --json | JSON |
| Seed Demo Corpus | tools\document_intelligence\seed_demo_corpus.py | Auto-registered: document_intelligence/seed_demo_corpus.py | --json | JSON |
| Analyze Backlog | tools\kanban\analyze_backlog.py | Auto-registered: kanban/analyze_backlog.py | --json | JSON |
| Balance Scheduler | tools\kanban\balance_scheduler.py | Auto-registered: kanban/balance_scheduler.py | --json | JSON |
| Promote Backlog To Scheduled | tools\kanban\promote_backlog_to_scheduled.py | Auto-registered: kanban/promote_backlog_to_scheduled.py | --json | JSON |
| Seed Acf Adaptations | tools\kanban\seed_acf_adaptations.py | Auto-registered: kanban/seed_acf_adaptations.py | --json | JSON |
| Seed Acf Completion | tools\kanban\seed_acf_completion.py | Auto-registered: kanban/seed_acf_completion.py | --json | JSON |
| Dic Personaliser | tools\second_brain\dic_personaliser.py | Auto-registered: second_brain/dic_personaliser.py | --json | JSON |
| Interactions | tools\second_brain\interactions.py | Auto-registered: second_brain/interactions.py | --json | JSON |
| Objective Tracker | tools\second_brain\objective_tracker.py | Auto-registered: second_brain/objective_tracker.py | --json | JSON |
| Personal Rag | tools\second_brain\personal_rag.py | Auto-registered: second_brain/personal_rag.py | --json | JSON |
| Relationship Health | tools\second_brain\relationship_health.py | Auto-registered: second_brain/relationship_health.py | --json | JSON |
| Retro | tools\second_brain\retro.py | Auto-registered: second_brain/retro.py | --json | JSON |
| Slides Tailor | tools\second_brain\slides_tailor.py | Auto-registered: second_brain/slides_tailor.py | --json | JSON |
| Commitment Watch Reflex | tools\genesis\reflexes\commitment_watch_reflex.py | Auto-registered: reflexes/commitment_watch_reflex.py | --json | JSON |
| Meeting Prep Reflex | tools\genesis\reflexes\meeting_prep_reflex.py | Auto-registered: reflexes/meeting_prep_reflex.py | --json | JSON |
| Objective Tracker Reflex | tools\genesis\reflexes\objective_tracker_reflex.py | Auto-registered: reflexes/objective_tracker_reflex.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Weekly Retro Reflex | tools\genesis\reflexes\weekly_retro_reflex.py | Auto-registered: reflexes/weekly_retro_reflex.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Rfi Engine Runner | tools\govcon\rfi_engine_runner.py | Auto-registered: govcon/rfi_engine_runner.py | --json | JSON |
| Rfi Markdown Validator | tools\govcon\rfi_markdown_validator.py | Auto-registered: govcon/rfi_markdown_validator.py | --json | JSON |
| Rfi Style Engine | tools\govcon\rfi_style_engine.py | Auto-registered: govcon/rfi_style_engine.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Deck Model | tools\viz\deck_model.py | Auto-registered: viz/deck_model.py | --json | JSON |
| Render Diagram Export | tools\viz\render_diagram_export.py | Auto-registered: viz/render_diagram_export.py | --json | JSON |
| Render Html | tools\viz\render_html.py | Auto-registered: viz/render_html.py | --json | JSON |
| Render Png | tools\viz\render_png.py | Auto-registered: viz/render_png.py | --json | JSON |
| Render Pptx | tools\viz\render_pptx.py | Auto-registered: viz/render_pptx.py | --json | JSON |
| Story Builder | tools\viz\story_builder.py | Auto-registered: viz/story_builder.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Coordination | tools\ace\agent_coordination.py | Auto-registered: ace/agent_coordination.py | --json | JSON |
| Chat Trigger | tools\ace\chat_trigger.py | Auto-registered: ace/chat_trigger.py | --json | JSON |
| Llm Step | tools\ace\llm_step.py | Auto-registered: ace/llm_step.py | --json | JSON |
| Markov Sequencer | tools\ace\markov_sequencer.py | Auto-registered: ace/markov_sequencer.py | --json | JSON |
| Message Bus | tools\ace\message_bus.py | Auto-registered: ace/message_bus.py | --json | JSON |
| Step Executor | tools\ace\step_executor.py | Auto-registered: ace/step_executor.py | --json | JSON |
| Team Assembler | tools\ace\team_assembler.py | Auto-registered: ace/team_assembler.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Specialist Consult | tools\govcon\specialist_consult.py | Auto-registered: govcon/specialist_consult.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Intent Router | tools\cortex\intent_router.py | Auto-registered: cortex/intent_router.py | --json | JSON |
| Modernization Routes | tools\document_intelligence\modernization_routes.py | Auto-registered: document_intelligence/modernization_routes.py | --json | JSON |
| Card Bridge | tools\doc_modernization\card_bridge.py | Auto-registered: doc_modernization/card_bridge.py | --json | JSON |
| Redline Drafter | tools\doc_modernization\redline_drafter.py | Auto-registered: doc_modernization/redline_drafter.py | --json | JSON |
| Regen Orchestrator | tools\doc_modernization\regen_orchestrator.py | Auto-registered: doc_modernization/regen_orchestrator.py | --json | JSON |
| Rubric Build Tools | tools\genesis\rubric_build_tools.py | Auto-registered: genesis/rubric_build_tools.py | --json | JSON |
| Cross Process Lease | tools\llm\cross_process_lease.py | Auto-registered: llm/cross_process_lease.py | --json | JSON |
| Pg Lease | tools\llm\pg_lease.py | Auto-registered: llm/pg_lease.py | --json | JSON |
| Proxy Resolver | tools\llm\proxy_resolver.py | Auto-registered: llm/proxy_resolver.py | --json | JSON |
| Rate Gate | tools\llm\rate_gate.py | Auto-registered: llm/rate_gate.py | --json | JSON |
| Conformance Reviewer | tools\testing\conformance_reviewer.py | Auto-registered: testing/conformance_reviewer.py | --json | JSON |
| Pg Pytest Tier | tools\testing\pg_pytest_tier.py | Auto-registered: testing/pg_pytest_tier.py | --json | JSON |
| Pipeline Grader | tools\workflow\pipeline_grader.py | Auto-registered: workflow/pipeline_grader.py | --json | JSON |
| Standards Catalog | tools\iqe\adapters\standards_catalog.py | Auto-registered: adapters/standards_catalog.py | --json | JSON |
| Doc Modernization Sweep | tools\genesis\reflexes\doc_modernization_sweep.py | Auto-registered: reflexes/doc_modernization_sweep.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Prd Common | tools\aiify\prd_common.py | Auto-registered: aiify/prd_common.py | --json | JSON |
| Export Pptx | tools\boundary_canvas\export_pptx.py | Auto-registered: boundary_canvas/export_pptx.py | --json | JSON |
| Drift Bridge | tools\doc_modernization\drift_bridge.py | Auto-registered: doc_modernization/drift_bridge.py | --json | JSON |
| Cost Volume Intake | tools\govcon\cost_volume_intake.py | Auto-registered: govcon/cost_volume_intake.py | --json | JSON |
| Key Personnel | tools\govcon\key_personnel.py | Auto-registered: govcon/key_personnel.py | --json | JSON |
| Build Mode | tools\kanban\build_mode.py | Auto-registered: kanban/build_mode.py | --json | JSON |
| Model Override | tools\kanban\model_override.py | Auto-registered: kanban/model_override.py | --json | JSON |
| Routing Policy | tools\llm\routing_policy.py | Auto-registered: llm/routing_policy.py | --json | JSON |
| Pptx Export | tools\network\pptx_export.py | Auto-registered: network/pptx_export.py | --json | JSON |
| Html Sanitizer | tools\quality\html_sanitizer.py | Auto-registered: quality/html_sanitizer.py | --json | JSON |
| Redaction Util | tools\second_brain\redaction_util.py | Auto-registered: second_brain/redaction_util.py | --json | JSON |
| Stub Gate | tools\security\stub_gate.py | Auto-registered: security/stub_gate.py | --json | JSON |
| Llm Adapter | tools\security_canvas\llm_adapter.py | Auto-registered: security_canvas/llm_adapter.py | --json | JSON |
| Lens Network | tools\oracle\lenses\lens_network.py | Auto-registered: lenses/lens_network.py | --json | JSON |
| Collab Capture | tools\network\routes\collab_capture.py | Auto-registered: routes/collab_capture.py | --json | JSON |
| Import Io | tools\network\routes\import_io.py | Auto-registered: routes/import_io.py | --json | JSON |
| Peering Inventory | tools\network\routes\peering_inventory.py | Auto-registered: routes/peering_inventory.py | --json | JSON |
| Topology Ops | tools\network\routes\topology_ops.py | Auto-registered: routes/topology_ops.py | --json | JSON |
| Twin Migration | tools\network\routes\twin_migration.py | Auto-registered: routes/twin_migration.py | --json | JSON |
| Bgp Hijack Monitor | tools\genesis\reflexes\bgp_hijack_monitor.py | Auto-registered: reflexes/bgp_hijack_monitor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Sme Gap Detector | tools\ace\sme_gap_detector.py | Auto-registered: ace/sme_gap_detector.py | --json | JSON |
| Seed Cdp Kanban | tools\kanban\seed_cdp_kanban.py | Auto-registered: kanban/seed_cdp_kanban.py | --json | JSON |
| Seed Dwo Kanban | tools\kanban\seed_dwo_kanban.py | Auto-registered: kanban/seed_dwo_kanban.py | --json | JSON |
| Seed Fga Kanban | tools\kanban\seed_fga_kanban.py | Auto-registered: kanban/seed_fga_kanban.py | --json | JSON |
| Seed Oss02 Kanban | tools\kanban\seed_oss02_kanban.py | Auto-registered: kanban/seed_oss02_kanban.py | --json | JSON |
| App Red Team | tools\security\app_red_team.py | Auto-registered: security/app_red_team.py | --json | JSON |
| Redteam Scope | tools\security\redteam_scope.py | Auto-registered: security/redteam_scope.py | --json | JSON |
| Dic Inbox Sweep | tools\genesis\reflexes\dic_inbox_sweep.py | Auto-registered: reflexes/dic_inbox_sweep.py | --json | JSON |
| Memory Maintenance Reflex | tools\genesis\reflexes\memory_maintenance_reflex.py | Auto-registered: reflexes/memory_maintenance_reflex.py | --json | JSON |
| Observability Retention | tools\genesis\reflexes\observability_retention.py | Auto-registered: reflexes/observability_retention.py | --json | JSON |
| Odc Coverage Refresh | tools\genesis\reflexes\odc_coverage_refresh.py | Auto-registered: reflexes/odc_coverage_refresh.py | --json | JSON |
| Change Control | tools\doc_modernization\packs\change_control.py | Auto-registered: packs/change_control.py | --json | JSON |
| Evidence Currency | tools\doc_modernization\packs\evidence_currency.py | Auto-registered: packs/evidence_currency.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Credibility | tools\bom\credibility.py | Auto-registered: bom/credibility.py | --json | JSON |
| Derivative | tools\bom\derivative.py | Auto-registered: bom/derivative.py | --json | JSON |
| Export Categorized | tools\bom\export_categorized.py | Auto-registered: bom/export_categorized.py | --json | JSON |
| Export Xlsx | tools\bom\export_xlsx.py | Auto-registered: bom/export_xlsx.py | --json | JSON |
| Extract Grid | tools\bom\extract_grid.py | Auto-registered: bom/extract_grid.py | --json | JSON |
| Forensics | tools\bom\forensics.py | Auto-registered: bom/forensics.py | --json | JSON |
| Formula Graph | tools\bom\formula_graph.py | Auto-registered: bom/formula_graph.py | --json | JSON |
| Seed Bom Concord | tools\kanban\seed_bom_concord.py | Auto-registered: kanban/seed_bom_concord.py | --json | JSON |
| Brand Deck | tools\slides\brand_deck.py | Auto-registered: slides/brand_deck.py | --json | JSON |
- [Agent Detection (AGOV / DET)](manifest/agent-detection.md)
