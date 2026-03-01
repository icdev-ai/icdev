# ICDEV Documentation

ICDEV is a meta-builder that autonomously builds Gov/DoD applications with full SDLC compliance. This documentation covers everything from getting started to production operations.

---

## Getting Started

| Guide | Description |
|-------|-------------|
| [Quickstart](dx/quickstart.md) | Get running in 5 minutes |
| [Integration Tiers](dx/integration-tiers.md) | Three ways to use ICDEV: Invisible, Conversational, Programmatic |
| [icdev.yaml Specification](dx/icdev-yaml-spec.md) | Project manifest reference — one file configures everything |

---

## Developer Experience (DX)

Guides for day-to-day development with ICDEV.

| Guide | Description |
|-------|-------------|
| [DX Overview](dx/README.md) | Developer experience index and integration tier comparison |
| [Claude Code Guide](dx/claude-code-guide.md) | Using natural language to drive ICDEV workflows |
| [AI Companion Guide](dx/companion-guide.md) | Multi-tool setup — Claude Code, Codex, Gemini, Copilot, Cursor, and 5 more |
| [LLM Routing Guide](dx/llm-routing-guide.md) | Per-task LLM provider selection — assign Claude, GPT, Gemini, or local models to specific functions |
| [CI/CD Integration](dx/ci-cd-integration.md) | Pipeline auto-attach for GitHub Actions and GitLab CI |
| [Dev Profiles](dx/dev-profiles.md) | Tenant coding standards, style enforcement, and personalization |
| [SDK Reference](dx/sdk-reference.md) | Programmatic Python API for custom tooling |
| [Unified MCP Setup](dx/unified-mcp-setup.md) | Single MCP server for all 225 tools — setup for VS Code, Cursor, Codex, Gemini, JetBrains, and more |

---

## Architecture

Deep-dive reference guides for ICDEV's internal architecture.

| Guide | Description |
|-------|-------------|
| [GOTCHA Framework](architecture/gotcha-framework.md) | 6-layer agentic architecture — Goals, Orchestration, Tools, Context, Hard Prompts, Args |
| [Multi-Agent System](architecture/multi-agent-system.md) | 15 agents, A2A protocol, MCP servers, DAG workflows, domain authority |
| [Database Schema](architecture/database-schema.md) | 5 databases, 183+ tables, migrations, backup/restore, append-only audit |
| [Compliance Framework](architecture/compliance-framework.md) | 26 frameworks, dual-hub crosswalk, BaseAssessor pattern, security gates |

---

## Operations

Guides for running ICDEV in production.

| Guide | Description |
|-------|-------------|
| [Dashboard Guide](operations/dashboard-guide.md) | Flask web UI — 22+ pages, auth, RBAC, BYOK, real-time updates, keyboard shortcuts |
| [SaaS Administration](operations/saas-admin-guide.md) | Multi-tenant platform — tenants, API gateway, auth, subscriptions, Helm deployment |
| [Deployment Guide](operations/deployment-guide.md) | Docker, K8s, Helm, multi-cloud Terraform, auto-scaling, air-gapped installation |
| [Security Operations](operations/security-operations-guide.md) | Security scanning, AI security, compliance gates, ZTA, self-healing, incident response |

---

## Administration

Guides for managing ICDEV subsystems.

| Guide | Description |
|-------|-------------|
| [Marketplace Guide](admin/marketplace-guide.md) | Federated GOTCHA asset marketplace — publish, install, review, federation sync |
| [Gateway Guide](admin/gateway-guide.md) | Remote Command Gateway — messaging channels, user binding, air-gapped mode |
| [Monitoring Guide](admin/monitoring-guide.md) | Heartbeat daemon, auto-resolver, distributed tracing, provenance, XAI |

---

## Runbooks

Step-by-step operational procedures.

| Guide | Description |
|-------|-------------|
| [Backup & Restore](runbooks/backup-restore.md) | Database backup, restore, encryption, migration, disaster recovery |
| [Troubleshooting](runbooks/troubleshooting.md) | Diagnostics, common issues, circuit breakers, log locations, escalation |

---

## Feature Documentation

Detailed documentation for each ICDEV capability phase.

### Core Platform (Phases 1-9)

