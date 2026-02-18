# CUI // SP-CTI

# Architecture Patterns for Agentic Applications

## Overview

This document describes the architecture patterns used when ICDEV generates agentic child applications. These patterns ensure consistency, security, and compliance across all generated applications. Every pattern is enforced by the blueprint engine and verified during post-generation checks.

Child applications are self-contained agentic systems with their own agents, memory, goals, and tools. They operate independently but can communicate back to the parent ICDEV instance via A2A protocol when a callback URL is configured.

---

## Pattern 1: GOTCHA Framework

Every child application uses the 6-layer GOTCHA framework. This is non-negotiable — it is the structural foundation that separates probabilistic AI orchestration from deterministic tool execution.

| Layer | Directory | Role in Child App |
|-------|-----------|-------------------|
| **Goals** | `goals/` | Process definitions — what to achieve, tool sequences, edge cases |
| **Orchestration** | *(the AI)* | Read goal, select tools, apply args, reference context, handle errors |
| **Tools** | `tools/` | Python scripts copied from ICDEV with path/port/db adaptations |
| **Context** | `context/` | Static reference material: compliance catalogs, language profiles, tone |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates for common tasks |
| **Args** | `args/` | YAML/JSON behavior settings that change behavior without editing code |

**Rationale:** LLMs are probabilistic. Business logic must be deterministic. 90% accuracy per step compounds to ~59% over 5 steps. GOTCHA enforces separation of concerns so the AI orchestrates while tools execute reliably.

**Enforcement:** The scaffolder generates all 6 directories. Post-generation verification checks that each contains at least one file. CLAUDE.md documents the framework for the child's AI orchestrator.

---

## Pattern 2: Agent Tiers

Agents are organized into 3 tiers based on their role and criticality:

### Tier 1: Core (Always Present)
| Agent | Port Offset | Role |
|-------|-------------|------|
| Orchestrator | +0 | Task routing, workflow management, result aggregation |
| Architect | +1 | ATLAS/M-ATLAS A/T phases, system design, technology selection |
| Builder | +2 | TDD code generation (RED-GREEN-REFACTOR), scaffolding, lint, format |

### Tier 2: Domain (Conditional — Based on Fitness Score)
| Agent | Port Offset | Condition |
|-------|-------------|-----------|
| Compliance | +3 | `compliance_sensitivity >= 5` or ATO required |
| Security | +4 | Always if compliance present; otherwise `integration_density >= 5` |
| MBSE | +8 | `mbse_enabled = true` in user decisions |
| Modernization | +9 | `modernization_enabled = true` (rare for new apps) |

### Tier 3: Support (Always Present)
| Agent | Port Offset | Role |
|-------|-------------|------|
| Knowledge | +6 | Self-healing patterns, failure analysis, recommendations |
| Monitor | +7 | Log analysis, health checks, metrics, alerts |

**Port calculation:** `child_port = icdev_base_port + port_offset + user_port_offset`

Example: ICDEV Orchestrator is 8443. User offset is 1000. Child Orchestrator is 8443 + 0 + 1000 = 9443.

**Agent cards:** Each agent publishes an Agent Card at `/.well-known/agent.json` describing its capabilities, skills, and A2A endpoint. Cards are generated during scaffolding and stored in `tools/agent/cards/<agent-name>.json`.

---

## Pattern 3: A2A Protocol

Agent-to-agent communication uses JSON-RPC 2.0 over mutual TLS within a Kubernetes cluster.

**Message format:**
```json
{
  "jsonrpc": "2.0",
  "method": "skill.execute",
  "params": {
    "skill_id": "code-generation",
    "input": { "test_file": "/path/to/test.py" },
    "context": { "project_id": "proj-123", "impact_level": "IL4" }
  },
  "id": "task-abc-123"
}
```

**Security requirements:**
- Mutual TLS with X.509 certificates (issued by cluster CA)
- All capabilities dropped in container security context
- Network policies restrict agent-to-agent traffic within namespace
- Audit logging on every A2A message (NIST AU-2)

