# Multi-Agent System Architecture Guide

CUI // SP-CTI

## Overview

ICDEV operates a 15-agent multi-agent system organized into 3 tiers: Core (2 agents), Domain (11 agents), and Support (2 agents). Agents communicate via the A2A protocol (JSON-RPC 2.0 over mutual TLS) within a Kubernetes cluster. Each agent publishes an Agent Card for service discovery. Claude Code interacts with the system through 19 MCP servers using stdio transport.

---

## Agent Topology

```
                        +---------------------------+
                        |     CORE TIER             |
                        |                           |
                        |  +-------------------+    |
                        |  | Orchestrator:8443 |    |
                        |  | Task routing,     |    |
                        |  | workflow mgmt     |    |
                        |  +--------+----------+    |
                        |           |               |
                        |  +--------v----------+    |
                        |  | Architect:8444     |    |
                        |  | ATLAS/M-ATLAS,    |    |
                        |  | system design     |    |
                        |  +-------------------+    |
                        +----------+----------------+
                                   |
                +------------------+------------------+
                |                                     |
    +-----------v-----------+         +---------------v---------+
    |     DOMAIN TIER       |         |     SUPPORT TIER        |
    |                       |         |                         |
    | Builder:8445          |         | Knowledge:8449          |
    | Compliance:8446       |         |  Self-healing, ML,      |
    | Security:8447         |         |  recommendations        |
    | Infrastructure:8448   |         |                         |
    | MBSE:8451             |         | Monitor:8450            |
    | Modernization:8452    |         |  Log analysis, metrics, |
    | Requirements:8453     |         |  alerts, health         |
    | Supply Chain:8454     |         +-------------------------+
    | Simulation:8455       |
    | DevSecOps & ZTA:8457  |
    | Gateway:8458          |
    +---+-------------------+
```

### Agent Registry

| Tier | Agent | Port | Primary Role | Effort Level |
|------|-------|------|-------------|--------------|
| Core | **Orchestrator** | 8443 | Task routing, workflow management, DAG execution | high |
| Core | **Architect** | 8444 | ATLAS/M-ATLAS Architecture and Trace phases, system design | high |
| Domain | **Builder** | 8445 | TDD code generation (RED-GREEN-REFACTOR), scaffolding | max |
| Domain | **Compliance** | 8446 | ATO artifacts (SSP, POAM, STIG, SBOM, FedRAMP, CMMC, OSCAL, eMASS, cATO) | high |
| Domain | **Security** | 8447 | SAST, dependency audit, secret detection, container scanning | high |
| Domain | **Infrastructure** | 8448 | Terraform, Ansible, K8s manifests, pipeline generation | high |
| Domain | **MBSE** | 8451 | SysML parsing, DOORS NG, digital thread, model-code sync, DES compliance | high |
| Domain | **Modernization** | 8452 | Legacy analysis, 7R assessment, migration planning, code generation | high |
| Domain | **Requirements Analyst** | 8453 | Conversational intake, gap detection, SAFe decomposition, readiness scoring | high |
| Domain | **Supply Chain** | 8454 | Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage, SCRM | high |
| Domain | **Simulation** | 8455 | Digital Program Twin, 6-dimension simulation, Monte Carlo, COA generation | high |
| Domain | **DevSecOps & ZTA** | 8457 | DevSecOps pipeline security, Zero Trust (NIST 800-207), policy-as-code, service mesh | high |
| Domain | **Gateway** | 8458 | Remote command reception (Telegram, Slack, Teams, Mattermost), 8-gate security | high |
| Support | **Knowledge** | 8449 | Self-healing patterns, ML-based analysis, recommendations | low |
| Support | **Monitor** | 8450 | Log analysis, metrics collection, alerts, health checks | low |

---

## A2A Protocol

Agents communicate using the Agent-to-Agent (A2A) protocol built on JSON-RPC 2.0 over mutual TLS within the Kubernetes cluster.

