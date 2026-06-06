# Agent Execution Framework (Phase 39)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Execution Framework (Phase 39)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Executor | tools/agent/agent_executor.py | Subprocess-based Claude Code CLI invocation with JSONL parsing, retry, audit | --prompt, --model, --max-retries, --timeout, --json | AgentPromptResponse |
| Agent Models | tools/agent/agent_models.py | Dataclasses: AgentPromptRequest, AgentPromptResponse, RetryCode enum | — | — |
| Skill Selector | tools/agent/skill_selector.py | Selective skill injection: keyword-based category matching, file detection, context-aware skill/goal/context loading (D146) | --query, --detect, --project-dir, --resolve, --format-context, --json | Matched categories + commands + goals |
| Context Pressure | tools/agent/context_pressure.py | Context pressure monitor & stuck detection guard (GSD-adapted): token estimation, 3-level pressure alerts (normal/warning/critical), analysis paralysis detection, duplicate loop detection, combined health check (D-GSD-4 through D-GSD-6) | --check pressure/stuck/health, --session-id, --json, --human | Pressure level + stuck status |

