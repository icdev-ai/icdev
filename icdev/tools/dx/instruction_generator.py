#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate AI coding tool instruction files from universal project data.

ADR D195: Instruction files generated from Jinja2 templates — tool-specific
formatting from universal project data (D50/D186 pattern).

Reads icdev.yaml + project state and generates instruction files for each
AI coding tool: AGENTS.md, GEMINI.md, .cursor/rules/icdev.mdc, etc.

Usage:
    python tools/dx/instruction_generator.py --all --write
    python tools/dx/instruction_generator.py --platform codex --dry-run --json
    python tools/dx/instruction_generator.py --platform cursor,copilot --write
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from jinja2 import Environment, BaseLoader

    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False

try:
    import yaml as _yaml

    def _load_yaml(path):
        with open(path, encoding="utf-8") as f:
            return _yaml.safe_load(f)
except ImportError:
    _yaml = None  # type: ignore[assignment]

    def _load_yaml(path):
        with open(path, encoding="utf-8") as f:
            return json.loads(f.read())


REGISTRY_PATH = BASE_DIR / "args" / "companion_registry.yaml"


# ── Template Helpers ────────────────────────────────────────────────────


def _render(template_str, data):
    """Render a Jinja2 template string, fallback to str.format for basics."""
    if _HAS_JINJA2:
        env = Environment(loader=BaseLoader(), keep_trailing_newline=True)  # nosec B701 — generates Markdown, not HTML
        env.filters["default"] = lambda v, d="": v if v else d
        tmpl = env.from_string(template_str)
        return tmpl.render(**data)
    # Minimal fallback
    return template_str


# ── Project Data Collection ─────────────────────────────────────────────


def collect_project_data(directory=None):
    """Collect universal project data for template rendering.

    Sources: icdev.yaml (manifest_loader), tools manifest summary,
    goals summary, dev profile defaults.
    """
    directory = Path(directory) if directory else Path.cwd()
    data = {
        "project_name": "ICDEV™ Project",
        "project_type": "webapp",
        "project_language": "python",
        "project_id": "",
        "impact_level": "IL4",
        "classification_level": "CUI",
        "classification_category": "SP-CTI",
        "cui_markings": True,
        "frameworks": ["fedramp_moderate"],
        "ato_status": "pre_ato",
        "cloud": "aws_govcloud",
        "deployment_platform": "k8s",
        "profile_template": "dod_baseline",
        "line_length": 100,
        "naming_convention": "snake_case",
        "test_framework": "pytest",
        "bdd_framework": "behave",
        "min_coverage": 80,
        "has_icdev_yaml": False,
        "has_mcp_json": (directory / ".mcp.json").exists(),
        "mcp_server_count": 0,
    }

    # Try to load icdev.yaml via manifest_loader
    try:
        from tools.project.manifest_loader import load_manifest

        result = load_manifest(directory=str(directory))
        if result["valid"]:
            cfg = result["normalized"]
            proj = cfg.get("project", {})
            data["project_name"] = proj.get("name", data["project_name"])
            data["project_type"] = proj.get("type", data["project_type"])
            data["project_language"] = proj.get("language", data["project_language"])
            data["project_id"] = proj.get("id", "")
            data["impact_level"] = cfg.get("impact_level", data["impact_level"])
            cl = cfg.get("classification", {})
            data["classification_level"] = cl.get("level", data["classification_level"])
            data["classification_category"] = cl.get("category", data["classification_category"])
            data["cui_markings"] = cl.get("cui_markings", data["cui_markings"])
            data["frameworks"] = cfg.get("compliance", {}).get("frameworks", data["frameworks"])
            data["ato_status"] = cfg.get("compliance", {}).get("ato", {}).get("status", data["ato_status"])
            data["cloud"] = cfg.get("deployment", {}).get("cloud", data["cloud"])
            data["deployment_platform"] = cfg.get("deployment", {}).get("platform", data["deployment_platform"])
            data["profile_template"] = cfg.get("profile", {}).get("template", data["profile_template"])
            data["has_icdev_yaml"] = True

            # Pipeline checks
            pipeline = cfg.get("pipeline", {})
            data["on_pr_checks"] = pipeline.get("on_pr", [])
            data["on_merge_checks"] = pipeline.get("on_merge", [])
            data["gates"] = pipeline.get("gates", {})
    except Exception:
        pass

    # Count MCP servers
    mcp_path = directory / ".mcp.json"
    if mcp_path.exists():
        try:
            with open(mcp_path, encoding="utf-8") as f:
                mcp = json.loads(f.read())
            data["mcp_server_count"] = len(mcp.get("mcpServers", {}))
            data["mcp_server_names"] = list(mcp.get("mcpServers", {}).keys())
        except Exception:
            pass

    # Companion config from icdev.yaml
    data.setdefault("on_pr_checks", ["sast", "unit_tests", "lint"])
    data.setdefault("on_merge_checks", [])
    data.setdefault("gates", {})
    data.setdefault("mcp_server_names", [])

    return data


