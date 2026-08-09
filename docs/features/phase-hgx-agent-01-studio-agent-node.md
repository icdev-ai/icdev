# CUI // SP-CTI

# `node_type: agent` — an agent loop as a Studio workflow step (hgx-agent-01)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Modules:** `tools/studio/executors/agent_executor.py`, `tools/studio/workflow_runner.py`,
`tools/orchestration/workflow_composer.py`, `tools/studio/template_linter.py`,
`args/agent_toolsets.yaml`, `args/agent_approval_policy.yaml`
**Tests:** `tests/studio/test_workflow_agent_node.py`,
`tests/test_dwo_mcp_node_type_enumerations.py`

---

## The gap

Studio's `workflow_runner.py` is already a durable DAG runtime — human gates,
restart-safe resume, per-node tool authorization, conditional edges. A step could
be a subprocess (`node_type: tool`), a registry tool (`node_type: mcp`) or a gate
(`human` / `approval`). Nothing ran an **agent loop** as a node, so a workflow
could orchestrate deterministic tools and pause for a person, but could not hand
one step to a model and let it work.

`template_linter.VALID_NODE_TYPES` was exactly `{tool, human, approval, mcp}`.

## What shipped

A fourth automated node type, built by extending the existing surface rather than
by growing a second engine:

```yaml
- id: document
  name: Document The Module
  node_type: agent
  prompt: "Read tools/foo.py and add a module docstring explaining what it does."
  agent_tools: [worktree_build]
  llm_function: code_generation     # a ROUTING key, never a model id
  work_dir: /path/to/worktree       # default: the repo root
```

`tools/studio/executors/agent_executor.py` mirrors `mcp_executor.py`: a subprocess
taking `--step-id` / `--project-id` / `--run-id` / `--json`, reading and writing
run memory, emitting one line of JSON. `_exec_step` gained a branch; it was not
restructured.

### The toolset is hgx-exec-01's, bounded by data

Tools come from `tools/genesis/rubric_build_tools.py::build_worktree_toolset` —
read / list / grep / glob / `git diff` / write / patch / allowlisted `run_command`
/ `done`, every path resolved and traversal-guarded inside `work_dir`. That is the
toolset an ICDEV build agent already uses; this executor did not grow another one.

A step's `agent_tools` names bundles in `args/agent_toolsets.yaml`, resolved with
the same `toolsets.resolve_bundles()` the standalone agent runtime uses, and the
offered toolset is the **intersection**. Two bundles were added as data:

| Bundle | Tools |
|--------|-------|
| `worktree_read` | `read_file`, `list_files`, `grep_files`, `search_files`, `git_diff`, `done` |
| `worktree_build` | `worktree_read` + `write_file`, `patch_file` |

They compose with the existing `terminal` bundle when a step also needs the
allowlisted command runner. **Default-deny:** a step that declares no bundle is
refused, not handed the worktree.

### Every call passes the reversibility gate

`approval_gate.build_approval_hook()` is passed to `run_agent_loop` *explicitly*
rather than left to `ICDEV_AGENT_APPROVAL_MODE`, so an agent node is gated whether
or not the deployment set that variable. Tiers come from
`args/agent_approval_policy.yaml`; decisions are audited append-only. The default
approver denies on EOF and a step subprocess has `stdin=DEVNULL`, so an unattended
run fails closed on anything the policy calls irreversible or unknown.

That gate had a real hole: the worktree toolset's own names were never enumerated
in the policy, so `grep_files`, `search_files`, `git_diff`, `done` and `patch_file`
all fell to `unknown` — which halts. An agent node would have been able to read and
list and nothing else. Those names now carry the tier they actually deserve
(`patch_file` is recoverable, the rest reversible), and
`test_every_worktree_tool_has_a_declared_reversibility_tier` fails if a future
worktree tool arrives without one. `run_command` is deliberately excluded from that
assertion: it is a shell, so its tier is decided by the command it is handed.

### A model that cannot do tool use degrades the step

`run_agent_loop` raises `AgentLoopUnsupported` when the routed provider is the CLI
bridge (which flattens tools to text) or a model declaring `supports_tools: false`.
The executor catches it and exits **0** with `degraded: true` and a
`degrade_reason`; `_exec_step` lifts that flag out of stdout and records the step
as `skipped` with the reason as its stderr.

`skipped` is not a failure, so the run continues and dependents can branch on it
with `when: {field: output.degraded, ...}` (hgx-cond-01). Exiting 0 alone would
have recorded `success` for a loop that never ran — the downgrade is what keeps the
board honest. The alternative, failing the run, would make any workflow containing
an agent step undeployable on an air-gapped box whose local model cannot serve one,
which is precisely the deployment ICDEV targets.

### Artifacts

Files the agent wrote or patched are read out of the loop's own `tool_call_log`
(not by scanning the worktree, which cannot distinguish this step's writes from the
tree it started with), declared as the step's `artifacts`, and lifted by the runner
into run memory under the step's name like any other step's.

## LLM- and OS-agnostic

- No model id anywhere. The step declares `llm_function`; `args/llm_config.yaml`
  decides the provider. There is no `--model` flag, and a test asserts a `model:`
  key on a step never reaches the executor.
- Native tool use only — no provider-specific payload.
- `pathlib` throughout; repo root from `__file__`, never `os.getcwd()`.
- No shell: list argv, `shell=False`, `sys.executable`.
- Threads, not asyncio (D36) — `run_agent_loop` is synchronous.
- File I/O an agent performs goes through `rubric_build_tools`'
  `open(..., encoding="utf-8", newline="")` helpers, so a one-line patch on Windows
  does not rewrite every line ending in the file.

## Scope note — the headless composer

`tools/orchestration/workflow_composer.py` builds a byte-identical command for an
agent step. Without that branch it would have read the step's empty `tool` and
silently skipped it, so a template that worked in the UI would have done nothing
from cron or an air-gapped shell. `test_the_headless_composer_builds_the_same_command`
pins the two builders together, the way `test_dwo_mcp_composer_parity.py` does for
mcp nodes.

## Authoring surfaces

Adding a node type means every surface that enumerates the vocabulary has to learn
it, which `tests/test_dwo_mcp_node_type_enumerations.py` enforces:

- `template_linter.VALID_NODE_TYPES`, plus a `bad_agent` lint for a step missing
  `prompt` or `agent_tools` — both fail quietly at run time, which is why they are
  linted rather than left to be discovered mid-run.
- `workflow_chat._SYSTEM_PROMPT_TEMPLATE`, so chat can author one.
- `args/workflow_templates/README.md` — the schema, the field-applicability matrix,
  and the allowed-values line.
- `tools/dashboard/static/js/workflow-studio.js` — the builder rebuilds YAML from
  its own node objects, so a key it does not know is not merely un-authorable, it is
  **destroyed** by a save. `prompt` and `agent_tools` now survive the
  save → YAML → reload round trip.

## Not in scope (hgx-agent-02)

Registry-backed tools in an agent node. Naming a registry bundle such as
`compliance` is refused with the tools it asked for listed, rather than silently
offering an empty toolbox. The `agent_workflow_tools` default-deny gate — modelled
on `mcp_workflow_tools`, with caller IL / role checks and a human gate for mutating
tools, audited to `studio_mcp_dispatch_audit` — is hgx-agent-02's job.
