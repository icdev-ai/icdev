#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Dynamic CLAUDE.md Generator - creates adaptive documentation for child apps.

Architecture Decision D26: Jinja2 templates produce CLAUDE.md that documents
only present capabilities.  Never references tools, agents, or features not
included in the child app.

Consumes a blueprint JSON (output of tools/builder/app_blueprint.py) and
renders a project-specific CLAUDE.md file.  When Jinja2 is available the full
template engine is used; otherwise a deterministic string-based fallback
produces equivalent output.

Usage:
    # Render to stdout
    python tools/builder/claude_md_generator.py --blueprint /path/to/blueprint.json

    # Render to file
    python tools/builder/claude_md_generator.py --blueprint /path/to/blueprint.json \
        --output /path/to/child-app/CLAUDE.md

    # JSON envelope (metadata + content)
    python tools/builder/claude_md_generator.py --blueprint /path/to/blueprint.json --json

Classification: CUI // SP-CTI
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
logger = logging.getLogger("icdev.claude_md_generator")

try:
    from jinja2 import Environment, BaseLoader
    _HAS_JINJA2 = True
except ImportError:
    _HAS_JINJA2 = False
    Environment = None  # type: ignore[assignment,misc]

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):  # type: ignore[misc]
        logger.debug("audit_logger unavailable -- skipping audit event")


# ===========================================================================
# JINJA2 TEMPLATE
# ===========================================================================
# The template is stored as a Python string constant so the tool remains a
# single self-contained file with zero filesystem dependencies beyond the
# blueprint JSON.
#
# Template variables come directly from the blueprint dict produced by
# tools/builder/app_blueprint.py.  All sections are conditionally rendered
# so the output never references capabilities, agents, or tools that are
# absent from the child app.
# ===========================================================================

