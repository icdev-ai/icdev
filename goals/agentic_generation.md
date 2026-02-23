# [TEMPLATE: CUI // SP-CTI]

# Goal: Agentic Application Generation

## Purpose

Generate mini-ICDEV clone applications that include the full GOTCHA framework, ATLAS workflow, own agents, memory system, and CI/CD — everything except the ability to generate new applications. These child applications are fully autonomous, cloud-native systems capable of building, testing, deploying, and self-healing their own workloads.

**Why this matters:** Organizations need multiple independent agentic systems — one per program, per enclave, or per mission thread. Manually bootstrapping each system is error-prone and takes weeks. Automated generation produces a consistent, compliant, tested child application in minutes, with all GOTCHA layers pre-wired and ready for mission-specific customization.

---

## When to Use

- User requests a new cloud-native application with agentic capabilities
- `/icdev-agentic` slash command is invoked
- Scaffolder is called with `--agentic` flag
- A new program office needs its own ICDEV-like system
- Standing up a child system in a new enclave or classification level

---

## Prerequisites

- [ ] ICDEV system initialized (`python tools/db/init_icdev_db.py`)
- [ ] Fitness scorer available (`tools/builder/agentic_fitness.py`)
- [ ] Blueprint engine available (`tools/builder/app_blueprint.py`)
- [ ] Scaffolder supports `--agentic` flag (`tools/builder/scaffolder.py`)
- [ ] `memory/MEMORY.md` loaded (session context)
- [ ] Project defaults configured (`args/project_defaults.yaml`)
- [ ] Target output directory exists and is writable

---

## Workflow

### Step 1: Gather Requirements

Collect the application specification from the user. This is an interactive step — the orchestrator asks questions, the user answers.

**Information to collect:**
- Application name and description (kebab-case, max 32 chars)
- Cloud provider: AWS GovCloud / GCP / Azure / Oracle Cloud / IBM Cloud (IC4G)
- Cloud region (default: `us-gov-west-1` for AWS GovCloud)
- Compliance requirements: ATO (FedRAMP, CMMC), NIST 800-53 baseline
- Impact level: IL2 (Public), IL4 (CUI/GovCloud), IL5 (CUI/Dedicated), IL6 (SECRET/SIPR)
- Primary language: Python, Java, JavaScript/TypeScript, Go, Rust, C#
- MBSE enabled? (digital thread, SysML/DOORS integration)
- Target deployment: K8s/OpenShift, ECS, Lambda, VM-based

**Output:** Specification JSON stored in `.tmp/agentic_spec.json`

**Error handling:**
- Missing required fields -> prompt user, do not proceed with defaults for critical fields
- Invalid cloud provider -> list supported providers, ask again
- IL6 requested without SIPR confirmation -> warn about network constraints, require explicit confirmation

---

### Step 2: Assess Fitness

**Tool:** `python tools/builder/agentic_fitness.py --spec "<spec>" --json`

Score the specification across 6 dimensions:

| Dimension | Weight | What It Measures |
|-----------|--------|------------------|
| data_complexity | 10% | Schema complexity, relationships, event sourcing |
| decision_complexity | 25% | Workflow branching, classification, adaptive behavior |
| user_interaction | 20% | NLQ interfaces, conversational UI, exploratory search |
| integration_density | 15% | External APIs, agent mesh, cross-system sync |
| compliance_sensitivity | 15% | CUI/SECRET, FedRAMP, CMMC, FIPS encryption |
| scale_variability | 15% | Burst patterns, auto-scaling, real-time streaming |

**Recommendation mapping:**

| Overall Score | Architecture |
|---------------|-------------|
| >= 6.0 | Full agent architecture |
| 4.0 - 5.9 | Hybrid (agent + traditional) |
| < 4.0 | Traditional REST/CRUD |

**Output:** Fitness scorecard JSON with `overall_score`, per-dimension scores, and `architecture` recommendation. Stored in `.tmp/agentic_fitness.json`.

**Error handling:**
- Score < 4.0 and `--agentic` flag specified -> warn user that traditional architecture is recommended, proceed only if user explicitly confirms
- Missing spec fields -> score available dimensions, warn about incomplete assessment