# ── Templates ───────────────────────────────────────────────────────────
# Stored as string constants (D186 pattern). Each template is tailored
# to its target tool's conventions and size constraints.

# Canonical Karpathy principles block — enforced across all 10 AI platform
# configs by tools/workflow/coherence_checker.py::check_karpathy_sync.
# Edit here, then run: python tools/dx/companion.py --sync --write --json
KARPATHY_PRINCIPLES_BLOCK = """
## Karpathy Principles — Pre-Design Engineering Gate

Before writing code, apply these 5 heuristics from `hardprompts/karpathy_principles.md`:

1. **State assumptions** — Name the constraints, inputs, invariants you're relying on. Unstated assumptions are where bugs hide.
2. **Enumerate interpretations** — For any ambiguous requirement, list the 2–4 ways it could be read before picking one. Surface them to the user if the choice is load-bearing.
3. **Prefer simpler** — Three similar lines beats one clever abstraction. Don't design for hypothetical future requirements. YAGNI.
4. **Bound your edit scope** — Only touch what the task requires. No drive-by refactors, no surrounding cleanup, no speculative error handling.
5. **Success criteria** — State how you'll know the change is done before writing it. If you can't write the test / acceptance check, the spec is incomplete.

Applies to: build, bug fix, refactor, TDD, and code review workflows.
"""
# ────────────────────────────────────────────────────────────────────────

TEMPLATE_AGENTS_MD = r"""# AGENTS.md

This file provides guidance to AI coding agents working with this ICDEV™ project.

---

## Project: {{ project_name }}

| Field | Value |
|-------|-------|
| Type | {{ project_type }} |
| Language | {{ project_language }} |
| Impact Level | {{ impact_level }} |
| Classification | {{ classification_level }} // {{ classification_category }} |
| ATO Status | {{ ato_status }} |
| Cloud | {{ cloud }} |

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
python tools/project/project_status.py --project-id "{{ project_id }}" --json

# Session context (load at start)
python tools/project/session_context_builder.py --format markdown

# Compliance
python tools/compliance/ssp_generator.py --project-id "{{ project_id }}" --json
python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json
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

This project has {{ mcp_server_count }} MCP servers available. Configure them in your tool to get full ICDEV™ capability:

{% for name in mcp_server_names %}| `{{ name }}` | `python tools/mcp/{{ name.replace('icdev-', '').replace('-', '_') }}_server.py` |
{% endfor %}

See `.mcp.json` for full server definitions. Use `python tools/dx/mcp_config_generator.py --platform codex --json` to generate Codex-compatible MCP config.

## Coding Standards

{% if cui_markings %}- **All Python files** must start with `# CUI // {{ classification_category }}`
{% endif %}- **Naming:** {{ naming_convention }}
- **Line length:** {{ line_length }} characters max
- **Tests:** {{ test_framework }} (unit) + {{ bdd_framework }} (BDD), >= {{ min_coverage }}% coverage
- **Formatting:** black + isort (Python), prettier (JS/TS)

## Compliance Guardrails

{% if cui_markings %}- CUI markings required on all generated files
{% endif %}- Security gates block on: CAT1 STIG findings, critical vulnerabilities, failed tests, missing markings
- Audit trail is append-only — never UPDATE/DELETE audit tables
- SBOM must be regenerated on every build
{% for fw in frameworks %}- {{ fw }} compliance required
{% endfor %}

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

## Project Cards — Mandatory for Every Multi-Task Build

Regardless of where the build request originated (CLI session, Kanban, chat):
register a project card in `args/projects.yaml` AND seed kanban tasks via
`tools.kanban.task_factory.create_tasks` BEFORE implementation starts — one task
per shippable unit, descriptions rich enough for a fresh session to resume from
cold. This is the token-exhaustion handoff contract. Manual-only work is gated
behind a `<prefix>-gate-00` task held `in_progress` so the autonomous runner
never dispatches it. See the Project-cards guardrail in CLAUDE.md for details.

---

*Generated by ICDEV™ Companion — `python tools/dx/companion.py --setup`*
"""

