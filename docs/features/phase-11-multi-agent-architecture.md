# Phase 11 — Multi-Agent Architecture

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 11 |
| Title | Multi-Agent Architecture |
| Status | Implemented |
| Priority | P0 |
| Dependencies | Phase 1 (GOTCHA Framework), Phase 2 (Tools Layer) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

A single monolithic AI agent cannot effectively manage the full software development lifecycle for Gov/DoD applications. The cognitive complexity of simultaneously handling architecture, code generation, compliance, security, infrastructure, and monitoring exceeds what one agent can reliably accomplish. Each domain requires specialized knowledge, different confidence thresholds, and distinct failure modes. A security agent must be paranoid; a builder agent must be creative; a compliance agent must be pedantic.

Furthermore, Gov/DoD environments demand separation of duties as a core security principle. NIST 800-53 AC-5 (Separation of Duties) requires that no single entity can both create and approve security-critical artifacts. A monolithic agent that generates code and then approves its own security scan violates this control. Multi-agent architecture enforces domain boundaries where the Security Agent can veto the Builder Agent's output and the Compliance Agent can block deployments.

The A2A (Agent-to-Agent) protocol provides a standardized communication mechanism using JSON-RPC 2.0 over mutual TLS, ensuring that inter-agent communication within the K8s cluster is authenticated, encrypted, and auditable. Each agent publishes its capabilities via an Agent Card at `/.well-known/agent.json`, enabling dynamic discovery and routing without hardcoded dependencies.

---

## 2. Goals

1. Deploy 15 specialized agents across 3 tiers (Core, Domain, Support) with clear responsibility boundaries and port assignments (8443-8458)
2. Implement A2A protocol using JSON-RPC 2.0 over mutual TLS for authenticated, encrypted inter-agent communication within the K8s cluster
3. Publish Agent Cards at `/.well-known/agent.json` per agent, enabling dynamic discovery of capabilities, accepted input modes, and output modes
4. Manage the full task lifecycle (submitted, working, input-required, completed, failed) with state transitions recorded in `a2a_tasks` and `a2a_task_history` tables
5. Route tasks from the Orchestrator to appropriate domain agents based on capability matching, with fallback and retry logic
6. Enforce domain authority via a YAML-defined authority matrix where Security Agent holds hard veto on code/deps/infra and Compliance Agent holds hard veto on artifacts/deploy
7. Support multi-agent workflows where the Orchestrator decomposes complex tasks into DAGs executed across multiple agents with artifact passing
8. Run each agent as a separate K8s Deployment with resource limits, liveness/readiness probes, network policies, and HPA auto-scaling

---

## 3. Architecture

```
+-----------------------------------------------------------+
|                   CORE TIER                                |
|                                                           |
|  +-------------------+  +-------------------+             |
|  | Orchestrator      |  | Architect         |             |
|  | Port 8443         |  | Port 8444         |             |
|  | Task routing,     |  | ATLAS/M-ATLAS,    |             |
|  | workflow mgmt,    |  | system design     |             |
|  | DAG execution     |  |                   |             |
|  +--------+----------+  +-------------------+             |
+-----------|-----------------------------------------------+
            |  A2A (JSON-RPC 2.0 / mTLS)
            v
+-----------------------------------------------------------+
|                   DOMAIN TIER                              |
|                                                           |
|  +----------+ +----------+ +----------+ +----------+     |
|  | Builder  | |Compliance| | Security | |  Infra   |     |
|  | 8445     | | 8446     | | 8447     | | 8448     |     |
|  | TDD gen  | | ATO/SSP  | | SAST/    | | Terraform|     |
|  |          | | POAM/CUI | | audit    | | Ansible  |     |
|  +----------+ +----------+ +----------+ +----------+     |
|                                                           |
|  +----------+ +----------+ +----------+ +----------+     |
|  |   MBSE   | | Modern.  | | Req.Anl  | | Supply   |     |
|  |   8451   | | 8452     | | 8453     | | Chain    |     |
|  | SysML/   | | Legacy   | | Intake/  | | 8454     |     |
|  | DOORS    | | 7R/migr. | | gap/SAFe | | SBOM/CVE |     |
|  +----------+ +----------+ +----------+ +----------+     |
|                                                           |
|  +----------+ +----------+ +----------+                  |
|  | Simulate | |DevSecOps | | Gateway  |                  |
|  |   8455   | | & ZTA    | | 8458     |                  |
|  | Monte    | | 8457     | | Remote   |                  |
|  | Carlo    | | Pipeline | | commands |                  |
|  +----------+ +----------+ +----------+                  |
+-----------------------------------------------------------+
            |
            v
+-----------------------------------------------------------+
|                   SUPPORT TIER                             |
|                                                           |
|  +-------------------+  +-------------------+             |
|  | Knowledge         |  | Monitor           |             |
|  | Port 8449         |  | Port 8450         |             |
|  | Self-healing,     |  | Log analysis,     |             |
|  | patterns, ML,     |  | metrics, alerts,  |             |
|  | recommendations   |  | health checks     |             |
|  +-------------------+  +-------------------+             |
+-----------------------------------------------------------+
```

