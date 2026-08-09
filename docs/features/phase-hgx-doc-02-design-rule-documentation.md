# Design-Rule Documentation and Manifest Refresh (hgx-doc-02)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Status:** shipped

## Problem

HGX shipped two execution runtimes side by side and never wrote down which one
to reach for. `tools/studio/workflow_runner.py` gained wave-parallel dispatch
(hgx-par-01), conditional edges (hgx-cond-01) and agent nodes (hgx-agent-01);
`tools/agent_runtime/` gained a full slash-command registry, standing goals,
project context and checkpoints. Both are good. Neither document says when the
graph is the wrong answer.

That gap has a specific cost, and it is not "a diagram is missing". Forcing a
graph onto exploratory work does not error — it **succeeds while producing a
worse answer**, because a graph node cannot look somewhere its author did not
name. The DAG quietly converts "find out what is wrong" into "confirm the
author's hypothesis", and returns a confident, well-structured, wrong report.
It also costs materially more to do so: `max_parallel: 4` is four concurrent
model contexts, each carrying its own system prompt, project-context block and
tool schemas — not one agent doing four things.

The second half of the problem is ordinary doc rot. Three surfaces described
shipped capability as unbuilt or undocumented:

- `tools/manifest/standalone-agent-runtime.md` still called `/rollback` a
  "stub in `commands.py`" and described the data-driven command registry as
  something that "plugs in via this seam" — future tense for 14 shipped
  commands.
- `tools/agent_runtime/commands.py`'s module docstring cited `test_commands.py`
  as the test enforcing docstring/registry parity. The test is
  `tests/agent_runtime/test_goal_commands.py::test_docstring_matches_registry`.
  Naming the wrong file in the one paragraph whose whole job is to stop drift is
  a small error with an outsized failure mode.
- `tools/manifest/icdev-studio-low-code-no-code-platform.md` documented
  `node_type: agent` and `node_type: mcp` in detail but never documented
  `max_parallel` or `when:` at all — the two keys that decide, respectively,
  what a run costs and whether a failed step has any route to remediation.

## What shipped

### 1. `docs/patterns/loop-vs-graph-decision-tree.md`

A new `docs/patterns/` surface (the directory did not exist) holding the
standing design rule:

> If you can enumerate the steps before you start, author a **graph**. If you
> cannot, run a plain **agent loop**, and only add graph structure once a stage
> has proven it is always there.

The document is deliberately more than that sentence, because the sentence alone
is what everyone already believes they are doing. It carries:

- **A side-by-side runtime table** — control flow, durability, concurrency, and
  token cost for `run_agent_loop`/`AgentRuntime` versus `workflow_runner.py`.
- **The decision tree itself**, with the second-level branch that actually
  matters: known steps still go in a graph even with `max_parallel` unset,
  because the steps being *data rather than a prompt* is the point.
- **Why a graph costs more**, stated as a multiplier rather than a warning:
  fan-out × the per-node context floor, where `project_context.py` alone budgets
  up to 25% of the available input window before task text. The costs buy
  wall-clock, isolation and lens diversity — buy them deliberately.
- **The hybrid**, which is the most common real shape: author the known skeleton
  as a graph and make the one non-enumerable stage a `node_type: agent` node.
  The graph supplies determinism, durability, gates and audit at the seams; the
  loop supplies judgment inside a node.
- **Escape hatches to check before concluding you need a loop** — `when:` for
  conditional branching, `node_type: human` for a decision the run should not
  guess at, `max_parallel` for independent fan-out.
- **An anti-pattern table**, so the rule is checkable against an artifact rather
  than only against intent: a YAML step named `investigate`, every edge carrying
  a `when:` with half of them unreachable, a raised `max_parallel` whose branches
  are never joined, a loop that re-derives the same five steps every session.

### 2. Manifest refresh

`tools/manifest/standalone-agent-runtime.md`:

- The slash-command extension seam now states that `commands.REGISTRY` **shipped**
  (14 commands, wired by `build_runtime()`), that `runtime.py`'s four-command set
  is a *fallback* for `command_handler=None`, and that nothing in the registry is
  a stub — naming the parity test that enforces it.