### Protocol Specifications

```
Transport:       HTTPS with mutual TLS (mTLS)
Message Format:  JSON-RPC 2.0
Authentication:  X.509 client certificates (per-agent)
Discovery:       Agent Cards at /.well-known/agent.json
Tracing:         W3C traceparent header propagation (D281, D285)
Signing:         HMAC-SHA256 for mailbox messages (D41)
```

### Agent Card Structure

Each agent publishes a discovery document at `/.well-known/agent.json`:

```json
{
  "name": "builder-agent",
  "version": "1.0.0",
  "port": 8445,
  "capabilities": ["scaffold", "generate_code", "write_tests", "run_tests"],
  "health_endpoint": "/health",
  "tier": "domain",
  "domain_authority": [],
  "effort_level": "max"
}
```

### Communication Flow

```
Agent A                                          Agent B
   |                                                |
   |  1. Resolve Agent Card                         |
   |  GET /.well-known/agent.json  ----------------->
   |  <----- 200 OK (capabilities, health) ---------|
   |                                                |
   |  2. Send Task (JSON-RPC 2.0 + mTLS)           |
   |  POST /rpc                                     |
   |  {                                             |
   |    "jsonrpc": "2.0",                           |
   |    "method": "generate_code",                  |
   |    "params": { ... },                          |
   |    "id": "task-uuid",                          |
   |    "metadata": {                               |
   |      "traceparent": "00-trace...",             |
   |      "correlation_id": "abc123..."             |
   |    }                                           |
   |  }  ------------------------------------------>|
   |                                                |
   |  3. Receive Result                             |
   |  <---- { "jsonrpc":"2.0", "result":{...} } ----|
   |                                                |
```

### Distributed Tracing

A2A calls propagate W3C `traceparent` headers (D285):

```
traceparent: 00-<trace-id>-<span-id>-01

- 3-line addition to agent_client.py (inject traceparent)
- 3-line addition to agent_server.py (extract and continue trace)
- Enables end-to-end trace visualization across all 15 agents
```

---

## MCP Servers

ICDEV exposes 19 MCP servers using stdio transport for Claude Code integration. All servers are configured in `.mcp.json` at the project root.

### Unified MCP Gateway (D301) — Recommended

The **`icdev-unified`** gateway aggregates all 225 tools from 18 domain servers plus 55 new tool wrappers into a single MCP server process. This reduces `.mcp.json` from 18+ entries to 1 while giving all AI coding tools (not just Claude Code) access to every ICDEV capability.

**Architecture:**
- **Declarative registry** (`tool_registry.py`): Maps tool name to (module, handler, schema)
- **Lazy loading**: Handlers imported via `importlib.import_module()` only on first call, cached thereafter
- **Gap handlers** (`gap_handlers.py`): 55 new handler functions for previously CLI-only tools (translation, dx, cloud, registry, security, testing, installer)
- **D284 auto-instrumentation**: Inherited from `base_server.py` — all 225 tools traced automatically

```json
"icdev-unified": {
    "command": "python",
    "args": ["tools/mcp/unified_server.py"],
    "env": { "ICDEV_DB_PATH": "data/icdev.db", "ICDEV_PROJECT_ROOT": "." }
}
```

The 18 individual servers remain independently runnable for backward compatibility and targeted debugging.

### Individual Server Registry

