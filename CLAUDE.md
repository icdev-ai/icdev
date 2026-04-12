# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Quick Reference

### Essential Commands
```bash
# Initialize framework (first run)
/initialize

# Session start
python tools/memory/memory_read.py --format markdown
python tools/project/session_context_builder.py --format markdown

# Memory
python tools/memory/memory_write.py --content "text" --type event
python tools/memory/hybrid_search.py --query "query"

# LLM Provider
python -c "from tools.llm.router import LLMRouter; r = LLMRouter(); print(r.get_provider_for_function('code_generation'))"
# Config: args/llm_config.yaml — providers, models, routing, embeddings

# Database
python tools/db/init_icdev_db.py
python tools/db/storage.py --health --json

# Testing
python tools/testing/health_check.py --json
python tools/testing/test_orchestrator.py --project-dir /path/to/project
python tools/testing/e2e_runner.py --run-all

# Companion sync (ALWAYS after code changes)
python tools/dx/companion.py --sync --write --json

# Coherence check
python tools/workflow/coherence_checker.py --all --fix --gate

# Internal Awareness Engine (Phase 1-6, D-AWARE)
python tools/awareness/component_indexer.py --scan --json        # Refresh kg-icdev-self-awareness nodes
python tools/awareness/health_prober.py --run-all --json         # Probe routes, imports, coherence
python tools/awareness/drift_detector.py --detect --json         # Detect regressions vs baseline
python tools/awareness/gap_detector.py --detect --json           # Surface structural gaps
python tools/awareness/suggested_card_writer.py --write --json   # Promote predictions to kanban
python -c "from tools.genesis.reflexes.awareness import run; run({}, None)"  # Full 5-phase cycle
# UI: http://localhost:5050/components-map (visual map) + /ask-icdev (Q&A chat)
# Config: args/awareness_config.yaml — 3h cadence, 7 gap rules, 0.7 threshold
```

### Python Dependencies
See `requirements.txt`. Key: sqlite3, pathlib, json (stdlib); openai, anthropic, python-dotenv (optional); pyyaml, jinja2, flask, pytest (ICDEV™).

> **Full command reference:** See [docs/reference/commands.md](docs/reference/commands.md) for all CLI commands across every ICDEV™ module.

---

## Architecture: FORGE Framework

6-layer agentic system. AI orchestrates; tools execute deterministically.

| Layer | Directory | Role |
|-------|-----------|------|
| **Goals** | `goals/` | Process definitions — what to achieve, which tools, expected outputs |
| **Orchestration** | *(you)* | Read goal → decide tool order → apply args → handle errors |
| **Tools** | `tools/` | Python scripts, one job each. Deterministic. Don't think, just execute. |
| **Args** | `args/` | YAML/JSON behavior settings. Change behavior without editing goals/tools |
| **Context** | `context/` | Static reference material (tone rules, writing samples, case studies) |
| **Hard Prompts** | `hardprompts/` | Reusable LLM instruction templates |

**Why:** LLMs are probabilistic. Business logic must be deterministic. 90% accuracy/step = ~59% over 5 steps.

### Key Files
- `goals/manifest.md` — Index of all goal workflows. Check before starting any task.
- `tools/manifest.md` — Master list of all tools. Check before writing a new script.
- `memory/MEMORY.md` — Curated long-term facts/preferences.
- `.env` — API keys, LLM model names. **Admins configure LLM here, not in code.**
- `.tmp/` — Disposable scratch work. Never store important data here.

---

## How to Operate

1. **Check goals first** — Read `goals/manifest.md` before starting a task. If a goal exists, follow it.
2. **Check tools first** — Read `tools/manifest.md` before writing new code. If you create a new tool, add it to the manifest.
3. **When tools fail** — Read the error, fix the tool, update the goal with what you learned.
4. **Goals are living docs** — Update when better approaches emerge. Never modify/create without permission.
5. **When stuck** — Explain what's missing. Don't guess or invent capabilities.

### Session Start Protocol
1. Read `memory/MEMORY.md` for long-term context
2. Read today's daily log (`memory/logs/YYYY-MM-DD.md`)
3. Read yesterday's log for continuity
4. Or run: `python tools/memory/memory_read.py --format markdown`
5. Load project context: `python tools/project/session_context_builder.py --format markdown`

### First Run
If `memory/MEMORY.md` doesn't exist, this is a fresh environment. Run `/initialize`.

---

## Running ICDEV Outside Claude Code

ICDEV™ is fully operable without the Claude Code CLI. Use `tools/airgap/` as the runtime shim — it replicates hooks, session management, and safety gates as plain Python.

### Quick Start

```bash
# Detect environment (cloud vs air-gap)
python -m tools.airgap --detect --json

# Activate local-only LLM routing (air-gap mode)
python -m tools.airgap --activate

# Health check before any risky operation
python tools/testing/health_check.py --json
```

### Cron Job Setup

