# CUI // SP-CTI

# ICDEV — Intelligent Coding Development Platform

ICDEV is an AI-powered meta-builder that autonomously builds Gov/DoD applications using the GOTCHA (Goals, Orchestration, Tools, Args, Context, Hard Prompts) framework and the ATLAS workflow. It handles the full SDLC with TDD/BDD, NIST 800-53 RMF compliance, multi-agent architecture across 15 specialized agents, and self-healing capabilities for classification levels up to SECRET (IL6).

## Classification

**CUI // SP-CTI** — This repository contains Controlled Unclassified Information. All generated artifacts include classification markings appropriate to impact level (CUI for IL4/IL5, SECRET for IL6). Markings are applied at generation time via `classification_manager.py`.

## Quick Start

### Option 1: Interactive Setup (Recommended)
```bash
python tools/installer/installer.py --interactive
```

### Option 2: Profile-Based Setup
```bash
# DoD Team (FedRAMP High + CMMC + MOSA + ZTA)
python tools/installer/installer.py --profile dod_team --platform k8s

# ISV Startup (minimal, no compliance)
python tools/installer/installer.py --profile isv_startup --platform docker

# Healthcare (HIPAA + HITRUST + SOC 2)
python tools/installer/installer.py --profile healthcare
```

### Option 3: Docker Compose
```bash
docker-compose up -d
```

### Manual Setup

1. **Clone and install dependencies:**
   ```bash
   git clone <repository-url>
   cd ICDev
   pip install -r requirements.txt
   ```

2. **Initialize the framework** (first run):
   Run `/initialize` in Claude Code to set up all directories, manifests, memory files, and databases.

3. **Start the dashboard:**
   ```bash
   python tools/dashboard/app.py
   ```
   Navigate to `http://localhost:5000` for the web UI.

4. **Run the test suite:**
   ```bash
   pytest tests/ -v --tb=short
   ```

5. **Read `CLAUDE.md`** for the full architecture documentation, all available commands, and operational instructions.

## Architecture

### GOTCHA Framework — 6-Layer Agentic System

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions — what to achieve, which tools to use, expected outputs |
| **Orchestration** | *(AI agent)* | Read goal, decide tool order, apply args, reference context, handle errors |
| **Tools** | `tools/` | Deterministic Python scripts, one job each |
| **Args** | `args/` | YAML/JSON behavior settings — change behavior without editing goals or tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates |

### Multi-Agent Architecture (15 Agents, 3 Tiers)

- **Core Tier:** Orchestrator (8443), Architect (8444)
- **Domain Tier:** Builder (8445), Compliance (8446), Security (8447), Infrastructure (8448), MBSE (8451), Modernization (8452), Requirements Analyst (8453), Supply Chain (8454), Simulation (8455), DevSecOps & ZTA (8457), Gateway (8458)
- **Support Tier:** Knowledge (8449), Monitor (8450)

Agents communicate via A2A protocol (JSON-RPC 2.0 over mutual TLS within K8s).

### Compliance Frameworks (20+)

NIST 800-53 Rev 5, FedRAMP (Moderate/High), NIST 800-171, CMMC Level 2/3, DoD CSSP (DI 8530.01), CISA Secure by Design, IEEE 1012 IV&V, DoDI 5000.87 DES, FIPS 199/200, CNSSI 1253, CJIS, HIPAA, HITRUST CSF v11, SOC 2 Type II, PCI DSS v4.0, ISO/IEC 27001:2022, NIST SP 800-207 (ZTA), DoD MOSA (10 U.S.C. section 4401), MITRE ATLAS, OWASP LLM Top 10, NIST AI RMF, ISO/IEC 42001.

Unified via a dual-hub crosswalk engine: NIST 800-53 (US hub) + ISO 27001 (international hub) with bidirectional bridge.

### AI Security (Phase 37 — MITRE ATLAS Integration)