CLAUDE_MD_TEMPLATE = r"""# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with {{ app_name }}.

---

## Quick Reference

### Commands
```bash
# Memory system
python tools/memory/memory_read.py --format markdown          # Load all memory
python tools/memory/memory_write.py --content "text" --type event  # Write to daily log + DB
python tools/memory/memory_write.py --content "text" --type fact --importance 7  # Store a fact
python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences  # Update MEMORY.md
python tools/memory/memory_db.py --action search --query "keyword"   # Keyword search
python tools/memory/semantic_search.py --query "concept"             # Semantic search (requires OpenAI key)
python tools/memory/hybrid_search.py --query "query"                 # Best: combined keyword + semantic
python tools/memory/embed_memory.py --all                            # Generate embeddings for all entries
```
{% if capabilities.get("testing", False) %}

### Testing Commands
```bash
python tools/testing/health_check.py                 # Full system health check
python tools/testing/health_check.py --json           # JSON output
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/e2e_runner.py --discover         # List available E2E test specs
python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests
```
{% endif %}
{% if capabilities.get("compliance", False) %}

### Compliance Commands
```bash
python tools/compliance/ssp_generator.py --project-id "{{ app_name }}"
python tools/compliance/poam_generator.py --project-id "{{ app_name }}"
python tools/compliance/stig_checker.py --project-id "{{ app_name }}"
python tools/compliance/sbom_generator.py --project-dir "/path/to/project"
python tools/compliance/cui_marker.py --file "/path/to/file" --marking "{{ classification }} // SP-CTI"
python tools/compliance/nist_lookup.py --control "AC-2"
python tools/compliance/control_mapper.py --activity "code.commit" --project-id "{{ app_name }}"
python tools/compliance/crosswalk_engine.py --control AC-2
python tools/compliance/crosswalk_engine.py --project-id "{{ app_name }}" --coverage
python tools/compliance/fedramp_assessor.py --project-id "{{ app_name }}" --baseline moderate
python tools/compliance/cmmc_assessor.py --project-id "{{ app_name }}" --level 2
python tools/compliance/oscal_generator.py --project-id "{{ app_name }}" --artifact ssp
python tools/compliance/classification_manager.py --impact-level {{ impact_level }}
```
{% endif %}
{% if capabilities.get("security", False) %}

### Security Commands
```bash
python tools/security/sast_runner.py --project-dir "/path"
python tools/security/dependency_auditor.py --project-dir "/path"
python tools/security/secret_detector.py --project-dir "/path"
python tools/security/container_scanner.py --image "{{ app_name }}:latest"
```
{% endif %}
{% if capabilities.get("mbse", False) %}

### MBSE Commands
```bash
python tools/mbse/xmi_parser.py --project-id "{{ app_name }}" --file /path/model.xmi --json
python tools/mbse/reqif_parser.py --project-id "{{ app_name }}" --file /path/reqs.reqif --json
python tools/mbse/digital_thread.py --project-id "{{ app_name }}" auto-link --json
python tools/mbse/digital_thread.py --project-id "{{ app_name }}" coverage --json
python tools/mbse/model_code_generator.py --project-id "{{ app_name }}" --language python --output ./src
python tools/mbse/sync_engine.py --project-id "{{ app_name }}" detect-drift --json
python tools/mbse/des_assessor.py --project-id "{{ app_name }}" --project-dir /path --json
```
{% endif %}
{% if capabilities.get("infra", False) %}

### Infrastructure Commands
```bash
python tools/infra/terraform_generator.py --project-id "{{ app_name }}"
python tools/infra/ansible_generator.py --project-id "{{ app_name }}"
python tools/infra/k8s_generator.py --project-id "{{ app_name }}"
python tools/infra/pipeline_generator.py --project-id "{{ app_name }}"
python tools/infra/rollback.py --deployment-id "deploy-123"
```
{% endif %}
{% if capabilities.get("cicd", False) %}

### CI/CD Commands
```bash
python tools/ci/triggers/webhook_server.py           # Start webhook server
python tools/ci/triggers/poll_trigger.py             # Start issue polling
python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline
```
{% endif %}
{% if capabilities.get("dashboard", False) %}

### Dashboard
```bash
python tools/dashboard/app.py                        # Start web dashboard on port 5000
```
{% endif %}

---

## Architecture: GOTCHA Framework

This is a 6-layer agentic system.  The AI (you) is the orchestration layer -- you read goals, call tools, apply args, reference context, and use hard prompts.  You never execute work directly; you delegate to deterministic Python scripts.

**Why:** LLMs are probabilistic.  Business logic must be deterministic.  90% accuracy/step = ~59% over 5 steps.  Separation of concerns fixes this.

### The 6 Layers

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions -- what to achieve, which tools to use, expected outputs, edge cases |
| **Orchestration** | *(you)* | Read goal -> decide tool order -> apply args -> reference context -> handle errors |
| **Tools** | `tools/` | Python scripts, one job each.  Deterministic.  Don't think, just execute. |
| **Args** | `args/` | YAML/JSON behavior settings (themes, modes, schedules).  Change behavior without editing goals/tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, ICP descriptions, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates (outline->post, rewrite-in-voice, summarize) |

### Key Files

- `goals/manifest.md` -- Index of all goal workflows.  Check before starting any task.
- `tools/manifest.md` -- Master list of all tools.  Check before writing a new script.
- `memory/MEMORY.md` -- Curated long-term facts/preferences, read at session start.
- `memory/logs/YYYY-MM-DD.md` -- Daily session logs.
- `.env` -- API keys and environment variables.
- `.tmp/` -- Disposable scratch work.  Never store important data here.

### Memory System Architecture

Dual storage: markdown files (human-readable) + SQLite databases (searchable).

**Databases:**
- `data/memory.db` -- `memory_entries` (with embeddings), `daily_logs`, `memory_access_log`
- `data/activity.db` -- `tasks` table for tracking

**Memory types:** fact, preference, event, insight, task, relationship

**Search ranking:** Hybrid search uses 0.7 * BM25 (keyword) + 0.3 * semantic (vector).  Configurable via `--bm25-weight` and `--semantic-weight` flags.

**Embeddings:** OpenAI text-embedding-3-small (1536 dims), stored as BLOBs in SQLite.

---

## How to Operate

1. **Check goals first** -- Read `goals/manifest.md` before starting a task.  If a goal exists, follow it.
2. **Check tools first** -- Read `tools/manifest.md` before writing new code.  If you create a new tool, add it to the manifest.
3. **When tools fail** -- Read the error, fix the tool, update the goal with what you learned (rate limits, batching, timing).
4. **Goals are living docs** -- Update when better approaches emerge.  Never modify/create goals without explicit permission.
5. **When stuck** -- Explain what is missing and what you need.  Do not guess or invent capabilities.

### Session Start Protocol

1. Read `memory/MEMORY.md` for long-term context
2. Read today's daily log (`memory/logs/YYYY-MM-DD.md`)
3. Read yesterday's log for continuity
4. Or run: `python tools/memory/memory_read.py --format markdown`

---

## {{ app_name }} System
{% if classification %}

### Classification

**Impact Level:** {{ impact_level }}
**Classification:** {{ classification }}{% if classification == "CUI" %} // SP-CTI{% endif %}

All generated artifacts MUST include classification markings appropriate to impact level.
{% endif %}

### Multi-Agent Architecture ({{ agents | length }} Agents)

| Tier | Agent | Port | Role |
|------|-------|------|------|
{% for agent in agents %}| {{ agent.tier }} | {{ agent.name | capitalize }} | {{ agent.port }} | {{ agent.role }} |
{% endfor %}

Agents communicate via **A2A protocol** (JSON-RPC 2.0 over mutual TLS within K8s).  Each publishes an Agent Card at `/.well-known/agent.json`.
{% if mcp_servers %}

### MCP Servers ({{ mcp_servers | length }} stdio servers for Claude Code)

| Server | Tools |
|--------|-------|
{% for server in mcp_servers %}| {{ server.name }} | {{ server.tools }} |
{% endfor %}{% endif %}
{% if capabilities.get("compliance", False) %}

### Compliance Frameworks Supported

| Framework | Description |
|-----------|-------------|
| NIST 800-53 Rev 5 | Federal information systems baseline |
| FedRAMP Moderate/High | Cloud services authorization |
| NIST 800-171 | CUI protection requirements |
| CMMC Level 2/3 | Cybersecurity maturity certification |
| DoD CSSP (DI 8530.01) | Cybersecurity service provider |
| CISA Secure by Design | Secure development principles |
| IEEE 1012 IV&V | Independent verification and validation |
| DoDI 5000.87 DES | Digital engineering strategy |

**Control Crosswalk:** Implementing one NIST 800-53 control auto-populates FedRAMP, CMMC, and 800-171 status via the crosswalk engine.
{% endif %}
{% if capabilities.get("mbse", False) %}

### MBSE Integration

Model-Based Systems Engineering: SysML XMI import, DOORS NG ReqIF import, digital thread traceability, model-to-code generation, drift detection, and DES compliance assessment.

- Import models: `xmi_parser.py`, `reqif_parser.py`
- Digital thread: `digital_thread.py` (auto-link, coverage, report)
- Code generation: `model_code_generator.py`
- Drift detection: `sync_engine.py`
- DES compliance: `des_assessor.py`, `des_report_generator.py`
{% endif %}

### ATLAS Workflow

Build process follows the ATLAS methodology:
{% if atlas_config.get("model_phase", False) %}
1. **Model** -- Import/validate SysML and DOORS models (M-ATLAS pre-phase)
{% endif %}
{% for phase in atlas_phases %}{{ loop.index }}. **{{ phase | capitalize }}** -- {{ atlas_phase_descriptions.get(phase, phase) }}
{% endfor %}
{% if capabilities.get("testing", False) %}

### Testing Framework

**Testing Architecture (7-step pipeline):**
1. **py_compile** -- Python syntax validation
2. **Ruff** -- Ultra-fast Python linter
3. **pytest** (tests/) -- Unit/integration tests with coverage
4. **behave/Gherkin** (features/) -- BDD scenario tests
5. **Bandit** -- SAST security scan
6. **Playwright MCP** (.claude/commands/e2e/*.md) -- Browser automation E2E tests
7. **Security + Compliance gates** -- CUI markings, STIG, secret detection
{% endif %}

### Database

| Database | Purpose |
|----------|---------|
| `data/{{ db_name }}` | Main operational DB: projects, agents, audit trail{% if capabilities.get("compliance", False) %}, compliance{% endif %}{% if capabilities.get("mbse", False) %}, MBSE{% endif %} |
| `data/memory.db` | Memory system: entries, daily logs, access log |
| `data/activity.db` | Task tracking |

**Audit trail is append-only/immutable** -- no UPDATE/DELETE operations.  Satisfies NIST 800-53 AU controls.
{% if goals_list %}

---

## Existing Goals

| Goal | File | Purpose |
|------|------|---------|
{% for goal in goals_list %}| {{ goal.name }} | `goals/{{ goal.file }}` | {{ goal.purpose }} |
{% endfor %}{% endif %}

---

## Guardrails

- Always check `tools/manifest.md` before writing a new script
- Verify tool output format before chaining into another tool
- Do not assume APIs support batch operations -- check first
- When a workflow fails mid-execution, preserve intermediate outputs before retrying
- Read the full goal before starting a task -- do not skim
- Audit trail is append-only -- NEVER add UPDATE/DELETE operations to audit tables
- Never store secrets in code or config -- use secrets manager or K8s secrets
- All containers must run as non-root with read-only root filesystem
{% if capabilities.get("compliance", False) %}- All generated artifacts MUST include classification markings appropriate to impact level
- SBOM must be regenerated on every build
- When implementing a NIST 800-53 control, always call crosswalk engine to auto-populate FedRAMP/CMMC/800-171 status
{% endif %}{% if capabilities.get("security", False) %}- Security gates block on: CAT1 STIG findings, critical/high vulnerabilities, failed tests, missing markings
{% endif %}- **This application CANNOT generate child applications** -- it is a generated child app of ICDEV.  The agentic fitness assessor, app blueprint engine, and child app generator are intentionally excluded.
{% if parent_callback.get("enabled", False) %}

### A2A Parent Callback

When this application needs capabilities not included locally, it calls back to parent ICDEV:
- **Callback URL:** {{ parent_callback.url }}
- **Auth method:** {{ parent_callback.auth }}
- **Excluded capabilities:** app generation, modernization
{% endif %}
{% if cloud_provider.get("mcp_servers") %}

### Cloud Service Provider Integration

**Target:** {{ cloud_provider.provider | upper }} ({{ cloud_provider.region }})
{% if cloud_provider.get("govcloud", False) %}**Partition:** GovCloud
{% endif %}
**MCP Servers:**
{% for server in cloud_provider.mcp_servers %}- {{ server }}
{% endfor %}{% endif %}
{% if key_decisions %}

---

## Key Architecture Decisions

{% for decision in key_decisions %}- **{{ decision.id }}:** {{ decision.text }}
{% endfor %}{% endif %}

---

## Continuous Improvement

Every failure strengthens the system: identify what broke -> fix the tool -> test it -> update the goal -> next run succeeds automatically.

Be direct.  Be reliable.  Get it done.
"""