### A2A Protocol

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

### Task Lifecycle

```
submitted --> working --> completed
                     \--> input-required --> working
                     \--> failed
```

---

## 4. Requirements

### 4.1 Agent Registration and Discovery

#### REQ-11-001: Agent Registration
Each agent SHALL register on startup with agent ID, name, version, capabilities (skills list), endpoint URL, and health check URL, stored in the `agents` table.

#### REQ-11-002: Agent Card Publication
Each agent SHALL publish an Agent Card at `/.well-known/agent.json` per the A2A specification, declaring capabilities, accepted input modes, and output modes.

#### REQ-11-003: Dynamic Discovery
The Orchestrator SHALL discover agent capabilities by fetching and caching Agent Cards, enabling routing decisions without hardcoded agent dependencies.

### 4.2 Communication Protocol

#### REQ-11-004: A2A Protocol
Inter-agent communication SHALL use JSON-RPC 2.0 over HTTPS with mutual TLS, authenticated via service mesh certificates within the K8s cluster.

#### REQ-11-005: Task Lifecycle Management
The system SHALL track task state transitions (submitted, working, input-required, completed, failed) in the `a2a_tasks` and `a2a_task_history` tables.

#### REQ-11-006: Artifact Passing
Multi-agent workflows SHALL pass artifacts between agents via the `a2a_task_artifacts` table, enabling sequential and parallel task execution with data dependencies.

### 4.3 Task Routing and Orchestration

#### REQ-11-007: Capability-Based Routing
The Orchestrator SHALL analyze incoming tasks to determine required agent(s) and route to appropriate agents based on their declared capabilities.

#### REQ-11-008: DAG-Based Workflow Execution
Complex tasks SHALL be decomposed into directed acyclic graphs (DAGs) using `graphlib.TopologicalSorter` (D40), enabling parallel execution of independent subtasks.

#### REQ-11-009: Domain Authority Vetoes
The system SHALL enforce domain authority via a YAML-defined matrix (`args/agent_authority.yaml`) where Security Agent holds hard veto on code/deps/infra and Compliance Agent holds hard veto on artifacts/deploy.

### 4.4 Health and Resilience

#### REQ-11-010: Heartbeat Monitoring
The system SHALL perform periodic heartbeat checks (every 30 seconds) on all agents, marking agents as offline after 3 consecutive failures.

#### REQ-11-011: Circuit Breaker Protection
The system SHALL implement circuit breaker protection (fail fast after 5 failures in 1 minute) per agent to prevent cascading failures.

#### REQ-11-012: Error Recovery
When an agent is offline, the system SHALL queue tasks for retry and route to backup agents when available.

### 4.5 Deployment

#### REQ-11-013: K8s Deployment
Each agent SHALL run as a separate K8s Deployment with resource limits (256Mi-512Mi memory, 250m-500m CPU), liveness/readiness probes, NetworkPolicy isolation, and HPA auto-scaling.