- **Prompt Injection Detection** — 5 detection categories (role hijacking, delimiter attacks, instruction injection, data exfiltration, encoded payloads) with confidence-based action thresholds
- **AI Telemetry** — Privacy-preserving audit logging with SHA-256 hashing (stores fingerprints, not plaintext)
- **AI BOM (Bill of Materials)** — Catalogs all AI/ML components (LLM providers, AI frameworks, MCP servers) for supply chain visibility
- **ATLAS Red Teaming** — Opt-in adversarial testing against 6 ATLAS techniques (prompt injection, jailbreaking, context poisoning, data leakage, poisoned agent tool, model evasion)
- **4-Framework AI Assessment** — MITRE ATLAS (35 mitigations), OWASP LLM Top 10, NIST AI RMF (4 functions, 12 subcategories), ISO/IEC 42001
- **Marketplace Hardening** — Gates 8-9: prompt injection scan (blocking) + behavioral sandbox (warning)

### Evolutionary Intelligence (Phase 36)

Parent-child capability genome lifecycle: Discover, Evaluate (7-dimension scoring incl. security_assessment), Stage (isolated worktree testing), Approve (HITL), Propagate (targeted/canary/fleet), Verify (72-hour stability window), Absorb (genome versioning with SHA-256 content hashing). Cross-pollination brokered through parent with prompt injection scanning at every ingestion boundary.

### Cloud-Agnostic Architecture (Phase 38)

- **6 Cloud Service Providers:** AWS GovCloud, Azure Government, GCP Assured Workloads, OCI Government Cloud, IBM Cloud for Government (IC4G), Local (air-gapped)
- **6 Service ABCs x 6 CSPs = 36 Implementations:** Secrets, Storage, KMS, Monitoring, IAM, Container Registry
- **4 Cloud Modes:** Commercial, Government, On-Premises, Air-Gapped — single config field drives endpoint selection
- **Multi-Cloud LLM:** Amazon Bedrock, Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx.ai, Ollama (local)
- **CSP-Specific IaC:** Terraform generators for AWS, Azure, GCP, OCI, IBM, and on-prem
- **Region Validation:** Compliance certification checks before deployment (FedRAMP, FIPS 140-2, DoD IL)

### Innovation Engine (Phase 35)

Autonomous self-improvement: web intelligence scanning (GitHub, NVD, Stack Overflow, Hacker News), 5-dimension signal scoring, 5-stage compliance triage, trend detection, solution generation, introspective analysis, competitive intel, standards monitoring.

### SaaS Multi-Tenancy

Per-tenant database isolation, 3 authentication methods (API key, OAuth 2.0/OIDC, CAC/PIV), subscription tiers (Starter/Professional/Enterprise), REST + MCP Streamable HTTP transport. Helm chart for on-prem deployment.

### Universal AI Coding Companion

Generates instruction files, MCP configs, and skill translations for 10 AI coding tools: Claude Code, Codex, Gemini, Copilot, Cursor, Windsurf, Amazon Q, JetBrains/Junie, Cline, Aider.

### Supported Languages (6 First-Class)

Python, Java, JavaScript/TypeScript, Go, Rust, C#

Each language has full support for scaffolding, linting, formatting, SAST, dependency auditing, BDD steps, and code generation.

## Testing

```bash
# Unit and integration tests (2100+ tests across 40+ test files)
pytest tests/ -v --tb=short

# BDD scenario tests
behave features/

# E2E browser tests (Playwright)
python tools/testing/e2e_runner.py --run-all

# Full health check
python tools/testing/health_check.py

# Security scanning
python tools/security/sast_runner.py --project-dir .
python tools/security/secret_detector.py --project-dir .
```

The testing pipeline follows a 9-step process: py_compile, Ruff, pytest, behave/Gherkin, Bandit SAST, Playwright E2E, vision validation, acceptance validation, and security/compliance gates.

## Key Commands

```bash
# Initialize database (146 tables)
python tools/db/init_icdev_db.py

# Compliance artifacts
python tools/compliance/ssp_generator.py --project-id "proj-123"
python tools/compliance/sbom_generator.py --project-dir .

# Multi-framework assessment (all applicable frameworks)
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json

# AI security assessment (MITRE ATLAS)
python tools/compliance/atlas_assessor.py --project-id "proj-123" --json
python tools/security/ai_bom_generator.py --project-id "proj-123" --project-dir . --json
python tools/compliance/atlas_report_generator.py --project-id "proj-123" --json

# Cloud mode management
python tools/cloud/cloud_mode_manager.py --status --json
python tools/cloud/cloud_mode_manager.py --validate --json
python tools/cloud/region_validator.py validate --csp aws --region us-gov-west-1 --frameworks fedramp_high --json

# TDD workflow
python tools/builder/test_writer.py --feature "user auth" --project-dir .
python tools/builder/code_generator.py --test-file test.py --project-dir .

# Innovation Engine
python tools/innovation/innovation_manager.py --run --json

# Universal AI Companion setup
python tools/dx/companion.py --setup --write

# Start API gateway
python tools/saas/api_gateway.py --port 8443 --debug

# Start dashboard
python tools/dashboard/app.py
```

