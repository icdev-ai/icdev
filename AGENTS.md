# AGENTS.md

This file provides guidance to AI coding agents working with this ICDEV™ project.

---

## Project: ICDEV™ Project

| Field | Value |
|-------|-------|
| Type | webapp |
| Language | python |
| Impact Level | IL4 |
| Classification | CUI // SP-CTI |
| ATO Status | pre_ato |
| Cloud | aws_govcloud |

## Architecture: FORGE Framework

This project uses the FORGE framework — a 6-layer agentic system where AI orchestrates deterministic Python tools:

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

This project has 2 MCP servers available. Configure them in your tool to get full ICDEV™ capability:

| `icdev-unified` | `python tools/mcp/unified_server.py` |
| `playwright` | `python tools/mcp/playwright_server.py` |


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

## Standalone Agent Runtime (SAG)

ICDEV ships a persistent, interactive agent runnable from a plain shell — **no
Claude Code, no web session**. It is a thin orchestration shell over the
production agent loop, chat persistence, provider abstraction, daemon/reflex
scheduler, NOVA skill generation, and the Remote Command Gateway — it adds **no
new LLM execution path or storage abstraction**. Package: `tools/agent_runtime/`.

```bash
icdev chat [-q "query"] [--resume <ctx-id>] [--stream]   # interactive / single-shot agent
icdev sessions list|export|search                        # conversation history (FTS)
icdev cron create|list|pause|resume|remove|run|runs      # user-facing cron (agent/script jobs)
icdev profile create|use|which|remove                    # directory-based operator profiles
python -m tools.agent_runtime.skills_lifecycle …         # HITL auto-skill lifecycle
```

Key rules when working on SAG:
- Mirror every new module to BOTH `tools/agent_runtime/` and
  `icdev/tools/agent_runtime/` (`tools/gateway/` is NOT mirrored to `icdev/`).
- SAG tests are **DB-independent** (faked persistence) — SAG tables self-create
  via `_ensure_schema()` and stay out of the conftest `MINIMAL_ICDEV_SCHEMA`.
- LLM-generated skills are **TRUST surfaces**: promotion is strictly HITL, with
  provenance frontmatter; the mutating file/terminal surface is never MCP-exposed.
- New Genesis reflexes (`agent_cron_reflex`, `sag_skill_curator`) must be
  registered in `REFLEX_NAMES` + `args/genesis_config.yaml`.

See `docs/features/phase-sag-standalone-agent.md`, `tools/manifest/standalone-agent-runtime.md`,
and ADRs D384–D390.

---

*Generated by ICDEV™ Companion — `python tools/dx/companion.py --setup`*

## Project Cards — Mandatory for Every Multi-Task Build

Regardless of where the build request originated (CLI session, Kanban, chat):
register a project card in `args/projects.yaml` AND seed kanban tasks via
`tools.kanban.task_factory.create_tasks` BEFORE implementation starts — one task
per shippable unit, descriptions rich enough for a fresh session to resume from
cold. This is the token-exhaustion handoff contract. Manual-only work is gated
behind a `<prefix>-gate-00` task held `in_progress` so the autonomous runner
never dispatches it. See the Project-cards guardrail in CLAUDE.md for details.

## Karpathy Principles — Pre-Design Engineering Gate

Before writing code, apply these 5 heuristics from `hardprompts/karpathy_principles.md`:

1. **State assumptions** — Name the constraints, inputs, invariants you're relying on. Unstated assumptions are where bugs hide.
2. **Enumerate interpretations** — For any ambiguous requirement, list the 2–4 ways it could be read before picking one. Surface them to the user if the choice is load-bearing.
3. **Prefer simpler** — Three similar lines beats one clever abstraction. Don't design for hypothetical future requirements. YAGNI.
4. **Bound your edit scope** — Only touch what the task requires. No drive-by refactors, no surrounding cleanup, no speculative error handling.
5. **Success criteria** — State how you'll know the change is done before writing it. If you can't write the test / acceptance check, the spec is incomplete.

Applies to: build, bug fix, refactor, TDD, and code review workflows.
