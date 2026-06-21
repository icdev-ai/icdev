# [TEMPLATE: CUI // SP-CTI]
# Goal: ACE Co-Worker Engine

## Purpose
Assemble and drive autonomous multi-role co-worker teams that collaborate on open-ended problems via delegation, creator-verifier cycles, negotiation, and broadcast — with HITL gates at configurable trust thresholds.

## When to Use
- A chat message, kanban task, or API call requires multi-role expertise (build + security + compliance + QA)
- A problem cannot be solved by a single LLM call and needs decomposition across specialized roles
- A task requires creator-verifier delegation (one role builds, another independently verifies)
- A task involves trade-off decisions that should be negotiated across role perspectives
- Continuous broadcast of progress is needed into `/coworker/` canvas and chat

## Trigger Sources

| Source | Entry Point | Ref Passed |
|--------|-------------|------------|
| Chat `/launch-ace` | `POST /api/ace/launch` | chat session_id |
| Kanban task execution | `ACEController.launch()` from kanban.py | task_id |
| Direct API | `POST /api/ace/launch` JSON body | caller-supplied ref |

## Workflow

### Step 1: Problem Classification
- Tool: `icdev/tools/ace/problem_classifier.py` — `ProblemClassifierLens`
- Three-phase Oracle pipeline: `analyze()` → `score()` → `propose()`
- Extracts RICOAS signals and keyword scores from the problem text
- Maps signal clusters to `TeamManifest` (list of `RoleSlot(role_id, count, priority)`)
- Fallback when confidence < 0.5: `[ai_developer×1, qa_manager×1]`
- Config: `args/ace/ace_config.yaml` → `hitl_threshold`, `max_team_size`

### Step 2: Team Assembly
- Tool: `icdev/tools/ace/team_assembler.py` — `TeamAssembler`
- Loads role YAML definitions from `args/ace/roles/<role_id>.yaml`
- Available roles: `ai_developer`, `compliance_manager`, `data_analyst`, `devops_engineer`, `qa_manager`, `requirements_engineer`, `security_analyst`
- Persists `ace_team_instances` and `ace_team_members` rows via `get_canvas_connection()`
- Returns: `TeamInstance` (instance_id, list of `CoWorkerSpec`)

### Step 3: Co-Worker Execution Loop
- Tool: `icdev/tools/ace/coworker_thread.py` — `CoWorkerThread`
- Each `CoWorkerSpec` runs in its own thread (ThreadPoolExecutor, max 16 workers)
- Step loop: load prompt chain → execute steps via `StepExecutor` → emit SSE progress
- Tool permissions enforced per-role from `CoWorkerSpec.tool_permissions`
- TrustKernel gate: `can_execute(step, context)` checked before every tool invocation

### Step 4: Communication Primitives
- Tool: `icdev/tools/ace/message_bus.py` — `MessageBus`

| Primitive | Trigger | Payload |
|-----------|---------|---------|
| **delegate** | creator assigns sub-task to another role | `{to_role, task_desc, artifacts}` |
| **verify** | creator requests independent review | `{artifact_id, criteria}` |
| **negotiate** | two roles disagree on an approach | `{proposal, counter_proposal, round}` |
| **broadcast** | any role emits status/result to all | `{event_type, content}` |

- All messages travel through `tools/agent/mailbox.py` (message_type=`notification`, subject=`ACE:<cw_type>`)
- Every send also writes to `ace_messages` for canvas traceability
- `NegotiationFailedError` raised after `max_negotiation_rounds` (default 3) without consensus

### Step 5: HITL Gate
- Threshold: `hitl_threshold` (default 0.6) from `args/ace/ace_config.yaml`
- When a co-worker's confidence score drops below threshold: pause thread, create HITL request row in `ace_hitl_requests`
- Chat notification sent via `tools/notification_service/alert_service.py`
- Resume on human approval via `POST /api/ace/<instance_id>/hitl-approve`
- Timeout after 24 h: escalate to instance abort

### Step 6: Result Surfacing
- Dashboard: results appear live on `/coworker/<instance_id>` (SSE via `websocket.py`)
- Canvas index: `/coworker/` lists all instances by status (running / completed / aborted)
- Chat: final summary injected as assistant message on the originating session
- Artifacts: downloadable at `GET /api/ace/<instance_id>/artifacts`
- Audit trail: every step, message, and HITL decision written to `ace_audit_log` (append-only)

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ace/launch` | Start new ACE instance; returns `instance_id` |
| GET | `/api/ace/instances` | List all instances (filterable by status) |
| GET | `/api/ace/<id>/status` | Fetch current status + step progress |
| GET | `/api/ace/<id>/messages` | Fetch inter-coworker message log |
| GET | `/api/ace/<id>/artifacts` | Download produced artifacts |
| POST | `/api/ace/<id>/abort` | Abort a running instance |

## Architecture Decisions
- `ACEController` is a process singleton — `ACEController.get_instance()` (thread-safe double-checked locking)
- Canvas DB accessed via `get_canvas_connection()` — `ace_*` tables have no `classification`/`tenant_id` columns
- `StepExecutor` uses restricted eval with `_SAFE_BUILTINS`; no shell exec
- Roles hot-reloaded from YAML (`hot_reload_roles: true`) without process restart
- Trust tier defaults to `yellow`; red roles block tool execution via TrustKernel

## Edge Cases
- If problem classifier confidence < 0.5 and LLM unavailable: default 2-role team
- If a co-worker thread fails: mark member as `failed`, continue others; instance completes as `partial`
- If negotiation exhausts rounds: emit `NegotiationFailedError`, elevate to HITL
- Stale instances older than `stale_instance_hours` (default 4 h) are reaped by the Genesis self-monitor reflex
- Max team size enforced: excess `RoleSlot` entries dropped at assembly time

## Success Criteria
- `ace_team_instances` row created with `status=running`
- All `CoWorkerThread` futures submitted to executor
- At least one SSE progress event emitted per step
- HITL gate triggered when confidence < threshold
- Final artifacts accessible at `/api/ace/<id>/artifacts`
- `ace_audit_log` row written for every state transition (append-only)
