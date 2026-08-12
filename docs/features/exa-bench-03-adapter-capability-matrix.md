# Adapter Capability Matrix — declared versus actual (exa-bench-03)

CUI // SP-CTI

**Module:** `tools/agents/capability_matrix.py`
**Claims:** `args/agent_capabilities.yaml`
**Tests:** `tests/test_agent_capability_matrix.py` (29)
**Card:** EXA — External Adoption

## The problem

omnigent ships a harness test bench that validates a capability matrix. ICDEV's
equivalent was a hand-written parity claim, and hand-written claims rot: the plan
docs listed standing goals, cron, checkpoints and session search as missing while
`tools/agent_runtime/` had already shipped a module for each of the four.

This replaces the claim with a measurement.

## What it measures — the seam, not the brochure

Every result is scoped to what a consumer of
`tools/agents/adapter_base.py::AgentAdapter` can actually request or observe.
That is deliberate. The Claude Code CLI runs sub-agents; the `claude_cli` adapter
exposes no way to request one and no way to see one, so the seam cannot deliver
sub-agents and a router must not assume it can. A capability the backend has but
the adapter does not surface is a real gap for everything routing through
`pick_default` — which is every consumer the seam has.

Seven capabilities, fixed: `streaming`, `tool_calling`, `sub_agents`,
`interruption`, `sandbox_passthrough`, `context_budget`, `structured_output`.
Adding an eighth means adding a probe that can measure it; a name with no probe
would be another hand-written claim wearing a measurement's clothes.

## Three statuses, and only two probe methods may assert presence

| `actual` | meaning |
|---|---|
| `present` | the probe observed the capability |
| `absent` | the probe observed its absence at the seam |
| `unconfirmed` | the probe could not determine it — never conflated with either |

| method | what it did | may assert |
|---|---|---|
| `behavioral` | ran adapter code and inspected the real return value — `parse_response` against three fixtures, `build_argv` under a sandbox/budget differential | present / absent |
| `interface` | inspected the live adapter object: public callables, `invoke` signature | present / absent |
| `source_evidence` | matched a documented contract in the module source that only a live run could exercise | **`unconfirmed` only** |

`source_evidence` is capped at `unconfirmed` on purpose. Grep is a lead, not a
measurement, and a probe that promotes a grep hit to "present" is the hand-written
parity table again with extra steps.

The argv probes stub the executable resolver on a **throwaway instance**
(`type(adapter)()`), never the shared module-level `ADAPTER` singleton. That keeps
two properties: the matrix does not change shape between a laptop with the CLIs
installed and an air-gapped runner without them, and a concurrent dispatch holding
the same adapter object is never disturbed by a measurement.

Offline by construction: no subprocess, no socket, no model call. A `--live` tier
that runs each available adapter with a trivial prompt would confirm several of
the `unconfirmed` cells and is the obvious next task — it is deliberately not this
one, because a probe that needs the network stops running in the air-gap
deployments this matrix exists for.

## First run — 35 cells

6 present, 23 absent, 6 unconfirmed. Six declared capabilities measured absent:

| adapter | capability | what the probe found |
|---|---|---|
| `claude_cli` | `tool_calling` | `parse_response()` returns `tool_calls: []` unconditionally. The CLI calls tools every session; the seam surfaces none of them. |
| `claude_cli` | `sub_agents` | no delegation entry point — the CLI's Task tool is unreachable and unobservable through the adapter. |
| `claude_cli` | `sandbox_passthrough` | the constructed argv is byte-identical with and without a caller-supplied sandbox mode: `--dangerously-skip-permissions` is hardcoded and the caller cannot change it. (See exa-bench-04.) |
| `codex_cli` | `streaming` | the CLI emits a JSONL stream, but the adapter has no `spawn()` and buffers through `subprocess.run` — nothing is visible until the run ends. |
| `local_agent` | `tool_calling` | the loop uses native tool use; `parse_response()` reports none of it. |
| `local_agent` | `sandbox_passthrough` | the worktree toolset is traversal-guarded, which is a sandbox — but a fixed one. The caller cannot choose a mode, which is what this capability names. |

Five declared capabilities came back `unconfirmed` rather than present: three
`AgentResult.structured` paths that only `invoke()` populates, `local_agent`'s
`metadata['stop_event']` cancellation contract, and the two router adapters'
token budgets. Each has evidence attached saying exactly what was seen and why it
is not a confirmation.

## Consuming it

```python
from tools.agents import pick_default, adapters_with

adapter = pick_default("build", require=["sandbox_passthrough"])
adapters_with("interruption")     # names measured present, nothing else
```

`require` is fail-closed: a capability that is merely declared, or that the probe
could not confirm, does not satisfy it. Routing work to an adapter on the strength
of a claim nobody verified is the failure this module exists to end.

Two deliberate non-changes:

* **`require` unset preserves selection exactly.** No probe runs, nothing is
  imported, no existing caller changes behaviour. `tools/genesis/reflexes/kanban.py`
  is untouched.
* **`ICDEV_AGENT_ADAPTER` still wins over `require`.** An operator forcing an
  adapter has said something more specific than a capability filter. The mismatch
  is logged at WARNING; silently overriding them would be another control that
  looks like it worked.

## Not the same question as `executor_parity.py`

Do not merge these two modules. The distinction is stated in
`capability_matrix.py`'s module docstring and guarded by a test.

| | `executor_parity.py` (hgx-exec-04) | `capability_matrix.py` (exa-bench-03) |
|---|---|---|
| question | **outcome parity** — can this executor finish a job? | **capability parity** — can it be handed a job that needs streaming / a sandbox / a cancel button? |
| method | replays a task corpus in disposable worktrees, grades the trees with the real delivery gates | probes the adapter seam offline |
| needs | a corpus, worktrees, live model calls, minutes | nothing; milliseconds |
| output | gate-pass rate vs self-report rate per executor | per (adapter, capability): declared, actual, method, evidence |

`executor_parity.py` is unmodified by this card.

## Gate

`python tools/agents/capability_matrix.py --gate` exits 1 when any capability is
declared but measured absent. It is opt-in and wired to no pipeline: it is a
report you can run, not a gate that runs itself. Six rows would fail it today, and
each names a real gap in an adapter rather than a formatting problem.

When a row comes back `overclaimed`, fix the adapter or fix the claim — editing
the claim to make the probe agree rebuilds the parity table this replaced.
