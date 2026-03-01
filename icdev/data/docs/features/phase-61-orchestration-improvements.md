# Phase 61 — Multi-Agent Orchestration Improvements

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 61 |
| Title | Multi-Agent Orchestration Improvements |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 44 (Innovation Adaptation), Phase 46 (Observability & XAI) |
| Author | ICDEV Architect Agent |
| Date | 2026-03-01 |

---

## 1. Problem Statement

ICDEV's multi-agent architecture (15 agents, 3 tiers) handles task decomposition, parallel execution, and domain authority — but several orchestration gaps remain:

1. **Orchestrator boundary violation** — Nothing prevents the Orchestrator agent from directly executing tools like `scaffold` or `code_generation`, violating the GOTCHA principle that orchestration and execution must be separated. When the Orchestrator bypasses delegation, it introduces probabilistic behavior where deterministic tool execution is required.

2. **No declarative prompt chaining** — Multi-step LLM reasoning (e.g., plan → critique → refine) requires ad-hoc Python code. Adding a new reasoning chain means writing new code rather than declaring steps in YAML.

3. **No adversarial plan review** — ATLAS workflow moves from Assemble directly to Stress-test with no structured review phase. Critical architecture flaws, compliance gaps, and security vulnerabilities are caught late (during stress-testing) rather than early (during review).

4. **No session intent tracking** — Agent sessions lack declared purpose, making NIST AU-3 audit traceability difficult. Post-incident forensics cannot determine what an agent session was authorized to do.

5. **No async result delivery** — When an agent completes a long-running task, results sit in the mailbox until the recipient polls. There is no priority mechanism to inject completed results into the next agent turn.

6. **No file access control** — All agents can read, write, and delete any file. Sensitive files (`.env`, `*.pem`, `*.tfstate`) have no protection beyond developer discipline.

7. **No orchestration visibility** — The dashboard shows individual agents and projects but provides no real-time view of workflow execution, task DAGs, mailbox activity, or agent collaboration.

Phase 61 closes these gaps with 7 features that strengthen orchestration boundaries, add structured reasoning, and provide real-time operational visibility.

---

## 2. Goals

1. Enforce dispatcher-only mode on the Orchestrator agent — delegate only, never execute tools directly (D-DISP-1)
2. Enable declarative YAML-driven prompt chains for sequential LLM-to-LLM reasoning (D-PC-1/2/3)
3. Add an ATLAS adversarial critique phase between Assemble and Stress-test with multi-agent plan review (D36, D6)
4. Track session purpose declarations for NIST AU-3 audit traceability (D-ORCH-5)
5. Deliver async task results via high-priority mailbox injection (D-ORCH-7)
6. Enforce tiered file access control: zero_access, read_only, no_delete (D-ORCH-8)
7. Provide a real-time orchestration dashboard with agent grid, workflow DAG, mailbox SSE stream, and collaboration history

---

## 3. Architecture