**Parent callback:** If `parent_callback_url` is configured, the child Orchestrator can send status updates and request assistance from the parent ICDEV instance using the same A2A protocol.

---

## Pattern 4: Blueprint-Driven Generation

A single blueprint JSON drives all generators. No hardcoded decisions exist in the generation code.

**Blueprint structure:**
```
blueprint.json
  ├── capability_map        # What to include
  ├── agent_roster          # Which agents and their configs
  ├── file_manifest         # What to copy and how to adapt
  ├── csp_mcp_servers       # Cloud provider MCP servers
  ├── db_schema             # Core + capability tables
  ├── memory_config         # Memory system settings
  ├── cicd_config           # Pipeline template and stages
  ├── atlas_config          # ATLAS or M-ATLAS workflow
  ├── gotcha_dirs           # Directory structure specification
  ├── agent_ports           # Port assignments
  └── classification        # CUI markings and encryption
```

**Rationale:** Centralized configuration prevents drift between generators. Every generator reads the blueprint, never makes assumptions. Changes to child app structure require only blueprint changes, not code changes.

**Verification:** Post-generation, the blueprint hash is stored in `data/generation_summary.json`. Governance review validates the hash matches the generated output.

---

## Pattern 5: Copy-and-Adapt

Tools are copied from ICDEV with text adaptations applied during scaffolding. ICDEV's own tools are the single source of truth — there is no separate template library.

**Adaptations applied during copy:**
- Port numbers remapped (ICDEV base -> child base + offset)
- Database name/path updated (`data/icdev.db` -> `data/<name>.db`)
- Import paths adjusted for child directory structure
- CUI markings verified/applied
- ICDEV-specific references stripped (generation tools, parent paths)

**What is NOT copied:**
- `tools/builder/agentic_fitness.py` (generation tool — grandchild prevention)
- `tools/builder/app_blueprint.py` (generation tool — grandchild prevention)
- Parent ICDEV configuration files
- `.env` files (secrets are never copied)

**Rationale:** Templates drift from reality. By copying actual working tools, child apps inherit battle-tested code. Adaptations are minimal and deterministic (string replacements, not logic changes).

---

## Pattern 6: Grandchild Prevention

Child applications CANNOT generate their own child applications. This is enforced at three independent levels:

**Level 1: Configuration Flag**
The child app's `args/project_defaults.yaml` contains `agentic_generation: false`. The scaffolder checks this flag and refuses to run with `--agentic` if it is false.

**Level 2: Scaffolder Stripping**
Generation tools (`agentic_fitness.py`, `app_blueprint.py`) are excluded from the file manifest. The `--agentic` flag is not available in the child's scaffolder.

**Level 3: CLAUDE.md Documentation**
The child's CLAUDE.md explicitly states: "This application CANNOT generate child applications. Agentic generation is only available in the parent ICDEV system."

**Rationale:** Uncontrolled proliferation of agentic systems creates security, compliance, and operational risks. Each generation layer adds configuration drift risk. Three independent enforcement levels ensure no single bypass can enable generation.

---

## Pattern 7: Minimal DB + Migration

Child applications start with a minimal core database schema and expand via `migrate_add_capability()`.

**Core tables (always present):**
- `projects` — Project metadata
- `agents` — Agent registry
- `tasks` — Task tracking
- `audit_trail` — Append-only audit log (immutable)

**Capability tables (added per blueprint):**
- Compliance: `nist_controls`, `compliance_assessments`, `poam_entries`
- Security: `security_findings`, `vulnerability_scans`
- MBSE: `sysml_elements`, `sysml_relationships`, `digital_thread_links`
- Memory: `memory_entries`, `daily_logs`
- Knowledge: `patterns`, `self_heal_history`

**Rationale:** A child app that doesn't need MBSE shouldn't have 10 empty MBSE tables. Minimal core + capability migration keeps the database clean and schema understandable.

---

## Pattern 8: Dynamic CLAUDE.md

The child application's CLAUDE.md is generated from a Jinja2 template that only includes documentation for capabilities present in the blueprint.

