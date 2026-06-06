# [TEMPLATE: CUI // SP-CTI]

# Goal: ACE Co-Worker — Agentic Co-Worker Engine

## Purpose

Assemble dynamic, problem-specific agentic teams from reusable role definitions, execute multi-step co-worker workflows with structured communication primitives, and surface results through both the conversational interface and the `/coworker/` dashboard canvas.

**Why this matters:** Complex GovCon and mission-critical tasks require coordinated effort across multiple specializations — compliance, security, architecture, research, procurement. Manually routing each concern to the right agent wastes time and loses context. ACE Co-Worker dynamically assembles the right team for each problem, delegates work with explicit accountability, verifies outputs before acceptance, and negotiates blockers without human re-engagement — escalating to HITL only when genuinely required.

---

## When to Use

- User sends a complex task in `/chat` that requires more than one domain (e.g., "review this proposal for compliance gaps and security risks")
- A Kanban task card is tagged `team:ace-coworker` or the task description contains a `!coworker` directive
- An external API call arrives at `POST /api/coworker/run` with a `problem` payload
- `/icdev-coworker` slash command is invoked with a problem description
- A Genesis Reflex or Innovation Engine cycle requires multi-domain validation

---

## Prerequisites

- [ ] ICDEV™ system initialized (`python tools/db/init_icdev_db.py`)
- [ ] Oracle lens available (`tools/oracle/classify.py`)
- [ ] Role registry loaded (`args/coworker_roles.yaml`)
- [ ] Co-worker DB schema initialized (`tools/coworker/db/init_db.py`)
- [ ] `memory/MEMORY.md` loaded (session context)
- [ ] A2A agent mesh reachable (at least one domain agent responding)
- [ ] Chat session active OR `/coworker/` canvas route registered

---

## Workflow

### Step 1: Trigger Detection

Detect the trigger source and extract the raw problem statement.

**Trigger sources:**

| Source | Detection Method | Tool |
|--------|-----------------|------|
| Chat | User message in `/chat` with `!coworker` or multi-domain classifier score ≥ 0.75 | `tools/coworker/trigger_detector.py --source chat` |
| Kanban | Task card with `team:ace-coworker` tag or `!coworker` in description | `tools/coworker/trigger_detector.py --source kanban --task-id <id>` |
| API | `POST /api/coworker/run` with `{"problem": "...", "context": {...}}` | Blueprint route in `tools/coworker/blueprint.py` |
| Slash command | `/icdev-coworker "<problem>"` | `.claude/skills/icdev-coworker/SKILL.md` |

**Output:** Trigger manifest JSON stored in `.tmp/coworker/trigger.json`:
```json
{
  "trigger_source": "chat|kanban|api|command",
  "session_id": "<id>",
  "problem_raw": "<raw text>",
  "context": { "project_id": "...", "user_id": "...", "classification": "CUI" }
}
```

**Error handling:**
- Ambiguous trigger (multi-domain score 0.6–0.74): present classification preview to user, ask for confirmation before assembling team
- Missing context fields: use session defaults from `args/coworker_defaults.yaml`

---

### Step 2: Problem Classification — Oracle Lens

Classify the problem across domains to determine which roles are needed and what authority constraints apply.

**Tool:** `python tools/oracle/classify.py --input .tmp/coworker/trigger.json --lens ace-coworker --json`

The Oracle lens applies structured reasoning across 8 classification dimensions:

| Dimension | Output Field | Example Values |
|-----------|-------------|----------------|
| Primary domain | `primary_domain` | compliance, security, architecture, research, procurement, govcon |
| Secondary domains | `secondary_domains[]` | Up to 3 supporting domains |
| Complexity tier | `complexity` | simple (1 role), moderate (2–3 roles), complex (4+ roles) |
| Authority required | `authority_required[]` | security_veto, compliance_gate, legal_review, user_approval |
| IL sensitivity | `il_level` | IL2, IL4, IL5, IL6 |
| Time horizon | `time_horizon` | realtime (<5 min), async (<30 min), batch (<4 hr) |
| Evidence standard | `evidence_standard` | narrative, documented, auditable, certified |
| Escalation threshold | `escalation_threshold` | auto (no HITL), advisory (HITL recommended), mandatory (HITL required) |

