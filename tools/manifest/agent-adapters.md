# Agent Adapters (OPT-71)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Agent Adapters (OPT-71)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Agent Adapter Base | tools/agents/adapter_base.py | Core protocol + dataclasses for the agent adapter pattern (OPT-71). Defines `AgentAdapter` (Protocol: available, prepare_prompt, invoke, detect_completion, parse_response), `AgentSession` (task_id, prompt, working_dir, max_turns, timeout_seconds, auth), `AgentResult` (completed, exit_code, output, duration_ms, structured), and `NotInstalledError`. One layer above tools/llm/*_provider.py — wraps a full multi-turn agent session, not a single LLM call. | (library — imported by adapter implementations) | AgentAdapter Protocol, AgentSession, AgentResult dataclasses |
| Local Agent Adapter (hgx-exec-02) | tools/agents/adapters/local_agent.py | The OWNED file-editing agent path behind the AgentAdapter seam. Binds `run_agent_loop_with_rubric` (multi-turn native tool use) + `build_worktree_toolset` (read/list/grep/search/write/patch/git_diff/run_command, traversal-guarded) + `make_pipeline_grader` (ruff/coherence/pytest/conformance as the rubric), so `completed` is a gate verdict rather than the model's say-so. Unlike `local_llm_router` (single-shot prose, still correct for research/plan) it edits files. LLM-agnostic: routes by `llm_function` only — no model id in the module. On `AgentLoopUnsupported` (CLI bridge / `supports_tools: false`) it DEGRADES to the prose path with `structured['degraded_reason']` recorded and never raises. Select with `ICDEV_AGENT_ADAPTER=local_agent`. | `ADAPTER.invoke(AgentSession)` (library) | AgentResult (structured: satisfied, degraded, grades, turns, cost) |


