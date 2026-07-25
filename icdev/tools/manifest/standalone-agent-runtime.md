# Standalone Agent Runtime (SAG)

> A persistent, interactive agent process. A thin orchestration shell over
> ICDEV's production agent loop, chat persistence, and provider abstraction —
> it introduces no new LLM execution path or storage. Canonical package:
> `icdev/tools/agent_runtime/` (mirrored to `tools/agent_runtime/`).

| Tool | Purpose |
|------|---------|
| `tools/agent_runtime/runtime.py` | `AgentRuntime` engine. `run_turn()` wraps `icdev.tools.llm.agent_loop.run_agent_loop` (native tool-use, budget caps, context compression, memory injection, session resume). Interactive `loop()` REPL with injectable I/O. LLM calls flow through `LLMRouter` (no hardcoded model IDs). CLI: `python -m tools.agent_runtime.runtime`. |
| `tools/agent_runtime/sessions.py` | `RuntimeSession` couples `chat_manager` (human-readable transcript in `chat_contexts`/`chat_messages`) with `agent_loop_session` (`save_session`/`load_session` in `agent_loop_sessions`, keyed by `AgentLoopResult.session_id`). Rolls token/cost usage forward across turns; `resume_session_id` restores tool-use history. No new persistence. |
| `tools/agent_runtime/builtin_tools.py` | `build_builtin_toolset()` — the small hardcoded starter toolset (read-only `read_file`, `search_files`, `health_check`), all confined to the repo root (`..` escapes rejected). Dynamic tool auto-discovery lands in sag-reg-01. |

## Extension seams

- **Slash commands (sag-rt-02):** `AgentRuntime.command_handler` is an injectable
  `(runtime, raw) -> (handled, response, should_exit)` dispatcher. A minimal
  built-in set (`/new`, `/tools`, `/help`, `/exit`) ships here; the full
  data-driven registry plugs in via this seam.
- **Gateway agent-mode (sag-gw-01):** reuses the same runtime + command handler
  behind the Remote Command Gateway's 8-gate security chain.
- **Tool discovery (sag-reg-01):** replaces `build_builtin_toolset()` with a
  registry-driven toolset.