**Output:** Classification JSON stored in `.tmp/coworker/classification.json`

**Error handling:**
- Oracle unavailable: fall back to keyword-based domain heuristics in `tools/oracle/fallback_classifier.py`
- IL6 problem with non-SIPR session: block execution, surface error to user with SIPR guidance
- All 8 dimensions scored with confidence ≥ 0.65 required to proceed without user confirmation

**Verify:** Classification JSON present with all 8 dimensions. IL level compatible with session classification.

---

### Step 3: Team Assembly — Role YAMLs

Load role definitions from the registry and assemble the optimal co-worker team for this problem.

**Tool:** `python tools/coworker/team_assembler.py --classification .tmp/coworker/classification.json --registry args/coworker_roles.yaml --json`

**Role registry structure (`args/coworker_roles.yaml`):**

```yaml
roles:
  compliance_reviewer:
    display_name: "Compliance Reviewer"
    domains: [compliance, ato, nist, fedramp, cmmc]
    agent_id: compliance-agent
    port: 8447
    skills: [ato_acceleration, compliance_workflow, nist_crosswalk]
    authority: advisory          # advisory | veto | gate
    max_concurrent: 2

  security_analyst:
    display_name: "Security Analyst"
    domains: [security, stig, cve, threat_model, pentest]
    agent_id: security-agent
    port: 8448
    skills: [security_scan, threat_triage, owasp_agentic]
    authority: veto
    max_concurrent: 1

  architect:
    display_name: "Solutions Architect"
    domains: [architecture, design, mbse, modernization]
    agent_id: architect-agent
    port: 8444
    skills: [mbse_integration, modernization_workflow, mosa_workflow]
    authority: advisory
    max_concurrent: 2

  researcher:
    display_name: "Research Analyst"
    domains: [research, industry, competitive, govcon]
    agent_id: knowledge-agent
    port: 8450
    skills: [industry_research, rag_subsystem, govcon_intelligence]
    authority: advisory
    max_concurrent: 3

  procurement_specialist:
    display_name: "Procurement Specialist"
    domains: [procurement, sam_gov, contract, proposal]
    agent_id: builder-agent
    port: 8445
    skills: [govcon_intelligence, procurement_intel, cpmp_workflow]
    authority: advisory
    max_concurrent: 2

  requirements_analyst:
    display_name: "Requirements Analyst"
    domains: [requirements, ricoas, intake, gap_analysis]
    agent_id: requirements-agent
    port: 8453
    skills: [requirements_intake, boundary_supply_chain, simulation_engine]
    authority: advisory
    max_concurrent: 2
```

**Team assembly rules:**
1. Always include the primary domain role
2. Add secondary domain roles (up to 3 supporting)
3. Add any roles required by `authority_required[]` from classification
4. Deduplicate — do not add the same role twice
5. Cap team size at 5 roles for `moderate` complexity; 7 for `complex`

**Output:** Team manifest JSON stored in `.tmp/coworker/team.json`:
```json
{
  "run_id": "<uuid>",
  "team": [
    { "role": "compliance_reviewer", "agent_id": "compliance-agent", "authority": "advisory" },
    { "role": "security_analyst", "agent_id": "security-agent", "authority": "veto" }
  ],
  "lead_role": "compliance_reviewer",
  "time_horizon": "async",
  "escalation_threshold": "advisory"
}
```

**Error handling:**
- Role agent not reachable: attempt A2A ping; if no response, mark role as `degraded` and fall back to Claude (this session) for that domain
- Team size exceeds 7: prune lowest-priority secondary domains, log pruning decision
- No roles match primary domain: escalate to user with classification summary

**Verify:** At least one role present. Lead role designated. All role agents reachable or degraded status logged.

---

### Step 4: Co-Worker Execution — Step Loop

Execute the co-worker workflow in a structured step loop. Each step delegates to a role, collects output, and applies communication primitives.

