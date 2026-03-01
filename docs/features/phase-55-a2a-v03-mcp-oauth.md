# Phase 55 — A2A v0.3 Protocol + MCP OAuth 2.1

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 55 |
| Title | A2A v0.3 Protocol + MCP OAuth 2.1 |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 11 (Multi-Agent Architecture), Phase 21 (SaaS Multi-Tenancy), Phase 46 (Observability & XAI), Phase 47 (Unified MCP Gateway) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-25 |

---

## 1. Problem Statement

ICDEV's 15-agent multi-agent architecture communicates via the A2A protocol (JSON-RPC 2.0 over mutual TLS). The prior implementation used a minimal Agent Card format that lacked structured capability advertisement, task subscription streaming, and version negotiation. When a new agent joined the cluster or an existing agent gained new skills, the Orchestrator had no standardized way to discover what capabilities were available without hardcoded routing tables. There was no streaming subscription model for long-running inter-agent tasks.

Separately, MCP Streamable HTTP transport (Phase 21) relied solely on API key authentication. Connected environments need OAuth 2.1 support for external identity providers, while air-gapped IL5/IL6 environments need offline token verification without calling an external authorization server. Additionally, MCP tools had no mechanism to request user input mid-execution (elicitation) or to track long-running tool invocations as first-class lifecycle objects (tasks).

Without these capabilities, ICDEV cannot:
- Dynamically discover agent capabilities at runtime
- Subscribe to task completion events across agents
- Negotiate protocol versions for backward compatibility
- Authenticate MCP clients via OAuth 2.1 in connected environments
- Verify tokens offline in air-gapped deployments
- Pause tool execution to request user clarification
- Track long-running MCP tool invocations with progress updates

Phase 55 closes these gaps with A2A v0.3 protocol compliance, an agent discovery server, and MCP OAuth 2.1 with elicitation and task lifecycle support.

---

## 2. Goals

1. Upgrade all 15 Agent Cards to A2A v0.3 format with structured `capabilities`, `skills`, and `tasks/sendSubscribe` metadata
2. Add backward-compatible `protocolVersion` field for version negotiation between v0.2 and v0.3 agents
3. Provide a centralized discovery server for agent registration, skill-based lookup, and capability-based filtering
4. Implement OAuth 2.1 token verification for MCP Streamable HTTP transport with 3 verification modes (JWT, API key, HMAC)
5. Generate offline HMAC-signed tokens for air-gapped environments without requiring an external authorization server
6. Support MCP Elicitation — allow tools to pause and request user input mid-execution
7. Support MCP Tasks — wrap long-running tool invocations with create/progress/complete lifecycle tracking
8. Register new tools in the unified MCP gateway for A2A discovery and MCP OAuth operations

---

## 3. Architecture

```
                   A2A v0.3 + MCP OAuth Architecture
     ┌───────────────────────────────────────────────────────┐
     │                  agent_config.yaml                     │
     │    (15 agents, ports, TLS certs, capabilities)         │
     └──────────────────────┬────────────────────────────────┘
                            │
          ┌─────────────────┼─────────────────────┐
          ↓                 ↓                      ↓
   Agent Card Gen     Discovery Server       MCP OAuth 2.1
   (a2a_agent_card_   (a2a_discovery_        (mcp_oauth.py)
    generator.py)      server.py)
          │                 │                      │
          ↓                 ↓                      ↓
   v0.3 Agent Cards   Skill/Capability       3-Mode Verifier
   (per-agent JSON)    Routing + Health       (JWT/APIKey/HMAC)
          │                 │                      │
          │                 ↓                      │
          │          agent_registry            ┌───┴───┐
          │          (health, status)          ↓       ↓
          │                                Elicitation Tasks
          │                                Handler    Manager
          │                                   │       │
          └───────────────────────────────────┘       │
                            │                          │
                            ↓                          ↓
                   Unified MCP Gateway           Long-Running
                   (tool_registry.py)            Tool Lifecycle
                   + A2A Discovery Tools         (create/progress/
                   + MCP OAuth Tools              complete/fail)
```

### Key Design Principles

