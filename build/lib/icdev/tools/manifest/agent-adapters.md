# Agent Adapters (OPT-71)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Adapters (OPT-71)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Adapter Base | tools/agents/adapter_base.py | Core protocol + dataclasses for the agent adapter pattern (OPT-71). Defines `AgentAdapter` (Protocol: available, prepare_prompt, invoke, detect_completion, parse_response), `AgentSession` (task_id, prompt, working_dir, max_turns, timeout_seconds, auth), `AgentResult` (completed, exit_code, output, duration_ms, structured), and `NotInstalledError`. One layer above tools/llm/*_provider.py — wraps a full multi-turn agent session, not a single LLM call. | (library — imported by adapter implementations) | AgentAdapter Protocol, AgentSession, AgentResult dataclasses |