| # | Server | Config Key | Transport | Key Tools |
|---|--------|-----------|-----------|-----------|
| 1 | **Playwright** | `playwright` | stdio (npx) | Browser automation for E2E testing |
| 2 | **Core** | `icdev-core` | stdio (python) | `project_create`, `project_list`, `project_status`, `task_dispatch`, `agent_status` |
| 3 | **Compliance** | `icdev-compliance` | stdio (python) | `ssp_generate`, `poam_generate`, `stig_check`, `sbom_generate`, `cui_mark`, `control_map`, `nist_lookup`, `crosswalk_query`, `fedramp_assess`, `cmmc_assess`, `oscal_generate`, `emass_sync`, `cato_monitor`, `fips199_categorize`, `fips200_validate`, `classification_check`, + more |
| 4 | **Builder** | `icdev-builder` | stdio (python) | `scaffold`, `generate_code`, `write_tests`, `run_tests`, `lint`, `format`, `dev_profile_create`, `dev_profile_get`, `dev_profile_resolve`, `dev_profile_detect` |
| 5 | **Infrastructure** | `icdev-infra` | stdio (python) | `terraform_plan`, `terraform_apply`, `ansible_run`, `k8s_deploy`, `pipeline_generate`, `rollback` |
| 6 | **Knowledge** | `icdev-knowledge` | stdio (python) | `search_knowledge`, `add_pattern`, `get_recommendations`, `analyze_failure`, `self_heal` |
| 7 | **Maintenance** | `icdev-maintenance` | stdio (python) | `scan_dependencies`, `check_vulnerabilities`, `run_maintenance_audit`, `remediate` |
| 8 | **MBSE** | `icdev-mbse` | stdio (python) | `import_xmi`, `import_reqif`, `trace_forward`, `trace_backward`, `generate_code`, `detect_drift`, `sync_model`, `des_assess`, `thread_coverage`, `model_snapshot` |
| 9 | **Modernization** | `icdev-modernization` | stdio (python) | `register_legacy_app`, `analyze_legacy`, `assess_seven_r`, `create_migration_plan`, `generate_migration_code`, `check_compliance_bridge`, `migrate_version` |
| 10 | **Requirements** | `icdev-requirements` | stdio (python) | `create_intake_session`, `resume_intake_session`, `process_intake_turn`, `upload_document`, `detect_gaps`, `score_readiness`, `decompose_requirements`, `generate_bdd` |
| 11 | **Supply Chain** | `icdev-supply-chain` | stdio (python) | `register_ato_system`, `assess_boundary_impact`, `generate_red_alternative`, `add_vendor`, `build_dependency_graph`, `propagate_impact`, `manage_isa`, `assess_scrm`, `triage_cve` |
| 12 | **Simulation** | `icdev-simulation` | stdio (python) | `create_scenario`, `run_simulation`, `run_monte_carlo`, `generate_coas`, `compare_coas`, `select_coa`, `manage_scenarios` |
| 13 | **Integration** | `icdev-integration` | stdio (python) | `configure_jira`, `sync_jira`, `configure_servicenow`, `sync_servicenow`, `configure_gitlab`, `sync_gitlab`, `export_reqif`, `submit_approval`, `build_traceability` |
| 14 | **Marketplace** | `icdev-marketplace` | stdio (python) | `publish_asset`, `install_asset`, `search_assets`, `list_assets`, `review_asset`, `check_compat`, `sync_status`, `asset_scan` |
| 15 | **DevSecOps** | `icdev-devsecops` | stdio (python) | `devsecops_profile_create`, `zta_maturity_score`, `zta_assess`, `pipeline_security_generate`, `policy_generate`, `service_mesh_generate`, `attestation_verify`, `zta_posture_check` |
| 16 | **Gateway** | `icdev-gateway` | stdio (python) | `bind_user`, `list_bindings`, `revoke_binding`, `send_command`, `gateway_status` |
| 17 | **Innovation** | `icdev-innovation` | stdio (python) | `scan_web`, `score_signals`, `triage_signals`, `detect_trends`, `generate_solution`, `run_pipeline`, `introspect`, `competitive_scan`, `standards_check` |
| 18 | **Context** | `icdev-context` | stdio (python) | `fetch_docs`, `list_sections`, `get_icdev_metadata`, `get_project_context`, `get_agent_context` |
| 19 | **Observability** | `icdev-observability` | stdio (python) | `trace_query`, `trace_summary`, `prov_lineage`, `prov_export`, `shap_analyze`, `xai_assess` |

### MCP Server Configuration

