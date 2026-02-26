<p align="center">
  <img src="https://img.shields.io/badge/license-AGPL--3.0--or--later-blue" alt="License">
  <img src="https://img.shields.io/badge/python-3.9%2B-brightgreen" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/compliance%20frameworks-29%2B-orange" alt="Compliance Frameworks">
  <img src="https://img.shields.io/badge/tools-251%2B-blueviolet" alt="Tools">
  <img src="https://img.shields.io/badge/languages-6-green" alt="Languages">
</p>

# ICDEV — Intelligent Coding Development Platform

**Describe what you want. Get an ATO-ready application.**

ICDEV is an AI-powered platform that builds complete, compliance-ready applications from a plain-English description. Not scaffolding. Not boilerplate. Full applications — with requirements traceability, security scanning, NIST 800-53 control mappings across 29+ frameworks, and every artifact you need for Authority to Operate.

One developer built this. Imagine what your team could do with it.

> **DISCLAIMER:** This repository does NOT contain classified or Controlled Unclassified Information (CUI). Terms like "CUI", "SECRET", "IL4", "IL5", "IL6" appear throughout as **configuration values and template strings** — not as indicators that this repository itself is classified. Classification terminology references publicly available U.S. government standards ([EO 13526](https://www.archives.gov/isoo/policy-documents/cnsi-eo.html), [32 CFR Part 2002](https://www.ecfr.gov/current/title-32/subtitle-B/chapter-XX/part-2002), [NIST SP 800-53](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final)). File headers containing `[TEMPLATE: CUI // SP-CTI]` are **template markers** demonstrating the format ICDEV applies to generated artifacts.

---

## From Idea to ATO in One Pipeline

Most GovTech teams spend 12-18 months and millions of dollars getting from "we need an app" to a signed ATO. ICDEV compresses this into a single, auditable pipeline:

```
"We need a mission planning tool for IL5"
        │
        ▼
┌─ INTAKE ──────────────────────────────────────────────┐
│  AI-driven conversational requirements gathering       │
│  → Extracts requirements, detects gaps, flags ATO risk │
│  → Scores readiness across 5 dimensions                │
│  → Auto-detects applicable compliance frameworks       │
└───────────────────────────┬───────────────────────────┘
                            ▼
┌─ SIMULATE ────────────────────────────────────────────┐
│  Digital Program Twin — what-if before you build       │
│  → 6-dimension simulation (schedule, cost, risk,       │
│    compliance, technical, staffing)                     │
│  → Monte Carlo estimation (10,000 iterations)          │
│  → 3 Courses of Action: Speed / Balanced / Full        │
└───────────────────────────┬───────────────────────────┘
                            ▼
┌─ GENERATE ────────────────────────────────────────────┐
│  Full application in 12 deterministic steps            │
│  → 300+ files: agents, tools, goals, tests, CI/CD     │
│  → 210-table database with append-only audit trail     │
│  → GOTCHA framework + ATLAS workflow baked in          │
│  → Connected to 100+ cloud MCP servers (AWS/Azure/GCP/OCI/IBM) │
└───────────────────────────┬───────────────────────────┘
                            ▼
┌─ BUILD ───────────────────────────────────────────────┐
│  TDD workflow: RED → GREEN → REFACTOR                  │
│  → 6 languages: Python, Java, Go, Rust, C#, TypeScript │
│  → 9-step test pipeline (unit → BDD → E2E → gates)    │
│  → SAST, dependency audit, secret detection, SBOM      │
└───────────────────────────┬───────────────────────────┘
                            ▼
┌─ COMPLY ──────────────────────────────────────────────┐
│  ATO package generated automatically                   │
│  → SSP covering 17 FIPS 200 control families           │
│  → POAM, STIG checklist, SBOM, OSCAL artifacts         │
│  → Crosswalk maps controls across 29+ frameworks       │
│  → cATO monitoring with evidence freshness tracking     │
└───────────────────────────┬───────────────────────────┘
                            ▼
                    ATO-ready application
```

**Every step is auditable. Every artifact is traceable. Every control is mapped.**

---

## How It Actually Works

### Step 1: Requirements Intake (RICOAS)

You describe what you need in plain English. ICDEV's Requirements Analyst agent runs a conversational intake session that:

- **Extracts requirements** automatically — categorized into 6 types (functional, non-functional, security, compliance, interface, data) at 4 priority levels
- **Detects ambiguities** — 7 pattern categories flag vague language ("as needed", "TBD", "etc.") for clarification
- **Flags ATO boundary impact** — every requirement is classified into 4 tiers:
  - **GREEN** — no boundary change
  - **YELLOW** — minor adjustment (SSP addendum)
  - **ORANGE** — significant change (ISSO review required)
  - **RED** — ATO-invalidating (full stop, alternative COAs generated)
- **Auto-detects compliance frameworks** — mentions of "HIPAA", "CUI", "CJIS", etc. trigger the applicable assessors
- **Scores readiness** across 5 weighted dimensions:

  | Dimension | Weight | What It Measures |
  |-----------|--------|------------------|
  | Completeness | 25% | Requirement types covered, total count vs target |
  | Clarity | 25% | Unresolved ambiguities, conversational depth |
  | Feasibility | 20% | Timeline, budget, and team indicators present |
  | Compliance | 15% | Security requirements and framework selection |
  | Testability | 15% | Requirements with acceptance criteria |

  Score ≥ 0.7 → proceed to decomposition. Score ≥ 0.8 → proceed to COA generation.

- **Decomposes into SAFe hierarchy** — Epic → Capability → Feature → Story → Enabler, each with WSJF scoring, T-shirt sizing, and auto-generated BDD acceptance criteria (Gherkin)

### Step 2: Simulation (Digital Program Twin)

Before writing a single line of code, ICDEV simulates the program across 6 dimensions:

- **Schedule** — Monte Carlo with 10,000 iterations, P50/P80/P95 confidence intervals
- **Cost** — $125-200/hr blended rate × estimated effort, low/high ranges
- **Risk** — probability × impact register, categorized by NIST risk factors
- **Compliance** — NIST controls affected, framework coverage gaps
- **Technical** — architecture complexity, integration density
- **Staffing** — team size, ramp-up timeline, skill requirements

Then generates **3 Courses of Action**:

| COA | Scope | Timeline | Cost | Risk |
|-----|-------|----------|------|------|
| **Speed** | P1 requirements only (MVP) | 1-2 PIs | S-M | Higher |
| **Balanced** | P1 + P2 requirements | 2-3 PIs | M-L | Moderate |
| **Comprehensive** | Full scope | 3-5 PIs | L-XL | Lowest |

Each COA includes an architecture summary, PI roadmap, risk register, compliance impact analysis, resource plan, and cost estimate. RED-tier requirements automatically get **alternative COAs** that achieve the same mission intent within the existing ATO boundary.

### Step 3: Application Generation

This is where ICDEV does what no other tool does. From the approved blueprint, it generates a **complete, working application** in 12 deterministic steps:

| Step | What Gets Generated |
|------|---------------------|
| 1. Directory Tree | 40+ directories following GOTCHA structure |
| 2. Tools | All deterministic Python scripts, adapted with app-specific naming and ports |
| 3. Agent Infrastructure | 5-7 AI agent definitions with Agent Cards, MCP server stubs, config |
| 4. Memory System | MEMORY.md, daily logs, SQLite database, semantic search capability |
| 5. Database | Standalone init script creating capability-gated tables |
| 6. Goals & Hard Prompts | 8 essential workflow definitions, adapted for the child app |
| 7. Args & Context | YAML config files, compliance catalogs, language profiles |
| 8. A2A Callback Client | JSON-RPC client for parent-child communication |
| 9. CI/CD | GitHub + GitLab pipelines, slash commands, .gitignore, requirements.txt |
| 10. Cloud MCP Config | Connected to 100+ cloud-provider MCP servers (AWS, Azure, GCP, OCI, IBM) |
| 11. CLAUDE.md | Dynamic documentation (Jinja2) — only documents present capabilities |
| 12. Audit & Registration | Logged to append-only audit trail, registered in child registry, genome manifest |

The generated application isn't a template. It's a **living system** with its own GOTCHA framework, ATLAS workflow, multi-agent architecture, memory system, compliance automation, and CI/CD pipeline. It inherits ICDEV's capabilities but is independently deployable.

Before generation, ICDEV scores **fitness across 6 dimensions** to determine the right architecture:

| Dimension | Weight | What It Measures |
|-----------|--------|------------------|
| Data Complexity | 10% | CRUD vs event-sourced vs graph models |
| Decision Complexity | 25% | Workflow branching, ML inference, classification |
| User Interaction | 20% | NLQ, conversational UI, dashboards |
| Integration Density | 15% | APIs, webhooks, multi-agent mesh |
| Compliance Sensitivity | 15% | CUI/SECRET, FedRAMP, CMMC, FIPS requirements |
| Scale Variability | 15% | Burst traffic, auto-scaling, real-time streaming |

Score ≥ 6.0 → full agent architecture. 4.0–5.9 → hybrid. < 4.0 → traditional.

### Step 4: Build (TDD + Security)

Every feature is built using the ATLAS workflow with true TDD:

```
[Model] → Architect → Trace → Link → Assemble → Stress-test
```

The 9-step testing pipeline runs automatically:

1. **py_compile** — syntax validation
2. **Ruff** — linting (replaces flake8 + isort + black)
3. **pytest** — unit/integration tests with coverage
4. **behave** — BDD scenario tests from generated Gherkin
5. **Bandit** — SAST security scan
6. **Playwright** — E2E browser tests
7. **Vision validation** — LLM-based screenshot analysis
8. **Acceptance validation** — criteria verification against test evidence
9. **Security gates** — CUI markings, STIG (0 CAT1), secret detection

### Step 5: Compliance (Automatic ATO Package)

ICDEV generates every artifact you need for ATO:

- **System Security Plan (SSP)** — covers all 17 FIPS 200 control families (AC, AT, AU, CA, CM, CP, IA, IR, MA, MP, PE, PL, PS, RA, SA, SC, SI) with dynamic baseline selection from FIPS 199 categorization
- **Plan of Action & Milestones (POAM)** — auto-populated from scan findings
- **STIG Checklist** — mapped to application technology stack
- **Software Bill of Materials (SBOM)** — CycloneDX format, regenerated every build
- **OSCAL artifacts** — machine-readable, validated against NIST Metaschema
- **Control crosswalks** — implement AC-2 once, ICDEV maps it to FedRAMP, CMMC, 800-171, CJIS, HIPAA, PCI DSS, ISO 27001, and 15+ more
- **cATO evidence** — continuous monitoring with freshness tracking and automated evidence collection
- **eMASS sync** — push/pull artifacts to eMASS

The **dual-hub crosswalk engine** eliminates duplicate assessments:

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

---

## Quick Start

```bash
# Clone and install
git clone https://github.com/icdev-ai/icdev.git
cd icdev
pip install -r requirements.txt

# Initialize databases (210 tables)
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

### Generate your first application:

```bash
# Assess fitness for agentic architecture
python tools/builder/agentic_fitness.py --spec "Mission planning tool for IL5 with CUI markings" --json

# Generate blueprint from scorecard
python tools/builder/app_blueprint.py --fitness-scorecard scorecard.json \
  --user-decisions '{}' --app-name "mission-planner" --json

# Generate the full application (12 steps, 300+ files)
python tools/builder/child_app_generator.py --blueprint blueprint.json \
  --project-path ./output --name "mission-planner" --json
```

### Or use Claude Code:

```bash
/icdev-intake        # Start conversational requirements intake
/icdev-simulate      # Run Digital Program Twin simulation
/icdev-agentic       # Generate the full application
/icdev-build         # TDD build (RED → GREEN → REFACTOR)
/icdev-comply        # Generate ATO artifacts
/icdev-transparency  # AI transparency & accountability audit
/icdev-accountability # AI accountability — oversight, CAIO, appeals, incidents
/audit               # 33-check production readiness audit
```

---

## 29+ Compliance Frameworks

| Category | Frameworks |
|----------|------------|
| **Federal** | NIST 800-53 Rev 5, FedRAMP (Moderate/High), CMMC Level 2/3, FIPS 199/200, CNSSI 1253 |
| **DoD** | DoDI 5000.87 DES, MOSA (10 U.S.C. §4401), CSSP (DI 8530.01), cATO Monitoring |
| **Healthcare** | HIPAA Security Rule, HITRUST CSF v11 |
| **Financial** | PCI DSS v4.0, SOC 2 Type II |
| **Law Enforcement** | CJIS Security Policy |
| **International** | ISO/IEC 27001:2022, ISO/IEC 42001:2023 |
| **AI/ML Security** | NIST AI RMF 1.0, MITRE ATLAS, OWASP LLM Top 10, OWASP Agentic AI |
| **AI Transparency** | OMB M-25-21 (High-Impact AI), OMB M-26-04 (Unbiased AI), NIST AI 600-1 (GenAI), GAO-21-519SP (AI Accountability) |
| **Architecture** | NIST 800-207 Zero Trust, CISA Secure by Design, IEEE 1012 IV&V |

---

## Multi-Agent Architecture (15 Agents)

| Tier | Agents | Role |
|------|--------|------|
| **Core** | Orchestrator, Architect | Task routing, system design |
| **Domain** | Builder, Compliance, Security, Infrastructure, MBSE, Modernization, Requirements Analyst, Supply Chain, Simulation, DevSecOps/ZTA, Gateway | Specialized domain work |
| **Support** | Knowledge, Monitor | Self-healing, observability |

Agents communicate via A2A protocol (JSON-RPC 2.0 over mutual TLS). Each publishes an Agent Card at `/.well-known/agent.json`. Workflows use DAG-based parallel execution with domain authority vetoes.

---

## 6 First-Class Languages — Build New or Modernize Legacy

Government agencies and defense contractors sit on millions of lines of legacy code — COBOL, Fortran, Struts, .NET Framework, Python 2 — with the original developers long gone and zero institutional knowledge left. Hiring is impossible: nobody wants to maintain a 20-year-old Java 6 monolith on WebLogic. The code works, but it's a ticking time bomb of tech debt, unpatched CVEs, and expired ATOs.

ICDEV solves this from both directions:

**Build new** — scaffold, TDD, lint, scan, and generate code in any of 6 languages with compliance baked in from line one:

| Language | Scaffold | TDD | Lint | SAST | BDD | Code Gen |
|----------|:--------:|:---:|:----:|:----:|:---:|:--------:|
| Python | Flask/FastAPI | pytest | ruff | bandit | behave | yes |
| Java | Spring Boot | JUnit | checkstyle | SpotBugs | Cucumber | yes |
| Go | net/http, Gin | go test | golangci-lint | gosec | godog | yes |
| Rust | Actix-web | cargo test | clippy | cargo-audit | cucumber-rs | yes |
| C# | ASP.NET Core | xUnit | analyzers | SecurityCodeScan | SpecFlow | yes |
| TypeScript | Express | Jest | eslint | eslint-security | cucumber-js | yes |

**Modernize legacy** — when the original team is gone, ICDEV becomes the team:

- **7R Assessment** — automated analysis scores each application across Rehost, Replatform, Refactor, Rearchitect, Rebuild, Replace, and Retire using a weighted multi-criteria decision matrix. No tribal knowledge required — ICDEV reads the code.
- **Architecture Extraction** — static analysis maps the dependency graph, identifies coupling hotspots, measures complexity, and generates documentation that never existed. Works on codebases with zero comments and zero docs.
- **Cross-Language Translation** — 5-phase hybrid pipeline translates between any of the 30 language pairs (Extract → Type-Check → Translate → Assemble → Validate+Repair). Migrating a Python 2 Flask app to Go? A legacy Java 8 monolith to modern Spring Boot? A .NET Framework service to ASP.NET Core? ICDEV generates pass@k candidate translations, validates with compiler feedback, and auto-repairs failures — up to 3 repair cycles per unit.
- **Strangler Fig Tracking** — for large monoliths that can't be rewritten overnight, ICDEV manages the gradual migration: dual-system traceability, feature-by-feature cutover tracking, and a compliance bridge that maintains ≥95% ATO control coverage throughout the entire transition.
- **Framework Migration** — declarative JSON mapping rules handle Struts → Spring Boot, Django 2 → Django 4, Rails 5 → Rails 7, Express → Fastify, and more. Add new migration paths without writing code.
- **ATO Compliance Bridge** — this is the killer feature for modernization. Legacy apps often have existing ATOs. ICDEV ensures the modernized application inherits the original control mappings through the crosswalk engine, so you don't lose years of compliance work. The bridge validates coverage every PI and blocks deployment if it drops below 95%.

The bottom line: **you don't need the original developers**. You don't need a team that knows the legacy stack. ICDEV analyzes the codebase, scores the migration strategy, translates the code, and maintains ATO coverage — with an append-only audit trail documenting every decision for your ISSO.

---

## 6 Cloud Providers

| Provider | Environment | LLM Integration |
|----------|-------------|-----------------|
| **AWS GovCloud** | us-gov-west-1 | Amazon Bedrock (Claude, Titan) |
| **Azure Government** | USGov Virginia | Azure OpenAI |
| **GCP** | Assured Workloads | Vertex AI (Gemini, Claude) |
| **OCI** | Government Cloud | OCI GenAI (Cohere, Llama) |
| **IBM** | Cloud for Government | watsonx.ai (Granite, Llama) |
| **Local** | Air-Gapped | Ollama (Llama, Mistral, CodeGemma) |

Generated applications connect to 100+ cloud-provider MCP servers automatically based on target CSP.

---

## GOTCHA Framework

ICDEV's core architecture separates deterministic tools from probabilistic AI:

```
┌──────────────────────────────────────────────────────┐
│  Goals         →  What to achieve (47 workflows)     │
│  Orchestration →  AI decides tool order (LLM layer)  │
│  Tools         →  Deterministic scripts (251+ tools) │
│  Context       →  Static reference (35 catalogs)     │
│  Hard Prompts  →  Reusable LLM templates             │
│  Args          →  YAML/JSON config (30+ files)       │
└──────────────────────────────────────────────────────┘
```

**Why?** LLMs are probabilistic. Business logic must be deterministic. 90% accuracy per step = ~59% over 5 steps. GOTCHA fixes this by keeping AI in the orchestration layer and critical logic in deterministic Python scripts.

Generated child applications inherit the full GOTCHA framework — they aren't wrappers or templates, they're autonomous systems that can build their own features using the same methodology.

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  Claude Code / AI IDE                      │
│            (39 slash commands, 251 MCP tools)              │
├──────────────────────────────────────────────────────────┤
│                 Unified MCP Gateway                        │
│          (single server, all 251 tools, lazy-loaded)       │
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
│  210 tables, append-only audit     │  AWS │Azure│GCP│OCI  │
│  Per-tenant DB isolation           │  IBM │Local/Air-Gap   │
└──────────────────────────────────────────────────────────┘
```

---

## Dashboard

```bash
python tools/dashboard/app.py
# → http://localhost:5000
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
| `/children` | Generated child application registry with health monitoring |
| `/traces` | Distributed trace explorer with span waterfall |
| `/provenance` | W3C PROV lineage viewer |
| `/xai` | Explainable AI dashboard with SHAP analysis |
| `/ai-transparency` | AI Transparency: model cards, system cards, AI inventory, fairness, GAO readiness |
| `/ai-accountability` | AI Accountability: oversight plans, CAIO registry, appeals, incidents, ethics reviews, reassessment |
| `/code-quality` | Code Quality Intelligence: AST metrics, smell detection, maintainability trend, runtime feedback |

Auth: per-user API keys (SHA-256 hashed), 5 RBAC roles (admin, pm, developer, isso, co). Optional BYOK (bring-your-own LLM keys) with AES-256 encryption.

---

## MCP Server Integration

All 251 tools exposed through a single MCP gateway. Works with any AI coding assistant:

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

---

## Security

Defense-in-depth by default:

- **STIG-hardened containers** — non-root, read-only rootfs, all capabilities dropped
- **Append-only audit trail** — no UPDATE/DELETE on audit tables, NIST AU compliant
- **CUI markings** — applied at generation time per impact level (IL4/IL5/IL6)
- **Mutual TLS** — all inter-agent communication within K8s
- **Prompt injection detection** — 5-category scanner for AI-specific threats
- **MITRE ATLAS red teaming** — adversarial testing against 6 techniques
- **Behavioral drift detection** — z-score baseline monitoring for all agents
- **Tool chain validation** — blocks dangerous execution sequences
- **MCP RBAC** — per-tool, per-role deny-first authorization
- **AI transparency** — model cards, system cards, AI use case inventory, confabulation detection, fairness assessment per OMB M-25-21/M-26-04, NIST AI 600-1, and GAO-21-519SP
- **AI accountability** — human oversight plans, CAIO designation, appeal tracking, AI incident response, ethics reviews, reassessment scheduling, cross-framework accountability audit
- **Self-healing** — confidence-based remediation (≥0.7 auto-fix, 0.3–0.7 suggest, <0.3 escalate)

---

## Deployment

### Desktop (Development)

```bash
pip install -r requirements.txt
python tools/dashboard/app.py
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
| **GovCloud Full** | All 29+ frameworks | Maximum compliance |

---

## Project Structure

```
icdev/
├── goals/                # 47 workflow definitions
├── tools/                # 251+ tools across 44 categories
│   ├── compliance/       # 25+ framework assessors, crosswalk, OSCAL
│   ├── security/         # SAST, AI security, ATLAS, prompt injection
│   ├── builder/          # TDD, scaffolding, app generation, 6 languages
│   ├── requirements/     # RICOAS intake, gap detection, SAFe decomposition
│   ├── simulation/       # Digital Program Twin, Monte Carlo, COA generation
│   ├── dashboard/        # Flask web UI, auth, RBAC, real-time events
│   ├── agent/            # Multi-agent orchestration, DAG workflows
│   ├── cloud/            # 6 CSP abstractions, region validation
│   ├── saas/             # Multi-tenant platform layer
│   ├── mcp/              # Unified MCP gateway (251 tools)
│   ├── modernization/    # 7R assessment, legacy migration
│   ├── observability/    # Tracing, provenance, AgentSHAP, XAI
│   ├── innovation/       # Autonomous self-improvement engine
│   └── ...               # 30+ more specialized categories
├── args/                 # 30+ YAML/JSON configuration files
├── context/              # 35 compliance catalogs, language profiles
├── hardprompts/          # Reusable LLM instruction templates
├── tests/                # 130 test files
├── k8s/                  # Production Kubernetes manifests
├── docker/               # STIG-hardened Dockerfiles
├── deploy/helm/          # Helm chart for on-prem deployment
├── .claude/commands/     # 38 Claude Code slash commands
└── CLAUDE.md             # Comprehensive architecture documentation
```

---

## Testing

```bash
# All tests (130 test files, 1600+ tests)
pytest tests/ -v --tb=short

# BDD scenario tests
behave features/

# E2E browser tests (Playwright)
python tools/testing/e2e_runner.py --run-all

# Production readiness audit (38 checks, 7 categories)
python tools/testing/production_audit.py --human --stream

# Code quality self-analysis
python tools/analysis/code_analyzer.py --project-dir tools/ --json
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
