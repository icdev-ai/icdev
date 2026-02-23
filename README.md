# ICDEV — Intelligent Coding Development Platform

> **DISCLAIMER: This repository does NOT contain classified or Controlled Unclassified Information (CUI).**
>
> ICDEV is a software development framework that *automates the generation* of
> classification markings, compliance artifacts, and security controls for
> applications it builds. The terms "CUI", "SECRET", "Top Secret", "IL4", "IL5",
> "IL6", and similar classification labels appear throughout the codebase as
> **configuration values, template strings, and automation logic** — not as
> indicators that this repository itself is classified or contains controlled
> information.
>
> All classification terminology references publicly available U.S. government
> standards including [Executive Order 13526](https://www.archives.gov/isoo/policy-documents/cnsi-eo.html),
> [32 CFR Part 2002](https://www.ecfr.gov/current/title-32/subtitle-B/chapter-XX/part-2002) (CUI Program),
> [NIST SP 800-53 Rev 5](https://csrc.nist.gov/publications/detail/sp/800-53/rev-5/final),
> and [CNSSI 1253](https://www.cnss.gov/CNSS/issuances/Instructions.cfm).
>
> File headers containing `[TEMPLATE: CUI // SP-CTI]` are **template markers**
> that demonstrate the marking format ICDEV applies to generated artifacts.
> They do not indicate the file itself is CUI.
>
> **If you are a government security reviewer:** This is an unclassified,
> open-source Apache 2.0 licensed project. No special handling is required.

ICDEV is an AI-powered meta-builder that autonomously builds Gov/DoD applications using the GOTCHA (Goals, Orchestration, Tools, Args, Context, Hard Prompts) framework and the ATLAS workflow. It handles the full SDLC with TDD/BDD, NIST 800-53 RMF compliance, multi-agent architecture across 15 specialized agents, and self-healing capabilities for classification levels up to SECRET (IL6).

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

### Compliance Frameworks (25+)

NIST 800-53 Rev 5, FedRAMP (Moderate/High), NIST 800-171, CMMC Level 2/3, DoD CSSP (DI 8530.01), CISA Secure by Design, IEEE 1012 IV&V, DoDI 5000.87 DES, FIPS 199/200, CNSSI 1253, CJIS, HIPAA, HITRUST CSF v11, SOC 2 Type II, PCI DSS v4.0, ISO/IEC 27001:2022, NIST SP 800-207 (ZTA), DoD MOSA (10 U.S.C. section 4401), MITRE ATLAS, OWASP LLM Top 10, NIST AI RMF, ISO/IEC 42001, OWASP Agentic AI.

Unified via a dual-hub crosswalk engine: NIST 800-53 (US hub) + ISO 27001 (international hub) with bidirectional bridge.

### AI Security (MITRE ATLAS Integration)

- **Prompt Injection Detection** — 5 detection categories with confidence-based action thresholds
- **AI Telemetry** — Privacy-preserving audit logging with SHA-256 hashing
- **AI BOM (Bill of Materials)** — Catalogs all AI/ML components for supply chain visibility
- **ATLAS Red Teaming** — Opt-in adversarial testing against 6 ATLAS techniques
- **4-Framework AI Assessment** — MITRE ATLAS, OWASP LLM Top 10, NIST AI RMF, ISO/IEC 42001
- **Marketplace Hardening** — Gates 8-9: prompt injection scan + behavioral sandbox

### Cloud-Agnostic Architecture

- **6 Cloud Service Providers:** AWS GovCloud, Azure Government, GCP Assured Workloads, OCI Government Cloud, IBM Cloud for Government (IC4G), Local (air-gapped)
- **4 Cloud Modes:** Commercial, Government, On-Premises, Air-Gapped
- **Multi-Cloud LLM:** Amazon Bedrock, Azure OpenAI, Vertex AI, OCI GenAI, IBM watsonx.ai, Ollama (local)
- **CSP-Specific IaC:** Terraform generators for all 6 CSPs
- **Region Validation:** Compliance certification checks before deployment

### Supported Languages (6 First-Class)

Python, Java, JavaScript/TypeScript, Go, Rust, C#

Each language has full support for scaffolding, linting, formatting, SAST, dependency auditing, BDD steps, and code generation.

## Testing

```bash
# Unit and integration tests (2100+ tests across 60+ test files)
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

## Dashboard

The web dashboard (`http://localhost:5000`) provides:

| Page | Purpose |
|------|---------|
| `/` | Home with auto-notifications |
| `/projects` | Project listing with friendly timestamps |
| `/agents` | Agent registry with heartbeat age |
| `/monitoring` | System monitoring with status icons |
| `/wizard` | Getting Started wizard |
| `/query` | Natural language compliance queries |
| `/activity` | Merged activity feed (audit + hook events) |
| `/usage` | Usage tracking + cost dashboard |
| `/traces` | Distributed trace explorer |
| `/provenance` | Provenance lineage viewer |
| `/xai` | Explainable AI dashboard |

Auth: per-user API keys (SHA-256 hashed), Flask signed sessions, 5 RBAC roles (admin, pm, developer, isso, co).

## Project Structure

```
goals/          -- Workflow process definitions (40+ goals)
tools/          -- Deterministic Python scripts (organized by domain)
  builder/      -- TDD code gen, scaffolding, dev profiles, agentic generation
  cloud/        -- CSP provider ABCs, factory, health checker, region validator
  compliance/   -- 25+ framework assessors, crosswalk engine, report generators
  dashboard/    -- Flask web UI with auth, RBAC, batch ops
  db/           -- Database init, migrations, backup/restore
  devsecops/    -- DevSecOps profiles, ZTA maturity, policy-as-code
  dx/           -- Universal AI companion (10 tools), session context
  infra/        -- Terraform generators (6 CSPs), Ansible, K8s, pipelines
  innovation/   -- Innovation Engine (web scanning, signal scoring, triage)
  llm/          -- Multi-cloud LLM router (6 providers), embeddings
  marketplace/  -- Federated GOTCHA asset marketplace
  mcp/          -- MCP servers (unified gateway + 18 individual)
  observability/-- Distributed tracing, provenance, AgentSHAP
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

## Dependency License Notice

Most dependencies use permissive licenses (MIT, BSD, Apache 2.0). The following
have copyleft or dual licenses that users should be aware of:

| Package | License | Notes |
|---------|---------|-------|
| psycopg2-binary | LGPL | LGPL permits use in proprietary software when dynamically linked (standard pip install). No source disclosure required for your application code. |
| docutils | BSD / GPL / Public Domain | Triple-licensed. ICDEV uses it under the **BSD license**. |

Run `pip-licenses -f markdown` to audit all dependency licenses in your environment.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing patterns, and contribution guidelines including the Developer Certificate of Origin (DCO) requirement.

## Attribution

See [NOTICE](NOTICE) for third-party acknowledgments and project inspirations.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for the full text.

```
Copyright 2024-2026 ICDEV Contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
