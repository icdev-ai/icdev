# GOTCHA Framework Architecture Guide

CUI // SP-CTI

## Overview

GOTCHA is a 6-layer agentic system architecture that separates probabilistic AI orchestration from deterministic business logic. The name is an acronym for its layers: **G**oals, **O**rchestration, **T**ools, **C**ontext, **H**ard Prompts, and **A**rgs.

### The Core Problem

LLMs are probabilistic. Business logic must be deterministic. At 90% accuracy per step, a 5-step workflow achieves only ~59% end-to-end accuracy (0.9^5 = 0.59). GOTCHA solves this by confining LLM reasoning to orchestration decisions while delegating all execution to deterministic Python scripts.

```
Accuracy degradation without separation of concerns:

  Step 1    Step 2    Step 3    Step 4    Step 5
  ------    ------    ------    ------    ------
   90%  -->  81%  -->  73%  -->  66%  -->  59%

With GOTCHA: LLM decides WHAT to do, deterministic tools DO it.
Each tool is 100% deterministic. LLM only picks the order.
```

---

## The 6 Layers

```
+-----------------------------------------------------------------------+
|                                                                       |
|  +-------------------+      +-------------------------------------+   |
|  |     GOALS (G)     | ---> |        ORCHESTRATION (O)            |   |
|  | Process defs:     |      | The AI (Claude) reads goals,        |   |
|  | what to achieve,  |      | decides tool order, applies args,   |   |
|  | which tools,      |      | references context, uses hard       |   |
|  | expected outputs, |      | prompts, handles errors.            |   |
|  | edge cases        |      |                                     |   |
|  +-------------------+      | Never executes work directly --     |   |
|                              | delegates to deterministic tools.   |   |
|  +-------------------+      +------+---------+----------+---------+   |
|  |    TOOLS (T)      | <----------+         |          |             |
|  | Python scripts,   |                      v          v             |
|  | one job each.     |      +---------------+--+  +----+----------+  |
|  | Deterministic.    |      | CONTEXT (C)      |  | HARD PROMPTS  |  |
|  | Don't think,      |      | Static ref       |  | (H)           |  |
|  | just execute.     |      | material: tone,  |  | Reusable LLM  |  |
|  +-------------------+      | samples, ICP,    |  | instruction   |  |
|                              | case studies     |  | templates     |  |
|  +-------------------+      +------------------+  +---------------+  |
|  |     ARGS (A)      |                                                |
|  | YAML/JSON config  |                                                |
|  | settings: themes, |                                                |
|  | modes, schedules. |                                                |
|  | Change behavior   |                                                |
|  | without editing   |                                                |
|  | goals or tools.   |                                                |
|  +-------------------+                                                |
|                                                                       |
+-----------------------------------------------------------------------+
```

### Layer Details

| Layer | Directory | Role | Examples |
|-------|-----------|------|----------|
| **Goals** | `goals/` | Process definitions | `build_app.md`, `compliance_workflow.md`, `security_scan.md` |
| **Orchestration** | *(the AI)* | Read goal, decide tool order, apply args, reference context, handle errors | Claude Code / multi-agent system |
| **Tools** | `tools/` | Python scripts. One job each. Deterministic. | `ssp_generator.py`, `scaffolder.py`, `stig_checker.py` |
| **Args** | `args/` | YAML/JSON behavior settings | `project_defaults.yaml`, `security_gates.yaml`, `cui_markings.yaml` |
| **Context** | `context/` | Static reference material | Tone rules, writing samples, ICP descriptions, language registry |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates | Outline-to-post, rewrite-in-voice, summarize |

---

## Directory Structure