- **Backward compatible** — v0.3 Agent Cards include `protocolVersion` field; v0.2 clients ignore new fields (D344)
- **Reuse existing auth** — MCP OAuth reuses SaaS auth middleware patterns, not a new auth stack (D345)
- **Air-gap safe** — HMAC offline tokens use stdlib `hmac` + `hashlib`, zero external dependencies (D345)
- **Non-blocking elicitation** — Tools create elicitation requests and yield; user responds asynchronously (D346)
- **Task lifecycle** — Long-running tools get create/progress/complete/fail states with percentage tracking (D346)

---

## 4. Components

### Component 1: A2A v0.3 Agent Card Generator (`tools/agent/a2a_agent_card_generator.py`)

Generates v0.3-compliant Agent Cards from `args/agent_config.yaml` for all 15 ICDEV agents.

**Agent Card v0.3 Schema:**
| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Agent identifier (e.g., `orchestrator-agent`) |
| `description` | string | Agent role description |
| `url` | string | Agent endpoint URL (mTLS) |
| `version` | string | Agent version (semver) |
| `protocolVersion` | string | A2A protocol version (`0.3`) |
| `contextId` | string | Context preservation identifier |
| `capabilities` | object | Structured capability flags |
| `authentication` | object | Supported auth schemes (`mutual_tls`, `api_key`) |
| `skills` | array | Skill definitions with input/output modes |
| `tasks` | object | Task subscription endpoints (`sendSubscribe`) |
| `metadata` | object | Tier, classification, ICDEV version |

**Default Capabilities (all agents):**
| Capability | Default | Description |
|------------|---------|-------------|
| `streaming` | false | Real-time response streaming |
| `pushNotifications` | false | Push notification support |
| `taskSubscription` | true | Subscribe to task completion events |
| `contextPreservation` | true | Preserve context across invocations |
| `asyncNotifications` | true | Asynchronous notification support |
| `stateTransitionHistory` | true | Task state transition history |

**Skill Definitions:** 15 agents with 30+ total skills mapped from `AGENT_SKILLS` registry, covering task dispatch, system design, TDD code generation, compliance (SSP/POAM/SBOM), security scanning, infrastructure, knowledge, monitoring, MBSE, modernization, requirements intake, supply chain, simulation, ZTA, and remote gateway operations.

### Component 2: A2A v0.3 Discovery Server (`tools/agent/a2a_discovery_server.py`)

Centralized agent discovery with health-aware routing and capability-based filtering.

**Discovery Operations:**
| Operation | Method | Description |
|-----------|--------|-------------|
| `discover_agents()` | List all | Returns all agents with cards and health status from `agent_registry` |
| `find_agent_for_skill(skill_id)` | Skill lookup | Find agents providing a specific skill (e.g., `ssp_generate`) |
| `find_agents_by_capability(cap)` | Capability filter | Find agents with a specific capability (e.g., `taskSubscription`) |
| `get_discovery_summary()` | Summary | Aggregate stats: tier distribution, health counts, skill totals, capability coverage |

**Health Integration:** Discovery server joins Agent Card data with live health status from the `agent_registry` table, providing real-time health-aware routing (healthy/unhealthy/unknown).

**Tier Distribution:** Agents classified as core (Orchestrator, Architect), domain (Builder, Compliance, Security, Infrastructure, MBSE, Modernization, Requirements Analyst, Supply Chain, Simulation, DevSecOps/ZTA, Gateway), and support (Knowledge, Monitor).

### Component 3: MCP OAuth 2.1 Verifier (`tools/saas/mcp_oauth.py`)

Three-mode token verification for MCP Streamable HTTP transport.

**Verification Chain (priority order):**
1. **API Key** (`icdev_*` prefix) — SHA-256 hash lookup against `platform.db` API keys table. Most common in ICDEV deployments.
2. **Offline HMAC** (`hmac_*` prefix) — HMAC-SHA256 signed payload with expiry. Air-gap safe, no database or network required.
3. **JWT** (3-part dot-separated) — Payload decode with expiry check. Full JWKS verification delegated to API gateway.

