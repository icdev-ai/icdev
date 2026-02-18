# CUI // SP-CTI
# Observability Goal — Hook-Based Agent Monitoring

## Purpose
Provide real-time visibility into agent execution, tool usage, and session lifecycle
through hook-based event capture, HMAC-signed audit trail, and optional SIEM forwarding.

## Trigger
- Automatically active when hooks are enabled in `.claude/settings.json`
- Agent executor invoked via `python tools/agent/agent_executor.py`

## Workflow

### 1. Hook Event Capture
All Claude Code hooks fire automatically:
- `pre_tool_use.py` — Blocks dangerous operations (existing)
- `post_tool_use.py` — Logs tool results to `hook_events` table
- `notification.py` — Logs user notifications
- `stop.py` — Captures session completion
- `subagent_stop.py` — Logs subagent task results

### 2. Event Storage
- All events stored in `hook_events` table (append-only)
- HMAC-SHA256 signature for tamper detection
- Payload truncated to 2000 chars to prevent DB bloat

### 3. Agent Execution
- `tools/agent/agent_executor.py` invokes Claude Code CLI as subprocess
- JSONL output parsed into structured response
- Retry logic: configurable delays [1, 3, 5] seconds
- Safe environment: only allowlisted env vars passed
- All executions logged to `agent_executions` table

### 4. SIEM Forwarding (Optional)
- Events forwarded to Splunk/ELK via HTTP POST
- Backlog buffer for offline/disconnected scenarios
- Configure via `args/observability_config.yaml`

### 5. Dashboard Integration
- Events streamed to dashboard via SSE (Server-Sent Events)
- Real-time timeline of agent activity
- Filter by hook type, tool name, session

## Tools Used
| Tool | Purpose |
|------|---------|
| `send_event.py` | Shared event utility (store + forward) |
| `agent_executor.py` | Subprocess-based CLI agent invocation |
| `agent_models.py` | Data models (request, response, retry codes) |

## Args
- `args/observability_config.yaml` — Hook, executor, dashboard, SIEM settings

## Success Criteria
- Every tool use generates a `hook_events` row
- Agent executions logged with tokens, duration, status
- HMAC signatures verifiable for tamper detection
- Dashboard SSE stream reflects events within 2 seconds

## Edge Cases
- Database not initialized → hooks silently skip (exit 0)
- Dashboard not running → SSE forward fails silently
- Agent CLI not in PATH → fatal error, no retry
- Rate limited → retry with exponential backoff
