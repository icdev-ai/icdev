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

NIST 800-53 Rev 5, FedRAMP (Moderate/High), NIST 800-171, CMMC Level 2/3, DoD CSSP (DI 8530.01), CISA Secure by Design, IEEE 1012 IV&V, DoDI 5000.87 DES, FIPS 199/200, CNSSI 1253, CJIS, HIPAA, HITRUST CSF v11, SOC 2 Type II, PCI DSS v4.0, ISO/IEC 27001:2022, NIST SP 800-207 (ZTA), DoD MOSA (10 U.S.C. section 4401).

Unified via a dual-hub crosswalk engine: NIST 800-53 (US hub) + ISO 27001 (international hub) with bidirectional bridge.

### SaaS Multi-Tenancy

Per-tenant database isolation, 3 authentication methods (API key, OAuth 2.0/OIDC, CAC/PIV), subscription tiers (Starter/Professional/Enterprise), REST + MCP Streamable HTTP transport. Helm chart for on-prem deployment.

### Supported Languages (6 First-Class)

Python, Java, JavaScript/TypeScript, Go, Rust, C#

Each language has full support for scaffolding, linting, formatting, SAST, dependency auditing, BDD steps, and code generation.

## Testing

```bash
# Unit and integration tests (330+ tests across 21 test files)
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
# Initialize database (143 tables)
python tools/db/init_icdev_db.py

# Compliance artifacts
python tools/compliance/ssp_generator.py --project-id "proj-123"
python tools/compliance/sbom_generator.py --project-dir .

# Multi-framework assessment
python tools/compliance/multi_regime_assessor.py --project-id "proj-123" --json

# TDD workflow
python tools/builder/test_writer.py --feature "user auth" --project-dir .
python tools/builder/code_generator.py --test-file test.py --project-dir .

# Start API gateway
python tools/saas/api_gateway.py --port 8443 --debug
```

See `CLAUDE.md` for the complete command reference.

## Project Structure

```
goals/          — Workflow process definitions (35+ goals)
tools/          — Deterministic Python scripts (organized by domain)
args/           — YAML/JSON configuration files
context/        — Static reference material and language profiles
hardprompts/    — Reusable LLM instruction templates
memory/         — Session logs and long-term memory
data/           — SQLite databases (icdev.db, platform.db, memory.db)
tests/          — pytest test suite
features/       — BDD/Gherkin scenario tests
specs/          — Feature specifications (per-issue directories)
k8s/            — Kubernetes manifests (deployments, HPA, PDB, network policies)
docker/         — STIG-hardened Dockerfiles
deploy/         — Helm chart and air-gapped installer
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, testing patterns, and contribution guidelines.

## License

Proprietary. See licensing documentation in `tools/saas/licensing/`.

# CUI // SP-CTI