```bash
# /etc/cron.d/icdev-audit — nightly compliance scan
0 2 * * * icdev-user cd /opt/icdev && \
  python -c "
from tools.airgap.hook_compat import get_session_id, run_auto_commit
get_session_id()   # sets CLAUDE_SESSION_ID + ICDEV_SESSION_ID for audit trail
# ... invoke tools here (health_check, bandit, etc.) ...
run_auto_commit('chore: nightly audit auto-commit')
" >> /var/log/icdev/cron.log 2>&1
```

### CI/CD Pipeline (GitLab Stage End)

```yaml
# .gitlab-ci.yml — security gate + auto-commit at stage end
security-scan:
  stage: validate
  script:
    - export ICDEV_AUTO_COMMIT=true
    - python tools/testing/health_check.py --json
    - python -m bandit -r tools/ --severity-level medium
    - python -c "from tools.airgap.hook_compat import run_pre_tool_check; \
        r = run_pre_tool_check('Bash', {'command': 'git push'}); \
        exit(0 if r['allowed'] else 1)"
    - python -c "from tools.airgap.hook_compat import run_auto_commit; \
        run_auto_commit('ci: post-scan auto-commit')"
```

### Headless ANVIL Workflow

> **Requires OPT-42** (`tools/anvil/*.py` — not yet merged)

```bash
python tools/anvil/run_workflow.py --goal goals/build_app.md --headless --json
```

### Skill Invocation (Headless)

> **Requires OPT-41** (`tools/skills/invoke.py` — not yet merged)

```bash
python tools/skills/invoke.py --skill icdev-secure --args "--scan tools/ --json"
```

### Air-Gap LLM Routing (Ollama-only)

```bash
# .env — forces all routing through local Ollama, no cloud fallback
OLLAMA_BASE_URL=http://localhost:11434
ICDEV_LLM_PROVIDER=ollama
# Also set in args/llm_config.yaml: two_tier.enabled: false
```

```python
# Programmatic activation
from tools.airgap import is_airgap, activate_airgap
if is_airgap():
    activate_airgap()   # patches llm_config.yaml routing to local-only
```

### Validation in Air-Gap Mode

```bash
python tools/testing/health_check.py --json                            # env + DB + deps
python tools/testing/e2e_runner.py --run-all --mode native --json      # UI lifecycle tests
python -m bandit -r tools/ --severity-level medium                     # security scan
python tools/workflow/coherence_checker.py --all --gate                # coherence gate
```

> **Long-form reference:** [docs/ops/airgap-runbook.md](docs/ops/airgap-runbook.md)

---

## Guardrails

- Always check `tools/manifest.md` before writing a new script
- Verify tool output format before chaining into another tool
- Don't assume APIs support batch operations — check first
- When a workflow fails mid-execution, preserve intermediate outputs before retrying
- Read the full goal before starting a task — don't skim
- **NEVER DELETE YOUTUBE VIDEOS** — Irreversible.
- When adding an append-only/immutable DB table, ALWAYS add it to `APPEND_ONLY_TABLES` in `.claude/hooks/pre_tool_use.py`
- When adding a new dashboard page route, ALWAYS add it to the `Pages:` line in `.claude/commands/start.md`
- Screenshots: ALWAYS use `playwright/screenshots/<name>.png` as the filename
- In Jinja2 templates, NEVER use `'%%.0f'|format(value)` — use `value|round(0)|int`
- In Behave step definitions, match step text to tool return signatures
- SQL CHECK constraints: derive from Python constants, never hardcode
- Entity types: add to BOTH the Python constant AND the SQL CHECK constraint
- Child apps: ALWAYS use `child_app_generator.py` + `forge_validator.py --gate`
- Before writing tests: ALWAYS run `api_surface_extractor.py --file <module> --json`
- **Cross-platform:** pathlib.Path, `encoding='utf-8'`, `tempfile.gettempdir()`, `datetime.now(timezone.utc)`, `hashlib.sha256` not md5
- **LLM config via `.env`**, never hardcode model IDs in Python
- **New tool/module registration checklist (8 points):**
  1. `tools/manifest.md` — add tool entry
  2. `CLAUDE.md` — add CLI commands to [docs/reference/commands.md](docs/reference/commands.md)
  3. `args/security_gates.yaml` — add gate if blocking/warning conditions
  4. `tools/mcp/tool_registry.py` + `gap_handlers.py` — register in MCP gateway
  5. `.claude/hooks/pre_tool_use.py` — add append-only tables
  6. `tests/conftest.py` — add new table schemas to MINIMAL_ICDEV_SCHEMA
  7. `python tools/dx/companion.py --sync --write --json` — sync to all AI platforms
  8. `python tools/workflow/coherence_checker.py --all --fix --gate` — coherence validation

---

## ICDEV™ System — Intelligent Certified Development

Meta-builder that autonomously builds Gov/DoD applications using FORGE + ANVIL workflow. Full SDLC with TDD/BDD, NIST 800-53 RMF compliance, and self-healing.