| Phase | Guide | Description |
|-------|-------|-------------|
| 1 | [GOTCHA Framework](features/phase-01-gotcha-framework.md) | 6-layer deterministic agentic architecture |
| 2 | [ATLAS Build Workflow](features/phase-02-atlas-build-workflow.md) | 5-phase build: Architect, Trace, Link, Assemble, Stress-test |
| 3 | [TDD/BDD Testing](features/phase-03-tdd-bdd-testing.md) | RED-GREEN-REFACTOR cycle with 6-language support |
| 4 | [NIST Compliance](features/phase-04-nist-compliance.md) | NIST 800-53 Rev 5 control mapping and SSP generation |
| 5 | [Security Scanning](features/phase-05-security-scanning.md) | SAST, dependency audit, secret detection, container scanning |
| 6 | [Infrastructure & Deployment](features/phase-06-infrastructure-deployment.md) | Terraform, Ansible, K8s, pipeline generation |
| 7 | [Code Review Gates](features/phase-07-code-review-gates.md) | Enforced review gates with security checks |
| 8 | [Self-Healing](features/phase-08-self-healing.md) | Pattern detection, root cause analysis, auto-remediation |
| 9 | [Monitoring & Observability](features/phase-09-monitoring-observability.md) | Log analysis, metrics, alerts, health checks |

### Dashboard & UX (Phases 10, 29-32)

| Phase | Guide | Description |
|-------|-------|-------------|
| 10 | [Dashboard Web UI](features/phase-10-dashboard-web-ui.md) | Flask dashboard with 22+ pages |
| 29 | [Proactive Monitoring](features/phase-29-proactive-monitoring.md) | Heartbeat daemon, auto-resolver, skill injection, time-decay memory |
| 30 | [Dashboard Auth](features/phase-30-dashboard-auth.md) | API key auth, RBAC, BYOK, usage tracking |
| 31 | [Dashboard UX (Low Impact)](features/phase-31-dashboard-ux-low-impact.md) | Glossary, tooltips, breadcrumbs, accessibility |
| 32 | [Dashboard UX (Medium Impact)](features/phase-32-dashboard-ux-medium-impact.md) | Charts, tables, tour, live updates, batch ops, keyboard shortcuts |

### Multi-Agent & Orchestration (Phases 11-13, 39-42)

| Phase | Guide | Description |
|-------|-------|-------------|
| 11 | [Multi-Agent Architecture](features/phase-11-multi-agent-architecture.md) | 15 agents, A2A protocol, 19 MCP servers |
| 12 | [Integration Testing](features/phase-12-integration-testing.md) | 9-step pipeline: syntax, lint, unit, BDD, SAST, E2E, vision, acceptance, gates |
| 13 | [CI/CD Integration](features/phase-13-cicd-integration.md) | GitHub + GitLab dual-platform automation |
| 39 | [Observability & Operations](features/phase-39-observability-operations.md) | Hook-based monitoring, agent executor, SIEM forwarding |
| 40 | [NLQ Compliance Queries](features/phase-40-nlq-compliance-queries.md) | Natural language to SQL for compliance queries |
| 41 | [Parallel CI/CD](features/phase-41-parallel-cicd.md) | Git worktree isolation, GitLab task routing |
| 42 | [Framework Planning](features/phase-42-framework-planning.md) | Language-specific build commands, 12 Leverage Points |

### Compliance & Security (Phases 14-17, 20, 23-26, 37, 45-46)