All servers share a common environment:

```json
{
  "env": {
    "ICDEV_DB_PATH": "data/icdev.db",
    "ICDEV_PROJECT_ROOT": "."
  }
}
```

### MCP Auto-Instrumentation (D284)

All 15 Python MCP servers are instrumented at `base_server.py._handle_tools_call()`. A single code change instruments every MCP tool call with:
- Span creation (tool name, parameters)
- Duration tracking
- Error recording
- Trace context propagation

---

## Multi-Agent Orchestration

### DAG Workflow Execution

The Orchestrator decomposes complex tasks into a Directed Acyclic Graph (DAG) and executes them with maximum parallelism.

```
Task: "Build secure microservice with compliance"

                    +------------------+
                    | Orchestrator     |
                    | decompose task   |
                    +--------+---------+
                             |
              +--------------+--------------+
              |                             |
    +---------v---------+       +-----------v---------+
    | Architect:8444    |       | Requirements:8453   |
    | System design     |       | Intake analysis     |
    +---------+---------+       +-----------+---------+
              |                             |
              +--------------+--------------+
                             |
              +--------------+--------------+
              |              |              |
    +---------v----+ +------v------+ +-----v--------+
    | Builder:8445 | | Security    | | Compliance   |
    | TDD code gen | | :8447 SAST  | | :8446 SSP    |
    +----------+---+ +------+------+ +-----+--------+
               |            |              |
               +------+-----+------+------+
                      |            |
            +---------v----+ +-----v--------+
            | Infra:8448   | | Monitor:8450 |
            | Terraform    | | Health check |
            +--------------+ +--------------+
```

**Implementation**: `graphlib.TopologicalSorter` (Python stdlib 3.9+, D40) for DAG scheduling. Air-gap safe, zero dependencies, cycle detection built-in.

### Parallel Execution

```python
# Conceptual flow (tools/agent/team_orchestrator.py)
from graphlib import TopologicalSorter

dag = TopologicalSorter(task_graph)
dag.prepare()

while dag.is_active():
    ready_tasks = dag.get_ready()
    # Execute ready tasks in parallel via ThreadPoolExecutor
    results = executor.map(dispatch_to_agent, ready_tasks)
    for task in ready_tasks:
        dag.done(task)
```

### Commands

```bash
# Decompose task into DAG
python tools/agent/team_orchestrator.py --decompose "task description" --project-id "proj-123"

# Execute workflow
python tools/agent/team_orchestrator.py --execute --workflow-id "wf-123"

# Route skill to healthy agent
python tools/agent/skill_router.py --route-skill "ssp_generate"

# Check agent health
python tools/agent/skill_router.py --health
```

---

## Domain Authority and Vetoes

Domain authority prevents agents from making decisions outside their expertise. Vetoes are recorded in an append-only table for audit compliance.

### Authority Matrix

Defined in `args/agent_authority.yaml`:

| Agent | Authority Type | Domains | Effect |
|-------|---------------|---------|--------|
| **Security** | Hard veto | `code_generation`, `dependency_management`, `infrastructure` | Blocks task. Cannot be overridden. |
| **Compliance** | Hard veto | `artifact_generation`, `deployment`, `deploy_gate` | Blocks task. Cannot be overridden. |
| **Architect** | Soft veto | `system_design`, `api_design`, `data_model` | Warning. Can be overridden with justification. |

### Veto Flow

```
Builder proposes code change
    |
    v
Security Agent reviews (hard veto authority)
    |
    +-- PASS --> Continue to next gate
    |
    +-- VETO --> BLOCKED
                 |
                 v
              Record in agent_vetoes table (append-only)
              Notify Orchestrator
              Generate remediation guidance
```

### Veto Verification

```bash
# Check domain authority
python tools/agent/authority.py --check security-agent code_generation

# View veto history (read-only query)
python tools/audit/audit_query.py --project "proj-123" --format json
```

---

## Agent Mailbox