```
                    Multi-Agent Orchestration Improvements
    ┌─────────────────────────────────────────────────────────────────┐
    │                                                                 │
    │   agent_config.yaml    prompt_chains.yaml    atlas_critique_    │
    │   (dispatcher mode)    (chain definitions)   config.yaml        │
    │                                                                 │
    │   file_access_tiers.yaml                                        │
    │   (zero_access / read_only / no_delete patterns)                │
    └───────────┬───────────┬──────────┬──────────┬──────────────────┘
                │           │          │          │
    ┌───────────▼──┐  ┌─────▼──────┐ ┌▼────────┐ ┌▼──────────────┐
    │  Dispatcher   │  │  Prompt    │ │ ATLAS   │ │  Session      │
    │  Mode         │  │  Chain     │ │ Critique│ │  Purpose      │
    │               │  │  Executor  │ │         │ │               │
    │  Blocks direct│  │  YAML →    │ │ Parallel│ │  Declare →    │
    │  tool calls   │  │  sequential│ │ critics │ │  Track →      │
    │  on orch.     │  │  LLM steps │ │ → GO/   │ │  Complete     │
    │               │  │            │ │  NOGO   │ │               │
    └───────┬──────┘  └─────┬──────┘ └┬────────┘ └┬──────────────┘
            │               │         │           │
            ▼               ▼         ▼           ▼
    dispatcher_mode_    prompt_chain_  atlas_critique_  session_purposes
    overrides           executions     sessions +       (audit trail)
    (per-project)       (append-only)  findings
                                       (append-only)
            │               │         │           │
            └───────────────┴─────┬───┴───────────┘
                                  │
                    ┌─────────────▼─────────────────┐
                    │   Orchestration Dashboard      │
                    │                                │
                    │  /orchestration                 │
                    │  /api/orchestration/*           │
                    │                                │
                    │  Stat Grid (8 cards)            │
                    │  Agent Grid (15 agents)         │
                    │  Workflow DAG (SVG)             │
                    │  Mailbox Feed (SSE stream)      │
                    │  Collaboration History          │
                    │  Prompt Chains / ATLAS Critiques│
                    └────────────────────────────────┘

    ┌──────────────────────┐     ┌──────────────────────┐
    │  Async Result        │     │  Tiered File Access   │
    │  Injection           │     │  Control              │
    │                      │     │                       │
    │  priority=9 mailbox  │     │  pre_tool_use.py hook │
    │  → inject next turn  │     │  zero_access (block)  │
    │                      │     │  read_only (no write) │
    │  agent_mailbox table │     │  no_delete (no rm)    │
    └──────────────────────┘     └───────────────────────┘
```

### Key Design Principles

- **GOTCHA enforcement** — Dispatcher mode ensures the Orchestrator delegates to domain agents, never executes tools directly (D-DISP-1)
- **Declarative configuration** — Prompt chains, critique roles, consensus rules, and file tiers are all YAML-driven; add new chains/critics/tiers without code changes (D26 pattern)
- **Append-only audit** — `atlas_critique_findings` and `prompt_chain_executions` are immutable (D6, NIST AU compliance)
- **Backward compatible** — All features default to disabled/optional; existing workflows unchanged without opt-in
- **Air-gap safe** — All implementations use Python stdlib only (D22 pattern); no external dependencies

---

## 4. Implementation

### Feature 1: Dispatcher-Only Mode (`tools/agent/dispatcher_mode.py`)

Enforces the GOTCHA orchestration principle: the Orchestrator agent delegates tasks but never executes tools directly.

**Allowed Tools (when enabled):**
- `task_dispatch` — delegate work to domain agents
- `agent_status` — check agent health
- `agent_mailbox` — read/send messages
- `workflow_status` — check workflow progress
- `prompt_chain_execute` — invoke prompt chains

**Blocked:** All direct tool execution (`scaffold`, `code_generation`, `compliance_export`, `sast_scan`, etc.)

**Per-Project Overrides:** The `dispatcher_mode_overrides` table allows per-project customization of allowed/blocked tool lists without changing the global config.

**Config:** `args/agent_config.yaml` → `agents.orchestrator.dispatcher_mode.enabled` (default: `false`)

### Feature 2: Prompt Chain Executor (`tools/agent/prompt_chain_executor.py`)

YAML-driven sequential LLM-to-LLM reasoning chains (D-PC-1/2/3).

**Chain Definition Format:**
```yaml
chains:
  plan_critique_refine:
    description: "Architect plans → Compliance reviews → Security reviews → Refined plan"
    steps:
      - id: plan
        agent: architect
        prompt: "Create an implementation plan for: $INPUT"
      - id: compliance_review
        agent: compliance
        prompt: "Review this plan for compliance gaps: $STEP{plan}"
      - id: security_review
        agent: security
        prompt: "Review for security vulnerabilities: $STEP{plan}"
      - id: refine
        agent: architect
        prompt: "Refine the plan based on feedback: $STEP{compliance_review} $STEP{security_review}"
```

**Variable Substitution:**
| Variable | Resolves To |
|----------|-------------|
| `$INPUT` | Original user input |
| `$ORIGINAL` | Same as `$INPUT` |
| `$STEP{step_id}` | Output from a previous step |