**Verify:** Scorecard has all 6 dimensions scored. Architecture recommendation present.

---

### Step 3: User Decisions

Present the fitness scorecard and confirm decisions with the user before proceeding.

**Decisions to confirm:**
1. **Architecture:** Accept recommendation or override? (agent / hybrid / traditional)
2. **MBSE enabled?** Digital thread traceability (SysML/DOORS integration)
3. **ATO required?** Triggers compliance agents, CUI markings, NIST control mapping
4. **Cloud provider and region?** Confirm from Step 1 or change
5. **Parent callback URL?** For A2A communication back to ICDEV (optional)
6. **Port offset?** Agent ports = ICDEV base ports + offset (default: +1000)
7. **Agent roster override?** Accept blueprint defaults or add/remove agents

**Output:** User decisions JSON stored in `.tmp/agentic_decisions.json`

**Error handling:**
- User overrides architecture to "agent" when score < 4.0 -> log decision with rationale, proceed
- Port conflict detected -> suggest next available offset, confirm with user

**Log decision:**

**Tool:** `python tools/audit/decision_recorder.py --project-id "<project_id>" --decision "Agentic generation approved" --rationale "<user rationale>" --actor "user"`

---

### Step 4: Generate Blueprint

**Tool:** `python tools/builder/app_blueprint.py --fitness-scorecard .tmp/agentic_fitness.json --user-decisions '.tmp/agentic_decisions.json' --app-name "<name>" --json`

The blueprint is the single source of truth for all generation steps. No hardcoded decisions — every generator reads the blueprint.

**Blueprint includes:**

| Section | Contents |
|---------|----------|
| capability_map | What to include: memory, compliance, security, mbse, monitoring, self-healing |
| agent_roster | 5 core agents (always) + conditional domain/support agents |
| file_manifest | What to copy from ICDEV and adapt (port remap, db rename, path fixup) |
| csp_mcp_servers | Cloud provider MCP server selection based on capabilities + provider |
| db_schema | Core tables first, capability tables via `migrate_add_capability()` |
| memory_config | Memory system settings: dual storage (markdown + SQLite), embedding model |
| cicd_config | Pipeline template, stages, gates, webhook endpoints |
| atlas_config | ATLAS or M-ATLAS workflow (if MBSE enabled) |
| gotcha_dirs | Directory structure: goals/, tools/, args/, context/, hardprompts/ |
| agent_ports | Base port + offset for each agent |
| classification | CUI markings, impact level, encryption requirements |

**Output:** Blueprint JSON stored in `.tmp/agentic_blueprint.json`

**Error handling:**
- Unsupported capability combination -> warn and suggest alternative, do not silently drop capabilities
- CSP MCP server not available for provider -> fall back to generic HTTP tools, warn user

**Verify:** Blueprint has all sections populated. Agent roster matches fitness recommendation. File manifest references only existing source files.

---

### Step 5: Scaffold + Generate

**Tool:** `python tools/builder/scaffolder.py --project-path <path> --name <name> --type <type> --agentic --fitness-scorecard .tmp/agentic_fitness.json`

This is the main generation step. It runs two phases:

**Phase 1: Base Language Scaffold (15+ files)**
- Project structure for the selected language
- Package manager config (requirements.txt / pom.xml / package.json / go.mod / Cargo.toml / .csproj)
- Entry point, health endpoint, configuration loader
- Dockerfile (STIG-hardened base)
- .gitignore, .env.example

**Phase 2: Agentic Generation (12 steps)**

| Step | What It Generates |
|------|-------------------|
| 1 | GOTCHA directory structure: `goals/`, `tools/`, `args/`, `context/`, `hardprompts/` |
| 2 | Tool scripts: copy from ICDEV with path/port/db adaptations |
| 3 | Agent definitions: `tools/agent/` with agent cards at `tools/agent/cards/*.json` |
| 4 | Memory system: `memory/MEMORY.md`, `tools/memory/`, SQLite schema |
| 5 | Goal files: `goals/manifest.md` + relevant goal workflows |
| 6 | Args configs: `args/project_defaults.yaml`, `args/agent_config.yaml` |
| 7 | Context files: language registry, compliance catalogs, tone rules |
| 8 | Hard prompts: task decomposition, architect, builder prompts |
| 9 | CI/CD pipeline: `.gitlab-ci.yml` or `.github/workflows/` |
| 10 | Database init: `tools/db/init_<name>_db.py` with core + capability tables |
| 11 | CLAUDE.md: Jinja2 template rendering only present capabilities |
| 12 | Grandchild prevention: config flag, scaffolder stripping, CLAUDE.md documentation |