**Token Format (HMAC offline):**
```
hmac_<base64url(payload)>.<base64url(signature)>
```
Payload contains: `sub`, `email`, `role`, `scopes`, `tenant_id`, `iat`, `exp`, `jti`.

**Caching:** Verification results cached by SHA-256 hash of token with 5-minute TTL to reduce repeated database lookups.

**Scopes:** `mcp:read`, `mcp:write`, `mcp:execute` — granular permission control for MCP tool invocations.

### Component 4: MCP Elicitation Handler (`MCPElicitationHandler`)

Allows MCP tools to pause execution and request user input.

**Elicitation Types:**
| Type | Description |
|------|-------------|
| `text` | Free-form text input |
| `choice` | Select from predefined options |
| `confirm` | Yes/no confirmation |

**Lifecycle:** `create_elicitation()` -> pending -> `resolve_elicitation(id, response)` -> resolved. Tools check `get_pending()` for outstanding requests.

### Component 5: MCP Task Manager (`MCPTaskManager`)

Wraps long-running MCP tool invocations as trackable tasks with lifecycle management.

**Task States:**
```
created -> running (with progress 0-100%) -> completed | failed
```

**Operations:**
| Method | Description |
|--------|-------------|
| `create_task(tool, params)` | Create task, returns task_id |
| `update_progress(id, pct)` | Update progress percentage |
| `complete_task(id, result)` | Mark complete with result payload |
| `fail_task(id, error)` | Mark failed with error message |
| `get_task(id)` | Get current task status |
| `list_tasks(status)` | List tasks, optionally filtered |

---

## 5. Database

### Existing Tables Used

| Table | Database | Usage |
|-------|----------|-------|
| `agent_registry` | `data/icdev.db` | Agent health status and heartbeat for discovery server health-aware routing |
| `api_keys` | `data/platform.db` | API key hash lookup for MCP OAuth API key verification mode |
| `users` | `data/platform.db` | User email and role lookup joined with API keys |

No new database tables are created by Phase 55. Agent Cards are generated dynamically from `agent_config.yaml`. Elicitation and task state are held in-memory (stateless per request cycle). HMAC tokens are self-contained and verified without database access.

---

## 6. Configuration

### `args/agent_config.yaml` (existing, extended)

Agent definitions now consumed by the Agent Card generator. Each agent entry contributes `port`, `host`, `id`, `description`, and optional `streaming` flag to the v0.3 Agent Card.

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `ICDEV_MCP_OAUTH_SECRET` | HMAC secret key for offline token signing/verification | Falls back to `ICDEV_DASHBOARD_SECRET` |
| `ICDEV_DASHBOARD_SECRET` | Fallback HMAC secret | Auto-generated if not set |

---

## 7. Dashboard

Phase 55 does not introduce new dashboard pages. Agent discovery and health information is surfaced through:

- `/agents` page — Existing agent registry with heartbeat age (Phase 10)
- `/traces` page — A2A distributed trace visualization (Phase 46)

Discovery server data is available via CLI and MCP tools for programmatic consumption.

---

## 8. Security Gates

A2A v0.3 and MCP OAuth integrate with existing security gates:

- **Remote Command Gate** — User binding required before any command execution; MCP OAuth token verification enforces identity chain (D136)
- **A2A mutual TLS** — All inter-agent communication uses mTLS within K8s cluster; Agent Cards declare `mutual_tls` as authentication scheme
- **Token expiry enforcement** — All three verification modes (JWT, API key, HMAC) check token expiry; expired tokens are rejected
- **HMAC tamper detection** — Offline tokens use HMAC-SHA256 with constant-time comparison (`hmac.compare_digest`) to prevent timing attacks
- **Scope-based access** — MCP tools require appropriate scopes (`mcp:read`, `mcp:write`, `mcp:execute`) verified from token payload

No new gate added to `args/security_gates.yaml` — Phase 55 operates within the existing authentication and authorization framework established by Phase 21 (SaaS) and Phase 28 (Remote Command Gateway).

---

## 9. Verification