```
ICDev/
+-- goals/                     # Process definitions (what to achieve)
|   +-- manifest.md            # Index of all goal workflows
|   +-- build_app.md           # ATLAS/M-ATLAS build workflow
|   +-- compliance_workflow.md # ATO artifact generation
|   +-- security_scan.md       # SAST, dependency audit, secrets
|   +-- deploy_workflow.md     # IaC, pipeline, staging, production
|   +-- ... (40+ goal files)
|
+-- tools/                     # Deterministic Python scripts
|   +-- manifest.md            # Master list of all tools
|   +-- builder/               # Code generation, scaffolding, TDD
|   +-- compliance/            # SSP, POAM, STIG, SBOM, crosswalk
|   +-- security/              # SAST, deps, secrets, container scan
|   +-- infra/                 # Terraform, Ansible, K8s, pipelines
|   +-- knowledge/             # Pattern detection, self-healing
|   +-- monitor/               # Log analysis, metrics, health checks
|   +-- memory/                # Memory read/write, search, embeddings
|   +-- mcp/                   # MCP servers (stdio transport)
|   +-- db/                    # Database init, migration, backup
|   +-- dashboard/             # Flask web UI
|   +-- saas/                  # Multi-tenant SaaS platform
|   +-- ... (30+ tool directories)
|
+-- args/                      # Behavior configuration
|   +-- project_defaults.yaml  # TDD, compliance, security thresholds
|   +-- agent_config.yaml      # 15 agent definitions, ports, models
|   +-- security_gates.yaml    # Blocking conditions for all gates
|   +-- cui_markings.yaml      # CUI banner templates, portion marking
|   +-- cloud_config.yaml      # Multi-cloud CSP settings
|   +-- llm_config.yaml        # LLM provider routing
|   +-- ... (30+ config files)
|
+-- context/                   # Static reference material
|   +-- languages/             # Language registry, profiles
|   +-- compliance/            # Framework catalogs, crosswalk data
|   +-- translation/           # Cross-language dependency mappings
|   +-- cloud/                 # CSP service registry
|   +-- profiles/              # Dev profile starter templates
|   +-- agentic/               # CSP MCP registry for child apps
|
+-- hardprompts/               # Reusable LLM instruction templates
|
+-- memory/                    # Curated long-term context
|   +-- MEMORY.md              # Long-term facts and preferences
|   +-- logs/                  # Daily session logs (YYYY-MM-DD.md)
|
+-- data/                      # SQLite databases
|   +-- icdev.db               # Main operational DB (193 tables)
|   +-- platform.db            # SaaS platform DB (6 tables)
|   +-- memory.db              # Memory system (3 tables)
|   +-- activity.db            # Task tracking (1 table)
|   +-- tenants/               # Per-tenant isolated databases
|
+-- .tmp/                      # Disposable scratch work
```

---

## Manifest Files

### goals/manifest.md

The index of all goal workflows. Check this before starting any task. If a goal exists for the task, follow it.

```
Goal files define:
- What to achieve (objective)
- Which tools to use (tool references)
- Expected outputs (artifacts)
- Edge cases and error handling
- Pre-conditions and post-conditions
```

### tools/manifest.md

The master list of all tools. Check this before writing a new script. If you create a new tool, add it to the manifest.

```
Tool entries define:
- Script path
- Input parameters
- Output format (--json / --human)
- Dependencies (stdlib vs optional)
- Related goals that use the tool
```

**Guardrail**: Always check `tools/manifest.md` before writing a new script. Duplicate tools create maintenance debt.

---

## How Orchestration Works

The AI (Claude) is the orchestration layer. It never executes work directly. The workflow for any task follows this sequence:

```
1. READ GOAL
   |
   v
2. DECIDE TOOL ORDER
   |  (which tools, what sequence, what parallelism)
   v
3. APPLY ARGS
   |  (load YAML/JSON config to parameterize tool calls)
   v
4. REFERENCE CONTEXT
   |  (tone rules, language registry, compliance catalogs)
   v
5. USE HARD PROMPTS
   |  (reusable instruction templates for LLM-dependent steps)
   v
6. EXECUTE TOOLS (deterministic)
   |
   v
7. HANDLE ERRORS
   |  (read error, fix tool, update goal with lessons learned)
   v
8. VERIFY OUTPUT
   (check output format before chaining into next tool)
```

### Error Handling Protocol

When tools fail:
1. Read the error message
2. Fix the tool (deterministic code fix)
3. Update the goal with what was learned (rate limits, batching, timing)
4. Goals are living docs -- update when better approaches emerge

### Guardrails

- Always check `tools/manifest.md` before writing a new script
- Verify tool output format before chaining into another tool
- Do not assume APIs support batch operations -- check first
- When a workflow fails mid-execution, preserve intermediate outputs before retrying
- Read the full goal before starting a task -- do not skim
- When adding an append-only/immutable DB table, always add it to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- When adding a new dashboard page route, always add it to the `Pages:` line in `.claude/commands/start.md`

---

## Session Start Protocol

Every session begins with context loading to maintain continuity:

```
Session Start
    |
    v
1. Read memory/MEMORY.md (long-term context)
    |
    v
2. Read today's daily log (memory/logs/YYYY-MM-DD.md)
    |
    v
3. Read yesterday's log (continuity)
    |
    v
4. Or run: python tools/memory/memory_read.py --format markdown
    |
    v
5. Load project context:
   python tools/project/session_context_builder.py --format markdown
    |
    v
Ready to work.
```