**Output:** Fully scaffolded child application at `<project-path>/<name>/`

**Error handling:**
- Source tool file missing in ICDEV -> warn and skip (graceful degradation), log missing file
- Target directory already exists -> overlay new files, do NOT delete existing files
- Sister modules unavailable (e.g., MBSE tools when MBSE not installed) -> use inline fallbacks
- File copy fails (permissions) -> report error, continue with remaining files

---

### Step 6: Verify

Run verification checks on the generated child application.

**Directory structure checks:**
```bash
# Required directories exist
ls <project-path>/<name>/goals/
ls <project-path>/<name>/tools/agent/
ls <project-path>/<name>/memory/
ls <project-path>/<name>/data/
ls <project-path>/<name>/args/
ls <project-path>/<name>/context/
ls <project-path>/<name>/hardprompts/
```

**Content checks:**
- CLAUDE.md exists and does NOT reference ICDEV generation tools (`agentic_fitness.py`, `app_blueprint.py`)
- CLAUDE.md contains "CANNOT generate child applications" grandchild prevention statement
- Agent cards present in `tools/agent/cards/` for all agents in the roster
- Goals manifest (`goals/manifest.md`) only lists goals that exist as files
- CUI markings present on all Python files and markdown files
- No hardcoded ICDEV paths or ports in generated files

**Functional checks:**
```bash
# Initialize child database
python <project-path>/<name>/tools/db/init_<name>_db.py

# Verify memory system
python <project-path>/<name>/tools/memory/memory_read.py --format markdown

# Verify agent cards are valid JSON
python -c "import json; [json.load(open(f)) for f in glob.glob('<path>/tools/agent/cards/*.json')]"
```

**Error handling:**
- Missing directory -> log as generation failure, list what's missing
- CLAUDE.md references generation tools -> strip references, re-generate CLAUDE.md section
- Database init fails -> check schema, report error with table that failed

---

### Step 7: Document and Register

**Register child application:**

**Tool:** `python tools/audit/audit_logger.py --event-type "agentic.generation" --actor "orchestrator-agent" --action "Generated child app: <name>" --project-id "<project_id>"`

**Store generation summary in child app:**
Write `data/generation_summary.json` in the child application directory containing:
- Parent ICDEV version
- Blueprint hash (SHA-256)
- Fitness scorecard summary
- User decisions summary
- Generation timestamp
- Files generated count
- Agent roster
- Grandchild prevention: active

**Record in ICDEV memory:**

**Tool:** `python tools/memory/memory_write.py --content "Generated agentic child app '<name>' at <path>. Architecture: <arch>. Agents: <count>. Compliance: <level>." --type event --importance 8`

**Verify:** Audit trail entry exists. Generation summary valid JSON. Memory entry recorded.

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D20 | Flag-based (`--agentic`) activation | Backward compatible — existing scaffolding unchanged without flag |
| D21 | Copy-and-adapt over template library | ICDEV's own tools are the source of truth; no template drift |
| D22 | Weighted rule-based fitness + optional LLM override | Deterministic scoring with human escape hatch |
| D23 | Blueprint-driven generation | Single JSON drives all generators; no hardcoded decisions |
| D28 | 3-layer grandchild prevention | Config flag + scaffolder stripping + CLAUDE.md documentation |
| D29 | Port offset for child agents | Avoids port conflicts when ICDEV and child run on same host |

---

## Edge Cases