# ===========================================================================
# ATLAS phase descriptions -- used by both Jinja2 and fallback renderers
# ===========================================================================

ATLAS_PHASE_DESCRIPTIONS: Dict[str, str] = {
    "architect": "System design, component decomposition, interface contracts",
    "trace": "Requirements traceability matrix, compliance mapping",
    "link": "Wire components together, dependency injection, A2A registration",
    "assemble": "Build, test (TDD RED->GREEN->REFACTOR), integrate",
    "stress_test": "Load testing, security scanning, compliance gate checks",
}


# ===========================================================================
# GOAL METADATA -- purpose descriptions keyed by goal file stem
# ===========================================================================

GOAL_METADATA: Dict[str, Dict[str, str]] = {
    "build_app": {
        "name": "ATLAS Workflow",
        "purpose": "5-step build: Architect -> Trace -> Link -> Assemble -> Stress-test",
    },
    "tdd_workflow": {
        "name": "TDD Workflow",
        "purpose": "RED->GREEN->REFACTOR cycle with Cucumber/Gherkin",
    },
    "compliance_workflow": {
        "name": "Compliance Workflow",
        "purpose": "Generate SSP, POAM, STIG, SBOM, CUI markings",
    },
    "security_scan": {
        "name": "Security Scan",
        "purpose": "SAST, dependency audit, secret detection, container scan",
    },
    "deploy_workflow": {
        "name": "Deploy Workflow",
        "purpose": "IaC generation, pipeline, staging, production deploy",
    },
    "monitoring": {
        "name": "Monitoring",
        "purpose": "Log analysis, metrics, alerts, health checks",
    },
    "self_healing": {
        "name": "Self-Healing",
        "purpose": "Pattern detection, root cause analysis, auto-remediation",
    },
    "agent_management": {
        "name": "Agent Management",
        "purpose": "A2A agent lifecycle, registration, health",
    },
    "integration_testing": {
        "name": "Integration Testing",
        "purpose": "Multi-layer testing: unit, BDD, E2E (Playwright), gates",
    },
    "cicd_integration": {
        "name": "CI/CD Integration",
        "purpose": "GitHub + GitLab dual-platform webhooks, polling, workflow automation",
    },
    "dashboard": {
        "name": "Dashboard",
        "purpose": "Web UI for project status, compliance, security",
    },
    "mbse_integration": {
        "name": "MBSE Integration",
        "purpose": "SysML, DOORS NG, digital thread, model-code sync, DES compliance",
    },
    "sbd_ivv_workflow": {
        "name": "SbD & IV&V Workflow",
        "purpose": "Secure by Design assessment + IV&V certification",
    },
    "maintenance_audit": {
        "name": "Maintenance Audit",
        "purpose": "Dependency scanning, vulnerability checking, SLA enforcement",
    },
    "ato_acceleration": {
        "name": "ATO Acceleration",
        "purpose": "Multi-framework ATO: FedRAMP + CMMC + OSCAL + eMASS + cATO",
    },
}


# ===========================================================================
# HELPER FUNCTIONS
# ===========================================================================