| Phase | Guide | Description |
|-------|-------|-------------|
| 14 | [Secure by Design & IV&V](features/phase-14-secure-by-design-ivv.md) | CISA SbD, IEEE 1012 IV&V, DoDI 5000.87 DES |
| 15 | [Maintenance Audit](features/phase-15-maintenance-audit.md) | Dependency scanning, CVE checks, SLA enforcement |
| 16 | [ATO Acceleration](features/phase-16-ato-acceleration.md) | FedRAMP, CMMC, OSCAL, eMASS, cATO |
| 17 | [Multi-Framework Compliance](features/phase-17-multi-framework-compliance.md) | Dual-hub crosswalk, 26 frameworks |
| 20 | [FIPS Security Categorization](features/phase-20-fips-security-categorization.md) | FIPS 199/200, SP 800-60, CNSSI 1253 |
| 23 | [Universal Compliance Platform](features/phase-23-universal-compliance-platform.md) | 10 data categories, multi-regime assessment |
| 24 | [DevSecOps Pipeline Security](features/phase-24-devsecops-pipeline-security.md) | Maturity assessment, policy-as-code, attestation |
| 25 | [Zero Trust Architecture](features/phase-25-zero-trust-architecture.md) | 7-pillar ZTA, NIST 800-207, service mesh |
| 26 | [DoD MOSA](features/phase-26-dod-mosa.md) | Modular Open Systems Approach (10 U.S.C. 4401) |
| 37 | [MITRE ATLAS Integration](features/phase-37-mitre-atlas-integration.md) | AI threat defense, prompt injection, red teaming |
| 45 | [OWASP Agentic Security](features/phase-45-owasp-agentic-security.md) | Behavioral drift, tool chain validation, trust scoring |
| 46 | [Observability & XAI](features/phase-46-observability-traceability-xai.md) | Distributed tracing, provenance, AgentSHAP, XAI |

### MBSE & Requirements (Phase 18, RICOAS)

| Phase | Guide | Description |
|-------|-------|-------------|
| 18 | [MBSE Integration](features/phase-18-mbse-integration.md) | SysML, DOORS NG, digital thread, model-code sync |

### Agentic & Generation (Phases 19, 21-22, 27-28, 33-36, 43-44)

| Phase | Guide | Description |
|-------|-------|-------------|
| 19 | [Agentic Generation](features/phase-19-agentic-generation.md) | Mini-ICDEV clone child app generation |
| 21 | [SaaS Multi-Tenancy](features/phase-21-saas-multi-tenancy.md) | Multi-tenant platform, API gateway, tenant isolation |
| 22 | [Federated Marketplace](features/phase-22-federated-gotcha-marketplace.md) | GOTCHA asset sharing with 9-gate security |
| 27 | [CLI Capabilities](features/phase-27-cli-capabilities.md) | Optional CLI toggles for headless/scripted execution |
| 28 | [Remote Command Gateway](features/phase-28-remote-command-gateway.md) | Messaging channel integration (5 channels) |
| 33 | [Modular Installation](features/phase-33-modular-installation.md) | Interactive wizard, 10 deployment profiles |
| 34 | [Dev Profiles](features/phase-34-dev-profiles.md) | 5-layer cascade, role-based locks, auto-detection |
| 35 | [Innovation Engine](features/phase-35-innovation-engine.md) | Autonomous self-improvement pipeline |
| 36 | [Evolutionary Intelligence](features/phase-36-evolutionary-intelligence.md) | Parent-child genome, capability propagation |
| 38 | [Cloud-Agnostic Architecture](features/phase-38-cloud-agnostic-architecture.md) | 6 CSPs, multi-cloud Terraform, LLM routing |
| 43 | [Cross-Language Translation](features/phase-43-cross-language-translation.md) | 5-phase hybrid translation, 30 language pairs |
| 44 | [Innovation Adaptation](features/phase-44-innovation-adaptation.md) | Multi-stream chat, extensions, memory consolidation |
| 47 | [Unified MCP Gateway](features/phase-47-unified-mcp-gateway.md) | Single MCP server — 225 tools, lazy loading, 55 new gap handlers |

---

## Documentation Statistics

| Category | Count |
|----------|-------|
| Developer Experience (DX) | 11 guides |
| Architecture | 4 guides |
| Operations | 4 guides |
| Administration | 3 guides |
| Runbooks | 2 guides |
| Feature Documentation | 47 phase guides |
| **Total** | **71 documents** |

---

## Quick Links

- **New to ICDEV?** Start with the [Quickstart](dx/quickstart.md)
- **Setting up your AI tool?** See the [AI Companion Guide](dx/companion-guide.md)
- **Deploying to production?** See the [Deployment Guide](operations/deployment-guide.md)
- **Managing tenants?** See the [SaaS Administration](operations/saas-admin-guide.md)
- **Troubleshooting?** See the [Troubleshooting Runbook](runbooks/troubleshooting.md)
- **Understanding the architecture?** Start with [GOTCHA Framework](architecture/gotcha-framework.md)
