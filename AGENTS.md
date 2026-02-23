# AGENTS.md

This file provides guidance to AI coding agents working with this ICDEV project.

---

## Project: ICDEV Project

| Field | Value |
|-------|-------|
| Type | webapp |
| Language | python |
| Impact Level | IL4 |
| Classification | CUI // SP-CTI |
| ATO Status | pre_ato |
| Cloud | aws_govcloud |

## Architecture: GOTCHA Framework

This project uses the GOTCHA framework — a 6-layer agentic system where AI orchestrates deterministic Python tools:

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions — what to achieve |
| **Orchestration** | *(you)* | Read goals, call tools, handle errors |
| **Tools** | `tools/` | Python scripts with `--json` output |
| **Args** | `args/` | YAML config (change behavior without code) |
| **Context** | `context/` | Static reference material |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates |

**Key principle:** You orchestrate; tools execute deterministically. Never implement business logic inline — delegate to the Python CLI tools.

## Essential Commands

```bash
# Project status
python tools/project/project_status.py --project-id "" --json

# Session context (load at start)
python tools/project/session_context_builder.py --format markdown

# Compliance
python tools/compliance/ssp_generator.py --project-id "" --json
python tools/compliance/stig_checker.py --project-id "" --json
python tools/compliance/sbom_generator.py --project-dir . --json

# Security
python tools/security/sast_runner.py --project-dir . --json
python tools/security/dependency_auditor.py --project-dir . --json
python tools/security/secret_detector.py --project-dir . --json

# Testing
pytest tests/ -v --tb=short
behave features/

# Builder
python tools/builder/test_writer.py --feature "description" --project-dir .
python tools/builder/code_generator.py --test-file tests/test_x.py --project-dir .

# CI/CD pipeline generation
python tools/ci/pipeline_config_generator.py --dir . --platform auto --dry-run --json

# Manifest validation
python tools/project/validate_manifest.py --file icdev.yaml --json
```

## MCP Servers

This project has 19 MCP servers available. Configure them in your tool to get full ICDEV capability:

| `playwright` | `python tools/mcp/playwright_server.py` |
| `icdev-core` | `python tools/mcp/core_server.py` |
| `icdev-compliance` | `python tools/mcp/compliance_server.py` |
| `icdev-builder` | `python tools/mcp/builder_server.py` |
| `icdev-infra` | `python tools/mcp/infra_server.py` |
| `icdev-knowledge` | `python tools/mcp/knowledge_server.py` |
| `icdev-maintenance` | `python tools/mcp/maintenance_server.py` |
| `icdev-mbse` | `python tools/mcp/mbse_server.py` |
| `icdev-modernization` | `python tools/mcp/modernization_server.py` |
| `icdev-requirements` | `python tools/mcp/requirements_server.py` |
| `icdev-supply-chain` | `python tools/mcp/supply_chain_server.py` |
| `icdev-simulation` | `python tools/mcp/simulation_server.py` |
| `icdev-integration` | `python tools/mcp/integration_server.py` |
| `icdev-marketplace` | `python tools/mcp/marketplace_server.py` |
| `icdev-devsecops` | `python tools/mcp/devsecops_server.py` |
| `icdev-gateway` | `python tools/mcp/gateway_server.py` |
| `icdev-context` | `python tools/mcp/context_server.py` |
| `icdev-innovation` | `python tools/mcp/innovation_server.py` |
| `icdev-observability` | `python tools/mcp/observability_server.py` |


See `.mcp.json` for full server definitions. Use `python tools/dx/mcp_config_generator.py --platform codex --json` to generate Codex-compatible MCP config.

## Coding Standards

- **All Python files** must start with `# CUI // SP-CTI`
- **Naming:** snake_case
- **Line length:** 100 characters max
- **Tests:** pytest (unit) + behave (BDD), >= 80% coverage
- **Formatting:** black + isort (Python), prettier (JS/TS)

## Compliance Guardrails

- CUI markings required on all generated files
- Security gates block on: CAT1 STIG findings, critical vulnerabilities, failed tests, missing markings
- Audit trail is append-only — never UPDATE/DELETE audit tables
- SBOM must be regenerated on every build
- fedramp_moderate compliance required


## Available Workflows

| Workflow | Description | Command |
|----------|-------------|---------|
| TDD Build | RED → GREEN → REFACTOR cycle | `$icdev-build "feature description"` |
| Test Suite | Full pytest + behave + security | `$icdev-test` |
| Compliance | Generate SSP, POAM, STIG, SBOM | `$icdev-comply` |
| Security | SAST + dependency audit + secrets | `$icdev-secure` |
| Deploy | IaC generation + pipeline | `$icdev-deploy` |
| Status | Project dashboard | `$icdev-status` |
| Init | Project initialization | `$icdev-init` |
| Review | Code review gates | `$icdev-review` |

Use `$skill-name` syntax to invoke these workflows if Codex skills are installed in `.agents/skills/`.

## Key Files

- `icdev.yaml` — Project manifest (single source of truth)
- `goals/manifest.md` — Index of all goal workflows
- `tools/manifest.md` — Master list of all tools
- `args/project_defaults.yaml` — Default configuration
- `data/icdev.db` — Operational database (SQLite)

---

*Generated by ICDEV Companion — `python tools/dx/companion.py --setup`*