TEMPLATE_GEMINI_MD = r"""# GEMINI.md

Guidance for Google Gemini when working with this ICDEV™ project.

## Project: {{ project_name }}

- **Type:** {{ project_type }} | **Language:** {{ project_language }}
- **Impact Level:** {{ impact_level }} | **Classification:** {{ classification_level }}
- **ATO Status:** {{ ato_status }}

## Architecture

This project uses the FORGE framework: Goals define workflows, Tools (Python CLI scripts in `tools/`) execute deterministically, Args/Context/Hard Prompts configure behavior. You orchestrate by reading goals and calling tools.

All tools support `--json` for machine-readable output.

## Essential Commands

```bash
# Load project context at session start
python tools/project/session_context_builder.py --format markdown

# Testing
pytest tests/ -v --tb=short
behave features/

# Security scanning
python tools/security/sast_runner.py --project-dir . --json
python tools/security/dependency_auditor.py --project-dir . --json

# Compliance artifacts
python tools/compliance/ssp_generator.py --project-id "{{ project_id }}" --json
python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json
python tools/compliance/sbom_generator.py --project-dir . --json

# Project status
python tools/project/project_status.py --project-id "{{ project_id }}" --json
```

## MCP Servers

{{ mcp_server_count }} MCP servers available. Add to `.gemini/settings.json`:

```json
{
  "mcpServers": {
    "icdev-core": {
      "command": "python",
      "args": ["tools/mcp/core_server.py"],
      "env": {"ICDEV_DB_PATH": "data/icdev.db", "ICDEV_PROJECT_ROOT": "."}
    }
  }
}
```

Run `python tools/dx/mcp_config_generator.py --platform gemini --json` for full config.

## Coding Rules

{% if cui_markings %}1. All Python files must start with `# CUI // {{ classification_category }}`
{% endif %}2. Use {{ naming_convention }} naming convention
3. Max line length: {{ line_length }}
4. All code changes require tests ({{ test_framework }} + {{ bdd_framework }})
5. Min coverage: {{ min_coverage }}%
6. Security gates: 0 CAT1 STIG findings, 0 critical vulns, 0 secrets

## Key Files

| File | Purpose |
|------|---------|
| `icdev.yaml` | Project manifest |
| `goals/manifest.md` | Workflow index |
| `tools/manifest.md` | Tool index |

---

*Generated by ICDEV™ Companion*
"""

TEMPLATE_COPILOT_MD = r"""# Copilot Instructions — {{ project_name }}

This is an ICDEV™-managed project ({{ impact_level }}, {{ classification_level }}).

## Coding Standards

{% if cui_markings %}- All Python files must begin with `# CUI // {{ classification_category }}`
{% endif %}- Use {{ naming_convention }} naming convention
- Maximum line length: {{ line_length }} characters
- Tests required: {{ test_framework }} (unit) + {{ bdd_framework }} (BDD)
- Minimum coverage: {{ min_coverage }}%

## Architecture

FORGE framework: deterministic Python tools in `tools/` with `--json` output. Read `goals/manifest.md` for workflows, `tools/manifest.md` for available tools.

## Key Commands

```bash
python tools/project/session_context_builder.py --format markdown  # Project context
pytest tests/ -v --tb=short                                         # Unit tests
behave features/                                                    # BDD tests
python tools/security/sast_runner.py --project-dir . --json         # Security scan
python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json  # STIG check
```

## Compliance

{% for fw in frameworks %}- {{ fw }}
{% endfor %}
Security gates block on: CAT1 STIG, critical vulns, failed tests, missing CUI markings.
{% if cui_markings %}
## CUI Markings

Every generated file must include classification marking `# CUI // {{ classification_category }}` as the first comment line.
{% endif %}

## Prompt Files

Custom workflows available in `.github/prompts/`:
- `icdev-build.prompt.md` — TDD build cycle
- `icdev-test.prompt.md` — Full test suite
- `icdev-comply.prompt.md` — Compliance artifacts
- `icdev-secure.prompt.md` — Security scanning

Reference with `#prompt:icdev-build` in Copilot Chat.

---

*Generated by ICDEV™ Companion*
"""

