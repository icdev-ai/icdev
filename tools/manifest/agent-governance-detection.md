# Agent Governance — Detection (AGOV / DET)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Normalized Agent Event View (agov-det-01)

`tools/agent_detect/events.py` is a READ-ONLY projection of the agent activity
ICDEV already stores into one `AgentEvent` shape. It creates **no table** and
issues **no write** — every source is one of the five tables ICDEV writes
already: `hook_events`, `agent_executions`, `ai_telemetry`, `audit_trail`,
`ace_audit_log`.

Event types are mutually exclusive and one source row yields at most one event:
`command.exec`, `file.read`, `file.write`, `file.delete`, `network.indicator`,
`tool.call`. A recognized shell request is `command.exec` and never additionally
`tool.call`; an unrecognized tool stays `tool.call`.

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Event View | tools/agent_detect/events.py | Read-only normalizer over the five agent-activity tables. `fetch_events()` / `iter_events()` return `AgentEvent` records; a source table that does not exist yet is skipped with a warning, not a failure. | session_id, sources, since, until, limit, event_types | `list[AgentEvent]` |
| Event Classifier | tools/agent_detect/events.py::classify | Pure, structured-only classification of one tool invocation → `(event_type, confidence, operands)`. No regex over any payload string; reads only the keys in `OPERAND_KEYS`. | tool_name, payload | `(str, str, dict)` |
| MCP Name Split | tools/agent_detect/events.py::split_mcp_tool | Recovers `(mcp_server, mcp_tool)` from a structured `mcp__<server>__<tool>` name. Splits on the first two separators, so underscored server names and `__`-bearing tool names both survive. | tool_name | `(str\|None, str\|None)` |
| Event Summary | tools/agent_detect/events.py::summarize | Counts by event type, source and confidence. | `Sequence[AgentEvent]` | `{total, by_event_type, by_source, by_confidence}` |

### The two honesty invariants (enforced in code, not by convention)

| Invariant | Where it is enforced | What it prevents |
|-----------|---------------------|------------------|
| (a) classification never reads free text | `_structured()` **raises** on any key in `FREE_TEXT_KEYS` (`output_summary`, `message`, `details`, `content`, `stdout`, …) | A command in tool OUTPUT, an attacker-supplied file body, or audit narrative being read as evidence that the action happened |
| (b) a promoted event carries the operand that justified it | `AgentEvent.__post_init__` rejects `command.exec` without a `command`, `file.*` without a `file_path`, `network.indicator` without a `url` | "Promote first, hope the operand shows up" — an ambiguous payload stays `tool.call` |

### Confidence vocabulary

Three named levels, each with a checkable definition — not a score.

| Level | Meaning |
|-------|---------|
| `direct` | The tool is known by name and the operand came from that tool's own documented input field (`Bash.command`, `Read.file_path`, `WebFetch.url`) |
| `derived` | The tool was recognized through the shared `command_tools` generic-executor list in `args/agent_approval_policy.yaml` rather than an exact entry |
| `declared` | The row names a tool, agent or audited action and nothing more — no operand, so no promotion |

`CONFIDENCE_RANK` orders them for rules that want "at least this direct".

### Source-specific mappings

| Source | Session correlator | Notes |
|--------|-------------------|-------|
| `hook_events` | `session_id` | The only source that can be promoted past `tool.call`, and only when the payload carries a structured operand. No actor column — `actor` stays `None` unless the payload names one. |
| `agent_executions` | `execution_id` | Always `tool.call`. `status` → `exit_code` for `completed`/`failed` only; `started`/`retried`/`timeout` get `None`, never a stand-in. |
| `ai_telemetry` | `agent_id` | Always `tool.call`. `function` → `tool_name`, `model_id` → `model`. |
| `audit_trail` | `session_id` | Always `tool.call`. `action` is read; the narrative `details` column never is. |
| `ace_audit_log` | `instance_id` | Always `tool.call`. `instance_id` is the ACE session correlator. |

### CLI

```bash
python tools/agent_detect/events.py --json --limit 20
python tools/agent_detect/events.py --session <session_id> --json
python tools/agent_detect/events.py --source hook_events --event-type command.exec --json
python tools/agent_detect/events.py --summary --json
python -m tools.agent_detect.events --since 2026-08-01 --json
```

### Known upstream gap

`.claude/hooks/post_tool_use.py` persists `tool_input_keys` (the key NAMES) and
`output_summary`, never the values. Measured against the live `hook_events`
table on 2026-08-09: 2726 `post_tool_use` rows, 2194 of them `Bash`, and not one
carries a `command`. The normalizer is therefore correct but currently emits
`tool.call` for every live hook row — no rule over `command.exec` can fire until
a writer persists the structured operand. Fixing that means editing the hook,
which is the MANUAL-ONLY surface the AGOV card gates; it belongs to a follow-on
DET task, not here.