**Tool:** `python tools/coworker/executor.py --team .tmp/coworker/team.json --problem .tmp/coworker/trigger.json --json`

**Execution loop (per step):**

```
for step in workflow_steps:
    1. SELECT: pick the role responsible for this step
    2. DELEGATE: send task to role agent via A2A
    3. EXECUTE: role agent runs its skill, returns result
    4. VERIFY: lead role or orchestrator checks result quality
    5. NEGOTIATE: if quality gate fails, attempt resolution (up to 2 retries)
    6. BROADCAST: publish step result to all team members and session
    7. CHECKPOINT: check HITL gate conditions
    8. ADVANCE: move to next step or surface final result
```

**Workflow step templates by complexity:**

| Complexity | Steps |
|-----------|-------|
| simple | [1] analyze → [2] respond |
| moderate | [1] analyze → [2] primary_work → [3] review → [4] respond |
| complex | [1] decompose → [2] parallel_work → [3] cross_review → [4] synthesize → [5] authority_gate → [6] respond |

**Parallel work (complex tier):** steps with no inter-dependency run concurrently via `ThreadPoolExecutor` (capped at `min(team_size, 4)` workers).

**State persistence:** Each step result stored in `coworker_step_results` table (append-only) keyed by `run_id + step_index`.

**Error handling:**
- A2A timeout (default 30s): retry once, then mark step `degraded`, continue with partial result
- Role returns empty result: trigger `negotiate` primitive before advancing
- 3 consecutive step failures: surface to HITL gate regardless of `escalation_threshold`

---

### Step 5: Communication Primitives

All inter-agent communication uses four typed primitives. Each primitive is a structured A2A message with a `primitive_type` field.

#### DELEGATE
Send a task from orchestrator (or lead role) to a co-worker role.

**Tool:** `python tools/coworker/primitives.py delegate --from <sender_role> --to <target_role> --task "<task>" --context-file .tmp/coworker/step_<N>_context.json --run-id <run_id>`

**Message schema:**
```json
{
  "primitive_type": "delegate",
  "run_id": "<uuid>",
  "from_role": "orchestrator",
  "to_role": "security_analyst",
  "task": "Identify CAT1 STIG findings in attached configuration",
  "context": { "artifacts": ["..."], "deadline_hint": "async" },
  "authority_constraints": ["veto_allowed"],
  "reply_to": "coworker.results.<run_id>"
}
```

---

#### VERIFY
Request quality review of a step result before acceptance.

**Tool:** `python tools/coworker/primitives.py verify --result-file .tmp/coworker/step_<N>_result.json --verifier-role <role> --quality-rubric args/coworker_quality.yaml --run-id <run_id>`

**Verification rubric (`args/coworker_quality.yaml`):**
```yaml
gates:
  completeness: 0.80     # fraction of required fields present
  accuracy: 0.75         # cross-checked against known facts / KB
  evidence_coverage: 0.70 # claims backed by sources
  il_compliance: 1.00    # classification markings correct (hard gate)
```

**Output:** Verification verdict (`pass | fail | partial`) with per-gate scores.

---

#### NEGOTIATE
Attempt resolution when a VERIFY gate fails, without escalating to HITL.

**Tool:** `python tools/coworker/primitives.py negotiate --step-result .tmp/coworker/step_<N>_result.json --verify-verdict .tmp/coworker/step_<N>_verify.json --negotiator-role <role> --max-rounds 2 --run-id <run_id>`

**Negotiation protocol:**
1. Negotiator sends targeted feedback to the original delegate role (specific gaps from verify verdict)
2. Delegate revises result addressing only the failing gates
3. Verifier re-checks revised result
4. After 2 rounds: if still failing, escalate to HITL gate; do not loop indefinitely

**Log:** Every negotiation round logged in `coworker_negotiations` table with round number, feedback, and revised score.

---

#### BROADCAST
Publish a step result or status update to all team members and the active session.

**Tool:** `python tools/coworker/primitives.py broadcast --message "<text>" --artifact-file .tmp/coworker/step_<N>_result.json --audience team,session --run-id <run_id>`