If `memory/MEMORY.md` does not exist, this is a fresh environment. Run `/initialize` to set up all directories, manifests, memory files, and databases.

---

## Memory System Architecture

ICDEV uses dual storage for memory: markdown files (human-readable) and SQLite databases (searchable).

```
+------------------+          +------------------+
|   Markdown       |          |   SQLite         |
|                  |          |                  |
| MEMORY.md        |          | data/memory.db   |
|  (curated facts) |          |  memory_entries  |
|                  |          |  daily_logs      |
| logs/YYYY-MM.md  |          |  memory_access   |
|  (daily events)  |          |  _log            |
+------------------+          +------------------+
        |                             |
        +----------+------------------+
                   |
           +-------v--------+
           | Hybrid Search  |
           | 0.7 * BM25     |
           | + 0.3 * Semantic|
           +----------------+
```

### Memory Types

| Type | Description | Half-Life (Time Decay) |
|------|-------------|------------------------|
| `fact` | Verified information | 90 days |
| `preference` | User/project preferences | 180 days |
| `event` | Session events, actions taken | 7 days |
| `insight` | Derived conclusions | 30 days |
| `task` | Work items and status | 14 days |
| `relationship` | Entity connections | 120 days |

### Search Ranking

Hybrid search combines keyword matching with vector similarity:

```
score = 0.7 * BM25_score + 0.3 * semantic_score
```

Configurable via `--bm25-weight` and `--semantic-weight` flags.

**Time-decay scoring** (opt-in via `--time-decay`):
```
decay = 2^(-(age_days / half_life))
final_score = 0.60 * relevance + 0.25 * recency_decay + 0.15 * importance
```

### Embeddings

- Model: OpenAI `text-embedding-3-small` (1536 dimensions)
- Storage: BLOBs in SQLite `memory_entries` table
- Air-gapped alternative: Ollama `nomic-embed-text`
- Generation: `python tools/memory/embed_memory.py --all`

### Memory Commands

```bash
# Read all memory
python tools/memory/memory_read.py --format markdown

# Write to daily log + DB
python tools/memory/memory_write.py --content "text" --type event

# Store a fact with importance
python tools/memory/memory_write.py --content "text" --type fact --importance 7

# Update MEMORY.md section
python tools/memory/memory_write.py --update-memory --content "text" --section user_preferences

# Search
python tools/memory/memory_db.py --action search --query "keyword"
python tools/memory/semantic_search.py --query "concept"
python tools/memory/hybrid_search.py --query "query"
python tools/memory/hybrid_search.py --query "query" --time-decay
```

---

## ATLAS / M-ATLAS Workflow

ATLAS is the build workflow for constructing applications. M-ATLAS adds a "Model" pre-phase for MBSE integration.

```
M-ATLAS Workflow (6 phases):

  [Model]  -->  Architect  -->  Trace  -->  Link  -->  Assemble  -->  Stress-test
     |             |             |           |            |              |
     v             v             v           v            v              v
  Parse SysML   System       Map reqs    Connect      Generate      Run full
  / DOORS NG    design,      to NIST     digital      code via      test suite:
  models.       component    800-53      thread:      TDD (RED ->   unit, BDD,
  (Skipped if   decomp,     controls.    model ->     GREEN ->      E2E, SAST,
  no model)     API design.              code ->      REFACTOR)     compliance
                                         test ->                    gates.
                                         control.

ATLAS Workflow (5 phases -- no Model step):

  Architect  -->  Trace  -->  Link  -->  Assemble  -->  Stress-test
```

### Phase Details

| Phase | Purpose | Key Tools |
|-------|---------|-----------|
| **Model** (M-ATLAS only) | Parse SysML XMI, import DOORS ReqIF | `xmi_parser.py`, `reqif_parser.py` |
| **Architect** | System design, component decomposition | `app_blueprint.py`, architecture decisions |
| **Trace** | Map requirements to NIST 800-53 controls | `control_mapper.py`, `crosswalk_engine.py` |
| **Link** | Build digital thread: model-code-test-control | `digital_thread.py`, `traceability_builder.py` |
| **Assemble** | Generate code via TDD cycle | `test_writer.py`, `code_generator.py` |
| **Stress-test** | Full validation pipeline | `test_orchestrator.py`, security gates |

---

## TDD Workflow

All code generation follows a strict RED-GREEN-REFACTOR cycle:

```
+----------+        +----------+        +------------+
|   RED    | -----> |  GREEN   | -----> |  REFACTOR  |
| Write a  |        | Write    |        | Improve    |
| failing  |        | minimal  |        | code       |
| test     |        | code to  |        | quality    |
| first.   |        | pass the |        | without    |
|          |        | test.    |        | changing   |
+----------+        +----------+        | behavior.  |
     ^                                  +-----+------+
     |                                        |
     +----------------------------------------+
              (next requirement)
```

