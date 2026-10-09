# Graph Execution Chat Advisory (hgx-doc-01)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Status:** shipped

## Problem

Studio's `workflow_runner.py` is already a durable DAG runtime: wave-parallel
dispatch off a prepared `TopologicalSorter`, human approval gates that survive a
process restart, and per-node tool authorization (MCP-WF-001). What it did not
have was a way to *tell anyone* what it was doing without opening the Studio run
modal in a browser.

The concrete failure that motivates this: a graph parks at an approval gate. The
run is not failed, not hung, and not finished — it is waiting for a person, and
the only place that fact is visible is a modal nobody has open. A parked gate
expires after `approval_timeout` (default 24h) and *fails the run*, so silence
here is not merely inconvenient; it is how a run dies.

## What shipped

`tools/extensions/builtins/031_graph_execution_chat.py` — a `chat_message_after`
hook, built on the proven `030_workflow_loop_chat.py` shape: an advisory dict
returned on the hook context, throttled by a per-context turn cooldown
(`ADVISORY_COOLDOWN_TURNS = 8`, matching 030).

It is **read-only**. It executes nothing, schedules nothing, and mutates
nothing; it is a view onto `studio_workflow_runs` / `studio_workflow_run_steps`
and the run's authored template. Per the card's ground rule, this extends the
existing surface rather than adding a second engine.

Four things it surfaces, which the task named:

| Question | Where the answer comes from |
|----------|----------------------------|
| Which nodes are done? | step rows with status `success` / `approved` / `skipped` |
| Which are running? | step rows with status `running`, named from the template |
| What is a barrier waiting for? | template nodes with ≥2 `depends_on` whose deps are not all done |
| Which gate needs approval? | the step row at `awaiting_approval`, plus the command to release it |

Example output, as chat renders it:

```
[Graph Run] Graph run 'Nightly Compliance' is paused at approval gate
'Approve release' — 3/4 nodes done. The run stays parked until the gate is decided.
Action: python -c "from tools.studio.workflow_runner import approve_step; approve_step('s4')"
```

```
[Graph Run] Graph run 'Nightly Compliance' is in flight — 1/4 nodes done;
1 running (Security scan); node 'Merge findings' is waiting on Security scan.
```

## Decisions worth stating

**Numbered 031, not 040.** The source design specified 040, but
`040_bayesian_learning_chat.py` already owns that slot and
`ExtensionManager._auto_load_builtins()` loads builtins in a lexicographic sort
of the filename — so 040 would have been a collision, not an ordering. 031 keeps
this handler adjacent to the workflow advisory it complements. Free slots
remaining: 032–039, 082–089, 091+.

**Reuses the `workflow_status` content type.** The advisory is registered in
`chat_manager._ADVISORY_TYPES` under a new key `graph_advisory` but with the
*existing* `workflow_status` content type. That type is already in the live
`chat_messages.content_type` CHECK constraint and already has a badge in
`chat.js`'s `ADVISORY_MAP`, so a new type would have cost a migration, a
`pg_consolidated.sql` edit and a frontend change to render identically. A graph
run *is* workflow status; the `[Graph Run]` label is what distinguishes it from
030's `[Workflow Status]`.

**A "barrier" is a join with ≥2 dependencies, not a runtime primitive.**
`workflow_runner._prepare_dag` is explicit that a join needs no barrier object —
`get_ready()` simply withholds a node until every `depends_on` is `done()`.
Single-dependency nodes are therefore excluded from barrier reporting: a chain
waiting on its one predecessor is just the run being in progress, and calling it
a barrier would make every running graph read as blocked.

**`skipped` counts as done.** A node the DAG skipped — an unconfigured step, or
one whose `when:` did not fire — releases its children exactly as `success`
does, so it must satisfy a barrier or the advisory would report a block that
does not exist.

**No step rows means no advisory.** A run row created but not yet dispatched has
nothing to report; announcing its template's joins would describe a barrier on a
graph where nothing has happened.

**The action is an import, not a CLI.** `workflow_runner.py` declares no
`argparse` and no `__main__` — it is a library. Per CLAUDE.md ("if a tool is a
library with no argparse/`__main__`, document the import, not a CLI"), the
advisory emits the `python -c "from tools.studio.workflow_runner import
approve_step; …"` form rather than inventing a command that does not exist.

**Quiet by default.** No active run in the project → nothing is injected, and
the cooldown is not consumed, so the next turn with something to say still
fires. An unscoped chat reads project `default`, which is `start_run`'s own
default, rather than seeing nothing at all.

## Card acceptance criteria

- **LLM-agnostic** — the handler makes no LLM call and names no model; there is
  no provider-specific payload to degrade.
- **OS-agnostic** — no filesystem access, no shell, no subprocess; the only
  inputs are database rows and a YAML string. Nothing here is path- or
  platform-sensitive.
- **Mirrored** — `icdev/tools/extensions/builtins/031_graph_execution_chat.py`
  and `icdev/tools/dashboard/chat_manager.py` carry the same content.

## Collateral fix

`coherence_checker.py::check_test_db_isolation` flagged this feature's own
negative test — a replacement factory that only `raise`s, used to prove the
handler survives an unavailable database — as though it handed runtime code a
raw sqlite3 connection. A function whose every statement is a `raise` returns no
connection at all, so `_only_raises()` now clears it. The narrowing is strict: a
function that raises on one path and returns `sqlite3.connect(...)` on another
is still a violation. This is the fourth remedy-rejection recorded in that
check's own comments.

## Files

| File | Change |
|------|--------|
| `tools/extensions/builtins/031_graph_execution_chat.py` | new — the hook |
| `tools/dashboard/chat_manager.py` | `graph_advisory` in `_ADVISORY_TYPES`; `run_id` in the dirty-payload allowlist |
| `tools/workflow/coherence_checker.py` | `_only_raises()` — see collateral fix |
| `tools/manifest/extensions.md` | registry row |
| `tests/test_graph_execution_chat_extension.py` | new — 26 tests |
| `icdev/tools/...` | mirrors of all three changed modules |