**Broadcast channels:**
- `team`: all role agents in `team.json` receive the update via A2A mailbox
- `session`: active chat session receives a formatted update card
- `canvas`: `/coworker/` dashboard receives a real-time SSE event
- `audit`: append-only record in `coworker_broadcast_log` table

**Use cases:**
- Announce step completion with key findings
- Alert team to a constraint discovered by one role (e.g., "security found IL4 scope change — all artifacts must be re-marked")
- Signal final synthesis ready for review

---

### Step 6: HITL Gates

Human-in-the-Loop gates pause execution and surface decision context to the user. Gates fire based on `escalation_threshold` from classification and runtime conditions.

**Tool:** `python tools/coworker/hitl_gate.py --run-id <run_id> --gate-type <type> --context .tmp/coworker/hitl_context.json`

**Gate types and conditions:**

| Gate Type | Fires When | User Action Required |
|-----------|-----------|---------------------|
| `advisory_review` | `escalation_threshold=advisory` + complexity=complex | Review summary, confirm proceed / redirect |
| `veto_authority` | A veto-authority role (e.g., security_analyst) issues a hard veto | Resolve the veto finding or explicitly override with rationale |
| `quality_failure` | NEGOTIATE exhausted (2 rounds, still failing) | Provide clarification, adjust scope, or accept partial result |
| `il_boundary_crossed` | Step result contains classification higher than session IL | Confirm classification upgrade or remove offending content |
| `scope_change` | New requirement discovered mid-execution that changes team composition | Approve team expansion or proceed with current team |
| `mandatory_approval` | `escalation_threshold=mandatory` (always fires before final output) | Explicit approval of synthesized result before delivery |

**Gate surface:**
- Chat: structured message with gate type, summary, and action buttons (Approve / Redirect / Override)
- `/coworker/` canvas: HITL gate card appears in the run timeline
- API trigger: `POST /api/coworker/runs/<run_id>/hitl` receives gate payload; caller must respond via `PUT /api/coworker/runs/<run_id>/hitl/<gate_id>`

**Timeout:** HITL gates time out after 4 hours (configurable in `args/coworker_defaults.yaml`). On timeout: run pauses, status set to `awaiting_hitl`, resumes when user responds.

**Log:** Gate events recorded in `coworker_hitl_events` table (append-only, NIST AU compliant).

---

### Step 7: Result Surfacing

Surface the final synthesized result through chat and the `/coworker/` canvas.

#### Chat Surface

**Tool:** `python tools/coworker/result_formatter.py --run-id <run_id> --format chat --json`

Format the result as a structured chat response:
- **Header:** Problem statement + team composition summary
- **Findings:** Bullet list of key outputs per role
- **Conflicts:** Any unresolved disagreements between roles (with negotiation history)
- **HITL decisions:** Summary of user decisions made during gates
- **Artifacts:** Links to generated documents/reports stored in `data/coworker/runs/<run_id>/`
- **Confidence:** Overall result confidence score (0.0–1.0) derived from verify gate scores

Delivered via `tools/chat/message_sender.py --session-id <id> --type coworker_result`.

#### /coworker/ Canvas Surface

**URL:** `http://localhost:5050/coworker/`

**Tool:** `python tools/coworker/canvas_sync.py --run-id <run_id> --write --json`

Canvas pages and components:

| Page / Panel | Content |
|-------------|---------|
| `/coworker/` (index) | Active runs table, recent completed runs, team composition timeline |
| `/coworker/runs/<run_id>` | Full run detail: step-by-step timeline, per-role outputs, verify scores, negotiate history, HITL gate log |
| `/coworker/team` | Role registry viewer — all available roles, current agent health, active assignments |
| `/coworker/analytics` | Run metrics: avg team size, step completion rate, escalation frequency, cycle time by complexity |

**SSE events:** Canvas receives real-time updates via `GET /api/coworker/runs/<run_id>/stream` (Server-Sent Events). Each step completion, gate firing, and broadcast is pushed as an SSE event.

