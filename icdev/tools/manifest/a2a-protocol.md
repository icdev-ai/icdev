# A2A Protocol

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## A2A Protocol
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| A2A Agent Server | tools/a2a/agent_server.py | Base A2A agent server (JSON-RPC 2.0 HTTPS) | — | — |
| A2A Client | tools/a2a/agent_client.py | Client for sending tasks to A2A agents | agent_url, skill_id, input | Task result |
| A2A Task Model | tools/a2a/task.py | Task, Artifact, StatusEvent dataclasses | — | — |
| Agent Registry | tools/a2a/agent_registry.py | Agent discovery and registration | — | Agent list |


## A2A Protocol (Additional)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Task Lease | tools/a2a/task_lease.py | Task lease management for agent coordination | --json | Lease status |
| A2A Agent Entrypoint | tools/a2a/agent_entrypoint.py | Container-aware agent bootstrap — reads agent card JSON + args/agent_config.yaml, registers all declared skills (stub or wired via `.agents/skills/` scan), auto-registers agent in DB for orchestrator discovery, and starts the A2A Flask server with optional mutual TLS | `--agent-id <id> --port <port> [--host 0.0.0.0] [--tls-cert <path>] [--tls-key <path>] [--tls-ca <path>] [--no-tls] [--debug] [--no-auto-register]` | Running A2A agent server |
| A2A Discovery Registry | tools/agents/a2a_registry.py | iii-inspired capability-first A2A discovery: register(name, port, capabilities), discover(capability) with prefix fallback, ping_all() health probe, seed_from_config() loads 15 agents from args/agent_config.yaml; module-level get_registry() singleton; replaces hard-coded port lookups with name-based capability index (adapt-iii-01/02). | --list, --discover CAPABILITY, --capabilities, --ping-all, --json | Agents list / discover result / ping summary |

