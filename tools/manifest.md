# Tools Manifest

> Master list of all tools. Check here before writing a new script.
> Split into shards by topic (2026-04-14). Original: `tools/manifest.md.bak`.

## Index

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
- [Deploy](manifest/deploy.md)
- [Infrastructure](manifest/infrastructure.md)
- [Knowledge & Self-Healing](manifest/knowledge-self-healing.md)
- [Monitoring](manifest/monitoring.md)
- [Dashboard](manifest/dashboard.md)
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
- [Bedrock Client (Opus 4.6 Multi-Agent — Phase A)](manifest/bedrock-client.md)
- [Multi-Agent Orchestration (Opus 4.6 Multi-Agent — Phase B)](manifest/multi-agent-orchestration.md)
- [Agent Collaboration (Opus 4.6 Multi-Agent — Phase C)](manifest/agent-collaboration.md)
- [Observability Hooks (Phase 39)](manifest/observability-hooks.md)
- [NLQ Compliance Queries (Phase 40)](manifest/nlq-compliance-queries.md)
- [Git Worktree Parallel CI/CD (Phase 41)](manifest/git-worktree-parallel-ci-cd.md)
- [Framework Planning Commands (Phase 42)](manifest/framework-planning-commands.md)
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
- [Auto-Registered (Coherence Fix)](manifest/auto-registered.md)
- [Air-Gap Mode (OPT-51/OPT-61)](manifest/air-gap-mode.md)
- [AI/ML Model Canvas (AIMC)](manifest/aiml-canvas.md)
- [Design Canvases (7-Canvas Suite)](manifest/design-canvases.md)
- [Migration Canvas](manifest/migration-canvas.md)
- [Canvas Auto-Remediation](manifest/canvas-auto-remediation.md)
- [BDC cATO Twin (Phase BDC-1)](manifest/bdc-cato-twin.md)
- [IDC IaC Twin (Phase IDC-1)](manifest/idc-twin.md)
- [Agent Adapters (OPT-71)](manifest/agent-adapters.md)
- [Skill Invocation (OPT-41, 2026-04-12)](manifest/skill-invocation.md)
- [ANVIL Headless Commands (OPT-42, 2026-04-12)](manifest/anvil-headless-commands.md)
- [Dashboard UX Enhancements (OPT-68, 2026-04-12)](manifest/dashboard-ux-enhancements.md)
- [Manifest Gap Fill (2026-04-12)](manifest/manifest-gap-fill.md)
- [AISG — AI Strategy Guide Tools](manifest/aisg.md)
- [IQE — Internal Query Engine](manifest/iqe-query-engine.md)
- [Kanban System](manifest/kanban.md)
- [Regulatory Foresight Engine (D352 — pint-regfore)](manifest/regulatory-foresight-engine.md)
- [Voice-of-Customer (VOC) Signal Capture (pint-voc)](manifest/voc.md)
- [Strategos — DIB Supply Chain & Strategy Intelligence](manifest/strategos.md)
- [Strategos Chat — Floating Analyst Chat Panel](manifest/strategos-chat.md)
- [TTX Engine — Tabletop Exercise (AI GameDay)](manifest/ttx.md)
- [Unclassified (auto-added)](manifest/unclassified.md)
- [DDC Data Science — Explore, Query Sandbox, Quality Rules](manifest/ddc-data-science.md)
- [System Graph — Federated Sigma.js Graph](manifest/system-graph.md)


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
| Alphadesk Trap Scenarios | tools\genesis\reflexes\alphadesk_trap_scenarios.py | Auto-registered: reflexes/alphadesk_trap_scenarios.py | --json | JSON |
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
| Forge Academy Oracle | tools\genesis\reflexes\forge_academy_oracle.py | Auto-registered: reflexes/forge_academy_oracle.py | --json | JSON |
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
| Sim Lifecycle E2E | tools\ttx\scenarios\sim_lifecycle_e2e.py | Auto-registered: scenarios/sim_lifecycle_e2e.py | --json | JSON |
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