**Agent-to-Function Mapping:** Each agent maps to an LLM router function for proper model selection:
| Agent | Router Function |
|-------|----------------|
| orchestrator | task_decomposition |
| architect | agent_architect |
| builder | code_generation |
| compliance | compliance_export |
| security | code_review |

**Execution Model:** Sequential only (D-PC-3). Parallelism is handled at the subtask level by `team_orchestrator.py`, not at the prompt chain level.

**Config:** `args/prompt_chains.yaml`

**Sample Chains:**
- `plan_critique_refine` — 4-step: architect plan → compliance review → security review → refined plan
- `scout_analyze_recommend` — 3-step: knowledge scout → architect analyze → builder recommend
- `security_review_chain` — 4-step: threat model → vuln scan → arch review → final assessment

### Feature 3: ATLAS Adversarial Critique (`tools/agent/atlas_critique.py`)

Multi-agent adversarial plan review inserted between the Assemble and Stress-test phases of M-ATLAS workflow.

**Critic Agents (configurable):**
| Critic | Focus Areas |
|--------|-------------|
| security-agent | security_vulnerability, data_handling_issue, deployment_risk |
| compliance-agent | compliance_gap, data_handling_issue |
| knowledge-agent | architecture_flaw, performance_risk, maintainability_concern, testing_gap |

**Finding Types (8):** security_vulnerability, compliance_gap, architecture_flaw, performance_risk, maintainability_concern, testing_gap, deployment_risk, data_handling_issue

**Severity Levels:** critical, high, medium, low

**Consensus Rules:**
| Decision | Condition |
|----------|-----------|
| **GO** | 0 critical findings AND 0 high findings |
| **CONDITIONAL** | 0 critical findings (high findings present — must revise) |
| **NOGO** | Any critical finding |

**Execution:** Critics run in parallel via `ThreadPoolExecutor` (D36). Each critic receives the phase output and returns structured findings. The consensus engine aggregates findings and renders a decision.

**Session Statuses:** `in_progress` → `go` | `nogo` | `conditional` → `revised` | `failed`

**Config:** `args/atlas_critique_config.yaml`

### Feature 4: Session Purpose Declaration (`tools/agent/session_purpose.py`)

Tracks session intent for NIST AU-3 audit traceability (D-ORCH-5).

**API:**
| Function | Purpose |
|----------|---------|
| `declare()` | Create a new session purpose with hash |
| `get_active()` | Retrieve current active purpose |
| `history()` | List all purposes for a project |
| `complete()` | Mark purpose completed |
| `abandon()` | Mark purpose abandoned |
| `get_prompt_injection()` | Retrieve purpose text for LLM system prompt injection |

**Scope Types:** `session`, `workflow`, `task`

**Integrity:** Each purpose is SHA-256 hashed (first 16 hex chars stored as `purpose_hash`) for tamper detection.

**LLM Integration:** Active session purpose is injectable into agent system prompts as a guardrail — agents can reference their declared purpose for decision-making boundaries.

### Feature 5: Async Result Injection (`tools/agent/mailbox.py`)

High-priority result delivery for completed async agent tasks (D-ORCH-7).

**Priority Level:** 9 (`PRIORITY_INJECT_NEXT_TURN`) — highest mailbox priority

**Functions:**
- `send_async_result()` — Send a high-priority `async_result` message
- `collect_pending_injections()` — Collect all unread async results, mark as read

**Flow:**
1. Builder completes long-running code generation
2. Builder calls `send_async_result()` → message stored with `priority=9`, `message_type='async_result'`
3. Orchestrator's next turn calls `collect_pending_injections()` → results injected into prompt context
4. Messages marked as read to prevent duplicate injection

### Feature 6: Tiered File Access Control (`args/file_access_tiers.yaml` + `pre_tool_use.py`)

Defense-in-depth file protection enforced by the pre_tool_use hook (D-ORCH-8).

**Three Tiers:**

| Tier | Behavior | Example Patterns |
|------|----------|-----------------|
| **zero_access** | Complete block (read + write) | `.env`, `*.pem`, `*.key`, `*.tfstate`, `credentials.json`, `.ssh/*` |
| **read_only** | Read allowed, write blocked | `context/compliance/*.json`, `package-lock.json`, `node_modules/**` |
| **no_delete** | Read/write allowed, delete blocked | `CLAUDE.md`, `goals/*.md`, `Dockerfile*`, `requirements.txt`, `.git/**` |