#### REQ-11-014: STIG-Hardened Containers
All agent containers SHALL use the STIG-hardened base image with read-only root filesystem, dropped ALL capabilities, non-root user (UID 1000), and resource limits enforced.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `agents` | Agent registry with ID, name, version, capabilities, endpoint, health status, last heartbeat |
| `a2a_tasks` | Task records with ID, source agent, target agent, status, message, result |
| `a2a_task_history` | Append-only task state transitions for audit trail |
| `a2a_task_artifacts` | Artifacts passed between agents during multi-agent workflows |
| `agent_token_usage` | Token consumption tracking per agent per model per project |
| `agent_workflows` | DAG-based workflow definitions with subtask dependencies |
| `agent_subtasks` | Individual subtasks within a workflow with agent assignment and status |
| `agent_mailbox` | HMAC-SHA256 signed inter-agent message queue (D41) |
| `agent_vetoes` | Domain authority veto records (append-only) |
| `agent_memory` | Agent-scoped memory entries by (agent_id, project_id) with team-shared via _team |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/a2a/agent_registry.py` | Agent registration, health monitoring, discovery |
| `tools/a2a/agent_client.py` | A2A client for sending tasks and fetching agent cards |
| `tools/a2a/task.py` | A2A task model with lifecycle state management |
| `tools/agent/team_orchestrator.py` | Task decomposition into DAGs and parallel execution |
| `tools/agent/skill_router.py` | Route skills to healthy agents based on capability matching |
| `tools/agent/collaboration.py` | Multi-agent collaboration patterns (reviewer, pair) |
| `tools/agent/authority.py` | Domain authority checking and veto enforcement |
| `tools/agent/mailbox.py` | HMAC-SHA256 signed agent inbox/outbox |
| `tools/agent/agent_memory.py` | Scoped agent memory recall and storage |
| `tools/agent/bedrock_client.py` | Bedrock LLM invocation with model fallback chain |
| `tools/agent/token_tracker.py` | Token usage tracking and cost breakdown per agent |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D2 | Stdio for MCP (Claude Code); HTTPS+mTLS for A2A (K8s inter-agent) | MCP uses stdio for local tooling; A2A needs authenticated network transport within K8s |
| D36 | `boto3 invoke_model()` + `ThreadPoolExecutor` for Bedrock | Matches existing subprocess/sqlite3 patterns; no asyncio needed |
| D37 | Model fallback chain: Opus 4.6, Sonnet 4.5, Sonnet 3.5 with 30min health probe TTL | Ensures continuity when preferred model is unavailable |
| D38 | Effort parameter mapped per agent role | Orchestrator=high, Builder=max, Monitor=low optimizes cost/quality per agent |
| D40 | `graphlib.TopologicalSorter` (stdlib Python 3.9+) for task DAG | Air-gap safe, zero deps, cycle detection built-in |
| D41 | SQLite-based agent mailbox with HMAC-SHA256 signing | Air-gap safe, append-only for audit, tamper-evident |
| D42 | Domain authority defined in YAML matrix, vetoes recorded append-only | Configurable without code changes, auditable |
| D43 | Agent memory scoped by (agent_id, project_id) | Prevents cross-project contamination; team-shared via agent_id='_team' |

---

## 8. Security Gate

**Agent Management Gate:**
- All inter-agent communication authenticated via mutual TLS within K8s cluster
- Domain authority vetoes enforced (Security Agent: hard veto on code/deps/infra; Compliance Agent: hard veto on artifacts/deploy)
- Agent mailbox messages HMAC-SHA256 signed for tamper detection
- All task lifecycle transitions recorded in append-only audit trail (NIST AC-2, AU-12)
- NetworkPolicy restricts inter-agent communication to declared dependencies only
- All containers: read-only rootfs, drop ALL capabilities, non-root (UID 1000)

---

## 9. Commands

```bash
# Agent health and routing
python tools/agent/skill_router.py --health
python tools/agent/skill_router.py --route-skill "ssp_generate"

# Task orchestration
python tools/agent/team_orchestrator.py --decompose "task description" --project-id "proj-123"
python tools/agent/team_orchestrator.py --execute --workflow-id "wf-123"

# Domain authority
python tools/agent/authority.py --check security-agent code_generation

# Agent communication
python tools/agent/mailbox.py --inbox --agent-id "builder-agent"
python tools/agent/agent_memory.py --recall --agent-id "builder-agent" --project-id "proj-123"

# Bedrock client
python tools/agent/bedrock_client.py --probe
python tools/agent/bedrock_client.py --prompt "text" --model opus --effort high
python tools/agent/bedrock_client.py --prompt "text" --stream

# Token tracking
python tools/agent/token_tracker.py --action summary --project-id "proj-123"
python tools/agent/token_tracker.py --action cost --project-id "proj-123"

# Collaboration patterns
python tools/agent/collaboration.py --pattern reviewer --project-id "proj-123"
```

**CUI // SP-CTI**
