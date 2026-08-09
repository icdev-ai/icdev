# CUI // SP-CTI

# AGENT-WF-001 — default-deny authorization for agent-node tools (hgx-agent-02)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Modules:** `tools/studio/executors/agent_tool_gate.py` (new),
`tools/studio/executors/agent_executor.py`,
`tools/studio/executors/mcp_executor.py`, `tools/studio/workflow_runner.py`,
`args/security_gates.yaml` (`agent_workflow_tools` + gate `AGENT-WF-001`),
`args/agent_toolsets.yaml`
**Tests:** `tests/test_dwo_agent_allowlist.py`,
`tests/studio/test_agent_tool_gate.py`
**Gate:** `AGENT-WF-001` — HIGH. **NIST:** AC-3, AC-6, AU-2, AU-12, CM-5.

---

## The gap

Per-node tool isolation is called the source docs' "#1 P0 security gap", and for
`node_type: mcp` steps it has been closed since dwo-mcp-02: `mcp_workflow_tools`
in `args/security_gates.yaml` is `default: deny`, 16 read-only tools dispatch
unattended, 13 state-changing ones dispatch only behind an approved
`node_type: human` gate in the same run, the caller's impact level and roles are
checked against the registry component that owns the tool's handler, and every
attempt lands in append-only `studio_mcp_dispatch_audit` with a `params_sha256`.

hgx-agent-01 then added a **fourth** automated node type, `node_type: agent`,
which had none of that. Its only bound was the bundle intersection: whatever
`args/agent_toolsets.yaml` said the declared bundles resolved to, the model got.
So a template author picked the toolset, and nothing asked whether the *caller*
running that template was allowed to use it.

An agent node is the harder case, not the easier one. An mcp step names one tool
at authoring time, so a reviewer reads the authorization question straight off the
template. An agent step names bundles and a **model** decides, per turn, which of
them to call.

## What shipped

The same treatment, deliberately built from the same parts.

### 1. Policy — `agent_workflow_tools` in `args/security_gates.yaml`

```yaml
agent_workflow_tools:
  default: deny
  nist_controls: [AC-3, AC-6, AU-2, AU-12, CM-5]
  default_min_il: IL4
  allowed:            # read-only inspection of the step's own worktree
    [read_file, list_files, grep_files, search_files, git_diff, done]
  requires_approval:  # mutating — an approved human gate first
    [write_file, patch_file, run_command]
  tool_limits:
    run_command:
      min_il: IL5
```

`tool_limits` is the one structural difference from `mcp_workflow_tools`. An mcp
tool inherits `min_il` / `required_roles` from the registry component owning its
handler module; a worktree tool has no owning component, so the limit has to be
declared. Where a tool *is* reachable from both surfaces the **stricter** impact
level wins, so an agent node can never be a cheaper route to a tool the mcp
surface holds at IL5.

`run_command` is held at IL5 because executing code inside the platform's own
process tree is not merely a worktree edit. A default IL4 run is simply never
handed it.

### 2. Enforcement — twice, in `executors/agent_tool_gate.py`

| Layer | Function | Why |
|---|---|---|
| Offer time | `authorize_toolset(names, caller=…)` | A tool the caller may not use is never described to the model, so it cannot be asked for and no turn is spent being refused. |
| Call time | `build_gate_hook(...)` → `PreToolUseHook` | "Not offered" is a weaker claim than "not authorized": a model can emit a tool name it was never given, and a future caller of `build_step_toolset` could get the offer wrong. **This is the layer that decides.** |

`agent_executor.run()` wires both — `apply_tool_gate()` narrows the bundle
intersection, and `_build_approval_hook()` returns the gate hook **chaining to**
the ars-appr-01 reversibility gate rather than replacing it. The two answer
different questions — *may this caller use this tool* (AC-3/AC-6) versus *is this
particular call recoverable* (ars-appr-01) — and a call must clear both.
Authorization runs first and short-circuits, so an unauthorized tool is refused
without asking anyone to review its arguments.

### 3. Extended, not duplicated

Everything load-bearing is the mcp surface's, reached through small
parameterisations of `mcp_executor`:

| Concern | Reused from | Parameterised by |
|---|---|---|
| Fail-closed policy reader (checkout / `icdev/` mirror / wheel) | `load_gate_policy` | `key="agent_workflow_tools"`, cached per section |
| Human gate on the existing HITL representation | `await_approval` / `open_approval_gate` | `prefix`, `label`, `surface`, `policy_key` |
| Caller resolution | `resolve_caller` | — (one caller vocabulary for both node types) |
| Impact-level ordering | `canvas_access._IL_ORDER` | — |
| Append-only audit | `record_dispatch_audit` → `studio_mcp_dispatch_audit` | — |

So `workflow_runner.approve_step` / `reject_step` / `get_pending_approvals`, the
workflow Details modal, the Telegram listener and the resume path all decide an
agent tool's gate unchanged. No new table, no second approval vocabulary, and
"what did this run try to do" stays **one** query across both node types.

The gate id namespace *is* separate — `approval:agent:<tool>` rather than
`approval:<tool>`. Approving `run_command` for a reviewed mcp step and approving
it for a loop that chose it mid-run are different questions, so they are two
gates even inside one run.

## The three acceptance criteria

**A tool outside the allowlist cannot be called, and the refusal is audited.**
`check_tool_allowed` refuses anything absent from both lists (with the closest
allowlisted names, because a typo is the common case). The hook returns a
`BLOCKED by the agent tool gate (AGENT-WF-001, agent_tool_not_allowlisted): …`
string, which the loop feeds back to the model as the tool result, and writes one
`decision='refused'` audit row.

**A mutating tool blocks until its human gate is approved.** The first
`write_file` call parks an `awaiting_approval` step row — no tool path, which is
what makes it a human node — and blocks. One gate per (run, tool): an agent that
writes ten files asks once. Rejected → `agent_tool_approval_rejected`. Undecided
within the window → `agent_tool_awaiting_human_approval`, audited as
`pending_approval` rather than `refused`, because nobody has said no. No run to
park a gate on → refused, since "no approver" does not mean "go ahead".

**A caller below the tool's declared impact level is refused.**
`check_caller_authorized` compares the caller's level against `tool_limits`
(falling back to `default_min_il`) using the platform's own ordering. An
unrecognised level is refused, not defaulted — the gate does not guess what an
unknown level permits.

## Fail-closed, deliberately

* No readable `default: deny` section → `agent_gate_policy_unavailable`, and the
  step is refused (`agent_step_gate_unavailable`). No policy means no toolset,
  not an unbounded one.
* Every tool withheld → `agent_step_all_tools_refused`, naming each reason.
  Better than handing an agent an empty toolbox and letting it discover mid-run
  that nothing works.
* The audit write is best-effort *by design* and never changes the outcome: the
  gate has already decided, and an unreachable audit store must not overturn that
  in either direction. `audit_written` reports whether the row landed.
* Arguments are digested, never stored. An agent's `write_file` call carries file
  content and an audit row is not the place for it.

## What a template may not do

`caller_il` / `caller_roles` are deliberately **absent** from
`workflow_runner._AGENT_STEP_FLAGS`. A template is authored content; letting a
step declare its own impact level would let it raise itself past the very limits
this gate exists to apply. The caller comes from the run's context — run memory's
`caller` key, then `$ICDEV_MCP_CALLER_IL` / `$ICDEV_IMPACT_LEVEL` /
`$ICDEV_MCP_CALLER_ROLES`, then the IL4 baseline.

## Scope

This authorizes the tools an agent node can actually be handed: the worktree
toolset. Registry-backed bundles (`compliance`, `kanban`, …) are still not
dispatchable from an agent node — they are reported in `unavailable_tools` — and
making them so is a separate capability change. The gate is already shaped for
it: a registry tool passing through `tool_limits` inherits its owning component's
`min_il` and takes the stricter of the two.

## Verify

```bash
python tools/studio/executors/agent_tool_gate.py --list --json
python tools/studio/executors/agent_tool_gate.py --tool run_command --caller-il IL4 --json  # exit 1
python tools/studio/executors/agent_tool_gate.py --tool run_command --caller-il IL5 --json  # exit 0
pytest tests/test_dwo_agent_allowlist.py tests/studio/test_agent_tool_gate.py -v
```