TEMPLATE_CURSOR_MDC = r"""---
description: ICDEV™ project rules — {{ impact_level }} {{ classification_level }} compliance-aware development
globs:
  - "**/*.py"
  - "**/*.yaml"
  - "**/*.md"
  - "**/*.json"
alwaysApply: true
---

# ICDEV™ Project: {{ project_name }}

**{{ impact_level }}** | **{{ classification_level }}** | **{{ project_language }}** {{ project_type }}

## Architecture

FORGE framework: you orchestrate deterministic Python CLI tools in `tools/`. All tools support `--json`. Read `goals/manifest.md` for workflows.

## Rules

{% if cui_markings %}1. All Python files start with `# CUI // {{ classification_category }}`
{% endif %}2. {{ naming_convention }} naming, {{ line_length }}-char lines
3. Tests: `pytest tests/` + `behave features/`, >= {{ min_coverage }}% coverage
4. Security: 0 CAT1 STIG, 0 critical vulns, 0 secrets
5. Load context: `python tools/project/session_context_builder.py --format markdown`

## Key Commands

```bash
pytest tests/ -v --tb=short                                    # Unit tests
python tools/security/sast_runner.py --project-dir . --json    # SAST
python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json
python tools/project/project_status.py --project-id "{{ project_id }}" --json
```

## MCP

{{ mcp_server_count }} servers available. Register in Cursor MCP settings — see `.mcp.json` for definitions or run `python tools/dx/mcp_config_generator.py --platform cursor`.
"""

TEMPLATE_WINDSURF_MD = r"""# ICDEV™ Project Rules — {{ project_name }}

**{{ impact_level }}** | **{{ classification_level }}** | **{{ project_language }}** {{ project_type }}

## Architecture

FORGE framework with deterministic Python CLI tools in `tools/`. All support `--json` output.

## Coding Rules

{% if cui_markings %}- All Python files: `# CUI // {{ classification_category }}` header
{% endif %}- {{ naming_convention }} naming, {{ line_length }}-char max lines
- Tests: {{ test_framework }} + {{ bdd_framework }}, >= {{ min_coverage }}% coverage
- Security gates: 0 CAT1 STIG, 0 critical vulns

## Essential Commands

```bash
python tools/project/session_context_builder.py --format markdown
pytest tests/ -v --tb=short
python tools/security/sast_runner.py --project-dir . --json
python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json
```

## MCP

{{ mcp_server_count }} MCP servers — see `.mcp.json` for definitions.

---

*Generated by ICDEV™ Companion*
"""

TEMPLATE_AMAZON_Q_MD = r"""# ICDEV™ Project Rules — {{ project_name }}

## Project

| Field | Value |
|-------|-------|
| Type | {{ project_type }} |
| Language | {{ project_language }} |
| Impact Level | {{ impact_level }} |
| Classification | {{ classification_level }} |
| Cloud | {{ cloud }} |

## Architecture

FORGE framework: deterministic Python tools in `tools/` with `--json` output. Goals in `goals/`, config in `args/`.

## AWS GovCloud

This project targets {{ cloud }}. All LLM inference via Amazon Bedrock. Secrets via AWS Secrets Manager.

## Coding Rules

{% if cui_markings %}- CUI marking required: `# CUI // {{ classification_category }}`
{% endif %}- {{ naming_convention }}, {{ line_length }}-char lines, {{ test_framework }} >= {{ min_coverage }}%

## Commands

```bash
python tools/project/session_context_builder.py --format markdown
pytest tests/ -v
python tools/security/sast_runner.py --project-dir . --json
python tools/compliance/ssp_generator.py --project-id "{{ project_id }}" --json
```

## MCP

{{ mcp_server_count }} servers. Config: `.amazonq/mcp.json` — run `python tools/dx/mcp_config_generator.py --platform amazon_q --write`.

---

*Generated by ICDEV™ Companion*
"""