**Supported Languages (6 First-Class)**:

| Language | Test Framework | BDD Framework | SAST | Dependency Audit |
|----------|---------------|---------------|------|------------------|
| Python | pytest | behave | bandit | pip-audit |
| Java | JUnit | Cucumber-JVM | SpotBugs | OWASP DC |
| JavaScript/TS | Jest/Vitest | cucumber-js | eslint-security | npm audit |
| Go | go test | godog | gosec | govulncheck |
| Rust | cargo test | cucumber-rs | cargo-audit | cargo-audit |
| C# | xUnit | SpecFlow | SecurityCodeScan | dotnet list |

---

## 12 Leverage Points of Agentic Development

These are the 12 dimensions available for tuning agent behavior, organized into two categories.

### In Agent (Core Four)

These directly configure what the agent knows, thinks with, and can do.

```
+----------------------------------------------------------+
|                   IN AGENT (Core Four)                   |
|                                                          |
|  1. CONTEXT          What agents know                    |
|     CLAUDE.md, goals/, context/ files                    |
|                                                          |
|  2. MODEL            Which LLM                           |
|     Bedrock Claude Opus/Sonnet via agent_config.yaml     |
|                                                          |
|  3. PROMPT           How to phrase                       |
|     hardprompts/ templates                               |
|                                                          |
|  4. TOOLS            What agents can do                  |
|     tools/ deterministic Python scripts                  |
+----------------------------------------------------------+
```

### Through Agent (Multipliers)

These shape the environment that amplifies agent effectiveness.

```
+----------------------------------------------------------+
|              THROUGH AGENT (Multipliers)                  |
|                                                          |
|  5.  Standard Output    Structured JSON (--json flag)    |
|  6.  Types              dataclasses, Pydantic, schemas   |
|  7.  Docs               CLAUDE.md, SKILL.md, goals/     |
|  8.  Tests              pytest + behave + Playwright +   |
|                         security/compliance gates        |
|  9.  Architecture       GOTCHA layers, ATLAS workflow    |
|  10. Plans              specs/, plan files, goals/       |
|  11. Templates          context/ JSON, hardprompts/,     |
|                         args/ YAML                       |
|  12. Workflows          goals/, CI/CD pipelines,         |
|                         GitLab task routing               |
+----------------------------------------------------------+
```

### Leverage Point Interactions

```
Context (1) + Plans (10) + Templates (11)
    --> The agent knows WHAT to build and HOW

Model (2) + Prompt (3) + Docs (7)
    --> The agent reasons correctly

Tools (4) + Standard Output (5) + Types (6)
    --> Execution is deterministic and chainable

Tests (8) + Architecture (9) + Workflows (12)
    --> Quality is enforced end-to-end
```

---

## Testing Pipeline

The 9-step validation pipeline ensures code quality and compliance:

```
Step 1: py_compile          Python syntax validation
Step 2: Ruff                Ultra-fast linting (Rust-based)
Step 3: pytest              Unit/integration tests + coverage
Step 4: behave/Gherkin      BDD scenario tests
Step 5: Bandit              SAST security scan
Step 6: Playwright MCP      Browser automation E2E
Step 7: Vision validation   LLM-based screenshot analysis
Step 8: Acceptance V&V      Deterministic criteria verification
Step 9: Security gates      CUI markings, STIG, secrets
```

---

## Key Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| D1: SQLite for ICDEV internals | Zero-config portability; PostgreSQL for apps ICDEV builds |
| D2: Stdio for MCP, HTTPS+mTLS for A2A | Claude Code compatibility; K8s inter-agent security |
| D3: Flask over FastAPI | Simpler, fewer deps, auditable SSR, smaller STIG surface |
| D6: Append-only audit trail | No UPDATE/DELETE -- NIST 800-53 AU compliance |
| D66: Provider abstraction pattern | ABC + implementations; vendor logic isolated per provider |
| D116: BaseAssessor ABC pattern | ~60 LOC per new framework vs ~400+ without |

---

## Quick Reference Commands

```bash
# Initialize framework (first run)
/initialize

# Memory system
python tools/memory/memory_read.py --format markdown
python tools/memory/hybrid_search.py --query "query"

# Session context
python tools/project/session_context_builder.py --format markdown

# Run tests
pytest tests/ -v --tb=short

# Health check
python tools/testing/health_check.py --json

# Production audit
python tools/testing/production_audit.py --human --stream
```
