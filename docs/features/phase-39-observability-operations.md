# Phase 39 — Observability & Operations

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 39 |
| Title | Observability & Operations — Hook-Based Agent Monitoring |
| Status | Implemented |
| Priority | P1 |
| Dependencies | Phase 28 (Remote Command Gateway), Phase 21 (SaaS Multi-Tenancy) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's 15-agent multi-agent architecture operates across MCP servers, A2A protocol channels, dashboard endpoints, and remote gateway channels. Without real-time visibility into agent execution, tool usage, and session lifecycle, operators cannot detect anomalies, audit agent behavior, or diagnose failures. Prior to Phase 39, tool invocations were fire-and-forget with no centralized event stream, making post-mortem analysis dependent on scattered log files.

The GOTCHA framework mandates that business logic remain deterministic while the AI orchestration layer handles probabilistic decisions. This separation requires an observability layer that captures every tool use event, agent execution result, and session lifecycle transition in an append-only, tamper-evident audit trail. Without HMAC-signed event integrity, the audit trail cannot satisfy NIST 800-53 AU controls for non-repudiation.

Furthermore, enterprise deployments require integration with existing SIEM platforms (Splunk, ELK) for centralized monitoring. The observability layer must forward events to external systems while maintaining air-gap compatibility through buffered offline operation and graceful degradation when SIEM endpoints are unreachable.

---

## 2. Goals

1. Capture every Claude Code hook event (pre_tool_use, post_tool_use, notification, stop, subagent_stop) in an append-only database table with HMAC-SHA256 tamper detection
2. Provide a subprocess-based agent execution framework that invokes Claude Code CLI with structured JSONL output, retry logic, and safe environment filtering
3. Forward events to enterprise SIEM platforms (Splunk, ELK) via HTTP POST with backlog buffering for disconnected scenarios
4. Stream events to the dashboard via SSE (Server-Sent Events) for real-time agent activity visualization
5. Maintain air-gap compatibility by storing all events locally first and forwarding opportunistically
6. Log all agent executions with token usage, duration, status, and session correlation for cost tracking and performance analysis

---

## 3. Architecture

```
Claude Code Hooks (.claude/hooks/)
  pre_tool_use.py ──┐
  post_tool_use.py ──┤
  notification.py ───┼──→ send_event.py ──→ hook_events table (append-only)
  stop.py ───────────┤         │                    │
  subagent_stop.py ──┘         │                    ↓
                               ├──→ SSE Manager ──→ Dashboard /events
                               │
                               └──→ SIEM Forwarder ──→ Splunk / ELK
                                        │
                                   Backlog Buffer (offline)

Agent Executor
  agent_executor.py ──→ Claude Code CLI (subprocess)
         │                    │
         ├──→ JSONL parse     │
         ├──→ Retry logic     │
         └──→ agent_executions table (append-only)
```

Hook events are captured at the Claude Code integration layer. Each hook fires a Python script that calls `send_event.py`, which stores the event in the `hook_events` table with an HMAC-SHA256 signature computed from the event payload and a shared secret (sourced from AWS Secrets Manager or environment variable). Events are simultaneously pushed to the SSE manager for real-time dashboard streaming and queued for SIEM forwarding.

The agent executor wraps Claude Code CLI invocations as subprocesses with JSONL output parsing, configurable retry delays, safe environment variable filtering (only allowlisted env vars passed), and structured result storage in the `agent_executions` table.

---

## 4. Requirements

### 4.1 Hook Event Capture

#### REQ-39-001: Post-Tool-Use Logging
The system SHALL log every tool use completion to the `hook_events` table, including tool name, input parameters (truncated to 2000 chars), output summary, duration, and session ID.

#### REQ-39-002: HMAC-SHA256 Event Signing
The system SHALL compute an HMAC-SHA256 signature for each event payload using a secret key sourced from the configured secrets provider. The signature SHALL be stored alongside the event for tamper detection verification.

#### REQ-39-003: Append-Only Event Storage
The `hook_events` table SHALL be append-only with no UPDATE or DELETE operations, satisfying NIST 800-53 AU-9 (Protection of Audit Information).

