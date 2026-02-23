<p align="center">
  <img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.9%2B-brightgreen" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/compliance%20frameworks-25%2B-orange" alt="Compliance Frameworks">
  <img src="https://img.shields.io/badge/tools-230%2B-blueviolet" alt="Tools">
  <img src="https://img.shields.io/badge/languages-6-green" alt="Languages">
</p>

# ICDEV — Intelligent Coding Development Platform

**Build government-grade software without becoming a compliance expert.**

ICDEV is an AI-powered development platform that automates the entire software lifecycle — from requirements intake to ATO (Authority to Operate) — for teams building in regulated environments. It handles NIST, FedRAMP, CMMC, HIPAA, PCI DSS, and 20+ other compliance frameworks so developers can focus on writing code.

One developer built this. Imagine what your team could do with it.

> **DISCLAIMER:** This repository does NOT contain classified or Controlled Unclassified Information (CUI). Terms like "CUI", "SECRET", "IL4", "IL5", "IL6" appear throughout as **configuration values and template strings** — not as indicators that this repository itself is classified. Classification terminology references publicly available U.S. government standards ([EO 13526](https://www.archives.gov/isoo/policy-documents/cnsi-eo.html), [32 CFR Part 2002](https://www.ecfr.gov/current/title-32/subtitle-B/chapter-XX/part-2002), [NIST SP 800-53](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)). File headers containing `[TEMPLATE: CUI // SP-CTI]` are **template markers** demonstrating the format ICDEV applies to generated artifacts.

---

## The Problem

Building software for the U.S. government or regulated industries means:

- **Months** generating compliance artifacts (SSPs, POAMs, STIGs, SBOMs) by hand
- **Millions** in consultant fees assembling ATO packages
- Every framework (FedRAMP, CMMC, HIPAA, PCI DSS) assessed separately — massive duplication
- Security scanning, CUI markings, audit trails — all manual
- Developers writing documentation instead of code

## The Solution

ICDEV treats compliance as code. Write your application — ICDEV generates the artifacts, runs security scans, maps controls across 25+ frameworks, and maintains your ATO posture automatically.

```
Developer writes code
        │
        ▼
ICDEV auto-generates:
  SSP, POAM, STIG checklist, SBOM, CUI markings,
  FedRAMP package, CMMC assessment, OSCAL artifacts,
  eMASS sync, cATO evidence, control crosswalks
        │
        ▼
ATO-ready application
```

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/icdev-ai/icdev.git
cd icdev
pip install -r requirements.txt

# Initialize databases (193 tables)
python tools/db/init_icdev_db.py

# Start the dashboard
python tools/dashboard/app.py
# → http://localhost:5000
```

### Or use modular installation:

```bash
# Interactive wizard
python tools/installer/installer.py --interactive

# Profile-based (pick your mission)
python tools/installer/installer.py --profile dod_team --compliance fedramp_high,cmmc
python tools/installer/installer.py --profile healthcare --compliance hipaa,hitrust
python tools/installer/installer.py --profile isv_startup --platform docker
```

### Your first compliance scan:

```bash
# Create a project
python tools/project/project_create.py --name "my-app" --type microservice

# Generate NIST 800-53 SSP
python tools/compliance/ssp_generator.py --project-id "proj-001"

# Run security scanning (SAST + deps + secrets)
python tools/security/sast_runner.py --project-dir ./my-app
python tools/security/dependency_auditor.py --project-dir ./my-app
python tools/security/secret_detector.py --project-dir ./my-app

# See how one control maps across ALL frameworks
python tools/compliance/crosswalk_engine.py --control AC-2
```

---

## Key Capabilities

### Compliance Automation (25+ Frameworks)

| Category | Frameworks |
|----------|------------|
| **Federal** | NIST 800-53 Rev 5, FedRAMP (Moderate/High), CMMC Level 2/3, FIPS 199/200, CNSSI 1253 |
| **DoD** | DoDI 5000.87 DES, MOSA (10 U.S.C. §4401), CSSP (DI 8530.01), cATO Monitoring |
| **Healthcare** | HIPAA Security Rule, HITRUST CSF v11 |
| **Financial** | PCI DSS v4.0, SOC 2 Type II |
| **Law Enforcement** | CJIS Security Policy |
| **International** | ISO/IEC 27001:2022, ISO/IEC 42001:2023 |
| **AI/ML Security** | NIST AI RMF 1.0, MITRE ATLAS, OWASP LLM Top 10, OWASP Agentic AI |
| **Architecture** | NIST 800-207 Zero Trust, CISA Secure by Design, IEEE 1012 IV&V |

### Dual-Hub Crosswalk Engine

Implement one control — satisfy dozens across frameworks. Never assess the same requirement twice.

```
                    ┌─────────────────┐
                    │  NIST 800-53    │  ← US Hub
                    │    Rev 5        │
                    └────────┬────────┘
            ┌────────────────┼────────────────┐
            │                │                │
       ┌────┴────┐     ┌────┴────┐     ┌────┴────┐
       │FedRAMP  │     │  CMMC   │     │800-171  │
       │Mod/High │     │  L2/L3  │     │  Rev 2  │
       └─────────┘     └─────────┘     └─────────┘
            │                │
       ┌────┴────┐     ┌────┴────┐
       │  CJIS   │     │ HIPAA   │     ...and 15+ more
       │ HITRUST │     │ PCI DSS │
       │  SOC 2  │     │ISO27001 │  ← Bridge to Int'l Hub
       └─────────┘     └─────────┘
```

**Example:** Implementing AC-2 (Account Management) automatically satisfies FedRAMP AC-2, NIST 800-171 3.1.1, CMMC AC.L2-3.1.1, CJIS 5.4, HIPAA 164.312(a)(1), PCI DSS 7.1, and ISO 27001 A.5.15.

### Multi-Agent Architecture (15 Agents)

| Tier | Agents | Role |
|------|--------|------|
| **Core** | Orchestrator, Architect | Task routing, system design |
| **Domain** | Builder, Compliance, Security, Infrastructure, MBSE, Modernization, Requirements Analyst, Supply Chain, Simulation, DevSecOps/ZTA, Gateway | Specialized domain work |
| **Support** | Knowledge, Monitor | Self-healing, observability |

Agents communicate via A2A protocol (JSON-RPC 2.0 over mutual TLS). Each publishes an Agent Card at `/.well-known/agent.json`. Workflows use DAG-based parallel execution with domain authority vetoes.

### 6 First-Class Languages

| Language | Scaffold | TDD | Lint | SAST | BDD | Code Gen |
|----------|:--------:|:---:|:----:|:----:|:---:|:--------:|
| Python | Flask/FastAPI | pytest | ruff | bandit | behave | yes |
| Java | Spring Boot | JUnit | checkstyle | SpotBugs | Cucumber | yes |
| Go | net/http, Gin | go test | golangci-lint | gosec | godog | yes |
| Rust | Actix-web | cargo test | clippy | cargo-audit | cucumber-rs | yes |
| C# | ASP.NET Core | xUnit | analyzers | SecurityCodeScan | SpecFlow | yes |
| TypeScript | Express | Jest | eslint | eslint-security | cucumber-js | yes |

### 6 Cloud Providers

| Provider | Environment | LLM Integration |
|----------|-------------|-----------------|
| **AWS GovCloud** | us-gov-west-1 | Amazon Bedrock (Claude, Titan) |
| **Azure Government** | USGov Virginia | Azure OpenAI |
| **GCP** | Assured Workloads | Vertex AI (Gemini, Claude) |
| **OCI** | Government Cloud | OCI GenAI (Cohere, Llama) |
| **IBM** | Cloud for Government | watsonx.ai (Granite, Llama) |
| **Local** | Air-Gapped | Ollama (Llama, Mistral, CodeGemma) |

---

## GOTCHA Framework

ICDEV's core architecture separates deterministic tools from probabilistic AI:

```
┌──────────────────────────────────────────────────────┐
│  Goals         →  What to achieve (46 workflows)     │
│  Orchestration →  AI decides tool order (LLM layer)  │
│  Tools         →  Deterministic scripts (230+ tools) │
│  Context       →  Static reference (35 catalogs)     │
│  Hard Prompts  →  Reusable LLM templates             │
│  Args          →  YAML/JSON config (30+ files)       │
└──────────────────────────────────────────────────────┘
```

**Why?** LLMs are probabilistic. Business logic must be deterministic. 90% accuracy per step = ~59% over 5 steps. GOTCHA fixes this by keeping AI in the orchestration layer and critical logic in deterministic Python scripts.

### ATLAS Workflow

Every feature follows a structured build methodology:

```
[Model] → Architect → Trace → Link → Assemble → Stress-test
```

The optional Model phase integrates SysML/DOORS NG for MBSE-driven development.

---

## What Can ICDEV Do?

### For Developers
- **Scaffold** projects in 6 languages with compliance baked in from day one
- **TDD workflow** — RED (failing test) → GREEN (minimal code) → REFACTOR
- **Security scanning** — SAST, dependency audit, secret detection, container scanning
- **Cross-language translation** — migrate codebases between Python, Java, Go, Rust, C#, TypeScript with a 5-phase hybrid pipeline

### For Compliance Teams
- **Auto-generate** SSPs, POAMs, STIG checklists, SBOMs, OSCAL artifacts
- **Crosswalk engine** — implement once, satisfy 25+ frameworks simultaneously
- **FedRAMP, CMMC, HIPAA, PCI DSS** assessments with gap analysis
- **cATO monitoring** — continuous compliance with evidence freshness tracking
- **FIPS 199/200** security categorization with CNSSI 1253 overlays

### For Program Managers
- **AI-driven requirements intake** — conversational requirements gathering (RICOAS)
- **Digital Program Twin** — 6-dimension what-if simulation with Monte Carlo analysis
- **COA generation** — Speed, Balanced, and Comprehensive courses of action
- **ATO boundary impact** — 4-tier assessment (GREEN / YELLOW / ORANGE / RED)
- **Bidirectional sync** with Jira, ServiceNow, GitLab, DOORS NG

### For DevSecOps
- **Zero Trust Architecture** — 7-pillar maturity scoring (DoD ZTA Strategy)
- **Policy-as-code** — Kyverno and OPA policy generation
- **Pipeline security** — integrated SAST, DAST, SCA, secret scanning, SBOM
- **OWASP Agentic AI** — behavioral drift detection, tool chain validation, trust scoring
- **MITRE ATLAS** red teaming with 6 adversarial test techniques

### For Modernization
- **7R assessment** — Rehost, Replatform, Refactor, Rearchitect, Rebuild, Replace, Retire
- **Legacy analysis** — architecture extraction, dependency mapping, complexity scoring
- **Strangler fig** pattern tracking with ATO compliance bridge
- **Framework migration** — Struts → Spring Boot, Django 2 → 4, and more

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Claude Code / AI IDE                      │
│            (37 slash commands, 230 MCP tools)              │
├──────────────────────────────────────────────────────────┤
│                 Unified MCP Gateway                        │
│          (single server, all 230 tools, lazy-loaded)       │
├──────────┬──────────┬───────────┬───────────┬────────────┤
│   Core   │  Domain  │  Domain   │  Domain   │  Support   │
│          │          │           │           │            │
│ Orchestr │ Builder  │ MBSE      │ DevSecOps │ Knowledge  │
│ Architect│ Complnce │ Modernize │ Gateway   │ Monitor    │
│          │ Security │ Req.Anlst │           │            │
│          │ Infra    │ SupplyChn │           │            │
│          │          │ Simulatn  │           │            │
├──────────┴──────────┴───────────┴───────────┴────────────┤
│                   GOTCHA Framework                         │
│       Goals │ Tools │ Args │ Context │ Hard Prompts        │
├──────────────────────────────────────────────────────────┤
│  SQLite (dev) / PostgreSQL (prod)  │   Multi-Cloud CSP    │
│  193 tables, append-only audit     │  AWS │Azure│GCP│OCI  │
│  Per-tenant DB isolation           │  IBM │Local/Air-Gap   │
└──────────────────────────────────────────────────────────┘
```

---

## Dashboard

Start the web dashboard at `http://localhost:5000`:

```bash
python tools/dashboard/app.py
```

| Page | Purpose |
|------|---------|
| `/` | Home with auto-notifications and pipeline status |
| `/projects` | Project listing with compliance posture |
| `/agents` | Agent registry with heartbeat monitoring |
| `/monitoring` | System health with status icons |
| `/wizard` | Getting Started wizard (3 questions → workflow) |
| `/query` | Natural language compliance queries |
| `/chat` | Multi-agent chat interface |
| `/activity` | Real-time audit + event feed |
| `/traces` | Distributed trace explorer with span waterfall |
| `/provenance` | W3C PROV lineage viewer |
| `/xai` | Explainable AI dashboard with SHAP analysis |
| `/usage` | Usage tracking and cost dashboard |
| `/dev-profiles` | Developer profile management |
| `/children` | Child application registry |

Auth: per-user API keys (SHA-256 hashed), 5 RBAC roles (admin, pm, developer, isso, co). Optional BYOK (bring-your-own LLM keys) with AES-256 encryption.

---

## MCP Server Integration

ICDEV exposes all 230 tools through a unified MCP (Model Context Protocol) gateway. Works with any AI coding assistant:

```json
{
  "mcpServers": {
    "icdev-unified": {
      "command": "python",
      "args": ["tools/mcp/unified_server.py"]
    }
  }
}
```

Compatible with: **Claude Code**, **OpenAI Codex**, **Google Gemini**, **GitHub Copilot**, **Cursor**, **Windsurf**, **Amazon Q**, **JetBrains/Junie**, **Cline**, **Aider**.

One server. 230 tools. Any AI assistant.

---

## Claude Code Integration

ICDEV includes 37 custom slash commands for Claude Code:

```bash
/icdev-init          # Initialize project with compliance scaffolding
/icdev-build         # TDD build (RED → GREEN → REFACTOR)
/icdev-comply        # Generate ATO artifacts (SSP, POAM, STIG, SBOM)
/icdev-secure        # Full security scan (SAST, deps, secrets, containers)
/icdev-test          # Run test suite (pytest + BDD)
/icdev-deploy        # Generate IaC and CI/CD pipeline
/icdev-intake        # AI-driven requirements intake
/icdev-translate     # Cross-language code translation
/icdev-zta           # Zero Trust Architecture assessment
/audit               # 30-check production readiness audit
/remediate           # Auto-fix audit blockers
```

---

## Testing

```bash
# All tests (124 test files, 1500+ tests)
pytest tests/ -v --tb=short

# BDD scenario tests
behave features/

# E2E browser tests (Playwright)
python tools/testing/e2e_runner.py --run-all

# Production readiness audit (30 checks, 6 categories)
python tools/testing/production_audit.py --human --stream

# Platform compatibility
python tools/testing/platform_check.py

# .claude directory governance validation
python tools/testing/claude_dir_validator.py --human
```

**9-step testing pipeline:** py_compile → Ruff linting → pytest → behave/Gherkin BDD → Bandit SAST → Playwright E2E → vision validation → acceptance validation → security/compliance gates.

---

## Security

Defense-in-depth by default:

- **STIG-hardened containers** — non-root, read-only rootfs, all capabilities dropped
- **Append-only audit trail** — no UPDATE/DELETE, NIST AU compliant
- **CUI markings** — applied at generation time per impact level (IL4/IL5/IL6)
- **Mutual TLS** — all inter-agent communication within K8s
- **Prompt injection detection** — 5-category scanner for AI-specific threats
- **MITRE ATLAS red teaming** — adversarial testing against 6 techniques
- **Behavioral drift detection** — z-score baseline monitoring for all agents
- **Tool chain validation** — blocks dangerous execution sequences
- **MCP RBAC** — per-tool, per-role deny-first authorization
- **Self-healing** — confidence-based automated remediation (≥0.7 auto, 0.3–0.7 suggest, <0.3 escalate)

---

## Deployment

### Desktop (Development)

```bash
pip install -r requirements.txt
python tools/dashboard/app.py
```

### Docker

```bash
docker build -f docker/Dockerfile.dashboard -t icdev-dashboard .
docker run -p 5000:5000 icdev-dashboard
```

### Kubernetes (Production)

```bash
kubectl apply -f k8s/
# Includes: namespace, network policies (default deny), 15 agent deployments,
# dashboard, API gateway, HPA auto-scaling, pod disruption budgets
```

### Helm (On-Premises / Air-Gapped)

```bash
helm install icdev deploy/helm/ --values deploy/helm/values-on-prem.yaml
```

### Installation Profiles

| Profile | Compliance | Best For |
|---------|------------|----------|
| **ISV Startup** | None | SaaS products, rapid prototyping |
| **DoD Team** | FedRAMP + CMMC + FIPS + cATO | Defense software |
| **Healthcare** | HIPAA + HITRUST + SOC 2 | Health IT / EHR |
| **Financial** | PCI DSS + SOC 2 + ISO 27001 | FinTech / Banking |
| **Law Enforcement** | CJIS + FIPS 199/200 | Criminal justice systems |
| **GovCloud Full** | All 25+ frameworks | Maximum compliance |

---

## Project Structure

```
icdev/
├── goals/                # 46 workflow definitions
├── tools/                # 230+ tools across 44 categories
│   ├── compliance/       # 25+ framework assessors, crosswalk, OSCAL
│   ├── security/         # SAST, AI security, ATLAS, prompt injection
│   ├── builder/          # TDD, scaffolding, 6 languages, dev profiles
│   ├── dashboard/        # Flask web UI, auth, RBAC, real-time events
│   ├── agent/            # Multi-agent orchestration, DAG workflows
│   ├── cloud/            # 6 CSP abstractions, region validation
│   ├── saas/             # Multi-tenant platform layer
│   ├── mcp/              # Unified MCP gateway (230 tools)
│   ├── requirements/     # RICOAS intake, gap detection, decomposition
│   ├── simulation/       # Digital Program Twin, Monte Carlo, COAs
│   ├── modernization/    # 7R assessment, legacy migration
│   ├── observability/    # Tracing, provenance, AgentSHAP, XAI
│   ├── innovation/       # Self-improvement engine
│   └── ...               # 30+ more specialized categories
├── args/                 # 30+ YAML/JSON configuration files
├── context/              # 35 compliance catalogs, language profiles
├── hardprompts/          # Reusable LLM instruction templates
├── tests/                # 124 test files
├── k8s/                  # Production Kubernetes manifests
├── docker/               # STIG-hardened Dockerfiles
├── deploy/helm/          # Helm chart for on-prem deployment
├── .claude/commands/     # 37 Claude Code slash commands
└── CLAUDE.md             # Comprehensive architecture documentation
```

---

## Dependency License Notice

Most dependencies use permissive licenses (MIT, BSD, Apache 2.0). Notable exceptions:

| Package | License | Notes |
|---------|---------|-------|
| psycopg2-binary | LGPL | Permits use in proprietary software via dynamic linking (standard pip install) |
| docutils | BSD / GPL / Public Domain | Triple-licensed; used under BSD |

Run `pip-licenses -f markdown` to audit all dependency licenses.

---

## Contributing

We welcome contributions. ICDEV uses a Contributor License Agreement (CLA) to support dual licensing. The CLA does **not** transfer your copyright — you retain full ownership of your work.

## Attribution

See [NOTICE](NOTICE) for third-party acknowledgments, standards references, and architectural inspirations.

## License

ICDEV is dual-licensed:

- **Open Source** — [GNU Affero General Public License v3.0 or later](LICENSE)
  Free for internal use, academic research, open-source projects, and evaluation.

- **Commercial** — [Commercial License](COMMERCIAL.md)
  Removes AGPL copyleft obligations for SaaS, embedded, or proprietary use.

## Contact

- **Commercial licensing:** agi@icdev.ai
- **Issues:** [github.com/icdev-ai/icdev/issues](https://github.com/icdev-ai/icdev/issues)

---

<p align="center">
  <i>Built by one developer. Ready for your entire team.</i>
</p>