1. **Fitness score < 4.0 with `--agentic` flag:** Warn user that traditional architecture is recommended. Proceed only if user explicitly confirms. Log the override decision.
2. **Source tool files missing:** Warn and skip missing tools (graceful degradation). The child app will have reduced capabilities but will still function.
3. **Sister modules unavailable:** If MBSE or modernization tools are not installed in ICDEV, use inline fallbacks in the child app rather than broken imports.
4. **Child app directory already exists:** Overlay new files on top of existing directory. Never delete existing files — the user may have customized them.
5. **Port conflict with running services:** Detect conflicts before generation. Suggest the next available port offset.
6. **IL6 classification:** Ensure all generated files use SECRET markings. Verify SIPR-only network constraints in generated infrastructure.
7. **No internet access (air-gapped):** Ensure all dependencies are available in the local PyPI mirror. Do not generate code that requires public internet.
8. **Multiple child apps from same ICDEV:** Each gets a unique port offset and database name. Registry tracks all children.
9. **Blueprint hash mismatch during verification:** Regenerate the mismatched component. Do not ship a child app with integrity violations.
10. **User requests capabilities not in ICDEV:** Log as a feature request. Do not generate stub capabilities that do nothing.

---

## Anti-Patterns

1. **Generating without fitness assessment** — You end up with an agent architecture for a simple CRUD app, wasting resources and adding complexity.
2. **Hardcoding decisions in generators** — Blueprint is the single source of truth. Generators that make their own decisions drift from the specification.
3. **Allowing grandchild generation** — Uncontrolled proliferation of agentic systems. Enforce the 3-layer prevention.
4. **Skipping verification** — A child app that fails to initialize its database or load its memory system is useless.
5. **Copying tools without adaptation** — ICDEV-specific paths, ports, and database names must be remapped for the child context.

---

## Success Criteria

- [ ] Fitness assessment completed with all 6 dimensions scored
- [ ] User decisions confirmed and logged in audit trail
- [ ] Blueprint generated with all sections populated
- [ ] Child app scaffolded with GOTCHA directory structure
- [ ] All agents in roster have valid agent cards
- [ ] Database initializes successfully
- [ ] Memory system reads and writes correctly
- [ ] CLAUDE.md is accurate and does not reference ICDEV generation tools
- [ ] Grandchild prevention active (3-layer check passes)
- [ ] CUI markings present on all generated files
- [ ] Generation summary stored in child's `data/generation_summary.json`
- [ ] Audit trail entry logged in parent ICDEV

---

## GOTCHA Layer Mapping

| Step | GOTCHA Layer | Component |
|------|-------------|-----------|
| Gather Requirements | Orchestration | AI (you) + user interaction |
| Assess Fitness | Tools | `agentic_fitness.py` |
| User Decisions | Orchestration | AI (you) + decision recording |
| Generate Blueprint | Tools | `app_blueprint.py` |
| Scaffold + Generate | Tools | `scaffolder.py` (with `--agentic` flag) |
| Verify | Orchestration | AI (you) + verification checks |
| Document | Tools | `audit_logger.py`, `memory_write.py` |
| Fitness rubric | Context | `context/agentic/fitness_rubric.md` |
| Architecture patterns | Context | `context/agentic/architecture_patterns.md` |
| Governance baseline | Context | `context/agentic/governance_baseline.md` |
| Behavior settings | Args | `args/project_defaults.yaml` agentic section |

---

## Related Files

- **Goals:** `goals/build_app.md` (ATLAS workflow used by child apps), `goals/tdd_workflow.md` (TDD cycle used by child apps), `goals/compliance_workflow.md` (ATO artifacts if compliance enabled)
- **Tools:** `tools/builder/agentic_fitness.py`, `tools/builder/app_blueprint.py`, `tools/builder/scaffolder.py`
- **Context:** `context/agentic/fitness_rubric.md`, `context/agentic/architecture_patterns.md`, `context/agentic/governance_baseline.md`, `context/agentic/capability_registry.yaml`, `context/agentic/csp_mcp_registry.yaml`
- **Hard Prompts:** `hardprompts/agent/agentic_architect.md`, `hardprompts/agent/skill_design.md`, `hardprompts/agent/governance_review.md`
- **Args:** `args/project_defaults.yaml` (agentic section), `args/agent_config.yaml`
- **MCP Server:** `tools/mcp/builder_server.py` (scaffold, generate_code tools)
- **Skill:** `.claude/skills/icdev-agentic/SKILL.md`

---

## Changelog

- 2026-02-17: Initial creation — Phase 19 agentic application generation workflow with 7-step process, fitness assessment, blueprint-driven generation, and grandchild prevention