**Pattern Matching:** Glob-style (`fnmatch` semantics)

**Enforcement Points:**
- Read, Edit, Write tools → file path checked against all three tiers
- Bash `rm` commands → dangerous pattern detection (`-rf /`, `-rf ~`)
- Violations block tool invocation (exit code 2)

### Feature 7: Real-Time Orchestration Dashboard

**Route:** `/orchestration`
**API Blueprint:** `/api/orchestration/*`

**Dashboard Sections:**

| Section | Description | Refresh |
|---------|-------------|---------|
| Stat Grid | 8 cards: Active Workflows, Total Agents, Agents Running, Subtasks Pending/Completed/Failed, Mailbox Unread, Avg Response (ms) | 3s |
| Agent Grid | 15 agents with tier badge (Core/Domain/Support), status, active task, elapsed time, tool calls, context % | 3s |
| Workflows | Table with status filter dropdown, duration, progress bar | 10s |
| Workflow DAG | SVG visualization of task dependency graph with status-colored nodes | on-select |
| Mailbox Feed | SSE-streamed messages with from/to agents, type, subject, priority | SSE (3s batches) |
| Collaboration History | Agent collaboration events with type, outcome, duration | 10s |
| Prompt Chains | Execution history with chain name, status, steps completed/total | 15s |
| ATLAS Critiques | Critique sessions with consensus, total findings, critical count | 15s |

**API Endpoints (9 total):**
| Endpoint | Method | Returns |
|----------|--------|---------|
| `/api/orchestration/stats` | GET | Summary stat grid data |
| `/api/orchestration/agents` | GET | All 15 agents with status, active task, token usage, tier |
| `/api/orchestration/workflows` | GET | Active/recent workflows with optional `?status=` filter |
| `/api/orchestration/workflows/<id>/dag` | GET | DAG nodes + edges for SVG rendering |
| `/api/orchestration/mailbox` | GET | Recent mailbox messages |
| `/api/orchestration/mailbox/stream` | GET | SSE stream for real-time mailbox updates (D29) |
| `/api/orchestration/collaboration` | GET | Recent collaboration events between agents |
| `/api/orchestration/chains` | GET | Prompt chain execution history |
| `/api/orchestration/critiques` | GET | ATLAS critique session history |

**Data Sources (read-only against existing tables):**
`agent_workflows`, `agent_subtasks`, `agent_mailbox`, `agents`, `agent_collaboration_history`, `agent_token_usage`, `a2a_tasks`

---

## 5. Database Schema

### New Tables (5)

| Table | Append-Only | Purpose |
|-------|-------------|---------|
| `dispatcher_mode_overrides` | No | Per-project dispatcher mode configuration |
| `prompt_chain_executions` | **Yes** | Chain step execution audit trail |
| `atlas_critique_sessions` | No | Critique session header (status updates allowed) |
| `atlas_critique_findings` | **Yes** | Individual findings per critique session |
| `session_purposes` | No | Session intent declarations (status transitions allowed) |

### Table: `dispatcher_mode_overrides`
```sql
CREATE TABLE dispatcher_mode_overrides (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    custom_dispatch_tools TEXT,  -- JSON array
    custom_blocked_tools TEXT,   -- JSON array
    created_at TEXT DEFAULT (datetime('now')),
    created_by TEXT
);
```