TEMPLATE_JUNIE_MD = r"""# ICDEV™ Project Guidelines — {{ project_name }}

## Overview

{{ project_language | capitalize }} {{ project_type }} project at {{ impact_level }} ({{ classification_level }}).

Uses the FORGE framework: deterministic Python CLI tools in `tools/` with `--json` output, goal workflows in `goals/`, configuration in `args/`.

## Coding Conventions

{% if cui_markings %}- Every Python file starts with `# CUI // {{ classification_category }}`
{% endif %}- Naming: {{ naming_convention }}
- Line length: {{ line_length }} characters
- Testing: {{ test_framework }} (unit) + {{ bdd_framework }} (BDD), minimum {{ min_coverage }}% coverage
- Format: black + isort | Security: bandit

## Key Commands

```bash
python tools/project/session_context_builder.py --format markdown
pytest tests/ -v --tb=short
behave features/
python tools/security/sast_runner.py --project-dir . --json
```

## MCP Servers

{{ mcp_server_count }} MCP servers available. Configure in JetBrains Settings > AI Assistant > MCP. Server definitions in `.mcp.json`.

---

*Generated by ICDEV™ Companion*
"""

TEMPLATE_CLINERULES = r"""# ICDEV™ Project Rules

Project: {{ project_name }} ({{ impact_level }}, {{ classification_level }})
Type: {{ project_language }} {{ project_type }}

## Architecture

FORGE framework — deterministic Python tools in `tools/` with `--json` output.
Goals: `goals/manifest.md` | Tools: `tools/manifest.md` | Config: `args/`

## Rules

{% if cui_markings %}- All Python files: `# CUI // {{ classification_category }}` header required
{% endif %}- {{ naming_convention }} naming, {{ line_length }}-char lines
- Tests: {{ test_framework }} + {{ bdd_framework }}, >= {{ min_coverage }}% coverage
- Security: 0 CAT1 STIG, 0 critical vulns, 0 secrets

## Commands

- Context: `python tools/project/session_context_builder.py --format markdown`
- Tests: `pytest tests/ -v`
- SAST: `python tools/security/sast_runner.py --project-dir . --json`
- STIG: `python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json`

## MCP Categories

@security: sast_runner, dependency_auditor, secret_detector
@compliance: ssp_generator, stig_checker, sbom_generator, control_mapper
@build: test_writer, code_generator, scaffolder, formatter, linter
@project: project_status, project_create, session_context_builder
"""

TEMPLATE_CONVENTIONS_MD = r"""# CONVENTIONS.md — {{ project_name }}

Coding conventions for this ICDEV™-managed project.

## Language & Style

- **Primary language:** {{ project_language }}
- **Naming convention:** {{ naming_convention }}
- **Max line length:** {{ line_length }}
- **Formatter:** black + isort (Python) | prettier (JS/TS)
- **Linter:** ruff (Python) | eslint (JS/TS)

{% if cui_markings %}## Classification Markings

Every generated file must include `# CUI // {{ classification_category }}` as the first comment line. This is a compliance requirement for {{ impact_level }} projects.
{% endif %}

## Testing

- **Unit tests:** {{ test_framework }} (`pytest tests/ -v`)
- **BDD tests:** {{ bdd_framework }} (`behave features/`)
- **Minimum coverage:** {{ min_coverage }}%
- Write tests before implementation (TDD)

## Security

- Run SAST: `python tools/security/sast_runner.py --project-dir . --json`
- Dependency audit: `python tools/security/dependency_auditor.py --project-dir . --json`
- Secret detection: `python tools/security/secret_detector.py --project-dir . --json`

## Architecture

This project uses the FORGE framework. Tools are in `tools/` (deterministic Python scripts, all support `--json`). Workflows are in `goals/`. Configuration in `args/`.

## Available CLI Tools

```bash
python tools/project/session_context_builder.py --format markdown  # Load project context
python tools/project/project_status.py --project-id "{{ project_id }}" --json
python tools/compliance/ssp_generator.py --project-id "{{ project_id }}" --json
python tools/compliance/stig_checker.py --project-id "{{ project_id }}" --json
```

---

*Generated by ICDEV™ Companion*
"""


