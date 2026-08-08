# Agent Adapters (OPT-71)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Adapters (OPT-71)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Adapter Base | tools/agents/adapter_base.py | Core protocol + dataclasses for the agent adapter pattern (OPT-71). Defines `AgentAdapter` (Protocol: available, prepare_prompt, invoke, detect_completion, parse_response), `AgentSession` (task_id, prompt, working_dir, max_turns, timeout_seconds, auth), `AgentResult` (completed, exit_code, output, duration_ms, structured), and `NotInstalledError`. One layer above tools/llm/*_provider.py — wraps a full multi-turn agent session, not a single LLM call. | (library — imported by adapter implementations) | AgentAdapter Protocol, AgentSession, AgentResult dataclasses |
| Claude CLI Adapter | tools/agents/adapters/claude_cli.py | THE claude-CLI shellout (hgx-exec-03 folded the kanban runner's hardened copy in here and deleted the thin duplicate). `resolve_claude_cli()` = `shutil.which` first (PATHEXT-aware on Windows) then a `~/.local/bin/claude` secondary probe. `spawn(session, stdout, stderr)` returns a live `subprocess.Popen` for callers with their own poll/kill loop; `invoke(session)` blocks and returns an `AgentResult`. Both share one argv/env/stdin construction: `--dangerously-skip-permissions --max-turns N --output-format text`, optional `--model` from `session.metadata["model_id"]`, `ICDEV_DISPATCH_SOURCE`/`_TASK_ID` env tags from `metadata["dispatch_source"]`, and the prompt piped from a temp file so a long instruction cannot trip the Windows 32767-char command-line limit. | AgentSession (metadata: model_id, dispatch_source, env, temp_dir, cleanup_delay_seconds) | subprocess.Popen (spawn) / AgentResult (invoke) |


