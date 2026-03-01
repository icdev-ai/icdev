# Phase 19 — Agentic Application Generation

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 19 |
| Title | Agentic Application Generation |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 12 (Build App / ATLAS Workflow), Phase 11 (Compliance Workflow) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Organizations need multiple independent agentic systems -- one per program, per enclave, or per mission thread. Manually bootstrapping each system is error-prone and takes weeks. Each new agentic application requires the full GOTCHA framework (6 layers), ATLAS workflow, agent infrastructure, memory system, compliance scaffolding, CI/CD pipeline, and cloud provider integration -- all wired together correctly from day one.

Without automated generation, teams either copy-paste from existing systems (creating drift and inconsistency) or build from scratch (wasting weeks of effort and introducing configuration errors). The result is a proliferation of inconsistent agentic systems that cannot be audited, updated, or governed centrally.

ICDEV needs the ability to generate mini-ICDEV clone child applications that inherit the full GOTCHA framework, ATLAS workflow, own agents, memory system, and CI/CD -- everything except the ability to generate further children (grandchild prevention). These child applications must be fully autonomous, cloud-native systems capable of building, testing, deploying, and self-healing their own workloads, produced in minutes rather than weeks.

---

## 2. Goals

1. Assess application specifications across 6 fitness dimensions (data complexity, decision complexity, user interaction, integration density, compliance sensitivity, scale variability) to recommend architecture type (full agent, hybrid, traditional)
2. Generate a comprehensive blueprint from fitness scorecard and user decisions that drives all subsequent generation steps
3. Scaffold child applications with the full GOTCHA directory structure (goals/, tools/, args/, context/, hardprompts/)
4. Generate 5 core agents (Orchestrator, Architect, Builder, Knowledge, Monitor) plus up to 2 ATO agents (Compliance, Security) when compliance is required
5. Produce dynamic CLAUDE.md via Jinja2 that documents only the capabilities present in the child application
6. Generate standalone DB init scripts with core tables and capability-specific tables added via migration
7. Integrate cloud provider MCP servers (AWS, GCP, Azure, Oracle, IBM) based on target cloud configuration
8. Enforce 3-layer grandchild prevention: config flag, scaffolder stripping, CLAUDE.md documentation (D52)

---

## 3. Architecture

```
+-------------------+     +-------------------+     +-------------------+
|  User Spec        |---->|  Fitness Assessor  |---->|  User Decisions   |
|  (interactive)    |     |  (6 dimensions)   |     |  (confirm/override)|
+-------------------+     +-------------------+     +-------------------+
                                                            |
                                                            v
                                                    +-------------------+
                                                    |  Blueprint Engine |
                                                    |  (single source   |
                                                    |   of truth)       |
                                                    +-------------------+
                                                            |
                                                            v
                                                    +-------------------+
                                                    |  Scaffolder       |
                                                    |  (--agentic flag) |
                                                    +-------------------+
                                                            |
                     +------+------+------+------+------+---+---+
                     |      |      |      |      |      |       |
                     v      v      v      v      v      v       v
                  GOTCHA  Agents Memory  Goals  CI/CD   DB    CLAUDE.md
                  Dirs    Cards  System  Files  Pipeline Init  (Jinja2)
```

The generation pipeline follows 7 steps:

1. **Gather Requirements** -- Interactive collection of app name, cloud provider, compliance needs, impact level, language, MBSE, and deployment target
2. **Assess Fitness** -- Score across 6 weighted dimensions (>= 6.0 full agent, 4.0-5.9 hybrid, < 4.0 traditional)
3. **User Decisions** -- Confirm architecture, MBSE, ATO, cloud provider, port offset, agent roster
4. **Generate Blueprint** -- Single JSON blueprint drives all generators (capability map, agent roster, file manifest, CSP MCP servers, DB schema, memory config, CI/CD config, ATLAS config, classification)
5. **Scaffold + Generate** -- Phase 1: base language scaffold (15+ files), Phase 2: agentic generation (12 steps)
6. **Verify** -- Directory structure, content, and functional checks
7. **Document and Register** -- Audit trail, generation summary, memory entry

---

## 4. Requirements

### 4.1 Fitness Assessment

#### REQ-19-001: 6-Dimension Fitness Scoring
The system SHALL score application specifications across: data_complexity (10%), decision_complexity (25%), user_interaction (20%), integration_density (15%), compliance_sensitivity (15%), and scale_variability (15%).

#### REQ-19-002: Architecture Recommendation
The system SHALL recommend architecture based on score: >= 6.0 full agent, 4.0-5.9 hybrid, < 4.0 traditional. Users MAY override the recommendation with documented rationale.

### 4.2 Blueprint Generation

#### REQ-19-003: Blueprint as Single Source of Truth
The blueprint JSON SHALL be the sole driver of all generation steps. No generator SHALL make hardcoded decisions outside the blueprint (D47).