### Table: `prompt_chain_executions`
```sql
CREATE TABLE prompt_chain_executions (
    id TEXT PRIMARY KEY,
    chain_name TEXT NOT NULL,
    step_id TEXT,
    agent TEXT,
    input_hash TEXT,
    output_hash TEXT,
    execution_ms INTEGER,
    status TEXT CHECK(status IN ('pending','running','completed','failed','skipped')),
    error TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Table: `atlas_critique_sessions`
```sql
CREATE TABLE atlas_critique_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    workflow_id TEXT,
    phase_output_hash TEXT,
    initial_status TEXT DEFAULT 'in_progress',
    consensus TEXT CHECK(consensus IN ('go','nogo','conditional')),
    total_findings INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT DEFAULT 'in_progress'
);
```

### Table: `atlas_critique_findings`
```sql
CREATE TABLE atlas_critique_findings (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES atlas_critique_sessions(id),
    critic_agent TEXT NOT NULL,
    round_number INTEGER DEFAULT 1,
    finding_type TEXT NOT NULL,
    severity TEXT CHECK(severity IN ('critical','high','medium','low')),
    title TEXT NOT NULL,
    description TEXT,
    evidence TEXT,
    recommendation TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

### Table: `session_purposes`
```sql
CREATE TABLE session_purposes (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    purpose TEXT NOT NULL,
    purpose_hash TEXT,
    declared_by TEXT,
    scope TEXT CHECK(scope IN ('session','workflow','task')),
    status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','abandoned')),
    metadata TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);
```

---

## 6. Configuration Files

| File | Pattern | Purpose |
|------|---------|---------|
| `args/agent_config.yaml` | Existing config extended | `agents.orchestrator.dispatcher_mode` toggle + dispatch_only_tools list |
| `args/prompt_chains.yaml` | D-PC-1, D26 | YAML chain definitions with variable substitution |
| `args/atlas_critique_config.yaml` | D26 | Critic agent roles, focus areas, consensus thresholds, revision prompt |
| `args/file_access_tiers.yaml` | D-ORCH-8 | Three-tier glob patterns for file protection |

---

## 7. Architecture Decisions

| Decision | Pattern | Rationale |
|----------|---------|-----------|
| **D-DISP-1** | Dispatcher-only mode | Enforces GOTCHA orchestration principle: orchestrator delegates, never executes. Per-project overrides via DB table. |
| **D-PC-1** | YAML-driven prompt chains | Add new reasoning chains without code changes (D26 pattern). Declarative step definitions. |
| **D-PC-2** | LLM routing via LLMRouter | Prompt chains use existing LLM router for function-level model selection, not A2A tool dispatch. |
| **D-PC-3** | Sequential execution only | No DAG parallelism in prompt chains. Parallelism handled by `team_orchestrator.py` at subtask level. |
| **D-ORCH-5** | Session purpose declaration | NIST AU-3 traceability. SHA-256 hashed purpose, injectable into agent system prompts as guardrail. |
| **D-ORCH-7** | Async result injection | High-priority mailbox (priority=9) for completed async tasks. Collector marks read to prevent duplicate injection. |
| **D-ORCH-8** | Tiered file access control | Defense-in-depth: zero_access (block all), read_only (no write), no_delete (no remove). Glob patterns in YAML, enforced by pre_tool_use.py hook. |

---

## 8. Testing

### Unit Tests (173 total)

| Module | Test File | Tests | Key Categories |
|--------|-----------|-------|----------------|
| Dispatcher Mode | `tests/test_dispatcher_mode.py` | 47 | Enable/disable, tool allowlist, project overrides, whitelist/blacklist logic |
| Prompt Chain Executor | `tests/test_prompt_chain_executor.py` | 63 | Chain parsing, variable substitution, agent mapping, sequential execution, timeout, error recovery |
| ATLAS Critique | `tests/test_atlas_critique.py` | 36 | Session creation, parallel critic dispatch, finding classification, consensus voting, revision rounds |
| Session Purpose + Async + File Access | `tests/test_session_purpose.py` | 27 | Declare/complete/abandon, history, prompt injection, async result injection, file tier matching |

### E2E Tests (Playwright)

| Test | Result | Notes |
|------|--------|-------|
| Page rendering | PASS | All 8 stat cards, agent grid, workflows, DAG, mailbox, collaboration, tabs render correctly |
| Navigation | PASS | "Orchestration" link appears in sidebar between Agents and Monitoring |
| API endpoints (9) | PASS | All endpoints return valid JSON with `status: "ok"` |
| SSE stream | PASS (after fix) | Fixed Flask request context error — `request.args.get()` moved outside generator |
| Tab switching | PASS | Prompt Chains / ATLAS Critiques tab toggle works correctly |
| Console errors | PASS | 0 browser console errors |
| CUI banners | PASS | CUI // SP-CTI banners present top and bottom |

### Bug Fixed During E2E

**SSE Mailbox Stream 500 Error:**
- **Root cause:** `request.args.get("since", "")` was inside the generator function `generate()`. Flask's `request` proxy is unavailable inside generators after the view function returns.
- **Fix:** Captured request args before the generator definition:
  ```python
  initial_since = request.args.get("since", "")
  def generate():
      last_id = initial_since  # Use captured value
  ```

---

## 9. Integration Points

| System | Integration |
|--------|-------------|
| **GOTCHA/ATLAS** | Dispatcher mode enforces orchestrator boundary; ATLAS critique inserted between Assemble and Stress-test; prompt chains execute within M-ATLAS workflow |
| **Agent Subsystem** | Async result injection uses existing `agent_mailbox` schema; dispatcher mode integrates with `agent_config.yaml`; file tiers use `pre_tool_use.py` hook |
| **Dashboard** | Real-time orchestration page at `/orchestration`; SSE streaming for mailbox; auto-refresh intervals for all sections |
| **LLM Router** | Prompt chain executor maps agents to router functions for proper model selection; fallback chains respected |
| **Audit Trail** | Session purposes, chain executions, and critique findings all append-only (NIST AU compliance) |

---

## 10. Backward Compatibility

All Phase 61 features are backward compatible:

| Feature | Default | Impact |
|---------|---------|--------|
| Dispatcher Mode | `enabled: false` | Existing orchestrator behavior unchanged |
| Prompt Chains | New optional feature | No impact on existing workflows |
| ATLAS Critique | Optional M-ATLAS phase | Can be disabled in config |
| Session Purpose | Optional context injection | Graceful degradation if table missing |
| Async Result | New mailbox message type | Existing mailbox code ignores unknown types |
| File Access Tiers | Additive hook logic | Existing enforcement continues unchanged |
| Orchestration Dashboard | New route `/orchestration` | No impact on existing dashboard pages |

---

## 11. Commands

```bash
# Dispatcher mode
python tools/agent/dispatcher_mode.py --check --agent-id orchestrator --json
python tools/agent/dispatcher_mode.py --override --project-id "proj-123" --enabled --json

# Prompt chains
python tools/agent/prompt_chain_executor.py --list --json
python tools/agent/prompt_chain_executor.py --execute --chain plan_critique_refine --input "Build auth module" --json

# ATLAS critique
python tools/agent/atlas_critique.py --create --project-id "proj-123" --json
python tools/agent/atlas_critique.py --history --project-id "proj-123" --json

# Session purpose
python tools/agent/session_purpose.py --declare --purpose "Implement auth feature" --project-id "proj-123" --json
python tools/agent/session_purpose.py --active --project-id "proj-123" --json
python tools/agent/session_purpose.py --complete --purpose-id "purpose-xxx" --json
python tools/agent/session_purpose.py --history --project-id "proj-123" --json

# Tests
pytest tests/test_dispatcher_mode.py tests/test_prompt_chain_executor.py tests/test_atlas_critique.py tests/test_session_purpose.py -v
```

---

## 12. Dashboard Pages

| Route | Purpose | Auth |
|-------|---------|------|
| `/orchestration` | Real-time multi-agent orchestration dashboard | All authenticated roles |

**Added to RBAC matrix:** `"orchestration": {"admin", "pm", "developer", "isso", "co"}`

---

## 13. Security Considerations

- **Dispatcher mode** prevents orchestrator privilege escalation — cannot execute compliance/security tools directly
- **File access tiers** protect secrets (`.env`, `*.pem`, `*.tfstate`) from agent read/write/delete
- **Session purpose** provides NIST AU-3 audit context for incident response forensics
- **Append-only tables** (`atlas_critique_findings`, `prompt_chain_executions`) satisfy NIST AU-9 integrity requirements
- **SSE streaming** uses `Cache-Control: no-cache` and `X-Accel-Buffering: no` to prevent proxy caching of sensitive data

---

**CUI // SP-CTI**