def _compute_content_hash(content: str) -> str:
    """Compute SHA-256 hash of the generated CLAUDE.md content.

    Args:
        content: Rendered CLAUDE.md string.

    Returns:
        Hex-encoded SHA-256 hash.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_blueprint(path: str) -> Dict[str, Any]:
    """Load a blueprint JSON file.

    Args:
        path: Filesystem path to the blueprint JSON.

    Returns:
        Parsed blueprint dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        ValueError: If the file does not contain a JSON object.
    """
    blueprint_path = Path(path)
    if not blueprint_path.exists():
        raise FileNotFoundError(f"Blueprint not found: {path}")

    with open(blueprint_path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"Blueprint must be a JSON object, got {type(data).__name__}"
        )

    # Validate minimal required fields
    required = ("app_name", "capabilities", "agents")
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(
            f"Blueprint missing required fields: {', '.join(missing)}"
        )

    return data


def _derive_agent_tier(agent: Dict[str, Any]) -> str:
    """Derive the tier label for an agent based on its name.

    Core agents (orchestrator, architect, builder, knowledge, monitor) are
    labeled by their functional tier.  Domain agents get 'Domain'.
    Support agents get 'Support'.

    Args:
        agent: Agent spec dict from the blueprint.

    Returns:
        Tier label string.
    """
    name = agent.get("name", "").lower()
    core_map = {
        "orchestrator": "Core",
        "architect": "Core",
    }
    domain_map = {
        "builder": "Domain",
        "compliance": "Domain",
        "security": "Domain",
        "infrastructure": "Domain",
        "mbse": "Domain",
    }
    support_map = {
        "knowledge": "Support",
        "monitor": "Support",
    }
    if name in core_map:
        return core_map[name]
    if name in domain_map:
        return domain_map[name]
    if name in support_map:
        return support_map[name]
    # Default heuristic: core flag from blueprint
    if agent.get("core", False):
        return "Core"
    return "Domain"


def _build_template_context(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """Transform a raw blueprint dict into the template rendering context.

    Enriches the blueprint data with derived values needed by the Jinja2
    template (agent tiers, goal metadata, ATLAS phase descriptions, etc.).

    Args:
        blueprint: Raw blueprint dict from app_blueprint.py.

    Returns:
        Template context dict ready for Jinja2 or fallback rendering.
    """
    app_name = blueprint.get("app_name", "child-app")
    capabilities = blueprint.get("capabilities", {})
    classification = blueprint.get("classification", "CUI")
    impact_level = blueprint.get("impact_level", "IL4")
    agents_raw = blueprint.get("agents", [])
    atlas_config = blueprint.get("atlas_config", {})
    parent_callback = blueprint.get("parent_callback", {})
    cloud_provider = blueprint.get("cloud_provider", {})
    goals_config = blueprint.get("goals_config", [])
    db_config = blueprint.get("db_config", {})

    # Enrich agents with tier labels
    agents = []
    for agent in agents_raw:
        enriched = dict(agent)
        enriched["tier"] = _derive_agent_tier(agent)
        agents.append(enriched)

    # Build MCP server list from agent roster
    mcp_servers = _derive_mcp_servers(agents, capabilities)

    # Determine ATLAS phases (exclude fitness assessment)
    atlas_phases = atlas_config.get("phases", [
        "architect", "trace", "link", "assemble", "stress_test",
    ])
    # Ensure fitness is never present
    atlas_phases = [p for p in atlas_phases if p != "fitness"]

    # Build goals list with metadata
    goals_list = []
    for goal_stem in goals_config:
        meta = GOAL_METADATA.get(goal_stem, {})
        goals_list.append({
            "name": meta.get("name", goal_stem.replace("_", " ").title()),
            "file": f"{goal_stem}.md",
            "purpose": meta.get("purpose", goal_stem.replace("_", " ")),
        })

    # Key architecture decisions for the child app
    key_decisions = _build_key_decisions(blueprint)

    # Database name
    db_name = db_config.get("name", f"{app_name}.db")

    return {
        "app_name": app_name,
        "capabilities": capabilities,
        "classification": classification,
        "impact_level": impact_level,
        "agents": agents,
        "mcp_servers": mcp_servers,
        "atlas_config": atlas_config,
        "atlas_phases": atlas_phases,
        "atlas_phase_descriptions": ATLAS_PHASE_DESCRIPTIONS,
        "parent_callback": parent_callback,
        "cloud_provider": cloud_provider,
        "goals_list": goals_list,
        "goals_config": goals_config,
        "db_config": db_config,
        "db_name": db_name,
        "key_decisions": key_decisions,
    }


def _derive_mcp_servers(
    agents: List[Dict[str, Any]],
    capabilities: Dict[str, bool],
) -> List[Dict[str, str]]:
    """Derive the MCP server documentation table from agents and capabilities.

    Each agent that has an associated MCP server gets an entry.  The tool
    list is a representative subset -- not exhaustive.

    Args:
        agents: Enriched agent list with tier labels.
        capabilities: Capability map from the blueprint.

    Returns:
        List of dicts with 'name' and 'tools' keys for the MCP table.
    """
    agent_mcp_map: Dict[str, Dict[str, str]] = {
        "orchestrator": {
            "name": "core",
            "tools": "project_create, project_list, project_status, task_dispatch, agent_status",
        },
        "builder": {
            "name": "builder",
            "tools": "scaffold, generate_code, write_tests, run_tests, lint, format",
        },
        "compliance": {
            "name": "compliance",
            "tools": "ssp_generate, poam_generate, stig_check, sbom_generate, cui_mark, control_map, nist_lookup",
        },
        "security": {
            "name": "security",
            "tools": "sast_scan, dep_audit, secret_detect, container_scan",
        },
        "knowledge": {
            "name": "knowledge",
            "tools": "search_knowledge, add_pattern, get_recommendations, self_heal",
        },
        "monitor": {
            "name": "monitor",
            "tools": "log_analyze, health_check, metrics_query, alert_manage",
        },
        "architect": {
            "name": "architect",
            "tools": "design_system, decompose, interface_contract",
        },
    }

    # Only include MBSE server if the mbse capability is on
    if capabilities.get("mbse", False):
        agent_mcp_map["mbse"] = {
            "name": "mbse",
            "tools": "import_xmi, import_reqif, trace_forward, trace_backward, detect_drift, sync_model",
        }

    servers: List[Dict[str, str]] = []
    agent_names = {a.get("name", "").lower() for a in agents}
    for agent_name in sorted(agent_names):
        mapping = agent_mcp_map.get(agent_name)
        if mapping:
            servers.append(mapping)

    return servers


def _build_key_decisions(blueprint: Dict[str, Any]) -> List[Dict[str, str]]:
    """Build the key architecture decisions section for the child app.

    Decisions are filtered based on which capabilities are enabled.  Child
    apps never include decisions about fitness assessment, modernization,
    or grandchild generation.

    Args:
        blueprint: Blueprint dict.

    Returns:
        List of dicts with 'id' and 'text' keys.
    """
    capabilities = blueprint.get("capabilities", {})
    decisions: List[Dict[str, str]] = []

    # Always-included decisions
    decisions.append({
        "id": "D1",
        "text": "SQLite for internal operational data (zero-config portability)",
    })
    decisions.append({
        "id": "D2",
        "text": "Stdio for MCP (Claude Code); HTTPS+mTLS for A2A (K8s inter-agent)",
    })
    decisions.append({
        "id": "D5",
        "text": "CUI markings applied at generation time (inline, not post-processing)",
    })
    decisions.append({
        "id": "D6",
        "text": "Audit trail is append-only/immutable (no UPDATE/DELETE -- NIST AU compliance)",
    })

    if capabilities.get("dashboard", False):
        decisions.append({
            "id": "D3",
            "text": "Flask over FastAPI (simpler, fewer deps, auditable SSR, smaller STIG surface)",
        })

    if capabilities.get("knowledge", False):
        decisions.append({
            "id": "D4",
            "text": "Statistical methods for pattern detection; Bedrock LLM for root cause analysis",
        })

    if capabilities.get("mbse", False):
        decisions.append({
            "id": "D7",
            "text": "Python stdlib xml.etree.ElementTree for XMI/ReqIF parsing (zero deps, air-gap safe)",
        })
        decisions.append({
            "id": "D8",
            "text": "Normalized DB tables for model elements (enables SQL joins across digital thread)",
        })
        decisions.append({
            "id": "D9",
            "text": "M-ATLAS adds Model pre-phase to ATLAS (backward compatible -- skips if no model)",
        })
        decisions.append({
            "id": "D12",
            "text": "N:M digital thread links (one block -> many code modules; one control -> many requirements)",
        })

    # Grandchild prevention is always documented
    decisions.append({
        "id": "D26",
        "text": "This is a generated child app -- grandchild app generation is disabled by design",
    })

    return decisions


# ===========================================================================
# JINJA2 RENDERER
# ===========================================================================

def _generate_with_jinja2(blueprint: Dict[str, Any]) -> str:
    """Render CLAUDE.md using the Jinja2 template engine.

    Args:
        blueprint: Blueprint dict from app_blueprint.py.

    Returns:
        Rendered CLAUDE.md content string.

    Raises:
        RuntimeError: If Jinja2 is not available (caller should use fallback).
    """
    if not _HAS_JINJA2:
        raise RuntimeError("Jinja2 is not installed")

    context = _build_template_context(blueprint)

    env = Environment(  # nosec B701 — generates Markdown, not HTML
        loader=BaseLoader(),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.from_string(CLAUDE_MD_TEMPLATE)
    rendered = template.render(**context)

    # Clean up excessive blank lines (more than 2 consecutive)
    lines = rendered.split("\n")
    cleaned: List[str] = []
    blank_count = 0
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= 2:
                cleaned.append(line)
        else:
            blank_count = 0
            cleaned.append(line)

    return "\n".join(cleaned)


# ===========================================================================
# FALLBACK RENDERER (no Jinja2)
# ===========================================================================

def _generate_fallback(blueprint: Dict[str, Any]) -> str:
    """Render CLAUDE.md using basic string operations when Jinja2 is absent.

    Produces equivalent output to the Jinja2 renderer but uses simple
    conditionals and string formatting instead of a template engine.

    Args:
        blueprint: Blueprint dict from app_blueprint.py.

    Returns:
        Rendered CLAUDE.md content string.
    """
    ctx = _build_template_context(blueprint)
    sections: List[str] = []

    # -- Header --
    sections.append("# CLAUDE.md\n")
    sections.append(
        f"This file provides guidance to Claude Code (claude.ai/code) "
        f"when working with {ctx['app_name']}.\n"
    )
    sections.append("---\n")

    # -- Quick Reference --
    sections.append("## Quick Reference\n")
    sections.append(_build_commands_section(ctx))

    # -- GOTCHA Framework --
    sections.append("---\n")
    sections.append(_build_gotcha_section())

    # -- How to Operate --
    sections.append("---\n")
    sections.append(_build_operate_section())

    # -- App System --
    sections.append("---\n")
    sections.append(_build_system_section(ctx))

    # -- Goals --
    if ctx["goals_list"]:
        sections.append("---\n")
        sections.append(_build_goals_section(ctx))

    # -- Guardrails --
    sections.append("---\n")
    sections.append(_build_guardrails_section(ctx))

    # -- Key Decisions --
    if ctx["key_decisions"]:
        sections.append("---\n")
        sections.append(_build_decisions_section(ctx))

    # -- Continuous Improvement --
    sections.append("---\n")
    sections.append("## Continuous Improvement\n")
    sections.append(
        "Every failure strengthens the system: identify what broke -> "
        "fix the tool -> test it -> update the goal -> next run succeeds "
        "automatically.\n"
    )
    sections.append("Be direct.  Be reliable.  Get it done.\n")

    content = "\n".join(sections)
    # Normalize excessive blank lines
    while "\n\n\n\n" in content:
        content = content.replace("\n\n\n\n", "\n\n\n")
    return content


def _build_commands_section(ctx: Dict[str, Any]) -> str:
    """Build the commands section for fallback rendering."""
    parts: List[str] = []

    # Memory commands -- always present
    parts.append("### Commands\n")
    parts.append("```bash")
    parts.append("# Memory system")
    parts.append('python tools/memory/memory_read.py --format markdown          # Load all memory')
    parts.append('python tools/memory/memory_write.py --content "text" --type event  # Write to daily log + DB')
    parts.append('python tools/memory/memory_write.py --content "text" --type fact --importance 7  # Store a fact')
    parts.append('python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences  # Update MEMORY.md')
    parts.append('python tools/memory/memory_db.py --action search --query "keyword"   # Keyword search')
    parts.append('python tools/memory/semantic_search.py --query "concept"             # Semantic search (requires OpenAI key)')
    parts.append('python tools/memory/hybrid_search.py --query "query"                 # Best: combined keyword + semantic')
    parts.append('python tools/memory/embed_memory.py --all                            # Generate embeddings for all entries')
    parts.append("```\n")

    caps = ctx["capabilities"]
    app = ctx["app_name"]
    classification = ctx["classification"]
    impact_level = ctx["impact_level"]

    if caps.get("testing", False):
        parts.append("### Testing Commands\n")
        parts.append("```bash")
        parts.append("python tools/testing/health_check.py                 # Full system health check")
        parts.append("python tools/testing/health_check.py --json           # JSON output")
        parts.append("python tools/testing/test_orchestrator.py --project-dir /path/to/project")
        parts.append("python tools/testing/e2e_runner.py --discover         # List available E2E test specs")
        parts.append("python tools/testing/e2e_runner.py --run-all           # Execute all E2E tests")
        parts.append("```\n")

    if caps.get("compliance", False):
        parts.append("### Compliance Commands\n")
        parts.append("```bash")
        parts.append(f'python tools/compliance/ssp_generator.py --project-id "{app}"')
        parts.append(f'python tools/compliance/poam_generator.py --project-id "{app}"')
        parts.append(f'python tools/compliance/stig_checker.py --project-id "{app}"')
        parts.append('python tools/compliance/sbom_generator.py --project-dir "/path/to/project"')
        parts.append(f'python tools/compliance/cui_marker.py --file "/path/to/file" --marking "{classification} // SP-CTI"')
        parts.append('python tools/compliance/nist_lookup.py --control "AC-2"')
        parts.append(f'python tools/compliance/control_mapper.py --activity "code.commit" --project-id "{app}"')
        parts.append("python tools/compliance/crosswalk_engine.py --control AC-2")
        parts.append(f'python tools/compliance/crosswalk_engine.py --project-id "{app}" --coverage')
        parts.append(f'python tools/compliance/fedramp_assessor.py --project-id "{app}" --baseline moderate')
        parts.append(f'python tools/compliance/cmmc_assessor.py --project-id "{app}" --level 2')
        parts.append(f'python tools/compliance/oscal_generator.py --project-id "{app}" --artifact ssp')
        parts.append(f"python tools/compliance/classification_manager.py --impact-level {impact_level}")
        parts.append("```\n")

    if caps.get("security", False):
        parts.append("### Security Commands\n")
        parts.append("```bash")
        parts.append('python tools/security/sast_runner.py --project-dir "/path"')
        parts.append('python tools/security/dependency_auditor.py --project-dir "/path"')
        parts.append('python tools/security/secret_detector.py --project-dir "/path"')
        parts.append(f'python tools/security/container_scanner.py --image "{app}:latest"')
        parts.append("```\n")

    if caps.get("mbse", False):
        parts.append("### MBSE Commands\n")
        parts.append("```bash")
        parts.append(f'python tools/mbse/xmi_parser.py --project-id "{app}" --file /path/model.xmi --json')
        parts.append(f'python tools/mbse/reqif_parser.py --project-id "{app}" --file /path/reqs.reqif --json')
        parts.append(f'python tools/mbse/digital_thread.py --project-id "{app}" auto-link --json')
        parts.append(f'python tools/mbse/digital_thread.py --project-id "{app}" coverage --json')
        parts.append(f'python tools/mbse/model_code_generator.py --project-id "{app}" --language python --output ./src')
        parts.append(f'python tools/mbse/sync_engine.py --project-id "{app}" detect-drift --json')
        parts.append(f'python tools/mbse/des_assessor.py --project-id "{app}" --project-dir /path --json')
        parts.append("```\n")

    if caps.get("infra", False):
        parts.append("### Infrastructure Commands\n")
        parts.append("```bash")
        parts.append(f'python tools/infra/terraform_generator.py --project-id "{app}"')
        parts.append(f'python tools/infra/ansible_generator.py --project-id "{app}"')
        parts.append(f'python tools/infra/k8s_generator.py --project-id "{app}"')
        parts.append(f'python tools/infra/pipeline_generator.py --project-id "{app}"')
        parts.append('python tools/infra/rollback.py --deployment-id "deploy-123"')
        parts.append("```\n")

    if caps.get("cicd", False):
        parts.append("### CI/CD Commands\n")
        parts.append("```bash")
        parts.append("python tools/ci/triggers/webhook_server.py           # Start webhook server")
        parts.append("python tools/ci/triggers/poll_trigger.py             # Start issue polling")
        parts.append("python tools/ci/workflows/icdev_sdlc.py 123          # Run full SDLC pipeline")
        parts.append("```\n")

    if caps.get("dashboard", False):
        parts.append("### Dashboard\n")
        parts.append("```bash")
        parts.append("python tools/dashboard/app.py                        # Start web dashboard on port 5000")
        parts.append("```\n")

    return "\n".join(parts)


def _build_gotcha_section() -> str:
    """Build the GOTCHA framework section for fallback rendering."""
    return """## Architecture: GOTCHA Framework