#### REQ-39-004: Payload Truncation
Event payloads SHALL be truncated to 2000 characters maximum to prevent database bloat from large tool outputs.

### 4.2 Agent Execution Framework

#### REQ-39-005: Subprocess-Based Agent Invocation
The agent executor SHALL invoke Claude Code CLI as a subprocess with `stdin=DEVNULL`, structured JSONL output parsing, and configurable timeout.

#### REQ-39-006: Retry Logic
The agent executor SHALL support configurable retry delays (default: [1, 3, 5] seconds) with exponential backoff for transient failures including rate limiting.

#### REQ-39-007: Safe Environment
The agent executor SHALL pass only allowlisted environment variables to the subprocess, preventing credential leakage.

#### REQ-39-008: Execution Logging
All agent executions SHALL be logged to the `agent_executions` table with token usage, duration, status, error details, and session correlation ID.

### 4.3 SIEM Forwarding

#### REQ-39-009: SIEM HTTP Forwarding
The system SHALL forward events to configured SIEM endpoints (Splunk HEC, ELK/Logstash) via HTTP POST with configurable URL, authentication, and batch size.

#### REQ-39-010: Backlog Buffering
When SIEM endpoints are unreachable, the system SHALL buffer events locally and forward them when connectivity is restored.

### 4.4 Dashboard Integration

#### REQ-39-011: SSE Event Streaming
The system SHALL stream events to the dashboard via Server-Sent Events with a connection status indicator and 3-second debounce batching (D99).

#### REQ-39-012: Event Timeline
The dashboard `/events` page SHALL display a real-time timeline of agent activity with filtering by hook type, tool name, and session.

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `hook_events` | Append-only storage of all Claude Code hook events with HMAC signatures, payload, tool name, hook type, session ID, timestamp |
| `agent_executions` | Append-only storage of all agent CLI invocations with token usage, duration, status, error details, model, prompt hash |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/agent/send_event.py` | Shared event utility — stores event in DB, forwards to SSE and SIEM |
| `tools/agent/agent_executor.py` | Subprocess-based Claude Code CLI invocation with retry, JSONL parsing, safe env |
| `tools/agent/agent_models.py` | Data models for agent request, response, retry codes, execution status |
| `.claude/hooks/post_tool_use.py` | Hook script — logs tool results to hook_events table |
| `.claude/hooks/notification.py` | Hook script — logs user notifications |
| `.claude/hooks/stop.py` | Hook script — captures session completion |
| `.claude/hooks/subagent_stop.py` | Hook script — logs subagent task results |
| `tools/dashboard/api/events.py` | Flask blueprint for SSE event streaming |
| `tools/dashboard/sse_manager.py` | SSE connection management and event dispatch |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D31 | HMAC-SHA256 event signing for hooks | Tamper detection without PKI overhead; secret via AWS Secrets Manager or env var |
| D35 | Agent executor stores JSONL output in `agents/` dir | Auditable, replayable, consistent with observability pattern |
| D29 | SSE over WebSocket for dashboard live updates | Flask-native, simpler, no additional deps, unidirectional sufficient |
| D99 | SSE live updates debounce to 3-second batches | Prevents API hammering while keeping dashboard near-real-time |
| D6 | Audit trail is append-only/immutable | No UPDATE/DELETE — NIST 800-53 AU compliance |

---

## 8. Security Gate

**Observability Gate:**
- Hook events table must have HMAC signatures verifiable for tamper detection
- Agent executions must be logged with token usage and duration for cost tracking
- SIEM forwarding must be configured for IL4+ deployments
- Dashboard SSE stream must reflect events within 2 seconds
- No plaintext secrets in agent executor environment (safe env filtering enforced)

---

## 9. Commands

```bash
# Agent execution
python tools/agent/agent_executor.py --prompt "echo hello" --model sonnet --json
python tools/agent/agent_executor.py --prompt "fix tests" --model opus --max-retries 3

# Dashboard events
# Start dashboard: python tools/dashboard/app.py
# Navigate to /events for real-time event timeline (SSE)

# Configuration
# args/observability_config.yaml — Hook, executor, dashboard, SIEM settings
```