#### REQ-19-004: CSP MCP Server Selection
The blueprint SHALL include cloud provider MCP server selections based on target cloud (AWS: 66+ servers, GCP: managed endpoints, Azure: catalog, Oracle: OCI servers, IBM: IC4G servers with watsonx.ai) from `context/agentic/csp_mcp_registry.yaml`.

### 4.3 Child Application Generation

#### REQ-19-005: GOTCHA Framework
The child application SHALL include the complete GOTCHA directory structure: goals/, tools/, args/, context/, hardprompts/ with content adapted from ICDEV.

#### REQ-19-006: Agent Roster
The child application SHALL include 5 core agents (Orchestrator, Architect, Builder, Knowledge, Monitor) with configurable port offsets (default +1000). Up to 2 additional ATO agents (Compliance, Security) SHALL be included when compliance is required.

#### REQ-19-007: Memory System
The child application SHALL include a complete memory system: MEMORY.md, daily logs, SQLite database with embeddings, and semantic search capability.

#### REQ-19-008: Dynamic CLAUDE.md
The child application SHALL include a CLAUDE.md generated via Jinja2 template that documents only the capabilities present in that specific child application (D50).

### 4.4 Governance

#### REQ-19-009: Grandchild Prevention
The system SHALL enforce 3-layer grandchild prevention (D52): config flag (`can_generate_children: false`), scaffolder stripping (generation tools excluded from child), and CLAUDE.md documentation ("CANNOT generate child applications").

#### REQ-19-010: CUI Markings
All generated files SHALL carry CUI // SP-CTI markings appropriate to the child application's impact level.

#### REQ-19-011: Generation Audit Trail
The system SHALL record all generation events in the append-only audit trail and store a `data/generation_summary.json` in each child application.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `child_app_registry` | Registered child apps: name, path, architecture, agent_count, compliance_level, genome_version |
| `agentic_fitness_assessments` | Fitness scorecards: spec_json, dimension_scores, overall_score, recommendation |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/builder/agentic_fitness.py` | 6-dimension fitness scoring engine |
| `tools/builder/app_blueprint.py` | Blueprint generation from scorecard + user decisions |
| `tools/builder/child_app_generator.py` | 12-step child application generation |
| `tools/builder/claude_md_generator.py` | Dynamic CLAUDE.md via Jinja2 template rendering |
| `tools/builder/goal_adapter.py` | Copy and adapt goals from ICDEV for child app context |
| `tools/builder/db_init_generator.py` | Generate standalone DB init scripts with core + capability tables |
| `tools/builder/scaffolder.py` | Base language scaffold with `--agentic` flag for full generation |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D44 | Flag-based (`--agentic`) for backward compatibility | Omitting flag produces identical output; existing scaffolding unchanged |
| D45 | Copy-and-adapt over template library | ICDEV tools are the source of truth; no template drift |
| D46 | Weighted rule-based fitness + optional LLM override | Deterministic scoring with human escape hatch |
| D47 | Blueprint-driven generation | Single JSON config drives all generators; no hardcoded decisions |
| D48 | ICDEV callback uses A2A protocol for child-to-parent communication | Standard protocol for all agent communication |
| D50 | Dynamic CLAUDE.md via Jinja2 | Documents only what is present; no stale references |
| D51 | Minimal DB + migration | Core tables first, expand as capabilities activate |
| D52 | 3-layer grandchild prevention (config + scaffolder + CLAUDE.md) | Prevents uncontrolled proliferation of agentic systems |
| D53 | Port offset for child agents (default +1000) | Avoids port conflicts when ICDEV and child run on same host |

---

## 8. Security Gate

**Agentic Generation Gate:**
- Fitness assessment completed with all 6 dimensions scored before generation proceeds
- User decisions confirmed and logged in audit trail
- Blueprint integrity verified (SHA-256 hash match)
- Grandchild prevention verified: config flag set, generation tools excluded, CLAUDE.md contains prevention statement
- CUI markings present on all generated files
- Child database initializes successfully
- No hardcoded ICDEV paths or ports in generated files

---

## 9. Commands

```bash
# Assess fitness
python tools/builder/agentic_fitness.py --spec "..." --json

# Generate blueprint
python tools/builder/app_blueprint.py --fitness-scorecard sc.json \
  --user-decisions '{}' --app-name "my-app" --json

# Generate child application
python tools/builder/child_app_generator.py --blueprint bp.json \
  --project-path /tmp --name "my-app" --json

# Scaffold with agentic flag
python tools/builder/scaffolder.py --project-path /tmp --name "my-app" \
  --type api --agentic --fitness-scorecard sc.json

# Generate dynamic CLAUDE.md
python tools/builder/claude_md_generator.py --blueprint bp.json --output CLAUDE.md

# Initialize child database
python tools/db/init_my_app_db.py
```