- The checkpoints seam names the live handlers: `/snapshot` →
  `create_checkpoint(paths, label="manual /snapshot")`, `/rollback` →
  `list_checkpoints()` / `describe_changes()` / `rollback(id, confirm=…)`,
  previewing by default and mutating only on an explicit `--yes`, with the
  rollback snapshotting first so it can itself be undone.
- A new **Design rule — loop or graph?** section links the decision tree.

`tools/manifest/icdev-studio-low-code-no-code-platform.md`:

- New row **Parallel DAG Dispatch (`max_parallel`)** — the prepared
  `TopologicalSorter` walked with `get_ready()`/`done()` in a bounded
  `ThreadPoolExecutor` (D40, D36); default 1 and opt-in so all 61 shipped
  templates stay byte-for-byte sequential; clamped `1..16`; no barrier primitive
  (fan-in falls out of graphlib); and the three things the parallel loop must get
  right that the linear walk never had to — a gate parking its own thread, an
  abort flag rather than a `break`, and a monotonic `seq` replacing the
  positional `index` on SSE events.
- New row **Conditional Edges (`when:`)** — the `{field, operator, value}` DSL
  imported from `automation_builder.py` rather than re-implemented, the three
  addressing forms (flat predecessor field, `steps.<id>.<field>` for a join,
  `output.<path>` over parsed stdout), the `str` coercion for YAML's bare
  `value: 0`, and the two invariants: a step with no `when` is byte-for-byte the
  prior behaviour, and a step declaring `when:` is exempt from the failure
  cascade — which is what makes `fail -> remediate` reachable at all.
- A new **Node types** section enumerating `VALID_NODE_TYPES` (`tool`, `human`,
  `approval`, `mcp`, `agent`) with a pointer to each one's owning row, plus the
  **Design rule** section linking the decision tree.

### 3. Docstring

`tools/agent_runtime/commands.py` (and its `icdev/` mirror) now cites the real
parity test by full node id and states explicitly that every documented command
is a live handler.

## What was already correct

The card's research notes predicted the `commands.py` docstring would omit
`/skill`, `/search` and `/snapshot` and would describe `/memory` and `/rollback`
as stubs. It does not, and it has not for some time: the docstring lists all 14
registered commands with accurate one-line descriptions, and
`test_docstring_matches_registry` is why. That test asserts every key of
`REGISTRY` appears in `__doc__`, so the class of drift the card was written
against is already gated — the only defect left in that paragraph was the wrong
test filename, which no test could catch because nothing asserts a docstring's
claims about itself.

Likewise, `node_type: agent` was already documented in the Studio shard (Agent
Step Dispatch, plus the Agent Tool Authorization Gate row), and feature docs for
hgx-par-01 and hgx-cond-01 already existed inside
`docs/features/dwo-durable-workflow-orchestration.md`. The refresh added what was
genuinely missing rather than restating those.

## Verification

- `pytest tests/agent_runtime/test_commands.py tests/agent_runtime/test_goal_commands.py`
  — docstring/registry parity holds after the docstring edit.
- `python tools/workflow/coherence_checker.py --tier fast --gate` — including
  `check_doc_command_paths`: every `python tools/...` invocation cited in the new
  documentation resolves to a committed file
  (`tools/studio/template_linter.py`, `python -m tools.agent_runtime.runtime`).
- `python tools/dx/companion.py --sync --write --json` — companion sync run after
  the doc changes.

## Related

- [docs/patterns/loop-vs-graph-decision-tree.md](../patterns/loop-vs-graph-decision-tree.md)
- [dwo-durable-workflow-orchestration.md](dwo-durable-workflow-orchestration.md) — hgx-par-01, hgx-cond-01
- [phase-hgx-agent-01-studio-agent-node.md](phase-hgx-agent-01-studio-agent-node.md) — `node_type: agent`
- [phase-hgx-agent-02-agent-tool-gate.md](phase-hgx-agent-02-agent-tool-gate.md) — AGENT-WF-001
- [phase-hgx-doc-01-graph-execution-chat.md](phase-hgx-doc-01-graph-execution-chat.md) — the sibling doc task
- [phase-sag-standalone-agent.md](phase-sag-standalone-agent.md) — the agent loop runtime