This is a 6-layer agentic system.  The AI (you) is the orchestration layer -- you read goals, call tools, apply args, reference context, and use hard prompts.  You never execute work directly; you delegate to deterministic Python scripts.

**Why:** LLMs are probabilistic.  Business logic must be deterministic.  90% accuracy/step = ~59% over 5 steps.  Separation of concerns fixes this.

### The 6 Layers

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions -- what to achieve, which tools to use, expected outputs, edge cases |
| **Orchestration** | *(you)* | Read goal -> decide tool order -> apply args -> reference context -> handle errors |
| **Tools** | `tools/` | Python scripts, one job each.  Deterministic.  Don't think, just execute. |
| **Args** | `args/` | YAML/JSON behavior settings (themes, modes, schedules).  Change behavior without editing goals/tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, ICP descriptions, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates (outline->post, rewrite-in-voice, summarize) |

### Key Files

- `goals/manifest.md` -- Index of all goal workflows.  Check before starting any task.
- `tools/manifest.md` -- Master list of all tools.  Check before writing a new script.
- `memory/MEMORY.md` -- Curated long-term facts/preferences, read at session start.
- `memory/logs/YYYY-MM-DD.md` -- Daily session logs.
- `.env` -- API keys and environment variables.
- `.tmp/` -- Disposable scratch work.  Never store important data here.