TEMPLATE_GOOSEHINTS = r"""# .goosehints — {{ project_name }}
# CUI // {{ classification_category }}

## Project Overview

{{ project_name }} is an ICDEV™-managed {{ project_type }} ({{ project_language }}).
Classification: {{ classification_level }} // {{ classification_category }} ({{ impact_level }})

## FORGE Framework (6 Layers)

1. **Goals** (`goals/`) — Workflow definitions (check `goals/manifest.md` first)
2. **Orchestration** (you) — Read goal, decide tool order, apply args, handle errors
3. **Tools** (`tools/`) — Deterministic Python scripts, one job each (all support `--json`)
4. **Args** (`args/`) — YAML/JSON behavior config (change behavior without editing code)
5. **Context** (`context/`) — Static reference material
6. **Hard Prompts** (`hardprompts/`) — Reusable LLM instruction templates

## How to Operate

- Check `goals/manifest.md` before starting any task
- Check `tools/manifest.md` before writing new code
- All tools support `--json` and `--gate` flags
- When tools fail: read error, fix tool, update goal
- Run `python tools/dx/companion.py --sync --write --json` after code changes

## LLM Router

Use the `llm_invoke` MCP tool (ICDEV™ LLM Router extension) for all LLM calls.
Three tiers: Planner (Claude), Worker (qwen3.5 draft + Claude review), Scanner (Ollama only).
Use `llm_resolve` to check which model handles a function before invoking.

{% if cui_markings %}## Classification Markings

Every generated file must include `# CUI // {{ classification_category }}` as the first comment line. This is a compliance requirement for {{ impact_level }} projects.
{% endif %}

## Testing

- **Syntax:** `python -m py_compile <file>`
- **Lint:** `ruff check <file>`
- **Unit tests:** {{ test_framework }} (`pytest tests/ -q`)
- **BDD tests:** {{ bdd_framework }} (`behave features/`)
- **Security:** `python -m bandit -r <file> --severity-level medium`
- **Tool verify:** Run each tool with `--json` and `--gate`

## Key Directories

| Dir | Purpose |
|-----|---------|
| `tools/` | 251+ Python tools (deterministic, one job each) |
| `goals/` | Workflow definitions |
| `args/` | YAML config (llm_config.yaml, security_gates.yaml) |
| `data/` | SQLite databases (icdev.db, memory.db, activity.db) |
| `hardprompts/` | Reusable LLM instruction templates |
| `context/` | Static reference material |

## Guardrails

- Never delete audit tables (append-only, NIST AU)
- Always include CUI classification markings
- Use `pathlib.Path`, `encoding='utf-8'`, `datetime.now(timezone.utc)`
- LLM config via `.env`, never hardcode model IDs
- Cross-platform: forward slashes, `tempfile.gettempdir()`, `hashlib.sha256`
- Security gates block on: CAT1 STIG, critical vulns, failed tests, missing markings

## Available CLI Tools

```bash
python tools/project/session_context_builder.py --format markdown  # Load context
python tools/testing/health_check.py --json                        # Health check
python tools/testing/test_orchestrator.py --project-dir . --json   # Run tests
python tools/compliance/ssp_generator.py --project-id "{{ project_id }}" --json
python tools/security/sast_runner.py --project-dir . --json        # SAST scan
python tools/dx/companion.py --sync --write --json                 # Companion sync
python tools/workflow/coherence_checker.py --all --fix --gate      # Coherence
```

---

*Generated by ICDEV™ Companion*
"""


# ── Template Registry ───────────────────────────────────────────────────