See `CLAUDE.md` for the complete command reference (800+ commands across 38 phases).

## Dashboard

The web dashboard (`http://localhost:5000`) provides:

| Page | Purpose |
|------|---------|
| `/` | Home with auto-notifications |
| `/projects` | Project listing with friendly timestamps |
| `/agents` | Agent registry with heartbeat age |
| `/monitoring` | System monitoring with status icons |
| `/children` | Child application registry (health, genome version, capabilities) |
| `/wizard` | Getting Started wizard (3 questions, workflow recommendation) |
| `/query` | Natural language compliance queries |
| `/activity` | Merged activity feed (audit + hook events) |
| `/usage` | Usage tracking + cost dashboard |
| `/dev-profiles` | Dev profile management (cascade, lock, versioning) |
| `/gateway` | Remote Command Gateway admin |
| `/batch` | Batch operations (ATO, Security, Compliance, Build workflows) |

Auth: per-user API keys (SHA-256 hashed), Flask signed sessions, 5 RBAC roles (admin, pm, developer, isso, co).

## Project Structure

```
goals/          -- Workflow process definitions (40+ goals)
tools/          -- Deterministic Python scripts (organized by domain)
  builder/      -- TDD code gen, scaffolding, dev profiles, agentic generation
  cloud/        -- CSP provider ABCs, factory, health checker, region validator
  compliance/   -- 20+ framework assessors, crosswalk engine, report generators
  dashboard/    -- Flask web UI with auth, RBAC, batch ops
  db/           -- Database init, migrations, backup/restore
  devsecops/    -- DevSecOps profiles, ZTA maturity, policy-as-code
  dx/           -- Universal AI companion (10 tools), session context
  infra/        -- Terraform generators (6 CSPs), Ansible, K8s, pipelines
  innovation/   -- Innovation Engine (web scanning, signal scoring, triage)
  llm/          -- Multi-cloud LLM router (6 providers), embeddings
  marketplace/  -- Federated GOTCHA asset marketplace
  mcp/          -- 14 MCP stdio servers for Claude Code
  registry/     -- Capability genome, evaluator, propagation, telemetry
  requirements/ -- RICOAS intake, gap detection, SAFe decomposition
  security/     -- SAST, prompt injection, AI telemetry, ATLAS red team, AI BOM
  simulation/   -- Digital Program Twin, Monte Carlo, COA generation
args/           -- YAML/JSON configuration files (30+ config files)
context/        -- Static reference material, compliance catalogs, language profiles
hardprompts/    -- Reusable LLM instruction templates
memory/         -- Session logs and long-term memory
data/           -- SQLite databases (icdev.db, platform.db, memory.db, activity.db)
tests/          -- pytest test suite (2100+ tests)
features/       -- BDD/Gherkin scenario tests
specs/          -- Feature specifications (per-issue directories)
k8s/            -- Kubernetes manifests (deployments, HPA, PDB, network policies)
docker/         -- STIG-hardened Dockerfiles (11 agent images)
deploy/         -- Helm chart and air-gapped installer
docs/           -- Feature specs, DX guide, companion guide
```

## Databases

| Database | Tables | Purpose |
|----------|--------|---------|
| `data/icdev.db` | 146 | Main operational DB: projects, agents, compliance, security, marketplace, innovation, evolution |
| `data/platform.db` | 6 | SaaS platform: tenants, users, API keys, subscriptions |
| `data/memory.db` | 3 | Memory system: entries, daily logs, access log |
| `data/activity.db` | 1 | Task tracking |
| `data/tenants/*.db` | Per-tenant | Isolated DB per tenant (strongest isolation) |

## Deployment

### Kubernetes (Production)
```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/
helm install icdev deploy/helm/ --set autoscaling.enabled=true
```

### Air-Gapped / On-Premises
```bash
python deploy/offline/install.py
```

