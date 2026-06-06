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