TEMPLATES = {
    "codex": TEMPLATE_AGENTS_MD,
    "gemini": TEMPLATE_GEMINI_MD,
    "copilot": TEMPLATE_COPILOT_MD,
    "cursor": TEMPLATE_CURSOR_MDC,
    "windsurf": TEMPLATE_WINDSURF_MD,
    "amazon_q": TEMPLATE_AMAZON_Q_MD,
    "junie": TEMPLATE_JUNIE_MD,
    "cline": TEMPLATE_CLINERULES,
    "aider": TEMPLATE_CONVENTIONS_MD,
    "goose": TEMPLATE_GOOSEHINTS,
}


# ── Generator ───────────────────────────────────────────────────────────


def generate_instructions(directory=None, platforms=None, style="full", write=False, dry_run=False):
    """Generate instruction files for specified platforms.

    Args:
        directory: Project directory (default: cwd).
        platforms: List of tool_ids or ['all']. Default: all except claude_code.
        style: 'full' or 'minimal'.
        write: Write files to disk.
        dry_run: Preview without writing.

    Returns:
        dict: {platform: {path, content, written, size_bytes}}
    """
    directory = Path(directory) if directory else Path.cwd()
    registry = _load_yaml(str(REGISTRY_PATH)) if REGISTRY_PATH.exists() else {}
    companions = registry.get("companions", {})
    skip = registry.get("defaults", {}).get("skip_platforms", [])

    # Resolve platform list
    if platforms is None or platforms == ["all"]:
        platforms = [k for k in TEMPLATES if k not in skip]
    elif isinstance(platforms, str):
        platforms = [p.strip() for p in platforms.split(",")]

    data = collect_project_data(directory)
    results = {}

    for platform in platforms:
        template_str = TEMPLATES.get(platform)
        if not template_str:
            results[platform] = {"error": f"No template for platform: {platform}"}
            continue

        companion_cfg = companions.get(platform, {})
        output_path = companion_cfg.get("instruction_file", f"{platform}.md")
        content = _render(template_str, data)

        # Append Karpathy principles block — required in every platform config.
        # Checked by tools/workflow/coherence_checker.py::check_karpathy_sync.
        if "State assumptions" not in content:
            content = content.rstrip() + "\n" + KARPATHY_PRINCIPLES_BLOCK

        full_path = directory / output_path
        written = False

        if write and not dry_run:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8", newline="")
            written = True

        results[platform] = {
            "path": str(output_path),
            "full_path": str(full_path),
            "content": content,
            "written": written,
            "size_bytes": len(content.encode("utf-8")),
            "display_name": companion_cfg.get("display_name", platform),
        }

    return results


def main():
    parser = argparse.ArgumentParser(description="Generate AI coding tool instruction files")
    parser.add_argument("--dir", help="Project directory")
    parser.add_argument("--platform", help="Comma-separated platform IDs")
    parser.add_argument("--all", action="store_true", help="All platforms")
    parser.add_argument("--style", default="full", choices=["full", "minimal"])
    parser.add_argument("--write", action="store_true", help="Write files to disk")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    platforms = ["all"] if args.all else ([p.strip() for p in args.platform.split(",")] if args.platform else None)

    results = generate_instructions(
        directory=args.dir,
        platforms=platforms,
        style=args.style,
        write=args.write,
        dry_run=args.dry_run,
    )

    if args.json:
        # Omit content in JSON summary unless dry_run
        out = {}
        for k, v in results.items():
            entry = {kk: vv for kk, vv in v.items() if kk != "content"}
            if args.dry_run:
                entry["content_preview"] = v.get("content", "")[:500]
            out[k] = entry
        print(json.dumps(out, indent=2))
    else:
        for platform, info in results.items():
            if "error" in info:
                print(f"ERROR [{platform}]: {info['error']}")
                continue
            status = "WRITTEN" if info["written"] else ("DRY-RUN" if args.dry_run else "PREVIEW")
            print(f"[{status}] {info['display_name']}: {info['path']} ({info['size_bytes']} bytes)")

        if not args.write and not args.dry_run:
            print("\nUse --write to save files or --dry-run to preview content.")


if __name__ == "__main__":
    main()
