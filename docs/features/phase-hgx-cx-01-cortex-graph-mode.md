# CUI // SP-CTI

# cortex.agent(mode="graph") + a validated mode argument (hgx-cx-01)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Modules:** `tools/cortex/api.py` (+ `icdev/` mirror),
`tools/mcp/cortex_server.py`, `tools/mcp/tool_registry.py` (+ mirrors)
**Tests:** `tests/cortex/test_api_governed.py`
**NIST:** AU-2, AU-12 (every mode still audits), SI-10 (input validation).

---

## The gap, and the bug underneath it

`cortex.agent()` had two execution modes — an ACE team launch and a single
agent-loop — while the platform's third and most capable executor, the Studio
DAG runtime (`tools/studio/workflow_runner.py`: durable, restart-safe resume,
human approval gates, per-node tool authorization under `AGENT-WF-001`), was
reachable only by importing it directly, i.e. ungoverned.

The dispatch was a single boolean:

```python
use_team = mode == "team" or (mode == "auto" and bool(roles))
```

so `mode` had no accepted set. Any unrecognised value — a typo, a stale caller,
or `"graph"` before it existed — fell through the `if` and ran a **single
agent**. The caller asked for one thing, was told nothing, and got a real,
billed execution of another. `reason()` has validated its mode against
`_REASON_MODES` and raised `ValueError` since it shipped; `agent()` did not.

## What shipped

### 1. `_AGENT_MODES` — an accepted set, and an error on anything else

```python
_AGENT_MODES = frozenset({"auto", "team", "single", "graph"})
```

`agent()` now normalises `mode` (strip + lower, as `reason()` does) and raises
`ValueError` on an unknown value, **before** any executor is reached. The
validation lives inside the governed body, so a rejected call is still
pre-checked and audited — the audit row records an attempted launch that never
ran, rather than nothing at all.

### 2. `mode="graph"` — an entry point, not a second engine

```python
cortex.agent(
    "run the release pipeline",
    mode="graph",
    graph={"workflow_id": "wf-…", "project_id": "…", "inputs": {"env": "staging"}},
)
```

dispatches to `tools.studio.workflow_runner.start_run(...)` through the
`_start_graph_run` seam (late-bound, like `_get_ace_controller` /
`_run_single_agent`, so tests inject a stub without importing the runtime).

* `workflow_id` is **required** — a graph run names a workflow, so `"auto"`
  never resolves to graph; there is nothing to infer it from.
* `project_id` defaults to `ctx.tenant_id`, then `"default"`.
* `inputs` is `{"goal": goal, **caller_inputs}` — a caller's own `"goal"` key
  wins. The goal is what the facade governed, so recording it on the run row
  keeps "what was this run started with" answerable from the run itself.

The start is non-blocking, exactly like the ACE team launch: `result.data`
carries `run_id` (as team mode carries `instance_id`), plus `workflow_id`,
`project_id` and `mode`, with `provider="studio"`.

Everything the Studio runtime already enforces — approval gates, resume,
`AGENT-WF-001` per-node tool authorization — applies unchanged. Cortex adds a
governed door, not a parallel runtime.

### 3. The MCP surface

`cortex_agent_launch` takes a `graph` object and its `mode` description reads
`auto | team | single | graph`. An unknown mode now returns an error from the
handler instead of quietly launching a single agent.

## Invariants held

* **The facade stamp.** `agent` stays in `CORTEX_FACADES` and keeps
  `__cortex_governed__`; graph mode runs the full TRUST chain (injection screen,
  input/output redaction, provenance, append-only audit) like every other mode.
  `tests/cortex/test_api_governed.py` passes (33 tests).
* **LLM-agnostic.** No model ids; graph mode makes no provider call at all — it
  hands off to the DAG runtime, whose nodes route through `LLMRouter`.
* **OS-agnostic.** No file I/O, no shell, no `os.getcwd()`; the runner's own
  threads (D36) do the work.
* **Mirrored.** Every changed `tools/` module is mirrored to `icdev/tools/`.

## Verified

Beyond the stubbed unit tests, the real path was exercised end to end against
the live runner (SQLite, worker body stubbed so the probe executed no tools):
`create_workflow` → `cortex.agent(mode="graph", …)` → run row
`run-964306c280b2` in `studio_workflow_runs` with
`inputs_json = {"goal": "probe the graph mode", "env": "staging"}`, and
`result.data["run_id"]` matching. `mode="nonsense"` raised
`ValueError: unknown agent mode 'nonsense'; expected one of
['auto', 'graph', 'single', 'team']`.