**IQE integration:** `/coworker/` canvas registered in IQE dispatch with seed queries:
- "Show me the last co-worker run for compliance review"
- "Which runs required HITL approval this week?"
- "What was the team composition for the last complex task?"

**Verify:** Run record exists in `coworker_runs` table. All step results in `coworker_step_results`. Canvas SSE stream confirmed active. Chat message delivered to session.

---

### Step 8: Audit and Memory

Record the run in the audit trail and persist lessons learned.

**Audit log:**

**Tool:** `python tools/audit/audit_logger.py --event-type "coworker.run.complete" --actor "ace-coworker-engine" --action "Co-worker run <run_id> completed: <complexity> problem, <team_size> roles, <step_count> steps" --project-id "<project_id>"`

**Memory persistence:**

**Tool:** `python tools/memory/memory_write.py --content "<summary of what worked, what escalated, what roles were most useful>" --type event --importance 7`

**Lessons learned engine:**

**Tool:** `python tools/kanban/lessons_learned.py --run-id <run_id> --source coworker --write-memory`

Captures: team composition effectiveness, negotiation patterns, HITL gate frequency per problem type, common failure modes.

**Verify:** Audit entry present. Memory write confirmed. Lessons learned entry in `coworker_lessons` table.

---

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D-ACE-1 | Role definitions in YAML (`args/coworker_roles.yaml`) | Roles configurable without code changes; new domains added by operators |
| D-ACE-2 | Oracle lens for classification (8 dimensions) | Deterministic scoring reduces mis-assembly; 8 dimensions cover all known GovCon problem types |
| D-ACE-3 | 4 typed primitives (delegate/verify/negotiate/broadcast) | Minimal but complete communication grammar; maps to real team communication patterns |
| D-ACE-4 | Negotiate max 2 rounds before HITL | Prevents infinite loops; 2 rounds catches most misunderstandings without over-escalating |
| D-ACE-5 | Append-only step result table | NIST AU compliance; full run replay possible from DB without re-executing |
| D-ACE-6 | SSE for canvas real-time updates | No polling; compatible with existing ICDEV™ SSE infrastructure in `app.py` |
| D-ACE-7 | HITL gates typed (6 types) | Typed gates allow role-based gate routing and UI-specific rendering per gate type |
| D-ACE-8 | ThreadPoolExecutor (not asyncio) for parallel steps | Consistent with ICDEV™ ADR D36; no asyncio in production execution paths |

---

## Edge Cases

1. **All team agents degraded:** Fall back to Claude (this session) for all roles, log degraded run, surface warning in chat and canvas.
2. **HITL gate times out:** Pause run at `awaiting_hitl` status. Resume endpoint re-triggers step from last checkpoint without re-running completed steps.
3. **Veto + override conflict:** If user overrides a security veto, log override with required rationale field. Emit `coworker.veto.overridden` audit event. Do not silently drop veto.
4. **IL boundary crossed mid-run:** Immediately pause, fire `il_boundary_crossed` HITL gate. Do not broadcast IL6 content to IL4 session channel.
5. **Problem scope expands mid-step:** BROADCAST scope change to team, fire `scope_change` HITL gate. User confirms whether to expand team or constrain scope.
6. **Duplicate run (same problem):** Detect via SHA-256 of problem + context in last 24h. Surface existing run ID; ask user if they want a fresh run or to review the prior one.
7. **API trigger with no active session:** Create a headless run. Results stored in `data/coworker/runs/<run_id>/`. Caller polls `GET /api/coworker/runs/<run_id>` for status and result.
8. **Complex problem with simple team (1 role available):** Downgrade complexity to `moderate`, warn user, proceed with available roles.

---

## Anti-Patterns

1. **Assembling a full team for a simple question** — Oracle classification prevents this; always check complexity tier before adding roles.
2. **Infinite negotiate loops** — Negotiate is capped at 2 rounds. Third failure always escalates to HITL.
3. **Broadcasting sensitive intermediate results to the wrong channel** — Always check IL level before broadcasting to session or canvas.
4. **Skipping verify on complex steps** — Quality gates are mandatory for `complex` tier; do not skip them to save time.
5. **Running without audit trail** — Every step must produce an append-only audit record. Silent failures corrupt the run timeline.
6. **Letting roles override each other's domain** — Compliance Reviewer does not write security findings; Security Analyst does not author compliance controls. Role boundaries are enforced by the role YAML `domains` field.

