# Tools Manifest

> Master list of all tools. Check here before writing a new script.
> Split into shards by topic (2026-04-14). Original: `tools/manifest.md.bak`.

## Index

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
- [AlphaDesk Trading Engine](manifest/alphadesk-trading-engine.md)
- [Auto-Registered (Coherence Fix)](manifest/auto-registered.md)
- [Air-Gap Mode (OPT-51/OPT-61)](manifest/air-gap-mode.md)
- [Design Canvases (7-Canvas Suite)](manifest/design-canvases.md)
- [Migration Canvas](manifest/migration-canvas.md)
- [Canvas Auto-Remediation](manifest/canvas-auto-remediation.md)
- [Agent Adapters (OPT-71)](manifest/agent-adapters.md)
- [Skill Invocation (OPT-41, 2026-04-12)](manifest/skill-invocation.md)
- [ANVIL Headless Commands (OPT-42, 2026-04-12)](manifest/anvil-headless-commands.md)
- [Dashboard UX Enhancements (OPT-68, 2026-04-12)](manifest/dashboard-ux-enhancements.md)
- [Manifest Gap Fill (2026-04-12)](manifest/manifest-gap-fill.md)


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|

| Pdf Import | tools\network\pdf_import.py | Auto-registered: network/pdf_import.py | --json | JSON |
| Snapshot Builder | tools\trading\market_intel\snapshot_builder.py | Auto-registered: market_intel/snapshot_builder.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Schedule Enterprise Frontend Plan | tools\scripts\schedule_enterprise_frontend_plan.py | Auto-registered: scripts/schedule_enterprise_frontend_plan.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Schedule Alphadesk News Plan | tools\scripts\schedule_alphadesk_news_plan.py | Auto-registered: scripts/schedule_alphadesk_news_plan.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Wheel Vendor | tools\airgap\wheel_vendor.py | Auto-registered: airgap/wheel_vendor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Self Debug | tools\workflow\self_debug.py | Auto-registered: workflow/self_debug.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Rss Ingestor | tools\trading\news\rss_ingestor.py | Auto-registered: news/rss_ingestor.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Scenario Matcher | tools\trading\news\scenario_matcher.py | Auto-registered: news/scenario_matcher.py | --json | JSON |
- [Unclassified (auto-added)](manifest/unclassified.md)


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| News Reasoner | tools\trading\news\news_reasoner.py | Auto-registered: news/news_reasoner.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Crisis Fingerprints | tools\trading\market_intel\crisis_fingerprints.py | Auto-registered: market_intel/crisis_fingerprints.py | --json | JSON |
| Cross Asset Divergence | tools\trading\market_intel\cross_asset_divergence.py | Auto-registered: market_intel/cross_asset_divergence.py | --json | JSON |


## Auto-Registered (Coherence Fix)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Lens News Intelligence | tools\trading\oracle\lens_news_intelligence.py | Auto-registered: oracle/lens_news_intelligence.py | --json | JSON |
| Lens Portfolio Stress | tools\trading\oracle\lens_portfolio_stress.py | Auto-registered: oracle/lens_portfolio_stress.py | --json | JSON |
| Lens Regime Trajectory | tools\trading\oracle\lens_regime_trajectory.py | Auto-registered: oracle/lens_regime_trajectory.py | --json | JSON |
| Lens Signal Convergence | tools\trading\oracle\lens_signal_convergence.py | Auto-registered: oracle/lens_signal_convergence.py | --json | JSON |
