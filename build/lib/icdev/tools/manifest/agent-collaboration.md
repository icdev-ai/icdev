# Agent Collaboration (Opus 4.6 Multi-Agent — Phase C)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Collaboration (Opus 4.6 Multi-Agent — Phase C)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Collaboration | tools/agent/collaboration.py | 5 patterns: reviewer, debate, consensus, veto, escalation | --pattern, --agent-ids, --project-id, --json | Pattern result |
| Authority | tools/agent/authority.py | Domain authority matrix (YAML): check_authority, record_veto, record_override | --check, --veto, --override, --history, --json | Veto status |
| Mailbox | tools/agent/mailbox.py | HMAC-SHA256 signed inter-agent messaging: send, broadcast, receive, verify | --send, --inbox, --verify, --json | Messages |
| Agent Memory | tools/agent/agent_memory.py | Project-scoped per-agent + team memory: store, recall, inject context, prune | --store, --recall, --inject, --prune, --json | Memory entries |
| Agent Topology | tools/agent/topology.py | Agent topology: graph-based dependency mapping of providers/models/functions/agents, SPOF detection, air-gap analysis | --map, --spof, --air-gap, --visualize, --json | Topology graph + SPOF report |