### Docker (Development)
```bash
docker-compose up -d
```

### Multi-Cloud Terraform
```bash
python tools/infra/terraform_generator.py --project-id "proj-123"           # AWS GovCloud
python tools/infra/terraform_generator_azure.py --project-id "proj-123"     # Azure Government
python tools/infra/terraform_generator_gcp.py --project-id "proj-123"       # GCP Assured Workloads
python tools/infra/terraform_generator_oci.py --project-id "proj-123"       # OCI Government
python tools/infra/terraform_generator_ibm.py --project-id "proj-123"       # IBM Cloud
python tools/infra/terraform_generator_onprem.py --project-id "proj-123"    # On-premises
```

## Deployment Profiles

| Profile | Modules | Compliance | Cloud Mode |
|---------|---------|------------|------------|
| ISV Startup | 7 core | None | Commercial |
| ISV Enterprise | 11 | FedRAMP Moderate | Commercial/Government |
| SI Consulting | 5 + RICOAS | FedRAMP + CMMC | Government |
| SI Enterprise | 14 | FedRAMP High + CMMC + CJIS | Government |
| DoD Team | 14 | FedRAMP High + CMMC + FIPS + cATO | Government |
| Healthcare | 9 | HIPAA + HITRUST + SOC 2 | Commercial/Government |
| Financial | 9 | PCI DSS + SOC 2 + ISO 27001 | Commercial/Government |
| Law Enforcement | 9 | CJIS + FIPS 199/200 | Government |
| GovCloud Full | ALL | ALL | Government |
| Custom | 3 minimum | User choice | User choice |

## Phase History

| Phase | Name | Status |
|-------|------|--------|
| 1-12 | Core Framework (GOTCHA, ATLAS, TDD, Compliance, Security, Infrastructure, Knowledge, Monitoring, Dashboard, CI/CD, Multi-Agent) | Complete |
| 13-14 | SbD/IV&V, Maintenance Audit | Complete |
| 15-16 | ATO Acceleration, App Modernization | Complete |
| 17 | Multi-Framework Compliance (FedRAMP, CMMC, OSCAL, eMASS, cATO) | Complete |
| 18 | MBSE Integration (SysML, DOORS NG, Digital Thread) | Complete |
| 19 | Agentic Generation (Child App Builder) | Complete |
| 20 | Security Categorization (FIPS 199/200, CNSSI 1253) | Complete |
| 21 | SaaS Multi-Tenancy (API Gateway, Tenant Isolation, Helm) | Complete |
| 22 | Federated GOTCHA Marketplace (7-gate security pipeline) | Complete |
| 23 | Universal Compliance Platform (10 data categories, 6 new frameworks) | Complete |
| 24 | DevSecOps Profile & Pipeline Security | Complete |
| 25 | Zero Trust Architecture (7-pillar maturity, NIST 800-207) | Complete |
| 26 | DoD MOSA (10 U.S.C. 4401, modularity analysis, ICD/TSP) | Complete |
| 27 | CLI Capabilities (CI/CD automation, parallel agents, containers) | Complete |
| 28 | Remote Command Gateway (Telegram, Slack, Teams, Mattermost) | Complete |
| 29 | Proactive Monitoring (heartbeat daemon, auto-resolver, skill injection) | Complete |
| 30 | Dashboard Auth, Activity Feed, BYOK, Usage Tracking | Complete |
| 31 | RICOAS (Requirements Intake, COA, Approval System) | Complete |
| 32 | External Integration (Jira, ServiceNow, GitLab, DOORS NG) | Complete |
| 33 | Modular Installation (10 profiles, wizard, compliance configurator) | Complete |
| 34 | Dev Profiles & Personalization (5-layer cascade, version history) | Complete |
| 35 | Innovation Engine (autonomous self-improvement, competitive intel) | Complete |
| 36 | Evolutionary Intelligence (capability genome, cross-pollination) | Complete |
| 37 | MITRE ATLAS Integration (AI security, prompt injection, AI BOM, red teaming) | Complete |
| 38 | Cloud-Agnostic Architecture (6 CSPs, multi-cloud LLM, Terraform, Helm) | Complete |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing patterns, and contribution guidelines.

## License

Proprietary. See licensing documentation in `tools/saas/licensing/`.

# CUI // SP-CTI