**Template variables:**
- `{{ agents }}` — List of agents with ports and roles
- `{{ capabilities }}` — Enabled capabilities (compliance, mbse, etc.)
- `{{ commands }}` — Available tool commands
- `{{ databases }}` — Database tables with descriptions
- `{{ classification }}` — Impact level and CUI marking rules
- `{{ grandchild_prevention }}` — Always included

**Rationale:** A child app's CLAUDE.md should be accurate and focused. Documenting tools that don't exist confuses the AI orchestrator and wastes context window tokens.

---

## Pattern 9: CSP MCP Integration

Cloud Service Provider MCP servers are selected based on the child app's capabilities and cloud provider choice.

**Selection logic:**
```
IF provider == "aws-govcloud":
    INCLUDE aws-bedrock-mcp (LLM inference)
    INCLUDE aws-s3-mcp (object storage)
    IF compliance_enabled:
        INCLUDE aws-securityhub-mcp
    IF monitoring_enabled:
        INCLUDE aws-cloudwatch-mcp

IF provider == "azure":
    INCLUDE azure-openai-mcp
    INCLUDE azure-blob-mcp
    ...
```

**Registry:** `context/agentic/csp_mcp_registry.yaml` contains the full mapping of capabilities to MCP servers per provider.

**Rationale:** MCP servers are the child app's connection to cloud services. The blueprint selects only what's needed, avoiding unnecessary dependencies and reducing the attack surface.

---

## Pattern 10: Port Offset

Child agents use ICDEV base ports + a configurable offset (default: 1000).

**Default port assignments (with offset 1000):**

| Agent | ICDEV Port | Child Port (offset 1000) |
|-------|------------|--------------------------|
| Orchestrator | 8443 | 9443 |
| Architect | 8444 | 9444 |
| Builder | 8445 | 9445 |
| Compliance | 8446 | 9446 |
| Security | 8447 | 9447 |
| Infrastructure | 8448 | 9448 |
| Knowledge | 8449 | 9449 |
| Monitor | 8450 | 9450 |
| MBSE | 8451 | 9451 |
| Modernization | 8452 | 9452 |

**Multiple children:** When generating multiple child apps from the same ICDEV instance, each should use a different offset (1000, 2000, 3000, etc.) to avoid port conflicts.

**Rationale:** Port offsets allow ICDEV and child apps to coexist on the same host during development and testing without conflicts. In production K8s, each app has its own namespace and ports are internal.

---

## Pattern Summary

| # | Pattern | Key Principle |
|---|---------|---------------|
| 1 | GOTCHA Framework | Separate AI orchestration from deterministic execution |
| 2 | Agent Tiers | Core always present; domain conditional; support always present |
| 3 | A2A Protocol | JSON-RPC 2.0 over mTLS for agent communication |
| 4 | Blueprint-Driven | Single JSON drives all generation; no hardcoded decisions |
| 5 | Copy-and-Adapt | ICDEV tools are the source of truth; adapt during copy |
| 6 | Grandchild Prevention | 3-layer enforcement: config flag, scaffolder strip, CLAUDE.md |
| 7 | Minimal DB | Core tables first; capabilities expand via migration |
| 8 | Dynamic CLAUDE.md | Jinja2 template renders only present capabilities |
| 9 | CSP MCP Integration | Cloud provider MCP servers selected per blueprint |
| 10 | Port Offset | Child ports = ICDEV base + offset (default 1000) |

---

## Related Files

- **Goal:** `goals/agentic_generation.md` — Workflow that uses these patterns
- **Context:** `context/agentic/fitness_rubric.md` — Scoring rubric for fitness assessment
- **Context:** `context/agentic/governance_baseline.md` — Governance requirements for child apps
- **Context:** `context/agentic/capability_registry.yaml` — Capability definitions and dependencies
- **Context:** `context/agentic/csp_mcp_registry.yaml` — Cloud provider MCP server mappings
- **Tools:** `tools/builder/agentic_fitness.py`, `tools/builder/app_blueprint.py`, `tools/builder/scaffolder.py`