Each agent has a SQLite-based mailbox for asynchronous task delivery and inter-agent messaging.

### Design (D41)

- **Storage**: SQLite (air-gap safe)
- **Signing**: HMAC-SHA256 for tamper-evident messages
- **Append-only**: Satisfies NIST 800-53 AU audit requirements
- **Delivery**: Polling-based (no WebSocket dependency)

### Mailbox Operations

```bash
# Check agent inbox
python tools/agent/mailbox.py --inbox --agent-id "builder-agent"

# Send message to agent
python tools/agent/mailbox.py --send --to "security-agent" --message '{"task":"review","code_path":"/src"}'
```

### Message Structure

```json
{
  "id": "msg-uuid",
  "from_agent": "orchestrator-agent",
  "to_agent": "builder-agent",
  "message_type": "task_assignment",
  "payload": { ... },
  "timestamp": "2026-02-23T10:30:00Z",
  "hmac_signature": "sha256:abcdef...",
  "correlation_id": "corr-uuid"
}
```

---

## Agent Memory

Agent memory is scoped to prevent cross-project contamination (D43).

### Scoping Model

```
+-------------------------------------------+
|          Agent Memory Scoping             |
|                                           |
|  Scope Key: (agent_id, project_id)        |
|                                           |
|  builder-agent + proj-123                 |
|    --> Memories about proj-123's build    |
|                                           |
|  builder-agent + proj-456                 |
|    --> Memories about proj-456's build    |
|                                           |
|  _team + proj-123                         |
|    --> Shared team memories for proj-123  |
|                                           |
+-------------------------------------------+
```

### Commands

```bash
# Recall agent-specific memories
python tools/agent/agent_memory.py --recall --agent-id "builder-agent" --project-id "proj-123"

# Store team-shared memory
python tools/agent/agent_memory.py --store --agent-id "_team" --project-id "proj-123" --content "Decision: use PostgreSQL"
```

---

## Token Tracking and Cost Management

All LLM invocations are tracked per agent, per project, with cost attribution.

### Tracking Commands

```bash
# Token usage summary
python tools/agent/token_tracker.py --action summary --project-id "proj-123"

# Cost breakdown by agent
python tools/agent/token_tracker.py --action cost --project-id "proj-123"
```

### Cost Dashboard

The web dashboard at `/usage` provides per-user and per-provider cost visualization.

### BYOK (Bring Your Own Key)

Users can provide their own LLM API keys (D175-D178):
- Stored AES-256 encrypted (Fernet) in `dashboard_user_llm_keys` table
- Per-user keys override per-department env vars, which override system config
- Disabled by default (`ICDEV_BYOK_ENABLED=false`)

---

## Model Fallback Chain

ICDEV uses a cascading fallback chain for LLM model availability (D37):

```
Primary         Fallback 1         Fallback 2
+-----------+   +---------------+   +-----------------+
| Opus 4.6  |-->| Sonnet 4.5    |-->| Sonnet 3.5      |
+-----------+   +---------------+   +-----------------+

Health Probing:
- Cached health status with 30-minute TTL
- Background probing at configurable interval
- Automatic failover on 503/429/timeout
```

### Per-Agent Effort Mapping (D38)

Effort levels optimize cost vs. quality per agent role:

| Agent Role | Default Effort | Rationale |
|-----------|---------------|-----------|
| Orchestrator | `high` | Complex routing decisions |
| Builder | `max` | Code generation needs highest quality |
| Compliance | `high` | Accuracy critical for ATO |
| Security | `high` | False negatives are costly |
| Monitor | `low` | Pattern matching, alerting |
| Knowledge | `low` | Search and retrieval |

Configuration: `args/bedrock_models.yaml`

---

## Multi-Cloud LLM Routing (D228)

Agents can route LLM calls through multiple cloud providers:

```
+-------------------------------------------------------+
|                  LLM Router                           |
|                  (tools/llm/router.py)                |
|                                                       |
|  Function-level routing (D68):                        |
|  - NLQ queries --> fast/cheap model                   |
|  - Code generation --> strong coder model             |
|  - Compliance text --> accuracy-optimized model       |
|                                                       |
|  Providers:                                           |
|  +------------------+  +--------------------+         |
|  | Amazon Bedrock   |  | Azure OpenAI       |         |
|  | (Claude, Titan)  |  | (GPT-4o, o1)       |         |
|  +------------------+  +--------------------+         |
|  +------------------+  +--------------------+         |
|  | Vertex AI        |  | OCI GenAI          |         |
|  | (Gemini, Claude) |  | (Cohere, Llama)    |         |
|  +------------------+  +--------------------+         |
|  +------------------+  +--------------------+         |
|  | IBM watsonx.ai   |  | Ollama (local)     |         |
|  | (Granite, Llama) |  | (air-gapped)       |         |
|  +------------------+  +--------------------+         |
+-------------------------------------------------------+
```

Configuration: `args/llm_config.yaml`

Air-gapped environments set `prefer_local: true` to route all LLM calls through Ollama.

---

## Collaboration Patterns

### Reviewer Pattern

```bash
python tools/agent/collaboration.py --pattern reviewer --project-id "proj-123"
```

Flow: Builder generates code, Security reviews, Compliance validates, Architect approves design.

### Self-Healing Pattern

```
Monitor detects anomaly
    |
    v
Knowledge analyzes failure
    |
    +-- Confidence >= 0.7 --> Auto-remediate (max 5/hour)
    |
    +-- Confidence 0.3-0.7 --> Suggest fix, require human approval
    |
    +-- Confidence < 0.3 --> Escalate with full context
```

### Deployment Pattern

```
Builder completes code
    --> Security runs SAST + dep audit
        --> Compliance generates SSP + SBOM
            --> Infrastructure generates Terraform + K8s
                --> Monitor confirms health
                    --> DEPLOY (requires all gates pass)
```

---

## Deployment

### Docker Containers

All agents run in STIG-hardened containers:
- Read-only root filesystem
- Drop ALL capabilities
- Non-root user (UID 1000)
- Resource limits enforced
- Minimal base packages

### Kubernetes

```
k8s/
+-- namespace.yaml              # icdev namespace
+-- configmap.yaml              # Shared configuration
+-- secrets.yaml                # TLS certs, API keys
+-- network-policies.yaml       # Default deny + per-agent allow
+-- ingress.yaml                # External access
+-- hpa.yaml                    # Horizontal Pod Autoscalers (18)
+-- pdb.yaml                    # Pod Disruption Budgets (18)
+-- node-autoscaler.yaml        # Cluster Autoscaler reference
+-- <agent>-deployment.yaml     # Per-agent deployment + service
```

### Auto-Scaling (D141-D144)

```
Tier       | Min | Max | CPU Target | Memory Target
-----------|-----|-----|------------|---------------
Core       |  2  |  8  |    70%     |     80%
Domain     |  1  |  6  |    75%     |     85%
Support    |  1  |  4  |    80%     |     85%
Dashboard  |  2  |  10 |    60%     |     75%
API Gateway|  3  |  12 |    65%     |     80%
```

Pod Disruption Budgets:
- Core agents + dashboard + gateway: `minAvailable=1`
- Domain + support agents: `maxUnavailable=1`

---

## Agent Execution Commands

```bash
# Execute agent via CLI
python tools/agent/agent_executor.py --prompt "echo hello" --model sonnet --json

# Execute via Bedrock
python tools/agent/agent_executor.py --prompt "fix tests" --model opus --max-retries 3

# Probe Bedrock model availability
python tools/agent/bedrock_client.py --probe

# Invoke Bedrock directly
python tools/agent/bedrock_client.py --prompt "text" --model opus --effort high

# Streaming invocation
python tools/agent/bedrock_client.py --prompt "text" --stream
```
