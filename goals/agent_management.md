# Goal: Agent Management

## Purpose
Manage the 8-agent multi-tier architecture: registration, health monitoring, task routing, A2A communication, and lifecycle management. Ensures all agents are operational and properly communicating via the A2A protocol.

## Trigger
- System startup (agent registration)
- `/icdev-status` skill invoked (agent health section)
- Agent heartbeat failure detected
- Task routing required by Orchestrator

## Inputs
- Agent configuration (`args/agent_config.yaml`)
- Agent cards (`tools/a2a/agent_cards/*.json`)
- A2A task model (`tools/a2a/task.py`)
- Agent registry state (`agents` table in icdev.db)

## Architecture

### Agent Tiers
| Tier | Agent | Port | Responsibilities |
|------|-------|------|-----------------|
| Core | Orchestrator | 8443 | Task routing, workflow coordination |
| Core | Architect | 8444 | ATLAS A/T phases, system design |
| Domain | Builder | 8445 | TDD code generation, testing, linting |
| Domain | Compliance | 8446 | ATO artifacts, STIG, SBOM, CUI |
| Domain | Security | 8447 | SAST, dependency audit, secret detection |
| Domain | Infrastructure | 8448 | Terraform, Ansible, K8s, CI/CD |
| Support | Knowledge | 8449 | Pattern detection, self-healing, recommendations |
| Support | Monitor | 8450 | Log analysis, metrics, alerts, health checks |

### Communication Protocol
- **A2A (Agent-to-Agent):** JSON-RPC 2.0 over HTTPS with mutual TLS
- **Agent Cards:** Published at `/.well-known/agent.json` per A2A spec
- **Task Lifecycle:** submitted → working → input-required → completed/failed
- **Within K8s:** Service mesh handles mTLS certificates

## Process

### Step 1: Agent Registration
**Tool:** `tools/a2a/agent_registry.py`
- Each agent registers on startup with:
  - Agent ID, name, version
  - Capabilities (skills list from agent card)
  - Endpoint URL
  - Health check URL
- Stored in `agents` table

### Step 2: Health Monitoring
**Tool:** `tools/a2a/agent_registry.py` → `check_health()`
- Periodic heartbeat checks (every 30 seconds)
- HTTP GET to each agent's health endpoint
- Track response time and availability
- Update `agents` table with last_heartbeat timestamp
- After 3 consecutive failures: mark agent as `offline`

### Step 3: Agent Discovery
**Tool:** `tools/a2a/agent_client.py` → `discover()`
- Fetch agent card from `/.well-known/agent.json`
- Parse capabilities, accepted input modes, output modes
- Cache agent cards for routing decisions

### Step 4: Task Routing
**Tool:** `tools/a2a/agent_client.py` → `send_task()`
- Orchestrator receives high-level task
- Analyze task to determine required agent(s)
- Route to appropriate agent via A2A protocol:
  ```json
  {
    "jsonrpc": "2.0",
    "method": "tasks/send",
    "params": {
      "id": "<uuid>",
      "message": {
        "role": "user",
        "parts": [{"type": "text", "text": "<task description>"}]
      }
    }
  }
  ```
- Track task in `a2a_tasks` table

### Step 5: Task Lifecycle Management
Track task state transitions:
1. `submitted` — Task received by target agent
2. `working` — Agent actively processing
3. `input-required` — Agent needs additional input
4. `completed` — Task finished successfully (with artifacts)
5. `failed` — Task failed (with error details)

Record all transitions in `a2a_task_history` table.

### Step 6: Multi-Agent Workflows
Complex tasks involve multiple agents:
1. Orchestrator breaks down high-level task
2. Routes subtasks to domain agents in dependency order
3. Passes artifacts between agents (via `a2a_task_artifacts` table)
4. Aggregates results
5. Reports completion to user

Example workflow for `/icdev-init`:
```
Orchestrator → Architect (design)
            → Builder (scaffold)
            → Compliance (baseline controls)
            → Security (initial scan)
```

### Step 7: Error Handling
- **Agent offline:** Route to backup or queue for retry
- **Task timeout:** Cancel after configurable timeout, retry once
- **Task failure:** Record failure, attempt alternative approach
- **Cascading failure:** Circuit breaker pattern (fail fast after 5 failures in 1 minute)

### Step 8: Audit Trail
**Tool:** `tools/audit/audit_logger.py`
- Record: agent registration, health state changes, task routing decisions
- **NIST Controls:** AC-2 (Account Management), AU-12 (Audit Record Generation)

## Outputs
- Agent registry (all 8 agents with health status)
- Task routing logs
- Agent health dashboard data
- A2A task history with artifacts

## K8s Deployment
Each agent runs as a separate Kubernetes Deployment:
- Resource limits: 256Mi-512Mi memory, 250m-500m CPU
- Liveness and readiness probes on health endpoint
- NetworkPolicy restricts inter-agent communication
- Service mesh provides mTLS
- HPA for auto-scaling based on task queue depth

## Edge Cases
- Agent startup order: Orchestrator must start first, others can start in any order
- Network partition: agents continue independently, reconcile when reconnected
- Version mismatch: agent cards include version, routing considers compatibility
- Resource exhaustion: throttle task submission when agent is overloaded
- Agent restart: re-register on startup, resume in-progress tasks from last checkpoint

## Related Goals
- `self_healing.md` — Agent self-healing on failure
- `monitoring.md` — Agent metric collection
- `dashboard.md` — Agent health display