### Memory System Architecture

Dual storage: markdown files (human-readable) + SQLite databases (searchable).

**Databases:**
- `data/memory.db` -- `memory_entries` (with embeddings), `daily_logs`, `memory_access_log`
- `data/activity.db` -- `tasks` table for tracking

**Memory types:** fact, preference, event, insight, task, relationship

**Search ranking:** Hybrid search uses 0.7 * BM25 (keyword) + 0.3 * semantic (vector).  Configurable via `--bm25-weight` and `--semantic-weight` flags.

**Embeddings:** OpenAI text-embedding-3-small (1536 dims), stored as BLOBs in SQLite.
"""


def _build_operate_section() -> str:
    """Build the How to Operate section for fallback rendering."""
    return """## How to Operate

1. **Check goals first** -- Read `goals/manifest.md` before starting a task.  If a goal exists, follow it.
2. **Check tools first** -- Read `tools/manifest.md` before writing new code.  If you create a new tool, add it to the manifest.
3. **When tools fail** -- Read the error, fix the tool, update the goal with what you learned (rate limits, batching, timing).
4. **Goals are living docs** -- Update when better approaches emerge.  Never modify/create goals without explicit permission.
5. **When stuck** -- Explain what is missing and what you need.  Do not guess or invent capabilities.

### Session Start Protocol