---

## Success Criteria

- [ ] Trigger detected and problem extracted from correct source
- [ ] Oracle classification scored across all 8 dimensions with confidence ≥ 0.65
- [ ] Team assembled with at least one role matching primary domain
- [ ] All role agents reachable or degraded status logged
- [ ] Step loop completed all steps (or paused at HITL gate awaiting response)
- [ ] All 4 primitives used correctly (delegate → verify → negotiate if needed → broadcast)
- [ ] HITL gates fired for all qualifying conditions
- [ ] Final result surfaced in chat with confidence score
- [ ] `/coworker/runs/<run_id>` canvas page populated with full timeline
- [ ] Audit trail entry complete (append-only)
- [ ] Memory write and lessons learned recorded

---

## FORGE Layer Mapping

| Step | FORGE Layer | Component |
|------|-------------|-----------|
| Trigger Detection | Tools | `tools/coworker/trigger_detector.py` |
| Problem Classification | Tools + Context | `tools/oracle/classify.py` + `context/oracle/ace_coworker_lens.yaml` |
| Team Assembly | Tools + Args | `tools/coworker/team_assembler.py` + `args/coworker_roles.yaml` |
| Co-Worker Execution | Tools | `tools/coworker/executor.py` |
| Communication Primitives | Tools | `tools/coworker/primitives.py` |
| HITL Gates | Tools | `tools/coworker/hitl_gate.py` |
| Result Surfacing | Tools | `tools/coworker/result_formatter.py`, `tools/coworker/canvas_sync.py` |
| Audit + Memory | Tools | `tools/audit/audit_logger.py`, `tools/memory/memory_write.py` |
| Quality rubric | Args | `args/coworker_quality.yaml` |
| Role definitions | Args | `args/coworker_roles.yaml` |
| Oracle lens config | Args + Context | `args/coworker_defaults.yaml`, `context/oracle/ace_coworker_lens.yaml` |
| HITL behavior | Args | `args/coworker_defaults.yaml` (escalation thresholds, timeouts) |

---

## Database Tables

| Table | Type | Purpose |
|-------|------|---------|
| `coworker_runs` | append-only | Run lifecycle: status, team, classification, timing |
| `coworker_step_results` | append-only | Per-step output, verify score, primitive type |
| `coworker_negotiations` | append-only | Negotiation rounds, feedback, revised scores |
| `coworker_broadcast_log` | append-only | Broadcast messages with audience and IL level |
| `coworker_hitl_events` | append-only | HITL gate type, context, user response, timestamp |
| `coworker_lessons` | mutable | Aggregated lessons learned, updated after each run |

---

## Related Files

- **Goals:** `goals/multi_agent_orchestration.md` (A2A + DAG execution patterns), `goals/requirements_intake.md` (Oracle intake patterns), `goals/session_coordination.md` (cross-session state)
- **Tools:** `tools/coworker/` (all co-worker tools), `tools/oracle/classify.py`, `tools/agent/team_orchestrator.py`
- **Args:** `args/coworker_roles.yaml` (role registry), `args/coworker_quality.yaml` (verify rubric), `args/coworker_defaults.yaml` (timeouts, thresholds)
- **Context:** `context/oracle/ace_coworker_lens.yaml` (8-dimension lens config)
- **Skill:** `.claude/skills/icdev-coworker/SKILL.md`
- **Blueprint:** `tools/coworker/blueprint.py` (API routes for `/coworker/` canvas and `/api/coworker/`)
- **Templates:** `tools/dashboard/templates/coworker/` (canvas pages)

---

## Changelog

- 2026-06-04: Initial creation — ACE Co-Worker Engine goal workflow: 8-step process, 6 roles, 4 communication primitives, 6 HITL gate types, chat + canvas result surfacing
