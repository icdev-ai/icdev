# Phase 2 — ATLAS Build Workflow

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 2 |
| Title | ATLAS Build Workflow |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 1 (GOTCHA Framework Foundation) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

Building production-grade applications with AI assistance requires more than code generation. Without a structured build methodology, AI-assisted development produces "vibe code" -- applications that demo well but fail under production scrutiny. Common failure modes include: building before designing (leading to full rewrites), skipping connection validation (hours wasted on broken integrations), no data modeling (schema changes cascading into UI rewrites), and no testing (shipping broken code).

Government and DoD systems demand a rigorous, repeatable build process that produces auditable, traceable, and production-ready artifacts. The build process must enforce design-before-code, validate all integrations before implementation, and stress-test before delivery. Furthermore, Model-Based Systems Engineering (MBSE) projects require an additional pre-phase that imports authoritative system models and establishes a digital thread before any design decisions are made.

ATLAS provides this structured 5-step build methodology (Architect, Trace, Link, Assemble, Stress-test), with M-ATLAS extending it to 6 steps by adding a Model pre-phase for MBSE-enabled projects. Each step maps directly to a GOTCHA layer, ensuring the framework's separation of concerns is maintained throughout the build lifecycle.

---

## 2. Goals

1. Define a repeatable 5-step build process (ATLAS) that prevents common "vibe coding" anti-patterns
2. Ensure all applications start with architecture decisions (Architect) before any code is written
3. Require data schema definition, integration mapping, and technology stack approval (Trace) before implementation
4. Validate all external connections (APIs, databases, MCP servers, OAuth flows) before building (Link)
5. Enforce a layered build order: database first, backend second, frontend last (Assemble)
6. Require functional, integration, and edge case testing before delivery (Stress-test)
7. Support MBSE-enabled projects via M-ATLAS variant that adds a Model pre-phase for SysML/DOORS NG import
8. Integrate acceptance criteria validation (V&V) as a mandatory gate in the Stress-test phase

---

## 3. Architecture

### 3.1 ATLAS Pipeline

```
+-------+    +-------+    +-------+    +----------+    +------------+
|   A   |--->|   T   |--->|   L   |--->|    A     |--->|     S      |
|Archi- |    |Trace  |    |Link   |    |Assemble  |    |Stress-test |
|tect   |    |       |    |       |    |          |    |            |
|Define |    |Data   |    |Validate|   |Build     |    |Test        |
|problem,|   |schema,|    |connec-|    |layers:   |    |functional, |
|users,  |    |integ- |    |tions  |    |DB->API-> |   |integration,|
|success |    |rations|    |before |    |UI        |    |edge cases  |
|metrics |    |stack  |    |build  |    |          |    |+ V&V gate  |
+-------+    +-------+    +-------+    +----------+    +------------+
                                            ^                |
                                            |  Issues found  |
                                            +----------------+
```

### 3.2 M-ATLAS Variant (MBSE-Enabled)

```
+-------+    +-------+    +-------+    +-------+    +----------+    +------------+
|   M   |--->|   A   |--->|   T   |--->|   L   |--->|    A     |--->|     S      |
|Model  |    |Archi- |    |Trace  |    |Link   |    |Assemble  |    |Stress-test |
|       |    |tect   |    |       |    |       |    |          |    |            |
|Import |    |System |    |Schema |    |Validate|   |Build w/  |    |Test incl.  |
|XMI/   |    |design |    |+ model|    |+ model|    |model-gen |    |model-gen   |
|ReqIF, |    |from   |    |trace- |    |code   |    |scaffold  |    |test stubs  |
|digital|    |model  |    |ability|    |maps   |    |          |    |            |
|thread |    |       |    |       |    |       |    |          |    |            |
+-------+    +-------+    +-------+    +-------+    +----------+    +------------+
```

### 3.3 GOTCHA Layer Mapping

| ATLAS Step | GOTCHA Layer | Role |
|------------|--------------|------|
| Architect | Goals | Define the process, problem, success criteria |
| Trace | Context | Reference patterns, data schemas, integrations |
| Link | Args | Environment setup, connection validation |
| Assemble | Tools | Deterministic execution of build steps |
| Stress-test | Orchestration | AI validates results, evaluates acceptance criteria |

---

## 4. Requirements

### 4.1 Architect Phase

#### REQ-02-001: Problem Definition
The system SHALL require a clear one-sentence problem statement before proceeding to any subsequent build phase.

#### REQ-02-002: User Identification
The system SHALL require identification of specific target users (not "everyone") as part of the architecture phase.

#### REQ-02-003: Success Metrics
The system SHALL require measurable success criteria that can be objectively evaluated during the Stress-test phase.

#### REQ-02-004: Agentic Fitness Assessment
The system SHALL evaluate each component's fitness for agentic architecture via `agentic_fitness.py`, scoring 6 dimensions (data_complexity, decision_complexity, user_interaction, integration_density, compliance_sensitivity, scale_variability) before architecture decisions.