```bash
# A2A v0.3 Agent Card generation
python tools/agent/a2a_agent_card_generator.py --all --json         # Generate all 15 agent cards
python tools/agent/a2a_agent_card_generator.py --agent-id builder --json  # Single agent card
python tools/agent/a2a_agent_card_generator.py --list --json        # List agents summary

# A2A v0.3 Discovery Server
python tools/agent/a2a_discovery_server.py --list --json            # Discover all agents with health
python tools/agent/a2a_discovery_server.py --find-skill ssp_generate --json  # Skill-based lookup
python tools/agent/a2a_discovery_server.py --find-capability taskSubscription --json  # Capability filter
python tools/agent/a2a_discovery_server.py --summary --json         # Discovery landscape summary

# MCP OAuth 2.1 verification (programmatic)
python -c "
from tools.saas.mcp_oauth import MCPOAuthVerifier
v = MCPOAuthVerifier()
token = v.generate_offline_token('user-1', 'admin@icdev.local', 'admin')
result = v.verify_token(token)
print(f'Verified: {result[\"verified\"]}, Method: {result[\"method\"]}, Role: {result[\"role\"]}')
"

# MCP Elicitation (programmatic)
python -c "
from tools.saas.mcp_oauth import MCPElicitationHandler
h = MCPElicitationHandler()
req = h.create_elicitation('ssp_generate', 'Select impact level', options=['IL4','IL5','IL6'], input_type='choice')
print(f'Elicitation: {req[\"elicitation_id\"]}, Status: {req[\"status\"]}')
resolved = h.resolve_elicitation(req['elicitation_id'], 'IL5')
print(f'Resolved: {resolved[\"status\"]}, Response: {resolved[\"response\"]}')
"

# MCP Tasks (programmatic)
python -c "
from tools.saas.mcp_oauth import MCPTaskManager
tm = MCPTaskManager()
task = tm.create_task('sbom_generate', {'project_id': 'proj-123'})
print(f'Task: {task[\"task_id\"]}, Status: {task[\"status\"]}')
tm.update_progress(task['task_id'], 50, 'running')
tm.complete_task(task['task_id'], {'sbom_path': '/tmp/sbom.json'})
print(f'Final: {tm.get_task(task[\"task_id\"])[\"status\"]}')
"
```

---

## 10. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D344 | A2A v0.3 adds `capabilities` to Agent Card and `tasks/sendSubscribe` for streaming. Backward compatible via `protocolVersion` field. | v0.2 clients ignore unknown fields; v0.3 clients use capabilities for intelligent routing. Discovery server provides skill-based and capability-based agent lookup without hardcoded routing tables. |
| D345 | MCP OAuth 2.1 reuses existing SaaS auth middleware. Supports offline HMAC token verification for air-gap. | No new auth stack — reuses Phase 21 API key infrastructure (SHA-256 hash lookup), extends with HMAC offline tokens for IL5/IL6 air-gapped deployments. JWT verification degrades gracefully when JWKS endpoint unavailable. |
| D346 | MCP Elicitation allows tools to request user input mid-execution. MCP Tasks wraps long-running tools with create/progress/complete lifecycle. | Elicitation supports interactive compliance workflows (e.g., selecting impact level during SSP generation). Task lifecycle enables progress tracking for operations that span minutes (e.g., full SBOM generation, Monte Carlo simulation). Both use in-memory state — no new database tables. |

---

## 11. Files

### New Files (3)
| File | LOC | Purpose |
|------|-----|---------|
| `tools/agent/a2a_agent_card_generator.py` | ~285 | A2A v0.3 Agent Card generation for all 15 agents |
| `tools/agent/a2a_discovery_server.py` | ~250 | Centralized agent discovery with health-aware routing |
| `tools/saas/mcp_oauth.py` | ~400 | MCP OAuth 2.1 verifier, elicitation handler, task manager |

### Modified Files (5)
| File | Change |
|------|--------|
| `tools/mcp/tool_registry.py` | +A2A discovery and MCP OAuth tool entries |
| `tools/mcp/gap_handlers.py` | +Handler functions for discovery/oauth tools |
| `CLAUDE.md` | +D344-D346, +Phase 55 commands, +A2A v0.3 goal entry |
| `tools/manifest.md` | +A2A v0.3 and MCP OAuth section |
| `goals/manifest.md` | +A2A v0.3 goal entry |
