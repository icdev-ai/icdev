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
| `tools/agent_runtime/commands.py` | Data-driven slash-command registry (sag-rt-02): `/new`, `/clear`, `/title`, `/tools`, `/skills`, `/memory` (stub → sag-mem-01), `/usage`, `/rollback` (stub → sag-safe-02), `/help`, `/exit`. Handled deterministically **without re-prompting the LLM**. `dispatch(runtime, raw)` matches `AgentRuntime.command_handler`; reused by gateway agent-mode (sag-gw-01). `build_runtime()` wires the registry into a runtime. |
| `tools/agent_runtime/discovery.py` | Tool auto-discovery + OpenAI schema generation (sag-reg-01). Derives agent-loop tool specs FROM the MCP `TOOL_REGISTRY` (one registration serves both surfaces) and the built-in toolset, so 440+ tools carry schemas for free. `schema_from_callable()` synthesises a schema from signature + type hints + Google-style docstring (stdlib only); the `@tool` decorator / `__tool_schema__` marker opts a new first-party function in. `ToolSpec.check` gates conditional availability; `build_registry()` layers sources (MCP → builtin → decorated → extra) and drops unavailable tools; `write_cache()`/`load_cache()` persist the serialisable half (schemas + `module`/`handler` dispatch coordinates, no live callables) to JSON for fast startup. Discovery only — handler dispatch/bundles land in sag-reg-02. CLI: `python -m tools.agent_runtime.discovery --json [--write-cache]`. |
| `args/agent_toolsets.yaml` | Toolset **bundles as data** (sag-reg-02): `file`, `terminal`, `compliance`, `security`, `canvas`, `govcon`, `kanban`. Each names a bounded set of tool names (resolved against discovery) so a small local model never sees the full 440+ surface. `mutating: true` marks bundles routed through the safety gate. Retune bundles here without touching Python. |
| `tools/agent_runtime/toolsets.py` | Loads `args/agent_toolsets.yaml`, resolves bundle names to tool sets, and assembles the `(tools, handlers)` pair for `run_agent_loop`. `build_toolset(names, safety_gate=…, task_id=…)`; `list_bundles()`; `resolve_bundles()`; `all_discovered_tools()` backs `icdev tools list`. |
| `tools/agent_runtime/dispatch.py` | Turns discovery `ToolSpec` `module`/`handler` coordinates into agent-loop `handler(input, stop) -> str` handlers (sag-reg-02). Source-aware invocation (MCP `handle_x(args)`, decorated named-kwargs, built-in passthrough), injects `stop_event`/`task_id` where the signature accepts them, normalises results to strings, and never raises. **Safety hook point**: every mutating tool is routed through a `SafetyGate`; `default_safety_gate` fails closed (mutations refused unless `ICDEV_SAG_ALLOW_MUTATION=1`) until sag-safe-01 injects the real approval UX. |
| `tools/agent_runtime/mutating_tools.py` | The state-mutating built-ins (sag-reg-02): `write_file` (repo-confined, `..` rejected) and `run_command` (reuses the `tools/skills/invoke.py` allowlist — only `python tools/ \| python -m tools \| python -c`). `@tool(read_only=False)`, so discovery picks them up and dispatch safety-gates them. **Intentionally NOT MCP-registered** — the local file-write/terminal surface must not be exposed to external agents over MCP. |
| `tools/cli/tools_list.py` | `icdev tools list [--json]` (all discovered tools + schemas) and `icdev tools bundles [--json]` (the YAML bundles). Wired into `tools/cli/__main__.py`. |
| `tools/agent_runtime/checkpoints.py` | Filesystem checkpoints + rollback (sag-safe-02). Before an approved destructive command runs (safety-gate trigger), snapshots affected paths to `.tmp/checkpoints/<id>/`: untracked files copy-based, tracked files via one `git stash create` object restored per-path with explicit pathspecs (`git restore --source=<sha> -- <path>`; never mass restores). A not-yet-existing file is recorded so rollback deletes it. `create_checkpoint()`, `snapshot_for_tool()`, `rollback(id, confirm=…)` (snapshots current state first → rollback-of-rollback), `list_checkpoints()`, `describe_changes()`, `prune(max_age_days=7)`. Backs `/snapshot` + `/rollback`. Windows-safe pathlib, no `/tmp`. |
| `tools/agent_runtime/safety.py` | Command-approval safety layer (sag-safe-01) — the real `SafetyGate` for the reg-02 dispatch seam. Composes the existing `tools.airgap.hook_compat.run_pre_tool_check` (destructive-git + append-only-table hard blocks) with an approval flow: modes `manual` (always prompt), `smart` (cheap-tier LLM risk judgment, heuristic fallback; auto-approve low risk), `off` (`--yolo`, still audited). `ICDEV_SAG_APPROVAL_MODE` sets the default mode. Approver is injectable (`console_approver` default; gateway agent-mode injects a messaging-adapter approver). Every approve/deny is appended to the existing append-only `hook_events` trail via `store_event` (no new table). `build_safety_gate(mode=…, approver=…, router=…)`. `toolsets.build_toolset()` uses it by default; `AgentRuntime.use_toolset(bundles, approval_mode=…, approver=…)` swaps the runtime onto a gated bundle. |

## Extension seams

- **Slash commands (sag-rt-02):** `AgentRuntime.command_handler` is an injectable
  `(runtime, raw) -> (handled, response, should_exit)` dispatcher. A minimal
  built-in set (`/new`, `/tools`, `/help`, `/exit`) ships here; the full
  data-driven registry plugs in via this seam.
- **Gateway agent-mode (sag-gw-01):** reuses the same runtime + command handler
  behind the Remote Command Gateway's 8-gate security chain.
- **Tool discovery (sag-reg-01):** `discovery.build_registry()` supersedes the
  hardcoded `build_builtin_toolset()` with a registry-driven toolset assembled
  from the MCP registry + built-ins + `@tool`-decorated functions.
- **Toolset bundles + dispatch (sag-reg-02):** `toolsets.build_toolset()` consumes
  `discovery.ToolSpec` `module`/`handler` coordinates via `dispatch.build_handlers()`
  to build agent-loop `handler(input, stop) -> str` callables; bundles defined as
  YAML data in `args/agent_toolsets.yaml`.
- **Safety layer (sag-safe-01):** `safety.build_safety_gate()` is the real gate;
  `toolsets.build_toolset()` installs it by default (fail-closed only if it cannot
  be built). Mutating tools cannot execute without passing `run_pre_tool_check`
  and, per mode, operator approval.
- **Checkpoints (sag-safe-02):** the approval flow is the trigger point — before an
  approved destructive command runs, snapshot affected paths (wires the `/rollback`
  stub in `commands.py`).
- **Gateway agent-mode (sag-gw-01):** inject a messaging-adapter `approver` into
  `build_safety_gate()` so approvals happen via confirmation replies.