### 4.2 Trace Phase

#### REQ-02-005: Data Schema First
The system SHALL require data schema definition (tables, relationships, field types) before any code generation begins.

#### REQ-02-006: Integration Mapping
The system SHALL require documentation of all external service connections (APIs, databases, MCP servers) with authentication types and availability status.

#### REQ-02-007: Technology Stack Approval
The system SHALL propose a technology stack and obtain user approval before proceeding to the Link phase.

#### REQ-02-008: Edge Case Documentation
The system SHALL document potential failure modes (rate limits, auth expiry, timeouts, invalid input) during the Trace phase.

### 4.3 Link Phase

#### REQ-02-009: Connection Validation
The system SHALL validate all database connections, API endpoints, MCP servers, and OAuth flows before proceeding to the Assemble phase.

#### REQ-02-010: Validation Completeness
All items on the connection validation checklist SHALL show green status before the Assemble phase begins.

### 4.4 Assemble Phase

#### REQ-02-011: Build Order Enforcement
The system SHALL enforce the build order: database schema first, backend API routes second, frontend UI last.

#### REQ-02-012: GOTCHA Separation
Generated code SHALL follow GOTCHA separation of concerns -- UI components, business logic, and data access in distinct layers.

### 4.5 Stress-test Phase

#### REQ-02-013: Functional Testing
The system SHALL verify all user-facing functionality (buttons, data persistence, navigation, error states) during the Stress-test phase.

#### REQ-02-014: Integration Testing
The system SHALL verify all external connections hold under test conditions (API calls succeed, auth persists, rate limits respected).

#### REQ-02-015: Acceptance Validation Gate
The system SHALL run acceptance criteria validation (`acceptance_validator.py`) with blocking conditions: 0 failed acceptance criteria, 0 pages with error patterns (500, tracebacks, JS errors).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `projects` | Project metadata including type, classification, status |
| `agents` | Agent registry for multi-agent orchestration |
| `audit_trail` | Append-only log of all build actions and decisions |
| `agentic_fitness_assessments` | Fitness scoring results for agentic architecture decisions |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/builder/agentic_fitness.py` | 6-dimension fitness scoring for agentic architecture |
| `tools/builder/app_blueprint.py` | Generate blueprint from fitness scorecard + user decisions |
| `tools/builder/scaffolder.py` | Scaffold project structure based on type and blueprint |
| `tools/builder/code_generator.py` | Generate implementation code from test specs |
| `tools/builder/test_writer.py` | Generate Gherkin features and pytest test cases |
| `tools/builder/linter.py` | Run language-appropriate linting (flake8/ruff, eslint) |
| `tools/builder/formatter.py` | Run language-appropriate formatting (black, prettier) |
| `tools/builder/language_support.py` | Detect project languages and list supported languages |
| `tools/testing/acceptance_validator.py` | Validate acceptance criteria against test evidence |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D9 | M-ATLAS adds "Model" pre-phase to ATLAS (backward compatible) | Skips if no model exists; ATLAS starts at Architect for non-MBSE projects |
| D44 | Flag-based (`--agentic`) for backward compatibility | Omitting the flag produces identical output to pre-agentic behavior |
| D46 | Fitness scoring: weighted rule-based + optional LLM override | Deterministic scoring with escape hatch for nuanced assessments |
| D47 | Blueprint-driven generation -- single config drives all generators | One source of truth for the entire generation pipeline |

---

## 8. Security Gate

**Acceptance Validation Gate:**
- 0 failed acceptance criteria
- 0 pages rendering with error patterns (500, tracebacks, JS errors)
- Plan MUST contain `## Acceptance Criteria` section
- V&V before handoff -- NEVER declare a fix/feature complete based solely on API validation; browser verification required for UI changes

---

## 9. Commands

```bash
# Agentic fitness assessment (Step 0)
python tools/builder/agentic_fitness.py --spec "..." --project-id "proj-123" --json

# Blueprint generation
python tools/builder/app_blueprint.py --fitness-scorecard sc.json \
  --user-decisions '{}' --app-name "my-app" --json

# Project scaffolding
python tools/builder/scaffolder.py --project-path /tmp --name "my-app" \
  --type api --agentic --fitness-scorecard sc.json

# Language detection
python tools/builder/language_support.py --detect "/path/to/project"
python tools/builder/language_support.py --list

# M-ATLAS Model phase (MBSE-enabled projects)
python tools/mbse/xmi_parser.py --project-id "proj-123" --file model.xmi --json
python tools/mbse/reqif_parser.py --project-id "proj-123" --file reqs.reqif --json
python tools/mbse/digital_thread.py --project-id "proj-123" auto-link --json
python tools/mbse/model_code_generator.py --project-id "proj-123" --language python --output ./src
python tools/mbse/model_control_mapper.py --project-id "proj-123" --map-all --json

# Build workflow skill
/icdev-build    # Build code using true TDD via M-ATLAS workflow
```