### Environment
- **Classification:** CUI // SP-CTI (IL4/IL5), SECRET (IL6)
- **Impact Levels:** IL2 (Public), IL4 (CUI/GovCloud), IL5 (CUI/Dedicated), IL6 (SECRET/SIPR)
- **Cloud:** Multi-cloud — AWS GovCloud, Azure Government, GCP, OCI, IBM, Local
- **LLM:** Multi-cloud — Bedrock, Azure OpenAI, Vertex AI, OCI GenAI, watsonx.ai, Ollama
- **Languages:** Python, Java, JavaScript/TS, Go, Rust, C# (6 first-class)
- **CI/CD:** GitLab | **Orchestration:** K8s/OpenShift | **IaC:** Terraform + Ansible

### Multi-Agent Architecture
15 agents across 3 tiers (Core, Domain, Support) on ports 8443-8458. See [docs/reference/architecture.md](docs/reference/architecture.md).

### Memory System
Dual storage: markdown (human-readable) + SQLite (searchable).
- `data/memory.db` — entries, daily logs, access log
- `data/activity.db` — task tracking
- Types: fact, preference, event, insight, task, relationship
- Search: hybrid_search.py (0.7 BM25 + 0.3 semantic)

### Self-Healing
- **≥ 0.7** confidence → auto-remediate (max 5/hour)
- **0.3–0.7** → suggest fix, require approval
- **< 0.3** → escalate with full context

### Databases
| Database | Purpose |
|----------|---------|
| `data/icdev.db` | Main operational DB (391 tables) |
| `data/platform.db` | SaaS platform DB |
| `data/tenants/{slug}.db` | Per-tenant isolated DB |
| `data/memory.db` | Memory system |
| `data/activity.db` | Task tracking |

Audit trail is **append-only/immutable** (NIST AU). Full schema: [docs/reference/databases.md](docs/reference/databases.md).

---

## ICDEV™ Guardrails

- All artifacts MUST include classification markings (CUI for IL4/IL5, SECRET for IL6)
- Use `classification_manager.py` for markings — don't hard-code CUI banners
- Audit trail is append-only — NEVER UPDATE/DELETE audit tables
- Security gates block on: CAT1 STIG, critical/high vulns, failed tests, missing markings
- When implementing NIST 800-53 control, call crosswalk engine for FedRAMP/CMMC auto-populate
- Self-healing limited to confidence ≥ 0.7 and max 5/hour
- All A2A uses mutual TLS; never store secrets in code
- SBOM regenerated on every build; containers non-root, read-only rootfs
- IL6/SECRET: SIPR-only, NSA Type 1 encryption, air-gapped CI/CD
- **V&V before handoff** — if change affects UI, verify with Playwright MCP before reporting
- **Playwright E2E after dashboard changes** — mandatory post-implementation verification
- **Feature docs** — create `docs/features/phase-{N}-{slug}.md` after each phase
- **Sandbox coverage (OPT-58)** — any new `tools/` module that ingests user-provided content MUST land a decision in [docs/security/sandbox-coverage.md](docs/security/sandbox-coverage.md) (sandboxed / trusted-first-party / sandboxed-on-demand / bypass-documented). Canvas templates are first-party; canvas design JSON is data only. `.tmp/*.py` scripts are dev-scratch only — productize under `tools/` before merge. Enforced by `coherence_checker.py:check_sandbox_coverage`.

---

## Security Gates (Summary)

Gates block on critical conditions. Full definitions: [docs/reference/compliance-security.md](docs/reference/compliance-security.md).

Key gates: Code Review, Merge, Deploy, FedRAMP, CMMC, cATO, DES, Migration, RICOAS, Supply Chain, FIPS 199/200, Marketplace, Multi-Regime, DevSecOps, ZTA, MOSA, AI Security, RAG, Fine-Tuning, Coherence, Acceptance Validation.

---

## Reference Documentation

Detailed reference material (read on-demand, not loaded automatically):

| File | Contents |
|------|----------|
| [commands.md](docs/reference/commands.md) | All CLI commands for every ICDEV™ module |
| [architecture.md](docs/reference/architecture.md) | Agents, MCP servers, languages, skills, deployment, scaling, installation |
| [adrs.md](docs/reference/adrs.md) | All architecture decision records (D1–D360+) |
| [compliance-security.md](docs/reference/compliance-security.md) | Compliance frameworks, crosswalk, security gates, args config |
| [subsystems.md](docs/reference/subsystems.md) | Innovation, Creative, Research engines; RICOAS; SaaS; Marketplace; CI/CD |
| [goals.md](docs/reference/goals.md) | All existing goal workflows with descriptions |
| [testing.md](docs/reference/testing.md) | Testing framework, test commands, E2E specs |
| [databases.md](docs/reference/databases.md) | Database tables, schemas, migration commands |
| [ops/airgap-runbook.md](docs/ops/airgap-runbook.md) | Running ICDEV™ outside Claude Code — cron, CI/CD, air-gap LLM, headless ANVIL |

---

## Continuous Improvement

Every failure strengthens the system: identify what broke → fix the tool → test it → update the goal → next run succeeds automatically.

Be direct. Be reliable. Get shit done.