1. Read `memory/MEMORY.md` for long-term context
2. Read today's daily log (`memory/logs/YYYY-MM-DD.md`)
3. Read yesterday's log for continuity
4. Or run: `python tools/memory/memory_read.py --format markdown`
"""


def _build_system_section(ctx: Dict[str, Any]) -> str:
    """Build the app system section for fallback rendering."""
    parts: List[str] = []
    app_name = ctx["app_name"]
    caps = ctx["capabilities"]

    parts.append(f"## {app_name} System\n")

    # Classification
    classification = ctx["classification"]
    impact_level = ctx["impact_level"]
    if classification:
        parts.append("### Classification\n")
        marking = f"{classification} // SP-CTI" if classification == "CUI" else classification
        parts.append(f"**Impact Level:** {impact_level}")
        parts.append(f"**Classification:** {marking}\n")
        parts.append("All generated artifacts MUST include classification markings appropriate to impact level.\n")

    # Agent table
    agents = ctx["agents"]
    parts.append(f"### Multi-Agent Architecture ({len(agents)} Agents)\n")
    parts.append(_build_agent_table(agents))
    parts.append("")
    parts.append("Agents communicate via **A2A protocol** (JSON-RPC 2.0 over mutual TLS within K8s).  Each publishes an Agent Card at `/.well-known/agent.json`.\n")

    # MCP servers
    mcp_servers = ctx["mcp_servers"]
    if mcp_servers:
        parts.append(f"### MCP Servers ({len(mcp_servers)} stdio servers for Claude Code)\n")
        parts.append("| Server | Tools |")
        parts.append("|--------|-------|")
        for server in mcp_servers:
            parts.append(f"| {server['name']} | {server['tools']} |")
        parts.append("")

    # Compliance frameworks
    if caps.get("compliance", False):
        parts.append("### Compliance Frameworks Supported\n")
        parts.append("| Framework | Description |")
        parts.append("|-----------|-------------|")
        parts.append("| NIST 800-53 Rev 5 | Federal information systems baseline |")
        parts.append("| FedRAMP Moderate/High | Cloud services authorization |")
        parts.append("| NIST 800-171 | CUI protection requirements |")
        parts.append("| CMMC Level 2/3 | Cybersecurity maturity certification |")
        parts.append("| DoD CSSP (DI 8530.01) | Cybersecurity service provider |")
        parts.append("| CISA Secure by Design | Secure development principles |")
        parts.append("| IEEE 1012 IV&V | Independent verification and validation |")
        parts.append("| DoDI 5000.87 DES | Digital engineering strategy |")
        parts.append("")
        parts.append("**Control Crosswalk:** Implementing one NIST 800-53 control auto-populates FedRAMP, CMMC, and 800-171 status via the crosswalk engine.\n")

    # MBSE
    if caps.get("mbse", False):
        parts.append("### MBSE Integration\n")
        parts.append(
            "Model-Based Systems Engineering: SysML XMI import, DOORS NG ReqIF import, "
            "digital thread traceability, model-to-code generation, drift detection, "
            "and DES compliance assessment.\n"
        )
        parts.append("- Import models: `xmi_parser.py`, `reqif_parser.py`")
        parts.append("- Digital thread: `digital_thread.py` (auto-link, coverage, report)")
        parts.append("- Code generation: `model_code_generator.py`")
        parts.append("- Drift detection: `sync_engine.py`")
        parts.append("- DES compliance: `des_assessor.py`, `des_report_generator.py`\n")

    # ATLAS workflow
    atlas_phases = ctx["atlas_phases"]
    parts.append("### ATLAS Workflow\n")
    parts.append("Build process follows the ATLAS methodology:\n")
    idx = 1
    if ctx["atlas_config"].get("model_phase", False):
        parts.append(f"{idx}. **Model** -- Import/validate SysML and DOORS models (M-ATLAS pre-phase)")
        idx += 1
    for phase in atlas_phases:
        desc = ATLAS_PHASE_DESCRIPTIONS.get(phase, phase)
        parts.append(f"{idx}. **{phase.capitalize()}** -- {desc}")
        idx += 1
    parts.append("")

    # Testing
    if caps.get("testing", False):
        parts.append("### Testing Framework\n")
        parts.append("**Testing Architecture (7-step pipeline):**")
        parts.append("1. **py_compile** -- Python syntax validation")
        parts.append("2. **Ruff** -- Ultra-fast Python linter")
        parts.append("3. **pytest** (tests/) -- Unit/integration tests with coverage")
        parts.append("4. **behave/Gherkin** (features/) -- BDD scenario tests")
        parts.append("5. **Bandit** -- SAST security scan")
        parts.append("6. **Playwright MCP** (.claude/commands/e2e/*.md) -- Browser automation E2E tests")
        parts.append("7. **Security + Compliance gates** -- CUI markings, STIG, secret detection\n")

    # Database
    db_name = ctx["db_name"]
    parts.append("### Database\n")
    parts.append("| Database | Purpose |")
    parts.append("|----------|---------|")
    purpose_parts = ["projects, agents, audit trail"]
    if caps.get("compliance", False):
        purpose_parts.append("compliance")
    if caps.get("mbse", False):
        purpose_parts.append("MBSE")
    parts.append(f"| `data/{db_name}` | Main operational DB: {', '.join(purpose_parts)} |")
    parts.append("| `data/memory.db` | Memory system: entries, daily logs, access log |")
    parts.append("| `data/activity.db` | Task tracking |")
    parts.append("")
    parts.append("**Audit trail is append-only/immutable** -- no UPDATE/DELETE operations.  Satisfies NIST 800-53 AU controls.\n")

    return "\n".join(parts)


def _build_agent_table(agents: List[Dict[str, Any]]) -> str:
    """Build a markdown table of agents.

    Args:
        agents: Enriched agent list with tier labels.

    Returns:
        Markdown table string.
    """
    lines = [
        "| Tier | Agent | Port | Role |",
        "|------|-------|------|------|",
    ]
    for agent in agents:
        name = agent.get("name", "unknown").capitalize()
        tier = agent.get("tier", "Domain")
        port = agent.get("port", "N/A")
        role = agent.get("role", "")
        lines.append(f"| {tier} | {name} | {port} | {role} |")
    return "\n".join(lines)


def _build_goals_section(ctx: Dict[str, Any]) -> str:
    """Build the goals section for fallback rendering."""
    parts: List[str] = []
    parts.append("## Existing Goals\n")
    parts.append("| Goal | File | Purpose |")
    parts.append("|------|------|---------|")
    for goal in ctx["goals_list"]:
        parts.append(f"| {goal['name']} | `goals/{goal['file']}` | {goal['purpose']} |")
    parts.append("")
    return "\n".join(parts)


def _build_guardrails_section(ctx: Dict[str, Any]) -> str:
    """Build the guardrails section for fallback rendering."""
    caps = ctx["capabilities"]
    parts: List[str] = []
    parts.append("## Guardrails\n")
    parts.append("- Always check `tools/manifest.md` before writing a new script")
    parts.append("- Verify tool output format before chaining into another tool")
    parts.append("- Do not assume APIs support batch operations -- check first")
    parts.append("- When a workflow fails mid-execution, preserve intermediate outputs before retrying")
    parts.append("- Read the full goal before starting a task -- do not skim")
    parts.append("- Audit trail is append-only -- NEVER add UPDATE/DELETE operations to audit tables")
    parts.append("- Never store secrets in code or config -- use secrets manager or K8s secrets")
    parts.append("- All containers must run as non-root with read-only root filesystem")

    if caps.get("compliance", False):
        parts.append("- All generated artifacts MUST include classification markings appropriate to impact level")
        parts.append("- SBOM must be regenerated on every build")
        parts.append("- When implementing a NIST 800-53 control, always call crosswalk engine to auto-populate FedRAMP/CMMC/800-171 status")

    if caps.get("security", False):
        parts.append("- Security gates block on: CAT1 STIG findings, critical/high vulnerabilities, failed tests, missing markings")

    parts.append(
        "- **This application CANNOT generate child applications** -- it is a generated "
        "child app of ICDEV.  The agentic fitness assessor, app blueprint engine, and "
        "child app generator are intentionally excluded."
    )

    # Parent callback
    parent = ctx["parent_callback"]
    if parent.get("enabled", False):
        parts.append("")
        parts.append("### A2A Parent Callback\n")
        parts.append("When this application needs capabilities not included locally, it calls back to parent ICDEV:")
        parts.append(f"- **Callback URL:** {parent.get('url', 'N/A')}")
        parts.append(f"- **Auth method:** {parent.get('auth', 'N/A')}")
        parts.append("- **Excluded capabilities:** app generation, modernization")

    # Cloud provider
    cloud = ctx["cloud_provider"]
    if cloud.get("mcp_servers"):
        parts.append("")
        parts.append("### Cloud Service Provider Integration\n")
        provider = cloud.get("provider", "aws").upper()
        region = cloud.get("region", "N/A")
        parts.append(f"**Target:** {provider} ({region})")
        if cloud.get("govcloud", False):
            parts.append("**Partition:** GovCloud")
        parts.append("\n**MCP Servers:**")
        for server in cloud["mcp_servers"]:
            parts.append(f"- {server}")

    parts.append("")
    return "\n".join(parts)


def _build_decisions_section(ctx: Dict[str, Any]) -> str:
    """Build the key architecture decisions section for fallback rendering."""
    parts: List[str] = []
    parts.append("## Key Architecture Decisions\n")
    for decision in ctx["key_decisions"]:
        parts.append(f"- **{decision['id']}:** {decision['text']}")
    parts.append("")
    return "\n".join(parts)


# ===========================================================================
# PUBLIC API
# ===========================================================================

def generate_claude_md(blueprint: Dict[str, Any]) -> str:
    """Generate CLAUDE.md content from a blueprint.

    Uses Jinja2 if available, falls back to simple string formatting.
    Both renderers produce functionally equivalent output.

    Args:
        blueprint: Blueprint dict from app_blueprint.py.

    Returns:
        Rendered CLAUDE.md content string.
    """
    if _HAS_JINJA2:
        logger.info("Rendering CLAUDE.md with Jinja2 template engine")
        return _generate_with_jinja2(blueprint)
    else:
        logger.info("Jinja2 not available -- using fallback string renderer")
        return _generate_fallback(blueprint)


# ===========================================================================
# CLI ENTRY POINT
# ===========================================================================

def main():
    """CLI entry point for the CLAUDE.md generator."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Dynamic CLAUDE.md Generator -- creates adaptive documentation "
            "for child apps from a deployment blueprint."
        ),
    )
    parser.add_argument(
        "--blueprint",
        required=True,
        help="Path to blueprint JSON file (output of app_blueprint.py)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write CLAUDE.md to this file path (default: stdout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Wrap output in JSON envelope with metadata",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load blueprint
    try:
        blueprint = _load_blueprint(args.blueprint)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to load blueprint: %s", e)
        sys.exit(1)

    # Generate content
    try:
        content = generate_claude_md(blueprint)
    except Exception as e:
        logger.error("Failed to generate CLAUDE.md: %s", e)
        sys.exit(1)

    content_hash = _compute_content_hash(content)
    line_count = content.count("\n") + 1
    renderer = "jinja2" if _HAS_JINJA2 else "fallback"

    logger.info(
        "Generated CLAUDE.md: %d lines, hash=%s, renderer=%s",
        line_count,
        content_hash[:16] + "...",
        renderer,
    )

    # Audit trail
    try:
        audit_log_event(
            event_type="claude_md.generated",
            actor="builder/claude_md_generator",
            action=f"Generated CLAUDE.md for '{blueprint.get('app_name', 'unknown')}'",
            project_id=blueprint.get("blueprint_id", ""),
            details=json.dumps({
                "app_name": blueprint.get("app_name"),
                "blueprint_id": blueprint.get("blueprint_id"),
                "blueprint_hash": blueprint.get("blueprint_hash", "")[:32],
                "content_hash": content_hash[:32],
                "line_count": line_count,
                "renderer": renderer,
                "capabilities_enabled": sum(
                    1 for v in blueprint.get("capabilities", {}).values() if v
                ),
                "agent_count": len(blueprint.get("agents", [])),
            }),
        )
    except Exception as e:
        logger.debug("Audit log failed: %s", e)

    # Output
    if args.json_output:
        envelope = {
            "status": "success",
            "generator": "icdev/claude_md_generator",
            "blueprint_id": blueprint.get("blueprint_id", ""),
            "app_name": blueprint.get("app_name", ""),
            "renderer": renderer,
            "content_hash": content_hash,
            "line_count": line_count,
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "content": content,
        }
        output_json = json.dumps(envelope, indent=2, ensure_ascii=False)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_json, encoding="utf-8")
            logger.info("JSON envelope written to %s", args.output)
        else:
            print(output_json)

    else:
        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(content, encoding="utf-8")
            logger.info("CLAUDE.md written to %s", args.output)
        else:
            print(content)


if __name__ == "__main__":
    main()
